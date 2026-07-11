"""実 PostgreSQL でのセッションリーク検証（手動実行・DBを使う）。

run_hunt の外部呼び出し（hunt）を数秒スリープで模擬し、その間に別コネクションから
pg_stat_activity を観測して「company_researches を参照した idle in transaction 接続」が
残らないことを確認する。処理後の idle in transaction 件数も 0 であることを確認する。
検証用の一時プロジェクトは最後に削除する（実データは触らない）。

実行（backend ディレクトリ、実 PostgreSQL 環境で）:
    python tests/verify_session_leak_pg.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.ai.contact_hunter import ContactHuntResult, PersonResult  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.company_research import CompanyResearch, ResearchStatus  # noqa: E402
from app.models.contact_person import ContactPerson  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import contact_hunter_service as chs  # noqa: E402

SLEEP_S = 4.0


class _SlowHunter:
    name = "slow-spy"
    last_usage = None

    def hunt(self, project, *, fetch_fn=None, search_fn=None, research=None):
        # 外部呼び出し（Claude/HTTP）を模擬。この間トランザクションを保持していない
        # ことを別コネクションから観測する。
        time.sleep(SLEEP_S)
        return ContactHuntResult(
            people=[PersonResult(
                name="Verify Person", title="Head of Sales",
                source_url="https://maker.example.com/team",
                priority=85, confidence=80,
            )],
            model="slow-spy",
        )


def _idle_in_txn_company_research() -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "select count(*) from pg_stat_activity "
            "where datname = current_database() "
            "and state = 'idle in transaction' "
            "and query ilike '%company_researches%'"
        )).scalar_one()


def _show_guc() -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("show idle_in_transaction_session_timeout")
        ).scalar_one()


def main() -> int:
    print(f"idle_in_transaction_session_timeout = {_show_guc()} (期待: 10min)")

    # 一時プロジェクト + company_research を用意
    db = SessionLocal()
    p = Project(title="__VERIFY session leak__", source_site="wadiz",
                source_url="https://www.example.com/verify",
                maker_name="Verify Maker", maker_url="https://maker.example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    pid = p.id
    db.add(CompanyResearch(project_id=pid, maker_name="Verify Maker",
                           research_status=ResearchStatus.completed.value, model="m"))
    db.commit()
    db.close()

    before = _idle_in_txn_company_research()

    def run():
        d = SessionLocal()
        try:
            proj = d.get(Project, pid)
            chs.run_hunt(d, proj, hunter=_SlowHunter())
        finally:
            d.close()

    t = threading.Thread(target=run)
    t.start()

    # 外部呼び出し（スリープ）中に観測：idle in transaction が増えていないこと
    time.sleep(SLEEP_S / 2)
    during = _idle_in_txn_company_research()
    t.join(timeout=SLEEP_S + 15)
    time.sleep(0.5)
    after = _idle_in_txn_company_research()

    # 検証結果
    ok = True
    print(f"idle-in-txn(company_researches)  before={before} during={during} after={after}")
    if during > before:
        ok = False
        print("  FAIL - 外部呼び出し中に idle in transaction が発生している")
    else:
        print("  ok   - 外部呼び出し中に idle in transaction を保持していない")
    if after > before:
        ok = False
        print("  FAIL - 処理後に idle in transaction が残っている")
    else:
        print("  ok   - 処理後に idle in transaction が残っていない")

    # 保存確認
    d = SessionLocal()
    saved = d.query(ContactPerson).filter_by(project_id=pid).count()
    print(f"  contact_people saved = {saved}")
    if saved < 1:
        ok = False
        print("  FAIL - 担当者が保存されていない")
    else:
        print("  ok   - 担当者が保存された")

    # 後片付け（自作の一時プロジェクトのみ削除）
    tp = d.get(Project, pid)
    assert tp is not None and tp.title.startswith("__VERIFY"), "safety: temp only"
    d.query(ContactPerson).filter_by(project_id=pid).delete()
    d.query(CompanyResearch).filter_by(project_id=pid).delete()
    d.delete(tp)
    d.commit()
    d.close()
    print(f"  cleaned temp project {pid}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
