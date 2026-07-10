"""ダッシュボード GET の読み取り専用・N+1 防止・ジョブ制御のオフライン検証。

12 秒タイムアウト回帰の再発防止：
- GET /sales/copilot-v2 相当（copilot_v2_dashboard）が
  * 外部 HTTP / Playwright / Claude / Contact Intelligence / Japan チェックを起動しない
  * Assessment を新規保存しない
  * Japan チェックジョブを作らない
  * 案件数に対して DB クエリが O(1)（案件ごとの個別 query をしない）
- 未評価案件は not_evaluated 状態で返す（その場で評価しない）
- 重い並列ジョブはセマフォで制限、起動時に孤児ジョブを回収

実行（backend ディレクトリで）:
    python tests/test_dashboard_performance.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "dash_perf_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import event, func  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus, CIJobType, ContactIntelligenceJob,
)
from app.models.japan_sales_check import JapanSalesCheck, JapanSalesStatus  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.sales_assessment import SalesAssessment  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import sales_assessment_service as sas  # noqa: E402
from app.services import sales_copilot_v2_service as v2  # noqa: E402

Base.metadata.create_all(engine)

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


# クエリ回数カウンタ
_query_count = {"n": 0}


@event.listens_for(engine, "before_cursor_execute")
def _count(conn, cursor, statement, params, context, executemany):
    _query_count["n"] += 1


def _seed(db, n=60):
    for i in range(n):
        db.add(Project(title=f"P{i}", source_site="kickstarter",
                       source_url=f"https://x/{i}", maker_name=f"maker{i}",
                       category="design goods", backers_count=100,
                       latest_score=50 + (i % 40)))
    db.commit()
    # 一部だけ Assessment 済みにする（残りは not_evaluated）
    for p in db.query(Project).limit(10).all():
        sas.run_assessment(db, p)


def test_dashboard_readonly_no_side_effects():
    print("test_dashboard_readonly_no_side_effects")
    db = SessionLocal()
    _seed(db, 60)
    jb = db.query(func.count()).select_from(ContactIntelligenceJob).scalar()
    sb = db.query(func.count()).select_from(SalesAssessment).scalar()
    for _ in range(3):
        v2.copilot_v2_dashboard(db)
    ja = db.query(func.count()).select_from(ContactIntelligenceJob).scalar()
    sa = db.query(func.count()).select_from(SalesAssessment).scalar()
    check("ジョブを作らない", ja == jb)
    check("Assessment を新規保存しない", sa == sb)
    db.close()


def test_dashboard_no_n_plus_one():
    print("test_dashboard_no_n_plus_one")
    db = SessionLocal()
    # 件数を変えてもクエリ数がほぼ一定（案件ごとの個別 query をしない）
    _query_count["n"] = 0
    v2.copilot_v2_dashboard(db)
    q60 = _query_count["n"]
    # 追加で 60 件足す
    for i in range(60, 120):
        db.add(Project(title=f"Q{i}", source_site="indiegogo",
                       source_url=f"https://y/{i}", maker_name=f"m{i}",
                       category="kitchen", latest_score=40))
    db.commit()
    _query_count["n"] = 0
    v2.copilot_v2_dashboard(db)
    q120 = _query_count["n"]
    check(f"クエリ数が件数に比例しない（60件={q60} / 120件={q120}）", q120 <= q60 + 5)
    check("クエリ数が案件数より十分少ない（O(1) 相当）", q120 < 30)
    db.close()


def test_unevaluated_returns_not_evaluated():
    print("test_unevaluated_returns_not_evaluated")
    db = SessionLocal()
    d = v2.copilot_v2_dashboard(db)
    items = d["items"]
    ne = [c for c in items if c["assessment_state"] == "not_evaluated"]
    check("未評価案件は not_evaluated で返る", len(ne) > 0)
    c = ne[0]
    check("未評価はスコア null", c["assessment"]["overall_priority_score"] is None)
    check("未評価は decision=not_evaluated", c["decision"] == "not_evaluated")
    check("summary_counts に not_evaluated 集計あり", "not_evaluated" in d["summary_counts"])
    db.close()


def test_evaluated_card_has_scores_and_grade():
    print("test_evaluated_card_has_scores_and_grade")
    db = SessionLocal()
    d = v2.copilot_v2_dashboard(db)
    ev = [c for c in d["items"] if c["assessment"]["saved"]]
    check("評価済み案件が存在", len(ev) > 0)
    c = ev[0]
    check("総合 grade あり", c["assessment"]["overall_grade"] in list("ABCDE"))
    check("独占 grade あり", c["assessment"]["exclusivity"]["grade"] in list("ABCDE"))
    check("japan_sales_check 状態あり", "status" in c["japan_sales_check"])
    db.close()


def test_semaphore_limits_concurrency():
    print("test_semaphore_limits_concurrency")
    # セマフォの上限が設定値で構築されている
    check("並列上限セマフォが存在", ci._job_semaphore is not None)
    check("上限は 1 以上", ci._MAX_CONCURRENT_JOBS >= 1)


def test_recover_orphaned_jobs():
    print("test_recover_orphaned_jobs")
    db = SessionLocal()
    p = db.query(Project).first()
    j1 = ContactIntelligenceJob(project_id=p.id, job_type=CIJobType.web_research.value,
                                status=CIJobStatus.running.value)
    j2 = ContactIntelligenceJob(project_id=p.id, job_type=CIJobType.japan_sales_check.value,
                                status=CIJobStatus.queued.value)
    db.add_all([j1, j2]); db.commit()
    n = ci.recover_orphaned_jobs(db)
    check("孤児ジョブを回収", n >= 2)
    db.refresh(j1); db.refresh(j2)
    check("running → failed", j1.status == CIJobStatus.failed.value)
    check("queued → failed", j2.status == CIJobStatus.failed.value)
    # 回収後は find_active が None（重複抑止が解ける）
    check("回収後 find_active なし",
          ci.find_active(db, p.id, CIJobType.japan_sales_check.value) is None)
    db.close()


def test_dashboard_no_external_calls():
    print("test_dashboard_no_external_calls")
    # httpx / playwright を呼ばないこと（呼べば例外にする番兵）
    import app.scrapers.http as http_mod
    orig = http_mod.HttpClient.get
    called = {"n": 0}

    def boom(self, *a, **k):
        called["n"] += 1
        raise AssertionError("dashboard must not perform external HTTP")

    http_mod.HttpClient.get = boom
    try:
        db = SessionLocal()
        v2.copilot_v2_dashboard(db)
        db.close()
        check("外部 HTTP を一切呼ばない", called["n"] == 0)
    finally:
        http_mod.HttpClient.get = orig


if __name__ == "__main__":
    test_dashboard_readonly_no_side_effects()
    test_dashboard_no_n_plus_one()
    test_unevaluated_returns_not_evaluated()
    test_evaluated_card_has_scores_and_grade()
    test_semaphore_limits_concurrency()
    test_recover_orphaned_jobs()
    test_dashboard_no_external_calls()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
