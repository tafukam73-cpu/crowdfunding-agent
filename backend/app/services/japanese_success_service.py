"""日本クラファン成功案件の業務ロジック。

- collect()       … Makuake 等から成功案件を収集して upsert
- list_*          … 一覧取得（フィルタ・ソート・ページング）
- find_similar()  … 海外案件に類似する日本の成功事例を抽出

類似度判定は「カテゴリ一致 + 達成率（成功の強さ）+ 共通キーワード」の単純な
ヒューリスティック。AI 評価と同様、後から改善しやすいよう独立関数にしている。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.models.japanese_success import JapaneseSuccessProject
from app.models.project import Project
from app.models.scrape_run import ScrapeRun, ScrapeStatus
from app.scrapers.greenfunding import GreenFundingScraper
from app.scrapers.makuake import MakuakeScraper
from app.schemas.japanese_success import CollectResult, JapaneseSuccessCreate

logger = logging.getLogger("japanese_success")

# 収集対象の日本クラファンプラットフォーム（platform 未指定時はこの全件を収集）
JAPANESE_PLATFORMS: list[str] = [
    MakuakeScraper.platform,
    GreenFundingScraper.platform,
]

# platform → スクレイパークラス
_SCRAPERS = {
    MakuakeScraper.platform: MakuakeScraper,
    GreenFundingScraper.platform: GreenFundingScraper,
}

# 並び替えに使えるカラム
SORTABLE = {
    "created_at": JapaneseSuccessProject.created_at,
    "raised_amount": JapaneseSuccessProject.raised_amount,
    "backers_count": JapaneseSuccessProject.backers_count,
    "end_date": JapaneseSuccessProject.end_date,
    "title": JapaneseSuccessProject.title,
}


# --- 収集（upsert） ---
def upsert_by_source_url(
    db: Session, data: JapaneseSuccessCreate
) -> tuple[JapaneseSuccessProject, bool]:
    """source_url をキーに upsert する。コミットは呼び出し側で行う。

    Returns: (obj, created)  created=True なら新規。
    """
    existing: JapaneseSuccessProject | None = None
    if data.source_url:
        existing = db.scalar(
            select(JapaneseSuccessProject).where(
                JapaneseSuccessProject.source_url == data.source_url
            )
        )

    payload = data.model_dump()
    if existing is None:
        obj = JapaneseSuccessProject(**payload)
        db.add(obj)
        return obj, True

    for key, value in payload.items():
        setattr(existing, key, value)
    return existing, False


def _collect_one(
    db: Session, platform: str, limit: int, job_run_id: int | None = None
) -> tuple[int, int, int]:
    """1 プラットフォームを収集し scrape_runs に記録する。

    実行結果（成功/エラー・件数）を scrape_runs に残す（要件）。
    収集失敗時も例外を送出せず、この回の件数 0 を返して他サイトの収集を続ける。
    Returns: (fetched, created, updated)
    """
    run = ScrapeRun(
        site=platform, status=ScrapeStatus.running.value, job_run_id=job_run_id
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    fetched = created = updated = 0
    try:
        scraper = _SCRAPERS[platform](limit=limit)
        items = scraper.scrape()
        fetched = len(items)
        for item in items:
            _, was_created = upsert_by_source_url(db, item)
            if was_created:
                created += 1
            else:
                updated += 1
        run.fetched_count = fetched
        run.created_count = created
        run.updated_count = updated
        run.status = ScrapeStatus.success.value
    except Exception as exc:  # noqa: BLE001  失敗は scrape_runs に記録し継続
        db.rollback()
        run.status = ScrapeStatus.error.value
        run.error = str(exc)[:2000]
        fetched = created = updated = 0
        logger.warning("japanese-success collect failed (%s): %s", platform, exc)
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        db.refresh(run)

    return fetched, created, updated


def collect(
    db: Session,
    platform: str | None = None,
    limit: int = 20,
    job_run_id: int | None = None,
) -> CollectResult:
    """日本クラファンの成功案件を収集して保存する（実スクレイピング・Playwright）。

    platform 指定あり：そのプラットフォームのみ収集。
    platform 指定なし：Makuake + GreenFunding を一括収集。
    各プラットフォームの実行結果は scrape_runs に記録する（job_run_id でジョブに紐づく）。
    重複は source_url をキーに upsert して防止する。
    """
    if platform is not None and platform not in _SCRAPERS:
        raise ValueError(f"未対応のプラットフォームです: {platform}")

    platforms = [platform] if platform else list(JAPANESE_PLATFORMS)

    fetched = created = updated = 0
    for p in platforms:
        f, c, u = _collect_one(db, p, limit, job_run_id=job_run_id)
        fetched += f
        created += c
        updated += u
    return CollectResult(fetched=fetched, created=created, updated=updated)


def seed_if_empty(db: Session) -> int:
    """成功案件が 1 件も無ければ収集して投入する（開発用・冪等）。投入件数を返す。"""
    count = db.scalar(
        select(func.count()).select_from(JapaneseSuccessProject)
    ) or 0
    if count > 0:
        return 0
    return collect(db).created


# --- 一覧 ---
def list_items(
    db: Session,
    *,
    platform: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "raised_amount",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[JapaneseSuccessProject], int]:
    conditions = []
    if platform:
        conditions.append(JapaneseSuccessProject.platform == platform)
    if category:
        conditions.append(JapaneseSuccessProject.category == category)
    if q:
        conditions.append(JapaneseSuccessProject.title.ilike(f"%{q}%"))

    base = select(JapaneseSuccessProject)
    count_stmt = select(func.count()).select_from(JapaneseSuccessProject)
    for cond in conditions:
        base = base.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.scalar(count_stmt) or 0

    sort_col = SORTABLE.get(sort, JapaneseSuccessProject.raised_amount)
    direction = asc if order == "asc" else desc
    base = base.order_by(direction(sort_col).nullslast())

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    base = base.offset((page - 1) * page_size).limit(page_size)

    return list(db.scalars(base)), total


# --- 類似事例マッチング ---
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _keywords(text: str | None) -> set[str]:
    """英数トークン（3 文字以上）を小文字で抽出する。"""
    if not text:
        return set()
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text)}


def _funding_rate(obj) -> int | None:
    """達成率（%）。goal/raised が無ければ None。"""
    if not obj.goal_amount or not obj.raised_amount:
        return None
    return int(round(float(obj.raised_amount) / float(obj.goal_amount) * 100))


def _funding_bonus(rate: int | None) -> tuple[int, str | None]:
    """達成率に応じた加点と理由文。"""
    if rate is None:
        return 0, None
    if rate >= 1000:
        return 25, f"日本で達成率 {rate:,}% の大型成功"
    if rate >= 300:
        return 15, f"日本で達成率 {rate:,}% の成功実績"
    if rate >= 100:
        return 8, f"日本で目標達成（{rate:,}%）"
    return 0, None


def _similarity(
    project: Project, jp: JapaneseSuccessProject
) -> tuple[int, list[str]]:
    """海外案件と日本成功案件の類似度（0〜100）と理由を返す。"""
    score = 0
    reasons: list[str] = []

    same_category = bool(
        project.category and jp.category and project.category == jp.category
    )
    if same_category:
        score += 70
        reasons.append(f"同じカテゴリ「{jp.category}」")

    common = _keywords(project.title) & _keywords(jp.title)
    if common:
        score += min(15, 5 * len(common))
        reasons.append("共通キーワード: " + ", ".join(sorted(common)))

    # カテゴリも共通キーワードも無ければ「類似」とはみなさない
    if not same_category and not common:
        return 0, []

    bonus, bonus_reason = _funding_bonus(_funding_rate(jp))
    score += bonus
    if bonus_reason:
        reasons.append(bonus_reason)

    return min(100, score), reasons


def find_similar(
    db: Session, project: Project, limit: int = 3
) -> list[tuple[JapaneseSuccessProject, int, list[str]]]:
    """海外案件に類似する日本の成功事例を抽出する。

    類似と判定できる事例が無い場合は、参考として高調達の成功事例を返す。
    Returns: [(成功案件, 類似度, 理由リスト), ...] 類似度の高い順。
    """
    candidates = list(db.scalars(select(JapaneseSuccessProject)))

    scored: list[tuple[JapaneseSuccessProject, int, list[str]]] = []
    for c in candidates:
        s, reasons = _similarity(project, c)
        if s > 0:
            scored.append((c, s, reasons))

    if not scored:
        # フォールバック：高調達の成功事例を参考表示
        candidates.sort(key=lambda c: float(c.raised_amount or 0), reverse=True)
        for c in candidates[:limit]:
            bonus, bonus_reason = _funding_bonus(_funding_rate(c))
            reasons = ["参考：日本の高調達成功事例"]
            if bonus_reason:
                reasons.append(bonus_reason)
            scored.append((c, max(1, bonus), reasons))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
