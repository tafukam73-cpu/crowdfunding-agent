"""pytest 共通ガード：本番 DB へのテスト接続を開始前に物理的に阻止する。

pytest は **どのテストモジュールより先に conftest.py を import する**。ここで
接続先を検証し、本番 DB（crowdfunding / host db=cfagent-db）や本番デフォルト
DATABASE_URL、テスト専用でない DB を指していれば `os._exit(99)` で即時終了する。

不変条件：
- TESTING=true をセッション全体で有効化する（pytest 実行＝テスト文脈）。これにより
  app.config / app.db.session 側の機械ガードも同時に武装する。
- DATABASE_URL 未設定なら、テスト専用のランダム sqlite 一時 DB を割り当てる
  （実 DB を絶対に指さない）。
- 接続先（ホスト / DB 名）を実行前に必ず表示する。
- 本番 DB / Docker volume / 既存データには一切変更を加えない（読み書きとも本番へ触れない）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

# backend をパスに載せて純粋ガードを import（app への副作用なし）。
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app import db_safety  # noqa: E402


def _provision_and_verify() -> str:
    # pytest 実行はテスト文脈。TESTING を有効化し、アプリ側ガードも武装させる。
    os.environ["TESTING"] = "true"

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        # 未設定なら本番を絶対に指さないよう、使い捨ての sqlite 一時 DB を割り当てる。
        tmp = Path(tempfile.gettempdir()) / f"cfagent_test_{uuid.uuid4().hex}.sqlite"
        url = f"sqlite:///{tmp.as_posix()}"
        os.environ["DATABASE_URL"] = url

    verdict = db_safety.evaluate(url)
    banner = "-" * 72
    sys.stderr.write(
        f"\n{banner}\n"
        f"[pytest DB ガード] TESTING={os.environ.get('TESTING')}\n"
        f"[pytest DB ガード] 接続先: {db_safety.describe_target(url)}\n"
        f"[pytest DB ガード] 判定: {'OK' if verdict.ok else 'REJECT'} — {verdict.reason}\n"
        f"{banner}\n"
    )
    sys.stderr.flush()
    if not verdict.ok:
        db_safety._abort(verdict.reason, url)
    return url


# import 時点（＝どのテストより先）に検証・武装する。
_ACTIVE_DB_URL = _provision_and_verify()


def pytest_configure(config):  # noqa: ANN001
    """テスト収集開始前に接続先をもう一度表示する（可視性のため）。"""
    config.addinivalue_line("markers", "db: DB を使うテスト")
    print(
        f"\n[pytest DB ガード] 実行対象 DB: {db_safety.describe_target(_ACTIVE_DB_URL)}"
    )
