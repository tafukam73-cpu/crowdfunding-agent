"""収集パイプライン。

スクレイパー実行 → 正規化済み案件を upsert → scrape_runs に結果を記録。
サイトに依存しない後段処理をここに集約する。

実行モデル：
  1) create_pending_runs() … running 状態の ScrapeRun を即時作成（API はこれを返す）
  2) run_pending()         … バックグラウンドで自前セッションを開き各 run を実行
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.project import SourceSite
from app.models.scrape_run import ScrapeRun, ScrapeStatus
from app.scrapers.registry import SUPPORTED_SITES, get_scraper
from app.services import project_service

logger = logging.getLogger("collector")


def create_pending_runs(db: Session, sites: list[SourceSite]) -> list[ScrapeRun]:
    """running 状態の ScrapeRun を作成して返す（即レスポンス用）。"""
    runs = [ScrapeRun(site=s.value, status=ScrapeStatus.running.value) for s in sites]
    db.add_all(runs)
    db.commit()
    for r in runs:
        db.refresh(r)
    return runs


def execute_run(db: Session, run: ScrapeRun, limit: int = 20) -> ScrapeRun:
    """running な ScrapeRun を実際に処理して結果を書き込む。"""
    site = SourceSite(run.site)
    created = updated = fetched = 0
    try:
        scraper = get_scraper(site, limit=limit)
        items = scraper.scrape()
        fetched = len(items)
        for item in items:
            _, was_created = project_service.upsert_by_source_url(db, item)
            if was_created:
                created += 1
            else:
                updated += 1

        run.fetched_count = fetched
        run.created_count = created
        run.updated_count = updated
        run.status = ScrapeStatus.success.value
    except Exception as exc:  # noqa: BLE001  例外は scrape_runs に記録
        db.rollback()
        run.status = ScrapeStatus.error.value
        run.error = str(exc)[:2000]
        logger.warning("scrape failed (site=%s): %s", run.site, exc)
    finally:
        run.finished_at = datetime.now(timezone.utc)
        db.add(run)
        db.commit()
        db.refresh(run)

    return run


def run_pending(run_ids: list[int], limit: int = 20) -> None:
    """バックグラウンドタスク本体。自前の DB セッションで running な run を順に処理。"""
    db = SessionLocal()
    try:
        for rid in run_ids:
            run = db.get(ScrapeRun, rid)
            if run is not None and run.status == ScrapeStatus.running.value:
                execute_run(db, run, limit=limit)
    finally:
        db.close()


# --- 同期実行（テスト・CLI 用の後方互換） ---
def run_site(db: Session, site: SourceSite, limit: int = 20) -> ScrapeRun:
    run = create_pending_runs(db, [site])[0]
    return execute_run(db, run, limit=limit)


def run_sites(
    db: Session, sites: list[SourceSite] | None = None, limit: int = 20
) -> list[ScrapeRun]:
    targets = sites if sites else SUPPORTED_SITES
    return [run_site(db, site, limit=limit) for site in targets]
