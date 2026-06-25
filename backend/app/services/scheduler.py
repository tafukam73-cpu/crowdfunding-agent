"""日次自動収集スケジューラ（APScheduler・スレッド型）。

毎日決まった時刻に 4 サイトを収集する：
  - 海外案件（projects）：Kickstarter / Indiegogo … collector 経由
  - 日本案件（japanese_success）：Makuake / GreenFunding … japanese_success_service 経由

Playwright（同期 API）は asyncio ループ上で動かせないため、BackgroundScheduler
（専用スレッド）で実行する。各サイトの結果は既存の scrape_runs に記録される。

注意：uvicorn を複数ワーカーで起動すると各ワーカーでスケジューラが動き多重実行に
なる。単一ワーカー運用（既定の docker compose 構成）を前提とする。
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db.session import SessionLocal
from app.models.project import SourceSite
from app.services import collector, japanese_success_service

logger = logging.getLogger("scheduler")

JOB_ID = "daily_collection"

# 海外案件として収集するサイト（projects テーブル）
OVERSEAS_SITES = [SourceSite.kickstarter, SourceSite.indiegogo]

_scheduler: BackgroundScheduler | None = None


def run_daily_collection(limit: int | None = None) -> None:
    """4 サイトを順に収集する（スケジュール／手動の共通処理）。

    1 系統が失敗しても他系統は継続する。サイト単位の成否・件数は
    各収集処理が scrape_runs に記録する。
    """
    limit = limit or settings.scrape_daily_limit
    logger.info("daily collection start (limit=%s)", limit)
    db = SessionLocal()
    try:
        # 海外案件（Kickstarter / Indiegogo）
        try:
            collector.run_sites(db, OVERSEAS_SITES, limit=limit)
        except Exception as exc:  # noqa: BLE001  ジョブ全体は止めない
            logger.exception("overseas collection failed: %s", exc)

        # 日本案件（Makuake / GreenFunding）
        try:
            japanese_success_service.collect(db, platform=None, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("japanese collection failed: %s", exc)
    finally:
        db.close()
    logger.info("daily collection done")


def start() -> None:
    """スケジューラを起動する（有効時のみ・冪等）。"""
    global _scheduler
    if _scheduler is not None:
        return
    sched = BackgroundScheduler(timezone=settings.scrape_timezone)
    trigger = CronTrigger.from_crontab(
        settings.scrape_schedule_cron, timezone=settings.scrape_timezone
    )
    sched.add_job(
        run_daily_collection,
        trigger=trigger,
        id=JOB_ID,
        max_instances=1,      # 多重起動を防止
        coalesce=True,        # 取りこぼしは 1 回にまとめる
        misfire_grace_time=3600,
        replace_existing=True,
    )
    sched.start()
    _scheduler = sched
    logger.info(
        "scheduler started (cron=%r tz=%s)",
        settings.scrape_schedule_cron,
        settings.scrape_timezone,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run_time() -> datetime | None:
    """次回実行予定時刻（未起動なら None）。"""
    if _scheduler is None:
        return None
    job = _scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None
