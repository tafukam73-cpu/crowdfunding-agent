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


def _mk_project(db) -> Project:
    p = Project(title="Test", source_site="kickstarter")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


_order = []


def _fake_web(db, project, cb=None):
    _order.append("web")
    if cb:
        cb("巡回中: https://example.com/1", pct=0.5)


def _fake_doc(db, project, cb=None):
    _order.append("doc")


def _fake_agent(db, project, cb=None):
    _order.append("agent")


def _fake_recursive(db, project, cb=None):
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
    # 同期実行
    ci._run_job(job.id)
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
    ci._run_job(job.id)
    db.refresh(job)
    check("実行順序 web→recursive→doc→agent",
          _order == ["web", "recursive", "doc", "agent"])
    check("full completed", job.status == CIJobStatus.completed.value)
    check("ランキング更新ログ", any("ランキング" in (l.get("message") or "") for l in (job.logs_json or [])))
    db.close()


def test_failed_saves_error():
    print("test_failed_saves_error")
    _install_fakes()

    def boom(db, project, cb=None):
        raise RuntimeError("探索失敗X")

    ci._run_web = boom
    ci._SINGLE_PHASES[CIJobType.web_research.value] = ("Web Research", boom)
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "web_research", runner=lambda jid: None)
    ci._run_job(job.id)
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
    ci._run_job(job.id)
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


def test_cancel_flag():
    print("test_cancel_flag")
    _install_fakes()
    db = SessionLocal()
    proj = _mk_project(db)
    job, _ = ci.create_job(db, proj, "full_contact_intelligence", runner=lambda jid: None)
    ci.request_cancel(db, job.id)
    db.refresh(job)
    check("cancel 要求で in-process フラグ", ci._is_cancelled(job.id))
    ci._run_job(job.id)
    db.refresh(job)
    check("中断で cancelled", job.status == CIJobStatus.cancelled.value)
    db.close()


def main():
    test_create_and_run_single()
    test_full_order()
    test_failed_saves_error()
    test_latest_and_cache()
    test_cancel_flag()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
