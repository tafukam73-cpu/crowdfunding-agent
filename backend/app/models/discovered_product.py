"""発掘商品候補モデル（Discovery Engine v1-1）。

海外クラウドファンディング等から発掘した「日本展開できそうな商品候補」を保存する
土台。今回は保存・一覧取得のみで、AI スコアリング・実サイト取得・フロント画面は
まだ実装しない（スコア系カラムは将来の AI 評価で埋める前提で nullable）。

source_url をユニークキーとして重複登録を防ぐ。Contact Intelligence（連絡先探索）
とは contact_discovery_id で緩く紐づけられる（任意）。
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DiscoverySourcePlatform(str, enum.Enum):
    """発掘元プラットフォーム。"""

    kickstarter = "kickstarter"
    indiegogo = "indiegogo"
    wadiz = "wadiz"              # 韓国発（プレオーダー/リワード型）
    zeczec = "zeczec"            # 台湾発（嘖嘖・デザイン/ガジェット雑貨）
    ulule = "ulule"             # フランス発（サステナブル/エコ/デザイン雑貨）
    backerkit = "backerkit"
    backertracker = "backertracker"
    crowdsupply = "crowdsupply"
    gamefound = "gamefound"
    producthunt = "producthunt"
    manual = "manual"
    other = "other"


class DiscoveredProductStatus(str, enum.Enum):
    """商品候補（キャンペーン）の状態。"""

    live = "live"                # 掲載中
    successful = "successful"    # 成功
    ended = "ended"              # 終了
    failed = "failed"            # 未達
    canceled = "canceled"        # 中止
    preorder = "preorder"        # 予約受付
    unknown = "unknown"          # 不明


class DiscoveredProduct(Base):
    __tablename__ = "discovered_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- 発掘元 ---
    source_platform: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=DiscoverySourcePlatform.other.value,
        server_default=DiscoverySourcePlatform.other.value,
        index=True,
    )
    # 重複登録を防ぐユニークキー
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)

    # --- 基本情報 ---
    project_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    creator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # --- キャンペーン状態 / 資金情報 ---
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DiscoveredProductStatus.unknown.value,
        server_default=DiscoveredProductStatus.unknown.value,
        index=True,
    )
    funding_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    funding_goal: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    backers_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 調達額の通貨（KRW / TWD / EUR / USD 等）。null 可。金額の解釈を誤らないために保存する
    # （KRW / TWD を USD/JPY として誤解しないよう、達成率は funding_amount/goal の比率で扱う）。
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # --- 掲載期間 ---
    launch_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- 営業先情報 ---
    official_website_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 発掘元の生データ（必要最小限。source 固有フィールドの根拠保管） ---
    # 例: Wadiz {amount, rate, participants, categoryName, makerName}、
    #     Zeczec {amount, rate, backers, enrichment...}。推測値は入れない。
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- AI 発掘スコア（将来の AI 評価で付与。今回は nullable のまま） ---
    japan_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crowdfunding_fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    novelty_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logistics_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regulatory_risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    competition_risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    japan_entry_risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 総合発掘スコア（0〜100 想定。一覧の既定ソートキー）
    overall_discovery_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    discovery_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_next_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Contact Intelligence（連絡先探索）への緩い紐づけ（任意） ---
    contact_discovery_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
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
