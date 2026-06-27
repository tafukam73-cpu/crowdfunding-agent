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
from app.email import active_provider_name, is_gmail_configured
from app.email.providers.base import EmailProviderError
from app.schemas.email_draft import (
    EmailDraftOut,
    EmailProviderInfo,
    GenerateDraftsRequest,
    ProviderDraftRequest,
    ProviderDraftResult,
    SelectSubjectRequest,
)
from app.services import email_delivery_service, email_service, project_service

logger = logging.getLogger("router.email_drafts")

router = APIRouter(tags=["email-drafts"])


@router.get("/email/provider", response_model=EmailProviderInfo)
def email_provider_info() -> EmailProviderInfo:
    """現在有効なメール下書きプロバイダー（gmail / mock）を返す。"""
    return EmailProviderInfo(
        provider=active_provider_name(), gmail_configured=is_gmail_configured()
    )


@router.post(
    "/email-drafts/{draft_id}/provider-draft", response_model=ProviderDraftResult
)
def create_provider_draft(
    draft_id: int,
    payload: ProviderDraftRequest | None = None,
    db: Session = Depends(get_db),
) -> ProviderDraftResult:
    """生成済み下書きを、設定中のプロバイダー（Gmail 等。未設定なら mock）に
    「下書き」として作成する。送信は行わない。"""
    draft = email_delivery_service.get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="下書きが見つかりません")

    to = payload.to if payload else None
    try:
        result, recipient = email_delivery_service.create_provider_draft(db, draft, to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EmailProviderError as exc:
        logger.warning("provider draft failed (draft=%s): %s", draft_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))

    return ProviderDraftResult(
        provider=result.provider,
        draft_id=result.draft_id,
        status=result.status,
        to=recipient,
        web_link=result.web_link,
        detail=result.detail,
    )


@router.post(
    "/projects/{project_id}/email-drafts/generate",
    response_model=list[EmailDraftOut],
)
def generate_email_drafts(
    project_id: int,
    payload: GenerateDraftsRequest | None = None,
    db: Session = Depends(get_db),
) -> list[EmailDraftOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    tone = (payload or GenerateDraftsRequest()).tone
    try:
        return email_service.generate_drafts(db, project, tone=tone)
    except Exception as exc:  # noqa: BLE001  失敗を記録しアプリは落とさない
        db.rollback()
        logger.warning("email generation failed (project=%s): %s", project_id, exc)
        raise HTTPException(
            status_code=502, detail=f"営業メール生成に失敗しました: {exc}"
        )


@router.patch("/email-drafts/{draft_id}/subject", response_model=EmailDraftOut)
def select_email_subject(
    draft_id: int,
    payload: SelectSubjectRequest,
    db: Session = Depends(get_db),
) -> EmailDraftOut:
    """件名候補から選択した件名を保存する（subject にも同期）。"""
    draft = email_delivery_service.get_draft(db, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="下書きが見つかりません")
    try:
        return email_service.select_subject(db, draft, payload.selected_subject)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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
