"""Discovery Crawler Framework の platform adapter 群（Discovery Engine v1-3）。

``get_adapter(source_platform)`` で発掘元に対応する adapter を得る。共通型
``DiscoveryCandidate`` はどの adapter でも同じ形で候補を返すための契約。
"""
from __future__ import annotations

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

# 発掘元プラットフォーム → adapter クラス
_ADAPTERS: dict[str, type[BaseAdapter]] = {
    DiscoverySourcePlatform.kickstarter.value: KickstarterAdapter,
    DiscoverySourcePlatform.indiegogo.value: IndiegogoAdapter,
    DiscoverySourcePlatform.backerkit.value: BackerkitAdapter,
    DiscoverySourcePlatform.manual.value: ManualAdapter,
}


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


__all__ = [
    "BaseAdapter",
    "DiscoveryCandidate",
    "get_adapter",
    "supported_platforms",
    "normalize_url",
    "normalize_status",
    "normalize_platform",
    "KickstarterAdapter",
    "IndiegogoAdapter",
    "BackerkitAdapter",
    "ManualAdapter",
]
