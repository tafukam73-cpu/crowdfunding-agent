"""日本販売状況チェックモデル。

営業前に「既に日本で販売されていないか」を AI が調査し、営業価値（★1〜5）を
判定した結果を保存する。チャネル（Amazon.co.jp / 楽天 / Yahoo! ショッピング /
日本代理店 / 日本法人 / Makuake / GREEN FUNDING）ごとの販売状況・検索 URL・
所見と、AI コメントを持つ。取得失敗してもアプリは落とさず status=failed で記録する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JapanSalesStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class JapanSalesCheck(Base):
    __tablename__ = "japan_sales_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 紐づく CRM メーカー（あれば）。
    maker_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JapanSalesStatus.pending.value, index=True
    )

    # 営業価値（★1〜5）。5=日本未販売で最も営業価値が高い。
    sales_value_stars: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # チャネルごとの調査結果 [{channel,label,status,search_url,note}]
    # status は found / limited / not_found / unknown。
    channels: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 検索に使ったクエリ候補（手動確認用）
    search_queries: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # AI コメント（日本語）と一行サマリ
    ai_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
