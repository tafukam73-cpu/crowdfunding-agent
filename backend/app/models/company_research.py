"""AI 企業リサーチモデル。

営業メール生成の前段で、メーカー・商品について追加リサーチした結果を保存する。
project_url / official_site_url / maker_name / description などから、会社・ブランド
要約、商品概要、具体的な魅力、日本市場適合性、個別称賛、営業の角度、注意点などを
まとめる。Claude 未設定時はモックで生成する。

1 案件につき実行のたびに履歴を残し、最新の completed をメール生成に利用する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ResearchStatus(str, enum.Enum):
    pending = "pending"       # 実行中／未完了
    completed = "completed"   # 完了
    failed = "failed"         # 失敗（JSON パース失敗など）


class CompanyResearch(Base):
    __tablename__ = "company_researches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 入力に使った参照情報
    maker_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_site_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    research_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ResearchStatus.pending.value, index=True
    )

    # --- リサーチ結果 ---
    brand_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_mission: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_product_features: Mapped[list | None] = mapped_column(JSON, nullable=True)
    brand_strengths: Mapped[list | None] = mapped_column(JSON, nullable=True)
    differentiation_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    japan_market_fit: Mapped[str | None] = mapped_column(Text, nullable=True)
    personalized_compliment: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_angles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risks_or_cautions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 生成エンジン（mock-research-v1 / claude-...）
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # 失敗時のエラー内容・Claude の生テキストなど
    raw_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
