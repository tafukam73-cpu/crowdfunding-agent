"""Zeczec（嘖嘖）スクレイパー（台湾発クラウドファンディング）。

Zeczec(www.zeczec.com) の一覧 `/categories` は server-render された HTML で、各カードに
プロジェクト URL・タイトル・画像・調達額(NT$)・達成率(%)・支援者数(person N 人)・
残り日数を持つ。トップ/`/explore` は 403 だが `/categories` はブラウザ相当ヘッダーの
httpx で 200 を返すため、これを一覧ソースにする（詳細ページは 403 のため使わない）。

取得項目（要件）：商品名 / URL / サイト名 / カテゴリ(null可) / 国 / 調達額 / 達成率 /
支援者数 / ステータス / 画像URL / メーカー名(null可) / 説明(メモ) / 取得日時。

エラー分類：blocked（403）/ empty_result（0件）/ structure（カード抽出不能）/ network。
0 件を握りつぶさず、理由付きで例外を送出する（collector が scrape_runs に記録）。
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from app.models.project import SourceSite
from app.models.scrape_run import ErrorKind
from app.schemas.project import ProjectCreate

from app.scrapers.base import BaseScraper

logger = logging.getLogger("scraper.zeczec")

BASE = "https://www.zeczec.com"
LIST_URL = f"{BASE}/categories"
CURRENCY = "TWD"
COUNTRY = "Taiwan"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": f"{BASE}/",
}

CHALLENGE_MARKERS = ("access denied", "forbidden", "just a moment", "error 403")

_MONEY_RE = re.compile(r"NT\$\s*([\d,]+)")
_PCT_RE = re.compile(r"([\d,]+)\s*%")
_BACKERS_RE = re.compile(r"person\s*([\d,]+)")
_DAYS_RE = re.compile(r"hourglass_top\s*([\d,]+)")


class ZeczecScrapeError(Exception):
    """Zeczec 取得失敗（分類つき）。collector が error_kind を尊重する。"""

    def __init__(self, message: str, error_kind: ErrorKind) -> None:
        super().__init__(message)
        self.error_kind = error_kind


# ---------------- 純粋関数（パース・正規化） ----------------
def _int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def _decimal(s: str | None) -> Decimal | None:
    n = _int(s)
    if n is None:
        return None
    try:
        return Decimal(n)
    except (InvalidOperation, ValueError):
        return None


def _looks_blocked(text: str) -> bool:
    low = (text or "")[:2000].lower()
    return any(m in low for m in CHALLENGE_MARKERS)


def parse_list_html(html: str, base: str = BASE) -> list[dict]:
    """`/categories` の HTML からプロジェクトカードを抽出する。

    各カードは `<a data-content-type="project" href="/projects/{slug}">` で、
    配下に `<img>`（data-src/src）・`<h3>`（タイトル）と、達成率/NT$/支援者数/残日数を
    含むテキストを持つ。
    """
    tree = HTMLParser(html)
    out: list[dict] = []
    seen: set[str] = set()
    for a in tree.css('a[data-content-type="project"]'):
        href = a.attributes.get("href")
        if not href:
            continue
        url = urljoin(base, href.split("?")[0].split("#")[0])
        path = urlparse(url).path
        if "/projects/" not in path:
            continue
        if url in seen:
            continue
        seen.add(url)

        h3 = a.css_first("h3")
        title = h3.text(strip=True) if h3 else None
        img = a.css_first("img")
        img_src = None
        if img is not None:
            img_src = (
                img.attributes.get("data-src")
                or img.attributes.get("src")
                or img.attributes.get("data-original")
            )
        text = " ".join(a.text().split())
        m_money = _MONEY_RE.search(text)
        m_pct = _PCT_RE.search(text)
        m_back = _BACKERS_RE.search(text)
        m_days = _DAYS_RE.search(text)
        out.append(
            {
                "url": url,
                "title": title,
                "img": img_src,
                "raised": m_money.group(1) if m_money else None,
                "rate": m_pct.group(1) if m_pct else None,
                "backers": m_back.group(1) if m_back else None,
                "days": m_days.group(1) if m_days else None,
            }
        )
    return out


def _goal_from_rate(amount: Decimal | None, rate_pct: int | None) -> Decimal | None:
    if amount is None or not rate_pct or rate_pct <= 0:
        return None
    try:
        return (amount * Decimal(100) / Decimal(rate_pct)).quantize(Decimal("1"))
    except (InvalidOperation, ZeroDivisionError):
        return None


def _build_memo(*, rate_pct, amount, goal, backers, days) -> str:
    parts = [f"Country: {COUNTRY}"]
    if days is not None and days <= 0:
        status = "Funded" if (rate_pct or 0) >= 100 else "Finished"
    else:
        status = "Live"
    parts.append(f"Status: {status}")
    if rate_pct is not None and amount is not None:
        if goal is not None:
            parts.append(f"Funded: {rate_pct}% (raised TWD {int(amount):,} / goal TWD {int(goal):,})")
        else:
            parts.append(f"Funded: {rate_pct}% (raised TWD {int(amount):,})")
    elif amount is not None:
        parts.append(f"Raised: TWD {int(amount):,}")
    if backers:
        parts.append(f"Backers: {backers}")
    if days is not None and days > 0:
        parts.append(f"Remaining: {days} days")
    return "[Zeczec] " + " · ".join(parts)


def normalize(card: dict) -> ProjectCreate:
    """一覧カード → ProjectCreate（source_site=zeczec）。"""
    amount = _decimal(card.get("raised"))
    rate_pct = _int(card.get("rate"))
    backers = _int(card.get("backers"))
    days = _int(card.get("days"))
    goal = _goal_from_rate(amount, rate_pct)
    memo = _build_memo(
        rate_pct=rate_pct, amount=amount, goal=goal, backers=backers, days=days
    )
    return ProjectCreate(
        title=(card.get("title") or "(no title)")[:500],
        source_site=SourceSite.zeczec,
        source_url=card.get("url"),
        category=None,  # 一覧カードにカテゴリ表記がないため null（詳細は 403 で不可）
        description=memo,
        image_url=card.get("img") or None,
        video_url=None,
        currency=CURRENCY,
        goal_amount=goal,
        raised_amount=amount,
        backers_count=backers,
        start_date=None,
        end_date=None,
        maker_name=None,  # 一覧にメーカー名がないため null
        maker_url=None,
        contact_info=None,
    )


class ZeczecScraper(BaseScraper):
    site = SourceSite.zeczec

    def __init__(
        self,
        *,
        limit: int = 20,
        list_urls: list[str] | None = None,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        retries: int = 2,
        fetcher=None,
    ) -> None:
        super().__init__(limit=limit)
        # 既定は総合一覧＋人気/最新で件数を確保（重複はURLで排除）。
        self.list_urls = list_urls or [
            LIST_URL,
            f"{LIST_URL}?scope=hot",
            f"{LIST_URL}?scope=new",
        ]
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self.retries = retries
        # fetcher.get_text(url)->html を渡すとそれを使う（テスト用）。
        self._fetcher = fetcher

    def scrape(self) -> list[ProjectCreate]:
        own = self._fetcher is None
        client: httpx.Client | None = None
        if own:
            client = httpx.Client(
                timeout=self.timeout, follow_redirects=True, headers=_HEADERS
            )

            def get_text(url: str) -> str:
                resp = client.get(url)
                if resp.status_code in (401, 403):
                    raise ZeczecScrapeError(
                        f"Zeczec[blocked]: 一覧がブロックされました（status={resp.status_code}, {url}）",
                        ErrorKind.blocked,
                    )
                resp.raise_for_status()
                return resp.text
        else:
            get_text = self._fetcher.get_text

        collected: dict[str, dict] = {}
        try:
            for url in self.list_urls:
                if len(collected) >= self.limit:
                    break
                try:
                    html = get_text(url)
                except ZeczecScrapeError:
                    raise
                except httpx.HTTPError as exc:
                    raise ZeczecScrapeError(
                        f"Zeczec[network]: 一覧取得に失敗しました: {exc}", ErrorKind.network
                    )
                if _looks_blocked(html):
                    raise ZeczecScrapeError(
                        f"Zeczec[blocked]: アクセスブロック/チャレンジ検出（{url}）",
                        ErrorKind.blocked,
                    )
                cards = parse_list_html(html)
                for c in cards:
                    key = c.get("url")
                    if not key or key in collected:
                        continue
                    collected[key] = c
                    if len(collected) >= self.limit:
                        break
        finally:
            if client is not None:
                client.close()
            if not own:
                try:
                    self._fetcher.close()
                except Exception:  # noqa: BLE001
                    pass

        if not collected:
            raise ZeczecScrapeError(
                "Zeczec[empty_result]: 一覧からプロジェクトカードを抽出できませんでした",
                ErrorKind.empty_result,
            )

        results: list[ProjectCreate] = []
        for c in list(collected.values())[: self.limit]:
            try:
                results.append(normalize(c))
            except Exception as exc:  # noqa: BLE001  1件失敗は継続
                logger.warning("Zeczec normalize 失敗, skip: %s", exc)
        if not results:
            raise ZeczecScrapeError(
                "Zeczec[structure]: カードは取れたが1件も正規化できませんでした",
                ErrorKind.structure,
            )
        logger.info("Zeczec 取得: %d 件", len(results))
        return results
