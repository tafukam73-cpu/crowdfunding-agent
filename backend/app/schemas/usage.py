"""コスト推定・使用量集計のスキーマ。"""
from __future__ import annotations

from pydantic import BaseModel


class EvaluateEstimateOut(BaseModel):
    mode: str            # claude / mock
    model: str
    count: int           # 未評価件数
    est_input_tokens: int
    est_output_tokens: int
    est_cost_usd: float


class UsageBucket(BaseModel):
    cost_usd: float
    input_tokens: int
    output_tokens: int
    calls: int


class UsageSummaryOut(BaseModel):
    today: UsageBucket
    month: UsageBucket
    total: UsageBucket
