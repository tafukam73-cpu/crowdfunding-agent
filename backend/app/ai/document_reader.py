"""AI Document Reader の共通インターフェース。

Web Research / Contact Hunter / AI Contact Research の上に載る「読解」レイヤー。
検索結果・HTMLリンク・正規表現だけでは拾えない、本文・埋め込みJSON・カード・
プロフィール説明・PDF・フッター等に散らばった情報を、ページ全体の文脈から
AI（Claude / モック）に整理させ、会社名・公式サイト・メール・SNS・問い合わせ
フォーム・担当者候補を構造化 JSON で受け取る。

設計方針：
- 入力は DocumentReaderContext（DB / モデル非依存）。service が Project・
  ContactDiscovery（Web Research 結果）から、重要ページの本文/リンク/抽出済み
  メール・SNS・検索スニペットを集めて組み立てる。
- 出力は DocumentReaderResult（DB / モデル非依存）。
- 最重要：メール・人名を推測で捏造させない。プロンプトで強く禁止し、さらに
  service 側で email_exclusion_reason / platform 除外 / 出典必須で再検証する。
- 自律的な無限検索はしない。渡されたページ・スニペット・PDF のみを読解する。

get_document_reader() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockDocumentReader（既定）
  - 設定済み            → ClaudeDocumentReader
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.config import settings

# AI が推奨できる連絡チャネル（これ以外は service 側で manual_search に丸める）
VALID_DOC_CHANNELS = {
    "email",
    "contact_form",
    "linkedin",
    "instagram",
    "facebook",
    "youtube",
    "tiktok",
    "press",
    "distributor_page",
    "manual_search",
}

# 1 ページあたりの本文最大文字数 / 全体入力の目安上限
PAGE_TEXT_MAX = 5000
TOTAL_TEXT_MAX = 40000


class DocReaderPage(BaseModel):
    """AI に読ませる 1 ページ（本文・リンク・抽出済みメール/SNS）。"""

    url: str
    title: str = ""
    page_type: str = ""      # official_site / contact / about / team / press / ...
    text: str = ""           # HTML 除去済み本文（切り詰め済み）
    links: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    socials: dict[str, str] = Field(default_factory=dict)


class DocReaderEmail(BaseModel):
    email: str
    purpose: str = ""        # general_contact / sales / support / press / ...
    confidence: int = 0
    source_url: str = ""
    reason: str = ""


class DocReaderPerson(BaseModel):
    name: str
    title: str = ""
    linkedin_url: str | None = None
    email: str | None = None
    confidence: int = 0
    source_url: str = ""
    reason: str = ""


class DocumentReaderContext(BaseModel):
    """AI Document Reader の入力（案件 + Web Research が集めたページ群）。"""

    title: str = ""
    maker_name: str = ""
    source_site: str = ""
    source_url: str = ""
    maker_url: str = ""
    description_clean: str = ""
    official_site_url: str = ""

    pages: list[DocReaderPage] = Field(default_factory=list)
    # 既に自動抽出/Web 調査で見つかっている情報（AI に既知として渡す）
    existing_emails: list[str] = Field(default_factory=list)
    existing_socials: dict[str, str] = Field(default_factory=dict)
    pdf_texts: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    # [{query, url, title, snippet}]
    search_snippets: list[dict] = Field(default_factory=list)

    # 営業候補から常に除外するプラットフォームのドメイン（source_site 由来）
    platform_domain: str = ""


class DocumentReaderResult(BaseModel):
    """AI Document Reader の結果（DB 非依存）。"""

    official_company_name: str | None = None
    brand_names: list[str] = Field(default_factory=list)
    official_site_url: str | None = None
    emails: list[DocReaderEmail] = Field(default_factory=list)
    # [{url, confidence, source_url}]
    contact_forms: list[dict] = Field(default_factory=list)
    # {instagram/facebook/linkedin/youtube/tiktok/x: url|null}
    socials: dict[str, str | None] = Field(default_factory=dict)
    people: list[DocReaderPerson] = Field(default_factory=list)
    recommended_channel: str = ""
    recommended_contact: str | None = None
    confidence_score: int = 0
    evidence_summary: str = ""
    missing_info: list[str] = Field(default_factory=list)
    # [{url, type, note}]
    sources: list[dict] = Field(default_factory=list)
    model: str = ""


class DocumentReader(ABC):
    """全 AI Document Reader の基底クラス。"""

    name: str = "base"
    #: 直近呼び出しのトークン使用量（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def read(self, ctx: DocumentReaderContext) -> DocumentReaderResult:
        """渡されたページ群を読解し、連絡先情報を構造化して返す。

        メール・人名を推測で捏造してはならない（渡したデータに実在するもののみ）。
        """
        raise NotImplementedError


def get_document_reader() -> DocumentReader:
    if settings.anthropic_api_key:
        from app.ai.claude_document_reader import ClaudeDocumentReader

        return ClaudeDocumentReader(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_document_reader import MockDocumentReader

    return MockDocumentReader()
