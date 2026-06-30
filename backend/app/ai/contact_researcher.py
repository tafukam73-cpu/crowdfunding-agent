"""AI 連絡先リサーチの共通インターフェース。

既存の Contact Discovery（HTML 抽出中心）でメールが見つからない / 低品質な場合に、
メーカー公式サイト・クラファンページ・SNS・会社名・既存の探索結果から、営業に
使える連絡先を AI（Claude / モック）で推定・整理する補完レイヤー。

設計方針：
- 入力は ContactResearchContext（DB / モデル非依存）。service が Project・
  CompanyResearch・ContactDiscovery から組み立てる。
- 出力は ContactResearchResult（DB / モデル非依存）。
- AI が推測でメールアドレスを捏造しないことが最重要。プロンプトで禁止し、さらに
  service 側で既存の email_exclusion_reason / 出典必須チェックで再検証する
  （ここでは検証しない＝責務を分離する）。

get_contact_researcher() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockContactResearcher（既定）
  - 設定済み            → ClaudeContactResearcher
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.config import settings

# AI が推奨できる連絡チャネル（これ以外は service 側で manual_research に丸める）
VALID_AI_CHANNELS = {
    "email",
    "contact_form",
    "linkedin",
    "instagram",
    "facebook",
    "press",
    "distributor_page",
    "manual_research",
}


class AiCandidateEmail(BaseModel):
    """AI が提示する候補メール 1 件。

    source_url は「そのメールが記載されている出典 URL」。出典の無い候補は
    捏造の疑いがあるため service 側で除外する（採用は出典付きのみ）。
    """

    email: str
    score: int = 0
    confidence: str = ""  # high / medium / low
    reason: str = ""
    source_url: str = ""


class ContactResearchContext(BaseModel):
    """AI 連絡先リサーチの入力（案件・企業リサーチ・既存探索の集約）。"""

    title: str = ""
    description_clean: str = ""
    source_site: str = ""
    source_url: str = ""
    maker_name: str = ""
    official_site_url: str = ""

    # company_research.sources
    company_sources: list[str] = Field(default_factory=list)

    # 既存 Contact Discovery の情報
    searched_urls: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    discovered_socials: dict[str, str] = Field(default_factory=dict)
    primary_contact_form_url: str = ""
    # 既存で見つかった候補メール [{email, score, tier, sources:[url]}]
    existing_candidate_emails: list[dict] = Field(default_factory=list)
    # 除外済みメール [{email, reason}]（AI に同じ候補を出させないため）
    excluded_emails: list[dict] = Field(default_factory=list)

    # 営業候補から除外すべきプラットフォームのドメイン（source_site 由来）
    platform_domain: str = ""


class ContactResearchResult(BaseModel):
    """AI 連絡先リサーチ結果（DB 非依存）。"""

    primary_email: str | None = None
    candidate_emails: list[AiCandidateEmail] = Field(default_factory=list)
    contact_form_url: str | None = None
    instagram_url: str | None = None
    facebook_url: str | None = None
    linkedin_url: str | None = None
    recommended_channel: str = ""
    confidence_score: int = 0
    search_queries: list[str] = Field(default_factory=list)
    # [{url, type, note}]
    sources: list[dict] = Field(default_factory=list)
    notes: str = ""
    model: str = ""


class ContactResearcher(ABC):
    """全 AI 連絡先リサーチャーの基底クラス。"""

    name: str = "base"
    #: 直近呼び出しのトークン使用量（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def research(self, ctx: ContactResearchContext) -> ContactResearchResult:
        """案件・企業・既存探索の情報から連絡先候補を整理して返す。

        メールアドレスを推測で捏造してはならない（出典付きのみ）。外部ページが
        取得できない場合でも、検索クエリ・推奨チャネル・SNS 提案で結果を作る。
        """
        raise NotImplementedError


def get_contact_researcher() -> ContactResearcher:
    if settings.anthropic_api_key:
        from app.ai.claude_contact_researcher import ClaudeContactResearcher

        return ClaudeContactResearcher(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_contact_researcher import MockContactResearcher

    return MockContactResearcher()
