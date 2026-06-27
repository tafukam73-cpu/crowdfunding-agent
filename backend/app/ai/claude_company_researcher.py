"""Claude 企業リサーチャー。

ANTHROPIC_API_KEY 設定時に get_company_researcher() がこれを使う。
project_url / official_site_url のページ本文をベストエフォートで取得し、案件情報と
あわせて Claude へ渡し、構造化出力（JSON）でリサーチ結果を受け取る。

ページ取得に失敗しても案件情報のみで推論する。JSON パース失敗時は ValueError を
送出し、呼び出し側（service）が research_status=failed として保存・表示する。
"""
from __future__ import annotations

import json
import logging

from app.ai.company_researcher import CompanyResearcher, ResearchResult
from app.models.project import Project

logger = logging.getLogger("ai.claude_company_researcher")

# 取得するページ本文の最大文字数（プロンプト肥大化を防ぐ）
PAGE_TEXT_MAX = 4000

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "brand_summary": {"type": "string"},
        "company_mission": {"type": "string"},
        "product_summary": {"type": "string"},
        "key_product_features": {"type": "array", "items": {"type": "string"}},
        "brand_strengths": {"type": "array", "items": {"type": "string"}},
        "differentiation_points": {"type": "array", "items": {"type": "string"}},
        "japan_market_fit": {"type": "string"},
        "personalized_compliment": {"type": "string"},
        "outreach_angles": {"type": "array", "items": {"type": "string"}},
        "risks_or_cautions": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "brand_summary",
        "company_mission",
        "product_summary",
        "key_product_features",
        "brand_strengths",
        "differentiation_points",
        "japan_market_fit",
        "personalized_compliment",
        "outreach_angles",
        "risks_or_cautions",
        "sources",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a company researcher supporting a Japanese distribution company. "
    "Given an overseas crowdfunding project and any available page text, produce a "
    "concise, factual research brief that will be used to write a highly personalized "
    "outreach email. Never invent facts that are not supported by the provided "
    "information; when unsure, keep statements general and add a caution. Output must "
    "follow the given JSON schema exactly."
)


def _fetch_text(url: str | None) -> str:
    """ページ本文をベストエフォートで取得する（失敗時は空文字）。"""
    if not url:
        return ""
    try:
        import re

        import httpx

        resp = httpx.get(url, timeout=8.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        # スクリプト/スタイルを除去し、タグを落として粗くテキスト化
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:PAGE_TEXT_MAX]
    except Exception as exc:  # noqa: BLE001  取得失敗は無視して推論にフォールバック
        logger.info("page fetch failed (%s): %s", url, exc)
        return ""


class ClaudeCompanyResearcher(CompanyResearcher):
    name = "claude-research"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, project: Project, page_text: str) -> str:
        lines = [
            "Research the maker and product below for a Japan-market outreach email.",
            "",
            "# Project",
            f"Product name: {project.title}",
            f"Maker: {project.maker_name or '(unknown)'}",
            f"Category: {project.category or ''}",
            f"Source platform: {project.source_site or ''}",
            f"Project URL: {project.source_url or ''}",
            f"Official site: {project.maker_url or ''}",
            f"Funding: {project.currency or ''} {project.raised_amount or ''} "
            f"(goal {project.goal_amount or ''})",
            f"Backers: {project.backers_count or ''}",
            f"Description: {project.description or ''}",
        ]
        if page_text:
            lines += [
                "",
                "# Page text (excerpt, may be noisy — extract only what is reliable)",
                page_text,
            ]
        lines += [
            "",
            "Produce: brand_summary, company_mission, product_summary, "
            "key_product_features, brand_strengths, differentiation_points, "
            "japan_market_fit (why it can work in Japan), personalized_compliment "
            "(one specific, genuine compliment), outreach_angles (points the sales "
            "email should make), risks_or_cautions (phrasings or assumptions to avoid), "
            "and sources (URLs actually used; may be empty).",
        ]
        return "\n".join(lines)

    def research(self, project: Project) -> ResearchResult:
        page_text = _fetch_text(project.source_url) or _fetch_text(project.maker_url)
        prompt = self._build_prompt(project, page_text)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": RESEARCH_SCHEMA}
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
            # パース失敗は呼び出し側が failed として保存・表示する
            snippet = text[:500]
            raise ValueError(f"リサーチ結果の JSON 解析に失敗しました: {exc} / 応答抜粋: {snippet}")

        def _list(key: str) -> list[str]:
            v = data.get(key)
            return [str(x) for x in v if x] if isinstance(v, list) else []

        return ResearchResult(
            maker_name=project.maker_name or "",
            official_site_url=project.maker_url or "",
            project_url=project.source_url or "",
            brand_summary=data.get("brand_summary", ""),
            company_mission=data.get("company_mission", ""),
            product_summary=data.get("product_summary", ""),
            key_product_features=_list("key_product_features"),
            brand_strengths=_list("brand_strengths"),
            differentiation_points=_list("differentiation_points"),
            japan_market_fit=data.get("japan_market_fit", ""),
            personalized_compliment=data.get("personalized_compliment", ""),
            outreach_angles=_list("outreach_angles"),
            risks_or_cautions=_list("risks_or_cautions"),
            sources=_list("sources") or [u for u in (project.source_url, project.maker_url) if u],
            raw_notes="" if page_text else "No external page text was available; inferred from campaign data.",
            model=self.name,
        )
