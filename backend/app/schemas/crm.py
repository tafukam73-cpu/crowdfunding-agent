"""CRM API のスキーマ（pydantic v2）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.crm import ActivityKind, CrmStatus
from app.services.url_validation import is_valid_business_url


# --- 担当者 ---
class ContactBase(BaseModel):
    name: str = Field(..., max_length=255)
    role: str | None = None
    department: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    role: str | None = None
    department: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None


class ContactOut(ContactBase):
    id: int
    maker_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("linkedin_url")
    @classmethod
    def _no_dummy_linkedin(cls, v: str | None) -> str | None:
        # example.com 等のダミー URL は表示しない。
        return v if (v and is_valid_business_url(v)) else None


# --- 営業履歴 ---
class ActivityBase(BaseModel):
    kind: ActivityKind = ActivityKind.note
    summary: str
    contact_id: int | None = None
    project_id: int | None = None
    occurred_at: datetime | None = None


class ActivityCreate(ActivityBase):
    pass


class ActivityOut(BaseModel):
    id: int
    maker_id: int
    contact_id: int | None
    project_id: int | None
    kind: ActivityKind
    summary: str
    occurred_at: datetime
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- メーカー ---
class MakerBase(BaseModel):
    name: str = Field(..., max_length=255)
    website_url: str | None = None
    country: str | None = None
    status: CrmStatus = CrmStatus.lead
    next_action: str | None = None
    next_action_date: date | None = None
    notes: str | None = None


class MakerCreate(MakerBase):
    pass


class MakerUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    website_url: str | None = None
    country: str | None = None
    status: CrmStatus | None = None
    next_action: str | None = None
    next_action_date: date | None = None
    notes: str | None = None


class MakerOut(MakerBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("website_url")
    @classmethod
    def _no_dummy_website(cls, v: str | None) -> str | None:
        # 既存 DB に example.com 等が残っていても公式サイトとして表示しない（要件5）。
        return v if (v and is_valid_business_url(v)) else None


class MakerDetailOut(MakerOut):
    """メーカー詳細：担当者・営業履歴・紐づく案件IDを含む。"""

    contacts: list[ContactOut]
    activities: list[ActivityOut]
    project_ids: list[int]


class MakerListOut(BaseModel):
    items: list[MakerOut]
    total: int
    page: int
    page_size: int


class ReminderOut(BaseModel):
    """リマインダー：次回アクション日が設定されたメーカー。"""

    maker_id: int
    maker_name: str
    status: CrmStatus
    next_action: str | None
    next_action_date: date
    overdue: bool  # 期限切れ（今日より前）
