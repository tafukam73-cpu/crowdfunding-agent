"""営業メール下書き API。

- POST /projects/{id}/email-drafts/generate  3種別の下書きを生成（同期）
- GET  /projects/{id}/email-drafts           下書き一覧（履歴・新しい順）

自動送信は行わない。生成された下書きは画面で確認・コピーする。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.email_draft import EmailDraftOut
from app.services import email_service, project_service

logger = logging.getLogger("router.email_drafts")

router = APIRouter(tags=["email-drafts"])


@router.post(
    "/projects/{project_id}/email-drafts/generate",
    response_model=list[EmailDraftOut],
)
def generate_email_drafts(
    project_id: int, db: Session = Depends(get_db)
) -> list[EmailDraftOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    try:
        return email_service.generate_drafts(db, project)
    except Exception as exc:  # noqa: BLE001  失敗を記録しアプリは落とさない
        db.rollback()
        logger.warning("email generation failed (project=%s): %s", project_id, exc)
        raise HTTPException(
            status_code=502, detail=f"営業メール生成に失敗しました: {exc}"
        )


@router.get(
    "/projects/{project_id}/email-drafts",
    response_model=list[EmailDraftOut],
)
def list_email_drafts(
    project_id: int, db: Session = Depends(get_db)
) -> list[EmailDraftOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return email_service.list_drafts(db, project_id)
