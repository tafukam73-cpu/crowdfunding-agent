"""Wadiz スクレイパー（韓国発クラウドファンディング）。

Wadiz(www.wadiz.kr) は SPA で、HTML 直取得は Akamai により 403 になる。だが
トップページが叩く公開 JSON API `platform.wadiz.kr/main2/api/v1/pc/main/funding`
はブラウザ相当のヘッダー（Origin/Referer）を付ければ httpx で取得できる。ここから
リワード/プレオーダー案件を構造化 JSON で取得する（Playwright 不要・安定・軽量）。

取得項目（要件）：商品名 / URL / サイト名 / カテゴリ / 国 / 調達額 / 達成率 /
支援者数 / ステータス / 画像URL / メーカー名 / 説明(メモ) / 取得日時。

エラーは監視用に分類して scrape_runs に記録する：
  blocked（Akamai 403）/ empty_result（0件）/ parse_error / network。
取得できない場合も 0 件を握りつぶさず、理由付きで例外を送出する（collector が記録）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from app.models.project import SourceSite
from app.models.scrape_run import ErrorKind
from app.schemas.project import ProjectCreate
from app.scrapers.base import BaseScraper

logger = logging.getLogger("scraper.wadiz")

BASE = "https://www.wadiz.kr"
# トップページの「펀딩(funding)」一覧を返す公開 API（PC 版）。
API_URL = "https://platform.wadiz.kr/main2/api/v1/pc/main/funding"
CURRENCY = "KRW"
COUNTRY = "South Korea"

# API はブラウザからの XHR を想定するため Origin/Referer が必要。
_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Origin": BASE,
    "Referer": f"{BASE}/",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

_PRODUCT_TYPE_LABELS = {
    "REWARD": "Reward",
    "PREORDER": "Pre-order",
}


class WadizScrapeError(Exception):
    """Wadiz 取得失敗（分類つき）。collector が error_kind を尊重する。"""

    def __init__(self, message: str, error_kind: ErrorKind) -> None:
        super().__init__(message)
        self.error_kind = error_kind


# ---------------- 純粋関数（パース・正規化） ----------------
def _decimal(v) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("1"))
    except (InvalidOperation, ValueError):
        return None


def _goal_from_rate(amount: Decimal | None, rate) -> Decimal | None:
    """達成率(rate, 整数%) と調達額から目標額を逆算する（rate>0 のときのみ）。

    Wadiz の rate は整数パーセント（例: 14185 = 14185% = 141.85倍）。
    goal = amount / (rate/100) = amount * 100 / rate。
    """
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return None
    if amount is None or r <= 0:
        return None
    try:
        return (amount * Decimal(100) / Decimal(str(r))).quantize(Decimal("1"))
    except (InvalidOperation, ZeroDivisionError):
        return None


def _iso_date(s) -> date | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _status_label(item: dict, rate) -> str:
    """達成率と種別からステータス文言を作る。"""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        r = 0
    kind = _PRODUCT_TYPE_LABELS.get(item.get("productType"), item.get("productType") or "")
    remaining = item.get("remainingDay")
    if remaining is not None and remaining <= 0:
        base = "Funded" if r >= 100 else "Finished"
    else:
        base = "Live"
    return f"{base}{f' ({kind})' if kind else ''}"


def _build_memo(item: dict, *, rate, amount, goal, backers, category) -> str:
    """判断材料メモ（[Wadiz] マーカー付き）。国・ステータス・達成率・支援者数を明示。"""
    parts = [f"Country: {COUNTRY}", f"Status: {_status_label(item, rate)}"]
    try:
        pct = int(round(float(rate)))
    except (TypeError, ValueError):
        pct = None
    if pct is not None and amount is not None:
        if goal is not None:
            parts.append(f"Funded: {pct}% (raised KRW {int(amount):,} / goal KRW {int(goal):,})")
        else:
            parts.append(f"Funded: {pct}% (raised KRW {int(amount):,})")
    if backers:
        parts.append(f"Backers: {backers}")
    if category:
        parts.append(f"Category: {category}")
    return "[Wadiz] " + " · ".join(parts)


def normalize(item: dict) -> ProjectCreate:
    """Wadiz funding API の 1 件 → ProjectCreate（source_site=wadiz）。"""
    title = (item.get("title") or "(no title)").strip()[:500]
    link = item.get("linkUrl") or ""
    source_url = link if link.startswith("http") else (BASE + link if link else None)
    category = item.get("categoryName") or item.get("mainCategoryName")
    amount = _decimal(item.get("amount"))
    rate = item.get("rate")
    goal = _goal_from_rate(amount, rate)
    backers = item.get("participants")
    memo = _build_memo(
        item, rate=rate, amount=amount, goal=goal, backers=backers, category=category
    )
    return ProjectCreate(
        title=title,
        source_site=SourceSite.wadiz,
        source_url=source_url,
        category=category,
        description=memo,
        image_url=item.get("thumbnail") or None,
        video_url=None,
        currency=CURRENCY,
        goal_amount=goal,
        raised_amount=amount,
        backers_count=int(backers) if isinstance(backers, int) else None,
        start_date=_iso_date(item.get("startDate")),
        end_date=_iso_date(item.get("endDate")),
        maker_name=(item.get("makerName") or None),
        maker_url=None,
        contact_info=None,
    )


class WadizScraper(BaseScraper):
    site = SourceSite.wadiz

    def __init__(
        self,
        *,
        limit: int = 20,
        rate_limit_seconds: float = 2.0,
        timeout: float = 30.0,
        retries: int = 2,
        page_size: int = 20,
        max_pages: int = 5,
        page_get=None,
    ) -> None:
        super().__init__(limit=limit)
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self.retries = retries
        self.page_size = page_size
        self.max_pages = max_pages
        # page_get(page, size)->dict を渡すとそれを使う（テスト用。本番は httpx）。
        self._page_get = page_get

    def scrape(self) -> list[ProjectCreate]:
        own = self._page_get is None
        client: httpx.Client | None = None
        if own:
            client = httpx.Client(
                timeout=self.timeout, follow_redirects=True, headers=_API_HEADERS
            )

            def page_get(page: int, size: int) -> dict:
                resp = client.get(API_URL, params={"page": page, "size": size})
                if resp.status_code in (401, 403):
                    raise WadizScrapeError(
                        f"Wadiz[blocked]: API がブロックされました（status={resp.status_code}）",
                        ErrorKind.blocked,
                    )
                resp.raise_for_status()
                return resp.json()
        else:
            page_get = self._page_get

        collected: dict[str, dict] = {}
        per_page: dict[int, int] = {}
        try:
            for page in range(self.max_pages):
                if len(collected) >= self.limit:
                    break
                try:
                    data = page_get(page, self.page_size)
                except WadizScrapeError:
                    raise
                except httpx.HTTPError as exc:
                    raise WadizScrapeError(
                        f"Wadiz[network]: API 取得に失敗しました: {exc}", ErrorKind.network
                    )
                items = _extract_items(data)
                new = 0
                for it in items:
                    key = str(it.get("id") or it.get("linkUrl") or it.get("title"))
                    if not key or key in collected:
                        continue
                    collected[key] = it
                    new += 1
                    if len(collected) >= self.limit:
                        break
                per_page[page] = new
                # これ以上ページがない（空/新規0）なら打ち切り
                if not items or new == 0:
                    break
            logger.info(
                "Wadiz API 取得: 合計 %d 件 / ページ別 %s", len(collected), per_page
            )
        finally:
            if client is not None:
                client.close()

        if not collected:
            raise WadizScrapeError(
                "Wadiz[empty_result]: API から案件を1件も取得できませんでした",
                ErrorKind.empty_result,
            )

        results: list[ProjectCreate] = []
        for it in list(collected.values())[: self.limit]:
            try:
                results.append(normalize(it))
            except Exception as exc:  # noqa: BLE001  1件失敗は継続
                logger.warning("Wadiz normalize 失敗, skip: %s", exc)
        if not results:
            raise WadizScrapeError(
                "Wadiz[parse_error]: 取得はできたが1件も正規化できませんでした",
                ErrorKind.parse_error,
            )
        return results


def _extract_items(data) -> list[dict]:
    """API レスポンスから案件リストを取り出す（{'data': [...]} 形式想定・堅牢化）。"""
    if isinstance(data, dict):
        d = data.get("data")
        if isinstance(d, list):
            return [x for x in d if isinstance(x, dict)]
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []
