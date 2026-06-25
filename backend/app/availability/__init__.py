"""日本未上陸判定の検索プロバイダー・ファクトリ。

5 サイト（Amazon.co.jp / 楽天 / Yahoo!ショッピング / Makuake / GreenFunding）の
検索プロバイダーを返す。現状はすべて mock。将来、楽天/Yahoo は公式 API、
Makuake/GreenFunding は実スクレイパーへ差し替え可能な構造にしている。
"""
from __future__ import annotations

from app.availability.providers.base import SearchHit, SearchProvider
from app.availability.providers.mock import MockSearchProvider
from app.models.availability import AvailabilitySite

# 判定対象サイト（表示順）
TARGET_SITES: list[AvailabilitySite] = [
    AvailabilitySite.amazon,
    AvailabilitySite.rakuten,
    AvailabilitySite.yahoo,
    AvailabilitySite.makuake,
    AvailabilitySite.greenfunding,
]


def get_search_providers() -> list[SearchProvider]:
    """判定に使う検索プロバイダー一覧を返す（現状すべて mock）。"""
    return [MockSearchProvider(site) for site in TARGET_SITES]


__all__ = [
    "SearchHit",
    "SearchProvider",
    "TARGET_SITES",
    "get_search_providers",
]
