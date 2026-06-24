"""日本クラファンの成功案件モデル（Makuake / GreenFunding）。

海外案件（projects）とは別管理。営業時の「日本での類似成功事例」比較に使う。
応援購入総額（raised_amount）が一定額以上のものを成功案件として保存する。
"""
from __future__ import annotations

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

# 成功案件とみなす応援購入総額の下限（円）
MIN_SUCCESS_JPY = 5_000_000


class JapaneseSuccessProject(Base):
    __tablename__ = "japanese_success_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="JPY")
    goal_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    raised_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    backers_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    maker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    maker_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
