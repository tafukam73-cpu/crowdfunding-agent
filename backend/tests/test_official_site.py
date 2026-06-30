"""公式サイト判定（プラットフォーム URL を公式として採用しない）の検証。

Kickstarter/Indiegogo/Ulule/Makuake/CAMPFIRE/GREEN FUNDING/READYFOR 等のクラファン
URL は official_site_url にしない。クラファン/プロフィールページ本文の外部リンク
（Official Website 等）から実際の企業ドメインを推定することを確認する。

実行（backend ディレクトリで）:
    python tests/test_official_site.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import web_research_service as w  # noqa: E402

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


PLATFORM_URLS = [
    "https://www.kickstarter.com/profile/lunosama",
    "https://www.kickstarter.com/projects/luno/luno",
    "https://www.indiegogo.com/individuals/123",
    "https://www.ulule.com/narrationos/",
    "https://www.makuake.com/project/x/",
    "https://camp-fire.jp/projects/view/123",
    "https://greenfunding.jp/lab/projects/123",
    "https://readyfor.jp/projects/123",
]


def test_platform_detection():
    print("test_platform_detection")
    for u in PLATFORM_URLS:
        check(f"is_platform_url: {cds._domain_of(u)}", cds.is_platform_url(u) is True)
        check(f"official_site_or_none -> None: {cds._domain_of(u)}",
              cds.official_site_or_none(u) is None)
    check("通常ドメインは公式として採用", cds.official_site_or_none("https://vitesy.com") == "https://vitesy.com")
    check("通常ドメインは platform でない", cds.is_platform_url("https://vitesy.com") is False)


def _project(maker_url, source_url, *, title, maker):
    class P:
        id = 1
        description = ""
        description_clean = ""
    P.title = title
    P.maker_name = maker
    P.maker_url = maker_url
    P.source_url = source_url
    P.source_site = "kickstarter" if "kickstarter" in source_url else (
        "ulule" if "ulule" in source_url else "indiegogo")
    return P()


def _run_both(project, pages):
    def fetch(u):
        return pages.get(u.rstrip("/")) or pages.get(u)
    res = w.web_research(project, None, fetch_fn=fetch, search_fn=lambda q: [])
    dsc = cds.discover(project, None, fetch_fn=fetch)
    return res["official_site_url"], dsc["official_site_url"]


def test_cases_not_platform():
    """NarrationOS / Luno / Vitesy で official_site_url がクラファンドメインにならない。"""
    print("test_cases_not_platform")

    # Luno（Kickstarter profile）→ プロフィール本文から外部公式
    luno = _project(
        "https://www.kickstarter.com/profile/lunosama",
        "https://www.kickstarter.com/projects/luno/luno-smart-helmet",
        title="Luno Smart Helmet", maker="Luno")
    luno_pages = {
        luno.maker_url: '<html><body><a href="https://lunohelmet.com">Official Website</a></body></html>',
        luno.source_url: '<html><body><a href="https://www.kickstarter.com/profile/lunosama">Creator</a></body></html>',
        "https://lunohelmet.com": '<html><body><a href="mailto:hello@lunohelmet.com">m</a></body></html>',
    }
    w_off, d_off = _run_both(luno, luno_pages)
    check("Luno web: 外部公式", w_off == "https://lunohelmet.com")
    check("Luno discover: 外部公式", d_off == "https://lunohelmet.com")
    check("Luno: クラファンドメインでない", "kickstarter.com" not in (w_off or "") and "kickstarter.com" not in (d_off or ""))

    # NarrationOS（Ulule）→ 案件本文の External Link から外部公式
    nos = _project(
        "https://www.ulule.com/narrationos/",
        "https://www.ulule.com/narrationos/",
        title="NarrationOS", maker="NarrationOS")
    nos_pages = {
        "https://www.ulule.com/narrationos": '<html><body><a href="https://narrationos.io">External Link</a></body></html>',
        "https://narrationos.io": '<html><body><a href="mailto:info@narrationos.io">m</a></body></html>',
    }
    w_off, d_off = _run_both(nos, nos_pages)
    check("NarrationOS web: 外部公式", w_off == "https://narrationos.io")
    check("NarrationOS: ulule.com にならない", "ulule.com" not in (w_off or "") and "ulule.com" not in (d_off or ""))

    # Vitesy（Indiegogo, maker_url 無し）→ 案件本文の公式リンク
    vit = _project(
        None,
        "https://www.indiegogo.com/projects/vitesy-fruit-bowl",
        title="Vitesy Fruit Bowl", maker="Vitesy")
    vit_pages = {
        vit.source_url: '<html><body><a href="https://vitesy.com">Official Website</a></body></html>',
        "https://vitesy.com": '<html><body><a href="mailto:hello@vitesy.com">m</a></body></html>',
    }
    w_off, d_off = _run_both(vit, vit_pages)
    check("Vitesy web: 公式 vitesy.com", w_off == "https://vitesy.com")
    check("Vitesy: indiegogo.com にならない", "indiegogo.com" not in (w_off or ""))


def test_not_found():
    """外部公式リンクが無ければ official_site_url は None（= 公式サイト未発見）。"""
    print("test_not_found")
    luno = _project(
        "https://www.kickstarter.com/profile/lunosama",
        "https://www.kickstarter.com/projects/luno/luno",
        title="Luno", maker="Luno")
    pages = {luno.maker_url: "<html><body>no external links</body></html>",
             luno.source_url: "<html><body>x</body></html>"}
    w_off, d_off = _run_both(luno, pages)
    check("web: 未発見は None", w_off is None)
    check("discover: 未発見は None", d_off is None)


def main():
    test_platform_detection()
    test_cases_not_platform()
    test_not_found()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
