"""テストが本番 DB へ接続することを機械的に防ぐ安全ガード。

過去に pytest が本番 PostgreSQL（crowdfunding / host db=cfagent-db）へ接続し、
実データを削除する重大事故が発生した。本モジュールはその再発を「注意」ではなく
**プロセス強制終了**で物理的に不能にする。

設計方針：
- **本番実行（TESTING 未設定・pytest 非実行）では完全に no-op**。
  production API / ci-worker / run_single_job は TESTING を設定せず pytest も import
  しないため、ガードは一切発火しない。
- **テスト文脈（TESTING=true または pytest 実行中）でのみ**、接続先が本番 DB らしい、
  またはテスト専用 DB でない場合に `os._exit(99)` で即時終了する。
- 判定は純粋関数に分離してテスト可能にし、`os._exit` はごく薄いラッパーだけが持つ。

このモジュールは stdlib のみに依存する（app.config を import しない＝循環回避、
かつ config 読み込みより前に安全に呼べる）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

# 本番 DB の識別子（いずれかに一致したらテスト文脈では拒否する）。
PRODUCTION_DB_NAMES = {"crowdfunding"}
# 本番 PostgreSQL のホスト。docker-compose のサービス名 `db`／コンテナ名 `cfagent-db`。
PRODUCTION_HOSTS = {"db", "cfagent-db"}
# app.config のデフォルトと docker 環境で実際に使われている本番 URL。
PRODUCTION_DEFAULT_URL = (
    "postgresql+psycopg://cfagent:cfagent_password@db:5432/crowdfunding"
)

# ガード発火の終了コード（通常のテスト失敗と区別できる固定値）。
GUARD_EXIT_CODE = 99


@dataclass(frozen=True)
class DbTarget:
    scheme: str
    host: str | None
    dbname: str | None
    is_sqlite: bool


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    target: DbTarget


def parse_db_target(url: str) -> DbTarget:
    """DATABASE_URL から scheme / host / db 名を取り出す（接続はしない）。"""
    url = (url or "").strip()
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme.startswith("sqlite"):
        # sqlite:///path/to.db → path を dbname 相当として保持。
        path = url.split("://", 1)[1] if "://" in url else ""
        return DbTarget(scheme=scheme, host=None, dbname=path.lstrip("/"), is_sqlite=True)
    parsed = urlparse(url)
    dbname = (parsed.path or "").lstrip("/") or None
    host = (parsed.hostname or None)
    return DbTarget(scheme=scheme or "", host=host, dbname=dbname, is_sqlite=False)


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/")


def looks_like_production(url: str) -> tuple[bool, str]:
    """接続先が本番 DB らしいかを判定する。理由文字列を併せて返す。"""
    if not url:
        return False, ""
    if _norm(url) == _norm(PRODUCTION_DEFAULT_URL):
        return True, "本番デフォルト DATABASE_URL と一致"
    t = parse_db_target(url)
    if t.is_sqlite:
        # 本番は必ず PostgreSQL。sqlite が本番と一致することはない。
        return False, ""
    if t.dbname and t.dbname.lower() in PRODUCTION_DB_NAMES:
        return True, f"本番 DB 名 '{t.dbname}' を検出"
    if t.host and t.host.lower() in PRODUCTION_HOSTS:
        return True, f"本番ホスト '{t.host}' を検出"
    return False, ""


def is_test_safe(url: str) -> tuple[bool, str]:
    """テスト専用の接続先として安全か判定する。

    - sqlite は常に安全（本番は PostgreSQL のみ）。
    - PostgreSQL はホストが本番でなく、DB 名が本番でなく、かつ DB 名が明示的に
      `test` で始まる（テスト専用のランダム DB 名）場合のみ安全。
    """
    if not url:
        return False, "DATABASE_URL が未設定"
    t = parse_db_target(url)
    if t.is_sqlite:
        return True, "sqlite（テスト用の使い捨て DB）"
    prod, why = looks_like_production(url)
    if prod:
        return False, why
    if not t.dbname:
        return False, "PostgreSQL の DB 名を特定できない"
    if not t.dbname.lower().startswith("test"):
        return False, (
            f"テスト専用 DB 名（'test' 始まり）ではない: '{t.dbname}'"
        )
    return True, f"テスト専用 PostgreSQL '{t.dbname}'"


def evaluate(url: str) -> Verdict:
    """接続先 URL がテストで使ってよいかを総合判定する（副作用なし）。"""
    target = parse_db_target(url)
    prod, why = looks_like_production(url)
    if prod:
        return Verdict(ok=False, reason=f"本番 DB への接続を検出: {why}", target=target)
    safe, why = is_test_safe(url)
    if not safe:
        return Verdict(ok=False, reason=f"テスト専用 DB ではない: {why}", target=target)
    return Verdict(ok=True, reason=why, target=target)


def in_test_context() -> bool:
    """現在がテスト文脈か（TESTING=true もしくは pytest 実行中）。"""
    testing = os.environ.get("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}
    pytest_running = ("pytest" in sys.modules) or bool(
        os.environ.get("PYTEST_CURRENT_TEST")
    )
    return testing or pytest_running


def describe_target(url: str) -> str:
    t = parse_db_target(url)
    if t.is_sqlite:
        return f"sqlite path='{t.dbname}'"
    return f"scheme={t.scheme} host={t.host} db={t.dbname}"


def _abort(reason: str, url: str) -> None:
    line = "=" * 72
    sys.stderr.write(
        f"\n{line}\n"
        "FATAL: テスト DB 安全ガードが発火しました。テストを中止します。\n"
        f"理由: {reason}\n"
        f"接続先: {describe_target(url)}\n"
        "本番 DB（crowdfunding / host db=cfagent-db）に対するテストは禁止です。\n"
        "TESTING=true と、sqlite もしくは 'test' で始まる専用 DB を指定してください。\n"
        f"{line}\n"
    )
    sys.stderr.flush()
    # sys.exit だと except で握られ得るため、確実にプロセスごと落とす。
    os._exit(GUARD_EXIT_CODE)


def guard_or_abort(url: str) -> None:
    """テスト文脈でのみ、接続先が安全でなければプロセスを即時終了する。

    本番実行（TESTING 未設定・pytest 非実行）では何もしない。
    app.config / app.db.session から、設定 singleton の読み込み時点で呼ばれる。
    """
    if not in_test_context():
        return
    verdict = evaluate(url)
    if not verdict.ok:
        _abort(verdict.reason, url)
