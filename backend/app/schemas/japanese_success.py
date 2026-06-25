"""日本クラファン成功案件 API のスキーマ（pydantic v2）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class JapaneseSuccessBase(BaseModel):
    platform: str = Field(..., max_length=30)
    title: str = Field(..., max_length=500)
    source_url: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    currency: str = "JPY"
    goal_amount: Decimal | None = None
    raised_amount: Decimal | None = None
    backers_count: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    maker_name: str | None = None
    maker_url: str | None = None


class JapaneseSuccessCreate(JapaneseSuccessBase):
    pass


class JapaneseSuccessOut(JapaneseSuccessBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SimilarSuccessOut(JapaneseSuccessOut):
    """海外案件に対する類似成功事例。類似度と判定理由を付与する。"""

    match_score: int  # 類似度 0〜100
    match_reasons: list[str]  # 類似と判定した理由


class JapaneseSuccessListOut(BaseModel):
    """ページング付き一覧レスポンス。"""

    items: list[JapaneseSuccessOut]
    total: int
    page: int
    page_size: int


class CollectResult(BaseModel):
    """成功案件収集（Makuake 等）の結果。"""

    fetched: int
    created: int
    updated: int
