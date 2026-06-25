"""日本未上陸判定モデル。

海外案件の商品が日本で既に売られているかを 5 サイト（Amazon.co.jp / 楽天 /
Yahoo!ショッピング / Makuake / GreenFunding）で検索し、ヒット根拠を保存したうえで
「未上陸 / 可能性あり / 日本販売済み」を判定する。判定は履歴として残す。

- AvailabilityCheck … 1 回の判定（親）。最終判定・最大一致スコア・根拠サマリ。
- AvailabilityHit   … 判定の根拠（子）。ヒットしたサイト・商品名・URL・一致スコア。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AvailabilityVerdict(str, enum.Enum):
    not_landed = "not_landed"   # 未上陸
    possible = "possible"       # 可能性あり
    sold = "sold"               # 日本販売済み


class AvailabilitySite(str, enum.Enum):
    amazon = "amazon"
    rakuten = "rakuten"
    yahoo = "yahoo"
    makuake = "makuake"
    greenfunding = "greenfunding"


class AvailabilityCheck(Base):
    __tablename__ = "availability_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    verdict: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 最大一致スコア（0〜100）。判定の主要根拠。
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 検索クエリと判定サマリ（根拠の要約文）
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 判定エンジン（mock-availability-v1 / claude-... など）
    engine: Mapped[str] = mapped_column(String(60), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AvailabilityHit(Base):
    __tablename__ = "availability_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_id: Mapped[int] = mapped_column(
        ForeignKey("availability_checks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    site: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
