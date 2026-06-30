"""Web Research 用の検索プロバイダー抽象化。

DuckDuckGo HTML スクレイピングだけでは検索精度が不足するため、検索 API を
切り替えて使えるようにする。SEARCH_PROVIDER（.env）で選択し、対応する API キーが
設定されていれば API を、無ければ DuckDuckGo HTML（手動検索クエリ方式）に
フォールバックする。

対応プロバイダー：
- brave       : Brave Search API（GET, header: X-Subscription-Token）
- serpapi     : SerpAPI（GET, engine=google）
- tavily      : Tavily Search API（POST, JSON body）
- google_cse  : Google Custom Search JSON API（GET, key + cx）
- none / 未設定 / キー無し : DuckDuckGo HTML フォールバック

各プロバイダーのレスポンスは parse_*_results() で {url,title,snippet} の共通形に
正規化する（純粋関数なのでネットワーク無しで検証できる）。get_search_fn() は
query->[{url,title,snippet}] を返す呼び出し可能オブジェクトで、`.provider`（実際に
使ったプロバイダー名）と `._client`（クローズ用）を属性に持つ。
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("search_providers")

VALID_PROVIDERS = ("none", "brave", "serpapi", "tavily", "google_cse")

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

DEFAULT_TIMEOUT = 10.0
DEFAULT_RATE_LIMIT = 1.0


# ---------------- レスポンス正規化（純粋関数） ----------------
def _clean(s) -> str:
    return str(s or "").strip()


def parse_brave_results(payload: dict) -> list[dict]:
    """Brave Search API のレスポンスを {url,title,snippet} に正規化する。"""
    out: list[dict] = []
    web = (payload or {}).get("web") or {}
    for r in web.get("results") or []:
        url = _clean(r.get("url"))
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": _clean(r.get("title")),
                "snippet": _clean(r.get("description")),
            }
        )
    return out


def parse_serpapi_results(payload: dict) -> list[dict]:
    """SerpAPI（Google エンジン）のレスポンスを正規化する。"""
    out: list[dict] = []
    for r in (payload or {}).get("organic_results") or []:
        url = _clean(r.get("link"))
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": _clean(r.get("title")),
                "snippet": _clean(r.get("snippet")),
            }
        )
    return out


def parse_tavily_results(payload: dict) -> list[dict]:
    """Tavily Search API のレスポンスを正規化する。"""
    out: list[dict] = []
    for r in (payload or {}).get("results") or []:
        url = _clean(r.get("url"))
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": _clean(r.get("title")),
                "snippet": _clean(r.get("content")),
            }
        )
    return out


def parse_google_cse_results(payload: dict) -> list[dict]:
    """Google Custom Search JSON API のレスポンスを正規化する。"""
    out: list[dict] = []
    for r in (payload or {}).get("items") or []:
        url = _clean(r.get("link"))
        if not url:
            continue
        out.append(
            {
                "url": url,
                "title": _clean(r.get("title")),
                "snippet": _clean(r.get("snippet")),
            }
        )
    return out


# ---------------- プロバイダー解決 ----------------
def resolve_provider() -> str:
    """設定とキーの有無から、実際に使うプロバイダー名を決める。

    SEARCH_PROVIDER が API を指していてもキー（google_cse は cx も）が無ければ
    "duckduckgo" にフォールバックする。
    """
    provider = (settings.search_provider or "none").strip().lower()
    if provider == "brave" and settings.brave_search_api_key:
        return "brave"
    if provider == "serpapi" and settings.serpapi_api_key:
        return "serpapi"
    if provider == "tavily" and settings.tavily_api_key:
        return "tavily"
    if provider == "google_cse" and settings.google_cse_api_key and settings.google_cse_cx:
        return "google_cse"
    return "duckduckgo"


# ---------------- 検索関数ファクトリ ----------------
def _new_client():
    from app.scrapers.http import HttpClient

    return HttpClient(
        rate_limit_seconds=DEFAULT_RATE_LIMIT, timeout=DEFAULT_TIMEOUT, retries=1
    )


def _brave_search_fn(count: int):
    # Brave は専用ヘッダー（X-Subscription-Token）が必要なため get() を直接使う。
    client = _new_client()

    def search(query: str) -> list[dict]:
        try:
            resp = client.get(
                BRAVE_ENDPOINT,
                params={"q": query, "count": count},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": settings.brave_search_api_key,
                },
            )
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.info("brave search failed (%s): %s", query, exc)
            return []
        return parse_brave_results(payload)[:count]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "brave"  # type: ignore[attr-defined]
    return search


def _serpapi_search_fn(count: int):
    client = _new_client()

    def search(query: str) -> list[dict]:
        try:
            payload = client.get_json(
                SERPAPI_ENDPOINT,
                params={
                    "engine": "google",
                    "q": query,
                    "num": count,
                    "api_key": settings.serpapi_api_key,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("serpapi search failed (%s): %s", query, exc)
            return []
        return parse_serpapi_results(payload)[:count]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "serpapi"  # type: ignore[attr-defined]
    return search


def _tavily_search_fn(count: int):
    client = _new_client()

    def search(query: str) -> list[dict]:
        try:
            payload = client.post_json(
                TAVILY_ENDPOINT,
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "max_results": count,
                    "search_depth": "basic",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("tavily search failed (%s): %s", query, exc)
            return []
        return parse_tavily_results(payload)[:count]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "tavily"  # type: ignore[attr-defined]
    return search


def _google_cse_search_fn(count: int):
    client = _new_client()

    def search(query: str) -> list[dict]:
        try:
            payload = client.get_json(
                GOOGLE_CSE_ENDPOINT,
                params={
                    "key": settings.google_cse_api_key,
                    "cx": settings.google_cse_cx,
                    "q": query,
                    "num": min(count, 10),  # CSE は最大 10
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("google_cse search failed (%s): %s", query, exc)
            return []
        return parse_google_cse_results(payload)[:count]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "google_cse"  # type: ignore[attr-defined]
    return search


_FACTORIES = {
    "brave": _brave_search_fn,
    "serpapi": _serpapi_search_fn,
    "tavily": _tavily_search_fn,
    "google_cse": _google_cse_search_fn,
}


def get_search_fn(count: int | None = None):
    """設定に基づく検索関数を返す（query -> [{url,title,snippet}]）。

    返り値は `.provider`（実際のプロバイダー名）と `._client`（close 用）を持つ。
    API が選べない場合は DuckDuckGo HTML フォールバックを返す。
    """
    count = count or settings.search_max_results
    provider = resolve_provider()
    factory = _FACTORIES.get(provider)
    if factory is not None:
        try:
            return factory(count)
        except Exception as exc:  # noqa: BLE001  生成失敗時もフォールバック
            logger.warning("search provider %s init failed: %s", provider, exc)

    # フォールバック：既存の DuckDuckGo HTML 検索
    from app.services.web_research_service import _default_search_fn

    fn = _default_search_fn()
    fn.provider = "duckduckgo"  # type: ignore[attr-defined]
    return fn
