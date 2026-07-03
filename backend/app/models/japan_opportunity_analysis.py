"""日本市場機会 分析モデル（Japan Opportunity Engine v1-2）。

Discovery Engine で発掘した商品（discovered_products）を、日本市場向けに評価した
「機会（opportunity）」の分析結果を保存する土台。1 発掘商品につき複数回の分析を
履歴として持てる（最新を取得できる）。

v1-2 では保存・取得の土台のみ。ルールベース評価・AI 評価・実検索・フロント統合は
後続バージョン（v1-3 以降）で実装する前提で、スコア系カラムは nullable。

設計方針（docs/japan_opportunity_engine_strategy.md 準拠）：
- スコアはすべて 0〜100・高いほど日本展開/営業に有利（リスク系も「高い=安全」）。
- 根拠（各 summary / reasoning / evidence_json）と confidence を保持する。
- discovered_product_id で発掘商品に紐づく（既存テーブルは変更しない）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JapanOpportunityAnalysis(Base):
    __tablename__ = "japan_opportunity_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 発掘商品への紐づけ（discovered_products.id）。商品削除時は分析も削除。
    discovered_product_id: Mapped[int] = mapped_column(
        ForeignKey("discovered_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- 評価軸（0〜100・高いほど有利。未評価は nullable のまま） ---
    japan_market_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    japan_entry_gap_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crowdfunding_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retail_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regulatory_safety_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logistics_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    margin_potential_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competition_gap_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sales_success_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 総合 Japan Opportunity Score（一覧の既定ソートキー）
    overall_opportunity_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    # 総合 confidence（0〜100。情報が薄いほど低い）
    confidence_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- 根拠テキスト ---
    japan_presence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    competition_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    regulatory_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    logistics_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pricing_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    opportunity_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_next_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 根拠明細（軸別 confidence・参照ソース等。dict/list を安全に保存） ---
    evidence_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
