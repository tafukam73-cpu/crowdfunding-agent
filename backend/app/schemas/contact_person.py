"""Contact Hunter AI（担当者発見）の API スキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ContactPersonOut(BaseModel):
    id: int
    project_id: int
    name: str | None = None
    title: str | None = None
    department: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    email_source: str | None = None
    source_url: str | None = None
    confidence: int | None = None
    priority: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplyPersonToCrmRequest(BaseModel):
    """担当者を CRM に反映するリクエスト。"""

    contact_person_id: int


class ApplyPersonToCrmResult(BaseModel):
    maker_id: int
    contact_id: int
    name: str | None = None
    recorded: bool = True
