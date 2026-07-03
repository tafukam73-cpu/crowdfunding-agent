"""Discovery Crawler Framework の共通土台（Discovery Engine v1-3）。

海外クラウドファンディングの商品候補を「発掘元プラットフォームに依存しない共通型」
``DiscoveryCandidate`` として表現し、各 platform adapter はサイト固有の取得・抽出を
担う。抽出は堅牢性のため次の順で試みる（前段で埋まらなかった項目のみ後段で補完）：

  1. JSON-LD（``<script type="application/ld+json">``）
  2. 埋め込み JSON（``window.__INITIAL__`` / ``gon`` 等・adapter 個別）
  3. meta タグ（og: / twitter: / 一般 meta）
  4. 一般 HTML（``<title>`` / ``<h1>``）

設計方針：
- **実ネットワークに依存しない**。取得は ``fetch_fn`` として注入でき、テストは
  fixture（HTML / JSON 文字列）を返す関数を渡すだけで完結する。v1-3 では実
  スクレイピングは必須ではなく、``fetch_fn`` 未指定のネットワーク系 adapter は
  安全に空リストを返す（外部送信・課金・スクレイピングサービスを一切使わない）。
- 不明な項目は ``None`` のまま、status 不明は ``"unknown"`` に倒す（安全側）。
- successful / ended / failed / canceled も**除外しない**（発掘対象に含める）。
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.discovered_product import (
    DiscoveredProductStatus,
    DiscoverySourcePlatform,
)

logger = logging.getLogger("discovery_crawler")

# fetch_fn の型：URL を受け取り本文（HTML / JSON 文字列）を返す。
FetchFn = Callable[[str], str]

_VALID_PLATFORM = {p.value for p in DiscoverySourcePlatform}
_VALID_STATUS = {s.value for s in DiscoveredProductStatus}

# サイト横断のステータス表記ゆれ → DiscoveredProductStatus への写像。
# 記載のない値・不明は "unknown"。成功/終了/失敗/中止も必ず受け入れる。
_STATUS_ALIASES: dict[str, str] = {
    "live": "live",
    "active": "live",
    "funding": "live",
    "started": "live",
    "launched": "live",
    "ongoing": "live",
    "successful": "successful",
    "success": "successful",
    "funded": "successful",
    "ended": "ended",
    "completed": "ended",
    "finished": "ended",
    "closed": "ended",
    "expired": "ended",
    "failed": "failed",
    "unsuccessful": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
    "suspended": "canceled",
    "preorder": "preorder",
    "pre-order": "preorder",
    "pre_order": "preorder",
    "unknown": "unknown",
}

# URL 正規化で除去するトラッキング系クエリパラメータ（前方一致 / 完全一致）。
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {
    "ref", "ref_", "refid", "recruiter_id", "source", "src", "fbclid",
    "gclid", "mc_cid", "mc_eid", "token", "share", "sfns",
}


def normalize_status(value: Any) -> str:
    """任意のステータス表記を DiscoveredProductStatus 値へ正規化する。"""
    if value is None:
        return DiscoveredProductStatus.unknown.value
    key = str(value).strip().lower()
    if key in _VALID_STATUS:
        return key
    return _STATUS_ALIASES.get(key, DiscoveredProductStatus.unknown.value)


def normalize_platform(value: Any) -> str:
    """発掘元プラットフォーム名を正規化する（未知は "other"）。"""
    if value is None:
        return DiscoverySourcePlatform.other.value
    key = str(value).strip().lower()
    return key if key in _VALID_PLATFORM else DiscoverySourcePlatform.other.value


def normalize_url(url: Any) -> str | None:
    """重複判定用に URL を正規化する。

    - scheme / host を小文字化、既定ポート・fragment を除去
    - トラッキング系クエリ（utm_* / ref / gclid 等）を除去
    - 末尾スラッシュを畳む（ルートは維持）
    正規化不能・空なら None。
    """
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    # scheme が無い場合は素直に空白畳みだけ行う（相対 URL 等）
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw or None

    scheme = parts.scheme.lower()
    netloc = parts.hostname or ""
    if parts.port and not (
        (scheme == "http" and parts.port == 80)
        or (scheme == "https" and parts.port == 443)
    ):
        netloc = f"{netloc}:{parts.port}"

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_KEYS
        and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    query = urlencode(kept)

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, query, ""))


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        # 通貨記号・カンマを除去してから解釈
        s = re.sub(r"[^0-9.\-]", "", str(value))
        if s in ("", "-", "."):
            return None
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(re.sub(r"[^0-9.\-]", "", str(value))))
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    """epoch 秒 / ISO 文字列 / date から date を得る。失敗時 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    # epoch 秒
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(int(value)).date()
        except (ValueError, OSError, OverflowError):
            return None
    s = str(value).strip()
    if s.isdigit() and len(s) >= 9:  # epoch 秒っぽい
        try:
            return datetime.utcfromtimestamp(int(s)).date()
        except (ValueError, OSError, OverflowError):
            return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    # ISO8601（タイムゾーン付き）を最後に試す
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


@dataclass
class DiscoveryCandidate:
    """発掘元に依存しない商品候補の共通型（discovered_products の入力）。"""

    source_platform: str | None = None
    source_url: str | None = None
    project_title: str | None = None
    creator_name: str | None = None
    product_name: str | None = None
    category: str | None = None
    description: str | None = None
    image_url: str | None = None
    country: str | None = None
    status: str | None = None
    funding_amount: Any = None
    funding_goal: Any = None
    backers_count: Any = None
    launch_date: Any = None
    end_date: Any = None
    official_website_url: str | None = None

    def merge_missing(self, other: "DiscoveryCandidate") -> None:
        """自分が未設定（None）の項目のみ other の値で補完する。"""
        for f in fields(self):
            if getattr(self, f.name) in (None, "") and getattr(other, f.name) not in (
                None,
                "",
            ):
                setattr(self, f.name, getattr(other, f.name))

    def to_product_dict(self, *, default_platform: str | None = None) -> dict:
        """discovered_products のカラムに合わせた dict に正規化する。

        - source_platform / status は妥当な値へ丸める（不明は other / unknown）
        - 金額は Decimal、支援者数は int、日付は date に型変換（失敗は None）
        """
        platform = self.source_platform or default_platform
        return {
            "source_platform": normalize_platform(platform),
            "source_url": normalize_url(self.source_url),
            "project_title": self.project_title or None,
            "creator_name": self.creator_name or None,
            "product_name": self.product_name or None,
            "category": self.category or None,
            "description": self.description or None,
            "image_url": self.image_url or None,
            "country": self.country or None,
            "status": normalize_status(self.status),
            "funding_amount": _to_decimal(self.funding_amount),
            "funding_goal": _to_decimal(self.funding_goal),
            "backers_count": _to_int(self.backers_count),
            "launch_date": _to_date(self.launch_date),
            "end_date": _to_date(self.end_date),
            "official_website_url": self.official_website_url or None,
        }


# --------------------------------------------------------------------------- #
# 抽出ヘルパー（JSON-LD / 埋め込み JSON / meta / HTML）
# --------------------------------------------------------------------------- #
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(
    r'(property|name)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE
)
_META_CONTENT_RE = re.compile(r'content\s*=\s*["\'](.*?)["\']', re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _TAG_RE.sub(" ", str(value))
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def extract_jsonld(text: str) -> list[dict]:
    """ページ内の JSON-LD ブロックを dict のリストで返す（壊れは無視）。"""
    out: list[dict] = []
    for block in _JSONLD_RE.findall(text or ""):
        raw = block.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            # @graph 形式を平坦化
            graph = data.get("@graph")
            if isinstance(graph, list):
                out.extend(x for x in graph if isinstance(x, dict))
            else:
                out.append(data)
        elif isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
    return out


def extract_meta(text: str) -> dict[str, str]:
    """og: / twitter: / name meta を {キー: content} で返す。"""
    result: dict[str, str] = {}
    for tag in _META_RE.findall(text or ""):
        key_m = _META_ATTR_RE.search(tag)
        content_m = _META_CONTENT_RE.search(tag)
        if not key_m or not content_m:
            continue
        key = key_m.group(2).strip().lower()
        content = _clean_text(content_m.group(1))
        if key and content and key not in result:
            result[key] = content
    return result


def extract_title(text: str) -> str | None:
    m = _H1_RE.search(text or "")
    if m:
        cleaned = _clean_text(m.group(1))
        if cleaned:
            return cleaned
    m = _TITLE_RE.search(text or "")
    if m:
        return _clean_text(m.group(1))
    return None


def _jsonld_offer_amount(offers: Any, key: str) -> Any:
    if isinstance(offers, dict):
        return offers.get(key)
    if isinstance(offers, list):
        for o in offers:
            if isinstance(o, dict) and o.get(key) is not None:
                return o.get(key)
    return None


def candidate_from_jsonld(node: dict) -> DiscoveryCandidate:
    """JSON-LD の Product / CreativeWork ノードから候補を組み立てる。"""
    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    author = node.get("author") or node.get("creator")
    if isinstance(author, dict):
        author = author.get("name")
    image = node.get("image")
    if isinstance(image, dict):
        image = image.get("url")
    if isinstance(image, list) and image:
        image = image[0] if isinstance(image[0], str) else None
    offers = node.get("offers")
    return DiscoveryCandidate(
        source_url=node.get("url") or node.get("@id"),
        project_title=_clean_text(node.get("name")),
        product_name=_clean_text(node.get("name")),
        creator_name=_clean_text(author or brand),
        category=_clean_text(node.get("category")),
        description=_clean_text(node.get("description")),
        image_url=image if isinstance(image, str) else None,
        funding_amount=_jsonld_offer_amount(offers, "price"),
    )


def candidate_from_meta(meta: dict[str, str], title: str | None) -> DiscoveryCandidate:
    """meta タグ + <title>/<h1> から候補を組み立てる（最後の砦）。"""
    name = meta.get("og:title") or meta.get("twitter:title") or title
    return DiscoveryCandidate(
        source_url=meta.get("og:url"),
        project_title=name,
        product_name=name,
        description=meta.get("og:description") or meta.get("description")
        or meta.get("twitter:description"),
        image_url=meta.get("og:image") or meta.get("twitter:image"),
        creator_name=meta.get("og:site_name"),
    )


def parse_page_cascade(text: str, url: str | None = None) -> DiscoveryCandidate | None:
    """単一ページ HTML から 1 候補を段階抽出する。

    JSON-LD → meta/HTML の順に組み立て、前段で欠けた項目を後段で補完する。
    何も取れなければ None。
    """
    if not text:
        return None

    candidate: DiscoveryCandidate | None = None
    for node in extract_jsonld(text):
        node_type = node.get("@type", "")
        types = node_type if isinstance(node_type, list) else [node_type]
        types = [str(t).lower() for t in types]
        if any(t in ("product", "creativework", "individualproduct") for t in types) \
                or node.get("name"):
            candidate = candidate_from_jsonld(node)
            break

    meta = extract_meta(text)
    title = extract_title(text)
    meta_candidate = candidate_from_meta(meta, title)
    if candidate is None:
        candidate = meta_candidate
    else:
        candidate.merge_missing(meta_candidate)

    if url and not candidate.source_url:
        candidate.source_url = url
    if candidate.official_website_url is None:
        candidate.official_website_url = None

    # タイトルも本文も無ければ抽出失敗とみなす
    if not candidate.project_title and not candidate.product_name \
            and not candidate.description:
        return None
    return candidate


class BaseAdapter:
    """platform adapter の基底。

    サブクラスは ``platform`` を設定し、必要なら ``build_query_url`` /
    ``parse_content`` を上書きする。``discover`` の共通実装は
    ``fetch_fn`` で本文を取得し ``parse_content`` に委譲する。
    """

    platform: str = DiscoverySourcePlatform.other.value

    def build_query_url(self, query: str | None) -> str | None:
        """検索/一覧の取得先 URL を返す（既定は None＝取得先なし）。"""
        return None

    def parse_content(self, text: str, url: str | None = None) -> list[DiscoveryCandidate]:
        """本文（HTML/JSON）から候補リストを抽出する。既定は単一ページ抽出。"""
        candidate = parse_page_cascade(text, url)
        return [candidate] if candidate else []

    def _stamp(self, candidates: Iterable[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
        """発掘元プラットフォームを補完して返す。"""
        stamped: list[DiscoveryCandidate] = []
        for c in candidates:
            if not c.source_platform:
                c.source_platform = self.platform
            stamped.append(c)
        return stamped

    def discover(
        self,
        query: str | None = None,
        limit: int = 20,
        *,
        fetch_fn: FetchFn | None = None,
        records: list[dict] | None = None,
        **_ignored: Any,
    ) -> list[DiscoveryCandidate]:
        """候補を最大 limit 件返す。

        v1-3 の安全設計：ネットワーク系 adapter は ``fetch_fn`` が無ければ
        **外部アクセスせず空リスト**を返す（実スクレイピングは注入で行う）。
        """
        limit = max(0, int(limit or 0))
        if limit == 0:
            return []
        if fetch_fn is None:
            logger.info(
                "adapter=%s: fetch_fn 未指定のため候補取得をスキップ"
                "（v1-3 は実ネットワークを既定で行わない）",
                self.platform,
            )
            return []
        url = self.build_query_url(query)
        if not url:
            return []
        text = fetch_fn(url)
        candidates = self._stamp(self.parse_content(text, url))
        return candidates[:limit]
