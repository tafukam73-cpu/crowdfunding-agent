"""CI ワーカーのプロセスツリー終了を実プロセスで検証する（POSIX / Linux コンテナ）。

要点：ハードタイムアウト／中断時にワーカーが実行サブプロセスを**プロセスツリーごと**
終了できること（＝子孫の Chromium 相当プロセスも残さない）。スレッドを見捨てる方式では
なく、実プロセスが本当に死ぬことを確認する。

実行（backend ディレクトリで）:
    python tests/test_ci_worker.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.workers.contact_intelligence_worker import (  # noqa: E402
    terminate_process_group,
)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# 親プロセス：子を 1 つ spawn し、子 PID を stdout に出して自身も sleep し続ける。
_PARENT_SRC = (
    "import subprocess,sys,time;"
    "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)']);"
    "print(c.pid,flush=True);"
    "time.sleep(300)"
)


def test_terminate_kills_process_tree():
    print("test_terminate_kills_process_tree")
    proc = subprocess.Popen(
        [sys.executable, "-c", _PARENT_SRC],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,  # 新セッション＝プロセスグループ（worker と同じ起動方式）
    )
    child_pid = int(proc.stdout.readline().strip())
    time.sleep(0.5)
    check("親プロセス生存", _pid_alive(proc.pid))
    check("子プロセス生存", _pid_alive(child_pid))

    # プロセスグループごと終了（graceful 猶予は短く）
    terminate_process_group(proc, grace=1.0)
    time.sleep(0.5)

    check("親プロセスは終了", not _pid_alive(proc.pid))
    check("子プロセス（Chromium 相当）も残らない", not _pid_alive(child_pid))
    check("returncode が設定される", proc.returncode is not None)


def test_terminate_noop_when_already_exited():
    print("test_terminate_noop_when_already_exited")
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True
    )
    proc.wait()
    # 既に終了済み。例外を投げず静かに戻ること。
    terminate_process_group(proc, grace=0.5)
    check("終了済みでも例外なし", True)


def main():
    test_terminate_kills_process_tree()
    test_terminate_noop_when_already_exited()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
