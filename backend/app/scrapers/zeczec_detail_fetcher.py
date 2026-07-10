"""Zeczec 詳細ページ取得（Playwright・Cloudflare 対策）。

詳細ページは Cloudflare の JS チャレンジで httpx では 403 になる。実測の結果、
**1 ページごとに新規ブラウザコンテキストを作り、ページ間に間隔（pacing）を空ける**と
チャレンジのエスカレーションを回避でき安定して 200 を得られる（同一コンテキストで
連続アクセスすると 2 ページ目以降がハードチャレンジ化し、最悪 10 分ハングする）。

安全設計:
- 1 ページ 1 試行（リトライしない）。取れなければ理由を返す（403 を突破し続けない）。
- goto タイムアウト・チャレンジ待機の上限で 1 ページの実時間を制限する。
- ブラウザは 1 つを使い回し、コンテキストのみ都度作り直す（リーク防止に必ず close）。
- Playwright sync API はスレッド安全でないため、生成〜利用〜破棄を同一スレッドで行う
  （デーモンスレッドのジョブ内で完結させる分には問題ない）。

playwright 未導入環境でも import は通る（遅延 import）。取得系のみ playwright を要求する。
"""
from __future__ import annotations

import logging
import time

from app.scrapers.zeczec_detail import BASE, looks_like_challenge

logger = logging.getLogger("scraper.zeczec_detail")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def project_url(slug_or_url: str) -> str:
    """slug でも URL でも詳細ページ URL に正規化する。"""
    s = (slug_or_url or "").strip()
    if s.startswith("http"):
        return s.split("?")[0].split("#")[0]
    return f"{BASE}/projects/{s}"


class ZeczecDetailFetcher:
    """実ブラウザで Zeczec 詳細ページを取得する（fresh context + pacing）。"""

    def __init__(
        self,
        *,
        pacing_seconds: float = 5.0,
        goto_timeout: float = 25.0,
        challenge_wait: float = 15.0,
        headless: bool = True,
    ) -> None:
        self.pacing_seconds = pacing_seconds
        self.goto_timeout_ms = int(goto_timeout * 1000)
        self.challenge_wait_ms = int(challenge_wait * 1000)
        self.headless = headless
        self._pw = None
        self._browser = None
        self._last_fetch_at: float | None = None
        self.last_status: int | None = None

    def _ensure_browser(self) -> None:
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)

    def _pace(self) -> None:
        if self._last_fetch_at is not None:
            wait = self.pacing_seconds - (time.monotonic() - self._last_fetch_at)
            if wait > 0:
                time.sleep(wait)
        self._last_fetch_at = time.monotonic()

    def fetch(self, slug_or_url: str) -> tuple[int | None, str, str]:
        """1 ページ取得して (status, html, inner_text) を返す。

        チャレンジを抜けられなければ html/inner にチャレンジページをそのまま返す
        （呼び出し側は parse_detail の challenged=True で判別できる）。例外は投げない。
        """
        self._ensure_browser()
        self._pace()
        url = project_url(slug_or_url)
        # 1 ページ 1 コンテキスト（CF エスカレーション回避の要）。
        ctx = self._browser.new_context(
            user_agent=_UA,
            locale="zh-TW",
            extra_http_headers={"Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        )
        page = ctx.new_page()
        status: int | None = None
        html = inner = ""
        try:
            resp = page.goto(
                url, wait_until="domcontentloaded", timeout=self.goto_timeout_ms
            )
            if resp is not None:
                status = resp.status
            html = page.content()
            inner = page.evaluate("() => document.body ? document.body.innerText : ''")
            waited = 0
            while looks_like_challenge(inner) or looks_like_challenge(html):
                if waited >= self.challenge_wait_ms:
                    break
                page.wait_for_timeout(2500)
                waited += 2500
                html = page.content()
                inner = page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
        except Exception as exc:  # noqa: BLE001  1 ページ失敗はアプリを止めない
            logger.warning("zeczec detail fetch error (%s): %s", url, exc)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self.last_status = status
        return status, html, inner

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("zeczec detail fetcher close error: %s", exc)
        finally:
            self._browser = self._pw = None

    def __enter__(self) -> "ZeczecDetailFetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
