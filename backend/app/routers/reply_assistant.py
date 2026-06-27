"""返信メール AI サポート API。

- POST /projects/{id}/reply-assist            受信メールを解析し返信案を作成（同期）
- GET  /projects/{id}/reply-assists           解析履歴（新しい順）
- GET  /reply-assists/{reply_assist_id}        単一取得
- POST /reply-assists/{id}/gmail-draft         Gmail（未設定なら mock）に返信下書き作成

取得・解析の失敗はアプリを落とさず status=failed で保存する。Gmail 作成失敗は 502。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.email.providers.base import EmailProviderError
from app.schemas.reply_assistant import (
    ReplyAssistCreate,
    ReplyAssistOut,
    ReplyGmailDraftRequest,
    ReplyGmailDraftResult,
)
from app.services import project_service, reply_assistant_service

logger = logging.getLogger("router.reply_assistant")

router = APIRouter(tags=["reply-assistant"])


@router.post("/projects/{project_id}/reply-assist", response_model=ReplyAssistOut)
def create_reply_assist(
    project_id: int,
    payload: ReplyAssistCreate,
    db: Session = Depends(get_db),
) -> ReplyAssistOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    if not (payload.incoming_body or "").strip():
        raise HTTPException(status_code=400, detail="返信本文が空です")
    return reply_assistant_service.create_reply_assist(
        db,
        project,
        incoming_subject=payload.incoming_subject,
        incoming_body=payload.incoming_body,
        incoming_from=payload.incoming_from,
        reply_tone=payload.reply_tone,
    )


@router.get(
    "/projects/{project_id}/reply-assists", response_model=list[ReplyAssistOut]
)
def list_reply_assists(
    project_id: int, db: Session = Depends(get_db)
) -> list[ReplyAssistOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return reply_assistant_service.list_assists(db, project_id)


@router.get("/reply-assists/{reply_assist_id}", response_model=ReplyAssistOut)
def get_reply_assist(
    reply_assist_id: int, db: Session = Depends(get_db)
) -> ReplyAssistOut:
    row = reply_assistant_service.get_assist(db, reply_assist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="返信サポートが見つかりません")
    return row


@router.post(
    "/reply-assists/{reply_assist_id}/gmail-draft",
    response_model=ReplyGmailDraftResult,
)
def create_reply_gmail_draft(
    reply_assist_id: int,
    payload: ReplyGmailDraftRequest | None = None,
    db: Session = Depends(get_db),
) -> ReplyGmailDraftResult:
    row = reply_assistant_service.get_assist(db, reply_assist_id)
    if row is None:
        raise HTTPException(status_code=404, detail="返信サポートが見つかりません")
    to = payload.to if payload else None
    try:
        result = reply_assistant_service.create_gmail_draft(db, row, to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except EmailProviderError as exc:
        logger.warning("reply gmail draft failed (id=%s): %s", reply_assist_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    return ReplyGmailDraftResult(**result)
