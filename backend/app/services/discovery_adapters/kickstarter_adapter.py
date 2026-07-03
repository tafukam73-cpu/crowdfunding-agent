"""Kickstarter 用 platform adapter（Discovery Engine v1-3）。

取得経路の想定は Kickstarter の discover advanced JSON（``{"projects": [...]}``）。
``fetch_fn`` がその本文（JSON 文字列）を返せば複数候補を抽出する。案件ページ
（HTML）が渡された場合は JSON-LD / meta の共通カスケードにフォールバックする。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.models.discovered_product import DiscoverySourcePlatform
from app.services.discovery_adapters.base import (
    BaseAdapter,
    DiscoveryCandidate,
    normalize_status,
)

logger = logging.getLogger("discovery_crawler.kickstarter")

DISCOVER_URL = "https://www.kickstarter.com/discover/advanced"


def _dig(d: Any, *path: str) -> Any:
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


# Kickstarter の state → 共通ステータス
_STATE_MAP = {
    "live": "live",
    "successful": "successful",
    "failed": "failed",
    "canceled": "canceled",
    "cancelled": "canceled",
    "suspended": "canceled",
    "purged": "canceled",
    "submitted": "unknown",
    "started": "live",
}


class KickstarterAdapter(BaseAdapter):
    platform = DiscoverySourcePlatform.kickstarter.value

    def build_query_url(self, query: str | None) -> str | None:
        # v1-3 では実検索は行わず、取得先の宣言のみ（fetch_fn がこの URL を解釈）。
        return DISCOVER_URL

    def parse_content(self, text: str, url: str | None = None) -> list[DiscoveryCandidate]:
        data = _try_json(text)
        if isinstance(data, dict) and isinstance(data.get("projects"), list):
            out: list[DiscoveryCandidate] = []
            for raw in data["projects"]:
                if not isinstance(raw, dict):
                    continue
                try:
                    out.append(self._from_project(raw))
                except Exception as exc:  # noqa: BLE001  1件失敗は継続
                    logger.warning("kickstarter candidate skip: %s", exc)
            return out
        # JSON でなければ案件ページ HTML とみなし共通カスケード
        return super().parse_content(text, url)

    def _from_project(self, raw: dict) -> DiscoveryCandidate:
        photo = raw.get("photo") or {}
        image = photo.get("full") or photo.get("1024x576") or photo.get("1024x768")
        raised = raw.get("usd_pledged")
        if raised is None:
            raised = raw.get("pledged")
        state = str(raw.get("state") or "").lower()
        status = _STATE_MAP.get(state) if state else None
        return DiscoveryCandidate(
            source_platform=self.platform,
            source_url=_dig(raw, "urls", "web", "project"),
            project_title=raw.get("name"),
            product_name=raw.get("name"),
            creator_name=_dig(raw, "creator", "name"),
            category=_dig(raw, "category", "name"),
            description=raw.get("blurb"),
            image_url=image,
            country=raw.get("country"),
            status=normalize_status(status) if status else None,
            funding_amount=raised,
            funding_goal=raw.get("goal"),
            backers_count=raw.get("backers_count"),
            launch_date=raw.get("launched_at"),
            end_date=raw.get("deadline"),
        )


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
