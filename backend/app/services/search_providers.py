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
import unicodedata
from urllib.parse import quote

from app.config import settings

logger = logging.getLogger("search_providers")

# スマートクォート/特殊ダッシュ等 → ASCII 等価へ変換（検索クエリの正規化）。
# 例: AfriK’Ecotour の ’（U+2019）→ '。NFKC では分解されないため明示的に置換する。
_SMART_CHARS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'",
    "′": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "–": "-", "—": "-", "−": "-", "…": "...",
    " ": " ", "　": " ",
}


def sanitize_query(query: str) -> str:
    """検索クエリを正規化する（NFKC ＋ スマートクォート→ASCII ＋ 余白整理）。

    検索 API へ送る前に必ず通す。ASCII へ落とすのではなく Unicode は維持し、
    送信側で UTF-8 として percent-encode する（要件）。
    """
    q = unicodedata.normalize("NFKC", query or "")
    for k, v in _SMART_CHARS.items():
        q = q.replace(k, v)
    return " ".join(q.split()).strip()


def _utf8_query_url(endpoint: str, params: dict) -> str:
    """params を UTF-8 で percent-encode して URL を組み立てる（ascii codec 回避）。

    httpx の params に依存せず、quote(..., safe="") で UTF-8 バイト列を
    percent-encode するため、非 ASCII でも 'ascii' codec エラーが起きない。
    """
    parts = [f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in params.items()]
    return endpoint + "?" + "&".join(parts)

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


def brave_reason(status: int | None, payload: dict | None, exc: Exception | None) -> str:
    """Brave が 0 件/失敗になった理由を機械可読な短文で返す（ログ・UI 用）。"""
    if exc is not None:
        if status == 401:
            return "401 unauthorized（APIキーが無効）"
        if status == 403:
            return "403 forbidden（プラン制限/キー権限）"
        if status == 429:
            return "429 rate limited / quota（レート制限・月次上限）"
        if status == 402:
            return "402 payment required（無料枠超過）"
        if status:
            return f"http {status}: {exc}"
        return f"exception: {exc}"
    if status and status >= 400:
        return f"http {status}"
    if isinstance(payload, dict):
        # Brave はエラーを {"type":"ErrorResponse",...} で返すことがある
        t = str(payload.get("type", ""))
        if "error" in t.lower():
            msg = ((payload.get("error") or {}).get("detail")
                   if isinstance(payload.get("error"), dict) else payload.get("message"))
            return f"error response: {msg or t}"
    return "web.results empty（該当なし）"


def brave_search_once(client, query: str, count: int) -> dict:
    """Brave を 1 回叩き、診断つきで返す（debug CLI と本番で共有する低レベル関数）。

    Returns: {url, status, body_head, results:[{url,title,snippet}], reason, error}
    """
    q = sanitize_query(query)
    diag = {"provider": "brave", "query": q, "url": None, "status": None,
            "body_head": "", "results": [], "reason": None, "error": None}
    # API キーを検証：前後空白/改行を除去し、ASCII でなければ送らない（httpx が
    # ヘッダーを ASCII エンコードするため、非 ASCII キーは 'ascii' codec 例外で全滅する）。
    key = (settings.brave_search_api_key or "").strip()
    if not key:
        diag["reason"] = "api key missing（BRAVE_SEARCH_API_KEY 未設定）"
        return diag
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        diag["reason"] = (
            "api key invalid：BRAVE_SEARCH_API_KEY に非ASCII文字が含まれています"
            "（.env のキーを確認してください。全角/日本語/引用符の混入など）"
        )
        logger.warning("Brave key contains non-ASCII characters; skipping Brave")
        return diag
    url = _utf8_query_url(BRAVE_ENDPOINT, {"q": q, "count": count})
    diag["url"] = url
    try:
        resp = client.get(url, headers={"X-Subscription-Token": key})
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        diag["status"] = status
        diag["error"] = str(exc)
        diag["body_head"] = _error_body(exc)
        diag["reason"] = brave_reason(status, None, exc)
        logger.warning("Brave error '%s': status=%s reason=%s", q, status, diag["reason"])
        return diag
    diag["status"] = resp.status_code
    try:
        diag["body_head"] = resp.text[:1000]
    except Exception:  # noqa: BLE001
        diag["body_head"] = ""
    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        diag["reason"] = f"parse error (non-JSON): {exc}"
        logger.warning("Brave non-JSON '%s': status=%s", q, resp.status_code)
        return diag
    results = parse_brave_results(payload)[:count]
    diag["results"] = results
    if not results:
        diag["reason"] = brave_reason(resp.status_code, payload, None)
        logger.warning("Brave 0 results '%s': status=%s reason=%s",
                       q, resp.status_code, diag["reason"])
    else:
        logger.info("Brave search '%s': status=%s, %d result(s)",
                    q, resp.status_code, len(results))
    return diag


def _brave_search_fn(count: int):
    # Brave は専用ヘッダー（X-Subscription-Token）が必要なため get() を直接使う。
    client = _new_client()

    def search(query: str) -> list[dict]:
        diag = brave_search_once(client, query, count)
        search.last_diag = diag  # type: ignore[attr-defined]
        return diag["results"]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "brave"  # type: ignore[attr-defined]
    search.last_diag = None  # type: ignore[attr-defined]
    return search


def _serpapi_search_fn(count: int):
    client = _new_client()

    def search(query: str) -> list[dict]:
        q = sanitize_query(query)
        url = _utf8_query_url(SERPAPI_ENDPOINT, {
            "engine": "google", "q": q, "num": count,
            "api_key": settings.serpapi_api_key,
        })
        try:
            payload = client.get(url).json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("SerpAPI search error '%s': %s | body=%s",
                           q, exc, _error_body(exc))
            return []
        results = parse_serpapi_results(payload)
        logger.info("SerpAPI search '%s': %d result(s)", q, len(results))
        return results[:count]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "serpapi"  # type: ignore[attr-defined]
    return search


def _tavily_search_fn(count: int):
    client = _new_client()

    def search(query: str) -> list[dict]:
        q = sanitize_query(query)
        try:
            payload = client.post_json(
                TAVILY_ENDPOINT,
                json={
                    "api_key": settings.tavily_api_key,
                    "query": q,
                    "max_results": count,
                    "search_depth": "basic",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily search error '%s': %s | body=%s",
                           q, exc, _error_body(exc))
            return []
        results = parse_tavily_results(payload)
        logger.info("Tavily search '%s': %d result(s)", q, len(results))
        return results[:count]

    search._client = client  # type: ignore[attr-defined]
    search.provider = "tavily"  # type: ignore[attr-defined]
    return search


def _google_cse_search_fn(count: int):
    client = _new_client()

    def search(query: str) -> list[dict]:
        q = sanitize_query(query)
        url = _utf8_query_url(GOOGLE_CSE_ENDPOINT, {
            "key": settings.google_cse_api_key,
            "cx": settings.google_cse_cx,
            "q": q,
            "num": min(count, 10),  # CSE は最大 10
        })
        try:
            payload = client.get(url).json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Google CSE search error '%s': %s | body=%s",
                           q, exc, _error_body(exc))
            return []
        results = parse_google_cse_results(payload)
        logger.info("Google CSE search '%s': %d result(s)", q, len(results))
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


class SearchRunner:
    """検索実行の統合ラッパー：プライマリ（Brave 等）が 0 件/失敗なら必ず DuckDuckGo
    HTML にフォールバックし、各クエリの診断（status/reason/results/fallback/URL）を
    収集する。Web Research / Search Agent / debug CLI が共通で使う。
    """

    def __init__(self, count: int) -> None:
        self.count = count
        self.provider = resolve_provider()
        self._primary = None
        self._ddg = None
        self.diagnostics: list[dict] = []
        self._client = None  # close 互換用（実体は close() で個別に閉じる）
        factory = _FACTORIES.get(self.provider)
        if factory is not None:
            try:
                self._primary = factory(count)
                logger.info("Using search provider: %s", self.provider)
                logger.info("%s enabled", _PROVIDER_LABELS.get(self.provider, self.provider))
            except Exception as exc:  # noqa: BLE001
                logger.warning("search provider %s init failed: %s", self.provider, exc)
                self._primary = None
        else:
            logger.info("Using search provider: duckduckgo (no/effective API)")

    def _ddg_fn(self):
        if self._ddg is None:
            from app.services.web_research_service import _default_search_fn

            self._ddg = _default_search_fn()
        return self._ddg

    def __call__(self, query: str) -> list[dict]:
        d: dict = {"query": query, "provider": self.provider, "status": None,
                   "reason": None, "results": 0, "fallback": None, "urls": []}
        results: list[dict] = []
        if self._primary is not None:
            try:
                results = self._primary(query) or []
            except Exception as exc:  # noqa: BLE001
                d["reason"] = f"exception: {exc}"
            pd = getattr(self._primary, "last_diag", None)
            if pd:
                d["status"] = pd.get("status")
                d["reason"] = pd.get("reason") or d["reason"]
            d["results"] = len(results)
        else:
            d["provider"] = "duckduckgo"

        # プライマリが 0 件/失敗 → DuckDuckGo HTML へフォールバック（要件6）
        if not results:
            try:
                fb = self._ddg_fn()(query) or []
            except Exception as exc:  # noqa: BLE001
                fb = []
                d["reason"] = f"{d['reason'] or ''} | ddg exception: {exc}".strip(" |")
            if fb:
                results = fb
                d["results"] = len(fb)
                if self._primary is not None:
                    d["fallback"] = "duckduckgo"
                    d["reason"] = (d["reason"] or "primary 0件") + " → DuckDuckGoで取得"
            else:
                if self._primary is None:
                    d["reason"] = d["reason"] or "DuckDuckGo 0件（ブロック/レート制限の可能性）"
                else:
                    d["reason"] = (d["reason"] or "primary 0件") + " / DuckDuckGoも0件"

        d["urls"] = [
            (r.get("url") if isinstance(r, dict) else str(r)) for r in results[:5]
        ]
        self.diagnostics.append(d)
        return results

    def close(self) -> None:
        for fn in (self._primary, self._ddg):
            client = getattr(fn, "_client", None)
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass


def get_search_fn(count: int | None = None) -> SearchRunner:
    """設定に基づく検索ランナーを返す（query -> [{url,title,snippet}]）。

    `.provider`（プライマリ名）/ `.diagnostics`（各クエリの診断）/ `.close()` を持つ。
    プライマリが 0 件/失敗なら必ず DuckDuckGo にフォールバックする。
    """
    return SearchRunner(count or settings.search_max_results)
