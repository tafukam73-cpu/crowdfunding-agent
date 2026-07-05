"""営業先連絡先探索 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models.contact_discovery import DiscoveryStatus
from app.services.contact_discovery_service import (
    NON_OFFICIAL_PLATFORM_DOMAINS,
    official_site_or_none,
)
from app.services.url_validation import (
    filter_business_urls,
    is_valid_business_url,
)


def _url_or_none(v: str | None) -> str | None:
    """example.com / dummy / test / localhost 等のダミー URL なら None にする。"""
    return v if (v and is_valid_business_url(v)) else None


def _scrub_url_list(v: list[str] | None) -> list[str] | None:
    """URL リストから無効/ダミー URL を除去する（空なら None）。"""
    if not v:
        return v
    cleaned = filter_business_urls(v)
    return cleaned or None


def _scrub_socials(v: dict | None) -> dict | None:
    """SNS 辞書の値がダミー URL のものを除去する（空なら None）。"""
    if not v:
        return v
    cleaned = {k: url for k, url in v.items() if is_valid_business_url(url)}
    return cleaned or None


# site: クエリ等に含まれると無効なダミー/プレースホルダーのドメイン断片。
# site:greenlab.example.com のようなクエリを UI に出さないため（要件4）。
_DUMMY_QUERY_FRAGMENTS = (
    ".example.", "example.com", "example.org", "example.net",
    "dummy.", "sample.", "test.", "localhost", "127.0.0.1",
    "yourdomain", "mydomain", "yourcompany", "mycompany",
)


def _drop_platform_queries(queries: list[str] | None) -> list[str] | None:
    """Google/LinkedIn 検索アシスト用クエリから、プラットフォーム/ダミードメインを
    含むもの（site:kickstarter.com / site:greenlab.example.com 等）を取り除く（要件4）。
    """
    if not queries:
        return queries
    cleaned = [
        q for q in queries
        if not any(d in q for d in NON_OFFICIAL_PLATFORM_DOMAINS)
        and not any(f in (q or "").lower() for f in _DUMMY_QUERY_FRAGMENTS)
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
    # 取得元による信頼度（high / medium / low / unverified / invalid）
    confidence: str | None = None
    confidence_label: str | None = None


class FallbackSearchQuery(BaseModel):
    """メールが見つからない時の手動検索導線（公式サイト/Google/LinkedIn/site:）。"""

    label: str
    type: str
    query: str
    url: str


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


class SearchProviderResult(BaseModel):
    provider: str | None = None
    results: int | None = None
    status: int | None = None
    reason: str | None = None


class WebSearchDiagnostic(BaseModel):
    """1 検索クエリの診断（0件の原因究明用）。"""

    query: str | None = None
    provider: str | None = None      # 実際に結果を返した/代表プロバイダー
    status: int | None = None
    reason: str | None = None
    results: int | None = None
    fallback: str | None = None
    urls: list[str] = []
    providers: list[SearchProviderResult] = []  # 各プロバイダーの試行結果


class WebKeywordCandidates(BaseModel):
    """検索語の素材になるキーワード候補（検索戦略のデバッグ表示用）。"""

    project_title: str | None = None
    short_title: str | None = None
    maker_name: str | None = None
    brand_names: list[str] = []
    official_domain: str | None = None
    domain_name: str | None = None
    source_site: str | None = None
    creator_slug: str | None = None
    project_slug: str | None = None
    maker_ambiguous: bool | None = None


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
    # v3 再帰クロールの PDF 解析結果（抽出メール数・本文長）。任意。
    emails: int | None = None
    text_len: int | None = None


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


class V2Step(BaseModel):
    """Contact Discovery v2 の探索ステップ（UI 進捗表示用）。"""

    step: int | None = None
    phase: str | None = None       # collect / official_site / crawl / linkedin / extract
    label: str | None = None
    status: str | None = None      # done / empty / running
    detail: str | None = None
    urls: list[str] = []


class V2Email(BaseModel):
    """v2 が発見・検証したメール（取得元による信頼度★付き）。"""

    email: str
    stars: int = 0                 # 1〜5（取得元による信頼度）
    confidence_source: str | None = None   # official_site_contact / footer / about ...
    confidence_label: str | None = None    # 公式サイト Contact 等
    confidence_level: str | None = None     # high / medium / low / unverified
    source_url: str | None = None
    email_owner: str | None = None
    sales_stars: int | None = None          # 営業のしやすさ（別軸）
    sales_reason: str | None = None
    sources: list[str] = []


class V2Candidate(BaseModel):
    """公式サイト候補（探索元・採用可否つき）。"""

    url: str
    score: int = 0
    source: str | None = None       # project_website / search
    adopted: bool = False
    reason: str | None = None
    query: str | None = None
    title: str | None = None


class V2CrawledPage(BaseModel):
    url: str
    kind: str | None = None         # root / contact / about / legal / other
    ok: bool | None = None
    emails: int | None = None


class V2LinkedIn(BaseModel):
    type: str                       # company / person
    url: str
    name: str | None = None
    source: str | None = None


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
    # 営業に使えるメールが無いときの「次の一手」（手動検索導線）
    fallback_search_queries: list[FallbackSearchQuery] = []

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
    web_search_diagnostics: list[WebSearchDiagnostic] | None = None
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

    # --- Contact Intelligence v3（公式サイト再帰クロール） ---
    recursive_crawl_enabled: bool = False
    recursive_crawled_urls: list[str] | None = None
    recursive_skipped_urls: list[str] | None = None
    recursive_emails: list[DiscoveredEmail] | None = None
    recursive_forms: list[str] | None = None
    recursive_socials: dict[str, str] | None = None
    recursive_pdfs: list[DiscoveredPdf] | None = None
    recursive_sitemap_urls: list[str] | None = None
    recursive_robots_sitemaps: list[str] | None = None
    recursive_has_mx: bool | None = None
    recursive_mx_provider: str | None = None
    recursive_spf_record: str | None = None
    recursive_dmarc_record: str | None = None
    recursive_failure_reasons: list[str] | None = None
    recursive_summary: str | None = None
    recursive_crawled_at: datetime | None = None

    # --- Contact Discovery v2（人間の検索手順に近い一本道フロー） ---
    v2_researched: bool = False
    v2_status: str | None = None
    v2_steps: list[V2Step] | None = None
    v2_company_name: str | None = None
    v2_product_name: str | None = None
    v2_campaign_url: str | None = None
    v2_official_site_url: str | None = None
    v2_official_site_source: str | None = None
    v2_official_site_candidates: list[V2Candidate] | None = None
    v2_crawled_pages: list[V2CrawledPage] | None = None
    v2_emails: list[V2Email] | None = None
    v2_socials: dict[str, str] | None = None
    v2_forms: list[str] | None = None
    v2_linkedin_company_url: str | None = None
    v2_linkedin_person_url: str | None = None
    v2_linkedin_candidates: list[V2LinkedIn] | None = None
    v2_searched_queries: list[str] | None = None
    v2_search_provider: str | None = None
    v2_primary_email: str | None = None
    v2_primary_source_url: str | None = None
    v2_primary_stars: int | None = None
    v2_confidence_score: int | None = None
    v2_recommended_channel: str | None = None
    v2_summary: str | None = None
    v2_error: str | None = None
    v2_researched_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    # --- 後方互換サニタイズ（古い行がプラットフォーム URL を保持していても出さない） ---
    @field_validator(
        "official_site_url",
        "doc_reader_official_site_url",
        "search_agent_official_site_url",
        "v2_official_site_url",
    )
    @classmethod
    def _no_platform_official(cls, v: str | None) -> str | None:
        # 公式サイトとして kickstarter.com/profile/... 等は返さない。
        return official_site_or_none(v) if v else v

    @field_validator(
        "search_queries",
        "web_searched_queries",
        "web_generated_queries",
        "v2_searched_queries",
        "ai_search_queries",
    )
    @classmethod
    def _no_platform_site_queries(cls, v: list[str] | None) -> list[str] | None:
        return _drop_platform_queries(v)

    @field_validator(
        "primary_email",
        "ai_primary_email",
        "web_primary_email",
    )
    @classmethod
    def _no_dummy_primary_email(cls, v: str | None) -> str | None:
        """古い行に example@ 等のダミーが残っていても UI には出さない（無効扱い）。"""
        from app.services.email_validation import is_valid_business_email

        if v and not is_valid_business_email(v):
            return None
        return v

    @model_validator(mode="after")
    def _scrub_dummy_urls(self):
        """API 境界の最終防波堤：全 URL フィールドからダミー/プレースホルダー URL
        （example.com / dummy / sample / test / localhost 等）を除去する（要件5・6）。

        既存 DB に example URL が残っていても、レスポンスでは必ず null / [] にする。
        Contact Discovery / Document Reader / Search Agent / v2 / CRM 反映候補の
        すべてに横断適用する。
        """
        # --- 単一 URL フィールド（無効なら None） ---
        scalar_urls = (
            "primary_contact_form_url",
            "instagram_url", "facebook_url", "twitter_url", "linkedin_url",
            "youtube_url",
            "ai_contact_form_url", "ai_instagram_url", "ai_facebook_url",
            "ai_linkedin_url",
            "web_primary_contact_form_url",
            "v2_primary_source_url",
            "v2_linkedin_company_url", "v2_linkedin_person_url",
        )
        for name in scalar_urls:
            if getattr(self, name, None):
                setattr(self, name, _url_or_none(getattr(self, name)))

        # --- URL リストフィールド（無効な URL を除去） ---
        list_urls = (
            "searched_urls", "discovered_forms",
            "web_searched_urls", "web_discovered_forms",
            "search_agent_searched_urls",
            "recursive_crawled_urls", "recursive_skipped_urls",
            "recursive_forms", "recursive_sitemap_urls",
            "recursive_robots_sitemaps",
            "v2_forms",
        )
        for name in list_urls:
            if getattr(self, name, None):
                setattr(self, name, _scrub_url_list(getattr(self, name)))

        # --- SNS 辞書（値がダミー URL のものを除去） ---
        for name in (
            "discovered_socials", "web_discovered_socials",
            "doc_reader_socials", "search_agent_socials",
            "recursive_socials", "v2_socials",
        ):
            if getattr(self, name, None):
                setattr(self, name, _scrub_socials(getattr(self, name)))

        # --- 出典リスト（AiSource.url がダミーの項目を除去） ---
        for name in ("ai_sources", "doc_reader_sources"):
            items = getattr(self, name, None)
            if items:
                setattr(self, name,
                        [s for s in items if is_valid_business_url(s.url)] or None)

        # --- メール候補の sources / source_url ---
        for e in (self.discovered_emails or []):
            e.sources = filter_business_urls(e.sources)
        for e in (self.sales_contacts or []):
            e.sources = filter_business_urls(e.sources)
        for e in (self.web_discovered_emails or []):
            e.sources = filter_business_urls(e.sources)
        for e in (self.recursive_emails or []):
            e.sources = filter_business_urls(e.sources)
        for coll in (self.ai_candidate_emails, self.doc_reader_emails,
                     self.search_agent_emails):
            for e in (coll or []):
                if getattr(e, "source_url", None) and not is_valid_business_url(
                    e.source_url
                ):
                    e.source_url = None
        for e in (self.v2_emails or []):
            if e.source_url and not is_valid_business_url(e.source_url):
                e.source_url = None
            e.sources = filter_business_urls(e.sources)

        # --- 外部連絡先リンク（approach_options / contact_forms / people） ---
        if self.approach_options:
            self.approach_options = [
                o for o in self.approach_options
                if o.url is None or is_valid_business_url(o.url)
            ] or None
        for name in ("doc_reader_contact_forms", "search_agent_contact_forms"):
            items = getattr(self, name, None)
            if items:
                setattr(self, name,
                        [f for f in items if is_valid_business_url(f.url)] or None)
        for name in ("doc_reader_people", "search_agent_people"):
            for p in (getattr(self, name, None) or []):
                if getattr(p, "linkedin_url", None) and not is_valid_business_url(
                    p.linkedin_url
                ):
                    p.linkedin_url = None
                if getattr(p, "source_url", None) and not is_valid_business_url(
                    p.source_url
                ):
                    p.source_url = None

        # --- 調査ページ / 検索結果 / PDF（url がダミーの項目を除去） ---
        for name in ("web_candidate_pages", "web_search_results",
                     "web_discovered_pdfs", "recursive_pdfs",
                     "v2_official_site_candidates", "v2_crawled_pages"):
            items = getattr(self, name, None)
            if items:
                setattr(self, name,
                        [i for i in items if is_valid_business_url(i.url)] or None)

        # --- v2 LinkedIn 候補 / 検索診断 URL ---
        if self.v2_linkedin_candidates:
            self.v2_linkedin_candidates = [
                li for li in self.v2_linkedin_candidates
                if is_valid_business_url(li.url)
            ] or None
        for d in (self.web_search_diagnostics or []):
            d.urls = filter_business_urls(d.urls)

        # --- Search Agent ステップの URL ---
        for st in (self.search_agent_steps or []):
            if getattr(st, "url", None) and not is_valid_business_url(st.url):
                st.url = None

        # --- v2 探索ステップの URL 群 ---
        for st in (self.v2_steps or []):
            if getattr(st, "urls", None):
                st.urls = filter_business_urls(st.urls)

        # --- 手動検索導線（site:greenlab.example.com 等を除去） ---
        if self.fallback_search_queries:
            self.fallback_search_queries = [
                q for q in self.fallback_search_queries
                if is_valid_business_url(q.url)
                and not any(f in (q.query or "").lower()
                            for f in _DUMMY_QUERY_FRAGMENTS)
            ]
        return self

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
