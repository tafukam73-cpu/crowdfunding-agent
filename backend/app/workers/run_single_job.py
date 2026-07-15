"""1 ジョブを実行するサブプロセスのエントリポイント。

    python -m app.workers.run_single_job <job_id> <execution_token>

ワーカー（contact_intelligence_worker）から `start_new_session=True` で起動される。
これにより本プロセスは新しいプロセスグループのリーダーになり、Playwright/Chromium の
子プロセスも同じグループに属する。ハードタイムアウト／中断時にワーカーが
`killpg` でグループごと終了させるため、スレッドを「見捨てる」ことは一切ない。

- 実処理は contact_intelligence_service.execute_job() に委譲する。
- SIGTERM を受けたら KeyboardInterrupt に変換し、フェーズ内の finally（Playwright の
  browser/context/page close 等）を巻き戻してから終了する（graceful）。ハングして
  応答しない場合はワーカーが SIGKILL でグループごと落とす（force）。
- 終了コード：completed=0 / それ以外=1（ワーカーは DB のステータスで最終判断する）。
"""
from __future__ import annotations

import logging
import os
import signal
import sys

# DB エンジン生成（app.db.session の import）より前に application_name を確定させる。
# pg_stat_activity で「このジョブ実行プロセスの接続」を識別できるようにする。
os.environ.setdefault("APP_COMPONENT", "cfagent-ci-job")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s ci-job[%(process)d] %(levelname)s %(message)s",
)
logger = logging.getLogger("ci_run_single_job")


def _on_term(signum, frame):  # noqa: ANN001
    # finally を確実に走らせるため例外に変換する。
    raise KeyboardInterrupt()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        logger.error("usage: run_single_job <job_id> [execution_token]")
        return 2
    job_id = int(argv[0])
    token = argv[1] if len(argv) > 1 else None

    signal.signal(signal.SIGTERM, _on_term)

    # import はここで（プロセス起動後）。ワーカーとは別プロセス＝独自 DB エンジン。
    from app.models.contact_intelligence_job import CIJobStatus
    from app.services import contact_intelligence_service as ci

    try:
        status = ci.execute_job(job_id, execution_token=token)
    except KeyboardInterrupt:
        # SIGTERM/中断。execute_job 内で cancelled/failed に確定済みのはず。
        logger.warning("job %s interrupted (SIGTERM)", job_id)
        return 1
    logger.info("job %s finished with status=%s", job_id, status)
    return 0 if status == CIJobStatus.completed.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
