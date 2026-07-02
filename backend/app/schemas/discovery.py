"""発掘商品候補 API のスキーマ（Discovery Engine v1-1）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.discovered_product import (
    DiscoveredProductStatus,
    DiscoverySourcePlatform,
)


class DiscoveredProductCreate(BaseModel):
    """商品候補の登録。source_url が既存と重複する場合は既存を再利用する。"""

    source_platform: DiscoverySourcePlatform = DiscoverySourcePlatform.other
    source_url: str | None = None

    project_title: str | None = None
    creator_name: str | None = None
    product_name: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    country: str | None = None

    status: DiscoveredProductStatus = DiscoveredProductStatus.unknown
    funding_amount: Decimal | None = None
    funding_goal: Decimal | None = None
    backers_count: int | None = None

    launch_date: date | None = None
    end_date: date | None = None

    official_website_url: str | None = None

    japan_fit_score: int | None = None
    crowdfunding_fit_score: int | None = None
    novelty_score: int | None = None
    logistics_score: int | None = None
    regulatory_risk_score: int | None = None
    competition_risk_score: int | None = None
    japan_entry_risk_score: int | None = None
    overall_discovery_score: int | None = None
    discovery_reasoning: str | None = None
    recommended_next_action: str | None = None

    contact_discovery_id: int | None = None

    # True のとき、作成直後に AI Discovery Scoring で自動スコアリングする（既定 False）。
    auto_score: bool = False

    @field_validator("source_platform")
    @classmethod
    def _platform_value(cls, v):
        return v.value if isinstance(v, DiscoverySourcePlatform) else v

    @field_validator("status")
    @classmethod
    def _status_value(cls, v):
        return v.value if isinstance(v, DiscoveredProductStatus) else v


class DiscoveryScoreOut(BaseModel):
    """AI Discovery Scoring の結果（スコア系カラムのみ）。"""

    japan_fit_score: int | None = None
    crowdfunding_fit_score: int | None = None
    novelty_score: int | None = None
    logistics_score: int | None = None
    regulatory_risk_score: int | None = None
    competition_risk_score: int | None = None
    japan_entry_risk_score: int | None = None
    overall_discovery_score: int | None = None
    discovery_reasoning: str | None = None
    recommended_next_action: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DiscoveredProductUpdate(BaseModel):
    """商品候補の更新（渡されたフィールドのみ更新）。"""

    source_platform: DiscoverySourcePlatform | None = None
    project_title: str | None = None
    creator_name: str | None = None
    product_name: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    country: str | None = None

    status: DiscoveredProductStatus | None = None
    funding_amount: Decimal | None = None
    funding_goal: Decimal | None = None
    backers_count: int | None = None

    launch_date: date | None = None
    end_date: date | None = None

    official_website_url: str | None = None

    japan_fit_score: int | None = None
    crowdfunding_fit_score: int | None = None
    novelty_score: int | None = None
    logistics_score: int | None = None
    regulatory_risk_score: int | None = None
    competition_risk_score: int | None = None
    japan_entry_risk_score: int | None = None
    overall_discovery_score: int | None = None
    discovery_reasoning: str | None = None
    recommended_next_action: str | None = None

    contact_discovery_id: int | None = None

    @field_validator("source_platform")
    @classmethod
    def _platform_value(cls, v):
        return v.value if isinstance(v, DiscoverySourcePlatform) else v

    @field_validator("status")
    @classmethod
    def _status_value(cls, v):
        return v.value if isinstance(v, DiscoveredProductStatus) else v


class DiscoveredProductOut(BaseModel):
    id: int

    source_platform: DiscoverySourcePlatform
    source_url: str | None = None

    project_title: str | None = None
    creator_name: str | None = None
    product_name: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    country: str | None = None

    status: DiscoveredProductStatus
    funding_amount: Decimal | None = None
    funding_goal: Decimal | None = None
    backers_count: int | None = None

    launch_date: date | None = None
    end_date: date | None = None

    official_website_url: str | None = None

    japan_fit_score: int | None = None
    crowdfunding_fit_score: int | None = None
    novelty_score: int | None = None
    logistics_score: int | None = None
    regulatory_risk_score: int | None = None
    competition_risk_score: int | None = None
    japan_entry_risk_score: int | None = None
    overall_discovery_score: int | None = None
    discovery_reasoning: str | None = None
    recommended_next_action: str | None = None

    contact_discovery_id: int | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
