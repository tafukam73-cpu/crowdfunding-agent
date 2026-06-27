"""営業先連絡先探索 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.contact_discovery import DiscoveryStatus


class DiscoveredEmail(BaseModel):
    email: str
    score: int
    tier: str
    sources: list[str] = []


class ContactDiscoveryOut(BaseModel):
    id: int
    project_id: int
    maker_id: int | None = None
    status: DiscoveryStatus

    primary_email: str | None = None
    primary_contact_form_url: str | None = None
    official_site_url: str | None = None

    instagram_url: str | None = None
    facebook_url: str | None = None
    twitter_url: str | None = None
    linkedin_url: str | None = None
    youtube_url: str | None = None

    discovered_emails: list[DiscoveredEmail] | None = None
    discovered_forms: list[str] | None = None
    discovered_socials: dict[str, str] | None = None
    searched_urls: list[str] | None = None

    confidence_score: int | None = None
    notes: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplyToCrmRequest(BaseModel):
    """CRM 反映リクエスト。email 未指定なら primary_email を使う。"""

    email: str | None = None


class ApplyToCrmResult(BaseModel):
    maker_id: int
    contact_id: int
    email: str
