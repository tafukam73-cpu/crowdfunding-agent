"""Contact Hunter AI が発見した「営業担当者候補」モデル。

会社単位ではなく「誰に送るか」を特定するための個人連絡先。Business Development /
Partnership / Export / Sales / Marketing / Founder などの役職を、出典 URL 付きで
保存する。AI が人名を捏造しないことが前提で、出典 URL を持つ人物だけを保存する。

実行のたびに案件の既存行を置き換える（最新の発見結果を保持）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ContactPerson(Base):
    __tablename__ = "contact_people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 氏名（出典ページに実在が確認できたものだけ。推測しない）
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 役職（例: Head of Business Development）
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 部署分類（Business Development / Partnership / Sales / Marketing / Founder ...）
    department: Mapped[str | None] = mapped_column(String(80), nullable=True)

    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # メール（既存フィルタ通過済みのもののみ）と、その出典種別
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 人物情報の出典 URL（必須。これが無い人物は保存しない）
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 信頼度（0〜100。LinkedIn/メール/役職が揃うほど高い）
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 営業優先度（0〜100。役職から決定的に算出）
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
