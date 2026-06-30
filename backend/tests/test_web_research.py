"""AI Web Research Mode のオフライン検証（ネットワーク/DB 不要）。

検索関数（search_fn）とページ取得関数（fetch_fn）を注入し、検索クエリ生成・候補
URL 取得・公式サイト/Contact 等の探索・メール抽出・既存フィルタ（platform / sentry /
no-reply 等の除外）・フォーム/SNS/PDF の保存を検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_web_research.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import web_research_service as w  # noqa: E402

_passed = 0
_failed = 0

SENTRY_DSN = "2c2bbb0dc8f6deb4cbe5c9175f5c7d02@o35514.ingest.sentry.io"


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


class FakeProject:
    id = 1
    title = "Cool Lamp"
    maker_name = "BrandCo"
    maker_url = "https://brandco.com"
    source_url = "https://www.kickstarter.com/projects/brandco/cool-lamp"
    source_site = "kickstarter"


# クロール対象ページの HTML（URL -> html）
_CONTACT_HTML = f"""
<html><body>
  <p>Sales: <a href="mailto:sales@brandco.com">sales@brandco.com</a></p>
  <p>partnership@brandco.com</p>
  <a href="mailto:support@kickstarter.com">platform help</a>
  <a href="mailto:no-reply@brandco.com">noreply</a>
  <span>{SENTRY_DSN}</span>
  <a href="https://www.instagram.com/brandco">IG</a>
  <a href="https://brandco.com/media-kit.pdf">Media kit</a>
</body></html>
"""

_ROOT_HTML = """
<html><body>
  <a href="/contact">Contact us</a>
  <p>Hello! info@brandco.com</p>
</body></html>
"""

_PAGES = {
    "https://brandco.com": _ROOT_HTML,
    "https://brandco.com/contact": _CONTACT_HTML,
}


def fake_fetch(url: str):
    return _PAGES.get(url)


def fake_search(query: str):
    # どのクエリでも同じ候補群を返す（social/pdf/platform の振り分けを検証）
    return [
        "https://brandco.com/contact",
        "https://www.instagram.com/brandco",
        "https://brandco.com/catalog.pdf",
        "https://www.kickstarter.com/help/contact",  # platform → クロールしない
    ]


def test_web_research_end_to_end() -> None:
    print("test_web_research_end_to_end")
    res = w.web_research(
        FakeProject(), None, fetch_fn=fake_fetch, search_fn=fake_search
    )

    emails = {e["email"].lower() for e in res["discovered_emails"]}
    check("sales@brandco.com を発見", "sales@brandco.com" in emails)
    check("partnership@brandco.com を発見", "partnership@brandco.com" in emails)
    check("info@brandco.com を発見", "info@brandco.com" in emails)
    check("platform メール support@kickstarter.com は除外", "support@kickstarter.com" not in emails)
    check("no-reply は除外", "no-reply@brandco.com" not in emails)
    check("sentry DSN は除外", SENTRY_DSN.lower() not in emails)
    check("各メールに出典 sources が付く", all(e["sources"] for e in res["discovered_emails"]))

    check("primary_email は営業向け", res["primary_email"] in ("partnership@brandco.com", "sales@brandco.com"))
    check("推奨チャネルは email", res["recommended_channel"] == "email")
    check("confidence_score > 0", res["confidence_score"] > 0)

    check("検索クエリを実行している", len(res["searched_queries"]) > 0)
    check("探索 URL を記録している", len(res["searched_urls"]) > 0)
    check("候補ページを記録している", len(res["candidate_pages"]) > 0)
    check(
        "Contact ページを candidate に分類",
        any(p["type"] == "contact" for p in res["candidate_pages"]),
    )

    socials = res["discovered_socials"]
    check("Instagram を SNS として保存", "instagram" in socials)

    forms = res["discovered_forms"]
    check("問い合わせフォームを保存", any("/contact" in f for f in forms))

    pdf_urls = {p["url"] for p in res["discovered_pdfs"]}
    check("検索結果の PDF を保存", "https://brandco.com/catalog.pdf" in pdf_urls)
    check("ページ内の PDF も保存", "https://brandco.com/media-kit.pdf" in pdf_urls)

    # platform のページ（kickstarter help）はクロール対象に入らない
    check(
        "platform ページはクロールしない",
        all("kickstarter.com/help" not in u for u in res["searched_urls"]),
    )


def test_graceful_when_search_empty() -> None:
    print("test_graceful_when_search_empty")
    # 検索が常に空（ブロック相当）でも、公式サイトクロールで結果を作る
    res = w.web_research(
        FakeProject(), None, fetch_fn=fake_fetch, search_fn=lambda q: []
    )
    emails = {e["email"].lower() for e in res["discovered_emails"]}
    check("検索が空でもメールを発見できる", "sales@brandco.com" in emails)
    check("注意メモに検索不可の旨", "search" in res["notes"].lower())


def test_query_generation() -> None:
    print("test_query_generation")
    qs = w.build_web_search_queries(FakeProject())
    check('"BrandCo" contact を含む', '"BrandCo" contact' in qs)
    check("site:brandco.com partnership を含む", "site:brandco.com partnership" in qs)
    check("filetype:pdf クエリを含む", any("filetype:pdf" in q for q in qs))


def test_ddg_parse_excludes_engine() -> None:
    print("test_ddg_parse_excludes_engine")
    html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fbrandco.com%2Fcontact&rut=x">Contact</a>'
        '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy">ad</a>'
    )
    out = w.parse_duckduckgo_results(html)
    check("外部 URL をデコード抽出", "https://brandco.com/contact" in out)
    check("検索エンジン自身の URL は除外", all("duckduckgo.com" not in u for u in out))


def main() -> int:
    test_query_generation()
    test_ddg_parse_excludes_engine()
    test_web_research_end_to_end()
    test_graceful_when_search_empty()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
