"""案件 API のスキーマ（pydantic v2）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.evaluation import Recommendation
from app.models.project import ProjectStatus, SourceSite


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
    latest_score: int | None = None
    latest_recommendation: Recommendation | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectListOut(BaseModel):
    """ページング付き一覧レスポンス。"""

    items: list[ProjectOut]
    total: int
    page: int
    page_size: int
