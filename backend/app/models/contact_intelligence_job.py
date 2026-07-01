"""Contact Intelligence の非同期ジョブモデル。

AI Web調査 / Document Reader / Search Agent は重く、HTTP リクエスト中に完了させると
タイムアウトする。これらをジョブ化し、進捗・ログ・結果を DB に保存してポーリングで
取得できるようにする。ジョブは別スレッドで実行され、この行を更新していく。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CIJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class CIJobType(str, enum.Enum):
    web_research = "web_research"
    document_reader = "document_reader"
    search_agent = "search_agent"
    full_contact_intelligence = "full_contact_intelligence"


class ContactIntelligenceJob(Base):
    __tablename__ = "contact_intelligence_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CIJobStatus.queued.value, index=True
    )
    # 0〜100 の進捗
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 現在の処理内容（"Web Research 実行中" 等）
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 進捗ログ [{ts, message}]
    logs_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 完了時の結果サマリ
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
