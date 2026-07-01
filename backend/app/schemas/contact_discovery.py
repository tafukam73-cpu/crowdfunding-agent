"""営業先連絡先探索 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.contact_discovery import DiscoveryStatus
from app.services.contact_discovery_service import (
    NON_OFFICIAL_PLATFORM_DOMAINS,
    official_site_or_none,
)


def _drop_platform_queries(queries: list[str] | None) -> list[str] | None:
    """Google 検索アシスト用クエリから site:<クラファンドメイン> を取り除く。

    過去に保存した行（公式サイトをプラットフォーム URL と誤判定していた頃の
    site:kickstarter.com など）が UI に出ないようにするための後方互換サニタイズ。
    """
    if not queries:
        return queries
    cleaned = [
        q for q in queries
        if not any(d in q for d in NON_OFFICIAL_PLATFORM_DOMAINS)
    ]
    return cleaned or None


class DiscoveredEmail(BaseModel):
    email: str
    score: int
    tier: str
    # 所有者分類（maker / platform / monitoring / unknown）。
    # platform は UI 非表示。過去データには無いため任意。
    email_owner: str | None = None
    sources: list[str] = []


class SalesContact(BaseModel):
    """営業のしやすさで格付けした連絡先（🏆 営業推奨連絡先）。"""

    email: str
    stars: int               # 1〜5（5 が最適）
    reason: str
    category: str | None = None
    score: int = 0
    email_owner: str | None = None
    sources: list[str] = []


class ApproachOption(BaseModel):
    channel: str
    label: str
    url: str | None = None
    score: int
    reason: str | None = None


class AiCandidateEmail(BaseModel):
    """AI 連絡先リサーチが提示し、既存フィルタで再検証済みの候補メール。"""

    email: str
    score: int = 0
    confidence: str | None = None
    reason: str | None = None
    source_url: str | None = None
    # 所有者分類（maker / unknown など。platform は保存時点で除外済み）
    email_owner: str | None = None


class AiSource(BaseModel):
    url: str
    type: str | None = None
    note: str | None = None


class WebCandidatePage(BaseModel):
    """AI Web Research が調査した候補ページ。"""

    url: str
    type: str | None = None
    ok: bool | None = None        # 取得成功したか
    emails: int | None = None     # そのページで抽出したメール数


class WebDebugCounts(BaseModel):
    """探索処理の集計（どこまで進んだかの可視化）。"""

    queries: int | None = None        # 実行した検索クエリ数
    results: int | None = None        # 検索結果件数
    crawled: int | None = None        # 巡回 URL 数
    ok: int | None = None             # 成功 URL 数
    failed: int | None = None         # 失敗 URL 数
    excluded: int | None = None       # 除外した検索結果 URL 数
    email_pages: int | None = None    # メールを抽出できたページ数
    # Kickstarter 等の埋め込み JSON "websites":[...]（要件 6）
    ks_websites_present: bool | None = None     # websites 配列が存在したか
    ks_websites_count: int | None = None        # websites 配列の URL 件数
    ks_websites_registered: bool | None = None  # 外部公式サイトが登録されていたか


class WebKeywordCandidates(BaseModel):
    """検索語の素材になるキーワード候補（検索戦略のデバッグ表示用）。"""

    project_title: str | None = None
    short_title: str | None = None
    maker_name: str | None = None
    brand_names: list[str] = []
    official_domain: str | None = None
    domain_name: str | None = None
    source_site: str | None = None


class WebSearchResult(BaseModel):
    """検索結果 1 件のスコアリング履歴（採用/除外理由つき）。"""

    query: str | None = None
    url: str
    title: str | None = None
    score: int | None = None
    kind: str | None = None      # social / pdf / page / excluded
    adopted: bool | None = None
    reason: str | None = None


class DiscoveredPdf(BaseModel):
    url: str
    label: str | None = None
    relevant: bool | None = None


class DocReaderEmail(BaseModel):
    email: str
    purpose: str | None = None
    confidence: int = 0
    source_url: str | None = None
    reason: str | None = None
    email_owner: str | None = None


class DocReaderContactForm(BaseModel):
    url: str
    confidence: int = 0
    source_url: str | None = None


class DocReaderPerson(BaseModel):
    name: str
    title: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    confidence: int = 0
    source_url: str | None = None
    reason: str | None = None


class SearchAgentStep(BaseModel):
    step: int | None = None
    action: str | None = None      # search / visit / skip / stop
    url: str | None = None
    query: str | None = None
    reason: str | None = None
    ok: bool | None = None
    results: int | None = None
    found: dict[str, int] | None = None
    missing: list[str] | None = None


class ContactDiscoveryOut(BaseModel):
    id: int
    project_id: int
    maker_id: int | None = None
    status: DiscoveryStatus

    primary_email: str | None = None
    primary_contact_form_url: str | None = None
    official_site_url: str | None = None

    instagram_url: str | None = None
    facebook_url: str | None = None
    twitter_url: str | None = None
    linkedin_url: str | None = None
    youtube_url: str | None = None

    discovered_emails: list[DiscoveredEmail] | None = None
    discovered_forms: list[str] | None = None
    discovered_socials: dict[str, str] | None = None
    searched_urls: list[str] | None = None

    # 🏆 営業推奨連絡先ランキング（発見メールを営業のしやすさ順に格付け）
    sales_contacts: list[SalesContact] = []

    confidence_score: int | None = None
    # Contact Intelligence
    contactability_score: int | None = None
    recommended_channel: str | None = None
    recommended_action: str | None = None
    discovery_checklist: dict[str, bool] | None = None
    approach_options: list[ApproachOption] | None = None
    search_queries: list[str] | None = None
    evidence_summary: str | None = None

    notes: str | None = None
    error: str | None = None

    # --- AI 連絡先リサーチ（自動抽出とは区別して表示） ---
    ai_researched: bool = False
    ai_primary_email: str | None = None
    ai_contact_form_url: str | None = None
    ai_instagram_url: str | None = None
    ai_facebook_url: str | None = None
    ai_linkedin_url: str | None = None
    ai_candidate_emails: list[AiCandidateEmail] | None = None
    ai_search_queries: list[str] | None = None
    ai_sources: list[AiSource] | None = None
    ai_confidence_score: int | None = None
    ai_recommended_channel: str | None = None
    ai_notes: str | None = None
    ai_model: str | None = None
    ai_researched_at: datetime | None = None

    # --- AI Web Research Mode（検索エンジン＋公式サイト横断クロール） ---
    web_researched: bool = False
    web_search_provider: str | None = None
    web_debug_counts: WebDebugCounts | None = None
    web_research_flow: str | None = None
    web_keyword_candidates: WebKeywordCandidates | None = None
    web_generated_queries: list[str] | None = None
    web_search_results: list[WebSearchResult] | None = None
    web_searched_queries: list[str] | None = None
    web_searched_urls: list[str] | None = None
    web_candidate_pages: list[WebCandidatePage] | None = None
    web_discovered_emails: list[DiscoveredEmail] | None = None
    web_discovered_forms: list[str] | None = None
    web_discovered_socials: dict[str, str] | None = None
    web_discovered_pdfs: list[DiscoveredPdf] | None = None
    web_primary_email: str | None = None
    web_primary_contact_form_url: str | None = None
    web_recommended_channel: str | None = None
    web_confidence_score: int | None = None
    web_evidence_summary: str | None = None
    web_notes: str | None = None
    web_research_error: str | None = None
    web_researched_at: datetime | None = None

    # --- AI Document Reader（ページ全体を読解して連絡先を整理） ---
    doc_reader_researched: bool = False
    doc_reader_model: str | None = None
    doc_reader_official_company_name: str | None = None
    doc_reader_brand_names: list[str] | None = None
    doc_reader_official_site_url: str | None = None
    doc_reader_emails: list[DocReaderEmail] | None = None
    doc_reader_contact_forms: list[DocReaderContactForm] | None = None
    doc_reader_socials: dict[str, str] | None = None
    doc_reader_people: list[DocReaderPerson] | None = None
    doc_reader_recommended_channel: str | None = None
    doc_reader_recommended_contact: str | None = None
    doc_reader_confidence_score: int | None = None
    doc_reader_evidence_summary: str | None = None
    doc_reader_missing_info: list[str] | None = None
    doc_reader_sources: list[AiSource] | None = None
    doc_reader_researched_at: datetime | None = None

    # --- AI Search Agent（反復探索） ---
    search_agent_researched: bool = False
    search_agent_model: str | None = None
    search_agent_status: str | None = None
    search_agent_steps: list[SearchAgentStep] | None = None
    search_agent_searched_queries: list[str] | None = None
    search_agent_searched_urls: list[str] | None = None
    search_agent_official_site_url: str | None = None
    search_agent_emails: list[DocReaderEmail] | None = None
    search_agent_contact_forms: list[DocReaderContactForm] | None = None
    search_agent_socials: dict[str, str] | None = None
    search_agent_people: list[DocReaderPerson] | None = None
    search_agent_recommended_channel: str | None = None
    search_agent_recommended_contact: str | None = None
    search_agent_confidence_score: int | None = None
    search_agent_evidence_summary: str | None = None
    search_agent_stop_reason: str | None = None
    search_agent_error: str | None = None
    search_agent_researched_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    # --- 後方互換サニタイズ（古い行がプラットフォーム URL を保持していても出さない） ---
    @field_validator(
        "official_site_url",
        "doc_reader_official_site_url",
        "search_agent_official_site_url",
    )
    @classmethod
    def _no_platform_official(cls, v: str | None) -> str | None:
        # 公式サイトとして kickstarter.com/profile/... 等は返さない。
        return official_site_or_none(v) if v else v

    @field_validator(
        "search_queries", "web_searched_queries", "web_generated_queries"
    )
    @classmethod
    def _no_platform_site_queries(cls, v: list[str] | None) -> list[str] | None:
        return _drop_platform_queries(v)

    model_config = ConfigDict(from_attributes=True)


class OutreachMessageOut(BaseModel):
    """問い合わせフォーム / SNS DM 用の短文アウトリーチ文。"""

    channel: str
    channel_label: str
    text: str
    char_count: int


class ApplyToCrmRequest(BaseModel):
    """CRM 反映リクエスト。email 未指定でも推奨チャネル等を記録する。"""

    email: str | None = None


class ApplyToCrmResult(BaseModel):
    maker_id: int
    contact_id: int | None = None
    email: str | None = None
    recorded: bool = True
