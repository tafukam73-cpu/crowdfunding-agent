"""手動投入用 platform adapter（Discovery Engine v1-3）。

ネットワークを一切使わず、与えられたレコード（dict）をそのまま
``DiscoveryCandidate`` に写す。オペレーターが見つけた案件の手入力や、
他システムからの取り込み、テストのシード投入に使う。
"""
from __future__ import annotations

import logging
from dataclasses import fields
from typing import Any

from app.models.discovered_product import DiscoverySourcePlatform
from app.services.discovery_adapters.base import (
    BaseAdapter,
    DiscoveryCandidate,
)

logger = logging.getLogger("discovery_crawler.manual")

_FIELD_NAMES = {f.name for f in fields(DiscoveryCandidate)}


class ManualAdapter(BaseAdapter):
    platform = DiscoverySourcePlatform.manual.value

    def discover(
        self,
        query: str | None = None,
        limit: int = 20,
        *,
        fetch_fn: Any = None,  # 使わない（ネットワーク不要）
        records: list[dict] | None = None,
        **_ignored: Any,
    ) -> list[DiscoveryCandidate]:
        """与えられた records を候補に変換して返す（最大 limit 件）。"""
        limit = max(0, int(limit or 0))
        if not records or limit == 0:
            return []
        candidates: list[DiscoveryCandidate] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            data = {k: v for k, v in rec.items() if k in _FIELD_NAMES}
            candidate = DiscoveryCandidate(**data)
            if not candidate.source_platform:
                candidate.source_platform = self.platform
            candidates.append(candidate)
        return candidates[:limit]
