"""Claude AI Document Reader。

ANTHROPIC_API_KEY 設定時に get_document_reader() がこれを使う。service が集めた
重要ページの本文・リンク・抽出済みメール/SNS・検索スニペットを Claude へ渡し、
構造化出力（JSON schema）で会社名・公式サイト・メール・SNS・フォーム・担当者候補を
受け取る。

最重要ルール：メール・人名を推測で捏造させない。プロンプトで強く禁止し、出典
（source_url）必須を要求する。さらに service 側で email_exclusion_reason /
platform 除外 / 出典必須で再検証するため、万一捏造されても営業候補には残らない。

JSON パース失敗時は ValueError を送出し、呼び出し側（service）が失敗として扱う。
"""
from __future__ import annotations

import json
import logging

from app.ai.document_reader import (
    DocReaderEmail,
    DocReaderPerson,
    DocumentReader,
    DocumentReaderContext,
    DocumentReaderResult,
    TOTAL_TEXT_MAX,
)

logger = logging.getLogger("ai.claude_document_reader")

_CHANNEL_ENUM = [
    "email", "contact_form", "linkedin", "instagram", "facebook",
    "youtube", "tiktok", "press", "distributor_page", "manual_search",
]

DOC_READER_SCHEMA = {
    "type": "object",
    "properties": {
        "official_company_name": {"type": ["string", "null"]},
        "brand_names": {"type": "array", "items": {"type": "string"}},
        "official_site_url": {"type": ["string", "null"]},
        "emails": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "purpose": {"type": "string"},
                    "confidence": {"type": "integer"},
                    "source_url": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["email", "purpose", "confidence", "source_url", "reason"],
                "additionalProperties": False,
            },
        },
        "contact_forms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "confidence": {"type": "integer"},
                    "source_url": {"type": "string"},
                },
                "required": ["url", "confidence", "source_url"],
                "additionalProperties": False,
            },
        },
        "socials": {
            "type": "object",
            "properties": {
                "instagram": {"type": ["string", "null"]},
                "facebook": {"type": ["string", "null"]},
                "linkedin": {"type": ["string", "null"]},
                "youtube": {"type": ["string", "null"]},
                "tiktok": {"type": ["string", "null"]},
                "x": {"type": ["string", "null"]},
            },
            "required": ["instagram", "facebook", "linkedin", "youtube", "tiktok", "x"],
            "additionalProperties": False,
        },
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "linkedin_url": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "confidence": {"type": "integer"},
                    "source_url": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "title", "linkedin_url", "email",
                             "confidence", "source_url", "reason"],
                "additionalProperties": False,
            },
        },
        "recommended_channel": {"type": "string", "enum": _CHANNEL_ENUM},
        "recommended_contact": {"type": ["string", "null"]},
        "confidence_score": {"type": "integer"},
        "evidence_summary": {"type": "string"},
        "missing_info": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "official_company_name", "brand_names", "official_site_url", "emails",
        "contact_forms", "socials", "people", "recommended_channel",
        "recommended_contact", "confidence_score", "evidence_summary", "missing_info",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a meticulous B2B research analyst for a Japanese distribution company "
    "that reaches out to overseas crowdfunding makers. You are given the full text and "
    "links of several already-fetched web pages (crowdfunding page, creator profile, "
    "official site, contact/about/team pages, PDFs, and search snippets). Read them "
    "holistically and organize how to contact the maker for a partnership.\n\n"
    "ABSOLUTE RULES — never break these:\n"
    "1. NEVER invent, guess, or complete an email address or a person's name. Only "
    "output an email or person that literally appears in the provided page text/links/"
    "snippets. Every email and person MUST include the source_url where it appears. If "
    "you cannot verify an email, return an empty emails list and null recommended_contact.\n"
    "2. NEVER output crowdfunding platform operator addresses (kickstarter.com, "
    "indiegogo.com, ulule.com, makuake.com, camp-fire.jp, greenfunding.jp, readyfor.jp, "
    "wadiz.kr) or monitoring/no-reply/postmaster/sentry addresses.\n"
    "3. Only output social URLs that appear in the provided data. Do not fabricate "
    "handles. Use the maker's own accounts, not the platform's official accounts.\n"
    "4. official_site_url must be the maker's own company/brand domain, never a "
    "crowdfunding/platform URL.\n"
    "It is good and expected to return no email but a recommended alternative channel "
    "(contact form, LinkedIn, Instagram, Facebook). Fill missing_info with what a "
    "salesperson still needs. Output must follow the given JSON schema exactly."
)


class ClaudeDocumentReader(DocumentReader):
    name = "claude-document-reader"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, ctx: DocumentReaderContext) -> str:
        lines = [
            "Analyze the maker below for a Japan-market partnership outreach.",
            "",
            "# Project",
            f"Title: {ctx.title}",
            f"Maker / brand: {ctx.maker_name or '(unknown)'}",
            f"Source platform: {ctx.source_site or ''}",
            f"Project URL: {ctx.source_url or ''}",
            f"Creator/Maker profile URL: {ctx.maker_url or ''}",
            f"Known official site: {ctx.official_site_url or '(unknown)'}",
            f"Description: {ctx.description_clean or ''}",
        ]
        if ctx.existing_emails:
            lines += ["", "# Emails already extracted automatically (verified, reuse):"]
            lines += [f"  - {e}" for e in ctx.existing_emails[:20]]
        if ctx.existing_socials:
            lines += ["", "# Socials already found:"]
            lines += [f"  - {k}: {v}" for k, v in ctx.existing_socials.items()]
        if ctx.search_snippets:
            lines += ["", "# Search result snippets"]
            for s in ctx.search_snippets[:15]:
                lines.append(
                    f"  - [{s.get('query','')}] {s.get('title','')} "
                    f"{s.get('url','')} :: {s.get('snippet','')}"
                )
        # ページ本文（合計上限を守りつつ添付）
        total = 0
        lines += ["", "# Fetched pages (read holistically; extract only verifiable data)"]
        for pg in ctx.pages:
            if total >= TOTAL_TEXT_MAX:
                break
            body = (pg.text or "")[: max(0, TOTAL_TEXT_MAX - total)]
            total += len(body)
            lines += [
                "",
                f"## PAGE type={pg.page_type or 'page'} url={pg.url}",
                f"title: {pg.title}",
            ]
            if pg.emails:
                lines.append("emails on page: " + ", ".join(pg.emails[:10]))
            if pg.socials:
                lines.append(
                    "socials on page: "
                    + ", ".join(f"{k}={v}" for k, v in pg.socials.items())
                )
            if pg.links:
                lines.append("links: " + " | ".join(pg.links[:25]))
            lines += ["text:", body]
        for i, ptxt in enumerate(ctx.pdf_texts[:3]):
            if total >= TOTAL_TEXT_MAX:
                break
            body = ptxt[: max(0, TOTAL_TEXT_MAX - total)]
            total += len(body)
            lines += ["", f"## PDF {i + 1}", body]
        if ctx.platform_domain:
            lines += ["", f"# Always exclude platform domain: {ctx.platform_domain}"]
        lines += [
            "",
            "Return the JSON. Emails and people MUST have a source_url and appear in the "
            "provided text/links. If none are verifiable, return empty lists. Set "
            "confidence_score 0-100. evidence_summary and missing_info in Japanese.",
        ]
        return "\n".join(lines)

    def read(self, ctx: DocumentReaderContext) -> DocumentReaderResult:
        prompt = self._build_prompt(ctx)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": DOC_READER_SCHEMA}
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
            raise ValueError(
                f"AI Document Reader の JSON 解析に失敗しました: {exc} / "
                f"応答抜粋: {text[:500]}"
            )

        emails = [
            DocReaderEmail(
                email=str(c.get("email", "")).strip(),
                purpose=str(c.get("purpose", "")),
                confidence=int(c.get("confidence", 0) or 0),
                source_url=str(c.get("source_url", "")),
                reason=str(c.get("reason", "")),
            )
            for c in (data.get("emails") or [])
            if isinstance(c, dict) and str(c.get("email", "")).strip()
        ]
        people = [
            DocReaderPerson(
                name=str(p.get("name", "")).strip(),
                title=str(p.get("title", "")),
                linkedin_url=p.get("linkedin_url") or None,
                email=p.get("email") or None,
                confidence=int(p.get("confidence", 0) or 0),
                source_url=str(p.get("source_url", "")),
                reason=str(p.get("reason", "")),
            )
            for p in (data.get("people") or [])
            if isinstance(p, dict) and str(p.get("name", "")).strip()
        ]
        forms = [
            {
                "url": str(f.get("url", "")),
                "confidence": int(f.get("confidence", 0) or 0),
                "source_url": str(f.get("source_url", "")),
            }
            for f in (data.get("contact_forms") or [])
            if isinstance(f, dict) and f.get("url")
        ]
        socials_raw = data.get("socials") or {}
        socials = {
            k: (socials_raw.get(k) or None)
            for k in ("instagram", "facebook", "linkedin", "youtube", "tiktok", "x")
        }
        return DocumentReaderResult(
            official_company_name=data.get("official_company_name") or None,
            brand_names=[str(b) for b in (data.get("brand_names") or []) if b],
            official_site_url=data.get("official_site_url") or None,
            emails=emails,
            contact_forms=forms,
            socials=socials,
            people=people,
            recommended_channel=str(data.get("recommended_channel", "")),
            recommended_contact=data.get("recommended_contact") or None,
            confidence_score=int(data.get("confidence_score", 0) or 0),
            evidence_summary=str(data.get("evidence_summary", "")),
            missing_info=[str(m) for m in (data.get("missing_info") or []) if m],
            sources=[
                {"url": p.url, "type": p.page_type or "page", "note": p.title or ""}
                for p in ctx.pages
            ],
            model=self.name,
        )
