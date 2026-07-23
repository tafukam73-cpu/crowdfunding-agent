"""発掘商品（discovered_products）→ 営業対象案件（projects）への昇格ワークフロー。

Discovery で発掘・スコアリングした有望商品を、**ユーザー確認後に**営業対象 projects へ
非破壊で追加し、既存の Sales Assessment / Contact Intelligence / Sales Copilot / CRM へ
接続する。新しいスクレイパーや収集ロジックは足さず、既存サービスを再利用する。

原則（要件）:
- 自動昇格しない。preview（DB 非更新）→ promote（ユーザー確認後）で進める。
- 推測しない。メーカー名・URL・メールを勝手に作らない（欠損は null のまま）。
- 非破壊。projects / discovered_products を削除・初期化しない。重い処理は背景ジョブ。
- 重複は新規作成しない。既存 project を duplicate_project として指す。
- API は 3 秒以内。重い Contact Intelligence 探索は queued ジョブに委ねる（同期実行しない）。

重複判定の優先順位:
  1. source_url 完全一致（projects 全体・source_url は一意）
  2. platform + source_id（同一 source_site 内で URL から抽出した ID の一致）
  3. 正規化 URL 一致（scheme/www/クエリ/末尾スラッシュを無視、同一 source_site 内）
  4. title + maker_name の近似一致 → **警告のみ**（自動統合しない）
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.discovered_product import (
    DiscoveredProduct,
    DiscoveryPromotionStatus,
)
from app.models.project import Project, SourceSite
from app.schemas.project import ProjectCreate

logger = logging.getLogger("discovery_promotion")

# 昇格処理でまとめて扱う最大件数（一括昇格の安全上限）。
MAX_BATCH_PROMOTE = 50

# 発掘元プラットフォーム → 営業対象案件（Project）の SourceSite。
# 対応が無いものは other（営業対象一覧に出ないが、昇格自体は可能）。
_PLATFORM_TO_SITE: dict[str, SourceSite] = {
    "kickstarter": SourceSite.kickstarter,
    "indiegogo": SourceSite.indiegogo,
    "wadiz": SourceSite.wadiz,
    "zeczec": SourceSite.zeczec,
}

# source_id 抽出時に無視するパスの汎用セグメント（言語・種別プレフィックス等）。
_ID_SKIP_SEGMENTS = {
    "projects", "project", "campaign", "detail", "example", "discover",
    "categories", "category", "p", "c", "en", "ko", "zh", "zh-tw", "ja", "fr",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _map_site(platform: str | None) -> SourceSite:
    return _PLATFORM_TO_SITE.get((platform or "").lower(), SourceSite.other)


def _canon_url(url: str | None) -> str | None:
    """正規化 URL（scheme/www/クエリ/フラグメント/末尾スラッシュを無視・小文字）。"""
    if not url:
        return None
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u or None


def _source_id(platform: str | None, url: str | None) -> str | None:
    """URL からプラットフォーム内で一意になりやすい ID（slug / campaign id）を抽出する。

    推測はしない（URL から機械的に取り出せる範囲）。取り出せなければ None。
    """
    if not url:
        return None
    if (platform or "").lower() == "wadiz":
        from app.services.wadiz_import_service import extract_campaign_id

        cid = extract_campaign_id(url)
        if cid:
            return cid
    canon = _canon_url(url)
    if not canon:
        return None
    parts = canon.split("/")
    # 先頭（host）を除き、汎用セグメントを飛ばして最後の意味のある slug を返す。
    segs = [s for s in parts[1:] if s and s not in _ID_SKIP_SEGMENTS]
    return segs[-1] if segs else None


def _norm_name(name: str | None) -> str:
    """近似一致用にメーカー名/タイトルを正規化（小文字・記号除去・空白圧縮）。"""
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"[^0-9a-z぀-ヿ一-鿿가-힣]+", " ", s)
    return " ".join(s.split()).strip()


def _product_title(product: DiscoveredProduct) -> str:
    return (
        product.product_name
        or product.project_title
        or product.creator_name
        or f"発掘商品 #{product.id}"
    )


# ---------------------------------------------------------------------------
#  重複判定
# ---------------------------------------------------------------------------
def find_duplicate_project(
    db: Session, product: DiscoveredProduct
) -> tuple[Project | None, str | None]:
    """既存 projects の中から同一商品を探す。(project, match_kind) を返す。

    match_kind: "source_url" / "platform_source_id" / "normalized_url" / None
    近似一致（title + maker_name）はここでは返さない（警告のみ・自動統合しない）。
    """
    # 1) source_url 完全一致（source_url は projects 全体で一意）
    if product.source_url:
        exact = db.scalar(
            select(Project).where(Project.source_url == product.source_url)
        )
        if exact is not None:
            return exact, "source_url"

    site = _map_site(product.source_platform)
    target_id = _source_id(product.source_platform, product.source_url)
    target_canon = _canon_url(product.source_url)
    if target_id is None and target_canon is None:
        return None, None

    # 同一 source_site の案件を 1 度だけ取得して照合（platform+source_id → 正規化 URL）。
    rows = list(
        db.scalars(select(Project).where(Project.source_site == site.value))
    )
    if target_id is not None:
        for p in rows:
            if _source_id(product.source_platform, p.source_url) == target_id:
                return p, "platform_source_id"
    if target_canon is not None:
        for p in rows:
            if _canon_url(p.source_url) == target_canon:
                return p, "normalized_url"
    return None, None


def find_approximate_matches(
    db: Session, product: DiscoveredProduct, *, limit: int = 5
) -> list[Project]:
    """title + maker_name の近似一致案件を返す（警告用。自動統合しない）。

    タイトルの正規化一致、またはメーカー名の正規化一致を候補とする。source_url が
    別（＝別キャンペーン）でも似ていれば警告として提示する。
    """
    title = _norm_name(_product_title(product))
    maker = _norm_name(product.creator_name)
    if not title and not maker:
        return []

    site = _map_site(product.source_platform)
    rows = list(
        db.scalars(select(Project).where(Project.source_site == site.value))
    )
    out: list[Project] = []
    for p in rows:
        if product.source_url and p.source_url == product.source_url:
            continue  # 完全一致は重複側で扱う
        p_title = _norm_name(p.title)
        p_maker = _norm_name(p.maker_name)
        title_hit = bool(title) and (title == p_title or title in p_title or p_title in title)
        maker_hit = bool(maker) and bool(p_maker) and (
            maker == p_maker or maker in p_maker or p_maker in maker
        )
        if title_hit or maker_hit:
            out.append(p)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
#  昇格対象のフィールド組み立て
# ---------------------------------------------------------------------------
# projects へ移す予定の項目キー（preview 表示・欠損判定に使う）。
_TARGET_FIELDS = [
    "source_platform", "source_url", "title", "description", "image_url",
    "category", "maker_name", "official_website_url", "raised_amount",
    "currency", "backers", "achievement_rate",
]


def _brand_name(product: DiscoveredProduct) -> str | None:
    """発掘元生データから確認済みブランド名を取り出す（無ければ None。推測しない）。"""
    raw = product.raw_data or {}
    for key in ("brand_name", "brandName", "brand"):
        v = raw.get(key)
        if v:
            return str(v)[:255]
    enr = raw.get("enrichment") or {}
    v = enr.get("brand_name")
    return str(v)[:255] if v else None


def _achievement_rate(product: DiscoveredProduct) -> int | None:
    """達成率（%）= funding_amount / funding_goal × 100。目標額が無ければ None。"""
    raw = product.raw_data or {}
    if raw.get("rate") is not None:
        try:
            return int(raw["rate"])
        except (TypeError, ValueError):
            pass
    r, g = product.funding_amount, product.funding_goal
    try:
        if r is not None and g is not None and float(g) > 0:
            return int(round(float(r) / float(g) * 100))
    except (TypeError, ValueError):
        return None
    return None


def build_target_fields(product: DiscoveredProduct) -> dict:
    """projects へ移す予定の項目（preview 表示・実際の作成の双方で使う）。"""
    return {
        "source_platform": product.source_platform,
        "source_site": _map_site(product.source_platform).value,
        "source_url": product.source_url,
        "title": _product_title(product)[:500],
        "description": product.description,
        "image_url": product.image_url,
        "category": product.category,
        "maker_name": product.creator_name,       # 推測しない（無ければ null）
        "brand_name": _brand_name(product),
        "official_website_url": product.official_website_url,  # 推測しない
        "raised_amount": (
            float(product.funding_amount)
            if product.funding_amount is not None else None
        ),
        "funding_goal": (
            float(product.funding_goal)
            if product.funding_goal is not None else None
        ),
        "currency": product.currency,
        "backers": product.backers_count,
        "achievement_rate": _achievement_rate(product),
    }


def _missing_fields(fields: dict) -> list[str]:
    """欠損している主要項目（画面の『データ不足』表示用）。title は必須のため除外。"""
    miss: list[str] = []
    for key in (
        "source_url", "description", "image_url", "category", "maker_name",
        "official_website_url", "raised_amount", "currency", "backers",
    ):
        if fields.get(key) in (None, ""):
            miss.append(key)
    return miss


# ---------------------------------------------------------------------------
#  プレビュー（DB 非更新）
# ---------------------------------------------------------------------------
def build_preview(db: Session, product: DiscoveredProduct) -> dict:
    """昇格プレビューを組み立てる（DB は一切更新しない）。"""
    fields = build_target_fields(product)
    dup, kind = find_duplicate_project(db, product)
    approx = find_approximate_matches(db, product)
    missing = _missing_fields(fields)

    warnings: list[str] = []
    if dup is not None:
        warnings.append(
            f"既存の営業案件と重複しています（一致: {kind} / project #{dup.id}）。"
            "新規作成せず既存案件を使います。"
        )
    if approx:
        warnings.append(
            "タイトル/メーカー名が近い既存案件があります（近似一致・自動統合はしません）："
            + ", ".join(f"#{p.id} {p.title[:40]}" for p in approx)
        )
    if not product.source_url:
        warnings.append("source_url が未設定です（重複判定・営業起点が弱くなります）。")
    if not product.creator_name:
        warnings.append("メーカー名が未取得です（推測はしません。空のまま昇格します）。")
    if not product.official_website_url:
        warnings.append(
            "公式サイト URL が未取得です（Contact Intelligence 探索で補完されます）。"
        )

    already = product.promotion_status == DiscoveryPromotionStatus.promoted.value
    if already:
        recommended = "already_promoted"
    elif dup is not None:
        recommended = "review_duplicate"
    else:
        recommended = "promote"

    return {
        "product_id": product.id,
        "promotion_status": product.promotion_status,
        "promoted_project_id": product.promoted_project_id,
        "target_fields": fields,
        "target_field_keys": _TARGET_FIELDS,
        "duplicate_project": (
            {
                "id": dup.id,
                "title": dup.title,
                "source_url": dup.source_url,
                "source_site": dup.source_site,
                "match_kind": kind,
            }
            if dup is not None
            else None
        ),
        "approximate_matches": [
            {
                "id": p.id,
                "title": p.title,
                "maker_name": p.maker_name,
                "source_url": p.source_url,
            }
            for p in approx
        ],
        "missing_fields": missing,
        "warnings": warnings,
        "recommended_action": recommended,
    }


# ---------------------------------------------------------------------------
#  昇格の実行（ユーザー確認後）
# ---------------------------------------------------------------------------
# 昇格後に queued する Contact Intelligence ジョブ種別（重い探索は背景で実行）。
from app.models.contact_intelligence_job import CIJobType  # noqa: E402

DEFAULT_CONTACT_JOB_TYPE = CIJobType.full_contact_intelligence.value


def _build_enrichment(product: DiscoveredProduct, fields: dict) -> dict:
    """昇格の由来（provenance）を projects.enrichment に非破壊で残す。

    新規 project なので enrichment は空。source_discovered_product_id と発掘元の
    生データ（raw provenance）・発掘スコアを保管し、再スクレイプでも消えないようにする。
    """
    now = _now().isoformat()
    return {
        "provenance": {"discovery_promotion": now},
        "source_discovered_product_id": product.id,
        "discovery": {
            "source_platform": product.source_platform,
            "brand_name": fields.get("brand_name"),
            "achievement_rate": fields.get("achievement_rate"),
            "overall_discovery_score": product.overall_discovery_score,
            "japan_fit_score": product.japan_fit_score,
            "crowdfunding_fit_score": product.crowdfunding_fit_score,
            "novelty_score": product.novelty_score,
            "country": product.country,
            "raw_data": product.raw_data,
            "promoted_at": now,
        },
    }


def _create_project(db: Session, product: DiscoveredProduct, fields: dict) -> Project:
    """発掘商品から営業案件（Project）を非破壊で新規作成する（推測しない）。"""
    from app.services import project_service

    data = ProjectCreate(
        title=fields["title"],
        source_site=_map_site(product.source_platform),
        source_url=product.source_url,
        category=product.category,
        description=product.description,
        image_url=product.image_url,
        currency=product.currency or "USD",
        goal_amount=product.funding_goal,
        raised_amount=product.funding_amount,
        backers_count=product.backers_count,
        start_date=product.launch_date,
        end_date=product.end_date,
        maker_name=product.creator_name,            # 推測しない
        maker_url=product.official_website_url,      # 推測しない（無ければ null）
    )
    project = project_service.create_project(db, data)
    # 由来（provenance）を非破壊で付与
    project.enrichment = _build_enrichment(product, fields)
    db.commit()
    db.refresh(project)
    return project


def _run_sales_assessment(db: Session, project: Project) -> tuple[str, int | None]:
    """Sales Assessment をルールベースで作成する（軽量・決定的・外部 HTTP なし）。

    Returns: (status, assessment_id)。失敗しても昇格は成功扱い（status="failed"）。
    """
    from app.services import sales_assessment_service

    try:
        row = sales_assessment_service.run_assessment(db, project)
        return "created", row.id
    except Exception as exc:  # noqa: BLE001  評価失敗で昇格自体は失敗にしない
        logger.warning("sales assessment failed on promote (project=%s): %s",
                       project.id, exc)
        db.rollback()
        return "failed", None


def _queue_contact_intelligence(
    db: Session,
    project: Project,
    *,
    job_type: str = DEFAULT_CONTACT_JOB_TYPE,
    runner=None,
) -> tuple[int | None, str | None]:
    """Contact Intelligence ジョブを queued で作成する（重い探索は背景実行）。

    Returns: (job_id, status)。作成失敗しても昇格は成功扱い。runner はテスト注入用。
    """
    from app.services import contact_intelligence_service

    try:
        job, _from_cache = contact_intelligence_service.create_job(
            db, project, job_type, runner=runner
        )
        return job.id, job.status
    except Exception as exc:  # noqa: BLE001  ジョブ作成失敗でも昇格は成功
        logger.warning("contact intelligence job creation failed (project=%s): %s",
                       project.id, exc)
        db.rollback()
        return None, None


def promote(
    db: Session,
    product_id: int,
    *,
    contact_job_type: str = DEFAULT_CONTACT_JOB_TYPE,
    queue_contact_intelligence: bool = True,
    contact_runner=None,
    assessment_runner=None,
) -> dict | None:
    """発掘商品を営業対象 projects へ昇格する（ユーザー確認後に呼ぶ）。

    - 既に promoted 済み → 冪等に既存 project_id を返す（新規作成しない）。
    - 重複 project あり → duplicate_project として既存 id を返す（新規作成しない）。
    - それ以外 → project を非破壊作成し、Sales Assessment を作成、Contact Intelligence
      ジョブを queued にする。重い処理は背景（同期実行しない）。

    Returns: 商品が無ければ None（router で 404）。それ以外は結果 dict。
    """
    product = db.get(DiscoveredProduct, product_id)
    if product is None:
        return None

    now = _now()

    # 冪等：既に昇格済みで昇格先 project が生きていれば再作成しない。
    if (
        product.promotion_status == DiscoveryPromotionStatus.promoted.value
        and product.promoted_project_id is not None
    ):
        existing = db.get(Project, product.promoted_project_id)
        if existing is not None:
            return {
                "status": "promoted",
                "already_promoted": True,
                "product_id": product.id,
                "project_id": existing.id,
                "promotion_status": product.promotion_status,
                "created_project": False,
                "duplicate_match_kind": None,
                "assessment_status": "skipped",
                "assessment_id": None,
                "contact_job_id": None,
                "contact_job_status": None,
                "message": "既に昇格済みです（新規作成しません）。",
            }

    # 重複 project（source_url / platform+source_id / 正規化 URL）。近似一致は含めない。
    dup, kind = find_duplicate_project(db, product)
    if dup is not None:
        product.promotion_status = DiscoveryPromotionStatus.duplicate_project.value
        product.promoted_project_id = dup.id
        product.promoted_at = now
        product.promotion_error = None
        db.commit()
        db.refresh(product)
        return {
            "status": "duplicate_project",
            "already_promoted": False,
            "product_id": product.id,
            "project_id": dup.id,
            "promotion_status": product.promotion_status,
            "created_project": False,
            "duplicate_match_kind": kind,
            "assessment_status": "skipped",
            "assessment_id": None,
            "contact_job_id": None,
            "contact_job_status": None,
            "message": f"既存の営業案件と重複します（一致: {kind}）。新規作成しません。",
        }

    # 処理中フラグ（二重押下・重複処理の抑止。ここから作成は短時間で完了する）。
    product.promotion_status = DiscoveryPromotionStatus.promotion_pending.value
    db.commit()

    try:
        fields = build_target_fields(product)
        project = _create_project(db, product, fields)
    except Exception as exc:  # noqa: BLE001  作成失敗は promotion_failed に記録
        db.rollback()
        product = db.get(DiscoveredProduct, product_id)
        if product is not None:
            product.promotion_status = DiscoveryPromotionStatus.promotion_failed.value
            product.promotion_error = str(exc)[:2000]
            db.commit()
        logger.warning("promotion failed to create project (product=%s): %s",
                       product_id, exc)
        return {
            "status": "failed",
            "already_promoted": False,
            "product_id": product_id,
            "project_id": None,
            "promotion_status": DiscoveryPromotionStatus.promotion_failed.value,
            "created_project": False,
            "duplicate_match_kind": None,
            "assessment_status": "skipped",
            "assessment_id": None,
            "contact_job_id": None,
            "contact_job_status": None,
            "message": f"営業案件の作成に失敗しました: {exc}",
        }

    # Sales Assessment（軽量・同期）。Sales Copilot v1/v2 が即座に参照できる。
    if assessment_runner is None:
        assessment_status, assessment_id = _run_sales_assessment(db, project)
    else:
        assessment_status, assessment_id = assessment_runner(db, project)

    # Contact Intelligence ジョブ（重い探索は背景で queued）。
    contact_job_id: int | None = None
    contact_job_status: str | None = None
    if queue_contact_intelligence:
        contact_job_id, contact_job_status = _queue_contact_intelligence(
            db, project, job_type=contact_job_type, runner=contact_runner
        )

    # 商品を promoted に更新（昇格先 project を記録）。
    product = db.get(DiscoveredProduct, product_id)
    product.promotion_status = DiscoveryPromotionStatus.promoted.value
    product.promoted_project_id = project.id
    product.promoted_at = now
    product.promotion_error = None
    db.commit()
    db.refresh(product)

    logger.info(
        "discovered product promoted: product=%s project=%s assessment=%s ci_job=%s",
        product.id, project.id, assessment_status, contact_job_id,
    )
    return {
        "status": "promoted",
        "already_promoted": False,
        "product_id": product.id,
        "project_id": project.id,
        "promotion_status": product.promotion_status,
        "created_project": True,
        "duplicate_match_kind": None,
        "assessment_status": assessment_status,
        "assessment_id": assessment_id,
        "contact_job_id": contact_job_id,
        "contact_job_status": contact_job_status,
        "message": "営業案件へ昇格しました。",
    }


def promote_batch(
    db: Session,
    product_ids: list[int],
    *,
    contact_job_type: str = DEFAULT_CONTACT_JOB_TYPE,
    queue_contact_intelligence: bool = True,
    contact_runner=None,
    assessment_runner=None,
) -> dict:
    """複数の発掘商品を 1 件ずつ独立に昇格する（一部失敗でも継続）。

    - 重複 ID は 1 回だけ処理（同一商品の重複処理防止）。
    - 最大 MAX_BATCH_PROMOTE 件まで（安全上限）。
    - 各件の結果を個別に返す（成功 / 重複 / 失敗 / 既昇格 / 不存在）。
    """
    # 重複除去（入力順を保つ）。
    seen: set[int] = set()
    ordered: list[int] = []
    for pid in product_ids:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    capped = ordered[:MAX_BATCH_PROMOTE]

    results: list[dict] = []
    counts = {
        "promoted": 0, "duplicate_project": 0, "failed": 0,
        "already_promoted": 0, "not_found": 0,
    }
    for pid in capped:
        try:
            res = promote(
                db, pid,
                contact_job_type=contact_job_type,
                queue_contact_intelligence=queue_contact_intelligence,
                contact_runner=contact_runner,
                assessment_runner=assessment_runner,
            )
        except Exception as exc:  # noqa: BLE001  1 件失敗で全体を止めない
            db.rollback()
            logger.warning("batch promote item failed (product=%s): %s", pid, exc)
            results.append({
                "status": "failed", "product_id": pid, "project_id": None,
                "message": f"昇格に失敗しました: {exc}",
            })
            counts["failed"] += 1
            continue
        if res is None:
            results.append({
                "status": "not_found", "product_id": pid, "project_id": None,
                "message": "商品候補が見つかりません。",
            })
            counts["not_found"] += 1
            continue
        results.append(res)
        if res.get("already_promoted"):
            counts["already_promoted"] += 1
        else:
            counts[res["status"]] = counts.get(res["status"], 0) + 1

    return {
        "requested": len(product_ids),
        "processed": len(capped),
        "skipped_over_limit": max(0, len(ordered) - len(capped)),
        "counts": counts,
        "results": results,
    }
