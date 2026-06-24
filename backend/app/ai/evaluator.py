"""AI 評価の共通インターフェース。

モック評価器・Claude 評価器はこの Evaluator を実装する。
評価結果は EvaluationResult（サイト/モデル非依存の構造）で返す。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.models.evaluation import Recommendation
from app.models.project import Project

# 要件「3.2 評価基準」に対応する評価軸（キー＝表示名）
AXES: list[str] = [
    "日本未上陸の可能性",
    "ガジェット性",
    "動画映え",
    "新規性",
    "日本クラファン適性",
    "Makuake向き",
    "GreenFunding向き",
    "営業すべきか",
]


class EvaluationResult(BaseModel):
    """評価器の出力（DB やレスポンスに依存しない中立構造）。"""

    total_score: int = Field(..., ge=0, le=100)
    recommendation: Recommendation
    axis_scores: dict[str, int]
    reasons: str
    concerns: str
    sales_comment: str
    model: str


def score_to_recommendation(total: int) -> Recommendation:
    """総合スコア → 推奨度の共通ルール（評価器間で統一）。"""
    if total >= 75:
        return Recommendation.high
    if total >= 50:
        return Recommendation.mid
    return Recommendation.low


class Evaluator(ABC):
    """全評価器の基底クラス。"""

    #: 評価エンジン名（DB の model 列に記録）
    name: str = "base"
    #: 直近呼び出しのトークン使用量 {"input_tokens", "output_tokens"}（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def evaluate(self, project: Project) -> EvaluationResult:
        """1 案件を評価して結果を返す。"""
        raise NotImplementedError
