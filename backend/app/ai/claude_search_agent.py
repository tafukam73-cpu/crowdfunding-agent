"""Claude AI Search Agent。

ANTHROPIC_API_KEY 設定時に get_search_agent() がこれを使う。各ステップで現在の
探索状態（発見済みの公式サイト・メール・SNS・フォーム・未訪問候補 URL・実行済み
検索クエリ）を Claude に渡し、「まだ足りない情報 / 次に取得する URL / 次に実行する
検索クエリ / 理由 / 続行か終了か」を JSON schema で受け取る。

取得・抽出・フィルタは service が安全に実行する（AI は判断のみ）。メール・人名の
捏造は service 側の既存フィルタ（出典必須 / platform 除外 / no-reply 除外）で防ぐ。
"""
from __future__ import annotations

import json
import logging

from app.ai.search_agent import (
    SearchAgent,
    SearchAgentPlan,
    SearchAgentState,
)

logger = logging.getLogger("ai.claude_search_agent")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "missing": {"type": "array", "items": {"type": "string"}},
        "next_urls": {"type": "array", "items": {"type": "string"}},
        "next_queries": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "stop": {"type": "boolean"},
    },
    "required": ["missing", "next_urls", "next_queries", "reason", "stop"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a research agent finding B2B sales contacts for overseas crowdfunding "
    "makers on behalf of a Japanese distributor. Each turn you are given the current "
    "discovery state (what has been found and which URLs/queries remain). Decide the "
    "NEXT best steps: which URLs to fetch and which web searches to run to find the "
    "maker's official website, contact page, business email, and official social "
    "profiles. Prefer following links you already discovered (SNS profiles, Linktree/"
    "Beacons/bio.site/carrd/lit.link link hubs, official site subpages like /contact, "
    "/about, /team, /press, /wholesale).\n\n"
    "RULES:\n"
    "- Only propose fetching/searching. You do NOT extract data yourself.\n"
    "- Never propose login-required pages. Never treat a crowdfunding platform URL "
    "(kickstarter.com, indiegogo.com, ulule.com, makuake.com, camp-fire.jp, "
    "greenfunding.jp, readyfor.jp) as the official site.\n"
    "- Stop when you already have a usable maker email, OR when there is nothing new "
    "worth exploring. Keep next_urls/next_queries small (<=4 and <=3). Reason in "
    "Japanese. Output must follow the JSON schema exactly."
)


class ClaudeSearchAgent(SearchAgent):
    name = "claude-search-agent"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        from anthropic import Anthropic

        self.model = model
        self.name = model
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, state: SearchAgentState) -> str:
        def _lst(items, limit=25):
            return "\n".join(f"  - {x}" for x in items[:limit]) or "  (none)"

        emails = "\n".join(
            f"  - {e.get('email')} (src={e.get('source_url')})" for e in state.emails
        ) or "  (none)"
        socials = "\n".join(f"  - {k}: {v}" for k, v in state.socials.items()) or "  (none)"
        return "\n".join([
            f"Step {state.step} of up to 5.",
            "",
            "# Project",
            f"Title: {state.title}",
            f"Maker/brand: {state.maker_name or '(unknown)'}",
            f"Platform: {state.source_site}",
            f"Project URL: {state.source_url}",
            f"Creator/Maker profile URL: {state.maker_url}",
            f"Official site (found so far): {state.official_site_url or '(not found)'}",
            "",
            "# Found so far",
            "Emails:", emails,
            "Socials:", socials,
            "Forms:", _lst(state.forms),
            "",
            "# Already visited URLs", _lst(state.visited_urls),
            "# Already run queries", _lst(state.ran_queries),
            "# Discovered but NOT yet visited (good candidates to follow)",
            _lst(state.candidate_urls),
            "",
            "Decide next_urls (<=4) and next_queries (<=3), list what is still missing, "
            "give your reason (Japanese), and set stop=true if you have a usable maker "
            "email or nothing else is worth exploring.",
        ])

    def plan(self, state: SearchAgentState) -> SearchAgentPlan:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._build_prompt(state)}],
            output_config={"format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
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
                f"Search Agent の JSON 解析に失敗しました: {exc} / 応答抜粋: {text[:300]}"
            )
        return SearchAgentPlan(
            missing=[str(x) for x in (data.get("missing") or []) if x],
            next_urls=[str(x) for x in (data.get("next_urls") or []) if x],
            next_queries=[str(x) for x in (data.get("next_queries") or []) if x],
            reason=str(data.get("reason", "")),
            stop=bool(data.get("stop", False)),
        )
