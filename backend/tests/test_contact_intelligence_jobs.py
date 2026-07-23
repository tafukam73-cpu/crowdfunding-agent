"""Contact Intelligence 非同期ジョブのオフライン検証（ネットワーク不要）。

重い探索フェーズはフェイクに差し替え、ジョブ作成・進捗更新・full の順序・失敗時の
error 保存・latest 取得・24h キャッシュ判定を検証する。

実行（backend ディレクトリで）:
    python tests/test_contact_intelligence_jobs.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# SessionLocal が束縛される前に file sqlite を指定（スレッド/別セッションで共有するため）
_DBFILE = os.path.join(tempfile.gettempdir(), "ci_jobs_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus, CIJobType, ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402

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


_project_seq = 0


def _mk_project(db) -> Project:
    # メール探索は日本クラファン適性ゲートを通る必要があるため、商品ページ URL と
    # 商品内容（物販・訴求点あり）を持つ案件にする。
    global _project_seq
    _project_seq += 1
    p = Project(
        title=f"Test Compact Kitchen Gadget {_project_seq}",
        source_site="kickstarter",
        source_url=(
            "https://www.kickstarter.com/projects/acme-lab/"
            f"compact-kitchen-gadget-{_project_seq}"
        ),
        category="kitchen",
        description="A compact and portable kitchen gadget with a minimal design.",
        description_clean="A compact and portable kitchen gadget with a minimal design.",
        backers_count=500,
        goal_amount=1000,
        raised_amount=5000,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


_order = []


def _fake_web(project_id, cb=None):
    _order.append("web")
    if cb:
        cb("巡回中: https://example.com/1", pct=0.5)


def _fake_doc(project_id, cb=None):
    _order.append("doc")


def _fake_agent(project_id, cb=None):
    _order.append("agent")


def _fake_recursive(project_id, cb=None):
    _order.append("recursive")
    if cb:
        cb("巡回中 (1/50): https://example.com/contact", pct=0.5)


def _install_fakes():
    ci._run_web = _fake_web
    ci._run_doc = _fake_doc
    ci._run_agent = _fake_agent
    ci._run_recursive = _fake_recursive
    ci._SINGLE_PHASES = {
        CIJobType.web_research.value: ("Web Research", _fake_web),
        CIJobType.document_reader.value: ("AI Document Reader", _fake_doc),
        CIJobType.search_agent.value: ("AI Search Agent", _fake_agent),
        CIJobType.recursive_crawl.value: ("公式サイト再帰クロール", _fake_recursive),
    }


def test_create_and_run_single():
    print("test_create_and_run_single")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    job, cached = ci.create_job(db, proj, "web_research", runner=lambda jid: None)
    check("queued で作成", job.status == CIJobStatus.queued.value)
    check("from_cache False", cached is False)
    # 実処理はワーカーのサブプロセスが呼ぶ execute_job。テストでは直接同期実行する。
    ci.execute_job(job.id)
    db.refresh(job)
    check("completed になる", job.status == CIJobStatus.completed.value)
    check("progress=100", job.progress == 100)
    check("ログが記録される", bool(job.logs_json))
    check("進捗コールバックのログ（巡回中）が入る",
          any("巡回中" in (l.get("message") or "") for l in (job.logs_json or [])))
    check("result_json が入る", job.result_json is not None)
    db.close()


def test_full_order():
    print("test_full_order")
    _install_fakes()
    _order.clear()
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    ci.execute_job(job.id)
    db.refresh(job)
    check("実行順序 web→recursive→doc→agent",
          _order == ["web", "recursive", "doc", "agent"])
    check("full completed", job.status == CIJobStatus.completed.value)
    check("ランキング更新ログ", any("ランキング" in (l.get("message") or "") for l in (job.logs_json or [])))
    db.close()


def test_failed_saves_error():
    print("test_failed_saves_error")
    _install_fakes()

    def boom(project_id, cb=None):
        raise RuntimeError("探索失敗X")

    ci._run_web = boom
    ci._SINGLE_PHASES[CIJobType.web_research.value] = ("Web Research", boom)
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "web_research", runner=lambda jid: None)
    ci.execute_job(job.id)
    db.refresh(job)
    check("failed になる", job.status == CIJobStatus.failed.value)
    check("error に保存", job.error and "探索失敗X" in job.error)
    db.close()


def test_latest_and_cache():
    print("test_latest_and_cache")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    job, cached = ci.create_job(db, proj, "search_agent", runner=lambda jid: None)
    ci.execute_job(job.id)
    db.refresh(job)
    # latest
    latest = ci.get_latest(db, proj.id)
    check("latest 取得", latest is not None and latest.id == job.id)
    check("job_type 指定 latest", ci.get_latest(db, proj.id, "search_agent").id == job.id)
    # cache: completed 済みなので from_cache True
    job2, cached2 = ci.create_job(db, proj, "search_agent", runner=lambda jid: None)
    check("24h以内 completed は再利用", cached2 is True and job2.id == job.id)
    # force で無視
    job3, cached3 = ci.create_job(db, proj, "search_agent", force=True, runner=lambda jid: None)
    check("force で新規作成", cached3 is False and job3.id != job.id)
    # 25h 前の completed はキャッシュ対象外
    old = ContactIntelligenceJob(
        project_id=proj.id, job_type="document_reader",
        status=CIJobStatus.completed.value, progress=100,
        completed_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    db.add(old); db.commit()
    _, cached4 = ci.create_job(db, proj, "document_reader", runner=lambda jid: None)
    check("25h前の completed は再利用しない", cached4 is False)
    db.close()


def test_cancel_queued_is_immediate():
    print("test_cancel_queued_is_immediate")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    # queued 中の cancel は実行前に即 cancelled（ワーカー不要）。
    ci.request_cancel(db, job.id)
    db.refresh(job)
    check("queued cancel は即 cancelled", job.status == CIJobStatus.cancelled.value)
    check("cancel_requested フラグ", ci.is_cancel_requested(db, job.id))
    db.close()


def test_cancel_running_stops_execution():
    print("test_cancel_running_stops_execution")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    # running 相当にしてから cancel_requested を立てる（ワーカーの検知を模擬）。
    job.status = CIJobStatus.running.value
    job.cancel_requested = True
    db.commit()
    check("_is_cancelled は DB フラグを見る", ci._is_cancelled(db, job.id))
    # execute_job は各フェーズ境界で cancel を検知して cancelled で終える。
    ci.execute_job(job.id)
    db.refresh(job)
    check("実行中 cancel で cancelled", job.status == CIJobStatus.cancelled.value)
    db.close()


def test_heavy_active_dedup():
    """同一 project で active な重い探索が 1 本あれば、重複・並列を作らず既存を返す。"""
    print("test_heavy_active_dedup")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    # runner=None のまま（実行しない）＝ queued のまま active
    full, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    check("full が active(queued)", full.status == CIJobStatus.queued.value)
    # 同種の再クリック → 新規を作らず同じジョブを返す
    dup, cached = ci.create_job(db, proj, "full_contact_intelligence", force=True, runner=lambda jid: None)
    check("同種の重複は既存 active を返す", dup.id == full.id and cached is False)
    # 子ジョブ v2 → full が active なので相互排他で full を返す（並列増殖しない）
    v2, cached2 = ci.create_job(db, proj, "contact_discovery_v2", force=True, runner=lambda jid: None)
    check("full active 中は子ジョブも full を返す", v2.id == full.id and cached2 is False)
    # 別 project は独立して作成できる
    proj2 = _mk_project(db)
    other, _ = ci.create_job(db, proj2, "contact_discovery_v2", runner=lambda jid: None)
    check("別 project は独立して作成", other.id != full.id)
    db.close()


def test_recover_stale_heartbeat():
    """heartbeat が途絶えた running を timed_out に回収し、重複抑止を解く。"""
    print("test_recover_stale_heartbeat")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    stale = ContactIntelligenceJob(
        project_id=proj.id, job_type="full_contact_intelligence",
        status=CIJobStatus.running.value, progress=1,
        started_at=old, heartbeat_at=old,
    )
    db.add(stale); db.commit(); db.refresh(stale)
    n = ci.recover_stale_jobs(db, proj.id)
    db.refresh(stale)
    check("stale を 1 件回収", n == 1)
    check("running→timed_out", stale.status == CIJobStatus.timed_out.value)
    # 回収後は active が無いので新規作成できる（重複抑止が解ける）
    fresh, cached = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    check("回収後は新規作成できる", fresh.id != stale.id and cached is False)
    db.close()


def test_claim_atomic_no_double_run():
    """claim は 1 ジョブを 1 回だけ running にし、同一 project heavy を二重 claim しない。"""
    print("test_claim_atomic_no_double_run")
    _install_fakes()
    db = SessionLocal()
    # 直前までのテストが残した queued/running を掃除して claim の判定を確定的にする。
    db.query(ContactIntelligenceJob).delete()
    db.commit()
    proj = _mk_project(db)
    j1, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    # 別 project の queued も用意
    proj2 = _mk_project(db)
    j2, _ = ci.create_job(db, proj2, "web_research", runner=lambda jid: None)
    # 1 回目の claim：どちらか 1 件が running になる
    c1 = ci.claim_next_job(db, "w1")
    check("1 回目で claim できる", c1 is not None)
    jid1, tok1 = c1
    row1 = db.get(ContactIntelligenceJob, jid1)
    check("claim したジョブは running", row1.status == CIJobStatus.running.value)
    check("execution_token 付与", row1.execution_token == tok1)
    # 2 回目：残り 1 件（別 project）を claim できる。同一 project の重複は起きない。
    c2 = ci.claim_next_job(db, "w1")
    check("2 回目で別 project を claim", c2 is not None and c2[0] != jid1)
    # 3 回目：もう queued は無い
    c3 = ci.claim_next_job(db, "w1")
    check("3 回目は claim なし", c3 is None)
    db.close()


def test_claim_skips_project_with_running_heavy():
    """同一 project に running heavy があれば、その project の queued heavy は claim しない。"""
    print("test_claim_skips_project_with_running_heavy")
    _install_fakes()
    db = SessionLocal()
    db.query(ContactIntelligenceJob).delete()
    db.commit()
    proj = _mk_project(db)
    running = ContactIntelligenceJob(
        project_id=proj.id, job_type="full_contact_intelligence",
        status=CIJobStatus.running.value, heartbeat_at=datetime.now(timezone.utc),
    )
    db.add(running); db.commit()
    # 同 project の queued heavy（本来 create_job で弾かれるが、直挿しで claim 層を検証）
    queued = ContactIntelligenceJob(
        project_id=proj.id, job_type="web_research",
        status=CIJobStatus.queued.value,
    )
    db.add(queued); db.commit()
    c = ci.claim_next_job(db, "w1")
    check("running heavy がある project の queued は claim しない", c is None)
    db.close()


def test_full_phase_failure_does_not_halt():
    """full：1 フェーズ失敗でも後続フェーズを継続し、job は failed にしない。"""
    print("test_full_phase_failure_does_not_halt")
    _install_fakes()
    _order.clear()

    def boom_recursive(project_id, cb=None):
        _order.append("recursive")
        raise RuntimeError("recursive boom")

    ci._run_recursive = boom_recursive
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    ci.execute_job(job.id)
    db.refresh(job)
    check("recursive 失敗でも後続 doc/agent が実行", "doc" in _order and "agent" in _order)
    check("1 フェーズ失敗で job は failed にしない", job.status == CIJobStatus.completed.value)
    phases = (job.result_json or {}).get("phases") or []
    check("フェーズ結果を記録", any(p.get("status") == "failed" for p in phases))
    check("outcome を保存", (job.result_json or {}).get("outcome") is not None)
    ci._run_recursive = _fake_recursive
    db.close()


def test_full_all_phases_failed_is_failed():
    """full：全フェーズ失敗なら job は failed。"""
    print("test_full_all_phases_failed_is_failed")
    _install_fakes()

    def boom(project_id, cb=None):
        raise RuntimeError("boom")

    ci._run_web = ci._run_recursive = ci._run_doc = ci._run_agent = boom
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    ci.execute_job(job.id)
    db.refresh(job)
    check("全フェーズ失敗で failed", job.status == CIJobStatus.failed.value)
    db.close()


def test_completed_no_contacts_outcome():
    """成果 0 件の完了は completed_no_contacts（単なる completed にしない）。"""
    print("test_completed_no_contacts_outcome")
    _install_fakes()  # フェイクは DB に何も保存しない＝成果 0
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    ci.execute_job(job.id)
    db.refresh(job)
    check("status は completed", job.status == CIJobStatus.completed.value)
    check("outcome は completed_no_contacts",
          (job.result_json or {}).get("outcome") == ci.OUTCOME_NO_CONTACTS)
    db.close()


def main():
    test_create_and_run_single()
    test_full_order()
    test_failed_saves_error()
    test_latest_and_cache()
    test_cancel_queued_is_immediate()
    test_cancel_running_stops_execution()
    test_heavy_active_dedup()
    test_recover_stale_heartbeat()
    test_claim_atomic_no_double_run()
    test_claim_skips_project_with_running_heavy()
    test_full_phase_failure_does_not_halt()
    test_full_all_phases_failed_is_failed()
    test_completed_no_contacts_outcome()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
