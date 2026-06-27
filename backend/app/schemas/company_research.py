"""AI 企業リサーチ API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.company_research import ResearchStatus


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

    model_config = ConfigDict(from_attributes=True)
