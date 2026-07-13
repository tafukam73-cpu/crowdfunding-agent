"""営業実行パイプライン（sales_outreach）モデル。

Discovery → Promote → Contact Intelligence → 営業実行 を一本化する最終段。
案件（projects）1 件につき 1 本の営業アウトリーチ・レコードを持ち、下書き生成から
送信・返信・商談・成約までのライフサイクルを追跡する。

既存の EmailDraft（3 種別の英語下書き）や SalesOpportunity（Contact Intelligence
単位の営業案件）とは別軸で、「今日どの案件をどの言語で営業するか」を実行管理する。
自動送信はしない（生成のみ）。実際の送信は既存の Gmail compose を再利用する。

方針:
- 1 project = 1 outreach（project_id は unique）。二重の下書き作成を防ぐ（要件 9）。
- 4 言語（英語/韓国語/中国語/日本語）を生成し、推奨言語を generated_* に採用しつつ
  全言語を generated_variants(JSON) に保持する。
- 生成（外部 Claude 呼び出しを含みうる重い処理）は背景ジョブで行い、GET/POST 内で
  同期実行しない（idle in transaction / 12 秒タイムアウト回帰の防止）。
"""
from __future__ import annotations

import enum
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


class OutreachStatus(str, enum.Enum):
    draft = "draft"                # 下書き（生成済み・未送信）
    sent = "sent"                  # 送信済み
    opened = "opened"              # 開封
    replied = "replied"            # 返信あり
    negotiating = "negotiating"    # 商談中
    contract = "contract"          # 成約
    lost = "lost"                  # 失注


class SalesOutreach(Base):
    __tablename__ = "sales_outreach"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 案件 1 件につき 1 本（二重下書き防止のため unique）。
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    outreach_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OutreachStatus.draft.value,
        server_default=OutreachStatus.draft.value,
        index=True,
    )
    # 営業実行優先度（0〜100）。生成時点のスコアを記録する。
    priority_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # --- 生成された営業メール（推奨言語を採用） ---
    generated_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 4 言語（en/ko/zh/ja）の全バリアント {lang: {subject, subject_options, body}}。
    generated_variants: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- ライフサイクルのタイムスタンプ ---
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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
