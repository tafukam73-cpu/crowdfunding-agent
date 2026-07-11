"""未評価スコア表示・バッチ評価・今日やること可視化のオフライン検証。

- 未評価案件を 0 点 / grade E で表示しない（v2 カードは None を返す）。
- 未評価バッチ評価（重複ジョブなし・冪等・連絡先なしでも 0 点にしない）。
- 今日やることに needs_contact（評価済み・連絡先なし）/ needs_evaluation（未評価）を出す。

実行（backend ディレクトリで）:
    python tests/test_wadiz_task_visibility.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "wadiz_vis_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus, CIJobType, ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.models.sales_assessment import SalesAssessment  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import sales_assessment_service as sas  # noqa: E402
from app.services import sales_copilot_v2_service as v2  # noqa: E402
from app.services import workflow_service as wf  # noqa: E402

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


_n = [0]


def _wadiz(db, **kw) -> Project:
    _n[0] += 1
    base = dict(
        title=f"Wadiz {_n[0]}", source_site="wadiz",
        source_url=f"https://www.wadiz.kr/web/campaign/detail/{_n[0]}",
        category="주방", currency="KRW", raised_amount=23000000, goal_amount=500000,
        backers_count=450, maker_name="주부디자인",
        description="[Wadiz] Country: South Korea Funded 4700%",
        sales_status="not_started",
    )
    base.update(kw)
    p = Project(**base)
    db.add(p); db.commit(); db.refresh(p)
    return p


def test_unevaluated_card_is_none_not_zero():
    print("test_unevaluated_card_is_none_not_zero")
    db = SessionLocal()
    p = _wadiz(db)
    card = v2.build_v2_card(db, p)
    check("未評価は priority_score None（0 でない）", card["priority_score"] is None)
    check("未評価は priority_grade None（E でない）", card["priority_grade"] is None)
    check("assessment_state=not_evaluated", card["assessment_state"] == "not_evaluated")
    check("overall_grade None", card["assessment"]["overall_grade"] is None)
    check("visibility_reason=not_evaluated", card.get("visibility_reason") == "not_evaluated")
    db.close()


def test_score_nonzero_without_contact():
    print("test_score_nonzero_without_contact")
    db = SessionLocal()
    p = _wadiz(db)  # 連絡先なし・日本チェックなし
    sig = sas._gather_signals(db, p)
    r = sas.assess(sig)
    check("連絡先なしでも日本市場適性 > 0", r["japan_market_fit"]["score"] > 0)
    check("連絡先なしでも Makuake 適性 > 0", r["makuake_fit"]["score"] > 0)
    check("連絡先なしでも総合 > 0（0 点にしない）", r["overall_priority_score"] > 0)
    db.close()


def test_japan_unchecked_lowers_confidence_not_score():
    print("test_japan_unchecked_lowers_confidence_not_score")
    db = SessionLocal()
    p = _wadiz(db)
    sig = sas._gather_signals(db, p)  # japan not checked
    r = sas.assess(sig)
    check("japan 未確認でも overall > 0", r["overall_priority_score"] > 0)
    check("japan 未確認は confidence 控えめ（<=80）", r["confidence"] <= 80)
    db.close()


def test_batch_evaluate_dedup_idempotent():
    print("test_batch_evaluate_dedup_idempotent")
    db = SessionLocal()
    for _ in range(3):
        _wadiz(db)
    before = db.query(func.count()).select_from(SalesAssessment).scalar()
    out = sas.run_missing_assessments(db, site="wadiz", runner=lambda jid: None)
    check("未評価をすべて評価", out["evaluated"] == out["unevaluated_found"] and out["evaluated"] >= 3)
    after = db.query(func.count()).select_from(SalesAssessment).scalar()
    check("Assessment が保存された", after > before)
    check("評価で overall > 0（0 点にしない）",
          all(r.get("overall", 0) > 0 for r in out["results"] if "overall" in r))
    # 2 回目：未評価は 0 件（冪等）
    out2 = sas.run_missing_assessments(db, site="wadiz", runner=lambda jid: None)
    check("2 回目は未評価 0 件（冪等）", out2["unevaluated_found"] == 0)
    # 重複 japan ジョブなし
    dups = (
        db.query(ContactIntelligenceJob.project_id, func.count())
        .filter(
            ContactIntelligenceJob.job_type == CIJobType.japan_sales_check.value,
            ContactIntelligenceJob.status.in_(
                [CIJobStatus.queued.value, CIJobStatus.running.value]
            ),
        )
        .group_by(ContactIntelligenceJob.project_id)
        .having(func.count() > 1).all()
    )
    check("重複 japan ジョブを作らない", len(dups) == 0)
    db.close()


def test_today_tasks_surfaces_unevaluated_and_needs_contact():
    print("test_today_tasks_surfaces_unevaluated_and_needs_contact")
    db = SessionLocal()
    # 未評価の案件（needs_evaluation に出るはず）
    uneval = _wadiz(db)
    # 評価済み・連絡先なしの案件（needs_contact に出るはず）
    evaled = _wadiz(db)
    sas.run_assessment(db, evaled)
    tasks = wf.today_tasks(db, per_group=20)
    ne_ids = {t["project_id"] for t in tasks.get("needs_evaluation", [])}
    nc_ids = {t["project_id"] for t in tasks.get("needs_contact", [])}
    check("未評価は needs_evaluation に出る", uneval.id in ne_ids)
    check("評価済み・連絡先なしは needs_contact に出る", evaled.id in nc_ids)
    # needs_evaluation の項目は evaluated=False, visibility=not_evaluated
    if tasks.get("needs_evaluation"):
        it = next(t for t in tasks["needs_evaluation"] if t["project_id"] == uneval.id)
        check("needs_evaluation は evaluated=False", it["evaluated"] is False)
        check("visibility_reason=not_evaluated", it["visibility_reason"] == "not_evaluated")
    db.close()


if __name__ == "__main__":
    test_unevaluated_card_is_none_not_zero()
    test_score_nonzero_without_contact()
    test_japan_unchecked_lowers_confidence_not_score()
    test_batch_evaluate_dedup_idempotent()
    test_today_tasks_surfaces_unevaluated_and_needs_contact()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
