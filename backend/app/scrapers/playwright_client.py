"""Playwright（ヘッドレス Chromium）による取得クライアント。

Cloudflare/JS チャレンジを通すため実ブラウザでページを開く。
HttpClient と同じ get_json / get_text / close を提供する。

安定化：
- リトライ（UA ローテーション）：goto 失敗・チャレンジ検出時に再試行
- 直近の試行回数を保持（詳細ログ用）

依存：`playwright`（pip）＋ ブラウザ本体（`playwright install chromium`）。
"""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlencode

from app.scrapers.useragents import ua_for_attempt

logger = logging.getLogger("scraper.playwright")

# ボット対策/チャレンジページの典型マーカー（検出したらリトライ）
CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "cf-challenge",
    "/cdn-cgi/challenge-platform",
    "verifying you are human",
)

EXTRA_HEADERS = {"Accept-Language": "en-US,en;q=0.9,ja;q=0.8"}


class PlaywrightClient:
    def __init__(
        self,
        *,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        retries: int = 2,
        headless: bool = True,
        wait_ms: int = 1500,
    ) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout_ms = int(timeout * 1000)
        self.retries = retries
        self.headless = headless
        self.wait_ms = wait_ms  # goto 後の待機（JSチャレンジ通過の猶予）
        self._pw = None
        self._browser = None
        self._context = None
        self._context_ua: str | None = None
        self._last_request_at: float | None = None
        # 詳細ログ用
        self.last_attempts: int = 0
        self.last_status: int | None = None
        self.last_content_type: str | None = None

    # --- ブラウザ起動（遅延）。UA を指定してコンテキストを作る ---
    def _ensure(self, user_agent: str) -> None:
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)

        # UA を変える場合はコンテキストを作り直す
        if self._context is None or self._context_ua != user_agent:
            if self._context is not None:
                self._context.close()
            self._context = self._browser.new_context(
                user_agent=user_agent,
                locale="en-US",
                extra_http_headers=EXTRA_HEADERS,
            )
            self._context_ua = user_agent

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            wait = self.rate_limit_seconds - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _looks_like_challenge(inner: str, html: str) -> bool:
        sample = (inner[:500] + " " + html[:1000]).lower()
        return any(m in sample for m in CHALLENGE_MARKERS)

    def _open(self, url: str) -> tuple[str, str]:
        """URL を開き (body innerText, full HTML) を返す。失敗時はリトライ。"""
        last_exc: Exception | None = None
        self.last_attempts = 0

        for attempt in range(self.retries + 1):
            self.last_attempts = attempt + 1
            self._ensure(ua_for_attempt(attempt))
            self._respect_rate_limit()
            page = self._context.new_page()  # type: ignore[union-attr]
            try:
                resp = page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout_ms
                )
                if resp is not None:
                    try:
                        self.last_status = resp.status
                        self.last_content_type = resp.headers.get("content-type")
                    except Exception:  # noqa: BLE001  メタ取得失敗は無視
                        pass
                if self.wait_ms:
                    page.wait_for_timeout(self.wait_ms)
                inner = page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
                html = page.content()
                if self._looks_like_challenge(inner or "", html or ""):
                    last_exc = RuntimeError("チャレンジページを検出")
                    logger.warning(
                        "challenge detected (attempt %d) for %s", attempt + 1, url
                    )
                    self._reset_context()
                    self._backoff(attempt)
                    continue
                return inner or "", html
            except Exception as exc:  # noqa: BLE001  goto/評価の失敗
                last_exc = exc
                logger.warning("playwright open error (attempt %d): %s", attempt + 1, exc)
                self._reset_context()
                self._backoff(attempt)
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass

        assert last_exc is not None
        raise last_exc

    def _reset_context(self) -> None:
        if self._context is not None:
            try:
                self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
            self._context_ua = None

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2 ** attempt, 8))

    def get_text(self, url: str) -> str:
        _, html = self._open(url)
        return html

    def get_content(self, url: str) -> tuple[str, str]:
        """(body innerText, full HTML) を返す。"""
        return self._open(url)

    def get_json(self, url: str, *, params: dict | None = None) -> dict:
        full = url + ("?" + urlencode(params) if params else "")
        inner, _ = self._open(full)
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, ValueError) as exc:
            snippet = (inner or "").strip()[:200]
            raise ValueError(
                f"JSON のパースに失敗（ブロック/チャレンジの可能性）: {snippet!r}"
            ) from exc

    def close(self) -> None:
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("playwright close error: %s", exc)
        finally:
            self._context = self._browser = self._pw = None
            self._context_ua = None
