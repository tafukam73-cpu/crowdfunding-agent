"""収集ジョブの実行履歴と実行ロック。

- JobRun  … 1 回の収集ジョブ（4 サイトまとめて）の親レコード。手動/日次共通。
- JobLock … 二重実行防止用の DB ロック（PK 一意制約で多重ワーカーでも 1 つだけ取得可）。

サイト単位の結果は scrape_runs（job_run_id で本ジョブに紐づく）に記録する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobTrigger(str, enum.Enum):
    schedule = "schedule"   # 日次スケジュール
    manual = "manual"       # 手動実行（今すぐ実行）


class JobStatus(str, enum.Enum):
    running = "running"      # 実行中
    success = "success"      # 全サイト成功
    partial = "partial"     # 一部サイト失敗
    error = "error"         # 全滅 or ジョブ自体の失敗
    skipped = "skipped"     # 既に実行中のためスキップ


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    trigger: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.running.value, index=True
    )

    sites_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sites_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class JobLock(Base):
    """実行ロック。name をキーに 1 行だけ存在できる（= 同時実行は 1 つ）。"""

    __tablename__ = "job_locks"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    job_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
