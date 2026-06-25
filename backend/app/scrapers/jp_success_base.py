"""日本クラファン成功案件スクレイパーの共通基盤（Playwright）。

discover ページからプロジェクト URL を集め、各詳細ページを開いて
JapaneseSuccessCreate へ正規化する。サイト固有の「URL 収集」「詳細パース」は
サブクラスで実装し、本文テキストからの数値抽出・JSON-LD/og:meta 解析などの
共通処理はここに集約する。

設計方針（要件対応）：
  - Playwright（ヘッドレス Chromium）で取得（JS/チャレンジ対策）
  - レート制限は PlaywrightClient が担保
  - 取得できない項目は None（null）
  - 1 件のパース失敗は握りつぶして継続（収集全体は止めない）
  - 応援購入総額が下限（MIN_SUCCESS_JPY）以上のものだけ成功案件として採用
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal, InvalidOperation

from app.config import settings
from app.models.japanese_success import MIN_SUCCESS_JPY
from app.schemas.japanese_success import JapaneseSuccessCreate
from app.scrapers.playwright_client import PlaywrightClient

logger = logging.getLogger("scraper.jp_success")


# --- 数値・日付パース（本文 innerText 用） ---
def to_decimal_jpy(num_str: str | None) -> Decimal | None:
    if not num_str:
        return None
    try:
        return Decimal(num_str.replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def find_amount(text: str, label: str) -> Decimal | None:
    """「<label> … 1,234,567円」形式から金額を取る（Makuake 等）。"""
    m = re.search(re.escape(label) + r"[\s\S]{0,12}?([\d,]+)\s*円", text)
    return to_decimal_jpy(m.group(1)) if m else None


def find_amount_yen(text: str, label: str) -> Decimal | None:
    """「<label> … ¥ 1,234,567」形式から金額を取る（GreenFunding 等）。"""
    m = re.search(re.escape(label) + r"[\s\S]{0,8}?¥\s*([\d,]+)", text)
    return to_decimal_jpy(m.group(1)) if m else None


def find_count(text: str, label: str, unit: str = "人") -> int | None:
    """「<label> … 1,234<unit>」形式から件数を取る。"""
    m = re.search(re.escape(label) + r"[\s\S]{0,12}?([\d,]+)\s*" + re.escape(unit), text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def find_date_after(text: str, anchor: str) -> date | None:
    """アンカー語の直後に出る「YYYY年MM月DD日」を取る。"""
    m = re.search(
        re.escape(anchor) + r"[\s\S]{0,12}?(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text
    )
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# --- HTML 構造化データ ---
def meta_content(html: str, prop: str) -> str | None:
    """og:* / name 系 meta の content を取る（属性順の両パターンに対応）。"""
    for pat in (
        r'<meta[^>]+property=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']*)["\']',
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']' + re.escape(prop) + r'["\']',
        r'<meta[^>]+name=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']*)["\']',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


def jsonld_blocks(html: str) -> list[dict]:
    """application/ld+json ブロックを dict で返す（壊れていれば無視）。"""
    out: list[dict] = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.S,
    ):
        raw = m.group(1).strip()
        data = None
        # そのまま → 文字列中の生改行/タブを潰して再試行（不正 JSON 対策）
        for attempt in (raw, re.sub(r"[\n\r\t]", " ", raw)):
            try:
                data = json.loads(attempt)
                break
            except (json.JSONDecodeError, ValueError):
                continue
        if data is None:
            continue
        if isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            out.append(data)
    return out


def find_video(html: str) -> str | None:
    """og:video または YouTube/Vimeo の埋め込み iframe から動画 URL を取る。"""
    v = meta_content(html, "og:video") or meta_content(html, "og:video:url")
    if v:
        return v
    m = re.search(
        r'src=["\']((?:https?:)?//(?:www\.)?(?:youtube\.com/embed|youtu\.be|player\.vimeo\.com)[^"\']+)["\']',
        html,
    )
    if m:
        src = m.group(1)
        return "https:" + src if src.startswith("//") else src
    return None


class JpSuccessScraper(ABC):
    """日本クラファン成功案件スクレイパーの基底クラス（Playwright）。"""

    platform: str

    def __init__(
        self,
        limit: int = 30,
        *,
        rate_limit_seconds: float | None = None,
        headless: bool = True,
        wait_ms: int = 2500,
        client: PlaywrightClient | None = None,
    ) -> None:
        self.limit = limit
        rls = (
            rate_limit_seconds
            if rate_limit_seconds is not None
            else settings.scrape_rate_limit_seconds
        )
        self._client = client or PlaywrightClient(
            rate_limit_seconds=rls, headless=headless, wait_ms=wait_ms
        )
        self._owns_client = client is None

    @abstractmethod
    def discover_urls(self) -> list[str]:
        """収集対象のプロジェクト詳細 URL 一覧を返す。"""

    @abstractmethod
    def parse_detail(
        self, url: str, inner_text: str, html: str
    ) -> JapaneseSuccessCreate | None:
        """詳細ページを正規化する。対象外/解析不能なら None。"""

    def scrape(self) -> list[JapaneseSuccessCreate]:
        items: list[JapaneseSuccessCreate] = []
        try:
            urls = list(dict.fromkeys(self.discover_urls()))[: self.limit]
            logger.info("%s: %d 件の候補URLを収集", self.platform, len(urls))
            for url in urls:
                try:
                    inner, html = self._client.get_content(url)
                    data = self.parse_detail(url, inner, html)
                except Exception as exc:  # noqa: BLE001  1件失敗は継続
                    logger.warning("%s detail failed (%s): %s", self.platform, url, exc)
                    continue
                if data is None:
                    continue
                # 成功案件（応援購入総額が下限以上）のみ採用
                if data.raised_amount is None or data.raised_amount < MIN_SUCCESS_JPY:
                    continue
                items.append(data)
        finally:
            if self._owns_client:
                self._client.close()
        return items
