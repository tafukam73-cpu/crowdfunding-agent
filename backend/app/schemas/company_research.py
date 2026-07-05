"""AI 企業リサーチ API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.company_research import ResearchStatus
from app.services.url_validation import filter_business_urls, is_valid_business_url


class CompanyResearchOut(BaseModel):
    id: int
    project_id: int
    maker_name: str | None = None
    official_site_url: str | None = None
    project_url: str | None = None
    research_status: ResearchStatus

    brand_summary: str | None = None
    company_mission: str | None = None
    product_summary: str | None = None
    key_product_features: list[str] | None = None
    brand_strengths: list[str] | None = None
    differentiation_points: list[str] | None = None
    japan_market_fit: str | None = None
    personalized_compliment: str | None = None
    outreach_angles: list[str] | None = None
    risks_or_cautions: list[str] | None = None
    sources: list[str] | None = None

    model: str | None = None
    raw_notes: str | None = None
    created_at: datetime
    updated_at: datetime

    # --- ダミー URL サニタイズ（古い行に example URL が残っていても表示しない） ---
    @field_validator("official_site_url", "project_url")
    @classmethod
    def _no_dummy_url(cls, v: str | None) -> str | None:
        return v if (v and is_valid_business_url(v)) else None

    @field_validator("sources")
    @classmethod
    def _clean_sources(cls, v: list[str] | None) -> list[str] | None:
        return filter_business_urls(v) or None

    model_config = ConfigDict(from_attributes=True)
