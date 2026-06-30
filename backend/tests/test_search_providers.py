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


def main() -> int:
    test_parsers()
    test_resolution()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
