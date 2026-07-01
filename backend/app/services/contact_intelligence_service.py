"""Contact Intelligence v2：重い探索の非同期ジョブ化。

AI Web調査 / Document Reader / Search Agent / full を HTTP リクエスト内で完了させると
タイムアウトするため、別スレッドで実行し、進捗・ログ・結果を contact_intelligence_jobs
に保存する。UI はポーリングで進捗を取得する。

- create_job: 24 時間以内に同 project_id/job_type の completed があれば再利用（force で無視）。
  無ければ queued 行を作り、デーモンスレッドで実行を開始する。
- ジョブスレッドは独自 DB セッションを使う（リクエストのセッションと分離）。
- cancel: 進行中ジョブに中断を要求（各フェーズ境界で確認して cancelled にする）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.contact_intelligence_job import (
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project
from app.services import (
    contact_discovery_service,
    document_reader_service,
    search_agent_service,
    web_research_service,
)

logger = logging.getLogger("contact_intelligence")

CACHE_TTL_HOURS = 24
# 進行中ジョブへの中断要求（同一プロセス内シグナル。単一 uvicorn ワーカー想定）
_cancel_requested: set[int] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(job: ContactIntelligenceJob, message: str) -> None:
    logs = list(job.logs_json or [])
    logs.append({"ts": _now().isoformat(), "message": message})
    job.logs_json = logs[-100:]  # 上限 100 行


def _find_cached(
    db: Session, project_id: int, job_type: str
) -> ContactIntelligenceJob | None:
    """24 時間以内に completed した同種ジョブを返す（キャッシュ判定）。"""
    since = _now() - timedelta(hours=CACHE_TTL_HOURS)
    stmt = (
        select(ContactIntelligenceJob)
        .where(
            ContactIntelligenceJob.project_id == project_id,
            ContactIntelligenceJob.job_type == job_type,
            ContactIntelligenceJob.status == CIJobStatus.completed.value,
            ContactIntelligenceJob.completed_at >= since,
        )
        .order_by(desc(ContactIntelligenceJob.completed_at))
        .limit(1)
    )
    return db.scalar(stmt)


def get_job(db: Session, job_id: int) -> ContactIntelligenceJob | None:
    return db.get(ContactIntelligenceJob, job_id)


def get_latest(
    db: Session, project_id: int, job_type: str | None = None
) -> ContactIntelligenceJob | None:
    stmt = select(ContactIntelligenceJob).where(
        ContactIntelligenceJob.project_id == project_id
    )
    if job_type:
        stmt = stmt.where(ContactIntelligenceJob.job_type == job_type)
    stmt = stmt.order_by(desc(ContactIntelligenceJob.id)).limit(1)
    return db.scalar(stmt)


def create_job(
    db: Session,
    project: Project,
    job_type: str,
    *,
    force: bool = False,
    runner=None,
) -> tuple[ContactIntelligenceJob, bool]:
    """ジョブを作成（or キャッシュ再利用）。(job, from_cache) を返す。

    runner を渡すとスレッド起動の代わりに同期実行する（テスト用）。
    """
    if job_type not in {t.value for t in CIJobType}:
        raise ValueError(f"未知の job_type: {job_type}")

    if not force:
        cached = _find_cached(db, project.id, job_type)
        if cached is not None:
            return cached, True

    job = ContactIntelligenceJob(
        project_id=project.id,
        job_type=job_type,
        status=CIJobStatus.queued.value,
        progress=0,
        logs_json=[{"ts": _now().isoformat(), "message": "ジョブを受け付けました"}],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    if runner is not None:
        runner(job.id)  # 同期（テスト）
    else:
        threading.Thread(target=_run_job, args=(job.id,), daemon=True).start()
    return job, False


def request_cancel(db: Session, job_id: int) -> ContactIntelligenceJob | None:
    """進行中ジョブに中断を要求する。queued/running のみ受け付ける。"""
    job = db.get(ContactIntelligenceJob, job_id)
    if job is None:
        return None
    if job.status in (CIJobStatus.queued.value, CIJobStatus.running.value):
        _cancel_requested.add(job_id)
        _log(job, "中断が要求されました")
        db.commit()
        db.refresh(job)
    return job


def _is_cancelled(job_id: int) -> bool:
    return job_id in _cancel_requested


# ---------------- ジョブ実行（別スレッド／独自セッション） ----------------
def _run_web(db: Session, project: Project) -> None:
    web_research_service.run_web_research(db, project)


def _run_doc(db: Session, project: Project) -> None:
    document_reader_service.run_document_reader(db, project)


def _run_agent(db: Session, project: Project) -> None:
    search_agent_service.run_search_agent(db, project)


_SINGLE_PHASES = {
    CIJobType.web_research.value: ("Web Research", _run_web),
    CIJobType.document_reader.value: ("AI Document Reader", _run_doc),
    CIJobType.search_agent.value: ("AI Search Agent", _run_agent),
}


def _run_job(job_id: int) -> None:
    """ジョブ本体。独自セッションで実行し、行を随時更新する。"""
    db = SessionLocal()
    try:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is None:
            return
        project = db.get(Project, job.project_id)
        if project is None:
            job.status = CIJobStatus.failed.value
            job.error = "案件が見つかりません"
            job.completed_at = _now()
            db.commit()
            return

        job.status = CIJobStatus.running.value
        job.started_at = _now()
        job.progress = 1
        _log(job, "実行を開始しました")
        db.commit()

        if job.job_type == CIJobType.full_contact_intelligence.value:
            _run_full(db, job, project)
        else:
            name, fn = _SINGLE_PHASES[job.job_type]
            job.current_step = f"{name} 実行中"
            job.progress = 10
            _log(job, f"{name} を実行します")
            db.commit()
            fn(db, project)
            job.progress = 90
            _log(job, f"{name} が完了しました")
            db.commit()

        if _is_cancelled(job_id):
            job.status = CIJobStatus.cancelled.value
            job.current_step = "中断されました"
            _log(job, "ジョブを中断しました")
        else:
            job.status = CIJobStatus.completed.value
            job.progress = 100
            job.current_step = "完了"
            job.result_json = _build_result(db, project)
            _log(job, "ジョブが完了しました")
        job.completed_at = _now()
        db.commit()
    except Exception as exc:  # noqa: BLE001  失敗は行に記録（アプリは落とさない）
        logger.warning("contact intelligence job %s failed: %s", job_id, exc)
        try:
            job = db.get(ContactIntelligenceJob, job_id)
            if job is not None:
                job.status = CIJobStatus.failed.value
                job.error = str(exc)[:4000]
                job.current_step = "失敗"
                _log(job, f"失敗しました: {exc}")
                job.completed_at = _now()
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
    finally:
        _cancel_requested.discard(job_id)
        db.close()


def _run_full(db: Session, job: ContactIntelligenceJob, project: Project) -> None:
    """full_contact_intelligence：Web調査 → Document Reader → Search Agent →
    営業推奨連絡先ランキング更新 を順に実行する。各フェーズ境界で中断を確認する。"""
    phases = [
        ("Web Research", _run_web),
        ("AI Document Reader", _run_doc),
        ("AI Search Agent", _run_agent),
    ]
    total = len(phases) + 1  # +1: ランキング更新
    for i, (name, fn) in enumerate(phases):
        if _is_cancelled(job.id):
            return
        job.current_step = f"{name} 実行中（{i + 1}/{total}）"
        job.progress = max(1, int(i / total * 100))
        _log(job, f"{name} を実行します")
        db.commit()
        fn(db, project)
        _log(job, f"{name} が完了しました")
        db.commit()

    if _is_cancelled(job.id):
        return
    # 営業推奨連絡先ランキング更新（sales_contacts は都度算出のため保存対象なし。
    # ここでは最新行から集計してログ・結果に反映する）。
    job.current_step = "営業推奨連絡先ランキングを更新中"
    job.progress = 95
    db.commit()
    row = contact_discovery_service.get_latest(db, project.id)
    ranked = contact_discovery_service.build_sales_contacts(row) if row else []
    _log(job, f"営業推奨連絡先ランキングを更新しました（{len(ranked)} 件）")
    db.commit()


def _build_result(db: Session, project: Project) -> dict:
    """完了時の結果サマリ（UI 表示・キャッシュ用）。最新の探索結果から集計する。"""
    row = contact_discovery_service.get_latest(db, project.id)
    if row is None:
        return {"summary": "探索結果がありません。"}
    ranked = contact_discovery_service.build_sales_contacts(row)
    official = (
        contact_discovery_service.official_site_or_none(row.official_site_url)
        or contact_discovery_service.official_site_or_none(
            getattr(row, "search_agent_official_site_url", None)
        )
        or contact_discovery_service.official_site_or_none(
            getattr(row, "doc_reader_official_site_url", None)
        )
    )
    socials = {}
    for src in (
        row.web_discovered_socials,
        getattr(row, "doc_reader_socials", None),
        getattr(row, "search_agent_socials", None),
    ):
        for k, v in (src or {}).items():
            if v and not socials.get(k):
                socials[k] = v
    forms = list(row.web_discovered_forms or [])
    return {
        "official_site_url": official,
        "top_contact": ranked[0] if ranked else None,
        "sales_contacts_count": len(ranked),
        "socials": socials,
        "forms_count": len(forms),
        "recommended_channel": row.recommended_channel,
    }
