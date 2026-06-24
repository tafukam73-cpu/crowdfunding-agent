"""海外クラファン案件モデル。

要件定義「3.1 取得項目」に対応したカラムを持つ。
AI 評価関連のカラムは Step 4 で追加する。
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceSite(str, enum.Enum):
    """収集元サイト。"""

    kickstarter = "kickstarter"
    indiegogo = "indiegogo"
    wadiz = "wadiz"
    makuake = "makuake"
    greenfunding = "greenfunding"
    other = "other"


class ProjectStatus(str, enum.Enum):
    """営業進捗ステータス。"""

    new = "new"               # 新規
    reviewing = "reviewing"   # 検討中
    contacted = "contacted"   # 連絡済み
    negotiating = "negotiating"  # 交渉中
    won = "won"               # 獲得（独占販売権交渉成立 など）
    rejected = "rejected"     # 見送り


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- 基本情報 ---
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_site: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- メディア ---
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 資金情報 ---
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    goal_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    raised_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    backers_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- 掲載期間 ---
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- メーカー / 営業先情報 ---
    maker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    maker_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 営業ステータス ---
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProjectStatus.new.value, index=True
    )

    # --- AI 評価キャッシュ（最新評価。一覧のソート/フィルタ用） ---
    latest_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    latest_recommendation: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )

    # --- メタ ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
