"""Discovery Crawler の実ネットワーク取得（Discovery Engine v1-6）。

``discovery_crawler_service.run`` に注入する ``fetch_fn``（URL -> 本文）を組み立てる。
v1-3 までは実ネットワークを行わなかったが、本モジュールにより **Kickstarter を対象に
実サイト取得を有効化**する（他プラットフォームは引き続き未接続で 0 件）。

取得経路：
- Kickstarter の discover/advanced は Cloudflare の JS チャレンジで保護されており、
  素の httpx では 403（"Just a moment"）になる。既存スクレイパーと同じく
  ``app.scrapers.fetcher.get_fetcher``（既定 Playwright ヘッドレス Chromium）で通す。
- 検索エンドポイントは JSON を返す。Playwright はブラウザの JSON ビューア経由で
  ``innerText`` から生 JSON を復元するため、``get_json`` で確実にパースできる。
  adapter は「本文（文字列）」を受け取る契約なので、取得した dict を JSON 文字列へ
  戻して返す（adapter 側が再パースし ``{"projects": [...]}`` を解釈する）。

安全設計：
- **robots.txt を尊重**する。対象 URL が Disallow なら取得しない（robots 自体は
  タイムアウト付きで取得し、読めなければ寛容側＝許可扱い）。
- **レート制限 / リトライ / タイムアウト / User-Agent** は既存 fetcher に委譲する。
- 取得失敗（チャレンジ・タイムアウト・非 2xx）は **例外を送出**し、呼び出し側の
  ``crawler.run`` が握って ``discovery_runs.error_message`` に記録する（画面を固めない）。
- Playwright はブラウザを抱えるため、``DiscoveryFetcher.close()`` で必ず解放する
  （呼び出し側が finally で閉じる）。
"""
from __future__ import annotations

import json
import logging
import urllib.robotparser
from urllib.parse import urlsplit

import httpx

from app.services.discovery_adapters.base import FetchFn

logger = logging.getLogger("discovery_crawler.fetch")

# 良識的なクローラであることを示す User-Agent（Playwright にも渡す）。
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; CrowdfundingAgentBot/1.0; "
    "+https://github.com/crowdfunding-agent)"
)
# 1 リクエストのタイムアウト（秒）。応答が無くても必ず打ち切る。
FETCH_TIMEOUT = 30.0
# robots.txt 取得のタイムアウト（秒）。read() のハングを避けるため自前取得する。
ROBOTS_TIMEOUT = 8.0
# リクエスト間隔（秒）。同一サイトへ過度に速くアクセスしない。
RATE_LIMIT_SECONDS = 2.0
# 実取得のリトライ回数（Playwright のチャレンジ再試行など）。全体時間を抑えるため控えめ。
FETCH_RETRIES = 1

# origin ("https://host") -> 解析済み robots（プロセス内キャッシュ）
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


class RobotsDisallowed(Exception):
    """robots.txt により対象 URL の取得が許可されていない。"""


def _load_robots(origin: str, user_agent: str) -> urllib.robotparser.RobotFileParser:
    """origin の robots.txt を **タイムアウト付き**で取得・解析して返す。

    取得・解析に失敗（非 2xx・接続失敗・robots 無し）した場合は空の robots
    （＝全許可）を返す。ネットワークは必ずタイムアウトで打ち切る。
    """
    rp = urllib.robotparser.RobotFileParser()
    robots_url = f"{origin}/robots.txt"
    try:
        resp = httpx.get(
            robots_url,
            timeout=ROBOTS_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        if resp.status_code >= 400:
            rp.parse([])  # robots が無い/読めない → 全許可扱い
        else:
            rp.parse(resp.text.splitlines())
    except Exception as exc:  # noqa: BLE001  robots 取得失敗は許可扱い（安全側で継続）
        logger.warning("robots.txt 取得失敗 (%s): %s — 許可扱いにする", robots_url, exc)
        rp.parse([])
    return rp


def _robots_allows(url: str, user_agent: str) -> bool:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return True
    origin = f"{parts.scheme}://{parts.netloc}"
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = _load_robots(origin, user_agent)
        _robots_cache[origin] = rp
    try:
        return rp.can_fetch(user_agent, url)
    except Exception:  # noqa: BLE001  robotparser 内部エラーは許可扱い
        return True


class DiscoveryFetcher:
    """実ネットワーク取得を行う callable。``fetch_fn`` として adapter に渡す。

    ``__call__(url)`` で JSON 検索エンドポイントを取得し、生 JSON 文字列を返す。
    使用後は ``close()`` で下位 fetcher（Playwright ブラウザ等）を解放すること。
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = FETCH_TIMEOUT,
        rate_limit_seconds: float = RATE_LIMIT_SECONDS,
        retries: int = FETCH_RETRIES,
        respect_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.respect_robots = respect_robots
        # 既存スクレイパーと同じ取得基盤を使う（既定 Playwright＝Cloudflare 通過）。
        from app.config import settings
        from app.scrapers.fetcher import get_fetcher

        method = getattr(settings, "scrape_fetcher", "httpx") or "httpx"
        self._method = method
        self._client = get_fetcher(
            method,
            rate_limit_seconds=rate_limit_seconds,
            timeout=timeout,
            retries=retries,
        )

    def __call__(self, url: str) -> str:
        if self.respect_robots and not _robots_allows(url, self.user_agent):
            raise RobotsDisallowed(f"robots.txt により取得不可: {url}")
        logger.info("discovery fetch: GET %s (method=%s)", url, self._method)
        # discover/advanced は JSON。get_json はブラウザ経路でも生 JSON を復元する。
        data = self._client.get_json(url)
        text = json.dumps(data, ensure_ascii=False)
        logger.info("discovery fetch done: %s (%d bytes json)", url, len(text))
        return text

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as exc:  # noqa: BLE001  解放失敗はログのみ
            logger.warning("discovery fetcher close error: %s", exc)


def build_http_fetcher(**kwargs) -> DiscoveryFetcher:
    """実ネットワーク取得を行う ``fetch_fn``（callable）を組み立てて返す。

    返り値は ``DiscoveryFetcher``（callable）。使用後に ``.close()`` を呼ぶこと。
    """
    return DiscoveryFetcher(**kwargs)


__all__ = ["build_http_fetcher", "DiscoveryFetcher", "RobotsDisallowed", "DEFAULT_USER_AGENT"]
