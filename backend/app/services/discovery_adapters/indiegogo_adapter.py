"""Indiegogo 用 platform adapter（Discovery Engine v1-3）。

Indiegogo はキャンペーン情報を埋め込み JSON（``window.__CAMPAIGN__`` 等）や
JSON-LD で持つことが多い。抽出は堅牢性のため次の順で試みる：

  1. discover/検索 JSON（``{"response": {"campaigns": [...]}}`` / ``{"campaigns": [...]}``）
  2. 埋め込み JSON（``gon.campaign`` / ``window.__CAMPAIGN__`` / ``window.__INITIAL_STATE__``）
  3. JSON-LD / meta / HTML（共通カスケード）
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

logger = logging.getLogger("discovery_crawler.indiegogo")

DISCOVER_URL = "https://www.indiegogo.com/explore/all"

# 埋め込みキャンペーン JSON を拾う正規表現（代表的な変数名を順に試す）
_EMBED_RES = (
    re.compile(r"gon\.campaign\s*=\s*(\{.*?\})\s*;", re.DOTALL),
    re.compile(r"window\.__CAMPAIGN__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL),
    re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", re.DOTALL),
)


def _dig(d: Any, *path: str) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


class IndiegogoAdapter(BaseAdapter):
    platform = DiscoverySourcePlatform.indiegogo.value

    def build_query_url(self, query: str | None) -> str | None:
        return DISCOVER_URL

    def parse_content(self, text: str, url: str | None = None) -> list[DiscoveryCandidate]:
        # 1) discover/検索 JSON
        data = _try_json(text)
        if isinstance(data, dict):
            camps = data.get("campaigns") or _dig(data, "response", "campaigns")
            if isinstance(camps, list):
                return [self._from_campaign(c) for c in camps if isinstance(c, dict)]

        # 2) 埋め込み JSON（+ 共通カスケードで本文を補完）
        embed = _find_embedded(text)
        cascade = parse_page_cascade(text, url)
        if embed is not None:
            candidate = self._from_campaign(embed)
            if cascade is not None:
                candidate.merge_missing(cascade)
            return [candidate]

        # 3) JSON-LD / meta / HTML のみ
        return [cascade] if cascade else []

    def _from_campaign(self, raw: dict) -> DiscoveryCandidate:
        status = raw.get("status") or raw.get("state")
        return DiscoveryCandidate(
            source_platform=self.platform,
            source_url=raw.get("url") or raw.get("clickthrough_url")
            or raw.get("preview_url"),
            project_title=raw.get("title") or raw.get("name"),
            product_name=raw.get("title") or raw.get("name"),
            creator_name=raw.get("owner_name") or _dig(raw, "owner", "name")
            or raw.get("creator"),
            category=raw.get("category") or _dig(raw, "category", "name"),
            description=raw.get("tagline") or raw.get("description")
            or raw.get("blurb"),
            image_url=raw.get("image_url") or raw.get("thumbnail_image_url"),
            country=raw.get("country") or _dig(raw, "location", "country"),
            status=normalize_status(status) if status else None,
            funding_amount=raw.get("collected_funds") or raw.get("balance")
            or raw.get("funds_raised_amount"),
            funding_goal=raw.get("goal") or raw.get("funding_goal")
            or raw.get("goal_amount"),
            backers_count=raw.get("contributions_count") or raw.get("backers_count")
            or raw.get("contributors"),
            launch_date=raw.get("launched_at") or raw.get("start_date"),
            end_date=raw.get("deadline") or raw.get("end_date")
            or raw.get("funding_ends_at"),
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
            # __INITIAL_STATE__ は campaign をネストして持つことがある
            if "campaign" in data and isinstance(data["campaign"], dict):
                return data["campaign"]
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
