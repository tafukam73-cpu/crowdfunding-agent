"""AI 評価モデル。

1 案件に対し複数回の評価履歴を保持する（再評価で行を追加）。
最新評価は projects.latest_score / latest_recommendation にキャッシュする。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Recommendation(str, enum.Enum):
    high = "high"   # 高
    mid = "mid"     # 中
    low = "low"     # 低


class AiEvaluation(Base):
    __tablename__ = "ai_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 総合スコア 0〜100
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    # 推奨度 high / mid / low
    recommendation: Mapped[str] = mapped_column(String(10), nullable=False)

    # 軸別スコア（要件の評価基準を JSON で保持。後から軸の増減に強い）
    axis_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    concerns: Mapped[str | None] = mapped_column(Text, nullable=True)
    sales_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 評価に使ったモデル/エンジン（例: mock-v1 / claude-...）
    model: Mapped[str] = mapped_column(String(60), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
