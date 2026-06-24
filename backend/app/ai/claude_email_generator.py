"""Claude 営業メール生成器。

ANTHROPIC_API_KEY が設定されると get_email_generator() がこれを使う。
公式 anthropic SDK で種別ごとに Messages API を呼び、構造化出力（JSON）で
subject/body を受け取って EmailDraftResult にマップする。自動送信はしない。

失敗（API エラー / JSON 不正 等）は呼び出し側で捕捉・記録され、
アプリ全体は落とさない。
"""
from __future__ import annotations

import json
import logging

from app.ai.email_generator import (
    EMAIL_TYPE_LABELS,
    EMAIL_TYPES,
    EmailDraftResult,
    EmailGenerator,
)
from app.config import settings
from app.models.email_draft import EmailType
from app.models.project import Project

logger = logging.getLogger("ai.claude_email_generator")

SYSTEM_PROMPT = (
    "You are a Japanese crowdfunding distributor (Makuake / GreenFunding) "
    "reaching out to overseas product makers. Write concise, professional sales "
    "emails in English. Never invent facts about the product. Output must follow "
    "the given JSON schema exactly."
)

EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}

# 種別ごとの狙い（プロンプトに含める）
_TYPE_INTENT: dict[EmailType, str] = {
    EmailType.initial_outreach: (
        "First-contact outreach: introduce yourself, express genuine interest in "
        "the product, and propose a short call about a Japan launch."
    ),
    EmailType.exclusive_rights: (
        "Propose an exclusive Japan distribution partnership, briefly noting the "
        "marketing/operational commitment exclusivity enables."
    ),
    EmailType.followup: (
        "Polite, brief follow-up to a previous unanswered message; lower the bar "
        "to a quick reply."
    ),
}


class ClaudeEmailGenerator(EmailGenerator):
    name = "claude-email"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, project: Project, email_type: EmailType) -> str:
        label = EMAIL_TYPE_LABELS[email_type]
        intent = _TYPE_INTENT[email_type]
        return (
            f"Write a sales email of type '{label}'. Goal: {intent}\n"
            f"Sign the email as {settings.sender_name} from {settings.sender_company}.\n"
            f"Return JSON with keys: subject, body.\n\n"
            f"# Product\n"
            f"Title: {project.title}\n"
            f"Maker: {project.maker_name}\n"
            f"Category: {project.category}\n"
            f"Source platform: {project.source_site}\n"
            f"Description: {project.description}\n"
        )

    def _generate_one(
        self, project: Project, email_type: EmailType
    ) -> tuple[EmailDraftResult, object]:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": self._build_prompt(project, email_type)}
            ],
            output_config={"format": {"type": "json_schema", "schema": EMAIL_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise ValueError("Claude 応答に JSON テキストが含まれていません")
        data = json.loads(text)
        result = EmailDraftResult(
            email_type=email_type,
            subject=data["subject"],
            body=data["body"],
            language="en",
            model=self.name,
        )
        return result, resp.usage

    def generate(self, project: Project) -> list[EmailDraftResult]:
        results: list[EmailDraftResult] = []
        in_tokens = out_tokens = 0
        for t in EMAIL_TYPES:
            result, usage = self._generate_one(project, t)
            in_tokens += usage.input_tokens
            out_tokens += usage.output_tokens
            results.append(result)
        # 3 種別合計のトークン使用量
        self.last_usage = {"input_tokens": in_tokens, "output_tokens": out_tokens}
        return results
