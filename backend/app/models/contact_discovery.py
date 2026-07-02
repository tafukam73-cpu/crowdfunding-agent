"""営業先連絡先探索モデル。

クラウドファンディングページだけでなく、メーカー公式サイト・問い合わせページ・
SNS から営業先候補（メール・問い合わせフォーム・SNS）を収集した結果を保存する。
取得失敗してもアプリは落とさず、status=failed で記録する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DiscoveryStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class ContactDiscovery(Base):
    __tablename__ = "contact_discoveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 紐づく CRM メーカー（あれば）。CRM 反映時の対象。
    maker_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DiscoveryStatus.pending.value, index=True
    )

    # --- 代表値（スコア最上位） ---
    primary_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_contact_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_site_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- SNS ---
    instagram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    facebook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    twitter_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 発見した候補一覧 ---
    # [{email, score, tier, sources:[url]}]
    discovered_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 問い合わせフォーム/コンタクトページの URL
    discovered_forms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {platform: url}
    discovered_socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 探索した URL（重複排除済み）
    searched_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 総合的な確度（0〜100）
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Contact Intelligence（メールが無くても営業可能性を総合評価） ---
    # 営業可能性スコア（0〜100）
    contactability_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 推奨連絡チャネル（email / contact_form / linkedin / instagram / facebook /
    # press / distributor_page / manual_research）
    recommended_channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 推奨アクション（具体的な次の一手）
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 探索チェックリスト（official_site_checked など）
    discovery_checklist: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 営業アプローチ候補 [{channel,label,url,score,reason}]
    approach_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 手動検索用クエリ候補
    search_queries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 根拠サマリ（次に取る行動が分かる説明文）
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- AI 連絡先リサーチ（HTML 抽出で見つからない/低品質な場合の補完） ---
    # AI リサーチを実行済みか
    ai_researched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # AI が最有力とした主要メール（既存フィルタで再検証済みのもののみ）
    ai_primary_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI が見つけた問い合わせフォーム / 公式 SNS
    ai_contact_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_instagram_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_facebook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 候補メール [{email, score, confidence, reason, source_url}]（再検証後）
    ai_candidate_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # AI が提案する検索クエリ候補
    ai_search_queries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # AI が参照した出典 [{url, type, note}]
    ai_sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # AI の総合確度（0〜100）
    ai_confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AI の推奨連絡チャネル
    ai_recommended_channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # AI の補足メモ（メール未発見だがこのチャネルがおすすめ 等）
    ai_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生成エンジン（mock-contact-research-v1 / claude-...）
    ai_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ai_researched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- AI Web Research Mode（検索エンジン＋公式サイト横断クロールの実調査） ---
    # 実行済みか
    web_researched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # 実際に使用した検索プロバイダー（brave / serpapi / tavily / google_cse /
    # duckduckgo）。UI で「何で検索したか」を表示する。
    web_search_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # デバッグ集計（queries / results / crawled / ok / failed / excluded /
    # email_pages）。探索処理がどこまで進んだかを可視化する。
    web_debug_counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 探索フローの要約（"brave検索 → 18件取得 → 公式サイト → Contact → ..."）
    web_research_flow: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生成したキーワード候補（project_title / short_title / maker_name /
    # brand_names / official_domain など。検索戦略のデバッグ表示用）
    web_keyword_candidates: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 生成した検索クエリ全体（優先度順。実行したのは web_searched_queries）
    web_generated_queries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 検索結果のスコアリング履歴
    # [{query, url, title, score, kind, adopted, reason}]
    web_search_results: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 各クエリの検索診断
    # [{query, provider, status, reason, results, fallback, urls}]
    web_search_diagnostics: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 実行した検索クエリ / 探索した URL
    web_searched_queries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    web_searched_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 調査した候補ページ [{url, type}]
    web_candidate_pages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 発見した連絡先（既存フィルタ通過済み）
    # [{email, score, tier, email_owner, sources:[url]}]
    web_discovered_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    web_discovered_forms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    web_discovered_socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    web_discovered_pdfs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 代表値
    web_primary_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_primary_contact_form_url: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    web_recommended_channel: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    web_confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    web_evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_research_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    web_researched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- AI Document Reader（ページ全体を読解して連絡先を整理する追加レイヤー） ---
    doc_reader_researched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    doc_reader_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    doc_reader_official_company_name: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    doc_reader_brand_names: Mapped[list | None] = mapped_column(JSON, nullable=True)
    doc_reader_official_site_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{email, purpose, confidence, source_url, reason, email_owner}]
    doc_reader_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # [{url, confidence, source_url}]
    doc_reader_contact_forms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {platform: url}
    doc_reader_socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # [{name, title, linkedin_url, email, confidence, source_url, reason}]
    doc_reader_people: Mapped[list | None] = mapped_column(JSON, nullable=True)
    doc_reader_recommended_channel: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    doc_reader_recommended_contact: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    doc_reader_confidence_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    doc_reader_evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_reader_missing_info: Mapped[list | None] = mapped_column(JSON, nullable=True)
    doc_reader_sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    doc_reader_researched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- AI Search Agent（次に見るページを判断しながら反復探索する） ---
    search_agent_researched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    search_agent_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    search_agent_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 探索ステップ [{step, action, url/query, reason, found, ...}]
    search_agent_steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_agent_searched_queries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_agent_searched_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_agent_official_site_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{email, purpose, confidence, source_url, reason, email_owner}]
    search_agent_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_agent_contact_forms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_agent_socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    search_agent_people: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_agent_recommended_channel: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    search_agent_recommended_contact: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    search_agent_confidence_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    search_agent_evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_agent_stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_agent_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_agent_researched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Contact Intelligence v3：公式サイト再帰クロール（発見率強化） ---
    # 公式サイトが見つかった場合のみ実行。サイト全体を安全に再帰巡回し、メール・
    # フォーム・SNS・PDF・営業窓口を「実際に取得したページから」抽出する。
    # 実行できたか（公式サイト未発見なら False）
    recursive_crawl_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # 実際に巡回した URL / スキップした URL（login/cart/robots/深さ超過など）
    recursive_crawled_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recursive_skipped_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 抽出した連絡先（既存フィルタ通過済み）
    # [{email, score, tier, email_owner, sources:[url]}]
    recursive_emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recursive_forms: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recursive_socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 解析した PDF [{url, label, relevant, emails:int, text_len:int}]
    recursive_pdfs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # sitemap.xml から拾った優先 URL / robots.txt の Sitemap: 行
    recursive_sitemap_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recursive_robots_sitemaps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # DNS / MX / SPF / DMARC（メール運用の有無。公開メール未発見でも運用ありを表示）
    recursive_has_mx: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recursive_mx_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    recursive_spf_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    recursive_dmarc_record: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 失敗理由コード（OFFICIAL_SITE_NOT_FOUND / EMAIL_NOT_PUBLIC / CRAWL_BLOCKED 等）
    recursive_failure_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 再帰クロールの要約（UI・ログ用）
    recursive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recursive_crawled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # --- 営業推奨連絡先ランキング（DB 非保存。API が from_attributes で読む） ---
    @property
    def sales_contacts(self) -> list[dict]:
        """発見メールを営業のしやすさ順（星評価＋理由）に並べたランキング。"""
        from app.services.contact_discovery_service import build_sales_contacts

        return build_sales_contacts(self)
