"""Contact Hunter のDBセッション/トランザクション寿命の検証。

company_researches を SELECT した後、外部呼び出し（Claude/HTTP 探索）に進む際に
トランザクションを保持し続けると接続が「idle in transaction」のまま残り、セッション
リークになる。run_hunt が外部呼び出しの前にトランザクションを閉じること、例外経路でも
保存済みデータを消さず・トランザクションを残さないことを検証する。

SQLite（ファイル）でトランザクション状態（db.in_transaction()）を確認する。実 PostgreSQL
での idle in transaction 検証は tests/verify_session_leak_pg.py（手動実行）で行う。

実行（backend ディレクトリで）:
    python tests/test_contact_hunter_session.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "hunter_session_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ai.contact_hunter import ContactHuntResult, PersonResult  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.company_research import CompanyResearch, ResearchStatus  # noqa: E402
from app.models.contact_person import ContactPerson  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import contact_hunter_service as chs  # noqa: E402

Base.metadata.create_all(engine)

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


_pcount = [0]


def _mk_project(db) -> Project:
    _pcount[0] += 1
    p = Project(
        title="P", source_site="wadiz",
        source_url=f"https://x/{_pcount[0]}",
        maker_name="Maker", maker_url="https://maker.example.com",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


class _SpyHunter:
    """hunt() 実行時にコールバックでセッション状態を確認できるスパイ。"""

    name = "spy"
    last_usage = None

    def __init__(self, on_hunt, people=None, raise_exc=False):
        self.on_hunt = on_hunt
        self._people = people or []
        self._raise = raise_exc

    def hunt(self, project, *, fetch_fn=None, search_fn=None, research=None):
        self.on_hunt(project)  # 外部呼び出し相当のタイミング
        if self._raise:
            raise RuntimeError("hunt boom")
        return ContactHuntResult(people=self._people, model="spy")


def test_no_transaction_held_during_hunt():
    print("test_no_transaction_held_during_hunt")
    db = SessionLocal()
    p = _mk_project(db)
    # company_research を用意し get_latest_completed が実 SELECT を行うようにする
    db.add(CompanyResearch(
        project_id=p.id, research_status=ResearchStatus.completed.value,
        maker_name="Maker", model="m",
    ))
    db.commit()

    state: dict = {}

    def on_hunt(project):
        # 外部呼び出しのタイミングでトランザクションを保持していないこと
        state["in_txn"] = db.in_transaction()
        # project の属性アクセスで再クエリ（再トランザクション）が起きないこと
        _ = project.maker_name, project.maker_url, project.source_url
        state["in_txn_after_attr"] = db.in_transaction()

    hunter = _SpyHunter(on_hunt, people=[PersonResult(
        name="Jane Doe", title="Head of Sales",
        source_url="https://maker.example.com/team", priority=85, confidence=80,
    )])
    out = chs.run_hunt(db, p, hunter=hunter)
    check("外部hunt中にトランザクションを保持していない", state.get("in_txn") is False)
    check("project属性アクセス後も再トランザクションが開かない",
          state.get("in_txn_after_attr") is False)
    check("担当者を保存して返す", any(r.name == "Jane Doe" for r in out))
    db.close()


def test_exception_path_no_leak_keeps_existing():
    print("test_exception_path_no_leak_keeps_existing")
    db = SessionLocal()
    p = _mk_project(db)
    db.add(ContactPerson(
        project_id=p.id, name="Existing Person",
        source_url="https://x/existing", priority=50, confidence=50,
    ))
    db.commit()

    state: dict = {}

    def on_hunt(project):
        state["in_txn"] = db.in_transaction()

    hunter = _SpyHunter(on_hunt, raise_exc=True)
    out = chs.run_hunt(db, p, hunter=hunter)
    check("失敗時も外部呼び出し前にトランザクションを閉じている",
          state.get("in_txn") is False)
    check("失敗時は既存担当者を保持して返す（保存を消さない）",
          any(r.name == "Existing Person" for r in out))
    db.close()


def test_repeated_runs_do_not_accumulate_open_txn():
    print("test_repeated_runs_do_not_accumulate_open_txn")
    db = SessionLocal()
    p = _mk_project(db)
    seen: list[bool] = []

    def on_hunt(project):
        seen.append(db.in_transaction())

    hunter = _SpyHunter(on_hunt, people=[PersonResult(
        name="Rep Person", title="Sales", source_url="https://maker.example.com/x",
        priority=85, confidence=70,
    )])
    for _ in range(5):
        chs.run_hunt(db, p, hunter=hunter)
    check("毎回 外部呼び出し前にトランザクションが閉じている",
          len(seen) == 5 and all(v is False for v in seen))
    db.close()


if __name__ == "__main__":
    test_no_transaction_held_during_hunt()
    test_exception_path_no_leak_keeps_existing()
    test_repeated_runs_do_not_accumulate_open_txn()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
