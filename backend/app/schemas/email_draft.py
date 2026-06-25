"""営業メール下書き API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.email_draft import EmailType


class EmailDraftOut(BaseModel):
    id: int
    project_id: int
    email_type: EmailType
    subject: str
    body: str
    language: str
    model: str
    provider: str | None = None
    provider_draft_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProviderDraftRequest(BaseModel):
    """プロバイダー下書き作成リクエスト。to 未指定なら宛先を自動解決。"""

    to: str | None = None


class ProviderDraftResult(BaseModel):
    """プロバイダー下書き作成結果。"""

    provider: str
    draft_id: str | None
    status: str
    to: str
    web_link: str | None = None
    detail: str | None = None


class EmailProviderInfo(BaseModel):
    """現在有効なメールプロバイダー情報。"""

    provider: str
    gmail_configured: bool
