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
from app.ai.personalization import build_personalization
from app.ai.prompts import (
    DEFAULT_TONE,
    SYSTEM_PROMPT,
    EmailTone,
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
        # 件名候補は 3 案
        "subject_options": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "body": {"type": "string"},
        # 送信前確認用の日本語要約
        "japanese_summary": {"type": "string"},
    },
    "required": ["subject_options", "body", "japanese_summary"],
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
        self,
        project: Project,
        email_type: EmailType,
        ctx: SenderContext,
        tone: EmailTone,
        personalization: dict,
        research: dict | None,
        japan_sales: dict | None,
    ) -> tuple[EmailDraftResult, object]:
        prompt = build_email_prompt(
            project,
            email_type,
            ctx,
            EMAIL_TYPE_LABELS[email_type],
            tone=tone,
            personalization=personalization,
            research=research,
            japan_sales=japan_sales,
        )
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": EMAIL_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise ValueError("Claude 応答に JSON テキストが含まれていません")
        data = json.loads(text)
        options = [s for s in data.get("subject_options", []) if s]
        if not options:
            raise ValueError("Claude 応答に件名候補が含まれていません")
        # 件名候補に商品名が 1 つも含まれなければ、商品名入りの候補を補う（要件 8）
        title = project.title or ""
        if title and not any(title in s for s in options):
            options[-1] = f"{title} — {options[-1]}"
        # 署名は AI に生成させず、保存済みテンプレートを末尾へ固定連結する
        result = EmailDraftResult(
            email_type=email_type,
            subject=options[0],
            subject_options=options,
            selected_subject=options[0],
            body=append_signature(data["body"], ctx),
            language="en",
            tone=tone.value,
            japanese_summary=data.get("japanese_summary", ""),
            personalization_context=personalization,
            personalized_compliment=personalization.get("personalized_compliment", ""),
            product_highlights=personalization.get("product_highlights", []),
            model=self.name,
        )
        return result, resp.usage

    def generate(
        self,
        project: Project,
        ctx: SenderContext | None = None,
        tone: EmailTone = DEFAULT_TONE,
        research: dict | None = None,
        japan_sales: dict | None = None,
    ) -> list[EmailDraftResult]:
        ctx = ctx or SenderContext.fallback()
        # 商品・メーカーごとの個別化材料を先に作る（全種別で共有）
        personalization = build_personalization(project)
        # 企業リサーチの称賛があれば個別化材料へ反映（保存値にも残る）
        if research and research.get("personalized_compliment"):
            personalization["personalized_compliment"] = research[
                "personalized_compliment"
            ]
        results: list[EmailDraftResult] = []
        in_tokens = out_tokens = 0
        for t in EMAIL_TYPES:
            result, usage = self._generate_one(
                project, t, ctx, tone, personalization, research, japan_sales
            )
            in_tokens += usage.input_tokens
            out_tokens += usage.output_tokens
            results.append(result)
        # 3 種別合計のトークン使用量
        self.last_usage = {"input_tokens": in_tokens, "output_tokens": out_tokens}
        return results
