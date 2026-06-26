"""Indiegogo スクレイパー。

取得経路：explore（カテゴリ）ページの SSR HTML。
キャンペーンは XHR ではなくサーバーレンダリングされた `div.gfu-project-card`
（data-qa 属性付き）に埋め込まれているため、fetcher の get_text で
レンダリング済み HTML を取得し、selectolax でカードをパースする。

初回検証の既定：1 カテゴリ（tech-innovation）・最大 10 件。
取得できない項目（goal/description/start_date/video 等）は null で保存する。
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from selectolax.parser import HTMLParser

from app.models.project import SourceSite
from app.schemas.project import ProjectCreate
from app.scrapers.base import BaseScraper, ScraperStructureError
from app.scrapers.fetcher import Fetcher, get_fetcher

logger = logging.getLogger("scraper.indiegogo")

EXPLORE_URL = (
    "https://www.indiegogo.com/explore/tech-innovation"
    "?project_type=campaign&project_timing=trending&sort=trending"
)
CATEGORY_LABEL = "Tech & Innovation"

# 通貨記号 → ISO コード（長い接頭辞を先に並べる）
_CURRENCY_SYMBOLS: list[tuple[str, str]] = [
    ("HK$", "HKD"), ("US$", "USD"), ("CA$", "CAD"), ("A$", "AUD"),
    ("NZ$", "NZD"), ("NT$", "TWD"), ("S$", "SGD"), ("R$", "BRL"),
    ("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"),
    ("₩", "KRW"), ("₹", "INR"),
]


def parse_money(text: str | None) -> tuple[str | None, Decimal | None]:
    """'HK$6,302,621' → ('HKD', Decimal('6302621.00'))"""
    if not text:
        return None, None
    currency = None
    for sym, code in _CURRENCY_SYMBOLS:
        if sym in text:
            currency = code
            break
    num = re.sub(r"[^\d.]", "", text)
    amount: Decimal | None = None
    if num:
        try:
            amount = Decimal(num).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            amount = None
    return currency, amount


def parse_count(text: str | None) -> int | None:
    """'464' → 464 / '1.9k' → 1900"""
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    m = re.match(r"([\d.]+)\s*([km]?)", t)
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
    m = re.search(r"(\d+)\s*day", text)
    return int(m.group(1)) if m else None


def clean_url(href: str | None) -> str | None:
    if not href:
        return None
    return href.replace(":443", "").split("?")[0]


def _qa(card, name: str) -> str | None:
    node = card.css_first(f'[data-qa="{name}"]')
    return node.text(strip=True) if node else None


def parse_cards(html: str) -> list[dict]:
    """explore ページの HTML から各カードの生データ dict を抽出（重複除去）。"""
    tree = HTMLParser(html)
    out: list[dict] = []
    seen: set[str] = set()
    for card in tree.css("div.gfu-project-card"):
        cid = card.attributes.get("data-qa") or ""
        if cid in seen:
            continue
        seen.add(cid)

        anchor = card.css_first("a[href*='/projects/']")
        href = anchor.attributes.get("href") if anchor else None
        title = (anchor.attributes.get("title") if anchor else None) or _qa(
            card, "project-card:ProjectName"
        )
        img = card.css_first("img")
        img_src = img.attributes.get("src") if img else None

        out.append(
            {
                "id": cid,
                "href": href,
                "title": title,
                "img": img_src,
                "backers": _qa(card, "project-card:BackersCount"),
                "funds": _qa(card, "project-card:FundsGathered"),
                "time_left": _qa(card, "project-card:TimeLeft"),
                "maker": _qa(card, "main-creator-name"),
            }
        )
    return out


def normalize_card(d: dict, category_label: str = CATEGORY_LABEL) -> ProjectCreate:
    """カード生データ → ProjectCreate。取得できない項目は null。"""
    currency, raised = parse_money(d.get("funds"))
    days = parse_days_left(d.get("time_left"))
    end_date = date.today() + timedelta(days=days) if days is not None else None

    return ProjectCreate(
        title=d.get("title") or "(no title)",
        source_site=SourceSite.indiegogo,
        source_url=clean_url(d.get("href")),
        category=category_label,
        description=None,        # 一覧カードには無い → null（詳細取得は将来）
        image_url=d.get("img"),
        video_url=None,          # 詳細ページ依存 → null
        currency=currency or "USD",
        goal_amount=None,        # カードに目標額表示なし → null
        raised_amount=raised,
        backers_count=parse_count(d.get("backers")),
        start_date=None,         # カードに開始日なし → null
        end_date=end_date,       # 「残り N 日」から概算
        maker_name=d.get("maker"),
        maker_url=None,
        contact_info=None,
    )


class IndiegogoScraper(BaseScraper):
    site = SourceSite.indiegogo

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

            # --- 構造変化検知 ---
            # SSR HTML から `div.gfu-project-card` が 1 枚も取れない＝セレクタ/
            # マークアップの変化、またはブロックページ。ネットワークエラーと区別する。
            if not cards:
                raise ScraperStructureError(
                    "Indiegogo：project カードを 1 枚も抽出できませんでした"
                    "（構造変化またはブロックの可能性）"
                )
            # カードは取れたが title・href が全件欠落＝カード内部構造の変化。
            if all(not c.get("title") and not c.get("href") for c in cards):
                raise ScraperStructureError(
                    "Indiegogo：カードから title/href を抽出できませんでした"
                    "（構造変化の可能性）"
                )

            results: list[ProjectCreate] = []
            for d in cards[: self.limit]:
                try:
                    results.append(normalize_card(d, self.category_label))
                except Exception as exc:  # noqa: BLE001  1件失敗は握りつぶし継続
                    logger.warning("normalize failed, skip: %s", exc)
                    continue

            return results
        finally:
            if self._owns_client:
                self._client.close()
