"""モック AI Search Agent。

外部 API を使わず、現在の探索状態から「次に見るべき URL / 検索クエリ」を決める
決定的なプランナー。既存の Web Research 結果（SNS・候補 URL）を起点に、
SNS プロフィール → Linktree 等のリンク集 → 公式サイト → Contact のように
段階的に掘り下げる疑似ステップを返す。

重要：ここでは取得も抽出もしない（service が実行）。メール・人名の捏造もしない。
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.ai.search_agent import (
    SearchAgent,
    SearchAgentPlan,
    SearchAgentState,
    STEP_QUERY_BUDGET,
    STEP_URL_BUDGET,
)

# 公式サイトで当たりにいく代表パス（優先順）
_KNOWN_PATHS = ["", "/contact", "/contact-us", "/about", "/team", "/press", "/wholesale"]


def _has_maker_email(state: SearchAgentState) -> bool:
    dom = ""
    if state.official_site_url:
        dom = urlparse(state.official_site_url).netloc.lower().replace("www.", "")
    for e in state.emails:
        addr = str(e.get("email", "")).lower()
        if dom and addr.endswith("@" + dom):
            return True
    return bool(state.emails)


class MockSearchAgent(SearchAgent):
    name = "mock-search-agent-v1"

    def plan(self, state: SearchAgentState) -> SearchAgentPlan:
        visited = set(state.visited_urls)
        ran = set(state.ran_queries)

        missing: list[str] = []
        if not state.official_site_url:
            missing.append("official site")
        if not _has_maker_email(state):
            missing.append("email")
        if not any(state.socials.get(k) for k in ("instagram", "facebook", "linkedin")):
            missing.append("SNS")

        next_urls: list[str] = []

        def add_url(u: str) -> None:
            if (
                u
                and u.startswith(("http://", "https://"))
                and u not in visited
                and u not in next_urls
            ):
                next_urls.append(u)

        # 1. 公式サイトが分かっていれば代表パスを掘る（Contact/About/Team/Press…）
        if state.official_site_url:
            p = urlparse(state.official_site_url)
            root = f"{p.scheme}://{p.netloc}"
            for path in _KNOWN_PATHS:
                add_url(root + path)
        # 2. 発見済みの未訪問候補（SNS プロフィール / Linktree / 外部リンク）を掘る
        for u in state.candidate_urls:
            add_url(u)

        # 3. まだ見るものが無ければ検索で候補を増やす
        next_queries: list[str] = []
        if not next_urls:
            base = (state.maker_name or state.title or "").strip()
            if base:
                for q in (
                    f'"{base}" official website',
                    f'"{base}" instagram',
                    f'"{base}" linktree',
                    f'"{base}" contact email',
                ):
                    if q not in ran and q not in next_queries:
                        next_queries.append(q)

        next_urls = next_urls[:STEP_URL_BUDGET]
        next_queries = next_queries[:STEP_QUERY_BUDGET]

        # 終了条件：営業に使えるメールを取得した / これ以上調べる先が無い
        stop = False
        reason = ""
        if _has_maker_email(state):
            stop = True
            reason = "営業に使えるメールを取得できたため探索を終了します。"
        elif not next_urls and not next_queries:
            stop = True
            reason = "未訪問の候補 URL・未実行の検索クエリが無くなったため終了します。"
        else:
            targets = next_urls or next_queries
            reason = (
                "不足情報（" + "・".join(missing or ["連絡先"]) + "）を補うため、"
                + f"次に {len(targets)} 件を調査します。"
            )

        return SearchAgentPlan(
            missing=missing,
            next_urls=next_urls,
            next_queries=next_queries,
            reason=reason,
            stop=stop,
        )
