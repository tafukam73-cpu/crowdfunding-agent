"""スクレイピング実行履歴モデル。

1 回の収集実行（サイト単位）を 1 レコードとして記録する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScrapeStatus(str, enum.Enum):
    running = "running"   # 実行中
    success = "success"   # 正常終了
    error = "error"       # エラー終了


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    site: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapeStatus.running.value, index=True
    )

    # どの収集ジョブ（job_runs）の一部か。手動/日次の親ジョブに紐づく（単発実行は null）
    job_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # 取得・反映件数
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
