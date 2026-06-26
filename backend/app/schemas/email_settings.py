"""メール設定 API のスキーマ（pydantic v2）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EmailSettingsBase(BaseModel):
    company_name: str | None = Field(None, max_length=255)
    sender_name: str | None = Field(None, max_length=255)
    sender_title: str | None = Field(None, max_length=255)
    sender_department: str | None = Field(None, max_length=255)
    sender_email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=60)
    website_url: str | None = None
    company_profile: str | None = None
    signature_template: str | None = None


class EmailSettingsUpdate(EmailSettingsBase):
    """PUT /email-settings のリクエスト。全項目任意（部分更新可）。"""


class EmailSettingsOut(EmailSettingsBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
