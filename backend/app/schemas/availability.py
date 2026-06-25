"""日本未上陸判定 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.availability import AvailabilityVerdict


class AvailabilityHitOut(BaseModel):
    id: int
    site: str
    title: str | None
    url: str | None
    match_score: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AvailabilityCheckOut(BaseModel):
    id: int
    project_id: int
    verdict: AvailabilityVerdict
    score: int
    query: str | None
    summary: str | None
    engine: str
    created_at: datetime
    hits: list[AvailabilityHitOut]

    model_config = ConfigDict(from_attributes=True)
