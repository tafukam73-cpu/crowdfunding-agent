"""日本販売状況チェックの共通インターフェースと純粋ロジック。

モックチェッカー・Claude チェッカーがこの JapanSalesChecker を実装する。
出力は JapanSalesResult（DB / モデル非依存）。

設計方針：
- 各チャネルの「検索 URL」は商品名・メーカー名から決定的に組み立てる純粋関数で
  作る（ネットワーク不要・テスト可能）。URL が無くても営業担当が手動で各チャネルを
  確認できるようにするため、status に関係なく常に検索 URL を付ける。
- 営業価値（★1〜5）はチャネルの status から決定的に算出する（compute_stars）。
  AI/モックは各チャネルの status と日本語コメントを返し、★は本ロジックで一貫付与する。

get_japan_sales_checker() が設定に応じてエンジンを選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockJapanSalesChecker（既定）
  - 設定済み            → ClaudeJapanSalesChecker
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import quote, quote_plus

from pydantic import BaseModel, Field

from app.config import settings
from app.models.project import Project

# 調査チャネル（表示順）。key はステータス辞書のキー、label は UI 表示名。
CHANNELS: list[tuple[str, str]] = [
    ("amazon", "Amazon.co.jp"),
    ("rakuten", "楽天市場"),
    ("yahoo", "Yahoo!ショッピング"),
    ("distributor", "日本代理店"),
    ("subsidiary", "日本法人"),
    ("makuake", "Makuake掲載歴"),
    ("greenfunding", "GREEN FUNDING掲載歴"),
]
CHANNEL_KEYS = [k for k, _ in CHANNELS]
CHANNEL_LABELS = dict(CHANNELS)

# チャネルの販売/掲載状況
STATUS_FOUND = "found"        # 明確に販売/掲載あり
STATUS_LIMITED = "limited"    # 一部・痕跡程度
STATUS_NOT_FOUND = "not_found"  # 見つからない
STATUS_UNKNOWN = "unknown"    # 判断できない
VALID_STATUSES = {STATUS_FOUND, STATUS_LIMITED, STATUS_NOT_FOUND, STATUS_UNKNOWN}

# EC（実販売）チャネル。営業価値の算出に使う。
_EC_KEYS = ("amazon", "rakuten", "yahoo")


class JapanSalesResult(BaseModel):
    """日本販売状況チェック結果（completed 時の中身）。"""

    # チャネル key -> status。未指定チャネルは unknown 扱い。
    channel_statuses: dict[str, str] = Field(default_factory=dict)
    # チャネル key -> 所見（任意の短い日本語メモ）
    channel_notes: dict[str, str] = Field(default_factory=dict)
    ai_comment: str = ""
    summary: str = ""
    model: str = ""


class JapanSalesChecker(ABC):
    """全チェッカーの基底クラス。"""

    name: str = "base"
    #: 直近呼び出しのトークン使用量（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def check(self, project: Project) -> JapanSalesResult:
        """案件の日本販売状況を調査して結果を返す。"""
        raise NotImplementedError


# ---------------- 純粋関数（検索 URL・クエリ・★算出） ----------------
def _ec_query(product: str, maker: str) -> str:
    """EC サイト検索向けのクエリ（商品名優先、無ければメーカー名）。"""
    return (product or "").strip() or (maker or "").strip()


def _biz_query(product: str, maker: str) -> str:
    """代理店/法人検索向けのクエリ（メーカー/ブランド名優先）。"""
    return (maker or "").strip() or (product or "").strip()


def search_url(channel: str, *, product: str | None, maker: str | None) -> str:
    """チャネルの検索 URL を商品名・メーカー名から組み立てる（決定的）。

    EC は各サイトのネイティブ検索、代理店/法人と各クラファンは Google 検索
    （site: 限定）を使う。常に有効な URL を返し、手動確認の起点にする。
    """
    product = (product or "").strip()
    maker = (maker or "").strip()
    ec_q = _ec_query(product, maker)
    biz_q = _biz_query(product, maker)

    if channel == "amazon":
        return f"https://www.amazon.co.jp/s?k={quote_plus(ec_q)}"
    if channel == "rakuten":
        return f"https://search.rakuten.co.jp/search/mall/{quote(ec_q)}/"
    if channel == "yahoo":
        return f"https://shopping.yahoo.co.jp/search?p={quote_plus(ec_q)}"
    if channel == "distributor":
        return f"https://www.google.com/search?q={quote_plus(biz_q + ' 日本 代理店')}"
    if channel == "subsidiary":
        return f"https://www.google.com/search?q={quote_plus(biz_q + ' 日本法人')}"
    if channel == "makuake":
        return f"https://www.google.com/search?q={quote_plus('site:makuake.com ' + ec_q)}"
    if channel == "greenfunding":
        return (
            "https://www.google.com/search?q="
            f"{quote_plus('site:greenfunding.jp ' + ec_q)}"
        )
    # 未知のチャネルは汎用 Google 検索
    return f"https://www.google.com/search?q={quote_plus(ec_q)}"


def build_search_queries(product: str | None, maker: str | None) -> list[str]:
    """手動確認用の検索クエリ候補（表示用）。"""
    product = (product or "").strip()
    maker = (maker or "").strip()
    ec_q = _ec_query(product, maker)
    biz_q = _biz_query(product, maker)
    queries: list[str] = []
    if ec_q:
        queries.append(ec_q)
    if product and maker and product != maker:
        queries.append(f"{product} {maker}")
    if biz_q:
        queries.append(f"{biz_q} 日本 代理店")
        queries.append(f"{biz_q} 日本法人")
    # 重複排除（順序維持）
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _is_found(statuses: dict[str, str], key: str) -> bool:
    return statuses.get(key) == STATUS_FOUND


def compute_stars(statuses: dict[str, str]) -> int:
    """チャネルの status から営業価値（★1〜5）を決定的に算出する。

    要件の判定基準：
      ★★★★★ 日本未販売・代理店なし・EC販売なし・クラファン掲載なし
      ★★★★☆ 一部販売のみ（限定的な痕跡 / クラファン掲載のみ）
      ★★★☆☆ 少数販売あり（EC 1 サイトで販売）
      ★★☆☆☆ 代理店あり
      ★☆☆☆☆ 広く販売済み（EC 2 サイト以上）
    """
    ec_found = sum(1 for k in _EC_KEYS if _is_found(statuses, k))
    ec_limited = sum(1 for k in _EC_KEYS if statuses.get(k) == STATUS_LIMITED)
    has_distributor = _is_found(statuses, "distributor") or _is_found(
        statuses, "subsidiary"
    )
    has_crowdfunding = _is_found(statuses, "makuake") or _is_found(
        statuses, "greenfunding"
    )

    if has_distributor:
        # 代理店あり。さらに広く EC 展開済みなら最低評価。
        return 1 if ec_found >= 2 else 2
    if ec_found >= 2:
        return 1  # 広く販売済み
    if ec_found == 1:
        return 3  # 少数販売あり
    # EC での確実な販売なし・代理店なし
    if ec_limited >= 1 or has_crowdfunding:
        return 4  # 一部販売のみ（限定的 / クラファン掲載のみ）
    return 5  # 完全に未確認＝最も営業価値が高い


def build_channels(
    product: str | None,
    maker: str | None,
    statuses: dict[str, str],
    notes: dict[str, str] | None = None,
) -> list[dict]:
    """UI/保存用のチャネル結果リストを組み立てる（検索 URL を常に付与）。"""
    notes = notes or {}
    out: list[dict] = []
    for key, label in CHANNELS:
        status = statuses.get(key, STATUS_UNKNOWN)
        if status not in VALID_STATUSES:
            status = STATUS_UNKNOWN
        out.append(
            {
                "channel": key,
                "label": label,
                "status": status,
                "search_url": search_url(key, product=product, maker=maker),
                "note": notes.get(key, ""),
            }
        )
    return out


def default_summary(stars: int, statuses: dict[str, str]) -> str:
    """★とチャネル状況から一行サマリ（日本語）を作る。"""
    found = [
        CHANNEL_LABELS[k]
        for k in CHANNEL_KEYS
        if statuses.get(k) in (STATUS_FOUND, STATUS_LIMITED)
    ]
    if not found:
        return "日本での販売・掲載は確認できませんでした（営業価値が高い）。"
    return "確認できたチャネル：" + "・".join(found) + "。"


def get_japan_sales_checker() -> JapanSalesChecker:
    if settings.anthropic_api_key:
        from app.ai.claude_japan_sales_checker import ClaudeJapanSalesChecker

        return ClaudeJapanSalesChecker(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    from app.ai.mock_japan_sales_checker import MockJapanSalesChecker

    return MockJapanSalesChecker()
