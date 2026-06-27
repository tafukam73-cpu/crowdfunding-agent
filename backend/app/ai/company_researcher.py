"""AI 企業リサーチの共通インターフェース。

モックリサーチャー・Claude リサーチャーがこの CompanyResearcher を実装する。
出力は ResearchResult（DB / モデル非依存）。リサーチは外部送信を伴わず、
案件・メーカー情報の整理と推論のみを行う。

get_company_researcher() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockCompanyResearcher（既定）
  - 設定済み            → ClaudeCompanyResearcher
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.config import settings
from app.models.project import Project


class ResearchResult(BaseModel):
    """企業リサーチ結果（completed 時の中身）。"""

    maker_name: str = ""
    official_site_url: str = ""
    project_url: str = ""

    brand_summary: str = ""
    company_mission: str = ""
    product_summary: str = ""
    key_product_features: list[str] = Field(default_factory=list)
    brand_strengths: list[str] = Field(default_factory=list)
    differentiation_points: list[str] = Field(default_factory=list)
    japan_market_fit: str = ""
    personalized_compliment: str = ""
    outreach_angles: list[str] = Field(default_factory=list)
    risks_or_cautions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    raw_notes: str = ""
    model: str = ""


class CompanyResearcher(ABC):
    """全リサーチャーの基底クラス。"""

    name: str = "base"
    #: 直近呼び出しのトークン使用量（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def research(self, project: Project) -> ResearchResult:
        """案件・メーカーをリサーチして結果を返す。

        外部ページが取得できない場合でも、案件情報からの推論で結果を作る
        （失敗で例外を投げるのは Claude の JSON パース失敗など限定的なケース）。
        """
        raise NotImplementedError


def get_company_researcher() -> CompanyResearcher:
    if settings.anthropic_api_key:
        from app.ai.claude_company_researcher import ClaudeCompanyResearcher

        return ClaudeCompanyResearcher(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_company_researcher import MockCompanyResearcher

    return MockCompanyResearcher()
