"""AI 評価 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.evaluation import Recommendation


class EvaluationOut(BaseModel):
    id: int
    project_id: int
    total_score: int
    recommendation: Recommendation
    axis_scores: dict[str, int]
    reasons: str | None
    concerns: str | None
    sales_comment: str | None
    model: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvaluateBulkResult(BaseModel):
    """一括評価の受付結果。"""

    queued: int
