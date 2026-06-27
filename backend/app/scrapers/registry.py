"""サイト → スクレイパー の対応表。

実装済みサイトから順に実スクレイパーへ差し替える。
Step 3-2 時点：Kickstarter のみ実装。残りはダミー。
"""
from __future__ import annotations

from app.config import settings
from app.models.project import SALES_TARGET_SITES, SourceSite
from app.scrapers.base import BaseScraper
from app.scrapers.dummy import DummyScraper
from app.scrapers.indiegogo import IndiegogoScraper
from app.scrapers.kickstarter import KickstarterScraper
from app.scrapers.ulule import UluleScraper

# 収集対象サイト（優先順位順）。
# 海外営業対象（Kickstarter / Indiegogo / Wadiz）のみ。projects テーブルに保存する。
# Makuake / GreenFunding は日本の成功事例（比較用）であり、この収集パイプライン
# では扱わない（japanese_success_service が japanese_success_projects へ収集する）。
SUPPORTED_SITES: list[SourceSite] = list(SALES_TARGET_SITES)


def get_scraper(site: SourceSite, limit: int = 20) -> BaseScraper:
    """サイトに対応するスクレイパーを返す。"""
    if site is SourceSite.kickstarter:
        # 初回検証の既定：1カテゴリ（Technology）・最大10件
        # 取得方法は設定（既定 playwright）で切り替え
        return KickstarterScraper(
            limit=min(limit, 10),
            fetch_method=settings.scrape_fetcher,
            rate_limit_seconds=settings.scrape_rate_limit_seconds,
            timeout=settings.scrape_timeout_seconds,
            retries=settings.scrape_retries,
        )
    if site is SourceSite.indiegogo:
        # 初回検証の既定：1カテゴリ（tech-innovation）・最大10件
        return IndiegogoScraper(
            limit=min(limit, 10),
            fetch_method=settings.scrape_fetcher,
            rate_limit_seconds=settings.scrape_rate_limit_seconds,
            timeout=settings.scrape_timeout_seconds,
            retries=settings.scrape_retries,
        )
    if site is SourceSite.ulule:
        # 初回検証の既定：1カテゴリ・最大10件（動的描画のため既定 playwright）
        return UluleScraper(
            limit=min(limit, 10),
            fetch_method=settings.scrape_fetcher,
            rate_limit_seconds=settings.scrape_rate_limit_seconds,
            timeout=settings.scrape_timeout_seconds,
            retries=settings.scrape_retries,
        )
    # 未実装サイトはダミーで配線を維持
    return DummyScraper(site=site, limit=limit)
