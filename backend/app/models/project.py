"""海外クラファン案件モデル。

要件定義「3.1 取得項目」に対応したカラムを持つ。
AI 評価関連のカラムは Step 4 で追加する。
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    wadiz = "wadiz"             # 韓国発（プレオーダー/リワード型に強い）
    zeczec = "zeczec"           # 台湾発（嘖嘖・デザイン/ガジェット雑貨に強い）
    makuake = "makuake"
    greenfunding = "greenfunding"
    other = "other"


# 営業対象（海外）サイト。projects テーブルに保存・一覧表示する対象。
SALES_TARGET_SITES: list[SourceSite] = [
    SourceSite.kickstarter,
    SourceSite.indiegogo,
    SourceSite.wadiz,
    SourceSite.zeczec,
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
    # HTML 除去済みの読みやすい概要（UI 表示用）。description から生成して保存する。
    # 未生成（過去データ）の場合は null。その場合は表示側で description を sanitize する。
    description_clean: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # --- 詳細補完の根拠（再スクレイプで消えない補完情報の保管場所） ---
    # 詳細ページから確認できた creator URL / ブランド名 / 商品説明 / 公式サイト候補
    # （確度つき）/ SNS / 取得元 URL / 取得できなかった理由 などを JSON で保持する。
    # 一覧スクレイパーは ProjectCreate の項目のみ upsert するためこの列には触れない。
    enrichment: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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

    # --- 日本クラファン適性ゲート（メール探索の事前判定キャッシュ） ---
    # 判定ロジックは contact_search_gate。スコアは既存 sales_assessment の
    # makuake_fit（日本クラファン適性）を再利用し、ここには結果だけを保存する。
    eligible_for_contact_search: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, index=True
    )
    contact_search_gate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    japan_crowdfunding_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- AI 評価キャッシュ（最新評価。一覧のソート/フィルタ用） ---
    latest_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    latest_recommendation: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )

    # --- 営業対象外（ソフトデリート） ---
    # 営業価値の低い案件を一覧・ランキング・Today Tasks・Sales Copilot・営業対象一覧・
    # 送信後フォローから除外する。NULL なら対象内、値があれば対象外（除外した日時）。
    # 完全削除ではないため、関連する調査結果・営業履歴は温存する。復元は値を NULL に戻す。
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # 営業対象外にした理由（選択式ラベルまたは自由入力）。将来の分析用に保存する。
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # 海外クラファンの商品ページ URL。正規フィールドは source_url で、source_site と
    # 整合するものだけを campaign_url として公開する（公式サイトで代用しない）。
    @property
    def campaign_url(self) -> str | None:
        from app.services.campaign_url import campaign_url_of

        return campaign_url_of(self)

    @property
    def campaign_url_missing(self) -> bool:
        return self.campaign_url is None

    @property
    def campaign_url_missing_reason(self) -> str | None:
        from app.services.campaign_url import missing_reason

        return missing_reason(self)

    # メーカー/商品の公式サイト。campaign_url とは明確に分離する。
    @property
    def official_site_url(self) -> str | None:
        from app.services.campaign_url import official_site_url_of

        return official_site_url_of(self)

    # 営業対象外かどうか（archived_at が入っていれば対象外）。
    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


def not_archived_clause():
    """営業対象外（ソフトデリート済み）案件を除外する SQL 条件。

    一覧・ランキング・Today Tasks・Sales Copilot・営業対象一覧・送信後フォローなど、
    「通常の営業対象」を返す全クエリで共通して使う。
    """
    return Project.archived_at.is_(None)
