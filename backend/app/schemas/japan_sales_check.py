"""日本販売状況チェック API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.japan_sales_check import JapanSalesStatus


class ChannelFinding(BaseModel):
    channel: str
    label: str
    # found / limited / not_found / unknown
    status: str
    search_url: str
    note: str = ""


class JapanSalesCheckOut(BaseModel):
    id: int
    project_id: int
    maker_id: int | None = None
    status: JapanSalesStatus

    sales_value_stars: int | None = None
    channels: list[ChannelFinding] | None = None
    search_queries: list[str] | None = None
    ai_comment: str | None = None
    summary: str | None = None

    model: str | None = None
    notes: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
