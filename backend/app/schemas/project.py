"""案件 API のスキーマ（pydantic v2）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.evaluation import Recommendation
from app.models.project import ProjectStatus, SalesStatus, SourceSite


class ProjectBase(BaseModel):
    title: str = Field(..., max_length=500)
    source_site: SourceSite = SourceSite.other
    source_url: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    currency: str = "USD"
    goal_amount: Decimal | None = None
    raised_amount: Decimal | None = None
    backers_count: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    maker_name: str | None = None
    maker_url: str | None = None
    contact_info: str | None = None


class ProjectCreate(ProjectBase):
    status: ProjectStatus = ProjectStatus.new


class ProjectUpdate(BaseModel):
    """部分更新。送られたフィールドのみ反映する。"""

    title: str | None = Field(None, max_length=500)
    source_site: SourceSite | None = None
    source_url: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    currency: str | None = None
    goal_amount: Decimal | None = None
    raised_amount: Decimal | None = None
    backers_count: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    maker_name: str | None = None
    maker_url: str | None = None
    contact_info: str | None = None
    status: ProjectStatus | None = None


class ProjectStatusUpdate(BaseModel):
    status: ProjectStatus


class ProjectOut(ProjectBase):
    id: int
    status: ProjectStatus
    sales_status: SalesStatus = SalesStatus.not_started
    latest_score: int | None = None
    latest_recommendation: Recommendation | None = None
    maker_id: int | None = None
    latest_availability: str | None = None
    latest_availability_at: datetime | None = None
    # HTML 除去済みの読みやすい概要（UI 表示用。元の description も併せて返す）
    description_clean: str | None = None
    # 詳細補完の根拠（creator URL / ブランド名 / 商品説明 / 公式サイト候補（確度）/
    # SNS / 取得元 URL / 取得不能理由 など）。未補完なら None。
    enrichment: dict | None = None
    # 海外クラファンの商品ページ URL（source_url のうち source_site と整合するもの）。
    # 取得できない場合は None ＋ campaign_url_missing=true（公式サイトで代用しない）。
    campaign_url: str | None = None
    campaign_url_missing: bool = True
    campaign_url_missing_reason: str | None = None
    # メーカー/商品の公式サイト URL（campaign_url とは別物）
    official_site_url: str | None = None
    # 日本クラファン適性ゲート（メール探索の事前判定）の結果。
    # 内部スコア（japan_crowdfunding_score）と内部向け判定理由は画面に出さない。
    eligible_for_contact_search: bool | None = None
    gate_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectListOut(BaseModel):
    """ページング付き一覧レスポンス。"""

    items: list[ProjectOut]
    total: int
    page: int
    page_size: int
