"""LQE 除外判定の評価ケースを選定して cases.json へ凍結する（**一度だけ使う選定ツール**）。

このスクリプトだけが DB を読みます。**読み取り専用**で、書き込み・外部 HTTP・
`run()`・Ground Truth の書き換えは一切行いません。

生成物 `cases.json` には `gather_signals()` の結果（signals スナップショット）を
凍結するため、以後の評価（`run_eval.py`）は **DB に触れずに** `qualify()` を
再実行できます。`project_id` に依存しません（参照用に保持するだけ）。

決定性: 同じ DB スナップショットなら同じ 30 件を選びます（乱数は使わず、
`canonical_maker_key` の SHA-256 で安定ソートします）。

実行:
    docker compose exec -T backend python tests/lqe_eval/_select_cases.py
    docker compose exec -T backend python tests/lqe_eval/_select_cases.py --dry-run
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_COMPONENT", "lqe-eval-select")

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.json"

SELECTOR_VERSION = "lqe-eval-selector-v1"

#: サイト別の目標件数（合計 30）。
SITE_QUOTA: dict[str, int] = {
    "kickstarter": 10,
    "indiegogo": 5,
    "wadiz": 10,
    "zeczec": 5,
}

#: キャンペーンの新旧区分。
#: **実データには 180 日以上前に終了した案件が 1 件も無い**（収集が直近のみ）ため、
#: 「1 年以上前かどうか」では区分できない。実際に差がつく「募集終了済み / 募集中 /
#: 不明」で分ける。閾値をいじって古い案件があるように見せかけない。
AGE_ENDED = "ended"
AGE_LIVE = "live"
AGE_UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
#  正規化 / canonical_maker_key
# --------------------------------------------------------------------------- #
def _norm_text(value: str | None) -> str:
    """Unicode 正規化 ＋ 空白・記号差の吸収（maker_name 用）。"""
    if not value:
        return ""
    s = unicodedata.normalize("NFKC", str(value)).lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[.,\-_'\"·・（）()\[\]｜|/\\]+", "", s)
    return s.strip()


def _norm_url(value: str | None) -> str:
    """scheme / www / trailing slash / query / fragment を落とした正規化 URL。"""
    if not value:
        return ""
    raw = unicodedata.normalize("NFKC", str(value)).strip().lower()
    if not raw:
        return ""
    if "//" not in raw:
        raw = "//" + raw
    try:
        p = urlparse(raw if raw.startswith(("http://", "https://")) else "http:" + raw)
    except ValueError:
        return ""
    host = (p.netloc or "").removeprefix("www.")
    path = (p.path or "").rstrip("/")
    if not host:
        return ""
    return f"{host}{path}"


def _norm_host(value: str | None) -> str:
    """ホストだけの正規化（公式サイト用）。"""
    full = _norm_url(value)
    return full.split("/", 1)[0] if full else ""


def _creator_part(campaign_url: str | None) -> str:
    """campaign URL の creator 部分（作者を識別できるプラットフォームのみ）。

    **URL に作者が現れないプラットフォームでは空文字を返します。** 先頭の共通
    セグメントを creator として扱うと、**全案件が同一メーカー扱い**になってしまう
    ためです。

    実 URL 形状（実データで確認）:
      kickstarter : /projects/<creator>/<slug>          ← 作者あり
      indiegogo   : /projects/<creator>/<slug>
                    /en/projects/<creator>/<slug>       ← ロケール接頭辞あり
      wadiz       : /web/campaign/detail/<id>           ← 作者なし
      zeczec      : /projects/<id>                      ← 作者なし
    """
    if not campaign_url:
        return ""
    try:
        p = urlparse(campaign_url)
    except ValueError:
        return ""
    host = (p.netloc or "").lower().removeprefix("www.")
    parts = [x for x in (p.path or "").split("/") if x]
    if "kickstarter.com" in host or "indiegogo.com" in host:
        if "projects" in parts:
            i = parts.index("projects")
            # creator と slug の 2 つが後続する形だけを採用する。
            if len(parts) >= i + 3:
                return f"{host}/projects/{parts[i + 1]}"
    # wadiz（/web/campaign/detail/<id>）と zeczec（/projects/<id>）は
    # URL から作者を特定できないため creator キーを作らない。
    return ""


def maker_key_candidates(project, signals: dict) -> list[tuple[str, str]]:
    """同一メーカー判定に使える**すべての**キー候補を優先順に返す。

    優先順位:
      1. maker_url / creator_url
      2. official_site の正規化 host
      3. maker_name の正規化値
      4. campaign URL の creator 部分
      5. campaign URL 全体

    **project_id は使いません。** maker_name が NULL / 空の案件同士を同一メーカー
    扱いしないよう、空の候補は返しません。
    """
    enrichment = project.enrichment if isinstance(project.enrichment, dict) else {}
    out: list[tuple[str, str]] = []

    for raw in (project.maker_url, enrichment.get("creator_url")):
        u = _norm_url(raw)
        if u:
            out.append((f"url:{u}", "maker_url_or_creator_url"))

    host = _norm_host((signals.get("official_site") or {}).get("url"))
    if host:
        out.append((f"host:{host}", "official_site_host"))

    for raw in (project.maker_name, enrichment.get("brand_name")):
        n = _norm_text(raw)
        if n:
            out.append((f"name:{n}", "maker_name"))

    creator = _creator_part(signals.get("campaign_url"))
    if creator:
        out.append((f"creator:{creator}", "campaign_creator"))

    whole = _norm_url(signals.get("campaign_url"))
    if whole:
        out.append((f"campaign:{whole}", "campaign_url"))

    seen, uniq = set(), []
    for k, src in out:
        if k not in seen:
            seen.add(k)
            uniq.append((k, src))
    return uniq


def canonical_maker_key(project, signals: dict) -> tuple[str, str]:
    """代表キー（最優先の候補）とその導出元。候補が無ければ空文字。"""
    cands = maker_key_candidates(project, signals)
    return cands[0] if cands else ("", "none")


# --------------------------------------------------------------------------- #
#  キャンペーンの新旧
# --------------------------------------------------------------------------- #
def campaign_age_bucket(project, now: datetime) -> str:
    """`ended` / `live` / `unknown`。

    **`unknown` をどちらかへ丸めません。** Zeczec は end_date をほぼ持たないため、
    unknown が出ること自体を評価対象（情報不足）として扱います。
    """
    end = getattr(project, "end_date", None)
    if end is None:
        return AGE_UNKNOWN
    end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    return AGE_LIVE if end_dt > now else AGE_ENDED


# --------------------------------------------------------------------------- #
#  選定
# --------------------------------------------------------------------------- #
def _stable_rank(key: str) -> str:
    """決定的な並び（乱数を使わない）。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _diversity_sort(rows: list[dict]) -> list[dict]:
    """カテゴリ・判定候補・新旧が偏らないように並べ替える。

    (decision, 新旧, カテゴリ) でグループ化し、ラウンドロビンで 1 件ずつ拾う。
    各グループ内は `_stable_rank` 順なので決定的。
    """
    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        buckets.setdefault(
            (r["pre_research_decision"], r["campaign_age_bucket"], r["category_key"]), []
        ).append(r)
    for v in buckets.values():
        v.sort(key=lambda r: r["_rank"])
    ordered_keys = sorted(buckets, key=lambda k: (k[0], k[1], k[2]))
    out: list[dict] = []
    while any(buckets[k] for k in ordered_keys):
        for k in ordered_keys:
            if buckets[k]:
                out.append(buckets[k].pop(0))
    return out


def select(db, now: datetime) -> tuple[list[dict], dict]:
    from app.models.project import Project, SALES_TARGET_SITES, not_archived_clause
    from app.services import campaign_url as cu
    from app.services import lead_qualification_service as lqs

    values = [s.value for s in SALES_TARGET_SITES]
    projects = (
        db.query(Project)
        .filter(Project.source_site.in_(values), not_archived_clause())
        .order_by(Project.id)
        .all()
    )

    stats = {"scanned": len(projects), "no_campaign_url": 0, "no_key": 0,
             "dup_key": 0, "eligible": 0}
    seen_keys: set[str] = set()
    pool: dict[str, list[dict]] = {s: [] for s in SITE_QUOTA}

    for p in projects:
        if p.source_site not in SITE_QUOTA:
            continue
        if not cu.campaign_url_of(p):
            stats["no_campaign_url"] += 1
            continue
        signals = lqs.gather_signals(db, p)
        cands = maker_key_candidates(p, signals)
        if not cands:
            stats["no_key"] += 1
            continue
        # **候補のいずれかが既出なら同一メーカーとみなす。** 代表キーだけで判定すると、
        # 導出元が違う同一メーカー（例: 片方は official_site、片方は maker_name）を
        # 別扱いしてしまう。
        if any(k in seen_keys for k, _ in cands):
            stats["dup_key"] += 1
            continue
        key, key_source = cands[0]
        seen_keys.update(k for k, _ in cands)
        stats["eligible"] += 1

        pre_r = lqs.qualify(signals, lqs.STAGE_PRE_RESEARCH)
        pre_o = lqs.qualify(signals, lqs.STAGE_PRE_OUTREACH)
        pool[p.source_site].append({
            "source_project_id": p.id,
            "source_site": p.source_site,
            "project_name": p.title,
            "maker_name": p.maker_name,
            "category": p.category,
            "category_key": (p.category or "(未分類)"),
            "canonical_maker_key": key,
            "key_source": key_source,
            "campaign_age_bucket": campaign_age_bucket(p, now),
            "pre_research_decision": pre_r.decision,
            "pre_outreach_decision": pre_o.decision,
            "project_snapshot": {
                "title": p.title, "source_site": p.source_site,
                "category": p.category, "maker_name": p.maker_name,
                "campaign_url": signals.get("campaign_url"),
                "end_date": p.end_date.isoformat() if p.end_date else None,
                "backers_count": p.backers_count,
            },
            "signals_snapshot": _jsonable(signals),
            "_rank": _stable_rank(key),
        })

    cases: list[dict] = []
    for site, quota in SITE_QUOTA.items():
        rows = _diversity_sort(pool[site])
        picked = rows[:quota]
        if len(picked) < quota:
            raise SystemExit(
                f"サイト {site} の候補が不足（{len(picked)}/{quota}）。"
                "選定条件を満たす案件が足りません。"
            )
        cases.extend(picked)

    for i, c in enumerate(cases, start=1):
        c["case_id"] = f"LQ{i:02d}"
        c["selected_at"] = now.isoformat()
        c["selector_version"] = SELECTOR_VERSION
        c.pop("_rank", None)
        c.pop("category_key", None)
    return cases, stats


def _jsonable(value):
    """signals を JSON 化できる形へ落とす（datetime → ISO8601）。"""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def main() -> int:
    dry = "--dry-run" in sys.argv
    from app.db.session import SessionLocal
    from app.models.project import Project

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        before = db.query(Project).count()
        cases, stats = select(db, now)
        after = db.query(Project).count()
        if before != after:
            raise SystemExit(f"DB 件数が変化した（{before} -> {after}）。中止する。")
    finally:
        db.close()

    from collections import Counter

    print(f"走査 {stats['scanned']} 件 / 選定候補 {stats['eligible']} 件")
    print(f"  campaign_url なし: {stats['no_campaign_url']} / キー不能: {stats['no_key']}"
          f" / キー重複: {stats['dup_key']}")
    print("サイト構成:", dict(Counter(c["source_site"] for c in cases)))
    print("pre_research:", dict(Counter(c["pre_research_decision"] for c in cases)))
    print("pre_outreach:", dict(Counter(c["pre_outreach_decision"] for c in cases)))
    print("新旧:", dict(Counter(c["campaign_age_bucket"] for c in cases)))
    keys = [c["canonical_maker_key"] for c in cases]
    print(f"canonical_maker_key 重複: {len(keys) - len(set(keys))} 件")
    print(f"DB 件数: {before} -> {after}（不変）")

    if dry:
        print("--dry-run のため cases.json は書きません。")
        return 0
    CASES_PATH.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"書き出し: {CASES_PATH}（{len(cases)} 件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
