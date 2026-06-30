"""Claude Contact Hunter（出典必須・人名捏造禁止）。

ANTHROPIC_API_KEY 設定時に get_contact_hunter() がこれを使う。公式サイトの Team /
About / Leadership / People / Contact 等を取得し、そのページ本文を Claude に渡して
「ページ上に明示されている担当者」だけを構造化抽出する。

安全策（多重）：
- プロンプトで人名の捏造を厳禁し、source_url は渡した URL のいずれかであることを要求。
- 受領後、source_url が取得済み URL に無い人物・人名らしくない人物は破棄。
- 役職→部署/優先度は決定的関数で再計算（Claude の優先度は信用しない）。
- メールは service 側で既存除外フィルタを通す。

JSON パース失敗時は ValueError を送出（service が graceful に扱う）。
"""
from __future__ import annotations

import json
import logging
import re

from app.ai.contact_hunter import (
    ContactHunter,
    ContactHuntResult,
    PersonResult,
    compute_confidence,
    looks_like_person_name,
    title_to_priority,
)
from app.ai.mock_contact_hunter import _candidate_urls, build_people_search_queries
from app.services import contact_discovery_service as cds
from app.services import web_research_service as wrs

logger = logging.getLogger("ai.claude_contact_hunter")

PAGE_TEXT_MAX = 3000
MAX_PAGES = 8
MAX_QUERIES = 8
MAX_RESULTS_PER_QUERY = 4

CONTACT_HUNTER_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "linkedin_url": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "source_url": {"type": "string"},
                },
                "required": ["name", "title", "source_url"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["people", "notes"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You find the right sales contact PEOPLE (not just the company) for a Japanese "
    "distribution company reaching out to overseas makers. You are given the text of "
    "several pages from the maker's website.\n\n"
    "ABSOLUTE RULES:\n"
    "1. NEVER invent or guess a person's name. Only include a person if their name is "
    "explicitly written in one of the provided page texts.\n"
    "2. Every person MUST have a source_url that is EXACTLY one of the provided page "
    "URLs (the page where the name appears).\n"
    "3. Only include their title/email/linkedin if those are actually present in the "
    "provided text. Use null otherwise. Do NOT fabricate emails.\n"
    "4. Prioritize Business Development, Partnership, International Sales, Export, Sales, "
    "Marketing, Founder, CEO. It is fine to return an empty list if no person is named.\n"
    "Output must follow the given JSON schema exactly."
)


def _fetch_text(fetch, url: str) -> str:
    html = fetch(url)
    if not html:
        return ""
    no_sd = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", no_sd)
    return re.sub(r"\s+", " ", text).strip()[:PAGE_TEXT_MAX]


class ClaudeContactHunter(ContactHunter):
    name = "claude-contact-hunter"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _gather_pages(self, project, fetch, search, own_search) -> tuple[dict, list[str]]:
        official_domain = cds._domain_of(getattr(project, "maker_url", None))
        urls = list(_candidate_urls(project))
        searched_queries: list[str] = []
        try:
            for q in build_people_search_queries(project)[:MAX_QUERIES]:
                searched_queries.append(q)
                try:
                    results = search(q) or []
                except Exception:  # noqa: BLE001
                    results = []
                for u in results[:MAX_RESULTS_PER_QUERY]:
                    if (
                        official_domain
                        and cds._same_domain(u, official_domain)
                        and "linkedin.com/in/" not in u.lower()
                        and u not in urls
                    ):
                        urls.append(u)
        finally:
            if own_search:
                client = getattr(search, "_client", None)
                if client is not None:
                    client.close()

        pages: dict[str, str] = {}
        for url in urls[:MAX_PAGES]:
            text = _fetch_text(fetch, url)
            if text:
                pages[url] = text
        return pages, searched_queries

    def _build_prompt(self, project, pages: dict) -> str:
        lines = [
            "Find the sales/partnership contact people for this maker.",
            "",
            f"Maker: {getattr(project, 'maker_name', None) or '(unknown)'}",
            f"Official site: {getattr(project, 'maker_url', None) or ''}",
            "",
            "# Page texts (only use names explicitly written here; source_url must be "
            "one of these URLs)",
        ]
        for url, text in pages.items():
            lines += ["", f"## {url}", text]
        return "\n".join(lines)

    def hunt(
        self, project, *, fetch_fn=None, search_fn=None, research=None
    ) -> ContactHuntResult:
        own_fetcher = fetch_fn is None
        own_search = search_fn is None
        fetch = fetch_fn or wrs._make_fetcher()
        search = search_fn or wrs._default_search_fn()

        try:
            pages, searched_queries = self._gather_pages(
                project, fetch, search, own_search
            )
        finally:
            if own_fetcher:
                client = getattr(fetch, "_client", None)
                if client is not None:
                    client.close()

        if not pages:
            return ContactHuntResult(
                people=[],
                searched_queries=searched_queries,
                searched_urls=[],
                notes="取得できるチーム/会社ページがありませんでした。",
                model=self.name,
            )

        prompt = self._build_prompt(project, pages)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": CONTACT_HUNTER_SCHEMA}
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
            raise ValueError(f"Contact Hunter の JSON 解析に失敗しました: {exc}")

        valid_urls = set(pages.keys())
        people: list[PersonResult] = []
        seen: set[str] = set()
        for p in data.get("people") or []:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", "")).strip()
            source_url = str(p.get("source_url", "")).strip()
            # 捏造防止：人名らしさ + 出典が実際に渡したページであること
            if not looks_like_person_name(name) or source_url not in valid_urls:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            title = str(p.get("title", "")).strip() or None
            department, priority = title_to_priority(title)
            linkedin = p.get("linkedin_url") or None
            email = p.get("email") or None
            people.append(
                PersonResult(
                    name=name,
                    title=title,
                    department=department,
                    linkedin_url=linkedin,
                    email=email,
                    email_source="page" if email else None,
                    source_url=source_url,
                    confidence=compute_confidence(
                        has_name=True,
                        has_linkedin=bool(linkedin),
                        has_email=bool(email),
                        has_known_title=department not in (None, "Other"),
                    ),
                    priority=priority,
                )
            )
        people.sort(key=lambda x: (x.priority, x.confidence), reverse=True)
        return ContactHuntResult(
            people=people,
            searched_queries=searched_queries,
            searched_urls=list(pages.keys()),
            notes=str(data.get("notes", "")),
            model=self.name,
        )
