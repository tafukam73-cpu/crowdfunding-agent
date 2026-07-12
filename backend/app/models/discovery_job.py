"""商品発掘（Discovery Crawler）の非同期ジョブモデル。

一覧取得は軽量（Wadiz/Zeczec は httpx）だが、Kickstarter は Playwright、Ulule/
Indiegogo も条件により重い。HTTP リクエスト内で完了させると 12 秒タイムアウトで
画面が固まるため、Contact Intelligence と同じくジョブ化してポーリングで進捗を返す。

Contact Intelligence ジョブ（contact_intelligence_jobs / project_id 紐づけ）とは
責務が別（発掘は projects に紐づかない）ため、独立したテーブル・独立した同時実行
セマフォで管理する（互いに並列枠を奪わない）。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DiscoveryJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source_platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # 保存時に自動スコアリングするか（実取得プラットフォームは既定 True）。
    auto_score: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DiscoveryJobStatus.queued.value,
        server_default=DiscoveryJobStatus.queued.value,
        index=True,
    )
    # 0〜100 の進捗
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # --- 結果サマリ（完了時に埋める） ---
    found_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 発掘実行ログ（discovery_runs）への参照（監視用・任意）
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
