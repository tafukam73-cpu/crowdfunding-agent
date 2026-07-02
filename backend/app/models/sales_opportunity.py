"""営業案件管理モデル（Contact Intelligence v5）。

Contact Intelligence（連絡先探索 + AI 営業分析）の結果を「営業活動」に変換して
管理する。既存の CRM（Maker/Contact/SalesActivity）とは別軸で、案件（発見結果）
単位の営業ステータス・次アクション・期限・メモを追跡する。自動同期は行わない。
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SalesOpportunityStatus(str, enum.Enum):
    not_started = "not_started"        # 未着手
    researched = "researched"          # 調査済み
    contacted = "contacted"            # 初回連絡済み
    waiting_reply = "waiting_reply"    # 返信待ち
    meeting = "meeting"                # 商談中
    negotiating = "negotiating"        # 条件交渉中
    likely_contract = "likely_contract"  # 契約見込み
    lost = "lost"                      # 失注
    on_hold = "on_hold"                # 保留


class SalesOpportunity(Base):
    __tablename__ = "sales_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 紐づく連絡先探索（Contact Intelligence 結果）。1 探索 1 案件（重複作成防止）。
    contact_discovery_id: Mapped[int] = mapped_column(
        ForeignKey("contact_discoveries.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # --- 案件情報（作成時に Contact Intelligence 結果から引き継ぐ） ---
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 営業分析（スコア・優先度・推奨チャネル・連絡先） ---
    sales_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 優先度（high / medium / low）
    sales_priority: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    recommended_channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    primary_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_social_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 営業活動の状態 ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SalesOpportunityStatus.not_started.value,
        server_default=SalesOpportunityStatus.not_started.value,
        index=True,
    )
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
