"""Discovery Crawler Framework の platform adapter 群（Discovery Engine v1-3/v2）。

``get_adapter(source_platform)`` で発掘元に対応する adapter を得る。共通型
``DiscoveryCandidate`` はどの adapter でも同じ形で候補を返すための契約。

Wadiz / Zeczec / Ulule / Indiegogo は既存スクレイパー（projects 向け収集で実績あり）を
そのまま流用する ``ScraperBackedAdapter`` で接続する（新規スクレイパーを作らない）。
Kickstarter は discover/advanced JSON を ``discovery_fetch`` 経由で取得する。

プラットフォームの「実取得対応 / 準備中」はこのモジュールの ``platform_availability()``
を単一の真実源（single source of truth）とし、UI もこれを取得して表示する
（実態とラベルを常に一致させ、ハードコードの二重管理を避ける）。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.discovered_product import DiscoverySourcePlatform
from app.services.discovery_adapters.backerkit_adapter import BackerkitAdapter
from app.services.discovery_adapters.base import (
    BaseAdapter,
    DiscoveryCandidate,
    normalize_platform,
    normalize_status,
    normalize_url,
)
from app.services.discovery_adapters.indiegogo_adapter import IndiegogoAdapter
from app.services.discovery_adapters.kickstarter_adapter import KickstarterAdapter
from app.services.discovery_adapters.manual_adapter import ManualAdapter
from app.services.discovery_adapters.scraper_adapter import (
    IndiegogoScraperAdapter,
    UluleAdapter,
    WadizAdapter,
    ZeczecAdapter,
)

# 発掘元プラットフォーム → adapter クラス
_ADAPTERS: dict[str, type[BaseAdapter]] = {
    DiscoverySourcePlatform.kickstarter.value: KickstarterAdapter,
    DiscoverySourcePlatform.indiegogo.value: IndiegogoScraperAdapter,
    DiscoverySourcePlatform.wadiz.value: WadizAdapter,
    DiscoverySourcePlatform.zeczec.value: ZeczecAdapter,
    DiscoverySourcePlatform.ulule.value: UluleAdapter,
    DiscoverySourcePlatform.backerkit.value: BackerkitAdapter,
    DiscoverySourcePlatform.manual.value: ManualAdapter,
}

# discovery_fetch（Playwright 経由の実サイト取得）を注入して動かすプラットフォーム。
# ここに無い network_backed adapter（Wadiz/Zeczec/Ulule/Indiegogo）は自前で取得する。
FETCH_INJECT_PLATFORMS = {DiscoverySourcePlatform.kickstarter.value}

# 発掘元プラットフォームの表示順（manual / other を除く）。
DISCOVERY_PLATFORM_ORDER = [
    DiscoverySourcePlatform.kickstarter.value,
    DiscoverySourcePlatform.indiegogo.value,
    DiscoverySourcePlatform.ulule.value,
    DiscoverySourcePlatform.wadiz.value,
    DiscoverySourcePlatform.zeczec.value,
    DiscoverySourcePlatform.backerkit.value,
]

_PLATFORM_LABELS = {
    "kickstarter": "Kickstarter",
    "indiegogo": "Indiegogo",
    "ulule": "Ulule",
    "wadiz": "Wadiz",
    "zeczec": "Zeczec",
    "backerkit": "BackerKit",
    "manual": "手動登録",
    "other": "その他",
}

# 検索クエリ（query）を使うプラットフォーム。ここに無いサイトは一覧取得型で、
# query を使わず新着・注目案件を取得する（UI にその旨を表示する）。
_QUERY_PLATFORMS = {DiscoverySourcePlatform.kickstarter.value}


def get_adapter(source_platform: str | None) -> BaseAdapter:
    """発掘元に対応する adapter インスタンスを返す。

    未対応・不明なプラットフォームは manual にフォールバックする
    （records があれば取り込め、無ければ安全に空を返す）。
    """
    key = normalize_platform(source_platform)
    adapter_cls = _ADAPTERS.get(key, ManualAdapter)
    return adapter_cls()


def supported_platforms() -> list[str]:
    return list(_ADAPTERS.keys())


def is_live_fetch(platform: str) -> bool:
    """そのプラットフォームが実サイト取得に対応済みか（＝実行可能か）。"""
    key = normalize_platform(platform)
    if key in FETCH_INJECT_PLATFORMS:
        return True
    adapter_cls = _ADAPTERS.get(key)
    return bool(adapter_cls and getattr(adapter_cls, "network_backed", False))


def query_supported(platform: str) -> bool:
    return normalize_platform(platform) in _QUERY_PLATFORMS


def needs_fetch_injection(platform: str) -> bool:
    return normalize_platform(platform) in FETCH_INJECT_PLATFORMS


@dataclass
class PlatformInfo:
    platform: str
    label: str
    available: bool          # 実取得対応（True）/ 準備中（False）
    query_supported: bool    # 検索クエリを使うか
    note: str


def _note(platform: str, available: bool, q: bool) -> str:
    if not available:
        return "未接続（準備中）。実行できません。"
    if q:
        return "実取得対応。検索クエリで案件を絞り込めます。"
    return "実取得対応。このサイトでは検索クエリは使用せず、新着・注目案件を取得します。"


def platform_availability() -> list[PlatformInfo]:
    """UI 表示用の各プラットフォームの対応状況（単一の真実源）。"""
    out: list[PlatformInfo] = []
    for p in DISCOVERY_PLATFORM_ORDER:
        available = is_live_fetch(p)
        q = query_supported(p)
        out.append(
            PlatformInfo(
                platform=p,
                label=_PLATFORM_LABELS.get(p, p),
                available=available,
                query_supported=q,
                note=_note(p, available, q),
            )
        )
    return out


__all__ = [
    "BaseAdapter",
    "DiscoveryCandidate",
    "get_adapter",
    "supported_platforms",
    "is_live_fetch",
    "query_supported",
    "needs_fetch_injection",
    "platform_availability",
    "PlatformInfo",
    "DISCOVERY_PLATFORM_ORDER",
    "FETCH_INJECT_PLATFORMS",
    "normalize_url",
    "normalize_status",
    "normalize_platform",
    "KickstarterAdapter",
    "IndiegogoAdapter",
    "BackerkitAdapter",
    "ManualAdapter",
    "WadizAdapter",
    "ZeczecAdapter",
    "UluleAdapter",
]
