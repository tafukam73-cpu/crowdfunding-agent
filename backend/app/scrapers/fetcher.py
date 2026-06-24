"""取得方法（fetcher）の抽象化と切り替え。

- "httpx"      … 既存の軽量 HTTP クライアント（app/scrapers/http.py）
- "playwright" … ヘッドレスブラウザ経由（Cloudflare/JS チャレンジ対策）

両者は get_json / get_text / close の同一インターフェースを満たすため、
スクレイパー側は取得方法に依存しない。playwright は遅延 import するので、
httpx だけ使う環境に playwright を入れる必要はない。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.scrapers.http import HttpClient


@runtime_checkable
class Fetcher(Protocol):
    def get_json(self, url: str, *, params: dict | None = None) -> dict: ...
    def get_text(self, url: str) -> str: ...
    def close(self) -> None: ...


def get_fetcher(
    method: str = "httpx",
    *,
    rate_limit_seconds: float = 2.0,
    timeout: float = 30.0,
) -> Fetcher:
    """取得方法に応じた Fetcher を返す。"""
    if method == "playwright":
        # 遅延 import：playwright 未導入でも httpx 経路は動く
        from app.scrapers.playwright_client import PlaywrightClient

        return PlaywrightClient(
            rate_limit_seconds=rate_limit_seconds, timeout=timeout
        )
    return HttpClient(rate_limit_seconds=rate_limit_seconds, timeout=timeout)
