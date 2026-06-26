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
from app.ai.prompts import (
    SYSTEM_PROMPT,
    SenderContext,
    append_signature,
    build_email_prompt,
)
from app.models.email_draft import EmailType
from app.models.project import Project

logger = logging.getLogger("ai.claude_email_generator")

EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}


class ClaudeEmailGenerator(EmailGenerator):
    name = "claude-email"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _generate_one(
        self, project: Project, email_type: EmailType, ctx: SenderContext
    ) -> tuple[EmailDraftResult, object]:
        prompt = build_email_prompt(
            project, email_type, ctx, EMAIL_TYPE_LABELS[email_type]
        )
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": EMAIL_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise ValueError("Claude 応答に JSON テキストが含まれていません")
        data = json.loads(text)
        # 署名は AI に生成させず、保存済みテンプレートを末尾へ固定連結する
        result = EmailDraftResult(
            email_type=email_type,
            subject=data["subject"],
            body=append_signature(data["body"], ctx),
            language="en",
            model=self.name,
        )
        return result, resp.usage

    def generate(
        self, project: Project, ctx: SenderContext | None = None
    ) -> list[EmailDraftResult]:
        ctx = ctx or SenderContext.fallback()
        results: list[EmailDraftResult] = []
        in_tokens = out_tokens = 0
        for t in EMAIL_TYPES:
            result, usage = self._generate_one(project, t, ctx)
            in_tokens += usage.input_tokens
            out_tokens += usage.output_tokens
            results.append(result)
        # 3 種別合計のトークン使用量
        self.last_usage = {"input_tokens": in_tokens, "output_tokens": out_tokens}
        return results
