"""ダミースクレイパー（配線確認用）。

実際のサイトへはアクセスせず、サイトごとに決め打ちのサンプル案件を返す。
source_url を固定にしているため、再実行すると新規ではなく更新として
upsert されることを確認できる（パイプラインの冪等性チェック）。

Step 3-2 以降で各サイトの実スクレイパーへ順次置き換える。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.project import SourceSite
from app.schemas.project import ProjectCreate
from app.scrapers.base import BaseScraper


class DummyScraper(BaseScraper):
    """任意サイト向けのダミー実装。"""

    def __init__(self, site: SourceSite, limit: int = 20) -> None:
        super().__init__(limit=limit)
        self.site = site

    def scrape(self) -> list[ProjectCreate]:
        items: list[ProjectCreate] = []
        count = min(self.limit, 3)
        for i in range(1, count + 1):
            slug = f"{self.site.value}-dummy-{i}"
            items.append(
                ProjectCreate(
                    title=f"[DUMMY] {self.site.value} サンプル案件 {i}",
                    source_site=self.site,
                    source_url=f"https://example.com/{self.site.value}/{slug}",
                    category="ガジェット",
                    description=f"{self.site.value} 用の配線確認ダミーデータ（{i}）。",
                    image_url=f"https://picsum.photos/seed/{slug}/640/360",
                    video_url=None,
                    currency="USD",
                    goal_amount=Decimal("10000.00"),
                    raised_amount=Decimal(str(10000 * i)),
                    backers_count=100 * i,
                    start_date=date(2026, 6, 1),
                    end_date=date(2026, 7, 1),
                    maker_name=f"Dummy Maker {i}",
                    maker_url="https://example.com/maker",
                    contact_info="dummy@example.com",
                )
            )
        return items
