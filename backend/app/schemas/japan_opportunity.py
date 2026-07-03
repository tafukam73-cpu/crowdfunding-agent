"""日本市場機会 分析 API のスキーマ（Japan Opportunity Engine v1-2）。

スコアは 0〜100（高いほど日本展開/営業に有利）。v1-2 は保存・取得の土台のため、
スコア系はすべて任意（未評価は None）。evidence_json は dict/list を安全に持つ。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# 分析の全スコア軸（サービス側の正規化と共有）
SCORE_FIELDS = (
    "japan_market_fit_score",
    "japan_entry_gap_score",
    "crowdfunding_fit_score",
    "retail_fit_score",
    "regulatory_safety_score",
    "logistics_score",
    "margin_potential_score",
    "competition_gap_score",
    "sales_success_score",
    "overall_opportunity_score",
    "confidence_score",
)


class _AnalysisScores(BaseModel):
    """スコア軸（0〜100・任意）。Create/Update/Out で共有する。"""

    japan_market_fit_score: int | None = None
    japan_entry_gap_score: int | None = None
    crowdfunding_fit_score: int | None = None
    retail_fit_score: int | None = None
    regulatory_safety_score: int | None = None
    logistics_score: int | None = None
    margin_potential_score: int | None = None
    competition_gap_score: int | None = None
    sales_success_score: int | None = None
    overall_opportunity_score: int | None = None
    confidence_score: int | None = None

    # 根拠テキスト
    japan_presence_summary: str | None = None
    competition_summary: str | None = None
    regulatory_summary: str | None = None
    logistics_summary: str | None = None
    pricing_summary: str | None = None
    opportunity_reasoning: str | None = None
    recommended_strategy: str | None = None
    recommended_next_action: str | None = None

    # 根拠明細（dict / list を許容）
    evidence_json: Any | None = None


class JapanOpportunityAnalysisCreate(_AnalysisScores):
    """分析の作成。discovered_product_id は必須。"""

    discovered_product_id: int


class JapanOpportunityAnalysisUpdate(_AnalysisScores):
    """分析の更新（渡されたフィールドのみ更新）。discovered_product_id は変更不可。"""


class JapanOpportunityAnalysisOut(_AnalysisScores):
    id: int
    discovered_product_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
