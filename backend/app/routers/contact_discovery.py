"""営業先連絡先探索 API。

- POST /projects/{id}/contact-discovery           探索を実行（同期）して保存
- GET  /projects/{id}/contact-discovery            最新の探索結果を取得
- POST /projects/{id}/contact-discovery/apply-to-crm  発見メールを CRM に反映

取得失敗してもアプリは落とさない（status=failed として 200 で返す）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.contact_discovery import (
    ApplyToCrmRequest,
    ApplyToCrmResult,
    ContactDiscoveryOut,
)
from app.services import contact_discovery_service, project_service

logger = logging.getLogger("router.contact_discovery")

router = APIRouter(tags=["contact-discovery"])


@router.post(
    "/projects/{project_id}/contact-discovery", response_model=ContactDiscoveryOut
)
def run_contact_discovery(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return contact_discovery_service.run_discovery(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery", response_model=ContactDiscoveryOut
)
def get_contact_discovery(project_id: int, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row


@router.post(
    "/projects/{project_id}/contact-discovery/apply-to-crm",
    response_model=ApplyToCrmResult,
)
def apply_to_crm(
    project_id: int,
    payload: ApplyToCrmRequest | None = None,
    db: Session = Depends(get_db),
) -> ApplyToCrmResult:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")

    email = (payload.email if payload else None) or None
    if not email:
        latest = contact_discovery_service.get_latest(db, project_id)
        email = latest.primary_email if latest else None
    if not email:
        raise HTTPException(
            status_code=400,
            detail="反映するメールアドレスがありません。先に連絡先探索を実行してください。",
        )

    maker_id, contact_id = contact_discovery_service.apply_to_crm(db, project, email)
    return ApplyToCrmResult(maker_id=maker_id, contact_id=contact_id, email=email)
