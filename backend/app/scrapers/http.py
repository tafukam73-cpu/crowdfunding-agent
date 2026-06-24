"""スクレイパー共通 HTTP クライアント。

- レート制限（リクエスト間隔の確保）
- リトライ（指数バックオフ）：ネットワークエラー・429・5xx のみ
- 403/404 などの恒久的エラーは即時送出（collector がエラー記録）
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("scraper.http")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# リトライ対象（一時的エラー）
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        *,
        rate_limit_seconds: float = 2.0,
        timeout: float = 20.0,
        retries: int = 2,
        user_agent: str = DEFAULT_UA,
    ) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self.retries = retries
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
        )

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.rate_limit_seconds - elapsed
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._respect_rate_limit()
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # 接続/タイムアウト等
                last_exc = exc
                logger.warning("HTTP error (attempt %d): %s", attempt + 1, exc)
                self._backoff(attempt)
                continue

            if resp.status_code in RETRYABLE_STATUS:
                logger.warning(
                    "retryable status %d (attempt %d) for %s",
                    resp.status_code,
                    attempt + 1,
                    url,
                )
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} for {url}", request=resp.request, response=resp
                )
                self._backoff(attempt)
                continue

            resp.raise_for_status()  # 403/404 等はここで送出（リトライしない）
            return resp

        assert last_exc is not None
        raise last_exc

    def get_json(self, url: str, *, params: dict | None = None) -> dict:
        resp = self.get(url, params=params, headers={"Accept": "application/json"})
        return resp.json()

    def get_text(self, url: str) -> str:
        return self.get(url).text

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2 ** attempt, 8))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
