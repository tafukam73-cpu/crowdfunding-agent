"""Ulule スクレイパー（フランス発クラウドファンディング）。

取得経路：discover（カテゴリ）ページの HTML。Ulule は動的描画寄りのため既定は
Playwright 経路で取得し、selectolax でプロジェクトカードをパースする。カードが
取れない場合（Cloudflare / 動的描画 / 構造変化）は ScraperStructureError を送出し、
collector が scrape_runs に「取得失敗理由」として記録する（ダミーは投入しない）。

初回検証の既定：1 カテゴリ・最大 10 件。取得できない項目は null。
カードに無い項目（説明・目標額・開始日 等）は将来の詳細取得で補う想定。

通貨は EUR 既定（ヨーロッパ案件）。country/region 列は無いため保存しない。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from selectolax.parser import HTMLParser

from app.models.project import SourceSite
from app.schemas.project import ProjectCreate
from app.scrapers.base import BaseScraper, ScraperStructureError
from app.scrapers.fetcher import Fetcher, get_fetcher

logger = logging.getLogger("scraper.ulule")

# サステナブル/デザイン/ライフスタイル系を狙う discover ページ
EXPLORE_URL = "https://www.ulule.com/en/discover/?sort=trending"
CATEGORY_LABEL = "Lifestyle & Design"

# Ulule のプロジェクトカード候補セレクタ（構造変化に多少耐えるよう複数）
_CARD_SELECTORS = (
    "div.ulule-project",
    "article.ulule-project",
    "li.project-item",
    "div.project-card",
    "article[class*=project]",
)

_CURRENCY_SYMBOLS: list[tuple[str, str]] = [
    ("€", "EUR"), ("$", "USD"), ("£", "GBP"), ("CHF", "CHF"),
]


def parse_money(text: str | None) -> tuple[str | None, Decimal | None]:
    """'12 500 €' / '€12,500' → ('EUR', Decimal('12500.00'))。

    ヨーロッパ表記（スペース/カンマ/ノーブレークスペースの桁区切り）を吸収し、
    記号が無い場合は EUR 既定。
    """
    if not text:
        return None, None
    currency = None
    for sym, code in _CURRENCY_SYMBOLS:
        if sym in text:
            currency = code
            break
    # 桁区切り（空白・NBSP・カンマ・末尾の小数点）を除去して整数部を得る
    num = re.sub(r"[^\d]", "", text.replace(" ", " "))
    amount: Decimal | None = None
    if num:
        try:
            amount = Decimal(num).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            amount = None
    return (currency or "EUR"), amount


def parse_count(text: str | None) -> int | None:
    """'320 contributors' / '1.2k' → 320 / 1200"""
    if not text:
        return None
    t = text.strip().lower()
    # thousands separators (space / NBSP / comma) between digits
    t = re.sub(r"(?<=\d)[\s ,](?=\d)", "", t)
    m = re.search(r"([\d.]+)\s*([km]?)", t)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    if unit == "k":
        val *= 1_000
    elif unit == "m":
        val *= 1_000_000
    return int(val)


def parse_days_left(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d+)\s*(day|jour)", text.lower())
    return int(m.group(1)) if m else None


def clean_url(href: str | None) -> str | None:
    if not href:
        return None
    href = href.split("#", 1)[0].split("?", 1)[0]
    if href.startswith("/"):
        href = "https://www.ulule.com" + href
    return href or None


def _text(card, selector: str) -> str | None:
    node = card.css_first(selector)
    return node.text(strip=True) if node else None


def _first_text(card, selectors: tuple[str, ...]) -> str | None:
    for sel in selectors:
        node = card.css_first(sel)
        if node:
            t = node.text(strip=True)
            if t:
                return t
    return None


def _parse_jsonld(html: str) -> list[dict]:
    """JSON-LD（ItemList 等）からプロジェクトを抽出するフォールバック。"""
    out: list[dict] = []
    tree = HTMLParser(html)
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        items = []
        if isinstance(data, dict) and data.get("@type") == "ItemList":
            items = [
                el.get("item", el) for el in data.get("itemListElement", [])
            ]
        elif isinstance(data, list):
            items = data
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name")
            url = it.get("url")
            if name or url:
                out.append({
                    "title": name,
                    "href": url,
                    "img": (it.get("image") if isinstance(it.get("image"), str) else None),
                    "maker": (it.get("author") or {}).get("name")
                    if isinstance(it.get("author"), dict) else None,
                })
    return out


def parse_cards(html: str) -> list[dict]:
    """discover ページの HTML からカードの生データ dict を抽出（重複除去）。"""
    tree = HTMLParser(html)
    cards = []
    for sel in _CARD_SELECTORS:
        cards = tree.css(sel)
        if cards:
            break

    out: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        anchor = card.css_first("a[href]")
        href = anchor.attributes.get("href") if anchor else None
        if href and href in seen:
            continue
        if href:
            seen.add(href)
        title = (
            _first_text(card, ("h2", "h3", ".project-title", "[class*=title]"))
            or (anchor.attributes.get("title") if anchor else None)
        )
        img = card.css_first("img")
        img_src = (
            img.attributes.get("src") or img.attributes.get("data-src")
            if img else None
        )
        out.append({
            "href": href,
            "title": title,
            "img": img_src,
            "maker": _first_text(card, ("[class*=author]", "[class*=creator]")),
            "funds": _first_text(card, ("[class*=amount]", "[class*=raised]", "[class*=collected]")),
            "goal": _first_text(card, ("[class*=goal]", "[class*=target]")),
            "backers": _first_text(card, ("[class*=backer]", "[class*=contributor]", "[class*=supporter]")),
            "time_left": _first_text(card, ("[class*=time]", "[class*=days]", "[class*=remaining]")),
        })

    if not out:
        out = _parse_jsonld(html)
    return out


def normalize_card(d: dict, category_label: str = CATEGORY_LABEL) -> ProjectCreate:
    """カード生データ → ProjectCreate（source_site=ulule）。取得できない項目は null。"""
    currency, raised = parse_money(d.get("funds"))
    _, goal = parse_money(d.get("goal"))
    days = parse_days_left(d.get("time_left"))
    end_date = date.today() + timedelta(days=days) if days is not None else None

    return ProjectCreate(
        title=d.get("title") or "(no title)",
        source_site=SourceSite.ulule,
        source_url=clean_url(d.get("href")),
        category=category_label,
        description=d.get("description"),
        image_url=d.get("img"),
        video_url=None,
        currency=currency or "EUR",
        goal_amount=goal,
        raised_amount=raised,
        backers_count=parse_count(d.get("backers")),
        start_date=None,
        end_date=end_date,
        maker_name=d.get("maker"),
        maker_url=None,
        contact_info=None,
    )


class UluleScraper(BaseScraper):
    site = SourceSite.ulule

    def __init__(
        self,
        *,
        limit: int = 10,
        explore_url: str = EXPLORE_URL,
        category_label: str = CATEGORY_LABEL,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        retries: int = 2,
        fetch_method: str = "playwright",
        fetcher: Fetcher | None = None,
    ) -> None:
        super().__init__(limit=limit)
        self.explore_url = explore_url
        self.category_label = category_label
        self._client: Fetcher = fetcher or get_fetcher(
            fetch_method,
            rate_limit_seconds=rate_limit_seconds,
            timeout=timeout,
            retries=retries,
        )
        self._owns_client = fetcher is None

    def scrape(self) -> list[ProjectCreate]:
        try:
            html = self._client.get_text(self.explore_url)
            cards = parse_cards(html)

            # --- 構造変化／取得失敗の検知 ---
            if not cards:
                raise ScraperStructureError(
                    "Ulule：プロジェクトカードを 1 件も抽出できませんでした"
                    "（Cloudflare / 動的描画 / 構造変化の可能性）"
                )
            if all(not c.get("title") and not c.get("href") for c in cards):
                raise ScraperStructureError(
                    "Ulule：カードから title/href を抽出できませんでした"
                    "（構造変化の可能性）"
                )

            results: list[ProjectCreate] = []
            for d in cards[: self.limit]:
                try:
                    results.append(normalize_card(d, self.category_label))
                except Exception as exc:  # noqa: BLE001  1 件失敗は握りつぶし継続
                    logger.warning("normalize failed, skip: %s", exc)
                    continue
            return results
        finally:
            if self._owns_client:
                self._client.close()
