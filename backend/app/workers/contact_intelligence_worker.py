"""Contact Intelligence 専用ワーカープロセス（cfagent-ci-worker）。

    python -m app.workers.contact_intelligence_worker

PostgreSQL の contact_intelligence_jobs をキューとして使い、queued ジョブを原子的に
claim して **ジョブごとに独立したサブプロセス**（app.workers.run_single_job）で実行する。
API（uvicorn）プロセスとは別コンテナ・別プロセスなので、重い探索がハングしても API の
イベントループには影響しない。

安全設計（要件）:
- 1 ワーカー = 同時実行 1 件（既定）。重い探索を直列化する。
- ジョブ単位のハードタイムアウト：超過した実行サブプロセスを **プロセスグループごと**
  SIGTERM→SIGKILL で終了する（Playwright/Chromium の子孫も一緒に死ぬ＝残存 0）。
  スレッドを見捨てる方式は使わない。
- heartbeat_at を定期更新。ワーカー／サブプロセスが落ちれば heartbeat が途絶え、次の
  起動時・定期回収で stale ジョブとして timed_out に回収される。
- cancel_requested を検知したら実行サブプロセスをプロセスグループごと終了する。
- SIGTERM 受信時：実行中サブプロセスを終了し、ジョブは stale 回収に委ねてから終了する。
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time

# DB エンジン生成（app.db.session の import）より前に application_name を確定させる。
os.environ.setdefault("APP_COMPONENT", "cfagent-ci-worker")

from app.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.contact_intelligence_job import CIJobStatus  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s ci-worker %(levelname)s %(message)s",
)
logger = logging.getLogger("ci_worker")

# サブプロセスに SIGTERM を送ってから SIGKILL するまでの猶予（graceful cleanup 用）。
_GRACE_SECONDS = 8.0


def terminate_process_group(proc: subprocess.Popen, grace: float = _GRACE_SECONDS) -> None:
    """サブプロセスをプロセスグループごと終了する（子孫の Chromium も含めて確実に）。

    まず SIGTERM（finally での Playwright close を促す）、猶予後もまだ生きていれば
    SIGKILL でグループごと強制終了する。POSIX 前提（Docker Linux コンテナで動作）。
    """
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    # 1) graceful: SIGTERM をグループへ
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    # 2) force: SIGKILL をグループへ（子孫ごと）
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.error("subprocess pgid=%s did not die after SIGKILL", pgid)


class CIWorker:
    def __init__(
        self,
        worker_id: str | None = None,
        *,
        poll_seconds: float | None = None,
        hard_timeout_seconds: int | None = None,
        heartbeat_seconds: float | None = None,
        stale_recover_seconds: float = 30.0,
    ) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.poll_seconds = (
            poll_seconds
            if poll_seconds is not None
            else float(getattr(settings, "ci_worker_poll_seconds", 2.0))
        )
        self.hard_timeout_seconds = (
            hard_timeout_seconds
            if hard_timeout_seconds is not None
            else max(0, int(getattr(settings, "ci_job_hard_timeout_minutes", 20)) * 60)
        )
        self.heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else float(getattr(settings, "ci_worker_heartbeat_seconds", 5.0))
        )
        self.stale_recover_seconds = stale_recover_seconds
        self._stop = threading.Event()
        self._current: tuple[subprocess.Popen, int] | None = None

    # ---- lifecycle ----
    def request_stop(self, *_args) -> None:
        logger.info("stop requested; finishing up")
        self._stop.set()
        cur = self._current
        if cur is not None:
            proc, job_id = cur
            logger.info("terminating in-flight job %s subprocess", job_id)
            terminate_process_group(proc)

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        logger.info(
            "worker %s started (poll=%.1fs hard_timeout=%ds heartbeat=%.1fs)",
            self.worker_id,
            self.poll_seconds,
            self.hard_timeout_seconds,
            self.heartbeat_seconds,
        )
        self._recover_stale()
        last_recover = time.monotonic()
        while not self._stop.is_set():
            claimed = self._claim()
            if claimed is None:
                if time.monotonic() - last_recover >= self.stale_recover_seconds:
                    self._recover_stale()
                    last_recover = time.monotonic()
                self._stop.wait(self.poll_seconds)
                continue
            job_id, token = claimed
            self._run_claimed(job_id, token)
        logger.info("worker %s stopped", self.worker_id)

    # ---- DB ops (short-lived sessions; never held across the subprocess) ----
    def _claim(self) -> tuple[int, str] | None:
        db = SessionLocal()
        try:
            return ci.claim_next_job(db, self.worker_id)
        except Exception:  # noqa: BLE001
            logger.exception("claim failed")
            db.rollback()
            return None
        finally:
            db.close()

    def _recover_stale(self) -> None:
        db = SessionLocal()
        try:
            n = ci.recover_stale_jobs(db)
            if n:
                logger.warning("recovered %d stale jobs", n)
        except Exception:  # noqa: BLE001
            logger.exception("stale recovery failed")
            db.rollback()
        finally:
            db.close()

    # ---- job execution via isolated subprocess ----
    def _launch(self, job_id: int, token: str) -> subprocess.Popen:
        # start_new_session=True で新しいプロセスグループ（=セッション）を作る。
        # これにより Chromium 子孫も同じグループになり killpg でまとめて終了できる。
        return subprocess.Popen(
            [sys.executable, "-m", "app.workers.run_single_job", str(job_id), token],
            start_new_session=True,
        )

    def _run_claimed(self, job_id: int, token: str) -> None:
        logger.info("running job %s (token=%s)", job_id, token[:8])
        proc = self._launch(job_id, token)
        self._current = (proc, job_id)
        start = time.monotonic()
        last_hb = 0.0
        try:
            while True:
                if proc.poll() is not None:
                    break  # サブプロセスが自分で終了（completed/failed/cancelled 確定）

                now = time.monotonic()
                # heartbeat + cancel 検知（短命セッション）
                if now - last_hb >= self.heartbeat_seconds:
                    last_hb = now
                    if self._heartbeat_and_check_cancel(job_id):
                        logger.info("job %s cancel requested; terminating", job_id)
                        terminate_process_group(proc)
                        self._finalize(job_id, CIJobStatus.cancelled.value,
                                       "中断要求により実行プロセスを終了しました")
                        break

                # hard timeout（プロセスツリーごと kill）
                if self.hard_timeout_seconds and now - start > self.hard_timeout_seconds:
                    logger.error("job %s exceeded hard timeout; killing process tree",
                                 job_id)
                    terminate_process_group(proc)
                    self._finalize(job_id, CIJobStatus.timed_out.value,
                                   "ハードタイムアウト超過により実行プロセスを終了しました")
                    break

                if self._stop.wait(1.0):  # シャットダウン中
                    terminate_process_group(proc)
                    break
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                terminate_process_group(proc, grace=1.0)
            self._current = None
            # サブプロセスが終端状態を書けずに死んだ場合（クラッシュ等）の後始末。
            self._reconcile(job_id, proc.returncode)

    def _heartbeat_and_check_cancel(self, job_id: int) -> bool:
        db = SessionLocal()
        try:
            ci.heartbeat(db, job_id)
            return ci.is_cancel_requested(db, job_id)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat failed")
            db.rollback()
            return False
        finally:
            db.close()

    def _finalize(self, job_id: int, status: str, message: str) -> None:
        db = SessionLocal()
        try:
            ci.finalize_terminated(db, job_id, status, message)
        except Exception:  # noqa: BLE001
            logger.exception("finalize failed")
            db.rollback()
        finally:
            db.close()

    def _reconcile(self, job_id: int, returncode: int | None) -> None:
        # まだ running のまま（サブプロセスが終端状態を書けなかった）なら failed に確定。
        db = SessionLocal()
        try:
            changed = ci.finalize_terminated(
                db,
                job_id,
                CIJobStatus.failed.value,
                f"実行プロセスが異常終了しました（exit={returncode}）",
            )
            if changed:
                logger.warning("job %s reconciled to failed (exit=%s)",
                               job_id, returncode)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile failed")
            db.rollback()
        finally:
            db.close()


def main() -> int:
    CIWorker().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
