"""検索プロバイダー抽象化のオフライン検証（ネットワーク/DB/httpx 不要）。

各プロバイダーのレスポンス正規化（parse_*）と、SEARCH_PROVIDER + キーの有無に
よるプロバイダー解決（resolve_provider / フォールバック）を検証する。

実行（backend ディレクトリで）:
    python tests/test_search_providers.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.services import search_providers as sp  # noqa: E402

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


def _reset() -> None:
    settings.search_provider = "none"
    settings.brave_search_api_key = ""
    settings.serpapi_api_key = ""
    settings.tavily_api_key = ""
    settings.google_cse_api_key = ""
    settings.google_cse_cx = ""


def test_parsers() -> None:
    print("test_parsers")
    b = sp.parse_brave_results(
        {"web": {"results": [{"url": "https://a.com", "title": "A", "description": "d"}]}}
    )
    check("brave 正規化", b == [{"url": "https://a.com", "title": "A", "snippet": "d"}])
    s = sp.parse_serpapi_results(
        {"organic_results": [{"link": "https://b.com", "title": "B", "snippet": "s"}]}
    )
    check("serpapi 正規化", s == [{"url": "https://b.com", "title": "B", "snippet": "s"}])
    t = sp.parse_tavily_results(
        {"results": [{"url": "https://c.com", "title": "C", "content": "x"}]}
    )
    check("tavily 正規化", t == [{"url": "https://c.com", "title": "C", "snippet": "x"}])
    g = sp.parse_google_cse_results(
        {"items": [{"link": "https://d.com", "title": "D", "snippet": "y"}]}
    )
    check("google_cse 正規化", g == [{"url": "https://d.com", "title": "D", "snippet": "y"}])
    check("空レスポンスは空配列", sp.parse_brave_results({}) == [])
    check("None 安全", sp.parse_serpapi_results(None) == [])


# PowerShell で取得した Brave Web Search API レスポンスを模した実構造。
# web.results に Web 結果、mixed.main は並び順メタデータ、videos は別バケット。
# 各結果には profile.url / meta_url / thumbnail などの入れ子 url も含む。
BRAVE_VITESY_RESPONSE = {
    "type": "search",
    "query": {"original": "Vitesy Facebook"},
    "mixed": {
        "type": "mixed",
        "main": [
            {"type": "web", "index": 0, "all": False},
            {"type": "web", "index": 1, "all": False},
            {"type": "videos", "all": True},
        ],
    },
    "web": {
        "type": "search",
        "results": [
            {
                "title": "Vitesy (@vitesy) • Instagram / Facebook",
                "url": "https://www.facebook.com/vitesy/",
                "description": "Official Vitesy Facebook page.",
                "profile": {
                    "name": "Facebook",
                    "url": "https://www.facebook.com/",
                    "img": "https://imgs.search.brave.com/abc.ico",
                },
                "meta_url": {"hostname": "www.facebook.com"},
                "thumbnail": {"src": "https://imgs.search.brave.com/thumb.jpg"},
            },
            {
                "title": "Vitesy – Natural Technology",
                "url": "https://www.vitesy.com/",
                "description": "Vitesy official site. Fruit Bowl and more.",
                "profile": {"url": "https://www.vitesy.com/"},
            },
            {
                "title": "Vitesy | LinkedIn",
                "url": "https://www.linkedin.com/company/vitesy/",
                "description": "Vitesy company page on LinkedIn.",
            },
        ],
    },
    "videos": {
        "type": "videos",
        "results": [
            {"title": "Vitesy video", "url": "https://www.youtube.com/watch?v=xyz"}
        ],
    },
}


def test_brave_real_response() -> None:
    """要件7：実レスポンス構造から facebook.com/vitesy を最低1件取得できる。"""
    print("test_brave_real_response")
    results = sp.parse_brave_results(BRAVE_VITESY_RESPONSE)
    urls = [r["url"] for r in results]
    check("facebook.com/vitesy を取得", "https://www.facebook.com/vitesy/" in urls)
    check("公式サイト vitesy.com を取得", "https://www.vitesy.com/" in urls)
    check("linkedin.com/company/vitesy を取得",
          "https://www.linkedin.com/company/vitesy/" in urls)
    check("web.results を3件取得（profile等の入れ子urlを誤収集しない）", len(results) == 3)
    check("入れ子の facebook.com トップは含めない",
          "https://www.facebook.com/" not in urls)
    fb = next(r for r in results if "facebook.com/vitesy" in r["url"])
    check("title/snippet も取得", bool(fb["title"]) and bool(fb["snippet"]))


def test_brave_fallback_when_structure_unexpected() -> None:
    """web.results が無い想定外構造でも、再帰収集で結果を拾える（0件にしない）。"""
    print("test_brave_fallback_when_structure_unexpected")
    weird = {
        "results": {
            "main": [
                {"title": "Vitesy FB", "url": "https://www.facebook.com/vitesy/",
                 "description": "x"},
            ]
        }
    }
    results = sp.parse_brave_results(weird)
    urls = [r["url"] for r in results]
    check("想定外構造でも facebook.com/vitesy を取得",
          "https://www.facebook.com/vitesy/" in urls)


def test_resolution() -> None:
    print("test_resolution")
    _reset()
    check("none はフォールバック", sp.resolve_provider() == "duckduckgo")

    settings.search_provider = "brave"
    check("brave 指定でもキー無しはフォールバック", sp.resolve_provider() == "duckduckgo")
    settings.brave_search_api_key = "x"
    check("brave 指定＋キーで brave", sp.resolve_provider() == "brave")

    _reset()
    settings.search_provider = "serpapi"
    settings.serpapi_api_key = "x"
    check("serpapi 指定＋キーで serpapi", sp.resolve_provider() == "serpapi")

    _reset()
    settings.search_provider = "tavily"
    settings.tavily_api_key = "x"
    check("tavily 指定＋キーで tavily", sp.resolve_provider() == "tavily")

    _reset()
    settings.search_provider = "google_cse"
    settings.google_cse_api_key = "x"
    check("google_cse は cx 無しだとフォールバック", sp.resolve_provider() == "duckduckgo")
    settings.google_cse_cx = "cx"
    check("google_cse は key+cx で採用", sp.resolve_provider() == "google_cse")

    _reset()


def test_sanitize_and_utf8_url() -> None:
    """要件：NFKC正規化＋スマートクォート変換、UTF-8 URLが ascii-safe（例外なし）。"""
    print("test_sanitize_and_utf8_url")
    # スマートクォート → ASCII
    check("’ を ' に変換", sp.sanitize_query("AfriK’Ecotour") == "AfriK'Ecotour")
    check("“ ” を \" に変換", sp.sanitize_query("“Smart”") == '"Smart"')
    check("NFKC で全角→半角", sp.sanitize_query("Ｖｉｔｅｓｙ") == "Vitesy")
    check("連続空白を1つに", sp.sanitize_query("a   b\tc") == "a b c")

    # 非 ASCII を含むタイトルでも URL は ascii-safe（'ascii' codec エラーが起きない）
    for title in [
        "AfriK’Ecotour",
        "Vitesy Fruit Bowl: Reinventing Fruit Freshness",
        "“Smart” Café – Pro",
        "日本語タイトル",
    ]:
        q = sp.sanitize_query(title)
        url = sp._utf8_query_url(sp.BRAVE_ENDPOINT, {"q": q, "count": 10})
        try:
            url.encode("ascii")
            ok = True
        except UnicodeEncodeError:
            ok = False
        check(f"URLがascii-safe: {title[:14]}", ok)

    # AfriK’Ecotour の ’ は percent-encode で %27（ASCII apostrophe）になる
    url = sp._utf8_query_url(sp.BRAVE_ENDPOINT, {"q": sp.sanitize_query("AfriK’Ecotour")})
    check("’→' が %27 で送られる", "AfriK%27Ecotour" in url)


def test_fallback_queries() -> None:
    """要件：短縮フォールバッククエリ（Vitesy → Vitesy Facebook → ...）。"""
    print("test_fallback_queries")
    from app.services.web_research_service import build_fallback_queries

    class P:
        title = "Vitesy Fruit Bowl: Reinventing Fruit Freshness"
        maker_name = "Vitesy"
        maker_url = None
        source_site = "indiegogo"
        description = ""
        description_clean = ""

    fb = build_fallback_queries(P())
    check("先頭は短いベース語 Vitesy", fb[0] == "Vitesy")
    check("Vitesy Facebook を含む", "Vitesy Facebook" in fb)
    check("Vitesy Instagram を含む", "Vitesy Instagram" in fb)
    check("Vitesy LinkedIn を含む", "Vitesy LinkedIn" in fb)

    class P2(P):
        maker_name = None  # メーカー名が無くてもタイトル先頭語から作る

    fb2 = build_fallback_queries(P2())
    check("メーカー名無しでも Vitesy ベース", fb2 and fb2[0] == "Vitesy")


def main() -> int:
    test_parsers()
    test_sanitize_and_utf8_url()
    test_fallback_queries()
    test_brave_real_response()
    test_brave_fallback_when_structure_unexpected()
    test_resolution()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
