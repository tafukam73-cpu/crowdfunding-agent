"""Claude AI 連絡先リサーチャー。

ANTHROPIC_API_KEY 設定時に get_contact_researcher() がこれを使う。
公式サイト・問い合わせページの本文をベストエフォートで取得し、案件情報・企業
リサーチ・既存探索結果とあわせて Claude へ渡し、構造化出力（JSON）で連絡先候補を
受け取る。

最重要ルール：メールアドレスを推測で捏造させない。プロンプトで強く禁止し、出典
（source_url）必須を要求する。さらに service 側で既存の email_exclusion_reason と
出典必須チェックで再検証するため、万一捏造されても営業候補には残らない。

JSON パース失敗時は ValueError を送出し、呼び出し側（service）が失敗として扱う。
"""
from __future__ import annotations

import json
import logging

from app.ai.contact_researcher import (
    AiCandidateEmail,
    ContactResearchContext,
    ContactResearcher,
    ContactResearchResult,
)

logger = logging.getLogger("ai.claude_contact_researcher")

PAGE_TEXT_MAX = 4000

_CHANNEL_ENUM = [
    "email",
    "contact_form",
    "linkedin",
    "instagram",
    "facebook",
    "press",
    "distributor_page",
    "manual_research",
]
_CONFIDENCE_ENUM = ["high", "medium", "low"]

CONTACT_RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_email": {"type": ["string", "null"]},
        "candidate_emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "score": {"type": "integer"},
                    "confidence": {"type": "string", "enum": _CONFIDENCE_ENUM},
                    "reason": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["email", "score", "confidence", "reason", "source_url"],
                "additionalProperties": False,
            },
        },
        "contact_form_url": {"type": ["string", "null"]},
        "instagram_url": {"type": ["string", "null"]},
        "facebook_url": {"type": ["string", "null"]},
        "linkedin_url": {"type": ["string", "null"]},
        "recommended_channel": {"type": "string", "enum": _CHANNEL_ENUM},
        "confidence_score": {"type": "integer"},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "type": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["url", "type", "note"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": [
        "primary_email",
        "candidate_emails",
        "contact_form_url",
        "instagram_url",
        "facebook_url",
        "linkedin_url",
        "recommended_channel",
        "confidence_score",
        "search_queries",
        "sources",
        "notes",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You research sales contact information for a Japanese distribution company that "
    "reaches out to overseas crowdfunding makers. Given a project, its maker, company "
    "research, and the results of an automated contact crawl, organize the best ways to "
    "contact the maker for a business/partnership inquiry.\n\n"
    "ABSOLUTE RULES — never break these:\n"
    "1. NEVER invent or guess an email address. Do NOT output 'info@<domain>', "
    "'sales@<domain>', etc. unless that exact address actually appears in the provided "
    "data (existing candidate emails or page text). Every candidate email MUST include a "
    "real source_url where it was seen. If you have no verifiable email, return an empty "
    "candidate_emails list and null primary_email.\n"
    "2. NEVER include crowdfunding platform operator emails (kickstarter.com, "
    "indiegogo.com, ulule.com, makuake.com, greenfunding.jp, wadiz.kr) or monitoring/"
    "no-reply/postmaster/sentry addresses.\n"
    "3. Do NOT re-propose any email listed under 'Already excluded emails'.\n"
    "When no email is verifiable, recommend the best alternative channel (official "
    "contact form, LinkedIn, Instagram, Facebook, press, distributor page) and provide "
    "useful Google search queries the salesperson can run manually. It is good and "
    "expected to return no email but a clear recommended channel. Output must follow the "
    "given JSON schema exactly."
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
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:PAGE_TEXT_MAX]
    except Exception as exc:  # noqa: BLE001  取得失敗は無視して推論にフォールバック
        logger.info("page fetch failed (%s): %s", url, exc)
        return ""


class ClaudeContactResearcher(ContactResearcher):
    name = "claude-contact-research"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, ctx: ContactResearchContext, page_text: str) -> str:
        existing = "\n".join(
            f"  - {e.get('email')} (tier={e.get('tier')}, "
            f"sources={', '.join(e.get('sources') or []) or 'none'})"
            for e in ctx.existing_candidate_emails
        ) or "  (none found by the automated crawl)"
        excluded = "\n".join(
            f"  - {e.get('email')} ({e.get('reason')})" for e in ctx.excluded_emails
        ) or "  (none)"
        socials = "\n".join(
            f"  - {k}: {v}" for k, v in (ctx.discovered_socials or {}).items()
        ) or "  (none found)"
        searched = "\n".join(f"  - {u}" for u in ctx.searched_urls[:20]) or "  (none)"

        lines = [
            "Research how to contact the maker below for a Japan-market partnership.",
            "",
            "# Project",
            f"Title: {ctx.title}",
            f"Maker / brand: {ctx.maker_name or '(unknown)'}",
            f"Source platform: {ctx.source_site or ''}",
            f"Project URL: {ctx.source_url or ''}",
            f"Official site: {ctx.official_site_url or '(unknown)'}",
            f"Description: {ctx.description_clean or ''}",
            "",
            "# Automated crawl results (already filtered for spam/platform emails)",
            "Existing candidate emails (use these as verified, they have sources):",
            existing,
            "Contact form found: "
            + (ctx.primary_contact_form_url or "(none)"),
            "Social profiles found:",
            socials,
            "Already searched URLs:",
            searched,
            "",
            "# Already excluded emails (DO NOT propose these again)",
            excluded,
        ]
        if ctx.platform_domain:
            lines.append("")
            lines.append(
                f"# Platform domain to always exclude: {ctx.platform_domain}"
            )
        if ctx.company_sources:
            lines.append("")
            lines.append("# Company research sources")
            lines += [f"  - {s}" for s in ctx.company_sources[:10]]
        if page_text:
            lines += [
                "",
                "# Official/contact page text (excerpt, noisy — extract only reliable "
                "emails with their URL as source_url)",
                page_text,
            ]
        lines += [
            "",
            "Return: primary_email (an email ONLY if verifiable with a source, else "
            "null), candidate_emails (each with a real source_url; empty if none), "
            "contact_form_url, instagram_url, facebook_url, linkedin_url, "
            "recommended_channel, confidence_score (0-100), search_queries (3-6 useful "
            "Google queries), sources (the URLs/types you used), and notes (Japanese; "
            "explain the recommended next step, especially when no email was found).",
        ]
        return "\n".join(lines)

    def research(self, ctx: ContactResearchContext) -> ContactResearchResult:
        page_text = _fetch_text(ctx.primary_contact_form_url) or _fetch_text(
            ctx.official_site_url
        )
        prompt = self._build_prompt(ctx, page_text)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": CONTACT_RESEARCH_SCHEMA}
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
                f"AI 連絡先リサーチの JSON 解析に失敗しました: {exc} / 応答抜粋: {snippet}"
            )

        candidates: list[AiCandidateEmail] = []
        for c in data.get("candidate_emails") or []:
            if not isinstance(c, dict):
                continue
            email = str(c.get("email", "")).strip()
            if not email:
                continue
            candidates.append(
                AiCandidateEmail(
                    email=email,
                    score=int(c.get("score", 0) or 0),
                    confidence=str(c.get("confidence", "")),
                    reason=str(c.get("reason", "")),
                    source_url=str(c.get("source_url", "")),
                )
            )

        def _str_list(key: str) -> list[str]:
            v = data.get(key)
            return [str(x) for x in v if x] if isinstance(v, list) else []

        sources = [
            {
                "url": str(s.get("url", "")),
                "type": str(s.get("type", "")),
                "note": str(s.get("note", "")),
            }
            for s in (data.get("sources") or [])
            if isinstance(s, dict) and s.get("url")
        ]

        return ContactResearchResult(
            primary_email=data.get("primary_email") or None,
            candidate_emails=candidates,
            contact_form_url=data.get("contact_form_url") or None,
            instagram_url=data.get("instagram_url") or None,
            facebook_url=data.get("facebook_url") or None,
            linkedin_url=data.get("linkedin_url") or None,
            recommended_channel=str(data.get("recommended_channel", "")),
            confidence_score=int(data.get("confidence_score", 0) or 0),
            search_queries=_str_list("search_queries"),
            sources=sources,
            notes=str(data.get("notes", "")),
            model=self.name,
        )
