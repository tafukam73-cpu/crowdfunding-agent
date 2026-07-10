"""営業適性アセスメント（Sales Copilot v2）。

営業対象案件（projects）に対する 3 つの適性スコアを保存する土台:
  - japan_market_fit_score   日本市場適性（消費者・クラファン市場での受容性）
  - exclusivity_score        独占販売可能性（独占販売権を取れる見込み）
  - makuake_fit_score        Makuake 適性（日本クラファン再ローンチ適性）

まずルールベースで算出し（engine=rule-based-...）、任意で AI 補正を重ねられる設計。
非破壊: 実行のたびに履歴を1行追加し、最新を採用する（既存テーブルは変更しない）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SalesAssessment(Base):
    __tablename__ = "sales_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # --- 3 つの適性スコア（0〜100・未算出は None） ---
    japan_market_fit_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    exclusivity_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    makuake_fit_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    # 3 スコアを束ねた総合営業優先度（0〜100）。v2 の優先度ランキングに使う。
    overall_priority_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    # 各スコアの内訳・理由・入力シグナル（{japan_market_fit:{score,level,reasons,subscores}, ...}）
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # データ充足度（0〜100）。入力が乏しいほど低い。
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 算出エンジン（rule-based-sales-assessment-v1 等）と AI 補正の有無。
    engine: Mapped[str] = mapped_column(String(60), nullable=False)
    ai_adjusted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
