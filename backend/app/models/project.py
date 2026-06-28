"""海外クラファン案件モデル。

要件定義「3.1 取得項目」に対応したカラムを持つ。
AI 評価関連のカラムは Step 4 で追加する。
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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


class SourceSite(str, enum.Enum):
    """収集元サイト。"""

    kickstarter = "kickstarter"
    indiegogo = "indiegogo"
    wadiz = "wadiz"
    ulule = "ulule"             # フランス発（サステナブル/エコ/デザイン雑貨に強い）
    makuake = "makuake"
    greenfunding = "greenfunding"
    other = "other"


# 営業対象（海外）サイト。projects テーブルに保存・一覧表示する対象。
SALES_TARGET_SITES: list[SourceSite] = [
    SourceSite.kickstarter,
    SourceSite.indiegogo,
    SourceSite.wadiz,
    SourceSite.ulule,
]

# 日本の成功事例（比較用）サイト。営業対象ではなく、japanese_success_projects
# にのみ保存する。projects には保存しない。
JAPANESE_SUCCESS_SITES: list[SourceSite] = [
    SourceSite.makuake,
    SourceSite.greenfunding,
]


class ProjectStatus(str, enum.Enum):
    """営業進捗ステータス。"""

    new = "new"               # 新規
    reviewing = "reviewing"   # 検討中
    contacted = "contacted"   # 連絡済み
    negotiating = "negotiating"  # 交渉中
    won = "won"               # 獲得（独占販売権交渉成立 など）
    rejected = "rejected"     # 見送り


class SalesStatus(str, enum.Enum):
    """営業ワークフロー上の営業状況。

    既存の status（ProjectStatus）とは別軸で、営業ワークフローカードが案内する
    「次に何をするか」の進捗を表す。
    """

    not_started = "not_started"        # 未営業
    ready = "ready"                    # 営業準備完了
    contacted = "contacted"            # 営業済み
    awaiting_reply = "awaiting_reply"  # 返信待ち
    replied = "replied"                # 返信あり
    negotiating = "negotiating"        # 商談中
    won = "won"                        # 契約
    rejected = "rejected"              # 見送り


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- 基本情報 ---
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_site: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- メディア ---
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 資金情報 ---
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    goal_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    raised_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    backers_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- 掲載期間 ---
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # --- メーカー / 営業先情報 ---
    maker_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    maker_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    # CRM のメーカー（営業先企業）への紐づけ。未リンクなら null。
    maker_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # --- 日本未上陸判定キャッシュ（最新判定。一覧表示用） ---
    latest_availability: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    latest_availability_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- 営業ステータス ---
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProjectStatus.new.value, index=True
    )

    # --- 営業ワークフロー上の営業状況（未営業→営業準備完了→営業済み→…） ---
    sales_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SalesStatus.not_started.value,
        server_default=SalesStatus.not_started.value,
        index=True,
    )

    # --- AI 評価キャッシュ（最新評価。一覧のソート/フィルタ用） ---
    latest_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    latest_recommendation: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )

    # --- メタ ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # --- 表示用の派生プロパティ（DB 非保存。ProjectOut が from_attributes で読む） ---
    @property
    def description_clean(self) -> str | None:
        """HTML を除去した読みやすい概要（UI 表示用）。"""
        from app.ai.ulule import clean_description

        return clean_description(self.description)

    @property
    def _ulule_product(self) -> dict | None:
        """Ulule 案件のみ商品性判定を返す（それ以外は None）。"""
        from app.ai.ulule import is_ulule, product_assessment

        if not is_ulule(self):
            return None
        return product_assessment(self)

    @property
    def physical_product_score(self) -> int | None:
        pa = self._ulule_product
        return pa["physical_product_score"] if pa else None

    @property
    def sales_target_score(self) -> int | None:
        pa = self._ulule_product
        return pa["sales_target_score"] if pa else None

    @property
    def is_sales_target_candidate(self) -> bool:
        """営業対象候補か。Ulule 以外は常に True（既存の営業対象サイト）。"""
        pa = self._ulule_product
        return pa["is_sales_target_candidate"] if pa else True
