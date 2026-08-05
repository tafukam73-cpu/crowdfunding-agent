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
    """営業ワークフロー上の営業状況（案件単位パイプラインの単一正本）。

    既存の status（ProjectStatus）とは別軸で、調査→契約→輸入準備→日本クラファン
    準備→販売中→終了 までを一貫管理する。既存値は後方互換のため温存し、契約後
    フェーズ（contract_agreed 以降）を追加している。

    won は非推奨（後方互換のため enum には残すが、新規コードでは contract_agreed を
    正とする）。永続値の読み取り時は normalize_sales_status() で won→contract_agreed に
    正規化する。
    """

    not_started = "not_started"          # 未着手（営業前）
    ready = "ready"                      # 営業準備完了（連絡先取得済で送信可）
    contacted = "contacted"              # 初回営業済み（メール送信済み）
    awaiting_reply = "awaiting_reply"    # 返信待ち（フォロー期間中）
    replied = "replied"                  # 返信あり（返信登録済み）
    negotiating = "negotiating"          # 商談中（面談/やり取り）
    contract_agreed = "contract_agreed"  # 契約合意（独占販売権等合意）
    import_prep = "import_prep"          # 輸入準備（認証/物流/在庫手配）
    jp_cf_prep = "jp_cf_prep"            # 日本クラファン準備（Makuake 等）
    selling = "selling"                  # 販売中（日本販売開始）
    rejected = "rejected"                # 成約見送り（失注/見送り）
    closed = "closed"                    # 終了（案件クローズ）
    won = "won"                          # 【非推奨】旧「契約」。contract_agreed に正規化


# won（非推奨）→ contract_agreed への正規化マップ。永続値の読み取り時に適用する。
_SALES_STATUS_ALIASES: dict[str, str] = {
    SalesStatus.won.value: SalesStatus.contract_agreed.value,
}


def normalize_sales_status(value: str | None) -> str:
    """永続化された sales_status を正規化する（won → contract_agreed）。

    新規コードでは contract_agreed を正とする。未知値・None はそのまま返す。
    """
    if value is None:
        return SalesStatus.not_started.value
    return _SALES_STATUS_ALIASES.get(value, value)


# 契約確保（deal secured）以降の「契約後の進行中」フェーズ。
# 新規営業対象（ranking/today/copilot）からは外すが、dashboard 等の件数には含める。
SALES_STATUS_SECURED: tuple[str, ...] = (
    SalesStatus.contract_agreed.value,
    SalesStatus.import_prep.value,
    SalesStatus.jp_cf_prep.value,
    SalesStatus.selling.value,
)

# 完全に決着した終端状態（見送り・終了）。
SALES_STATUS_CLOSED: tuple[str, ...] = (
    SalesStatus.rejected.value,
    SalesStatus.closed.value,
)

# 新規営業アクションの対象外（契約後 or 決着）。旧実装の {won, rejected} を置き換える。
SALES_STATUS_DONE: tuple[str, ...] = SALES_STATUS_SECURED + SALES_STATUS_CLOSED

# 許可する状態遷移（正規化後の値で判定）。各状態から「次に進める」∪「戻せる」の集合。
# 厳密な状態機械：ここに無い遷移は不正（API/サービスで 409/ValueError）。
SALES_STATUS_TRANSITIONS: dict[str, set[str]] = {
    SalesStatus.not_started.value: {
        SalesStatus.ready.value, SalesStatus.contacted.value, SalesStatus.rejected.value,
    },
    SalesStatus.ready.value: {
        SalesStatus.contacted.value, SalesStatus.rejected.value,
        SalesStatus.not_started.value,
    },
    SalesStatus.contacted.value: {
        SalesStatus.awaiting_reply.value, SalesStatus.replied.value,
        SalesStatus.rejected.value, SalesStatus.ready.value,
    },
    SalesStatus.awaiting_reply.value: {
        SalesStatus.replied.value, SalesStatus.rejected.value,
        SalesStatus.contacted.value,
    },
    SalesStatus.replied.value: {
        SalesStatus.negotiating.value, SalesStatus.rejected.value,
        SalesStatus.awaiting_reply.value,
    },
    SalesStatus.negotiating.value: {
        SalesStatus.contract_agreed.value, SalesStatus.rejected.value,
        SalesStatus.replied.value,
    },
    SalesStatus.contract_agreed.value: {
        SalesStatus.import_prep.value, SalesStatus.rejected.value,
        SalesStatus.negotiating.value,
    },
    SalesStatus.import_prep.value: {
        SalesStatus.jp_cf_prep.value, SalesStatus.selling.value,
        SalesStatus.contract_agreed.value,
    },
    SalesStatus.jp_cf_prep.value: {
        SalesStatus.selling.value, SalesStatus.import_prep.value,
    },
    SalesStatus.selling.value: {
        SalesStatus.closed.value, SalesStatus.jp_cf_prep.value,
    },
    SalesStatus.rejected.value: {
        SalesStatus.not_started.value,  # 復活
    },
    SalesStatus.closed.value: {
        SalesStatus.selling.value,  # 終了の取り消し
    },
}


def can_transition_sales_status(from_status: str | None, to_status: str) -> bool:
    """from → to の sales_status 遷移が許可されているか（正規化後で判定）。

    同一状態への遷移（冪等）は常に許可する。won 等の別名は正規化してから判定する。
    """
    src = normalize_sales_status(from_status)
    dst = normalize_sales_status(to_status)
    if src == dst:
        return True
    return dst in SALES_STATUS_TRANSITIONS.get(src, set())


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

    # --- 営業対象除外判定キャッシュ（Lead Qualification Engine） ---
    # 判定の正本は lead_qualifications（追記専用の履歴）。ここには一覧の
    # フィルタ/ソート用に「最新の判定」だけを持つ（上書き更新してよい）。
    # blocked / review / clear。未判定は NULL。
    #
    # **保持するのは最新の pre_research 判定だけ。** pre_outreach（送信可否）の
    # 判定は履歴にのみ残し、この 2 列は更新しない。送信可否は案件一覧の絞り込み
    # 軸ではなく、送信直前に判定するものだから。
    lead_qualification_decision: Mapped[str | None] = mapped_column(
        String(12), nullable=True, index=True
    )
    lead_qualification_at: Mapped[datetime | None] = mapped_column(
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
