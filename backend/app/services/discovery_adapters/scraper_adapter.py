"""既存スクレイパー流用型の Discovery adapter（Wadiz / Zeczec / Ulule / Indiegogo）。

新しいスクレイパーをゼロから作らず、projects 向け収集で実績のある既存スクレイパー
（``app.scrapers.registry.get_scraper``）をそのまま実行し、返り値 ``ProjectCreate`` を
発掘用の共通型 ``DiscoveryCandidate`` へ変換する。これにより取得ロジック（Wadiz の
公開 JSON API・Zeczec の /categories HTML・Ulule の公式 API・Indiegogo の SSR HTML）を
一切重複実装しない。

方針：
- 値が存在しない場合は推測せず None のままにする（推測メーカー/URL/メールを作らない）。
- 通貨（KRW/TWD/EUR/USD 等）は保持する。達成率は金額比（raised/goal）で扱うため
  通貨をまたいで誤解しない。
- Wadiz 詳細ページ（Akamai 403）や Zeczec 詳細（Cloudflare 403）へはこの段階で
  アクセスしない。一覧取得で得られる情報のみ保存する（詳細補完は既存の Contact
  Intelligence / Zeczec enrichment ジョブへ委ねる）。
- スクレイパーが分類付き例外（blocked / empty_result 等）を送出したらそのまま伝播させ、
  crawler.run が discovery_runs にエラーとして記録する（0 件を握りつぶさない）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.models.discovered_product import DiscoverySourcePlatform
from app.models.project import SourceSite
from app.schemas.project import ProjectCreate
from app.services.discovery_adapters.base import BaseAdapter, DiscoveryCandidate

logger = logging.getLogger("discovery_crawler.scraper_adapter")

# メモ（description）から「Country: X」「Status: X」を取り出す正規表現。
_MEMO_FIELD_RE = {
    "country": re.compile(r"Country:\s*([^·\n]+)"),
    "status": re.compile(r"Status:\s*([^·\n(]+)"),
}

# 一覧メモのステータス表記 → 共通ステータスは normalize_status に委譲する。
# ここでは括弧内の種別（例: "Live (Reward)"）を落として素の状態語だけを渡す。


def _memo_field(description: str | None, key: str) -> str | None:
    if not description:
        return None
    m = _MEMO_FIELD_RE[key].search(description)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _rate_percent(raised: Any, goal: Any) -> int | None:
    """達成率（%）= raised / goal × 100（整数）。通貨に依存しない比率。"""
    try:
        r = float(raised)
        g = float(goal)
    except (TypeError, ValueError):
        return None
    if g <= 0:
        return None
    return int(round(r / g * 100))


class ScraperBackedAdapter(BaseAdapter):
    """``get_scraper(site, limit)`` を実行して候補を得る adapter の基底。

    サブクラスは ``platform`` と ``site``（＋任意の ``default_country``）を設定する。
    """

    site: SourceSite = SourceSite.other
    default_country: str | None = None
    network_backed = True

    def _to_candidate(
        self, pc: ProjectCreate, *, enrichment: dict | None = None
    ) -> DiscoveryCandidate:
        """ProjectCreate（一覧取得結果）→ DiscoveryCandidate（発掘共通型）。

        存在しない値は推測せず None にする。通貨・生データ（必要最小限）を保持する。

        ``enrichment``（既存の Zeczec detail enrichment 等）が渡された場合は、その
        確認済み事実（maker_name / category / description / official_site_candidates）で
        欠損項目のみ**非破壊**に補完し、生データに残す。無ければ全て None のまま返す
        （enrichment が無くてもエラーにしない）。
        """
        country = _memo_field(pc.description, "country") or self.default_country
        status = _memo_field(pc.description, "status")  # normalize_status で丸める
        rate = _rate_percent(pc.raised_amount, pc.goal_amount)

        enr = enrichment or {}
        # enrichment の確認済み事実（推測しない）。scraper 値が無いときだけ採用する。
        maker_name = pc.maker_name or enr.get("maker_name") or None
        category = pc.category or enr.get("category") or None
        # description は enrichment の実商品説明を優先（一覧の memo は状態要約のため）。
        description = enr.get("description") or pc.description or None
        official = pc.maker_url or enr.get("official_site_url") or None

        # 発掘元固有の生データ（必要最小限。推測値は入れない）。
        raw: dict[str, Any] = {}
        if pc.raised_amount is not None:
            raw["amount"] = str(pc.raised_amount)
        if rate is not None:
            raw["rate"] = rate
        if pc.backers_count is not None:
            # Wadiz は participants、その他は backers として同じ支援者数を残す。
            raw["participants" if self.site is SourceSite.wadiz else "backers"] = (
                pc.backers_count
            )
        if category:
            raw["categoryName"] = category
        if maker_name:
            raw["makerName"] = maker_name
        if pc.currency:
            raw["currency"] = pc.currency
        # enrichment があれば根拠を最小限そのまま残す（official_site_candidates 等）。
        if enr:
            raw["enrichment"] = {
                k: enr.get(k)
                for k in (
                    "maker_name", "category", "description",
                    "official_site_candidates", "official_site_url",
                )
                if enr.get(k) is not None
            } or None

        return DiscoveryCandidate(
            source_platform=self.platform,
            source_url=pc.source_url,
            project_title=pc.title,
            product_name=pc.title,
            creator_name=maker_name,
            category=category,
            description=description,
            image_url=pc.image_url or None,
            country=country,
            status=status,
            currency=pc.currency or None,
            funding_amount=pc.raised_amount,
            funding_goal=pc.goal_amount,
            backers_count=pc.backers_count,
            launch_date=pc.start_date,
            end_date=pc.end_date,
            # 一覧段階では公式サイトは未確定（推測しない）。source_url を営業起点に使う。
            official_website_url=official,
            raw_data=raw or None,
        )

    def discover(
        self,
        query: str | None = None,
        limit: int = 20,
        *,
        fetch_fn: Any = None,
        records: list[dict] | None = None,
        **_ignored: Any,
    ) -> list[DiscoveryCandidate]:
        """既存スクレイパーを実行して候補を返す。

        検索クエリは一覧取得型サイトでは使用しない（新着/注目案件を取得する）。
        scrape() が分類付き例外を送出したら握りつぶさず伝播させる。
        """
        limit = max(0, int(limit or 0))
        if limit == 0:
            return []
        from app.scrapers.registry import get_scraper

        scraper = get_scraper(self.site, limit=limit)
        projects = scraper.scrape()  # 失敗時は分類付き例外を送出（crawler が記録）
        out: list[DiscoveryCandidate] = []
        for pc in projects[:limit]:
            try:
                out.append(self._to_candidate(pc))
            except Exception as exc:  # noqa: BLE001  1 件失敗は継続
                logger.warning("discovery candidate 変換失敗, skip: %s", exc)
        return out


class WadizAdapter(ScraperBackedAdapter):
    platform = DiscoverySourcePlatform.wadiz.value
    site = SourceSite.wadiz
    default_country = "South Korea"


class ZeczecAdapter(ScraperBackedAdapter):
    platform = DiscoverySourcePlatform.zeczec.value
    site = SourceSite.zeczec
    default_country = "Taiwan"


class UluleAdapter(ScraperBackedAdapter):
    platform = DiscoverySourcePlatform.ulule.value
    site = SourceSite.ulule
    default_country = None  # 案件により異なるため推測しない（メモから取得）


class IndiegogoScraperAdapter(ScraperBackedAdapter):
    platform = DiscoverySourcePlatform.indiegogo.value
    site = SourceSite.indiegogo
    default_country = None
