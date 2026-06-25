"""日次自動収集スケジューラ（APScheduler・スレッド型）。

毎日決まった時刻に収集ジョブ（collection_job.run_collection）を起動する。
収集本体・二重実行防止・履歴記録は collection_job が担うため、ここは
「定時に手動と同じジョブを呼ぶ」役割のみを持つ。

Playwright（同期 API）は asyncio ループ上で動かせないため、BackgroundScheduler
（専用スレッド）で実行する。

注意：uvicorn を複数ワーカーで起動した場合、各ワーカーでスケジューラが動くが、
collection_job 側の DB ロックにより実際の収集は 1 ワーカーのみが実行する。
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.models.job_run import JobTrigger
from app.services import collection_job

logger = logging.getLogger("scheduler")

JOB_ID = "daily_collection"

_scheduler: BackgroundScheduler | None = None


def run_daily_collection() -> None:
    """スケジュール起動の入口（手動実行と同じジョブを trigger=schedule で呼ぶ）。"""
    collection_job.run_collection(trigger=JobTrigger.schedule.value)


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
        max_instances=1,      # 同一プロセス内の多重起動を防止
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
