"""AI Search Agent の共通インターフェース。

AI Document Reader が「取得済みページを読む」だけなのに対し、Search Agent は
Web 版 Claude のように「次に見るべきページ・検索クエリを毎ステップ判断」しながら、
公式サイト・SNS・Linktree 等のリンク集・問い合わせページ・担当者候補を反復探索する。

設計方針（安全な agentic ループ）：
- 判断（AI）と実行（service）を分離する。AI は各ステップで state（現在の発見状況・
  未訪問候補）を見て「次に取得する URL / 実行する検索クエリ / 理由 / 続行か終了か」を
  返すだけ。実際の取得・検索・抽出・フィルタは service が安全に実行する。
- 最大 5 ステップ / 20 URL / 20 クエリ / 1 URL 12 秒。ログイン必須ページはスキップ。
- メール・人名を推測で捏造させない。出典 URL 必須。既存の除外フィルタ（platform /
  no-reply / sentry 等）を必ず通す。

get_search_agent() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockSearchAgent（既定）
  - 設定済み            → ClaudeSearchAgent
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.config import settings

MAX_STEPS = 5
MAX_URLS = 20
MAX_QUERIES = 20
FETCH_TIMEOUT = 12.0
# 1 ステップあたりの実行上限（暴走防止）
STEP_URL_BUDGET = 4
STEP_QUERY_BUDGET = 3


class SearchAgentState(BaseModel):
    """探索の現在状態（AI に渡す）。"""

    title: str = ""
    maker_name: str = ""
    source_site: str = ""
    source_url: str = ""
    maker_url: str = ""
    description_clean: str = ""

    official_site_url: str = ""
    emails: list[dict] = Field(default_factory=list)     # {email, source_url}
    socials: dict[str, str] = Field(default_factory=dict)
    forms: list[str] = Field(default_factory=list)
    people: list[dict] = Field(default_factory=list)

    visited_urls: list[str] = Field(default_factory=list)
    ran_queries: list[str] = Field(default_factory=list)
    # 発見済みだが未訪問の候補 URL（SNS プロフィール / Linktree / 外部リンク等）
    candidate_urls: list[str] = Field(default_factory=list)
    step: int = 0


class SearchAgentPlan(BaseModel):
    """AI が返す各ステップの計画。"""

    missing: list[str] = Field(default_factory=list)      # まだ足りない情報
    next_urls: list[str] = Field(default_factory=list)    # 次に取得する URL
    next_queries: list[str] = Field(default_factory=list)  # 次に実行する検索クエリ
    reason: str = ""                                       # 判断理由
    stop: bool = False                                     # 探索を終了するか


class SearchAgent(ABC):
    """全 AI Search Agent の基底クラス。"""

    name: str = "base"
    last_usage: dict | None = None

    @abstractmethod
    def plan(self, state: SearchAgentState) -> SearchAgentPlan:
        """現在状態から「次に何を調べるか」を決める（取得・検索は service が実行）。"""
        raise NotImplementedError


def get_search_agent() -> SearchAgent:
    if settings.anthropic_api_key:
        from app.ai.claude_search_agent import ClaudeSearchAgent

        return ClaudeSearchAgent(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_search_agent import MockSearchAgent

    return MockSearchAgent()
