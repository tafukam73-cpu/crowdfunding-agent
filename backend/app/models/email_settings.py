"""メール設定モデル（差出人・会社情報・署名テンプレート）。

営業メール生成時に会社情報を AI コンテキストへ渡し、本文末尾へ署名を
自動挿入するために使う。利用者は 1 人前提のため、レコードは 1 件のみ
（id=1）を upsert で運用する。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailSettings(Base):
    __tablename__ = "email_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 会社・差出人情報
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI へ渡す会社紹介文（長い場合は生成時にトリム）
    company_profile: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 本文末尾へ連結する署名テンプレート（プレースホルダ可。未設定なら既定値）
    signature_template: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
