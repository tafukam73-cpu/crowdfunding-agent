"""Contact Intelligence の DB セッション・ライフサイクル回帰テスト（SQLite）。

目的：フェーズ本体（外部 HTTP/Playwright/Claude）実行中に DB セッション／トランザクションを
保持していないことを、コード経路レベルで固定する。実 PostgreSQL の idle in transaction 検証は
verify_session_lifecycle_pg.py で別途行う（本ファイルはロジックの回帰防止）。

実行（backend ディレクトリで）:
    python tests/test_ci_session_lifecycle.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "ci_session_lifecycle_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine, worker_session  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus, CIJobType, ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import web_research_service  # noqa: E402

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


def _mk_project(db) -> Project:
    p = Project(title="Test", source_site="kickstarter", maker_url="https://example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _mk_row(db, project_id) -> ContactDiscovery:
    row = ContactDiscovery(project_id=project_id, status="completed")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_release_connection_ends_transaction():
    print("test_release_connection_ends_transaction")
    db = SessionLocal()
    p = _mk_project(db)
    # SELECT でトランザクション開始
    _ = cds.get_latest(db, p.id)
    check("read 後はトランザクション中", db.in_transaction())
    cds.release_connection(db)
    check("release_connection 後はトランザクション無し", not db.in_transaction())
    check("expire_on_commit=False になる", db.expire_on_commit is False)
    db.close()


def test_run_discovery_no_txn_and_no_pending_row_during_external():
    print("test_run_discovery_no_txn_and_no_pending_row_during_external")
    db = SessionLocal()
    p = _mk_project(db)
    holder = {}

    def fake_discover(project, research, fetch_fn=None):
        # 外部処理中：トランザクション無し・未コミットの ContactDiscovery INSERT 無し
        holder["in_txn"] = db.in_transaction()
        holder["pending_rows"] = [o for o in db.new if isinstance(o, ContactDiscovery)]
        return {k: None for k in (
            "primary_email", "primary_contact_form_url", "official_site_url",
            "instagram_url", "facebook_url", "twitter_url", "linkedin_url",
            "youtube_url", "discovered_emails", "discovered_forms",
            "discovered_socials", "searched_urls", "confidence_score",
            "contactability_score", "recommended_channel", "recommended_action",
            "discovery_checklist", "approach_options", "search_queries",
            "evidence_summary", "notes",
        )}

    orig = cds.discover
    cds.discover = fake_discover
    try:
        row = cds.run_discovery(db, p)
    finally:
        cds.discover = orig
    check("外部 discover 中はトランザクション無し", holder.get("in_txn") is False)
    check("外部 discover 中は未コミット行を保持しない",
          holder.get("pending_rows") == [])
    check("外部処理の後に行が保存される", row.id is not None)
    db.close()


def test_run_web_research_no_txn_during_external():
    print("test_run_web_research_no_txn_during_external")
    db = SessionLocal()
    p = _mk_project(db)
    _mk_row(db, p.id)
    holder = {}

    class _Stop(Exception):
        pass

    def fake_core(project, research, *, fetch_fn=None, search_fn=None, progress_cb=None):
        holder["in_txn"] = db.in_transaction()
        raise _Stop()

    orig = web_research_service.web_research
    web_research_service.web_research = fake_core
    try:
        web_research_service.run_web_research(db, p)  # 例外は wrapper が握って error 保存
    finally:
        web_research_service.web_research = orig
    check("外部 web_research 中はトランザクション無し", holder.get("in_txn") is False)
    db.close()


def test_progress_cb_uses_own_short_session():
    print("test_progress_cb_uses_own_short_session")
    db = SessionLocal()
    p = _mk_project(db)
    job = ContactIntelligenceJob(
        project_id=p.id, job_type=CIJobType.web_research.value,
        status=CIJobStatus.running.value, progress=1,
    )
    db.add(job); db.commit()
    jid = job.id
    # 呼び出し側は db を渡さない（自前の短命セッションで更新する）
    cb = ci._make_progress_cb(jid, base=5, span=90)
    cb("巡回中: https://example.com", pct=0.5)
    # 別セッションから見て更新が反映されている＝短命セッションで commit している
    with worker_session() as s:
        j = s.get(ContactIntelligenceJob, jid)
        check("progress が更新される", j.progress == int(5 + 90 * 0.5))
        check("current_step が更新される", "巡回中" in (j.current_step or ""))
        check("heartbeat が更新される", j.heartbeat_at is not None)
    db.close()


def test_progress_cb_raises_on_cancel():
    print("test_progress_cb_raises_on_cancel")
    db = SessionLocal()
    p = _mk_project(db)
    job = ContactIntelligenceJob(
        project_id=p.id, job_type=CIJobType.web_research.value,
        status=CIJobStatus.running.value, cancel_requested=True,
    )
    db.add(job); db.commit()
    cb = ci._make_progress_cb(job.id, base=0, span=100)
    raised = False
    try:
        cb("進捗", pct=0.1)
    except ci._JobCancelled:
        raised = True
    check("cancel_requested 中は _JobCancelled を送出", raised)
    db.close()


def test_cancel_checker_uses_own_short_session():
    print("test_cancel_checker_uses_own_short_session")
    db = SessionLocal()
    p = _mk_project(db)
    job = ContactIntelligenceJob(
        project_id=p.id, job_type=CIJobType.web_research.value,
        status=CIJobStatus.running.value, cancel_requested=False,
    )
    db.add(job); db.commit()
    check_fn = ci._make_cancel_checker(job.id)
    check("初期は False", check_fn() is False)
    # 別セッションで cancel を立てる → checker が拾う（毎回新セッションで読む証拠）
    with worker_session() as s:
        s.get(ContactIntelligenceJob, job.id).cancel_requested = True
        s.commit()
    check("別セッションの cancel を検知", check_fn() is True)
    db.close()


def test_full_run_uses_multiple_short_sessions():
    print("test_full_run_uses_multiple_short_sessions")
    # worker_session の生成回数を数え、full 実行で複数の短命セッションが使われることを固定。
    import app.db.session as sess_mod
    calls = {"n": 0}
    orig = sess_mod.worker_session
    # ci は worker_session を直接 import しているのでそちらを差し替える
    import contextlib

    @contextlib.contextmanager
    def counting_session():
        calls["n"] += 1
        with orig() as s:
            yield s

    ci_orig = ci.worker_session
    ci.worker_session = counting_session
    # フェーズ本体はフェイク（外部処理なし・順序記録）
    order = []

    def mk_fake(tag):
        def _f(project_id, cb=None):
            order.append(tag)
        return _f

    saved = {name: getattr(ci, name) for name in
             ("_run_web", "_run_recursive", "_run_doc", "_run_agent")}
    ci._run_web = mk_fake("web")
    ci._run_recursive = mk_fake("rec")
    ci._run_doc = mk_fake("doc")
    ci._run_agent = mk_fake("agent")
    db = SessionLocal()
    p = _mk_project(db)
    job = ContactIntelligenceJob(
        project_id=p.id, job_type=CIJobType.full_contact_intelligence.value,
        status=CIJobStatus.queued.value,
    )
    db.add(job); db.commit()
    jid = job.id
    db.close()
    try:
        ci.execute_job(jid)
    finally:
        ci.worker_session = ci_orig
        for k, v in saved.items():
            setattr(ci, k, v)
    check("full の各フェーズが直列実行される",
          order == ["web", "rec", "doc", "agent"])
    # 1 本の長寿命セッションではなく、多数の短命セッションが使われている
    check("full 実行で複数の短命セッションを使用（>=6）", calls["n"] >= 6)
    with worker_session() as s:
        j = s.get(ContactIntelligenceJob, jid)
        check("full が completed", j.status == CIJobStatus.completed.value)


def main():
    test_release_connection_ends_transaction()
    test_run_discovery_no_txn_and_no_pending_row_during_external()
    test_run_web_research_no_txn_during_external()
    test_progress_cb_uses_own_short_session()
    test_progress_cb_raises_on_cancel()
    test_cancel_checker_uses_own_short_session()
    test_full_run_uses_multiple_short_sessions()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
