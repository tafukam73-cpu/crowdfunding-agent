"""営業メール下書きモデル。

自動送信はしない。下書きを生成・保存し、画面で確認/コピーするためのもの。
1 案件に対し種別ごと・生成回ごとに履歴を残す。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
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
    # subject は「実際に採用された件名」（後方互換のため必須のまま維持）
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # --- 営業メール品質向上で追加（いずれも任意・後方互換） ---
    # 件名候補（3案）を JSON 配列で保存
    subject_options: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 利用者が実際に選択した件名（既定は候補の先頭。subject と同期する）
    selected_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生成時に選択したトーン（professional / friendly / executive / short / detailed）
    tone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 送信前確認用の日本語要約
    japanese_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- パーソナライズ（商品・メーカーごとの個別化）。いずれも任意・後方互換 ---
    # 生成前に作った個別化材料一式（product_name / key_features 等）
    personalization_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 商品ごとに変わる具体的な称賛の一文
    personalized_compliment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # UI 表示用の注目ポイント（実績＋特徴の要約リスト）
    product_highlights: Mapped[list | None] = mapped_column(JSON, nullable=True)

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
