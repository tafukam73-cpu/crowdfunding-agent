"""Wadiz 手動取り込み（人間のブラウザで開いた公開ページ情報の取り込み）モデル。

Wadiz 詳細ページは Akamai により自動取得できないため、ユーザーが通常の Chrome で
閲覧・「もっと見る」展開した後の公開情報（本文/HTML）を貼り付け、その中から
メール・メーカー情報・公式サイト・SNS を抽出して取り込む。

- 保存は「確認して保存（confirm）」時のみ。プレビューは保存しない。
- 冪等：同一案件で同じ raw_content_hash の取り込みは重複作成しない。
- 生本文は保存せず、ハッシュと抽出結果（証拠スニペット含む）だけを保持する。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WadizImport(Base):
    __tablename__ = "wadiz_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    # 取り込み元本文のハッシュ（冪等判定）。生本文は保存しない。
    raw_content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    imported_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 抽出・保存した内容（emails/socials/official/maker/excluded/evidence）
    extracted_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    email_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
