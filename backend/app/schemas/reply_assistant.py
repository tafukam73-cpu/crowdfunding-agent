"""返信メール AI サポート API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.ai.reply_assistant import DEFAULT_REPLY_TONE, ReplyTone
from app.models.reply_assistant import ReplyStatus


class ReplyAssistCreate(BaseModel):
    """返信案作成リクエスト（受信メールを貼り付け）。"""

    incoming_subject: str | None = None
    incoming_body: str
    incoming_from: str | None = None
    reply_tone: ReplyTone = DEFAULT_REPLY_TONE


class ReplyAssistOut(BaseModel):
    id: int
    project_id: int
    maker_id: int | None = None

    incoming_subject: str | None = None
    incoming_body: str
    incoming_from: str | None = None

    detected_language: str | None = None
    japanese_summary: str | None = None
    intent: str | None = None
    sentiment: str | None = None
    key_points: list[str] | None = None
    requested_actions: list[str] | None = None
    risks_or_cautions: list[str] | None = None
    recommended_next_action: str | None = None

    reply_tone: str | None = None
    reply_subject: str | None = None
    reply_body: str | None = None

    gmail_draft_id: str | None = None
    gmail_web_link: str | None = None

    model: str | None = None
    status: ReplyStatus
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReplyGmailDraftRequest(BaseModel):
    """Gmail 返信下書き作成リクエスト。to 未指定なら incoming_from を使う。"""

    to: str | None = None


class ReplyGmailDraftResult(BaseModel):
    provider: str
    draft_id: str | None
    status: str
    to: str
    web_link: str | None = None
    detail: str | None = None
