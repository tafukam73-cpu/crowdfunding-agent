"""Phase 2 人物抽出 precision（Step A: 非maker/UI由来人物の除去）の単体検証。

gold 案件 ID や正解人物名で分岐しない。一般ルール（source 所有判定 × UI語ガード ×
extract_people_from_html）が正しく働くことを機能別に検証する。pytest 非依存で実行できる。

実行: docker exec cfagent-backend python tests/test_contact_precision_phase2_person.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.contact_hunter import looks_like_person_name  # noqa: E402
from app.ai.mock_contact_hunter import extract_people_from_html  # noqa: E402
from app.services import source_ownership as so  # noqa: E402

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  - {name}")
    else:
        _f += 1
        print(f"  FAIL- {name}")


# ---- source 所有判定 ----
def test_platform_source_rejected():
    print("test_platform_source_rejected")
    for u in ("https://www.kickstarter.com/profile/x",
              "https://www.indiegogo.com/individuals/123",
              "https://www.wadiz.kr/web/campaign/detail/1",
              "https://www.zeczec.com/projects/abc",
              "https://ulule.com/foo/"):
        check(f"platform source 除外: {u}", not so.is_maker_owned_person_source(u))


def test_thirdparty_source_rejected():
    print("test_thirdparty_source_rejected")
    for u in ("https://ideafound.com/team", "https://reurl.cc/abc",
              "https://m.me/foo", "https://www.amazon.com/dp/x",
              "https://hanboost.kickbooster.me/x"):
        check(f"第三者 source 除外: {u}", not so.is_maker_owned_person_source(u))


def test_maker_owned_source_kept():
    print("test_maker_owned_source_kept")
    for u in ("https://schweizercomics.com/contact", "https://alltimelab.com/about",
              "https://nocfree.kr/", "https://www.hanboost.com/pages/contact"):
        check(f"maker 所有 source 維持: {u}", so.is_maker_owned_person_source(u))
    # official 指定時は同一登録ドメインのみ
    check("official 一致は維持",
          so.is_maker_owned_person_source("https://shop.sharge.com/x", "sharge.com"))
    check("official 不一致は除外",
          not so.is_maker_owned_person_source("https://other.com/x", "sharge.com"))


# ---- UI chrome ガード ----
def test_ui_chrome_not_person_name():
    print("test_ui_chrome_not_person_name")
    for s in ("Open Calls", "Combined Shape", "Cookie Settings", "Skip Navigation",
              "Load More", "Sign In", "Learn More"):
        check(f"UI片は人名でない: {s!r}", not looks_like_person_name(s))


def test_real_names_still_valid():
    print("test_real_names_still_valid")
    for s in ("Chris Schweizer", "ZOU WENXIAO", "Maria Garcia", "Jean Dupont"):
        check(f"実名は維持: {s!r}", looks_like_person_name(s))


# ---- extract_people_from_html ページ所有ガード ----
_KS_HTML = (
    '<html><body><nav>'
    '<a>Open Calls</a><span>Make 100 Zine Quest</span>'
    '<button>Combined Shape</button><span>Press to copy</span>'
    '<p>Chris Schweizer, Founder</p>'
    '</body></html>'
)


def test_extract_skips_platform_page():
    print("test_extract_skips_platform_page")
    people = extract_people_from_html(_KS_HTML, "https://www.kickstarter.com/projects/schweizer/x")
    check("platform ページからは人物を抽出しない", people == [])


def test_extract_on_maker_page_no_ui_fp():
    print("test_extract_on_maker_page_no_ui_fp")
    people = extract_people_from_html(_KS_HTML, "https://schweizercomics.com/about")
    names = {(p.name or "").lower() for p in people}
    check("UI片 Open Calls を人物化しない", "open calls" not in names)
    check("UI片 Combined Shape を人物化しない", "combined shape" not in names)


def main():
    test_platform_source_rejected()
    test_thirdparty_source_rejected()
    test_maker_owned_source_kept()
    test_ui_chrome_not_person_name()
    test_real_names_still_valid()
    test_extract_skips_platform_page()
    test_extract_on_maker_page_no_ui_fp()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
