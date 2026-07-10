"""日本販売チェック自動連携 + 営業適性再計算のオフライン検証（ネットワーク不要）。

検証項目:
- 日本販売結果の解釈（sold_in_japan / not_found_in_japan / inconclusive / failed /
  not_checked）。検索ゼロ（unknown 多数）を「日本未販売」と断定しない。
- 未実施なら japan_sales_check ジョブを作成、実行中は重複作成しない。
- completed 後に Assessment を再計算し、履歴を保持する。
- inconclusive は独占スコアを中立に、failed は confidence のみ低下。
- v1 判定を変更しない。Zeczec 以外の projects でも動作する。

実行（backend ディレクトリで）:
    python tests/test_japan_sales_automation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "japan_auto_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

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
from app.services import japan_sales_service  # noqa: E402
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


def _mk_project(db, site="zeczec", **kw) -> Project:
    p = Project(title=kw.get("title", "Gadget X"), source_site=site,
                source_url=kw.get("url", f"https://x/{datetime.now().timestamp()}"),
                category=kw.get("category", "design goods"),
                maker_name=kw.get("maker_name", "small studio"),
                backers_count=kw.get("backers", 200))
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _jsc(db, project, status, channels=None, error=None, stars=None, ago_min=0):
    row = JapanSalesCheck(
        project_id=project.id, status=status, channels=channels,
        sales_value_stars=stars, error=error, model="mock-checker-v1",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    if ago_min:
        row.created_at = datetime.now(timezone.utc) - timedelta(minutes=ago_min)
        db.commit()
    return row


def _ch(channel, status, url="https://search/x", note=""):
    return {"channel": channel, "label": channel, "status": status,
            "search_url": url, "note": note}


# ---------------- 解釈（検索ゼロ≠未販売） ----------------
def test_interpret_sold_in_japan():
    print("test_interpret_sold_in_japan")
    db = SessionLocal()
    p = _mk_project(db)
    jsc = _jsc(db, p, JapanSalesStatus.completed.value,
               channels=[_ch("distributor", "found"), _ch("amazon", "not_found")])
    r = sas.interpret_japan_check(jsc)
    check("代理店 found → sold_in_japan", r["result"] == "sold_in_japan")
    check("高 confidence", r["confidence"] >= 80)
    check("evidence を残す", len(r["evidence"]) >= 1)
    db.close()


def test_interpret_not_found_medium_confidence():
    print("test_interpret_not_found_medium_confidence")
    db = SessionLocal()
    p = _mk_project(db)
    jsc = _jsc(db, p, JapanSalesStatus.completed.value,
               channels=[_ch("distributor", "not_found"), _ch("amazon", "not_found"),
                         _ch("rakuten", "not_found"), _ch("makuake", "not_found")])
    r = sas.interpret_japan_check(jsc)
    check("能動検索で不在 → not_found_in_japan", r["result"] == "not_found_in_japan")
    check("確度は中（断定しない・65 以下）", r["confidence"] <= 65)
    db.close()


def test_interpret_inconclusive_not_treated_as_unsold():
    print("test_interpret_inconclusive_not_treated_as_unsold")
    db = SessionLocal()
    p = _mk_project(db)
    # ほぼ unknown（検索できていない）→ inconclusive。未販売と断定しない。
    jsc = _jsc(db, p, JapanSalesStatus.completed.value,
               channels=[_ch("distributor", "unknown"), _ch("amazon", "unknown")])
    r = sas.interpret_japan_check(jsc)
    check("unknown 多数 → inconclusive", r["result"] == "inconclusive")
    check("inconclusive は not_found_in_japan ではない", r["result"] != "not_found_in_japan")
    check("低 confidence", r["confidence"] <= 30)
    db.close()


def test_interpret_failed_and_not_checked():
    print("test_interpret_failed_and_not_checked")
    db = SessionLocal()
    p = _mk_project(db)
    check("チェック無し → not_checked", sas.interpret_japan_check(None)["status"] == "not_checked")
    jsc = _jsc(db, p, JapanSalesStatus.failed.value, error="boom")
    r = sas.interpret_japan_check(jsc)
    check("失敗 → status=failed", r["status"] == "failed")
    check("失敗は result なし（未販売断定しない）", r["result"] is None)
    check("error_reason を残す", r["error_reason"] == "boom")
    db.close()


# ---------------- スコアへの反映 ----------------
def test_exclusivity_reflects_japan_result():
    print("test_exclusivity_reflects_japan_result")
    base = dict(maker_name="small studio", has_contact=True)
    sold = sas.score_exclusivity({**base, "japan_result": "sold_in_japan", "japan_confidence": 85})
    notf = sas.score_exclusivity({**base, "japan_result": "not_found_in_japan", "japan_confidence": 60})
    inc = sas.score_exclusivity({**base, "japan_result": "inconclusive", "japan_confidence": 25})
    check("sold は独占余地が小さい", sold["subscores"]["japan_openness"] <= 25)
    check("not_found は余地あり（ただし断定せず 100 未満）",
          40 <= notf["subscores"]["japan_openness"] < 100)
    check("inconclusive は中立(50)", inc["subscores"]["japan_openness"] == 50)
    check("sold < not_found", sold["score"] < notf["score"])


def test_failed_lowers_confidence_not_score():
    print("test_failed_lowers_confidence_not_score")
    db = SessionLocal()
    p = _mk_project(db)
    _jsc(db, p, JapanSalesStatus.failed.value, error="x")
    row = sas.run_assessment(db, p)
    check("failed でも独占スコアは 0 でない", row.exclusivity_score > 0)
    check("failed は confidence が控えめ", row.confidence <= 80)
    db.close()


# ---------------- ジョブ連携（重複防止・暫定・再計算・履歴） ----------------
def test_ensure_creates_job_when_not_checked():
    print("test_ensure_creates_job_when_not_checked")
    db = SessionLocal()
    p = _mk_project(db)
    job, state = sas.ensure_japan_check(db, p, runner=lambda jid: None)  # noop runner
    check("未実施 → ジョブ作成", job is not None)
    check("job_type=japan_sales_check", job.job_type == CIJobType.japan_sales_check.value)
    db.close()


def test_no_duplicate_job_when_active():
    print("test_no_duplicate_job_when_active")
    db = SessionLocal()
    p = _mk_project(db)
    j1 = ContactIntelligenceJob(project_id=p.id,
                                job_type=CIJobType.japan_sales_check.value,
                                status=CIJobStatus.running.value)
    db.add(j1); db.commit(); db.refresh(j1)
    job, state = sas.ensure_japan_check(db, p, runner=lambda jid: None)
    check("実行中なら既存ジョブを返す", job.id == j1.id)
    check("重複ジョブを作らない",
          db.query(ContactIntelligenceJob).filter_by(
              project_id=p.id,
              job_type=CIJobType.japan_sales_check.value).count() == 1)
    db.close()


def test_no_job_when_completed():
    print("test_no_job_when_completed")
    db = SessionLocal()
    p = _mk_project(db)
    _jsc(db, p, JapanSalesStatus.completed.value,
         channels=[_ch("amazon", "not_found"), _ch("distributor", "not_found"),
                   _ch("rakuten", "not_found")])
    job, state = sas.ensure_japan_check(db, p, runner=lambda jid: None)
    check("完了済みなら新規ジョブを作らない", job is None)
    check("state=completed", state == "completed")
    db.close()


def test_provisional_then_recompute_keeps_history():
    print("test_provisional_then_recompute_keeps_history")
    db = SessionLocal()
    p = _mk_project(db)

    # japan チェックを実行中に見せかけて暫定評価
    out = sas.assess_with_japan(db, p, auto_check=True, runner=lambda jid: None)
    check("暫定評価フラグ", out["provisional"] is True)
    prov_row = out["assessment"]
    check("暫定は details.provisional=True", (prov_row.details_json or {}).get("provisional") is True)

    # ジョブ runner がやること＝run_check + 再評価。ネットワーク回避のため run_check を差し替え。
    orig = japan_sales_service.run_check

    def fake_run_check(db_, project_, checker=None):
        return _jsc(db_, project_, JapanSalesStatus.completed.value,
                    channels=[_ch("distributor", "not_found"), _ch("amazon", "not_found"),
                              _ch("rakuten", "not_found"), _ch("makuake", "not_found")])

    japan_sales_service.run_check = fake_run_check
    try:
        ci._run_japan_sales_check(db, p, None)  # 完了 → 再評価
    finally:
        japan_sales_service.run_check = orig

    rows = db.query(SalesAssessment).filter_by(project_id=p.id).order_by(
        SalesAssessment.id).all()
    check("履歴が保持される（複数行）", len(rows) >= 2)
    latest = rows[-1]
    check("再評価は暫定でない", (latest.details_json or {}).get("provisional") in (False, None))
    check("再評価で japan_result 反映",
          (latest.details_json or {}).get("signals", {}).get("japan_result")
          == "not_found_in_japan")
    db.close()


# ---------------- v1 非改変 / v2 反映 / 非Zeczec ----------------
def test_v1_decision_unchanged_by_v2():
    print("test_v1_decision_unchanged_by_v2")
    db = SessionLocal()
    p = _mk_project(db, site="kickstarter")
    from app.services import sales_copilot_service as v1
    before = v1.build_card(db, p)["decision"]
    _ = v2.build_v2_card(db, p)  # v2 算出
    after = v1.build_card(db, p)["decision"]
    check("v2 実行後も v1 decision は不変", before == after)
    db.close()


def test_v2_card_reflects_state_and_grade():
    print("test_v2_card_reflects_state_and_grade")
    db = SessionLocal()
    p = _mk_project(db, site="indiegogo")  # 非 Zeczec でも動作
    _jsc(db, p, JapanSalesStatus.completed.value,
         channels=[_ch("distributor", "not_found"), _ch("amazon", "not_found"),
                   _ch("rakuten", "not_found")])
    sas.run_assessment(db, p)
    card = v2.build_v2_card(db, p)
    check("非Zeczec でも v2 カードを生成", card["project_id"] == p.id)
    check("japan_sales_check 状態を反映", card["japan_sales_check"]["status"] == "completed")
    check("grade が付く（A-E）", card["assessment"]["overall_grade"] in list("ABCDE"))
    check("assessment_state を持つ",
          card["assessment_state"] in ("evaluated", "recompute_pending", "data_insufficient"))
    check("各スコアに grade", card["assessment"]["exclusivity"]["grade"] in list("ABCDE"))
    db.close()


def test_history_preserved_on_rerun():
    print("test_history_preserved_on_rerun")
    db = SessionLocal()
    p = _mk_project(db)
    sas.run_assessment(db, p)
    sas.run_assessment(db, p)
    n = db.query(SalesAssessment).filter_by(project_id=p.id).count()
    check("再実行で履歴が積まれる（消さない）", n == 2)
    db.close()


def test_grade_bands():
    print("test_grade_bands")
    check("80→A", sas.grade(80) == "A")
    check("70→B", sas.grade(70) == "B")
    check("55→C", sas.grade(55) == "C")
    check("40→D", sas.grade(40) == "D")
    check("10→E", sas.grade(10) == "E")
    check("None→None", sas.grade(None) is None)


if __name__ == "__main__":
    test_interpret_sold_in_japan()
    test_interpret_not_found_medium_confidence()
    test_interpret_inconclusive_not_treated_as_unsold()
    test_interpret_failed_and_not_checked()
    test_exclusivity_reflects_japan_result()
    test_failed_lowers_confidence_not_score()
    test_ensure_creates_job_when_not_checked()
    test_no_duplicate_job_when_active()
    test_no_job_when_completed()
    test_provisional_then_recompute_keeps_history()
    test_v1_decision_unchanged_by_v2()
    test_v2_card_reflects_state_and_grade()
    test_history_preserved_on_rerun()
    test_grade_bands()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
