"""スクレイパー共通 HTTP クライアント。

- レート制限（リクエスト間隔の確保）
- リトライ（指数バックオフ）：ネットワークエラー・429・5xx・403
- 403 はボット対策の可能性があるため UA をローテーションして再試行する
- 404 などの恒久的エラーは即時送出（collector がエラー記録）
- 直近の試行回数・最終 HTTP ステータスを保持（詳細ログ用）
"""
from __future__ import annotations

import logging
import time

import httpx

from app.scrapers.useragents import ua_for_attempt

logger = logging.getLogger("scraper.http")

# リトライ対象（一時的エラー）。403 は UA ローテーションで再試行する。
RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


class HttpClient:
    def __init__(
        self,
        *,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        retries: int = 2,
        default_headers: dict | None = None,
        rotate_user_agent: bool = True,
    ) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self.retries = retries
        # rotate_user_agent=False の場合、試行ごとのブラウザ UA 偽装をやめる
        # （JSON 検索 API など、ブラウザ偽装ヘッダーが不要/有害な呼び出し向け）。
        self.rotate_user_agent = rotate_user_agent
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=default_headers if default_headers is not None else DEFAULT_HEADERS,
        )
        # 詳細ログ用
        self.last_attempts: int = 0
        self.last_status: int | None = None
        self.last_content_type: str | None = None

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            wait = self.rate_limit_seconds - elapsed
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def get(
        self, url: str, *, params: dict | None = None, headers: dict | None = None
    ) -> httpx.Response:
        last_exc: Exception | None = None
        self.last_attempts = 0
        self.last_status = None

        for attempt in range(self.retries + 1):
            self.last_attempts = attempt + 1
            self._respect_rate_limit()
            # 試行ごとに UA を変える（403/ボット対策）。API 呼び出しでは無効化できる。
            req_headers: dict = {}
            if self.rotate_user_agent:
                req_headers["User-Agent"] = ua_for_attempt(attempt)
            if headers:
                req_headers.update(headers)
            try:
                resp = self._client.get(url, params=params, headers=req_headers)
            except httpx.HTTPError as exc:  # 接続/タイムアウト等
                last_exc = exc
                logger.warning("HTTP error (attempt %d): %s", attempt + 1, exc)
                self._backoff(attempt)
                continue

            self.last_status = resp.status_code
            self.last_content_type = resp.headers.get("content-type")
            if resp.status_code in RETRYABLE_STATUS:
                logger.warning(
                    "retryable status %d (attempt %d) for %s",
                    resp.status_code, attempt + 1, url,
                )
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} for {url}", request=resp.request, response=resp
                )
                self._backoff(attempt)
                continue

            resp.raise_for_status()  # 404 等はここで送出（リトライしない）
            return resp

        assert last_exc is not None
        raise last_exc

    def get_json(self, url: str, *, params: dict | None = None) -> dict:
        resp = self.get(url, params=params, headers={"Accept": "application/json"})
        return resp.json()

    def post_json(
        self, url: str, *, json: dict, headers: dict | None = None
    ) -> dict:
        """JSON を POST して JSON を受け取る（検索 API の POST 方式用）。

        get() と同じくレート制限・リトライ（429/5xx/接続エラー）を適用する。
        """
        last_exc: Exception | None = None
        self.last_attempts = 0
        self.last_status = None
        req_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        for attempt in range(self.retries + 1):
            self.last_attempts = attempt + 1
            self._respect_rate_limit()
            try:
                resp = self._client.post(url, json=json, headers=req_headers)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("HTTP POST error (attempt %d): %s", attempt + 1, exc)
                self._backoff(attempt)
                continue

            self.last_status = resp.status_code
            if resp.status_code in RETRYABLE_STATUS:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} for {url}", request=resp.request, response=resp
                )
                self._backoff(attempt)
                continue

            resp.raise_for_status()
            return resp.json()

        assert last_exc is not None
        raise last_exc

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
