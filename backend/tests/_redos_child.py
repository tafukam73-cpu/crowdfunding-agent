"""ReDoS テストの実行子プロセス。

親から抽出関数名を argv[1]、対象 HTML を stdin（バイト）で受け取り、抽出に要した
秒数と結果を stdout に出す。実 DB へは一切接続しない（sqlite 固定＋TESTING=true）。

親側は本スクリプトを **プロセスグループ独立**で起動し、ハードタイムアウト時に
プロセスツリーごと kill する。これにより C 実装の正規表現が暴走しても（SIGALRM では
中断できない）確実に打ち切れ、子孫プロセスも残らない。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

# 実 DB を絶対に指さない。TESTING を立てて app 側の安全ガードも武装させる。
os.environ.setdefault("TESTING", "true")
os.environ["DATABASE_URL"] = "sqlite:///" + (
    Path(tempfile.gettempdir()) / "redos_child.sqlite"
).as_posix()

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402


def main() -> int:
    func = sys.argv[1]
    data = sys.stdin.buffer.read().decode("utf-8", "replace")
    t = time.perf_counter()
    if func == "extract_emails":
        res = cds.extract_emails(data, None)
    else:
        res = getattr(cds, func)(data)
    dt = time.perf_counter() - t
    sys.stdout.write(f"__DT__={dt:.6f}\n")
    sys.stdout.write(f"__RES__={res!r}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
