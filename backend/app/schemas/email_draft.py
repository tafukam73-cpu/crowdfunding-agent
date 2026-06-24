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
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
