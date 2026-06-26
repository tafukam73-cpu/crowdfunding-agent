"""メール設定 API。

- GET /email-settings  保存済みのメール設定を返す（未登録なら空の既定値）
- PUT /email-settings  メール設定を作成/更新（1 件運用）

ここで保存した会社情報・署名テンプレートは営業メール生成時に利用される。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.email_settings import EmailSettingsOut, EmailSettingsUpdate
from app.services import email_settings_service

router = APIRouter(tags=["email-settings"])


@router.get("/email-settings", response_model=EmailSettingsOut | None)
def get_email_settings(db: Session = Depends(get_db)) -> EmailSettingsOut | None:
    """保存済みのメール設定を返す。未登録なら null（フロントは空フォーム表示）。"""
    row = email_settings_service.get_settings(db)
    return row


@router.put("/email-settings", response_model=EmailSettingsOut)
def put_email_settings(
    data: EmailSettingsUpdate, db: Session = Depends(get_db)
) -> EmailSettingsOut:
    """メール設定を作成または更新して返す。"""
    return email_settings_service.upsert_settings(db, data)
