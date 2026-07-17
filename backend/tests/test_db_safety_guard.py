"""テスト DB 安全ガード（app/db_safety.py・conftest.py）の検証。

2 層を検証する：
1) 純粋関数（looks_like_production / is_test_safe / evaluate / in_test_context）。
2) 機械層：本番 URL + TESTING=true で app.config を import した子プロセスが
   本当に GUARD_EXIT_CODE で即死すること。逆に sqlite なら正常起動すること。

このテスト自体は本番 DB へ一切接続しない（sqlite すら開かない）。

実行（backend ディレクトリ）:
    TESTING=true python tests/test_db_safety_guard.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app import db_safety  # noqa: E402

PROD_URL = "postgresql+psycopg://cfagent:cfagent_password@db:5432/crowdfunding"

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


# --- 純粋関数 ----------------------------------------------------------------

def test_looks_like_production():
    print("test_looks_like_production")
    check("本番デフォルト URL は本番", db_safety.looks_like_production(PROD_URL)[0])
    check(
        "DB 名 crowdfunding は本番",
        db_safety.looks_like_production(
            "postgresql://u:p@somehost:5432/crowdfunding"
        )[0],
    )
    check(
        "ホスト db は本番",
        db_safety.looks_like_production("postgresql://u:p@db:5432/other")[0],
    )
    check(
        "ホスト cfagent-db は本番",
        db_safety.looks_like_production("postgresql://u:p@cfagent-db:5432/other")[0],
    )
    check(
        "sqlite は本番でない",
        not db_safety.looks_like_production("sqlite:////tmp/x.sqlite")[0],
    )
    check(
        "test_ 付き別ホストは本番でない",
        not db_safety.looks_like_production(
            "postgresql://u:p@localhost:5432/test_ci_abc"
        )[0],
    )


def test_is_test_safe():
    print("test_is_test_safe")
    check("sqlite は安全", db_safety.is_test_safe("sqlite:////tmp/x.sqlite")[0])
    check(
        "test_ PostgreSQL(localhost) は安全",
        db_safety.is_test_safe("postgresql://u:p@localhost:5432/test_ci_abc")[0],
    )
    check("本番 URL は不安全", not db_safety.is_test_safe(PROD_URL)[0])
    check(
        "crowdfunding は不安全",
        not db_safety.is_test_safe("postgresql://u:p@localhost:5432/crowdfunding")[0],
    )
    check(
        "test 始まりでない PG は不安全",
        not db_safety.is_test_safe("postgresql://u:p@localhost:5432/prod_like")[0],
    )
    check("空 URL は不安全", not db_safety.is_test_safe("")[0])


def test_evaluate():
    print("test_evaluate")
    check("本番 URL は evaluate NG", not db_safety.evaluate(PROD_URL).ok)
    check(
        "sqlite temp は evaluate OK",
        db_safety.evaluate("sqlite:////tmp/cfagent_test_x.sqlite").ok,
    )
    check(
        "test_ PG は evaluate OK",
        db_safety.evaluate("postgresql://u:p@localhost:5432/test_x").ok,
    )
    check(
        "host db は evaluate NG",
        not db_safety.evaluate("postgresql://u:p@db:5432/test_x").ok,
    )


def test_parse_db_target():
    print("test_parse_db_target")
    t = db_safety.parse_db_target(PROD_URL)
    check("host=db 抽出", t.host == "db")
    check("db=crowdfunding 抽出", t.dbname == "crowdfunding")
    check("not sqlite", not t.is_sqlite)
    s = db_safety.parse_db_target("sqlite:////tmp/a/b.sqlite")
    check("sqlite 判定", s.is_sqlite)


def test_in_test_context():
    print("test_in_test_context")
    prev = os.environ.get("TESTING")
    try:
        os.environ["TESTING"] = "true"
        check("TESTING=true で test 文脈", db_safety.in_test_context())
        os.environ["TESTING"] = ""
        # pytest 実行中は sys.modules に pytest があるため True になり得る。
        # 少なくとも TESTING を落としても関数が例外を出さないことを確認。
        _ = db_safety.in_test_context()
        check("TESTING 空でも例外なし", True)
    finally:
        if prev is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = prev


# --- 機械層（子プロセスで os._exit を実証） -----------------------------------

def _run_import_config(env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONPATH"] = str(BACKEND) + os.pathsep + env.get("PYTHONPATH", "")
    # pytest が親から継承されないよう、テスト文脈判定は env_extra の TESTING に委ねる。
    env.pop("PYTEST_CURRENT_TEST", None)
    return subprocess.run(
        [sys.executable, "-c", "import app.config"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_machine_guard_blocks_production():
    print("test_machine_guard_blocks_production")
    r = _run_import_config({"TESTING": "true", "DATABASE_URL": PROD_URL})
    check(
        f"本番URL+TESTING で GUARD_EXIT_CODE={db_safety.GUARD_EXIT_CODE} で即死 "
        f"(実際 rc={r.returncode})",
        r.returncode == db_safety.GUARD_EXIT_CODE,
    )
    check("停止理由が stderr に出る", "安全ガード" in (r.stderr or ""))


def test_machine_guard_allows_sqlite():
    print("test_machine_guard_allows_sqlite")
    tmp = Path(tempfile.gettempdir()) / f"cfagent_test_{uuid.uuid4().hex}.sqlite"
    r = _run_import_config(
        {"TESTING": "true", "DATABASE_URL": f"sqlite:///{tmp.as_posix()}"}
    )
    check(f"sqlite+TESTING は正常起動 (rc={r.returncode})", r.returncode == 0)


def test_no_guard_in_production_context():
    print("test_no_guard_in_production_context")
    # TESTING 未設定・pytest 非実行（子プロセス）＝本番文脈。
    # 本番 URL でもガードは no-op で import が成功する（=本番運用を壊さない）。
    r = _run_import_config({"DATABASE_URL": PROD_URL, "TESTING": ""})
    check(
        f"本番文脈ではガード no-op で import 成功 (rc={r.returncode})",
        r.returncode == 0,
    )


def main():
    test_parse_db_target()
    test_looks_like_production()
    test_is_test_safe()
    test_evaluate()
    test_in_test_context()
    test_machine_guard_blocks_production()
    test_machine_guard_allows_sqlite()
    test_no_guard_in_production_context()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
