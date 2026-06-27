"""返信メール AI サポートの共通インターフェース。

モック・Claude の双方がこの ReplyAssistant を実装する。出力は ReplyAssistResult
（DB / モデル非依存）。署名は AI に生成させず、サービス層で固定テンプレートを
返信本文の末尾へ連結する（営業メール生成と同じ方針）。

get_reply_assistant() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockReplyAssistant（既定）
  - 設定済み            → ClaudeReplyAssistant
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.config import settings
from app.models.project import Project


class ReplyTone(str, enum.Enum):
    professional = "professional"
    friendly = "friendly"
    concise = "concise"
    detailed = "detailed"
    executive = "executive"


DEFAULT_REPLY_TONE = ReplyTone.professional

# 相手の意図（intent）の取りうる値
INTENTS = [
    "interested",
    "needs_more_info",
    "asks_terms",
    "requests_call",
    "not_interested",
    "already_has_distributor",
    "unclear",
]
# 感情 / 温度感
SENTIMENTS = ["positive", "neutral", "negative"]


class IncomingEmail(BaseModel):
    subject: str = ""
    body: str = ""
    sender: str = ""  # incoming_from


class ReplyAssistResult(BaseModel):
    detected_language: str = "en"
    japanese_summary: str = ""
    intent: str = "unclear"
    sentiment: str = "neutral"
    key_points: list[str] = Field(default_factory=list)
    requested_actions: list[str] = Field(default_factory=list)
    risks_or_cautions: list[str] = Field(default_factory=list)
    recommended_next_action: str = ""
    reply_subject: str = ""
    # 署名なしの返信本文（サービス層で署名を連結する）
    reply_body: str = ""
    model: str = ""


class ReplyAssistant(ABC):
    name: str = "base"
    last_usage: dict | None = None

    @abstractmethod
    def assist(
        self,
        project: Project,
        incoming: IncomingEmail,
        tone: ReplyTone = DEFAULT_REPLY_TONE,
    ) -> ReplyAssistResult:
        """受信メールを解析し、解析結果と英語の返信案を返す。"""
        raise NotImplementedError


def get_reply_assistant() -> ReplyAssistant:
    if settings.anthropic_api_key:
        from app.ai.claude_reply_assistant import ClaudeReplyAssistant

        return ClaudeReplyAssistant(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_reply_assistant import MockReplyAssistant

    return MockReplyAssistant()
