"""BackerKit（Crowdfunding / pledge manager）用 platform adapter（v1-3）。

BackerKit のプロジェクトページは JSON-LD（Product / CreativeWork）や og: meta を
持つことが多い。よって共通カスケード（JSON-LD → meta → HTML）を主経路とし、
支援額・支援者数・ステータスは埋め込み JSON があれば補完する。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models.discovered_product import DiscoverySourcePlatform
from app.services.discovery_adapters.base import (
    BaseAdapter,
    DiscoveryCandidate,
    normalize_status,
    parse_page_cascade,
)

logger = logging.getLogger("discovery_crawler.backerkit")

DISCOVER_URL = "https://www.backerkit.com/discover"

_EMBED_RES = (
    re.compile(r"window\.__PROJECT__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL),
    re.compile(r'"project"\s*:\s*(\{.*?\})\s*[,}]', re.DOTALL),
)


class BackerkitAdapter(BaseAdapter):
    platform = DiscoverySourcePlatform.backerkit.value

    def build_query_url(self, query: str | None) -> str | None:
        return DISCOVER_URL

    def parse_content(self, text: str, url: str | None = None) -> list[DiscoveryCandidate]:
        # 検索/一覧 JSON（{"projects": [...]}）を最初に試す
        data = _try_json(text)
        if isinstance(data, dict) and isinstance(data.get("projects"), list):
            return [
                self._from_project(p) for p in data["projects"] if isinstance(p, dict)
            ]

        cascade = parse_page_cascade(text, url)
        embed = _find_embedded(text)
        if embed is not None:
            candidate = self._from_project(embed)
            if cascade is not None:
                candidate.merge_missing(cascade)
            return [candidate]
        return [cascade] if cascade else []

    def _from_project(self, raw: dict) -> DiscoveryCandidate:
        status = raw.get("status") or raw.get("state")
        return DiscoveryCandidate(
            source_platform=self.platform,
            source_url=raw.get("url") or raw.get("project_url"),
            project_title=raw.get("name") or raw.get("title"),
            product_name=raw.get("name") or raw.get("title"),
            creator_name=raw.get("creator") or raw.get("creator_name")
            or raw.get("owner"),
            category=raw.get("category"),
            description=raw.get("tagline") or raw.get("description"),
            image_url=raw.get("image_url") or raw.get("image"),
            country=raw.get("country"),
            status=normalize_status(status) if status else None,
            funding_amount=raw.get("raised") or raw.get("amount_raised")
            or raw.get("pledged"),
            funding_goal=raw.get("goal") or raw.get("goal_amount"),
            backers_count=raw.get("backers") or raw.get("backers_count"),
            launch_date=raw.get("launched_at") or raw.get("start_date"),
            end_date=raw.get("ends_at") or raw.get("end_date"),
        )


def _find_embedded(text: str) -> dict | None:
    for pat in _EMBED_RES:
        m = pat.search(text or "")
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _try_json(text: str) -> Any:
    if not text:
        return None
    stripped = text.lstrip()
    if not stripped.startswith("{") and not stripped.startswith("["):
        return None
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        return None
