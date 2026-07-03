"""発掘実行ログモデル（Discovery Engine v1-3）。

Discovery Crawler Framework の 1 回の実行（source_platform × query）を記録する。
何件見つかり・保存し・重複で弾いたか、エラーは何かを残し、収集の運用監視に使う。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DiscoveryRunStatus(str, enum.Enum):
    """発掘実行の結果状態。"""

    running = "running"    # 実行中
    success = "success"    # 正常終了（エラーなし）
    partial = "partial"    # 一部エラーだが保存はできた
    error = "error"        # 収集自体が失敗


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source_platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DiscoveryRunStatus.running.value,
        server_default=DiscoveryRunStatus.running.value,
        index=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    found_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    saved_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    duplicate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
