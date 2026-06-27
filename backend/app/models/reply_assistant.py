"""返信メール AI サポートモデル。

海外メーカーから届いた返信メールを貼り付けると、AI が内容を解析し（意図・感情・
日本語要約・重要点・要求・注意点・推奨次アクション）、英語の返信案を生成する。
Gmail 返信下書きの作成結果（draft_id / web_link）も保存する。送信はしない。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReplyStatus(str, enum.Enum):
    draft = "draft"           # 解析前/作成中
    completed = "completed"   # 解析・返信案生成 完了
    failed = "failed"         # 失敗（JSON パース失敗など）


class ReplyAssistant(Base):
    __tablename__ = "reply_assistants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    maker_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # --- 受信メール（入力） ---
    incoming_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    incoming_body: Mapped[str] = mapped_column(Text, nullable=False)
    incoming_from: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- AI 解析結果 ---
    detected_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    japanese_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    sentiment: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    key_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    requested_actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risks_or_cautions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recommended_next_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 返信案 ---
    reply_tone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reply_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Gmail 返信下書き ---
    gmail_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    gmail_web_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReplyStatus.draft.value, index=True
    )
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
