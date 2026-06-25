"""収集ジョブ（手動/日次 共通）。

run_collection() が唯一の入口で、日次スケジューラ・手動「今すぐ実行」の双方が
これを呼ぶ。1 ジョブで 4 サイト（KS / Indiegogo / Makuake / GreenFunding）を収集し、
実行履歴（job_runs）と二重実行防止ロック（job_locks）を管理する。

二重実行防止：
  - job_locks に PK(name) で 1 行だけ作れる性質を利用。INSERT 競合（IntegrityError）
    で「既に実行中」を検出するため、複数ワーカー/同時起動でも 1 実行に収束する。
  - 異常終了でロックが残った場合に備え、一定時間（STALE_LOCK_SECONDS）経過した
    ロックは奪取して再実行できる。

サイト単位の成否・件数は scrape_runs（job_run_id で本ジョブに紐づく）に記録する。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.models.job_run import JobLock, JobRun, JobStatus, JobTrigger
from app.models.project import SourceSite
from app.models.scrape_run import ScrapeRun, ScrapeStatus
from app.services import collector, japanese_success_service

logger = logging.getLogger("collection_job")

LOCK_NAME = "daily_collection"
# これより古いロックは異常終了の残骸とみなして奪取する（秒）
STALE_LOCK_SECONDS = 2 * 60 * 60

# 海外案件として収集するサイト（projects テーブル）
OVERSEAS_SITES = [SourceSite.kickstarter, SourceSite.indiegogo]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _try_acquire(db: Session, name: str) -> bool:
    """ロックを取得できれば True。既に有効なロックがあれば False。"""
    existing = db.get(JobLock, name)
    if existing is not None:
        age = (_now() - _as_utc(existing.acquired_at)).total_seconds()
        if age < STALE_LOCK_SECONDS:
            return False
        # 古いロック（クラッシュ残骸）→ 奪取
        existing.acquired_at = _now()
        existing.job_run_id = None
        db.commit()
        return True

    db.add(JobLock(name=name, acquired_at=_now()))
    try:
        db.commit()
    except IntegrityError:
        # 別ワーカーが同時に取得 → 競り負け
        db.rollback()
        return False
    return True


def _release(db: Session, name: str) -> None:
    obj = db.get(JobLock, name)
    if obj is not None:
        db.delete(obj)
        db.commit()


def run_collection(trigger: str = JobTrigger.manual.value, limit: int | None = None) -> dict:
    """4 サイトを収集する（手動/日次 共通）。

    二重実行はスキップして job_runs に skipped を残す。
    Returns: 実行結果サマリ（job_id, status, 件数）。
    """
    limit = limit or settings.scrape_daily_limit
    db = SessionLocal()
    try:
        if not _try_acquire(db, LOCK_NAME):
            logger.warning("collection already running; skipped (trigger=%s)", trigger)
            jr = JobRun(
                trigger=trigger,
                status=JobStatus.skipped.value,
                finished_at=_now(),
                error="既に実行中のためスキップしました",
            )
            db.add(jr)
            db.commit()
            db.refresh(jr)
            return {"job_id": jr.id, "status": jr.status}

        job = JobRun(trigger=trigger, status=JobStatus.running.value)
        db.add(job)
        db.commit()
        db.refresh(job)

        lock = db.get(JobLock, LOCK_NAME)
        if lock is not None:
            lock.job_run_id = job.id
            db.commit()

        logger.info("collection job start (id=%s trigger=%s limit=%s)", job.id, trigger, limit)
        try:
            # 海外案件（Kickstarter / Indiegogo）
            try:
                collector.run_sites(db, OVERSEAS_SITES, limit=limit, job_run_id=job.id)
            except Exception as exc:  # noqa: BLE001  系統単位で握りつぶし継続
                logger.exception("overseas collection failed: %s", exc)
            # 日本案件（Makuake / GreenFunding）
            try:
                japanese_success_service.collect(
                    db, platform=None, limit=limit, job_run_id=job.id
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("japanese collection failed: %s", exc)

            succeeded, failed = _count_site_results(db, job.id)
            job.sites_succeeded = succeeded
            job.sites_failed = failed
            job.status = _job_status(succeeded, failed)
            if succeeded == 0 and failed == 0:
                job.error = "収集結果が記録されませんでした"
            job.finished_at = _now()
            db.add(job)
            db.commit()
            db.refresh(job)
        except Exception as exc:  # noqa: BLE001  想定外のジョブ全体失敗
            db.rollback()
            logger.exception("collection job failed (id=%s): %s", job.id, exc)
            job.status = JobStatus.error.value
            job.error = str(exc)[:2000]
            job.finished_at = _now()
            db.add(job)
            db.commit()
            db.refresh(job)

        logger.info(
            "collection job done (id=%s status=%s ok=%s ng=%s)",
            job.id, job.status, job.sites_succeeded, job.sites_failed,
        )
        return {
            "job_id": job.id,
            "status": job.status,
            "sites_succeeded": job.sites_succeeded,
            "sites_failed": job.sites_failed,
        }
    finally:
        _release(db, LOCK_NAME)
        db.close()


def _count_site_results(db: Session, job_id: int) -> tuple[int, int]:
    succeeded = db.scalar(
        select(func.count()).select_from(ScrapeRun).where(
            ScrapeRun.job_run_id == job_id,
            ScrapeRun.status == ScrapeStatus.success.value,
        )
    ) or 0
    failed = db.scalar(
        select(func.count()).select_from(ScrapeRun).where(
            ScrapeRun.job_run_id == job_id,
            ScrapeRun.status == ScrapeStatus.error.value,
        )
    ) or 0
    return succeeded, failed


def _job_status(succeeded: int, failed: int) -> str:
    if failed == 0 and succeeded > 0:
        return JobStatus.success.value
    if succeeded > 0 and failed > 0:
        return JobStatus.partial.value
    return JobStatus.error.value
