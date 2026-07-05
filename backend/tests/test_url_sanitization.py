"""ダミー/プレースホルダー URL（example.com 等）が API 境界で確実に除去されることの検証。

seed.py の *.example.com のような URL が Contact Intelligence（Contact Discovery /
Document Reader / Search Agent / v2）の公式サイト・参照URL・探索URL・検索クエリ・
外部連絡先リンクとして表示されないことを、DB を介さずスキーマ境界で確認する。
pytest 非依存。実行: python tests/test_url_sanitization.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "url_sanitization_test.sqlite"
)
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.schemas.contact_discovery import ContactDiscoveryOut  # noqa: E402
from app.services.contact_discovery_service import official_site_or_none  # noqa: E402
from app.services.email_validation import build_fallback_search_queries  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_official_site_or_none_blocks_dummy():
    print("test_official_site_or_none_blocks_dummy")
    check("example.com は公式にしない",
          official_site_or_none("https://greenlab.example.com") is None)
    check("サブドメイン example も弾く",
          official_site_or_none("https://www.greenlab.example.com") is None)
    check("dummy/test/localhost を弾く",
          official_site_or_none("https://dummy.io") is None
          and official_site_or_none("http://localhost:3000") is None)
    check("プラットフォームは弾く",
          official_site_or_none("https://www.kickstarter.com/projects/x") is None)
    check("実在ドメインは通す",
          official_site_or_none("https://greenlab.io") == "https://greenlab.io")


def test_fallback_no_site_query_for_dummy_domain():
    print("test_fallback_no_site_query_for_dummy_domain")
    q = build_fallback_search_queries(
        company_name="GreenLab", official_domain="greenlab.example.com"
    )
    site = [x for x in q if x["type"] == "site_search"]
    check("ダミードメインでは site: クエリを出さない", site == [])
    check("site:greenlab.example.com を含まない",
          all("example.com" not in x["query"] for x in q))
    q2 = build_fallback_search_queries(
        company_name="GreenLab", official_domain="greenlab.io"
    )
    check("実在ドメインでは site: クエリを出す",
          any(x["type"] == "site_search" for x in q2))


def _row(**kw) -> ContactDiscovery:
    base = dict(
        id=1, project_id=1, status="completed",
        ai_researched=False, web_researched=False, doc_reader_researched=False,
        search_agent_researched=False, recursive_crawl_enabled=False,
        v2_researched=True,
        created_at=datetime.now(), updated_at=datetime.now(),
    )
    base.update(kw)
    return ContactDiscovery(**base)


def test_api_boundary_strips_example_everywhere():
    print("test_api_boundary_strips_example_everywhere")
    row = _row(
        official_site_url="https://greenlab.example.com",
        doc_reader_official_site_url="https://greenlab.example.com",
        search_agent_official_site_url="https://greenlab.example.com",
        v2_official_site_url="https://greenlab.example.com",
        primary_contact_form_url="https://greenlab.example.com/contact",
        searched_urls=["https://greenlab.example.com", "https://real.io/a"],
        web_searched_urls=["https://greenlab.example.com/x"],
        discovered_forms=["https://greenlab.example.com/contact"],
        discovered_socials={
            "instagram": "https://instagram.com/greenlab",
            "x": "https://test.example.com",
        },
        ai_search_queries=["site:greenlab.example.com email", "GreenLab contact"],
        search_queries=["site:greenlab.example.com contact"],
        ai_sources=[{"url": "https://greenlab.example.com", "type": "site"},
                    {"url": "https://real.io", "type": "site"}],
        v2_steps=[{"step": 1, "phase": "crawl", "label": "x",
                   "urls": ["https://greenlab.example.com", "https://real.io"]}],
    )
    out = ContactDiscoveryOut.model_validate(row)
    check("official_site_url が null", out.official_site_url is None)
    check("doc_reader_official_site_url が null",
          out.doc_reader_official_site_url is None)
    check("search_agent_official_site_url が null",
          out.search_agent_official_site_url is None)
    check("v2_official_site_url が null", out.v2_official_site_url is None)
    check("primary_contact_form_url が null",
          out.primary_contact_form_url is None)
    check("searched_urls から example 除去",
          out.searched_urls == ["https://real.io/a"])
    check("web_searched_urls が空→null", out.web_searched_urls is None)
    check("discovered_forms が空→null", out.discovered_forms is None)
    check("SNS の example を除去",
          out.discovered_socials == {"instagram": "https://instagram.com/greenlab"})
    check("ai_search_queries から site:example 除去",
          out.ai_search_queries == ["GreenLab contact"])
    check("search_queries が空→null", out.search_queries is None)
    check("ai_sources から example 除去",
          out.ai_sources is not None
          and all("example" not in s.url for s in out.ai_sources))
    check("v2_steps の URL から example 除去",
          out.v2_steps is not None
          and out.v2_steps[0].urls == ["https://real.io"])
    # レスポンス全体を JSON 化して example が 1 文字も出ないことを確認
    dumped = out.model_dump_json()
    check("レスポンス JSON に example.com が含まれない",
          "example.com" not in dumped)
    check("レスポンス JSON に greenlab.example が含まれない",
          "greenlab.example" not in dumped)


def test_valid_urls_survive():
    print("test_valid_urls_survive")
    row = _row(
        official_site_url="https://greenlab.io",
        searched_urls=["https://greenlab.io/contact"],
        discovered_socials={"instagram": "https://instagram.com/greenlab"},
    )
    out = ContactDiscoveryOut.model_validate(row)
    check("実在の公式サイトは残る", out.official_site_url == "https://greenlab.io")
    check("実在の探索URLは残る",
          out.searched_urls == ["https://greenlab.io/contact"])
    check("実在の SNS は残る", bool(out.discovered_socials))


def main() -> int:
    test_official_site_or_none_blocks_dummy()
    test_fallback_no_site_query_for_dummy_domain()
    test_api_boundary_strips_example_everywhere()
    test_valid_urls_survive()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
