"""営業先連絡先探索 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.contact_discovery import DiscoveryStatus


class DiscoveredEmail(BaseModel):
    email: str
    score: int
    tier: str
    # 所有者分類（maker / platform / monitoring / unknown）。
    # platform は UI 非表示。過去データには無いため任意。
    email_owner: str | None = None
    sources: list[str] = []


class ApproachOption(BaseModel):
    channel: str
    label: str
    url: str | None = None
    score: int
    reason: str | None = None


class AiCandidateEmail(BaseModel):
    """AI 連絡先リサーチが提示し、既存フィルタで再検証済みの候補メール。"""

    email: str
    score: int = 0
    confidence: str | None = None
    reason: str | None = None
    source_url: str | None = None
    # 所有者分類（maker / unknown など。platform は保存時点で除外済み）
    email_owner: str | None = None


class AiSource(BaseModel):
    url: str
    type: str | None = None
    note: str | None = None


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
    # Contact Intelligence
    contactability_score: int | None = None
    recommended_channel: str | None = None
    recommended_action: str | None = None
    discovery_checklist: dict[str, bool] | None = None
    approach_options: list[ApproachOption] | None = None
    search_queries: list[str] | None = None
    evidence_summary: str | None = None

    notes: str | None = None
    error: str | None = None

    # --- AI 連絡先リサーチ（自動抽出とは区別して表示） ---
    ai_researched: bool = False
    ai_primary_email: str | None = None
    ai_contact_form_url: str | None = None
    ai_instagram_url: str | None = None
    ai_facebook_url: str | None = None
    ai_linkedin_url: str | None = None
    ai_candidate_emails: list[AiCandidateEmail] | None = None
    ai_search_queries: list[str] | None = None
    ai_sources: list[AiSource] | None = None
    ai_confidence_score: int | None = None
    ai_recommended_channel: str | None = None
    ai_notes: str | None = None
    ai_model: str | None = None
    ai_researched_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OutreachMessageOut(BaseModel):
    """問い合わせフォーム / SNS DM 用の短文アウトリーチ文。"""

    channel: str
    channel_label: str
    text: str
    char_count: int


class ApplyToCrmRequest(BaseModel):
    """CRM 反映リクエスト。email 未指定でも推奨チャネル等を記録する。"""

    email: str | None = None


class ApplyToCrmResult(BaseModel):
    maker_id: int
    contact_id: int | None = None
    email: str | None = None
    recorded: bool = True
