"""Claude 日本販売状況チェッカー。

ANTHROPIC_API_KEY 設定時に get_japan_sales_checker() がこれを使う。
案件情報（商品名・メーカー名・ブランド名・説明）をもとに、各チャネル
（Amazon.co.jp / 楽天 / Yahoo! ショッピング / 日本代理店 / 日本法人 / Makuake /
GREEN FUNDING）で日本販売・掲載があるかを Claude の知識で評価し、構造化出力
（JSON）で受け取る。

注意：本チェッカーは Web 検索を行わず Claude の知識に基づく推定を返す。確証が無い
場合は not_found ではなく unknown を返すよう促し、最終確認は各チャネルの検索 URL
（service が決定的に付与）から手動で行う前提。JSON パース失敗時は ValueError を
送出し、呼び出し側（service）が status=failed として保存・表示する。
"""
from __future__ import annotations

import json
import logging

from app.ai.japan_sales_checker import (
    CHANNEL_KEYS,
    STATUS_UNKNOWN,
    VALID_STATUSES,
    JapanSalesChecker,
    JapanSalesResult,
)
from app.models.project import Project

logger = logging.getLogger("ai.claude_japan_sales_checker")

PAGE_TEXT_MAX = 3000

_STATUS_ENUM = ["found", "limited", "not_found", "unknown"]

JAPAN_SALES_SCHEMA = {
    "type": "object",
    "properties": {
        "channels": {
            "type": "object",
            "properties": {
                k: {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": _STATUS_ENUM},
                        "note": {"type": "string"},
                    },
                    "required": ["status", "note"],
                    "additionalProperties": False,
                }
                for k in CHANNEL_KEYS
            },
            "required": list(CHANNEL_KEYS),
            "additionalProperties": False,
        },
        "ai_comment": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["channels", "ai_comment", "summary"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You support a Japanese distribution company that finds overseas crowdfunding "
    "products NOT yet sold in Japan. Assess whether the given product/brand already "
    "has a Japanese presence on each channel. Base your assessment only on your "
    "knowledge of the product and brand; you cannot browse the web. When you are not "
    "reasonably confident a product is sold/listed in Japan, use 'unknown' rather than "
    "guessing 'found'. Prefer 'not_found' only when you have genuine reason to believe "
    "there is no Japanese presence. The ai_comment and summary MUST be written in "
    "Japanese. Output must follow the given JSON schema exactly."
)

_CHANNEL_DESCRIPTIONS = (
    "amazon: sold on Amazon.co.jp; rakuten: sold on Rakuten Ichiba (楽天市場); "
    "yahoo: sold on Yahoo! Shopping; distributor: has a Japanese distributor/reseller; "
    "subsidiary: has a Japanese subsidiary/legal entity (日本法人); "
    "makuake: has run a Makuake crowdfunding campaign; "
    "greenfunding: has run a GREEN FUNDING crowdfunding campaign."
)


def _fetch_text(url: str | None) -> str:
    if not url:
        return ""
    try:
        import re

        import httpx

        resp = httpx.get(url, timeout=8.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:PAGE_TEXT_MAX]
    except Exception as exc:  # noqa: BLE001  取得失敗は無視して推論にフォールバック
        logger.info("page fetch failed (%s): %s", url, exc)
        return ""


class ClaudeJapanSalesChecker(JapanSalesChecker):
    name = "claude-japan-sales"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, project: Project, page_text: str) -> str:
        lines = [
            "Assess the Japanese market presence of the product/brand below.",
            "",
            "# Product",
            f"Product name: {project.title}",
            f"Maker / brand: {project.maker_name or '(unknown)'}",
            f"Category: {project.category or ''}",
            f"Source platform: {project.source_site or ''}",
            f"Official site: {project.maker_url or ''}",
            f"Description: {project.description or ''}",
            "",
            "# Channels to assess (status one of found/limited/not_found/unknown)",
            _CHANNEL_DESCRIPTIONS,
        ]
        if page_text:
            lines += [
                "",
                "# Page text (excerpt, may be noisy — use only what is reliable)",
                page_text,
            ]
        lines += [
            "",
            "For each channel return {status, note}. Keep each note short (Japanese, "
            "may be empty). Then write ai_comment (2-4 sentences, Japanese) explaining "
            "the sales opportunity in Japan, and a one-line Japanese summary. If nothing "
            "is found in Japan, say so and note the opportunity to become the first "
            "distributor (初の販売代理店になれる可能性).",
        ]
        return "\n".join(lines)

    def check(self, project: Project) -> JapanSalesResult:
        page_text = _fetch_text(project.maker_url) or _fetch_text(project.source_url)
        prompt = self._build_prompt(project, page_text)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": JAPAN_SALES_SCHEMA}
            },
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
            snippet = text[:500]
            raise ValueError(
                f"日本販売状況の JSON 解析に失敗しました: {exc} / 応答抜粋: {snippet}"
            )

        channels = data.get("channels") or {}
        statuses: dict[str, str] = {}
        notes: dict[str, str] = {}
        for key in CHANNEL_KEYS:
            entry = channels.get(key) or {}
            status = str(entry.get("status", STATUS_UNKNOWN))
            if status not in VALID_STATUSES:
                status = STATUS_UNKNOWN
            statuses[key] = status
            note = entry.get("note")
            if note:
                notes[key] = str(note)

        return JapanSalesResult(
            channel_statuses=statuses,
            channel_notes=notes,
            ai_comment=str(data.get("ai_comment", "")),
            summary=str(data.get("summary", "")),
            model=self.name,
        )
