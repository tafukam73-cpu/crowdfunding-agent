"""営業先連絡先探索モデル。

クラウドファンディングページだけでなく、メーカー公式サイト・問い合わせページ・
SNS から営業先候補（メール・問い合わせフォーム・SNS）を収集した結果を保存する。
取得失敗してもアプリは落とさず、status=failed で記録する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
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

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
