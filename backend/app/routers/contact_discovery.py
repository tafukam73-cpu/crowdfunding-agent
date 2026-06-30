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

from app.ai.outreach import OUTREACH_CHANNELS
from app.db.session import get_db
from app.schemas.contact_discovery import (
    ApplyToCrmRequest,
    ApplyToCrmResult,
    ContactDiscoveryOut,
    OutreachMessageOut,
)
from app.services import contact_discovery_service, email_service, project_service

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
    "/projects/{project_id}/contact-discovery/ai-research",
    response_model=ContactDiscoveryOut,
)
def run_ai_contact_research(
    project_id: int, db: Session = Depends(get_db)
) -> ContactDiscoveryOut:
    """AI 連絡先リサーチを実行して最新の探索結果に保存する（同期）。

    既存の探索結果が無ければ先に自動探索を実行する。ANTHROPIC_API_KEY 未設定時は
    モックで動作する。失敗時も ai_notes にエラーを記録し 200 で返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return contact_discovery_service.run_ai_research(db, project)


@router.get(
    "/projects/{project_id}/contact-discovery/outreach-message",
    response_model=OutreachMessageOut,
)
def get_outreach_message(
    project_id: int,
    channel: str | None = None,
    db: Session = Depends(get_db),
) -> OutreachMessageOut:
    """メール以外のチャネル向けの短文アウトリーチ文を生成して返す。

    channel 未指定なら最新の探索結果の推奨チャネルを使う。問い合わせフォーム /
    SNS（Instagram / LinkedIn / Facebook）以外のチャネルでは 400 を返す。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")

    if channel is None:
        latest = contact_discovery_service.get_latest(db, project_id)
        channel = latest.recommended_channel if latest else None
    if channel not in OUTREACH_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=(
                "短文アウトリーチ文は問い合わせフォーム / SNS（Instagram / "
                "LinkedIn / Facebook）チャネル向けです。"
            ),
        )
    return email_service.generate_outreach_message(db, project, channel)


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

    latest = contact_discovery_service.get_latest(db, project_id)
    email = (payload.email if payload else None) or None
    if not email and latest is not None:
        email = latest.primary_email
    if latest is None and not email:
        raise HTTPException(
            status_code=400,
            detail="反映する情報がありません。先に連絡先探索を実行してください。",
        )

    # メールが無くても推奨チャネル・アクション等を CRM に記録する
    maker_id, contact_id = contact_discovery_service.apply_to_crm(
        db, project, email=email, row=latest
    )
    return ApplyToCrmResult(
        maker_id=maker_id, contact_id=contact_id, email=email, recorded=True
    )
