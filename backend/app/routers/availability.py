"""日本未上陸判定 API。

- POST /projects/{id}/availability-check    判定を実行（同期）、根拠とともに保存
- GET  /projects/{id}/availability-checks   判定履歴（新しい順・根拠付き）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.availability import AvailabilityCheckOut, AvailabilityHitOut
from app.services import availability_service, project_service

logger = logging.getLogger("router.availability")

router = APIRouter(tags=["availability"])


def _to_out(db: Session, check) -> AvailabilityCheckOut:
    hits = availability_service.list_hits(db, check.id)
    return AvailabilityCheckOut(
        id=check.id,
        project_id=check.project_id,
        verdict=check.verdict,
        score=check.score,
        query=check.query,
        summary=check.summary,
        engine=check.engine,
        created_at=check.created_at,
        hits=[AvailabilityHitOut.model_validate(h) for h in hits],
    )


@router.post(
    "/projects/{project_id}/availability-check", response_model=AvailabilityCheckOut
)
def run_check(project_id: int, db: Session = Depends(get_db)) -> AvailabilityCheckOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    try:
        check = availability_service.check_project(db, project)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("availability check failed (project=%s): %s", project_id, exc)
        raise HTTPException(status_code=502, detail=f"判定に失敗しました: {exc}")
    return _to_out(db, check)


@router.get(
    "/projects/{project_id}/availability-checks",
    response_model=list[AvailabilityCheckOut],
)
def list_checks(
    project_id: int, db: Session = Depends(get_db)
) -> list[AvailabilityCheckOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return [_to_out(db, c) for c in availability_service.list_checks(db, project_id)]
