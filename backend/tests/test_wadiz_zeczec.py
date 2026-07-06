"""Wadiz / Zeczec スクレイパーのオフライン検証（パース・正規化・エラー分類）。

実ネットワーク不要。fixture（API JSON / 一覧 HTML）から ProjectCreate への正規化と、
取得失敗時のエラー分類（blocked / empty_result / network / structure）を検証する。

実行（backend ディレクトリで）:
    python tests/test_wadiz_zeczec.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from app.models.project import SourceSite  # noqa: E402
from app.models.scrape_run import ErrorKind  # noqa: E402
from app.scrapers import wadiz as W  # noqa: E402
from app.scrapers import zeczec as Z  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


# ---------------------------------------------------------------------------
#  Wadiz
# ---------------------------------------------------------------------------
_WADIZ_ITEM = {
    "id": 405426,
    "productType": "REWARD",
    "title": "초슬림 프리미엄 제습기",
    "mainCategoryName": "가전",
    "categoryName": "생활가전",
    "participants": 1011,
    "linkUrl": "/web/campaign/detail/405426",
    "thumbnail": "https://cdn3.wadiz.kr/studio/images/x.jpeg",
    "makerName": "와이투에스 코리아",
    "amount": 87903000,
    "rate": 17580,  # 17580% → goal = amount*100/17580
    "startDate": "2026-05-21T14:00:00+09:00",
    "endDate": "2026-06-30T23:59:59+09:00",
    "remainingDay": 5,
}


def test_wadiz_normalize():
    print("test_wadiz_normalize")
    p = W.normalize(_WADIZ_ITEM)
    check("source_site=wadiz", p.source_site == SourceSite.wadiz)
    check("通貨=KRW", p.currency == "KRW")
    check("タイトル保持", p.title == "초슬림 프리미엄 제습기")
    check("URL は絶対化", p.source_url == "https://www.wadiz.kr/web/campaign/detail/405426")
    check("調達額", int(p.raised_amount) == 87903000)
    # goal = 87903000 * 100 / 17580 = 500,017（四捨五入）
    check("目標額を達成率から逆算", p.goal_amount is not None and int(p.goal_amount) == 500017)
    check("支援者数", p.backers_count == 1011)
    check("カテゴリ", p.category == "생활가전")
    check("メーカー名", p.maker_name == "와이투에스 코리아")
    check("画像URL", p.image_url and p.image_url.startswith("https://cdn3.wadiz.kr"))
    check("国をメモに記録", "Country: South Korea" in (p.description or ""))
    check("達成率をメモに記録", "17580%" in (p.description or ""))
    check("開始日", p.start_date is not None and p.start_date.year == 2026)


def test_wadiz_scraper_paging_and_limit():
    print("test_wadiz_scraper_paging_and_limit")
    pages = {
        0: {"code": 200, "data": [dict(_WADIZ_ITEM, id=i) for i in range(1, 6)]},
        1: {"code": 200, "data": [dict(_WADIZ_ITEM, id=i) for i in range(5, 9)]},  # id=5 は重複
    }

    def page_get(page, size):
        return pages.get(page, {"data": []})

    s = W.WadizScraper(limit=7, page_size=5, max_pages=5, page_get=page_get)
    items = s.scrape()
    # ページ0で5件 + ページ1で新規3件（id5は重複）= 8 だが limit=7 で打ち切り
    check("limit で打ち切り", len(items) == 7)
    check("全件 wadiz", all(i.source_site == SourceSite.wadiz for i in items))


def test_wadiz_empty_and_blocked():
    print("test_wadiz_empty_and_blocked")
    s = W.WadizScraper(limit=5, page_get=lambda p, sz: {"data": []})
    try:
        s.scrape()
        check("空は例外", False)
    except W.WadizScrapeError as e:
        check("空は empty_result", e.error_kind == ErrorKind.empty_result)

    def blocked(page, size):
        raise W.WadizScrapeError("blocked", ErrorKind.blocked)

    s2 = W.WadizScraper(limit=5, page_get=blocked)
    try:
        s2.scrape()
        check("ブロックは例外", False)
    except W.WadizScrapeError as e:
        check("ブロックは blocked", e.error_kind == ErrorKind.blocked)

    def netfail(page, size):
        raise httpx.ConnectError("boom")

    s3 = W.WadizScraper(limit=5, page_get=netfail)
    try:
        s3.scrape()
        check("ネットワーク失敗は例外", False)
    except W.WadizScrapeError as e:
        check("ネットワーク失敗は network", e.error_kind == ErrorKind.network)


# ---------------------------------------------------------------------------
#  Zeczec
# ---------------------------------------------------------------------------
_ZEC_HTML = """
<html><body>
<div class='container'>
  <a data-content-type="project" data-item-id="inopro" href="/projects/inopro">
    <div><img data-src="https://assets.zeczec.com/asset_1.jpg" src=""/>
      <h3 class='font-bold'>INOPRO 牙齒淨白貼片</h3>
      <div>1,144% NT$114,406 person70 人 hourglass_top 25 天</div>
    </div>
  </a>
  <a data-content-type="project" data-item-id="fanx" href="/projects/UC614V1?ref=x">
    <div><img data-src="https://assets.zeczec.com/asset_2.jpg"/>
      <h3>復刻阿嬤家的經典電風扇</h3>
      <div>NT$543,319 person185 人</div>
    </div>
  </a>
  <a href="/about">not a project</a>
</div>
</body></html>
"""


class _FakeFetcher:
    def __init__(self, html):
        self.html = html

    def get_text(self, url):
        return self.html

    def close(self):
        pass


def test_zeczec_parse_and_normalize():
    print("test_zeczec_parse_and_normalize")
    cards = Z.parse_list_html(_ZEC_HTML)
    check("プロジェクトカードを2件抽出", len(cards) == 2)
    check("about は除外", all("/projects/" in c["url"] for c in cards))

    p = Z.normalize(cards[0])
    check("source_site=zeczec", p.source_site == SourceSite.zeczec)
    check("通貨=TWD", p.currency == "TWD")
    check("タイトル", p.title == "INOPRO 牙齒淨白貼片")
    check("URL 絶対化・クエリ除去", p.source_url == "https://www.zeczec.com/projects/inopro")
    check("調達額", int(p.raised_amount) == 114406)
    # goal = 114406*100/1144 = 10000.5 → 四捨五入 10001（rate=1144）
    check("目標額を達成率から逆算", p.goal_amount is not None and int(p.goal_amount) == 10001)
    check("支援者数", p.backers_count == 70)
    check("画像URL", p.image_url == "https://assets.zeczec.com/asset_1.jpg")
    check("国をメモに記録", "Country: Taiwan" in (p.description or ""))

    # 2件目：達成率なし → goal は None でも落ちない
    p2 = Z.normalize(cards[1])
    check("達成率なしでも正規化", p2.raised_amount is not None and p2.goal_amount is None)
    check("2件目 URL のクエリ除去", p2.source_url == "https://www.zeczec.com/projects/UC614V1")


def test_zeczec_scraper_and_errors():
    print("test_zeczec_scraper_and_errors")
    s = Z.ZeczecScraper(limit=5, list_urls=["x"], fetcher=_FakeFetcher(_ZEC_HTML))
    items = s.scrape()
    check("scraper が2件返す", len(items) == 2)
    check("全件 zeczec", all(i.source_site == SourceSite.zeczec for i in items))

    # 空 HTML → empty_result
    s2 = Z.ZeczecScraper(limit=5, list_urls=["x"], fetcher=_FakeFetcher("<html></html>"))
    try:
        s2.scrape()
        check("空は例外", False)
    except Z.ZeczecScrapeError as e:
        check("空は empty_result", e.error_kind == ErrorKind.empty_result)

    # ブロックページ → blocked
    s3 = Z.ZeczecScraper(
        limit=5, list_urls=["x"], fetcher=_FakeFetcher("<html>Access Denied</html>")
    )
    try:
        s3.scrape()
        check("ブロックは例外", False)
    except Z.ZeczecScrapeError as e:
        check("ブロックは blocked", e.error_kind == ErrorKind.blocked)


if __name__ == "__main__":
    test_wadiz_normalize()
    test_wadiz_scraper_paging_and_limit()
    test_wadiz_empty_and_blocked()
    test_zeczec_parse_and_normalize()
    test_zeczec_scraper_and_errors()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
