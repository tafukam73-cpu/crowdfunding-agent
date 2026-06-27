"""Claude 返信メール AI サポート。

ANTHROPIC_API_KEY 設定時に get_reply_assistant() がこれを使う。受信メールと案件情報を
渡し、構造化出力（JSON）で解析結果＋英語の返信案を受け取る。署名は付けさせず、
サービス層で email_settings の署名を末尾連結する。

JSON パース失敗時は ValueError を送出し、呼び出し側が status=failed として保存する。
"""
from __future__ import annotations

import json
import logging

from app.ai.reply_assistant import (
    DEFAULT_REPLY_TONE,
    INTENTS,
    SENTIMENTS,
    IncomingEmail,
    ReplyAssistant,
    ReplyAssistResult,
    ReplyTone,
)
from app.models.project import Project

logger = logging.getLogger("ai.claude_reply_assistant")

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "detected_language": {"type": "string"},
        "japanese_summary": {"type": "string"},
        "intent": {"type": "string", "enum": INTENTS},
        "sentiment": {"type": "string", "enum": SENTIMENTS},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "requested_actions": {"type": "array", "items": {"type": "string"}},
        "risks_or_cautions": {"type": "array", "items": {"type": "string"}},
        "recommended_next_action": {"type": "string"},
        "reply_subject": {"type": "string"},
        "reply_body": {"type": "string"},
    },
    "required": [
        "detected_language",
        "japanese_summary",
        "intent",
        "sentiment",
        "key_points",
        "requested_actions",
        "risks_or_cautions",
        "recommended_next_action",
        "reply_subject",
        "reply_body",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You support a Japanese distribution company handling replies from overseas "
    "makers. Read the incoming email, analyze it precisely, and draft a natural "
    "English reply. Never invent facts. Do NOT add a signature or contact block — "
    "the signature is appended separately by the system. Output must follow the "
    "given JSON schema exactly. japanese_summary must be written in Japanese."
)

TONE_HINTS = {
    ReplyTone.professional: "standard, polite and professional",
    ReplyTone.friendly: "warm and friendly while professional",
    ReplyTone.concise: "concise and to the point",
    ReplyTone.detailed: "thorough and detailed",
    ReplyTone.executive: "high-level and brief, for a busy executive",
}

REPLY_GUIDELINES = (
    "The reply_body must naturally: thank them for their reply; directly address "
    "their questions/concerns; reaffirm interest in launching in Japan via Makuake / "
    "GreenFunding; raise exclusive Japanese distribution where appropriate (skip if "
    "they declined); propose a concrete next action; and offer a short online meeting. "
    "Do not include any signature."
)


class ClaudeReplyAssistant(ReplyAssistant):
    name = "claude-reply"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(
        self, project: Project, incoming: IncomingEmail, tone: ReplyTone
    ) -> str:
        return "\n".join(
            [
                "Analyze the incoming reply and draft a response.",
                f"Reply tone: {TONE_HINTS.get(tone, TONE_HINTS[DEFAULT_REPLY_TONE])}.",
                "",
                REPLY_GUIDELINES,
                "",
                "# Our product / project",
                f"Product: {project.title}",
                f"Maker: {project.maker_name or '(unknown)'}",
                f"Category: {project.category or ''}",
                "",
                "# Incoming email",
                f"From: {incoming.sender or '(unknown)'}",
                f"Subject: {incoming.subject or '(none)'}",
                "Body:",
                incoming.body or "",
                "",
                "Return JSON with the required keys. reply_subject should normally be "
                "'Re: <original subject>'. Do not include a signature in reply_body.",
            ]
        )

    def assist(
        self,
        project: Project,
        incoming: IncomingEmail,
        tone: ReplyTone = DEFAULT_REPLY_TONE,
    ) -> ReplyAssistResult:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": self._build_prompt(project, incoming, tone)}
            ],
            output_config={"format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
        )
        self.last_usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise ValueError("Claude 応答に JSON テキストが含まれていません")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"返信案の JSON 解析に失敗しました: {exc} / 応答抜粋: {text[:500]}"
            )

        def _list(key: str) -> list[str]:
            v = data.get(key)
            return [str(x) for x in v if x] if isinstance(v, list) else []

        intent = data.get("intent")
        if intent not in INTENTS:
            intent = "unclear"
        sentiment = data.get("sentiment")
        if sentiment not in SENTIMENTS:
            sentiment = "neutral"

        return ReplyAssistResult(
            detected_language=data.get("detected_language", "en"),
            japanese_summary=data.get("japanese_summary", ""),
            intent=intent,
            sentiment=sentiment,
            key_points=_list("key_points"),
            requested_actions=_list("requested_actions"),
            risks_or_cautions=_list("risks_or_cautions"),
            recommended_next_action=data.get("recommended_next_action", ""),
            reply_subject=data.get("reply_subject", ""),
            reply_body=data.get("reply_body", ""),
            model=self.name,
        )
