"""営業メール下書きモデル。

自動送信はしない。下書きを生成・保存し、画面で確認/コピーするためのもの。
1 案件に対し種別ごと・生成回ごとに履歴を残す。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailType(str, enum.Enum):
    initial_outreach = "initial_outreach"   # 初回営業
    exclusive_rights = "exclusive_rights"    # 独占販売権打診
    followup = "followup"                    # フォローアップ


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    email_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # 営業先は海外メーカー想定のため既定は英語
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")

    # 生成に使ったエンジン/モデル（mock-email-v1 / claude-...）
    model: Mapped[str] = mapped_column(String(60), nullable=False)

    # メールプロバイダーに下書きを作成した場合の記録（未作成なら null）
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provider_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
