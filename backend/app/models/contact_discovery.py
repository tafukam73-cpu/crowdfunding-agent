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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
