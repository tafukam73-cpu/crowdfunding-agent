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

import json
import logging

from app.config import settings

logger = logging.getLogger("search_providers")

# 検索 API 呼び出しにはブラウザ偽装ヘッダー（Sec-Fetch-*, Accept: text/html 等）を
# 付けない。これらが付くと一部 API（Brave 等）がリクエストを弾き、結果が 0 件に
# なることがある。JSON を要求するクリーンなヘッダーのみを送る。
_API_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "User-Agent": "crowdfunding-agent/1.0",
}

VALID_PROVIDERS = ("none", "brave", "serpapi", "tavily", "google_cse")

# ログ表示用のプロバイダー名（例: "Brave Search API enabled"）
_PROVIDER_LABELS = {
    "brave": "Brave Search API",
    "serpapi": "SerpAPI",
    "tavily": "Tavily Search API",
    "google_cse": "Google Custom Search API",
}

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

DEFAULT_TIMEOUT = 10.0
DEFAULT_RATE_LIMIT = 1.0


# ---------------- レスポンス正規化（純粋関数） ----------------
def _clean(s) -> str:
    return str(s or "").strip()


def _result_record(r) -> dict | None:
    """検索結果 1 件（url を持つ dict）を {url,title,snippet} に正規化する。"""
    if not isinstance(r, dict):
        return None
    url = _clean(r.get("url"))
    if not url.startswith(("http://", "https://")):
        return None
    return {
        "url": url,
        "title": _clean(r.get("title")),
        "snippet": _clean(r.get("description") or r.get("snippet") or r.get("content")),
    }


def _collect_url_records(node, out: list[dict], seen: set[str], depth: int = 0) -> None:
    """JSON を再帰的に走査し、url を持つ結果オブジェクトを収集する（構造変化への保険）。

    url を持つ dict は「結果」とみなしてそれ以上潜らない（profile/meta_url など
    入れ子の url を二重取得しない）。
    """
    if depth > 6:
        return
    if isinstance(node, dict):
        rec = _result_record(node)
        if rec is not None:
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
            return
        for v in node.values():
            _collect_url_records(v, out, seen, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _collect_url_records(v, out, seen, depth + 1)


def parse_brave_results(payload: dict) -> list[dict]:
    """Brave Search API のレスポンスを {url,title,snippet} に正規化する。

    Brave のレスポンスは web / news / videos / mixed などのバケットを持つ。通常は
    web.results に Web 検索結果が入る（mixed.main は並び順メタデータで、実体は各
    バケット側）。web.results を最優先し、補助バケットも拾う。構造が想定外でも
    0 件にならないよう、最後に payload 全体から url を持つ結果を再帰収集する。
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(r) -> None:
        rec = _result_record(r)
        if rec and rec["url"] not in seen:
            seen.add(rec["url"])
            out.append(rec)

    web = (payload or {}).get("web") or {}
    for r in web.get("results") or []:
        add(r)
    # 補助バケット（news / faq / discussions）も Web ページ URL を持つ
    for key in ("news", "faq", "discussions"):
        bucket = (payload or {}).get(key) or {}
        for r in bucket.get("results") or []:
            add(r)
    # web.results が空でも、構造変化に備えて payload 全体から再帰収集（保険）
    if not out:
        _collect_url_records(payload, out, seen)
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
        rate_limit_seconds=DEFAULT_RATE_LIMIT,
        timeout=DEFAULT_TIMEOUT,
        retries=1,
        default_headers=dict(_API_HEADERS),
        rotate_user_agent=False,
    )


def _error_body(exc: Exception) -> str:
    """例外に HTTP レスポンスが付いていれば本文（先頭）を取り出す（ログ用）。"""
    resp = getattr(exc, "response", None)
    if resp is None:
        return ""
    try:
        return resp.text[:1000]
    except Exception:  # noqa: BLE001
        return ""


def _brave_search_fn(count: int):
    # Brave は専用ヘッダー（X-Subscription-Token）が必要なため get() を直接使う。
    client = _new_client()

    def search(query: str) -> list[dict]:
        try:
            resp = client.get(
                BRAVE_ENDPOINT,
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": settings.brave_search_api_key},
            )
        except Exception as exc:  # noqa: BLE001  ステータス・本文を必ずログに残す
            logger.warning(
                "Brave search error '%s': %s | body=%s",
                query, exc, _error_body(exc),
            )
            return []
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001  非 JSON はそのまま記録
            logger.warning(
                "Brave non-JSON response '%s': status=%s body=%s",
                query, resp.status_code, resp.text[:1000],
            )
            return []
        results = parse_brave_results(payload)
        logger.info(
            "Brave search '%s': status=%s, %d result(s)",
            query, resp.status_code, len(results),
        )
        # 0 件ならレスポンス JSON を丸ごと（先頭）ログに出して原因を可視化する
        if not results:
            try:
                raw = json.dumps(payload, ensure_ascii=False)[:2000]
            except Exception:  # noqa: BLE001
                raw = str(payload)[:2000]
            logger.warning("Brave 0 results '%s' raw=%s", query, raw)
        return results[:count]

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
            logger.warning("SerpAPI search error '%s': %s | body=%s",
                           query, exc, _error_body(exc))
            return []
        results = parse_serpapi_results(payload)
        logger.info("SerpAPI search '%s': %d result(s)", query, len(results))
        return results[:count]

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
            logger.warning("Tavily search error '%s': %s | body=%s",
                           query, exc, _error_body(exc))
            return []
        results = parse_tavily_results(payload)
        logger.info("Tavily search '%s': %d result(s)", query, len(results))
        return results[:count]

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
            logger.warning("Google CSE search error '%s': %s | body=%s",
                           query, exc, _error_body(exc))
            return []
        results = parse_google_cse_results(payload)
        logger.info("Google CSE search '%s': %d result(s)", query, len(results))
        return results[:count]

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
            fn = factory(count)
            logger.info("Using search provider: %s", provider)
            logger.info("%s enabled", _PROVIDER_LABELS.get(provider, provider))
            return fn
        except Exception as exc:  # noqa: BLE001  生成失敗時もフォールバック
            logger.warning("search provider %s init failed: %s", provider, exc)

    # フォールバック：既存の DuckDuckGo HTML 検索
    configured = (settings.search_provider or "none").strip().lower()
    if configured in _FACTORIES:
        logger.info(
            "Using search provider: duckduckgo "
            "(SEARCH_PROVIDER=%s but API key/cx missing; falling back)",
            configured,
        )
    else:
        logger.info("Using search provider: duckduckgo (no search API configured)")
    from app.services.web_research_service import _default_search_fn

    fn = _default_search_fn()
    fn.provider = "duckduckgo"  # type: ignore[attr-defined]
    return fn
