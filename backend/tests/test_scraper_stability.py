"""スクレイパー安定化のオフライン検証（ネットワーク/PostgreSQL 不要）。

構造変化検知（ScraperStructureError）・エラー分類・取得成功率集計を、
fetcher 注入と in-memory SQLite で検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_scraper_stability.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# app.db.session が import 時に engine を作るため、PostgreSQL ドライバを避けて
# SQLite に差し替える（このテストは実 DB に接続しない）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# backend/ を import パスに追加（どこから実行しても通るように）
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.models.project import SourceSite  # noqa: E402
from app.models.scrape_run import ErrorKind, ScrapeRun, ScrapeStatus  # noqa: E402
from app.scrapers.base import ScraperStructureError  # noqa: E402
from app.scrapers.indiegogo import IndiegogoScraper  # noqa: E402
from app.scrapers.kickstarter import KickstarterScraper  # noqa: E402
from app.services import scrape_monitor  # noqa: E402
from app.services.collector import classify_error  # noqa: E402

FIXTURE = BACKEND / "tests" / "fixtures" / "kickstarter_discover.json"

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}")


class FakeFetcher:
    """get_json / get_text / close を満たす固定レスポンス fetcher。"""

    def __init__(self, json_data=None, text: str = ""):
        self._json = json_data
        self._text = text
        self.closed = False

    def get_json(self, url, *, params=None):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json

    def get_text(self, url):
        return self._text

    def close(self):
        self.closed = True


def test_kickstarter_structure_detection() -> None:
    with open(FIXTURE, encoding="utf-8") as f:
        discover = json.load(f)

    ks = KickstarterScraper(fetch_detail=False, fetcher=FakeFetcher(json_data=discover))
    check("KS 正常系で案件取得", len(ks.scrape()) == len(discover["projects"]))

    # projects キー欠落 → 構造エラー
    ks = KickstarterScraper(fetch_detail=False, fetcher=FakeFetcher(json_data={"x": 1}))
    check("KS projects 欠落で構造エラー", _raises(ks.scrape, ScraperStructureError))

    # projects 空 → 構造エラー
    ks = KickstarterScraper(fetch_detail=False, fetcher=FakeFetcher(json_data={"projects": []}))
    check("KS 空 projects で構造エラー", _raises(ks.scrape, ScraperStructureError))

    # ネットワーク例外は構造エラーにせず透過
    ks = KickstarterScraper(
        fetch_detail=False, fetcher=FakeFetcher(json_data=httpx.ConnectError("boom"))
    )
    check("KS ネットワーク例外は透過", _raises(ks.scrape, httpx.ConnectError))


_CARD_HTML = """
<html><body>
<div class="gfu-project-card" data-qa="c1">
  <a href="https://www.indiegogo.com/projects/foo" title="Foo Gadget">x</a>
  <img src="https://img/foo.jpg">
  <span data-qa="project-card:BackersCount">123</span>
  <span data-qa="project-card:FundsGathered">US$45,000</span>
  <span data-qa="project-card:TimeLeft">10 days left</span>
  <span data-qa="main-creator-name">Foo Inc</span>
</div>
</body></html>
"""


def test_indiegogo_structure_detection() -> None:
    ig = IndiegogoScraper(fetcher=FakeFetcher(text=_CARD_HTML))
    items = ig.scrape()
    check("IG 正常系で取得", len(items) == 1 and items[0].title == "Foo Gadget")

    ig = IndiegogoScraper(fetcher=FakeFetcher(text="<html><body>none</body></html>"))
    check("IG カード 0 枚で構造エラー", _raises(ig.scrape, ScraperStructureError))

    empty = '<div class="gfu-project-card" data-qa="c1"><img src="x"></div>'
    ig = IndiegogoScraper(fetcher=FakeFetcher(text=f"<html><body>{empty}</body></html>"))
    check("IG title/href 全欠落で構造エラー", _raises(ig.scrape, ScraperStructureError))


def test_classify_error() -> None:
    check("classify structure", classify_error(ScraperStructureError("x")) is ErrorKind.structure)
    check("classify network(httpx)", classify_error(httpx.ConnectError("x")) is ErrorKind.network)
    check("classify network(timeout)", classify_error(TimeoutError("x")) is ErrorKind.network)
    check("classify unknown", classify_error(ValueError("x")) is ErrorKind.unknown)


def test_success_rate_monitor() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    def add(site, status, kind=None):
        db.add(
            ScrapeRun(
                site=site.value,
                status=status.value,
                error_kind=kind.value if kind else None,
            )
        )
        db.commit()

    # KS: success x3, network x1, structure x1, running x1（running は集計外）
    add(SourceSite.kickstarter, ScrapeStatus.success)
    add(SourceSite.kickstarter, ScrapeStatus.success)
    add(SourceSite.kickstarter, ScrapeStatus.success)
    add(SourceSite.kickstarter, ScrapeStatus.error, ErrorKind.network)
    add(SourceSite.kickstarter, ScrapeStatus.error, ErrorKind.structure)
    db.add(ScrapeRun(site=SourceSite.kickstarter.value, status=ScrapeStatus.running.value))
    db.commit()

    ks = scrape_monitor.site_stats(db, SourceSite.kickstarter, window=20)
    check("monitor total=5（running除外）", ks.total == 5)
    check("monitor success_rate=0.6", ks.success_rate == 0.6)
    check("monitor structure_change_suspected", ks.structure_change_suspected is True)
    check("monitor last_structure_error_at", ks.last_structure_error_at is not None)
    check("monitor window 制限", scrape_monitor.site_stats(db, SourceSite.kickstarter, window=2).total == 2)

    # IG: 全 network エラー → degraded・構造変化なし
    for _ in range(3):
        add(SourceSite.indiegogo, ScrapeStatus.error, ErrorKind.network)
    ig = scrape_monitor.site_stats(db, SourceSite.indiegogo, window=20)
    check("monitor degraded", ig.degraded is True and ig.success_rate == 0.0)
    check("monitor degraded は構造変化扱いしない", ig.structure_change_suspected is False)

    rep = scrape_monitor.report(db, window=20)
    check("report 集約フラグ", rep.structure_change_suspected and rep.degraded)
    mk = next(s for s in rep.sites if s.site == SourceSite.makuake)
    check("未実行サイトは total=0/rate=None", mk.total == 0 and mk.success_rate is None)


def _raises(fn, exc_type) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def main() -> int:
    test_kickstarter_structure_detection()
    test_indiegogo_structure_detection()
    test_classify_error()
    test_success_rate_monitor()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
