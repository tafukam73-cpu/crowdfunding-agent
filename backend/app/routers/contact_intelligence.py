"""Contact Intelligence v2：非同期ジョブ API。

- POST /projects/{id}/contact-intelligence/jobs      ジョブ開始（24h キャッシュ再利用）
- GET  /contact-intelligence/jobs/{job_id}           ジョブ取得（ポーリング用）
- GET  /projects/{id}/contact-intelligence/jobs/latest 最新ジョブ取得
- POST /contact-intelligence/jobs/{job_id}/cancel    ジョブ中断要求

重い探索（Web調査 / Document Reader / Search Agent / full）はジョブとして別スレッドで
実行し、HTTP はすぐ返す。UI はポーリングで進捗を取得する。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.contact_intelligence_job import ContactIntelligenceJobOut
from app.services import contact_intelligence_service, project_service

router = APIRouter(tags=["contact-intelligence"])


def _out(job, from_cache: bool = False) -> ContactIntelligenceJobOut:
    dto = ContactIntelligenceJobOut.model_validate(job)
    dto.from_cache = from_cache
    return dto


@router.post(
    "/projects/{project_id}/contact-intelligence/jobs",
    response_model=ContactIntelligenceJobOut,
)
def create_job(
    project_id: int,
    job_type: str = Query(
        "full_contact_intelligence",
        description="web_research / document_reader / search_agent / "
        "full_contact_intelligence",
    ),
    force: bool = Query(False, description="24h キャッシュを無視して再実行する"),
    db: Session = Depends(get_db),
) -> ContactIntelligenceJobOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    try:
        job, from_cache = contact_intelligence_service.create_job(
            db, project, job_type, force=force
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(job, from_cache)


@router.get(
    "/contact-intelligence/jobs/{job_id}", response_model=ContactIntelligenceJobOut
)
def get_job(job_id: int, db: Session = Depends(get_db)) -> ContactIntelligenceJobOut:
    job = contact_intelligence_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return _out(job)


@router.get(
    "/projects/{project_id}/contact-intelligence/jobs/latest",
    response_model=ContactIntelligenceJobOut,
)
def get_latest_job(
    project_id: int,
    job_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    job = contact_intelligence_service.get_latest(db, project_id, job_type)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _out(job)


@router.post(
    "/contact-intelligence/jobs/{job_id}/cancel",
    response_model=ContactIntelligenceJobOut,
)
def cancel_job(
    job_id: int, db: Session = Depends(get_db)
) -> ContactIntelligenceJobOut:
    job = contact_intelligence_service.request_cancel(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return _out(job)
