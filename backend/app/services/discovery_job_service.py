"""商品発掘（Discovery Crawler）の非同期ジョブ実行。

一覧取得〜スコアリングを HTTP リクエスト内で完了させると 12 秒でタイムアウトして
画面が固まる（過去の Contact Intelligence 同期 POST と同じ回帰）。そこで POST は
ジョブ行（queued）を作って即返し、実体はデーモンスレッドで実行、進捗・結果は
GET でポーリングする。

設計（Contact Intelligence ジョブと責務分離）:
- 独立したテーブル（discovery_jobs）と **独立した同時実行セマフォ** を使う。収集
  ジョブと探索ジョブが互いに並列枠を奪わない（要件 7）。
- セマフォ待ちの間は DB セッションを開かない（コネクションを掴んだまま待たない）。
- 実際の収集は既存の ``discovery_crawler_service.run`` を再利用する。外部 HTTP
  （adapter.discover）は最初の DB 書き込みより前に行われ、その間 DB トランザクションを
  保持しない（idle in transaction を残さない）。
- 同一 platform × query × limit の queued/running が既にあれば重複作成しない（要件 7）。
- 起動時に孤児（queued/running のまま残った）ジョブを failed に回収する。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.models.discovery_job import DiscoveryJob, DiscoveryJobStatus
from app.services import discovery_crawler_service
from app.services.discovery_adapters import needs_fetch_injection, normalize_platform

logger = logging.getLogger("discovery_job")

# 発掘ジョブ専用の同時実行セマフォ（Contact Intelligence とは別枠）。
_MAX_CONCURRENT = max(1, int(getattr(settings, "discovery_max_concurrent_jobs", 1)))
_job_semaphore = threading.BoundedSemaphore(_MAX_CONCURRENT)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_job(db: Session, job_id: int) -> DiscoveryJob | None:
    return db.get(DiscoveryJob, job_id)


def find_active(
    db: Session, platform: str, query: str | None, limit: int
) -> DiscoveryJob | None:
    """同一条件（platform × query × limit）の進行中ジョブを返す（重複作成の抑止）。"""
    stmt = (
        select(DiscoveryJob)
        .where(
            DiscoveryJob.source_platform == platform,
            DiscoveryJob.query.is_(None) if query in (None, "") else DiscoveryJob.query == query,
            DiscoveryJob.limit == limit,
            DiscoveryJob.status.in_(
                [DiscoveryJobStatus.queued.value, DiscoveryJobStatus.running.value]
            ),
        )
        .order_by(desc(DiscoveryJob.id))
        .limit(1)
    )
    return db.scalar(stmt)


def recover_orphaned_jobs(db: Session) -> int:
    """起動時に残っている queued/running ジョブを failed に回収する。"""
    stmt = select(DiscoveryJob).where(
        DiscoveryJob.status.in_(
            [DiscoveryJobStatus.queued.value, DiscoveryJobStatus.running.value]
        )
    )
    rows = list(db.scalars(stmt))
    for job in rows:
        job.status = DiscoveryJobStatus.failed.value
        job.error = "バックエンド再起動により中断されました（再実行してください）"
        job.current_step = "中断"
        job.completed_at = _now()
    if rows:
        db.commit()
        logger.info("recovered %d orphaned discovery jobs on startup", len(rows))
    return len(rows)


def create_job(
    db: Session,
    *,
    source_platform: str,
    query: str | None = None,
    limit: int = 20,
    auto_score: bool = True,
    runner=None,
) -> tuple[DiscoveryJob, bool]:
    """発掘ジョブを作成する。(job, is_new) を返す。

    同一条件の進行中ジョブがあればそれを返す（is_new=False。重複起動しない）。
    runner を渡すとスレッド起動の代わりに同期実行する（テスト用）。
    """
    platform = normalize_platform(source_platform)
    q = query or None
    limit = max(0, int(limit or 0))

    active = find_active(db, platform, q, limit)
    if active is not None:
        return active, False

    job = DiscoveryJob(
        source_platform=platform,
        query=q,
        limit=limit,
        auto_score=1 if auto_score else 0,
        status=DiscoveryJobStatus.queued.value,
        progress=0,
        current_step="キューに登録しました",
        product_ids=[],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if runner is not None:
        runner(job.id)  # 同期（テスト）
    else:
        threading.Thread(target=_run_job, args=(job.id,), daemon=True).start()
    return job, True


def _run_job(job_id: int) -> None:
    """発掘ジョブ本体。専用セマフォで並列数を制限してから実行する。

    セマフォ待ちの間は DB セッションを開かない（queued のまま待機）。
    """
    with _job_semaphore:
        _run_job_inner(job_id)


def _run_job_inner(job_id: int) -> None:
    db = SessionLocal()
    fetch_fn = None
    try:
        job = db.get(DiscoveryJob, job_id)
        if job is None:
            return

        platform = job.source_platform
        job.status = DiscoveryJobStatus.running.value
        job.started_at = _now()
        job.progress = 5
        job.current_step = "一覧を取得中"
        db.commit()  # ここで一旦コミットしてトランザクションを閉じる（HTTP 前に解放）

        # Kickstarter のみ discovery_fetch（Playwright 経由）を注入。network_backed
        # adapter（Wadiz/Zeczec/Indiegogo）は自前で取得するため fetch_fn は None。
        if needs_fetch_injection(platform):
            from app.services import discovery_fetch

            fetch_fn = discovery_fetch.build_http_fetcher()

        # 収集本体（外部 HTTP は最初の DB 書き込みより前・トランザクション非保持）。
        result = discovery_crawler_service.run(
            db,
            source_platform=platform,
            query=job.query,
            limit=job.limit,
            auto_score=bool(job.auto_score),
            fetch_fn=fetch_fn,
            record_run=True,
        )

        # 結果をジョブ行へ反映する。
        job = db.get(DiscoveryJob, job_id)
        job.found_count = result.get("found_count", 0)
        job.saved_count = result.get("saved_count", 0)
        job.duplicate_count = result.get("duplicate_count", 0)
        job.scored_count = result.get("scored_count", 0)
        job.failed_count = result.get("failed_count", 0)
        job.product_ids = result.get("product_ids", []) or []
        job.warnings = result.get("warnings", []) or []
        job.run_id = result.get("run_id")
        job.error = result.get("error_message")
        job.progress = 100
        job.completed_at = _now()

        # 収集自体が失敗（1 件も取得できずエラー）なら failed、それ以外は completed。
        if result.get("status") == "error" and job.saved_count == 0:
            job.status = DiscoveryJobStatus.failed.value
            job.current_step = "失敗"
        else:
            job.status = DiscoveryJobStatus.completed.value
            job.current_step = "完了"
        db.commit()
        logger.info(
            "discovery job done: id=%s platform=%s found=%s saved=%s dup=%s status=%s",
            job_id, platform, job.found_count, job.saved_count,
            job.duplicate_count, job.status,
        )
    except Exception as exc:  # noqa: BLE001  失敗は行に記録（アプリは落とさない）
        logger.warning("discovery job %s failed: %s", job_id, exc)
        try:
            db.rollback()
            job = db.get(DiscoveryJob, job_id)
            if job is not None:
                job.status = DiscoveryJobStatus.failed.value
                job.error = str(exc)[:4000]
                job.current_step = "失敗"
                job.completed_at = _now()
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    finally:
        if fetch_fn is not None:
            try:
                fetch_fn.close()
            except Exception:  # noqa: BLE001
                pass
        db.close()
