"""Lead Qualification Engine（営業対象除外エンジン）— PR-1: 判定コア。

「営業してはいけない案件」を **Evidence 付き** で判定する。CLAUDE.md §1 のとおり、
無駄な調査・無駄なメール送信・誤った連絡先を減らすことがこのモジュールの価値であり、
返信率・成功確率・可能性スコアの類は **一切算出しない**。

## このモジュールが守る不変条件

1. **外部 HTTP を行わない。** 探索は既存ジョブ（Contact Intelligence / japan_sales_check）の
   仕事であり、LQE は既に取れている証跡を読んで判定するだけ。同期呼び出しで安全。
2. **DB へ書き込まない。** PR-1 は純粋関数のみ（永続化は PR-2）。
3. **URL を組み立てない。** 証跡は「見つけた URL」だけ。`https://<maker>.com` の類は禁止。
4. **Evidence の無い blocker は作れない。** `claim / source_url / checked_at / method` の
   4 点セットが揃わない所見は、集約時に自動で review へ降格する。
5. **数値スコアを作らない。** confidence は `high / medium / low / unverified` のラベルのみ
   （evidence-ledger の語彙。独自基準を作らない）。点数の合算による判定も行わない。
6. **decision は「最も重い Finding」で決まる。**

## 2 つのステージ

同じ所見でも「調査してよいか」と「送ってよいか」で必要な厳しさが違う。

- ``pre_research``  … Contact Intelligence を走らせる価値があるか（無駄な調査を減らす）
- ``pre_outreach``  … 営業メールを送ってよいか（誤送信を防ぐ）

例: 代理店出品（D）は pre_research では review（本当のメーカーを探す価値がある）だが、
pre_outreach では blocker（代理店に独占交渉メールを送るのは誤送信）。

## signals（入力）の契約

``qualify()`` は DB に触らない。呼び出し側が以下の形の dict を組み立てて渡す
（組み立ては PR-2 の責務）。すべてのキーは任意で、欠けていれば
「証跡が無い」＝ ``insufficient_evidence`` として扱う（推測で埋めない）。

```
project_id                  int
title / description / category            str | None
campaign_url                str | None      商品ページ URL（official_site で代用しない）
campaign_url_missing_reason str | None
japanese_summary            str | None      product_context_service の日本語概要
end_date                    date | datetime | None
maker_name                  str | None

official_site   {"url","verified":bool,"source_url","checked_at","method"} | None
maker_identity  {"verified":bool,"source_url","checked_at","method"} | None
creator_domain  {"url","ownership_class","checked_at","method"} | None
                  ownership_class は source_ownership.classify_domain の分類値

japan_sales     {"status","result","confidence","source_urls":[...],
                 "checked_at","channels":[{"channel","status","search_url","label"}]} | None
                  result は interpret_japan_check の sold_in_japan /
                  not_found_in_japan / inconclusive

japan_cf_listings    [{"url","checked_at","method","excerpt"}]  日本クラファン掲載
other_brand_listings [{"url","checked_at","method","excerpt"}]  別ブランドでの流通（C）
global_listings      [{"url","checked_at","method","excerpt"}]  大量流通の販売ページ（R）
oem_notice           {"source_url","checked_at","method","excerpt"} | None
discontinued_notice  {"source_url","checked_at","method","excerpt"} | None

business_emails  [{"email","source_url","checked_at","method","role"}]
decision_makers  [{"name","source_url","checked_at","method"}]
contact_form_url str | None

business_facts   dict   将来の priority_band 用の事実（BUSINESS_VALUE_FACT_KEYS 参照）
```
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

RULE_VERSION = "lqe-v1"
ENGINE_NAME = "rule-based-lead-qualification-v1"

# --------------------------------------------------------------------------- #
#  型・定数
# --------------------------------------------------------------------------- #
STAGE_PRE_RESEARCH = "pre_research"
STAGE_PRE_OUTREACH = "pre_outreach"
STAGES: tuple[str, ...] = (STAGE_PRE_RESEARCH, STAGE_PRE_OUTREACH)

DECISION_BLOCKED = "blocked"   # 営業停止
DECISION_REVIEW = "review"     # 要レビュー
DECISION_CLEAR = "clear"       # 営業可能
DECISIONS: tuple[str, ...] = (DECISION_BLOCKED, DECISION_REVIEW, DECISION_CLEAR)

VERDICT_HIT = "hit"
VERDICT_NO_HIT = "no_hit"
VERDICT_INSUFFICIENT = "insufficient_evidence"
VERDICT_STALE = "stale"
VERDICTS: tuple[str, ...] = (
    VERDICT_HIT, VERDICT_NO_HIT, VERDICT_INSUFFICIENT, VERDICT_STALE,
)

SEVERITY_BLOCKER = "blocker"
SEVERITY_REVIEW = "review"
SEVERITY_INFO = "info"
SEVERITIES: tuple[str, ...] = (SEVERITY_BLOCKER, SEVERITY_REVIEW, SEVERITY_INFO)
_SEVERITY_RANK = {SEVERITY_BLOCKER: 0, SEVERITY_REVIEW: 1, SEVERITY_INFO: 2}

# confidence は evidence-ledger の語彙をそのまま使う。**数値 confidence は作らない。**
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_UNVERIFIED = "unverified"
CONFIDENCES: tuple[str, ...] = (
    CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW, CONFIDENCE_UNVERIFIED,
)

# 取得元の種類（product_facts_service の語彙に揃える）
SOURCE_CAMPAIGN = "クラファン商品ページ"
SOURCE_OFFICIAL = "メーカー公式サイト"
SOURCE_EC = "ECサイト"
SOURCE_JP_CROWDFUNDING = "日本クラファンサイト"
SOURCE_DISTRIBUTOR = "代理店・販売店サイト"
# DB の状態そのものを指す内部ロケータ専用の source_kind。**外部 URL と必ず区別する。**
# この source_kind を持つ証跡は外部 Web リンクではないため、UI でクリック可能な
# リンクとして表示してはならない（表示側の扱いは PR-6）。
SOURCE_INTERNAL_DB = "internal_db"

# 取得方法（evidence-ledger の method）
METHOD_CAMPAIGN_PARSE = "campaign_page_parse"
METHOD_DB_STATE = "db_state"
METHOD_JAPAN_SALES_CHECK = "japan_sales_check"
METHOD_CLASSIFY_DOMAIN = "classify_domain"

# --- 企業役割（B: OEM・企業役割の補助属性）------------------------------------ #
# **証拠が無ければ unknown。** OEM / ODM / private_label を推測だけで blocker にしない。
ROLE_BRAND_OWNER = "brand_owner"
ROLE_MANUFACTURER = "manufacturer"
ROLE_FACTORY = "factory"
ROLE_OEM = "oem"
ROLE_ODM = "odm"
ROLE_PRIVATE_LABEL = "private_label"
ROLE_DISTRIBUTOR = "distributor"
ROLE_RETAILER = "retailer"
ROLE_IMPORTER = "importer"
ROLE_AGENCY = "agency"
ROLE_UNKNOWN = "unknown"
ENTITY_ROLES: tuple[str, ...] = (
    ROLE_BRAND_OWNER, ROLE_MANUFACTURER, ROLE_FACTORY, ROLE_OEM, ROLE_ODM,
    ROLE_PRIVATE_LABEL, ROLE_DISTRIBUTOR, ROLE_RETAILER, ROLE_IMPORTER,
    ROLE_AGENCY, ROLE_UNKNOWN,
)

# source_ownership.classify_domain の分類値 → 企業役割。
# ここに無い分類は unknown（推測で役割を決めない）。
_OWNERSHIP_TO_ROLE: dict[str, str] = {
    "distributor": ROLE_DISTRIBUTOR,
    "retailer": ROLE_RETAILER,
    "agency": ROLE_AGENCY,
    "maker_official": ROLE_BRAND_OWNER,
    "maker_subdomain": ROLE_BRAND_OWNER,
}
# 第三者出品（D の対象）とみなす役割。
_RESELLER_ROLES = (ROLE_DISTRIBUTOR, ROLE_RETAILER, ROLE_AGENCY, ROLE_IMPORTER)

# --- A. Business Value（将来の priority_band 用の「事実」の器）----------------- #
# **PR-1〜3 では優先度判定ロジック・DB 列・UI を実装しない。** ここにあるのは
# 「どの事実を集約しうるか」の定義だけ。営業成功可能性・返信可能性へ変換しない。
BUSINESS_VALUE_FACT_KEYS: tuple[str, ...] = (
    "backers_count", "raised_amount", "achievement_rate", "comments_count",
    "updates_count", "campaign_state", "official_site_verified",
    "decision_maker_confirmed", "business_email_available",
    "japan_official_sales", "japan_distributor", "past_campaigns",
    "social_presence_confirmed", "legal_entity_confirmed",
)
# 将来 priority_band が取りうる値（**算出ロジックは未実装**）。
PRIORITY_BAND_VALUES: tuple[str, ...] = (
    "high", "medium", "low", "insufficient_evidence",
)

# --- C. positive_facts（営業する根拠。PR-1 の初期対応 8 種） -------------------- #
# 事実が確認できた場合のみ追加する。**存在しない証拠を補完しない。**
# 「日本で売れる」「返信が期待できる」等の推測文は作らない。
POSITIVE_FACT_LABELS: dict[str, str] = {
    "campaign_url_verified": "商品ページURLを確認済み",
    "physical_product_confirmed": "物理商品であることを商品ページ上で確認",
    "maker_name_present": "メーカー名を保持している",
    "official_site_verified": "公式サイトを検証済み",
    "maker_identity_verified": "メーカー本人（法人・ブランド）を同定済み",
    "business_contact_found": "営業に使える連絡先を取得済み",
    "decision_maker_found": "意思決定者を実在確認済み",
    "japan_sales_check_completed": "日本販売状況チェックを完了済み",
}

# --- 証跡の鮮度（evidence-ledger の期限。カテゴリ別）--------------------------- #
_FRESHNESS_DAYS_DEFAULT = 365
_FRESHNESS_DAYS: dict[str, int] = {
    "C": 180,   # 別ブランド流通の確認
    "F": 90,    # 日本展開の有無
    "Q": 7,     # キャンペーン状態
    "R": 90,    # 流通状況
}

# --------------------------------------------------------------------------- #
#  語彙（既存実装から借りる／LQE 固有の細分化）
# --------------------------------------------------------------------------- #
# N/O/P は contact_search_gate の非物理語彙を **細分化して** 使う。語彙そのものは
# 複製せず、遅延 import で取得する（PR-3 で gate が LQE を呼ぶため循環を避ける）。
#
# N デジタル・コンテンツ商品 / O ソフトウェアのみ / P サービスのみ
_STRONG_BUCKET: dict[str, tuple[str, ...]] = {
    "O": ("mobile app", "saas", "software only", "web service", "ソフトウェア"),
    "N": ("documentary", "short film", "video game", "board game", "tabletop",
          "映画", "書籍"),
    "P": ("subscription service", "サブスクリプション", "concert", "festival",
          "exhibition", "donation", "charity", "fundraiser", "nonprofit", "ngo",
          "scholarship", "membership", "coaching", "consulting", "retreat",
          "workshop", "寄付", "募金", "講座"),
}
# LQE 固有の追加語（gate には無いが N を明確に示すもの）。
_EXTRA_STRONG: dict[str, tuple[str, ...]] = {
    "N": ("nft", "e-book", "ebook", "audiobook", "digital download",
          "電子書籍", "ダウンロード版"),
    "O": (),
    "P": (),
}
# WEAK 語のバケット割当（STRONG が無く物理商品語も無いときだけ使う）。
_WEAK_BUCKET: dict[str, tuple[str, ...]] = {
    "O": ("app", "plugin", "アプリ", "앱"),
    "N": ("movie", "film", "game", "book", "novel", "comic", "manga", "album",
          "music", "song", "ゲーム", "音楽", "게임", "음악"),
    "P": ("event", "course", "イベント"),
}

# A で見る「輸入規制負担が大きい」カテゴリ（category_keywords の CAUTION の部分集合）。
# 無線・電池は G〜M 側で個別に扱うため、ここには含めない（二重計上を避ける）。
_A_HEAVY_CATEGORIES: tuple[str, ...] = (
    "medical", "supplement", "food", "cosmetics", "nicotine", "alcohol",
    "chemical", "weapon", "knife",
)
# 大型・重量物（A）。輸入・物流の負担が大きい。
_BULKY_HINTS: tuple[str, ...] = (
    "furniture set", "sofa", "mattress", "e-bike", "ebike", "electric bike",
    "scooter", "vehicle", "kayak", "tent house", "shed", "refrigerator",
    "washing machine", "大型家具", "電動自転車", "冷蔵庫", "洗濯機",
)
# 非物販（B）。Makuake の物販モデルに乗らない企画。
_NON_PRODUCT_HINTS: tuple[str, ...] = (
    "service", "course", "workshop", "coaching", "membership", "subscription",
    "charity", "donation", "nonprofit", "ngo", "retreat", "consulting",
    "体験", "講座", "募款", "捐款", "公益", "計画", "計畫",
)
# OEM / ODM / private label を示す語（C）。**語だけでは blocker にしない。**
_OEM_TERMS: tuple[str, ...] = (
    "oem", "odm", "private label", "white label", "contract manufacturer",
    "sourced from", "rebrand", "リブランド", "受託製造",
)
# 大手・グローバルブランド（R）。**名前一致だけでは blocker にしない。**
_MEGA_BRAND_HINTS: tuple[str, ...] = (
    "sony", "samsung", "panasonic", "philips", "bosch", "xiaomi", "anker",
    "asus", "acer", "lenovo", "dell", "microsoft", "google", "apple", "huawei",
    "nintendo", "canon", "nikon", "bose", "jbl", "logitech", "dyson",
)
# 終売の一次証拠となる文言（Q）。
_DISCONTINUED_TERMS: tuple[str, ...] = (
    "discontinued", "no longer available", "sold out permanently",
    "production ended", "販売終了", "生産終了",
)
# 規制カテゴリ（G〜M）。語彙は category_keywords を正本として遅延 import する。
# ここでは「どの CAUTION カテゴリを、どの LQE コードへ割り当てるか」だけを持つ。
_REGULATORY_MAP: dict[str, tuple[str, ...]] = {
    "H": ("food", "supplement"),
    "I": ("medical",),
    "J": ("cosmetics",),
    "K": ("wireless", "radio"),
    "L": ("large battery",),
}
_REGULATORY_LAW: dict[str, str] = {
    "H": "食品衛生法",
    "I": "薬機法",
    "J": "薬機法",
    "K": "電波法",
    "L": "電気用品安全法（PSE）",
    "M": "電波法（技術基準適合証明）",
}

# 日本での販売を確認できたとみなすチャネル（interpret_japan_check と同じ考え方）。
_JAPAN_PRESENCE_CHANNELS = ("distributor", "subsidiary", "amazon", "rakuten", "yahoo")
_JAPAN_CF_CHANNELS = ("makuake", "greenfunding", "green")

# 日本語概要としてこの文字数未満は「商品内容が判別できない」（gate と同じ閾値）。
MIN_SUMMARY_LEN = 20


# --------------------------------------------------------------------------- #
#  ユーティリティ
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _has_term(text: str, term: str) -> bool:
    """語が含まれるかを判定する（単語境界照合）。

    ラテン文字を含む語は単語境界で照合する（"app" が "companion app" に一致し、
    "application" や "happy" には一致しない）。日本語など単語境界の概念が無い語は
    部分一致で照合する。``contact_search_gate._has_term`` と同じ規則。
    部分一致による誤ヒット（`x.com` が `brandx.com` に当たる類）を防ぐため、
    **自前の素朴な `in` 判定を書かない**こと。
    """
    if not term:
        return False
    if any("a" <= ch <= "z" for ch in term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def _matched(text: str, terms: Iterable[str]) -> list[str]:
    return [t for t in terms if _has_term(text, t)]


def _text_of(signals: dict) -> str:
    return " ".join(
        str(signals.get(k) or "")
        for k in ("title", "description", "category")
    ).lower()


def _as_datetime(value: Any) -> datetime | None:
    """checked_at を tz-aware な datetime に正規化する。解釈できなければ None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _iso(value: Any) -> str | None:
    dt = _as_datetime(value)
    return dt.isoformat() if dt is not None else None


def _gate_vocab() -> Any:
    """contact_search_gate を **遅延 import** して返す。

    PR-3 で gate 側が LQE を呼ぶため、module レベルで import すると循環する。
    非物理語彙（STRONG / WEAK / 物理商品語）の正本は gate 側に置いたままにし、
    LQE では複製しない。
    """
    from app.services import contact_search_gate

    return contact_search_gate


def _caution_categories(text: str) -> list[str]:
    """category_keywords の CAUTION カテゴリ一致（正本を呼ぶだけ）。"""
    from app.services.category_keywords import CAUTION_KEYWORDS, match_categories

    return match_categories(text, CAUTION_KEYWORDS)


def _caution_terms(text: str, category: str) -> list[str]:
    """CAUTION カテゴリのうち、実際にページ上に現れた根拠語を返す。"""
    from app.services.category_keywords import CAUTION_KEYWORDS

    return [t for t in CAUTION_KEYWORDS.get(category, ()) if t in text]


# --------------------------------------------------------------------------- #
#  データ構造
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Evidence:
    """1 件の証跡。evidence-ledger の 4 点セット＋抜粋。

    ``source_url`` に使ってよいのは「実際に取得したページの URL」だけ。
    組み立てた URL（``https://<maker>.com`` の類）は使わない。

    例外として、DB の状態そのものが証拠になる場合（campaign_url が保存されていない、
    maker identity が未確定 など）に限り ``method="db_state"`` ＋
    ``source_kind="internal_db"`` ＋ ``source_url="db://projects/<id>..."`` を使う。
    これは推測 URL ではなく「その行を見れば確認できる」内部ロケータである。

    内部ロケータの取り扱い（固定事項）:
      - ``db://`` を使うのは ``method="db_state"`` のときだけ
      - その ``source_kind`` は必ず ``internal_db``（外部 URL と区別する）
      - 外部 Web リンクとして扱わない／UI でクリック可能に表示しない
      - **外部証跡の代用にしない**（外部で確認すべき事実を db:// で置き換えない）
      - maker 名等から推測生成しない（``db://projects/<id>`` 以外を作らない）
    """

    claim: str
    source_url: str | None = None
    source_kind: str | None = None
    method: str | None = None
    checked_at: datetime | None = None
    excerpt: str | None = None

    def is_complete(self) -> bool:
        """4 点セット（claim / source_url / checked_at / method）が揃っているか。"""
        return bool(
            (self.claim or "").strip()
            and (self.source_url or "").strip()
            and (self.method or "").strip()
            and self.checked_at is not None
        )

    def is_state_evidence(self) -> bool:
        return self.method == METHOD_DB_STATE

    def is_stale(self, *, now: datetime, max_age_days: int) -> bool:
        """鮮度切れか。DB 状態の証跡は常に最新（その場で確認している）。"""
        if self.is_state_evidence():
            return False
        if self.checked_at is None:
            return False
        return self.checked_at < now - timedelta(days=max_age_days)

    def to_dict(self) -> dict:
        return {
            "claim": self.claim,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "method": self.method,
            "checked_at": _iso(self.checked_at),
            "excerpt": self.excerpt,
        }


@dataclass
class Finding:
    """1 カテゴリの判定結果。"""

    code: str
    key: str
    label: str
    stage: str
    verdict: str
    severity: str
    confidence: str
    reason: str
    evidence: list[Evidence] = field(default_factory=list)
    rule_version: str = RULE_VERSION
    # --- 補助属性 ---
    entity_role: str = ROLE_UNKNOWN     # B. OEM・企業役割（証拠が無ければ unknown）
    facts: dict = field(default_factory=dict)   # A. Business Value の事実（判定に使わない）
    # 集約時に降格した場合の記録（監査用）。
    downgraded_from: str | None = None
    downgrade_reason: str | None = None

    def complete_evidence(self) -> list[Evidence]:
        return [e for e in self.evidence if e.is_complete()]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "key": self.key,
            "label": self.label,
            "stage": self.stage,
            "verdict": self.verdict,
            "severity": self.severity,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "rule_version": self.rule_version,
            "entity_role": self.entity_role,
            "facts": dict(self.facts),
            "downgraded_from": self.downgraded_from,
            "downgrade_reason": self.downgrade_reason,
        }


@dataclass
class PositiveFact:
    """営業する根拠（C. Why Contact）。**確認できた事実のみ**。

    「日本で売れる」「返信が期待できる」等の推測文は作らない。
    証跡（4 点セット）が揃わない事実は追加しない。
    """

    key: str
    label: str
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class QualificationResult:
    """1 案件・1 ステージの判定結果。"""

    project_id: int | None
    stage: str
    decision: str
    findings: list[Finding]
    blocker_codes: list[str]
    review_codes: list[str]
    positive_facts: list[PositiveFact]
    evidence_count: int
    rule_version: str
    evaluated_at: datetime

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "stage": self.stage,
            "decision": self.decision,
            "findings": [f.to_dict() for f in self.findings],
            "blocker_codes": list(self.blocker_codes),
            "review_codes": list(self.review_codes),
            "positive_facts": [p.to_dict() for p in self.positive_facts],
            "evidence_count": self.evidence_count,
            "rule_version": self.rule_version,
            "evaluated_at": _iso(self.evaluated_at),
        }


# --------------------------------------------------------------------------- #
#  カテゴリ定義（A〜T）
# --------------------------------------------------------------------------- #
CATEGORY_LABELS: dict[str, str] = {
    "A": "日本市場不適合",
    "B": "Makuake 向きではない",
    "C": "OEM 商品の可能性",
    "D": "代理店・販売店のみ",
    "E": "メーカー未確認",
    "F": "既に日本正規販売あり",
    "G": "規制リスク（総称）",
    "H": "食品",
    "I": "医療",
    "J": "化粧品",
    "K": "Bluetooth・Wi-Fi・無線",
    "L": "電源・PSE",
    "M": "技適リスク",
    "N": "デジタル商品",
    "O": "ソフトウェアのみ",
    "P": "サービスのみ",
    "Q": "Kickstarter 限定で終売",
    "R": "既に大量流通",
    "S": "ブランド所有者不明",
    "T": "情報不足",
}
CATEGORY_KEYS: dict[str, str] = {
    "A": "japan_market_unfit",
    "B": "makuake_unfit",
    "C": "possible_oem",
    "D": "reseller_only",
    "E": "maker_unverified",
    "F": "already_sold_in_japan",
    "G": "regulatory_risk",
    "H": "regulated_food",
    "I": "regulated_medical",
    "J": "regulated_cosmetics",
    "K": "wireless_radio",
    "L": "power_pse",
    "M": "giteki_required",
    "N": "digital_goods",
    "O": "software_only",
    "P": "service_only",
    "Q": "campaign_discontinued",
    "R": "mass_distributed",
    "S": "brand_owner_unknown",
    "T": "insufficient_information",
}
CATEGORY_CODES: tuple[str, ...] = tuple(sorted(CATEGORY_LABELS))


def _finding(
    code: str,
    stage: str,
    *,
    verdict: str,
    severity: str,
    confidence: str,
    reason: str,
    evidence: list[Evidence] | None = None,
    entity_role: str = ROLE_UNKNOWN,
    facts: dict | None = None,
) -> Finding:
    return Finding(
        code=code,
        key=CATEGORY_KEYS[code],
        label=CATEGORY_LABELS[code],
        stage=stage,
        verdict=verdict,
        severity=severity,
        confidence=confidence,
        reason=reason,
        evidence=list(evidence or []),
        entity_role=entity_role,
        facts=dict(facts or {}),
    )


def _no_hit(code: str, stage: str, reason: str) -> Finding:
    return _finding(
        code, stage, verdict=VERDICT_NO_HIT, severity=SEVERITY_INFO,
        confidence=CONFIDENCE_UNVERIFIED, reason=reason,
    )


def _campaign_evidence(
    signals: dict, claim: str, terms: list[str], *, now: datetime
) -> list[Evidence]:
    """商品ページ上の根拠語を証跡にする。campaign_url が無ければ証跡にならない。"""
    url = signals.get("campaign_url")
    if not url:
        return []
    return [
        Evidence(
            claim=claim,
            source_url=url,
            source_kind=SOURCE_CAMPAIGN,
            method=METHOD_CAMPAIGN_PARSE,
            checked_at=now,
            excerpt=("ページ上の記載語: " + ", ".join(terms[:5])) if terms else None,
        )
    ]


def _state_evidence(signals: dict, claim: str, anchor: str, *, now: datetime) -> Evidence:
    """DB の状態そのものを証跡にする（推測 URL ではない内部ロケータ）。

    ロケータは常に ``db://projects/<project_id>#<anchor>`` の形だけを作る。
    maker 名やドメインから組み立てることはしない。
    """
    pid = signals.get("project_id")
    return Evidence(
        claim=claim,
        source_url=f"db://projects/{pid}#{anchor}",
        source_kind=SOURCE_INTERNAL_DB,
        method=METHOD_DB_STATE,
        checked_at=now,
        excerpt=anchor,
    )


def _listing_evidence(
    items: list[dict], claim: str, source_kind: str, *, limit: int = 5
) -> list[Evidence]:
    """外部ページのリスト（販売ページ・掲載ページ等）を証跡に変換する。

    URL を持たない項目は証跡にしない（組み立てて補完しない）。
    """
    out: list[Evidence] = []
    for item in (items or [])[:limit]:
        url = (item or {}).get("url") or (item or {}).get("source_url")
        if not url:
            continue
        out.append(
            Evidence(
                claim=claim,
                source_url=url,
                source_kind=item.get("source_kind") or source_kind,
                method=item.get("method"),
                checked_at=_as_datetime(item.get("checked_at")),
                excerpt=item.get("excerpt"),
            )
        )
    return out


# --------------------------------------------------------------------------- #
#  A. 日本市場不適合（review のみ。blocker にしない）
# --------------------------------------------------------------------------- #
def _rule_a(signals: dict, stage: str, now: datetime) -> Finding:
    text = _text_of(signals)
    heavy = [c for c in _caution_categories(text) if c in _A_HEAVY_CATEGORIES]
    bulky = _matched(text, _BULKY_HINTS)
    if not heavy and not bulky:
        return _no_hit("A", stage, "日本市場適性を下げる材料はページ上に見当たらない")

    parts: list[str] = []
    terms: list[str] = []
    if heavy:
        parts.append(f"輸入規制負担が大きいカテゴリ: {', '.join(heavy)}")
        for c in heavy:
            terms += _caution_terms(text, c)
    if bulky:
        parts.append("大型・重量物で輸入/物流の負担が大きい")
        terms += bulky
    # 市場性は「予測」であり事実ではないため、**pre_outreach では判定を止めない**。
    severity = SEVERITY_REVIEW if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
    ev = _campaign_evidence(signals, "日本市場適性に懸念がある", terms, now=now)
    return _finding(
        "A", stage, verdict=VERDICT_HIT, severity=severity,
        confidence=CONFIDENCE_LOW if ev else CONFIDENCE_UNVERIFIED,
        reason="; ".join(parts) + "（該当の断定ではなく、確認が必要な項目）",
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
#  B. Makuake 向きではない（明確な証拠がある場合のみ blocker 候補）
# --------------------------------------------------------------------------- #
def _rule_b(signals: dict, stage: str, now: datetime) -> Finding:
    listings = signals.get("japan_cf_listings") or []
    ev = _listing_evidence(listings, "日本のクラファンに既に掲載されている",
                           SOURCE_JP_CROWDFUNDING)
    if ev:
        # 日本 CF 掲載ページ URL という一次証拠がある場合のみ blocker 候補。
        severity = SEVERITY_BLOCKER if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
        return _finding(
            "B", stage, verdict=VERDICT_HIT, severity=severity,
            confidence=CONFIDENCE_HIGH,
            reason="日本のクラウドファンディングに既に掲載されている（掲載ページを確認）",
            evidence=ev,
        )

    text = _text_of(signals)
    non_product = _matched(text, _NON_PRODUCT_HINTS)
    if non_product:
        # 物販モデルに乗らない可能性。市場性の主観では止めないため review 止まり。
        severity = SEVERITY_REVIEW if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
        return _finding(
            "B", stage, verdict=VERDICT_HIT, severity=severity,
            confidence=CONFIDENCE_LOW,
            reason="体験/サービス/計画型の語があり Makuake の物販モデルに乗らない可能性",
            evidence=_campaign_evidence(
                signals, "非物販型の企画を示す語がある", non_product, now=now
            ),
        )
    return _no_hit("B", stage, "日本クラファン掲載の証跡なし・非物販型の語もなし")


# --------------------------------------------------------------------------- #
#  C. OEM 商品の可能性（常に review。推測で blocker にしない）
# --------------------------------------------------------------------------- #
def _rule_c(signals: dict, stage: str, now: datetime) -> Finding:
    notice = signals.get("oem_notice") or None
    others = signals.get("other_brand_listings") or []
    ev: list[Evidence] = []
    role = ROLE_UNKNOWN
    reasons: list[str] = []

    if notice and notice.get("source_url"):
        ev.append(
            Evidence(
                claim="ページ上に OEM/ODM/private label の記載がある",
                source_url=notice.get("source_url"),
                source_kind=notice.get("source_kind") or SOURCE_CAMPAIGN,
                method=notice.get("method"),
                checked_at=_as_datetime(notice.get("checked_at")),
                excerpt=notice.get("excerpt"),
            )
        )
        role = _role_from_terms(notice.get("excerpt") or "")
        reasons.append("ページ上に OEM/ODM 等の記載")

    other_ev = _listing_evidence(others, "同一商品が別ブランド名で流通している", SOURCE_EC)
    if len(other_ev) >= 2:
        ev += other_ev
        reasons.append(f"同一商品を別ブランドで {len(other_ev)} 件確認")
        if role == ROLE_UNKNOWN:
            role = ROLE_PRIVATE_LABEL

    if not reasons:
        if other_ev:
            # 1 件だけでは「別ブランド流通」と言えない（要 corroboration）。
            return _finding(
                "C", stage, verdict=VERDICT_INSUFFICIENT, severity=SEVERITY_INFO,
                confidence=CONFIDENCE_UNVERIFIED,
                reason="別ブランドでの流通が 1 件のみで確証にならない（2 件以上が必要）",
                evidence=other_ev, entity_role=ROLE_UNKNOWN,
            )
        return _no_hit("C", stage, "OEM/ODM を示す証跡なし（役割は unknown のまま）")

    # **常に review。** OEM は断定できないため、どのステージでも判定を止めない。
    return _finding(
        "C", stage, verdict=VERDICT_HIT, severity=SEVERITY_REVIEW,
        confidence=CONFIDENCE_MEDIUM,
        reason="; ".join(reasons) + "（OEM/ODM の断定はしない。営業可否は人が判断する）",
        evidence=ev, entity_role=role,
    )


def _role_from_terms(text: str) -> str:
    """記載語から企業役割を推定する。**該当語が無ければ unknown。**"""
    low = (text or "").lower()
    if _has_term(low, "odm"):
        return ROLE_ODM
    if _has_term(low, "oem"):
        return ROLE_OEM
    if "private label" in low or "white label" in low:
        return ROLE_PRIVATE_LABEL
    if "contract manufacturer" in low or "受託製造" in text:
        return ROLE_MANUFACTURER
    return ROLE_UNKNOWN


# --------------------------------------------------------------------------- #
#  D. 代理店・販売店のみ（pre_research=review / pre_outreach=blocker）
# --------------------------------------------------------------------------- #
def _rule_d(signals: dict, stage: str, now: datetime) -> Finding:
    creator = signals.get("creator_domain") or None
    if not creator or not creator.get("ownership_class"):
        severity = SEVERITY_INFO if stage == STAGE_PRE_RESEARCH else SEVERITY_REVIEW
        return _finding(
            "D", stage, verdict=VERDICT_INSUFFICIENT, severity=severity,
            confidence=CONFIDENCE_UNVERIFIED,
            reason="出品者ドメインの所有者分類が未取得（第三者出品かを判定できない）",
        )

    role = _OWNERSHIP_TO_ROLE.get(str(creator.get("ownership_class")), ROLE_UNKNOWN)
    if role not in _RESELLER_ROLES:
        return _no_hit(
            "D", stage,
            f"出品者ドメインの分類は {creator.get('ownership_class')}（第三者出品ではない）",
        )

    ev = [
        Evidence(
            claim=f"出品者ドメインは {role}（メーカー本人ではない）",
            source_url=creator.get("url"),
            source_kind=creator.get("source_kind") or SOURCE_DISTRIBUTOR,
            method=creator.get("method") or METHOD_CLASSIFY_DOMAIN,
            checked_at=_as_datetime(creator.get("checked_at")),
            excerpt=str(creator.get("ownership_class")),
        )
    ]
    severity = SEVERITY_REVIEW if stage == STAGE_PRE_RESEARCH else SEVERITY_BLOCKER
    reason = (
        f"出品者が {role} と分類された。"
        + ("本当のメーカーを探す価値があるため調査は止めない"
           if stage == STAGE_PRE_RESEARCH
           else "代理店・販売店へ独占交渉メールを送るのは誤送信にあたる")
    )
    return _finding(
        "D", stage, verdict=VERDICT_HIT, severity=severity,
        confidence=CONFIDENCE_HIGH, reason=reason, evidence=ev, entity_role=role,
    )


# --------------------------------------------------------------------------- #
#  E. メーカー未確認（pre_research=info/review / pre_outreach=blocker）
# --------------------------------------------------------------------------- #
def _rule_e(signals: dict, stage: str, now: datetime) -> Finding:
    identity = signals.get("maker_identity") or {}
    if identity.get("verified"):
        return _no_hit("E", stage, "メーカー本人（法人・ブランド）を同定済み")

    maker_name = (signals.get("maker_name") or "").strip()
    ev = [
        _state_evidence(
            signals,
            "maker identity が未確定である",
            "maker_identity" if maker_name else "maker_name",
            now=now,
        )
    ]
    if stage == STAGE_PRE_OUTREACH:
        return _finding(
            "E", stage, verdict=VERDICT_HIT, severity=SEVERITY_BLOCKER,
            confidence=CONFIDENCE_HIGH,
            reason="メーカー本人を同定できていない状態で営業メールを送ることはできない",
            evidence=ev,
        )
    # 調査前は「未確認であること」が調査の目的そのもの。名前すら無い場合のみ review。
    severity = SEVERITY_REVIEW if not maker_name else SEVERITY_INFO
    reason = (
        "メーカー名が保存されておらず同定の起点が無い"
        if not maker_name
        else "メーカー名はあるが本人同定は未実施（調査で解決する見込み）"
    )
    return _finding(
        "E", stage, verdict=VERDICT_HIT, severity=severity,
        confidence=CONFIDENCE_HIGH, reason=reason, evidence=ev,
    )


# --------------------------------------------------------------------------- #
#  F. 既に日本正規販売あり（sold_in_japan かつ source_url ありのみ blocker）
# --------------------------------------------------------------------------- #
def _rule_f(signals: dict, stage: str, now: datetime) -> Finding:
    japan = signals.get("japan_sales") or {}
    result = japan.get("result")
    checked_at = _as_datetime(japan.get("checked_at"))

    if result == "sold_in_japan":
        channels = japan.get("channels") or []
        ev: list[Evidence] = []
        labels: list[str] = []
        for ch in channels:
            key = str(ch.get("channel", "")).lower()
            status = str(ch.get("status", "")).lower()
            if status not in ("found", "limited"):
                continue
            if not any(k in key for k in _JAPAN_PRESENCE_CHANNELS):
                continue
            url = ch.get("search_url")
            if not url:
                continue
            labels.append(ch.get("label") or key)
            ev.append(
                Evidence(
                    claim="日本で販売されている",
                    source_url=url,
                    source_kind=SOURCE_EC,
                    method=METHOD_JAPAN_SALES_CHECK,
                    checked_at=checked_at,
                    excerpt=f"{ch.get('label') or key}: {status}",
                )
            )
        if not ev:
            # sold_in_japan だが販売ページ URL が取れていない → blocker にできない。
            for url in (japan.get("source_urls") or [])[:5]:
                ev.append(
                    Evidence(
                        claim="日本での販売が示唆されている",
                        source_url=url,
                        source_kind=SOURCE_EC,
                        method=METHOD_JAPAN_SALES_CHECK,
                        checked_at=checked_at,
                    )
                )
            return _finding(
                "F", stage, verdict=VERDICT_HIT, severity=SEVERITY_REVIEW,
                confidence=CONFIDENCE_MEDIUM,
                reason="日本販売の判定は sold_in_japan だが、販売ページ URL を確認できていない",
                evidence=ev,
            )
        return _finding(
            "F", stage, verdict=VERDICT_HIT, severity=SEVERITY_BLOCKER,
            confidence=CONFIDENCE_HIGH,
            reason="日本で販売されている（" + " / ".join(labels[:3]) + "）",
            evidence=ev,
            facts={"japan_official_sales": True},
        )

    if result == "not_found_in_japan":
        # **「見つからなかった」は不在の証明ではない。** blocker にしない。
        return _finding(
            "F", stage, verdict=VERDICT_NO_HIT, severity=SEVERITY_INFO,
            confidence=CONFIDENCE_LOW,
            reason="主要チャネルを検索し日本での販売を確認できなかった（不在の確証ではない）",
            evidence=_listing_evidence(
                [{"url": u, "method": METHOD_JAPAN_SALES_CHECK,
                  "checked_at": japan.get("checked_at")}
                 for u in (japan.get("source_urls") or [])],
                "日本での販売を確認できなかった検索", SOURCE_EC,
            ),
            facts={"japan_official_sales": False},
        )

    severity = SEVERITY_INFO if stage == STAGE_PRE_RESEARCH else SEVERITY_REVIEW
    return _finding(
        "F", stage, verdict=VERDICT_INSUFFICIENT, severity=severity,
        confidence=CONFIDENCE_UNVERIFIED,
        reason="日本販売状況が未確定（未実施 / inconclusive）",
    )


# --------------------------------------------------------------------------- #
#  G〜M. 規制（原則 review または info。法令対象を断定しない）
# --------------------------------------------------------------------------- #
def _rule_regulatory(code: str, signals: dict, stage: str, now: datetime) -> Finding:
    """H/I/J/K/L の共通実装。**どのステージでも blocker にしない。**"""
    text = _text_of(signals)
    categories = _REGULATORY_MAP[code]
    hit_categories = [c for c in categories if c in _caution_categories(text)]
    if not hit_categories:
        return _no_hit(code, stage, f"{CATEGORY_LABELS[code]}を示す記載語なし")

    terms: list[str] = []
    for c in hit_categories:
        terms += _caution_terms(text, c)
    severity = SEVERITY_REVIEW if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
    law = _REGULATORY_LAW[code]
    return _finding(
        code, stage, verdict=VERDICT_HIT, severity=severity,
        confidence=CONFIDENCE_LOW,
        reason=(
            f"{law}の確認が必要な可能性がある（該当の断定ではない）。"
            f"根拠語: {', '.join(sorted(set(terms))[:5])}"
        ),
        evidence=_campaign_evidence(
            signals, f"{law}に関わる記載語が商品ページにある", sorted(set(terms)), now=now
        ),
    )


def _rule_m(signals: dict, stage: str, now: datetime, k_finding: Finding) -> Finding:
    """M 技適。K（無線機能あり＝事実）の帰結として「技適の確認が必要」を出す。"""
    if k_finding.verdict != VERDICT_HIT:
        return _no_hit("M", stage, "無線機能を示す記載語が無いため技適の確認対象ではない")
    severity = SEVERITY_REVIEW if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
    return _finding(
        "M", stage, verdict=VERDICT_HIT, severity=severity,
        confidence=CONFIDENCE_LOW,
        reason=(
            "電波を発する機器の可能性があり技術基準適合証明（技適）の確認が必要"
            "（該当の断定ではない）"
        ),
        evidence=list(k_finding.evidence),
    )


def _rule_g(stage: str, children: list[Finding]) -> Finding:
    """G 規制リスク（総称）。H〜M の集約フラグ。**単独では判定しない。**"""
    hits = [f for f in children if f.verdict == VERDICT_HIT]
    if not hits:
        return _no_hit("G", stage, "規制上の確認項目に該当なし")
    codes = ", ".join(f"{f.code}:{f.label}" for f in hits)
    # 二重計上を避けるため、集約は常に info（判定を動かすのは個別カテゴリ側）。
    return _finding(
        "G", stage, verdict=VERDICT_HIT, severity=SEVERITY_INFO,
        confidence=CONFIDENCE_LOW,
        reason=f"確認が必要な規制項目がある: {codes}",
        evidence=[e for f in hits for e in f.evidence][:5],
    )


# --------------------------------------------------------------------------- #
#  N/O/P. 非物理（STRONG 一致のみ blocker / WEAK のみは review）
# --------------------------------------------------------------------------- #
def _non_physical_analysis(text: str) -> dict:
    """非物理判定の内訳を返す。物理商品語があれば WEAK は打ち消される。"""
    gate = _gate_vocab()
    physical = _matched(text, gate._PHYSICAL_PRODUCT_HINTS)
    strong: dict[str, list[str]] = {}
    weak: dict[str, list[str]] = {}
    for code in ("N", "O", "P"):
        terms = tuple(_STRONG_BUCKET[code]) + tuple(_EXTRA_STRONG.get(code, ()))
        found = _matched(text, terms)
        if found:
            strong[code] = found
        wfound = _matched(text, _WEAK_BUCKET[code])
        if wfound:
            weak[code] = wfound
    return {"strong": strong, "weak": weak, "physical": physical}


def _rule_non_physical(
    code: str, signals: dict, stage: str, now: datetime, analysis: dict
) -> Finding:
    strong = analysis["strong"].get(code) or []
    weak = analysis["weak"].get(code) or []
    physical = analysis["physical"]

    if strong:
        return _finding(
            code, stage, verdict=VERDICT_HIT, severity=SEVERITY_BLOCKER,
            confidence=CONFIDENCE_LOW,
            reason=(
                f"{CATEGORY_LABELS[code]}を示す語が商品ページにある: "
                f"{', '.join(strong[:5])}（物理商品の輸入販売にあたらない）"
            ),
            evidence=_campaign_evidence(
                signals, f"{CATEGORY_LABELS[code]}を示す語がある", strong, now=now
            ),
        )
    if weak and not physical:
        # WEAK 語だけ、かつ物理商品語が無い → 断定できないので review 止まり。
        return _finding(
            code, stage, verdict=VERDICT_INSUFFICIENT, severity=SEVERITY_REVIEW,
            confidence=CONFIDENCE_LOW,
            reason=(
                f"{CATEGORY_LABELS[code]}の可能性がある語のみで断定できない: "
                f"{', '.join(weak[:5])}"
            ),
            evidence=_campaign_evidence(
                signals, f"{CATEGORY_LABELS[code]}の可能性がある語", weak, now=now
            ),
        )
    if weak and physical:
        return _no_hit(
            code, stage,
            f"付随機能を示す語（{', '.join(weak[:3])}）はあるが物理商品語"
            f"（{', '.join(physical[:3])}）があるため除外しない",
        )
    return _no_hit(code, stage, f"{CATEGORY_LABELS[code]}を示す語なし")


# --------------------------------------------------------------------------- #
#  Q. Kickstarter 限定で終売（一次証拠なしでは review 止まり）
# --------------------------------------------------------------------------- #
def _rule_q(signals: dict, stage: str, now: datetime) -> Finding:
    notice = signals.get("discontinued_notice") or None
    if notice and notice.get("source_url"):
        ev = [
            Evidence(
                claim="販売終了・入手不可が明記されている",
                source_url=notice.get("source_url"),
                source_kind=notice.get("source_kind") or SOURCE_OFFICIAL,
                method=notice.get("method"),
                checked_at=_as_datetime(notice.get("checked_at")),
                excerpt=notice.get("excerpt"),
            )
        ]
        severity = SEVERITY_BLOCKER if stage == STAGE_PRE_RESEARCH else SEVERITY_REVIEW
        return _finding(
            "Q", stage, verdict=VERDICT_HIT, severity=severity,
            confidence=CONFIDENCE_HIGH,
            reason="販売終了が一次情報として明記されている",
            evidence=ev,
        )

    end = _as_datetime(signals.get("end_date"))
    official = signals.get("official_site") or {}
    ended = end is not None and end < now
    if ended and not official.get("url"):
        return _finding(
            "Q", stage, verdict=VERDICT_INSUFFICIENT, severity=SEVERITY_REVIEW,
            confidence=CONFIDENCE_LOW,
            reason=(
                "キャンペーン終了済みで公式サイトも未確認のため、"
                "販売が継続しているか確認できない（終売の確証はない）"
            ),
            evidence=[
                _state_evidence(signals, "キャンペーンは終了済み", "end_date", now=now)
            ],
        )
    return _no_hit("Q", stage, "終売を示す一次情報なし")


# --------------------------------------------------------------------------- #
#  R. 既に大量流通（販売ページ URL 3 件以上等の証拠必須）
# --------------------------------------------------------------------------- #
R_LISTING_THRESHOLD = 3


def _rule_r(signals: dict, stage: str, now: datetime) -> Finding:
    listings = signals.get("global_listings") or []
    ev = _listing_evidence(listings, "同一商品が広く流通している", SOURCE_EC, limit=8)
    complete = [e for e in ev if e.is_complete()]
    if len(complete) >= R_LISTING_THRESHOLD:
        severity = SEVERITY_BLOCKER if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
        return _finding(
            "R", stage, verdict=VERDICT_HIT, severity=severity,
            confidence=CONFIDENCE_HIGH,
            reason=f"販売ページを {len(complete)} 件確認（既に広く流通している）",
            evidence=ev,
        )

    maker = (signals.get("maker_name") or "").lower()
    mega = [b for b in _MEGA_BRAND_HINTS if maker and _has_term(maker, b)]
    if mega:
        # **ブランド名だけでは blocker にしない。**
        severity = SEVERITY_REVIEW if stage == STAGE_PRE_RESEARCH else SEVERITY_INFO
        return _finding(
            "R", stage, verdict=VERDICT_HIT, severity=severity,
            confidence=CONFIDENCE_LOW,
            reason=(
                f"メーカー名が大手ブランド（{', '.join(mega[:2])}）と一致する。"
                "流通実態は未確認のため停止根拠にはしない"
            ),
            evidence=[_state_evidence(signals, "メーカー名が大手ブランドと一致",
                                      "maker_name", now=now)],
        )
    if ev:
        return _finding(
            "R", stage, verdict=VERDICT_INSUFFICIENT, severity=SEVERITY_INFO,
            confidence=CONFIDENCE_UNVERIFIED,
            reason=(
                f"販売ページの確認が {len(complete)} 件のみで "
                f"{R_LISTING_THRESHOLD} 件に満たない"
            ),
            evidence=ev,
        )
    return _no_hit("R", stage, "大量流通を示す販売ページの証跡なし")


# --------------------------------------------------------------------------- #
#  S. ブランド所有者不明（pre_research=info/review / pre_outreach=blocker）
# --------------------------------------------------------------------------- #
def _rule_s(signals: dict, stage: str, now: datetime) -> Finding:
    official = signals.get("official_site") or {}
    if official.get("verified") and official.get("url"):
        return _no_hit("S", stage, "公式サイトを検証済みでブランド所有者を特定できている")

    ev = [
        _state_evidence(
            signals, "公式サイトが未検証でブランド所有者を特定できない",
            "official_site", now=now,
        )
    ]
    if stage == STAGE_PRE_OUTREACH:
        return _finding(
            "S", stage, verdict=VERDICT_HIT, severity=SEVERITY_BLOCKER,
            confidence=CONFIDENCE_HIGH,
            reason="ブランド所有者を特定できていない状態で営業メールを送ることはできない",
            evidence=ev,
        )
    severity = SEVERITY_REVIEW if official.get("url") else SEVERITY_INFO
    reason = (
        "公式サイト候補はあるが検証が完了していない"
        if official.get("url")
        else "公式サイトが未取得（調査で解決する見込み）"
    )
    return _finding(
        "S", stage, verdict=VERDICT_HIT, severity=severity,
        confidence=CONFIDENCE_HIGH, reason=reason, evidence=ev,
    )


# --------------------------------------------------------------------------- #
#  T. 情報不足（DB 状態そのものを証拠とする。既存 gate 互換）
# --------------------------------------------------------------------------- #
def _rule_t(signals: dict, stage: str, now: datetime) -> Finding:
    blockers: list[str] = []
    ev: list[Evidence] = []

    if not signals.get("campaign_url"):
        reason = signals.get("campaign_url_missing_reason") or "商品ページURL未確認"
        blockers.append(f"商品ページURL未確認（{reason}）")
        ev.append(
            _state_evidence(signals, "campaign_url が保存されていない",
                            "campaign_url", now=now)
        )

    summary = (signals.get("japanese_summary") or "").strip()
    if len(summary) < MIN_SUMMARY_LEN:
        blockers.append("商品内容が判別できない（日本語概要を生成できない）")
        ev.append(
            _state_evidence(signals, "日本語の商品概要を生成できない",
                            "japanese_summary", now=now)
        )

    if not blockers:
        return _no_hit("T", stage, "商品ページURL・商品内容ともに確認できている")
    return _finding(
        "T", stage, verdict=VERDICT_HIT, severity=SEVERITY_BLOCKER,
        confidence=CONFIDENCE_HIGH,
        reason="; ".join(blockers),
        evidence=ev,
    )


# --------------------------------------------------------------------------- #
#  positive_facts（C. Why Contact）
# --------------------------------------------------------------------------- #
def _positive_facts(signals: dict, analysis: dict, now: datetime) -> list[PositiveFact]:
    """確認できた事実だけを積む。証跡（4 点セット）が揃わないものは追加しない。"""
    out: list[PositiveFact] = []

    def add(key: str, evidence: list[Evidence]) -> None:
        complete = [e for e in evidence if e.is_complete()]
        if not complete:
            return
        out.append(PositiveFact(key=key, label=POSITIVE_FACT_LABELS[key],
                                evidence=complete))

    campaign_url = signals.get("campaign_url")
    if campaign_url:
        add("campaign_url_verified", [
            Evidence(claim="商品ページURLを確認済み", source_url=campaign_url,
                     source_kind=SOURCE_CAMPAIGN, method=METHOD_DB_STATE,
                     checked_at=now)
        ])

    # 物理商品であることの確認（物理商品語があり、STRONG な非物理語が無い）。
    if analysis["physical"] and not analysis["strong"]:
        add("physical_product_confirmed",
            _campaign_evidence(signals, "物理商品を示す語が商品ページにある",
                               analysis["physical"], now=now))

    if (signals.get("maker_name") or "").strip():
        add("maker_name_present",
            [_state_evidence(signals, "メーカー名を保持している", "maker_name", now=now)])

    official = signals.get("official_site") or {}
    if official.get("verified") and official.get("url"):
        add("official_site_verified", [
            Evidence(claim="公式サイトを検証済み",
                     source_url=official.get("source_url") or official.get("url"),
                     source_kind=SOURCE_OFFICIAL, method=official.get("method"),
                     checked_at=_as_datetime(official.get("checked_at")))
        ])

    identity = signals.get("maker_identity") or {}
    if identity.get("verified"):
        add("maker_identity_verified", [
            Evidence(claim="メーカー本人を同定済み", source_url=identity.get("source_url"),
                     source_kind=SOURCE_OFFICIAL, method=identity.get("method"),
                     checked_at=_as_datetime(identity.get("checked_at")))
        ])

    emails = signals.get("business_emails") or []
    email_ev = [
        Evidence(claim=f"営業に使える連絡先を取得済み: {e.get('email')}",
                 source_url=e.get("source_url"), source_kind=SOURCE_OFFICIAL,
                 method=e.get("method"), checked_at=_as_datetime(e.get("checked_at")))
        for e in emails if e.get("email")
    ]
    add("business_contact_found", email_ev)

    people = signals.get("decision_makers") or []
    people_ev = [
        Evidence(claim=f"意思決定者を確認: {p.get('name')}", source_url=p.get("source_url"),
                 source_kind=SOURCE_OFFICIAL, method=p.get("method"),
                 checked_at=_as_datetime(p.get("checked_at")))
        for p in people if p.get("name")
    ]
    add("decision_maker_found", people_ev)

    japan = signals.get("japan_sales") or {}
    if japan.get("status") == "completed":
        add("japan_sales_check_completed", [
            Evidence(claim="日本販売状況チェックを完了済み",
                     source_url=(japan.get("source_urls") or [None])[0],
                     source_kind=SOURCE_EC, method=METHOD_JAPAN_SALES_CHECK,
                     checked_at=_as_datetime(japan.get("checked_at")))
        ])
    return out


# --------------------------------------------------------------------------- #
#  集約（不変条件の強制）
# --------------------------------------------------------------------------- #
def _enforce_invariants(finding: Finding, now: datetime) -> Finding:
    """blocker が成立する条件を **1 か所で** 強制する。

    1. verdict が hit 以外の所見は blocker になれない
    2. 4 点セットの揃った証跡が 1 件も無ければ blocker になれない
    3. 証跡がすべて鮮度切れなら blocker になれない（verdict=stale へ）

    降格先は review。**降格の事実と理由を残す**（監査のため）。
    """
    if finding.severity != SEVERITY_BLOCKER:
        return finding

    if finding.verdict != VERDICT_HIT:
        finding.downgraded_from = SEVERITY_BLOCKER
        finding.downgrade_reason = f"verdict={finding.verdict} は blocker になれない"
        finding.severity = SEVERITY_REVIEW
        return finding

    complete = finding.complete_evidence()
    if not complete:
        finding.downgraded_from = SEVERITY_BLOCKER
        finding.downgrade_reason = (
            "証跡の 4 点セット（claim / source_url / checked_at / method）が揃っていない"
        )
        finding.severity = SEVERITY_REVIEW
        return finding

    max_age = _FRESHNESS_DAYS.get(finding.code, _FRESHNESS_DAYS_DEFAULT)
    fresh = [e for e in complete if not e.is_stale(now=now, max_age_days=max_age)]
    if not fresh:
        finding.downgraded_from = SEVERITY_BLOCKER
        finding.downgrade_reason = f"証跡が鮮度切れ（{max_age}日を超過）"
        finding.verdict = VERDICT_STALE
        finding.severity = SEVERITY_REVIEW
    return finding


def _decide(findings: list[Finding]) -> str:
    """decision は **最も重い Finding** で決まる。点数の合算は行わない。"""
    if any(f.severity == SEVERITY_BLOCKER for f in findings):
        return DECISION_BLOCKED
    if any(f.severity == SEVERITY_REVIEW for f in findings):
        return DECISION_REVIEW
    return DECISION_CLEAR


# --------------------------------------------------------------------------- #
#  公開 API
# --------------------------------------------------------------------------- #
def qualify(
    signals: dict, stage: str = STAGE_PRE_RESEARCH, *, now: datetime | None = None
) -> QualificationResult:
    """営業対象除外判定を行う（純粋関数。DB 書き込みも外部 HTTP も行わない）。

    Args:
        signals: モジュール docstring の「signals の契約」を参照。
        stage:   ``pre_research`` / ``pre_outreach``
        now:     判定時刻（鮮度判定の基準）。テスト用に注入できる。

    Returns:
        QualificationResult
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    now = now or _now()
    text = _text_of(signals)
    analysis = _non_physical_analysis(text)

    findings: list[Finding] = [
        _rule_a(signals, stage, now),
        _rule_b(signals, stage, now),
        _rule_c(signals, stage, now),
        _rule_d(signals, stage, now),
        _rule_e(signals, stage, now),
        _rule_f(signals, stage, now),
    ]

    regulatory = [
        _rule_regulatory(code, signals, stage, now) for code in ("H", "I", "J", "K", "L")
    ]
    k_finding = next(f for f in regulatory if f.code == "K")
    m_finding = _rule_m(signals, stage, now, k_finding)
    findings.append(_rule_g(stage, regulatory + [m_finding]))
    findings += regulatory
    findings.append(m_finding)

    findings += [
        _rule_non_physical(code, signals, stage, now, analysis)
        for code in ("N", "O", "P")
    ]
    findings += [
        _rule_q(signals, stage, now),
        _rule_r(signals, stage, now),
        _rule_s(signals, stage, now),
        _rule_t(signals, stage, now),
    ]

    findings = [_enforce_invariants(f, now) for f in findings]
    findings.sort(key=lambda f: (_SEVERITY_RANK[f.severity], f.code))

    positive_facts = _positive_facts(signals, analysis, now)
    evidence_count = sum(len(f.complete_evidence()) for f in findings) + sum(
        len([e for e in p.evidence if e.is_complete()]) for p in positive_facts
    )
    return QualificationResult(
        project_id=signals.get("project_id"),
        stage=stage,
        decision=_decide(findings),
        findings=findings,
        blocker_codes=sorted(
            {f.code for f in findings if f.severity == SEVERITY_BLOCKER}
        ),
        review_codes=sorted({f.code for f in findings if f.severity == SEVERITY_REVIEW}),
        positive_facts=positive_facts,
        evidence_count=evidence_count,
        rule_version=RULE_VERSION,
        evaluated_at=now,
    )
