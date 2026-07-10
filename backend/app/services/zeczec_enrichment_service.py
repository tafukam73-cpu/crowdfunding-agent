"""Zeczec 案件の詳細補完（メーカー名・カテゴリ・説明・公式サイト候補）。

一覧（/categories）からは商品名/URL/調達額/達成率/支援者数/画像しか取れず、メーカー名と
カテゴリが null になる。詳細ページ（Cloudflare 保護）を実ブラウザで取得し、**確認できた
事実だけ** を既存 projects レコードに非破壊で書き戻す。

方針（要件）:
- 軽量な情報源を優先し、取れない項目は None のままにして理由を残す（推測しない）。
- 既存の非 None 値を空値で上書きしない（非破壊更新）。
- 取得元 URL を enrichment（根拠 JSON）に残す。再スクレイプで消えない場所に保存する。
- 公式サイト候補は確度つき（high=Zeczec ページ直リンク / medium=creator・SNS 経由 /
  low=検索結果）。low は自動確定せず候補扱い。high が 1 ドメインに定まるときのみ
  maker_url に自動採用する。
- メーカー名（提案人）または公式サイト候補が取れれば Contact Intelligence を実行できる
  （CI は project.maker_name を会社名、project.maker_url を公式サイト候補に使う）。

このモジュールはパース済み detail（app.scrapers.zeczec_detail.parse_detail の返り値）を
入力に取り、DB 非依存の純粋関数 build_enrichment_updates() と、DB へ適用する
apply_enrichment() / orchestrator enrich_project() / batch を分離する（テスト容易性）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Project, SourceSite
from app.services import contact_discovery_service as cds

logger = logging.getLogger("zeczec_enrichment")

MAX_SEARCH_QUERIES = 2


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _enrichment_owned(project: Project, field: str) -> bool:
    """その項目の現在値を「以前この enrichment が設定した」かどうか（provenance で判定）。"""
    prov = (project.enrichment or {}).get("provenance") or {}
    return field in prov


def _can_write(project: Project, field: str, current) -> bool:
    """その項目を書いてよいか（非破壊 + 自己修正）。

    - 空なら書ける。
    - 空でなくても、以前この enrichment が設定した値なら再取得で更新してよい（自己修正）。
    - 一覧スクレイパーやユーザーが入れた値（provenance に無い）は上書きしない。
    """
    if current is None or (isinstance(current, str) and not current.strip()):
        return True
    return _enrichment_owned(project, field)


def _root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else url


def _dedup_candidates(cands: list[dict]) -> list[dict]:
    """URL 正規化して重複排除。confidence は high>medium>low を優先保持。"""
    order = {"high": 3, "medium": 2, "low": 1}
    best: dict[str, dict] = {}
    for c in cands:
        url = (c.get("url") or "").strip()
        if not url:
            continue
        key = url.split("#")[0]
        cur = best.get(key)
        if cur is None or order.get(c.get("confidence"), 0) > order.get(
            cur.get("confidence"), 0
        ):
            best[key] = {**c, "url": key}
    # high→low で安定ソート
    return sorted(best.values(), key=lambda c: -order.get(c.get("confidence"), 0))


def _search_official_candidates(project: Project, search_fn) -> list[dict]:
    """検索で公式サイト候補（low）を最大数件集める。プラットフォーム/SNS は除外。"""
    if search_fn is None:
        return []
    company = (project.maker_name or "").strip()
    product = (project.title or "").strip()
    queries: list[str] = []
    if company:
        queries.append(f"{company} official site")
    if product and len(queries) < MAX_SEARCH_QUERIES:
        queries.append(f"{product} official site")
    queries = queries[:MAX_SEARCH_QUERIES]

    out: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        try:
            results = search_fn(q) or []
        except Exception as exc:  # noqa: BLE001  1 クエリ失敗は無視
            logger.info("enrichment search failed %r: %s", q, exc)
            continue
        for r in results:
            url = r.get("url") if isinstance(r, dict) else str(r)
            if not url or not url.startswith(("http://", "https://")):
                continue
            if cds.is_platform_url(url):
                continue
            root = _root(url)
            if root in seen:
                continue
            seen.add(root)
            out.append({
                "url": root,
                "confidence": "low",
                "source": "search_result",
                "query": q,
            })
            if len(out) >= 5:
                return out
    return out


def _pick_official_site(candidates: list[dict]) -> tuple[str | None, str | None]:
    """公式サイトを自動採用できるか判定して (url, reason) を返す。

    high 確度の候補が 1 ドメインに定まるときのみ採用する（複数 high は誤採用を避けて
    保留）。medium/low は自動採用しない（候補扱い）。
    """
    highs = [c for c in candidates if c.get("confidence") == "high"]
    domains = {cds._domain_of(c["url"]) for c in highs if cds._domain_of(c["url"])}
    if len(domains) == 1:
        # 同一ドメインの中で最も浅い URL（ルート）を選ぶ
        same = [c for c in highs if cds._domain_of(c["url"]) in domains]
        same.sort(key=lambda c: len(urlparse(c["url"]).path))
        return same[0]["url"], "zeczec_page_direct_link（high・単一ドメイン）"
    if len(domains) > 1:
        return None, f"high 候補が複数ドメイン（{len(domains)}）のため自動採用せず候補保留"
    if any(c.get("confidence") in ("medium", "low") for c in candidates):
        return None, "medium/low 候補のみのため自動確定せず候補扱い"
    return None, "公式サイト候補なし"


def build_enrichment_updates(
    project: Project, detail: dict, *, extra_candidates: list[dict] | None = None
) -> dict:
    """detail（parse_detail の結果）から (column_updates, enrichment, reasons) を作る純粋関数。

    Returns:
      {
        "column_updates": {maker_name?, maker_url?, category?, end_date?},  # 非破壊で埋める値のみ
        "enrichment": {...},                                                # 根拠 JSON
        "reasons": {field: 理由},                                            # 取得不能/保留の理由
        "provenance": {field: 取得元},
      }
    """
    if detail.get("challenged"):
        return {
            "column_updates": {},
            "enrichment": None,
            "reasons": {"all": "詳細ページが Cloudflare チャレンジで取得不可（403/JS依存）"},
            "provenance": {},
        }

    column_updates: dict = {}
    reasons: dict = {}
    provenance: dict = {}

    detail_url = detail.get("source_detail_url")

    def _set(field: str, value, prov_label: str) -> None:
        """非破壊 + 自己修正で列更新を積む。value=None なら過去に enrichment が
        設定した値のみクリア（他ソースの値は温存）。"""
        if not _can_write(project, field, getattr(project, field, None)):
            return
        if value is not None:
            column_updates[field] = value
            provenance[field] = prov_label
        elif _enrichment_owned(project, field) and getattr(project, field, None) is not None:
            column_updates[field] = None  # 以前の enrichment 値が今回無効化 → クリア

    # --- メーカー名（提案人） ---
    maker_name = detail.get("maker_name")
    _set("maker_name", maker_name[:255] if maker_name else None, "zeczec_detail:提案人")
    if not maker_name:
        reasons["maker_name"] = "詳細ページに提案人リンクが見つからない"

    # --- カテゴリ ---
    category = detail.get("category")
    _set("category", category[:120] if category else None,
         "zeczec_detail:breadcrumb(category=)")
    if not category:
        reasons["category"] = "詳細ページにカテゴリ breadcrumb が無い"

    # --- 終了日（確定できる場合のみ） ---
    end_date = detail.get("end_date")
    _set("end_date", end_date, "zeczec_detail:『已於 … 募資成功/結束』")
    if not end_date:
        reasons["end_date"] = "終了日が明示ラベルで確認できない（推測しない）"
    # 開始日は詳細ページに明示ラベルが無いため常に None（推測しない）
    reasons.setdefault("start_date", "開始日が明示ラベルで確認できない（推測しない）")

    # --- 公式サイト候補（確度つき） ---
    candidates = list(detail.get("official_candidates") or [])
    if extra_candidates:
        candidates += extra_candidates
    candidates = _dedup_candidates(candidates)
    official_url, official_reason = _pick_official_site(candidates)
    _set("maker_url", official_url, "zeczec_detail:公式サイト直リンク(high)")
    reasons["official_site"] = official_reason

    # --- 根拠 JSON（再スクレイプで消えない保管場所） ---
    enrichment = {
        "source": "zeczec_detail",
        "source_detail_url": detail_url,
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "brand_name": maker_name,
        "creator_url": detail.get("creator_url"),
        "project_type": detail.get("project_type"),
        "product_description": detail.get("description"),
        "og_title": detail.get("og_title"),
        "status": detail.get("status"),
        "official_site_candidates": candidates or None,
        "official_site_selected": official_url,
        "socials": detail.get("socials") or None,
        "reasons": reasons,
        "provenance": provenance,
    }

    return {
        "column_updates": column_updates,
        "enrichment": enrichment,
        "reasons": reasons,
        "provenance": provenance,
    }


def apply_enrichment(
    db: Session, project: Project, detail: dict, *, search_fn=None
) -> dict:
    """detail を非破壊で project に適用して commit する。summary を返す。"""
    detail = dict(detail)
    detail.setdefault(
        "source_detail_url",
        (project.source_url or "").split("?")[0] or None,
    )

    extra = []
    # ページに high 直リンクが無いときだけ検索で low 候補を補完（軽量情報源を優先）。
    page_has_high = any(
        c.get("confidence") == "high"
        for c in (detail.get("official_candidates") or [])
    )
    if not page_has_high and not detail.get("challenged"):
        extra = _search_official_candidates(project, search_fn)

    built = build_enrichment_updates(project, detail, extra_candidates=extra)

    for key, value in built["column_updates"].items():
        setattr(project, key, value)
    if built["enrichment"] is not None:
        # 既存 enrichment があってもこの詳細取得の結果で置き換える（根拠の最新化）。
        project.enrichment = built["enrichment"]

    db.commit()
    db.refresh(project)

    return {
        "project_id": project.id,
        "challenged": bool(detail.get("challenged")),
        "updated_fields": sorted(built["column_updates"].keys()),
        "maker_name": project.maker_name,
        "category": project.category,
        "maker_url": project.maker_url,
        "has_description": bool(
            (project.enrichment or {}).get("product_description")
        ),
        "official_candidates": (project.enrichment or {}).get(
            "official_site_candidates"
        ),
        "reasons": built["reasons"],
    }


def enrich_project(
    db: Session,
    project: Project,
    *,
    detail_fetcher=None,
    search_fn=None,
    progress_cb=None,
) -> dict:
    """1 案件をエンリッチする。detail_fetcher.fetch(url)->(status, html, inner) を使う。

    Zeczec 以外はスキップ。詳細取得に失敗/チャレンジなら理由を残して 0 件成功にしない。
    """
    from app.scrapers.zeczec_detail import parse_detail

    if project.source_site != SourceSite.zeczec.value:
        return {"project_id": project.id, "skipped": "not_zeczec"}

    def _log(msg: str, pct: float | None = None) -> None:
        if progress_cb:
            try:
                progress_cb(msg, pct)
            except Exception:  # noqa: BLE001
                pass

    url = (project.source_url or "").split("?")[0]
    if not url:
        return {"project_id": project.id, "skipped": "no_source_url"}

    _log(f"詳細取得: {url}", 0.1)
    own = detail_fetcher is None
    if own:
        from app.scrapers.zeczec_detail_fetcher import ZeczecDetailFetcher

        detail_fetcher = ZeczecDetailFetcher()
    try:
        status, html, inner = detail_fetcher.fetch(url)
    finally:
        if own:
            detail_fetcher.close()

    detail = parse_detail(html, inner)
    detail["source_detail_url"] = url
    if detail.get("challenged") or (status in (401, 403) and not detail.get("maker_name")):
        _log("詳細ページがブロック（Cloudflare チャレンジ）。理由を記録します。", 0.9)
        # 挑戦検出でも理由を enrichment に残す（0 件成功にしない）。
        return apply_enrichment(db, project, {"challenged": True, "source_detail_url": url})

    _log(f"詳細を解析（提案人={detail.get('maker_name')} / カテゴリ={detail.get('category')}）", 0.6)
    return apply_enrichment(db, project, detail, search_fn=search_fn)


def list_zeczec_projects(
    db: Session, *, only_missing: bool = True, limit: int | None = None
) -> list[Project]:
    """エンリッチ対象の Zeczec 案件を返す。

    only_missing=True: メーカー名 or カテゴリ or enrichment が未設定のものだけ。
    """
    stmt = select(Project).where(Project.source_site == SourceSite.zeczec.value)
    stmt = stmt.order_by(Project.id)
    rows = list(db.scalars(stmt))
    if only_missing:
        rows = [
            p
            for p in rows
            if _blank(p.maker_name) or _blank(p.category) or not p.enrichment
        ]
    if limit is not None:
        rows = rows[:limit]
    return rows


def run_enrichment_batch(
    project_ids: list[int] | None = None,
    *,
    only_missing: bool = True,
    limit: int | None = None,
    search_fn=None,
    progress_cb=None,
) -> dict:
    """複数 Zeczec 案件を 1 つのブラウザ（fresh context/page）で順にエンリッチする。

    自前 DB セッションを開くのでデーモンスレッド/CLI から安全に呼べる。重い処理を
    バックグラウンドで回す用途。summary を返す。
    """
    from app.db.session import SessionLocal
    from app.scrapers.zeczec_detail_fetcher import ZeczecDetailFetcher

    db = SessionLocal()
    results: list[dict] = []
    try:
        if project_ids:
            projects = [
                p
                for pid in project_ids
                if (p := db.get(Project, pid)) is not None
                and p.source_site == SourceSite.zeczec.value
            ]
        else:
            projects = list_zeczec_projects(db, only_missing=only_missing, limit=limit)

        if not projects:
            return {"total": 0, "enriched": 0, "blocked": 0, "results": []}

        fetcher = ZeczecDetailFetcher()
        try:
            for i, project in enumerate(projects):
                def _cb(msg, pct=None, _i=i, _n=len(projects), _t=project.title):
                    if progress_cb:
                        base = _i / _n
                        progress_cb(f"[{_i+1}/{_n}] {_t}: {msg}",
                                    base + (pct or 0) / _n)
                try:
                    res = enrich_project(
                        db, project, detail_fetcher=fetcher,
                        search_fn=search_fn, progress_cb=_cb,
                    )
                except Exception as exc:  # noqa: BLE001  1 件失敗で全体を止めない
                    logger.warning("enrich project %s failed: %s", project.id, exc)
                    db.rollback()
                    res = {"project_id": project.id, "error": str(exc)[:300]}
                results.append(res)
        finally:
            fetcher.close()
    finally:
        db.close()

    enriched = sum(1 for r in results if r.get("updated_fields"))
    blocked = sum(1 for r in results if r.get("challenged"))
    return {
        "total": len(results),
        "enriched": enriched,
        "blocked": blocked,
        "results": results,
    }
