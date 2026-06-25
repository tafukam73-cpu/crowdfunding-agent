"""モック検索プロバイダー（実サイトへはアクセスしない）。

クエリ＋サイト名のハッシュから決定的にヒットを生成する（同じ案件は常に同じ結果）。
判定パイプライン・根拠保存・履歴・UI の動作確認に使う。実検索（楽天/Yahoo API、
Amazon PA-API、Makuake/GreenFunding スクレイプ）は将来差し替える。
"""
from __future__ import annotations

import hashlib

from app.availability.providers.base import SearchHit, SearchProvider
from app.models.availability import AvailabilitySite

# このスコア以上のヒットがある確率・強さをサイトごとに微調整するための係数
_HIT_CHANCE = 45  # %（各サイトでヒットが出る確率）


class MockSearchProvider(SearchProvider):
    def __init__(self, site: AvailabilitySite) -> None:
        self.site = site

    def search(self, query: str) -> list[SearchHit]:
        seed = f"{self.site.value}|{query}".encode()
        h = int(hashlib.md5(seed).hexdigest(), 16)

        # ヒット有無（決定的）
        if h % 100 >= _HIT_CHANCE:
            return []

        score = 35 + (h % 61)  # 35〜95
        ident = h % 1_000_000
        return [
            SearchHit(
                site=self.site.value,
                title=f"[{self.site.value}] {query[:40]} 関連商品",
                url=f"https://example.com/{self.site.value}/item/{ident}",
                match_score=score,
            )
        ]
