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


class DiscoveryRunRequest(BaseModel):
    """発掘実行（Discovery Crawler Framework）のリクエスト。"""

    source_platform: DiscoverySourcePlatform = DiscoverySourcePlatform.manual
    query: str | None = None
    limit: int = 20
    # True のとき保存した候補を AI Discovery Scoring で自動評価する（既定 False）。
    auto_score: bool = False

    @field_validator("source_platform")
    @classmethod
    def _platform_value(cls, v):
        return v.value if isinstance(v, DiscoverySourcePlatform) else v

    @field_validator("limit")
    @classmethod
    def _limit_range(cls, v):
        # 想定外の巨大 limit を弾く（安全側・0〜200）
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 20
        return max(0, min(200, v))


class DiscoveryRunResult(BaseModel):
    """発掘実行の結果サマリ。"""

    run_id: int | None = None
    source_platform: str
    query: str | None = None
    status: str
    found_count: int
    saved_count: int
    duplicate_count: int
    error_message: str | None = None
    product_ids: list[int] = []
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # True: 実サイトから取得を試みた（Kickstarter 等）。False: fetch 未接続（0 件は仕様）。
    network_fetched: bool = False


class DiscoveryContactIntelligenceResult(BaseModel):
    """発掘商品から Contact Intelligence を開始した結果。"""

    product_id: int
    contact_discovery_id: int | None = None
    used_url: str | None = None
    # started（新規開始）/ existing（既存連携あり）/ error（URL 未設定 等）
    status: str
    message: str


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
