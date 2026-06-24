"""Playwright（ヘッドレス Chromium）による取得クライアント。

Cloudflare/JS チャレンジを通すため実ブラウザでページを開く。
HttpClient と同じ get_json / get_text / close を提供する。

依存：`playwright`（pip）＋ ブラウザ本体（`playwright install chromium`）。
"""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlencode

logger = logging.getLogger("scraper.playwright")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightClient:
    def __init__(
        self,
        *,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_UA,
        headless: bool = True,
        wait_ms: int = 1500,
    ) -> None:
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout_ms = int(timeout * 1000)
        self.user_agent = user_agent
        self.headless = headless
        self.wait_ms = wait_ms  # goto 後の待機（JSチャレンジ通過の猶予）
        self._pw = None
        self._browser = None
        self._context = None
        self._last_request_at: float | None = None

    # --- ブラウザ起動（遅延） ---
    def _ensure(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            user_agent=self.user_agent, locale="en-US"
        )

    def _respect_rate_limit(self) -> None:
        if self._last_request_at is not None:
            wait = self.rate_limit_seconds - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _open(self, url: str) -> tuple[str, str]:
        """URL を開き (body innerText, full HTML) を返す。"""
        self._ensure()
        self._respect_rate_limit()
        page = self._context.new_page()  # type: ignore[union-attr]
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            if self.wait_ms:
                page.wait_for_timeout(self.wait_ms)
            inner = page.evaluate(
                "() => document.body ? document.body.innerText : ''"
            )
            html = page.content()
            return inner or "", html
        finally:
            page.close()

    def get_text(self, url: str) -> str:
        _, html = self._open(url)
        return html

    def get_json(self, url: str, *, params: dict | None = None) -> dict:
        full = url + ("?" + urlencode(params) if params else "")
        inner, _ = self._open(full)
        try:
            return json.loads(inner)
        except (json.JSONDecodeError, ValueError) as exc:
            # Cloudflare チャレンジページ等で JSON が返らなかった場合
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
