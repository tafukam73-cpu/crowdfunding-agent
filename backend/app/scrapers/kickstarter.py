"""Kickstarter スクレイパー。

取得経路：Kickstarter の discover advanced JSON
  https://www.kickstarter.com/discover/advanced?category_id=<id>&sort=newest&format=json&page=1

discover JSON だけで大半の項目が取れる。video_url は一覧に無いため、
fetch_detail=True のとき案件ページを best-effort で取得して補完する
（取得不可なら null のまま）。

初回検証の既定：1 カテゴリ（Technology=16）・最大 10 件。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app.models.project import SourceSite
from app.schemas.project import ProjectCreate
from app.scrapers.base import BaseScraper, ScraperStructureError
from app.scrapers.fetcher import Fetcher, get_fetcher

logger = logging.getLogger("scraper.kickstarter")

DISCOVER_URL = "https://www.kickstarter.com/discover/advanced"

# Kickstarter のカテゴリ ID（親カテゴリ）
CATEGORY_TECHNOLOGY = 16


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _epoch_to_date(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        return None


def _dig(d: dict, *path):
    """ネストした dict を安全に辿る。"""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def normalize_project(raw: dict) -> ProjectCreate:
    """discover JSON の 1 案件を ProjectCreate へ正規化する。

    取得できない項目は None（null）で保存する。
    """
    photo = raw.get("photo") or {}
    image_url = photo.get("full") or photo.get("1024x576") or photo.get("1024x768")

    source_url = _dig(raw, "urls", "web", "project")
    category = _dig(raw, "category", "name")
    maker_name = _dig(raw, "creator", "name")
    maker_url = _dig(raw, "creator", "urls", "web", "user")

    # 調達額は usd_pledged を優先（無ければ pledged）
    raised = raw.get("usd_pledged")
    if raised is None:
        raised = raw.get("pledged")

    return ProjectCreate(
        title=raw.get("name") or "(no title)",
        source_site=SourceSite.kickstarter,
        source_url=source_url,
        category=category,
        description=raw.get("blurb"),
        image_url=image_url,
        video_url=None,  # 一覧JSONには無い → 詳細で補完
        currency=raw.get("currency") or "USD",
        goal_amount=_to_decimal(raw.get("goal")),
        raised_amount=_to_decimal(raised),
        backers_count=raw.get("backers_count"),
        start_date=_epoch_to_date(raw.get("launched_at")),
        end_date=_epoch_to_date(raw.get("deadline")),
        maker_name=maker_name,
        maker_url=maker_url,
        contact_info=None,  # 要件により無理に取得しない
    )


# 案件ページから動画URLを拾う best-effort 正規表現
_OG_VIDEO_RE = re.compile(r'<meta[^>]+property="og:video"[^>]+content="([^"]+)"')
_VIDEO_HLS_RE = re.compile(r'"(https://[^"]+\.mp4)"')


def extract_video_url(html: str) -> str | None:
    m = _OG_VIDEO_RE.search(html)
    if m:
        return m.group(1)
    m = _VIDEO_HLS_RE.search(html)
    if m:
        return m.group(1)
    return None


class KickstarterScraper(BaseScraper):
    site = SourceSite.kickstarter

    def __init__(
        self,
        *,
        limit: int = 10,
        category_id: int = CATEGORY_TECHNOLOGY,
        fetch_detail: bool = True,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        retries: int = 2,
        fetch_method: str = "httpx",
        fetcher: Fetcher | None = None,
    ) -> None:
        super().__init__(limit=limit)
        self.category_id = category_id
        self.fetch_detail = fetch_detail
        # 取得方法は httpx / playwright を切り替え可能（fetcher 直接注入も可）
        self._client: Fetcher = fetcher or get_fetcher(
            fetch_method,
            rate_limit_seconds=rate_limit_seconds,
            timeout=timeout,
            retries=retries,
        )
        self._owns_client = fetcher is None

    def scrape(self) -> list[ProjectCreate]:
        params = {
            "category_id": self.category_id,
            "sort": "newest",
            "format": "json",
            "page": 1,
        }
        try:
            data = self._client.get_json(DISCOVER_URL, params=params)

            # --- 構造変化検知 ---
            # discover JSON は必ず "projects" キーを持つ。欠落＝API レスポンス構造の
            # 変化（仕様変更/ブロックページ）とみなし、ネットワークエラーと区別する。
            if not isinstance(data, dict) or "projects" not in data:
                raise ScraperStructureError(
                    "Kickstarter discover JSON に 'projects' キーがありません"
                    "（構造変化またはブロックの可能性）"
                )
            raw_projects = (data.get("projects") or [])[: self.limit]

            results: list[ProjectCreate] = []
            for raw in raw_projects:
                try:
                    item = normalize_project(raw)
                except Exception as exc:  # noqa: BLE001  1件失敗は握りつぶし継続
                    logger.warning("normalize failed, skip: %s", exc)
                    continue

                if self.fetch_detail and item.source_url:
                    self._enrich_video(item)

                results.append(item)

            # 取得成功なのに 1 件も正規化できない＝カードのキー構成が変わった疑い。
            if not results:
                raise ScraperStructureError(
                    "Kickstarter：案件を 1 件も取得できませんでした"
                    "（構造変化またはブロックの可能性）"
                )

            return results
        finally:
            if self._owns_client:
                self._client.close()

    def _enrich_video(self, item: ProjectCreate) -> None:
        """案件ページから動画URLを best-effort 補完。失敗時は null のまま。"""
        try:
            html = self._client.get_text(item.source_url)  # type: ignore[arg-type]
            item.video_url = extract_video_url(html)
        except Exception as exc:  # noqa: BLE001
            logger.info("detail fetch skipped (%s): %s", item.source_url, exc)
