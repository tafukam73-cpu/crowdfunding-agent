"""CRM（営業管理）モデル。

営業の起点は「メーカー（企業）」。担当者・営業履歴・交渉ステータス・
次回アクション/リマインダーをメーカーにぶら下げる。海外案件（projects）は
maker_id でメーカーに紐づく（1 メーカー : 多 案件）。
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


class CrmStatus(str, enum.Enum):
    """メーカーとの交渉ステータス（営業パイプライン）。"""

    lead = "lead"               # リード（未接触）
    contacted = "contacted"     # 連絡済み
    negotiating = "negotiating" # 交渉中
    won = "won"                 # 成約（独占販売権獲得 など）
    lost = "lost"               # 見送り / 失注


class ActivityKind(str, enum.Enum):
    """営業履歴の種別。"""

    email = "email"       # メール
    call = "call"         # 電話
    meeting = "meeting"   # 打ち合わせ
    note = "note"         # メモ
    other = "other"       # その他


class Maker(Base):
    __tablename__ = "makers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # 交渉ステータス
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CrmStatus.lead.value, index=True
    )

    # 次回アクション / リマインダー
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_action_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True
    )

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


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    maker_id: Mapped[int] = mapped_column(
        ForeignKey("makers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # role は役職（例: Head of Business Development）。
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 部署分類（Business Development / Partnership / Sales ...）と LinkedIn
    department: Mapped[str | None] = mapped_column(String(80), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(60), nullable=True)
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


class SalesActivity(Base):
    __tablename__ = "sales_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    maker_id: Mapped[int] = mapped_column(
        ForeignKey("makers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
