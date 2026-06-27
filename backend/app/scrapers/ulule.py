"""Ulule スクレイパー（フランス発クラウドファンディング）。

Ulule は server-render された discover ページにプロジェクトのアンカー（ルート直下の
スラッグ URL）と `img[alt]`（タイトル）を持つが、CSS クラスはビルドハッシュで不安定。
そこで「安定して取れるもの」を軸に取得する：

  1) 一覧（discover）を Playwright で描画＋スクロールし、レンダリング済み HTML から
     プロジェクト URL・タイトル(img alt)・画像(img src/srcset) を抽出（HTML を直接パース）。
  2) 各詳細ページ（最大 limit 件）を開き、安定した OG メタ（og:title / og:description /
     og:image）と、本文テキストから資金額・支援者数を best-effort で補完。
  3) フォールバック：詳細が取れなければ一覧の title + url + image だけで保存。

エラーは分かりやすく分類して scrape_runs に記録する：
  blocked / empty_result / structure / parse_error / network。
失敗時は backend/debug/ulule_last.html / ulule_last.png を保存（Git 管理外）。

robots.txt/利用規約配慮：ログイン要箇所には入らず、レート制限・件数上限を守る。
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from app.models.project import SourceSite
from app.models.scrape_run import ErrorKind
from app.schemas.project import ProjectCreate
from app.scrapers.base import BaseScraper, ScraperStructureError
from app.scrapers.useragents import ua_for_attempt

logger = logging.getLogger("scraper.ulule")

BASE = "https://www.ulule.com"
# 実在を確認済みの一覧 URL（server-render でアンカーが出る）
DISCOVER_URL = "https://www.ulule.com/discover/"
CATEGORY_LABEL = "Lifestyle & Design"

# デバッグ保存先（backend/debug）。.gitignore 済み。
DEBUG_DIR = Path(__file__).resolve().parents[2] / "debug"

# プロジェクト URL とみなさないルートスラッグ
_EXCLUDE_SLUGS = {
    "discover", "signin", "signup", "soon", "pages", "en", "fr", "es", "de",
    "it", "nl", "search", "explore", "about", "blog", "help", "press", "jobs",
    "contact", "login", "logout", "account", "settings", "stats", "news",
}

CHALLENGE_MARKERS = (
    "just a moment", "attention required", "cf-challenge",
    "/cdn-cgi/challenge-platform", "verifying you are human",
    "enable javascript and cookies",
)

_CURRENCY_SYMBOLS: list[tuple[str, str]] = [
    ("€", "EUR"), ("$", "USD"), ("£", "GBP"), ("CHF", "CHF"),
]


class UluleScrapeError(Exception):
    """Ulule 取得失敗（分類つき）。collector が error_kind を尊重する。"""

    def __init__(self, message: str, error_kind: ErrorKind) -> None:
        super().__init__(message)
        self.error_kind = error_kind


# ---------------- 純粋関数（パース・正規化） ----------------
def _is_project_url(url: str) -> bool:
    path = urlparse(url).path.strip("/")
    if not path or "/" in path:
        return False
    return path.lower() not in _EXCLUDE_SLUGS


def _best_img(img) -> str | None:
    """img の srcset/src から最良の画像 URL を選ぶ（media.ulule.com を優先）。"""
    srcset = img.attributes.get("srcset")
    if srcset:
        candidates = [c.strip().split(" ")[0] for c in srcset.split(",") if c.strip()]
        for c in candidates:
            if "media.ulule.com" in c:
                return c
        if candidates:
            return candidates[-1]
    return img.attributes.get("src") or img.attributes.get("data-src")


def parse_list_html(html: str, base: str = BASE) -> list[dict]:
    """レンダリング済み discover HTML からカード（url/title/img）を抽出（重複除去）。"""
    tree = HTMLParser(html)
    out: list[dict] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href")
        if not href:
            continue
        url = urljoin(base, href.split("?")[0].split("#")[0])
        if not url.startswith(("http://", "https://")) or not _is_project_url(url):
            continue
        if not url.endswith("/"):
            url += "/"
        if url in seen:
            continue
        seen.add(url)

        title = img_src = None
        img = a.css_first("img")
        if img is not None:
            title = (img.attributes.get("alt") or "").strip() or None
            img_src = _best_img(img)
        title = (
            title
            or (a.attributes.get("title") or "").strip()
            or (a.attributes.get("aria-label") or "").strip()
            or None
        )
        out.append({"href": url, "title": title, "img": img_src})
    return out


def parse_money(text: str | None) -> tuple[str | None, Decimal | None]:
    """'12 500 €' / '€12,500' → ('EUR', Decimal)。記号なしは EUR 既定。"""
    if not text:
        return None, None
    currency = None
    for sym, code in _CURRENCY_SYMBOLS:
        if sym in text:
            currency = code
            break
    num = re.sub(r"[^\d]", "", text)
    amount: Decimal | None = None
    if num:
        try:
            amount = Decimal(num).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            amount = None
    return (currency or "EUR"), amount


def parse_count(text: str | None) -> int | None:
    if not text:
        return None
    t = re.sub(r"(?<=\d)[\s,](?=\d)", "", text.strip().lower())
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


def parse_detail_html(html: str) -> dict:
    """詳細ページ HTML から OG メタ＋本文 best-effort で項目を抽出する。"""
    tree = HTMLParser(html)

    def meta(*keys: str) -> str | None:
        for k in keys:
            n = (
                tree.css_first(f'meta[property="{k}"]')
                or tree.css_first(f'meta[name="{k}"]')
            )
            if n is not None:
                v = (n.attributes.get("content") or "").strip()
                if v:
                    return v
        return None

    title = meta("og:title", "twitter:title")
    description = meta("og:description", "twitter:description", "description")
    image = meta("og:image", "twitter:image")

    # 本文テキストから資金額・支援者数を best-effort 抽出
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    cur = raised = None
    m_money = re.search(r"(\d[\d\s ,]{2,})\s*€", text)
    if m_money:
        cur, raised = parse_money(m_money.group(0))
    backers = None
    m_back = re.search(
        r"(\d[\d\s ,]*)\s*(?:backers|contributors|supporters|contributeur)", text,
        re.IGNORECASE,
    )
    if m_back:
        backers = parse_count(m_back.group(1))

    return {
        "title": title,
        "description": description,
        "image": image,
        "currency": cur,
        "raised": raised,
        "backers": backers,
    }


def normalize(card: dict, detail: dict | None, category_label: str | None) -> ProjectCreate:
    """一覧カード＋（あれば）詳細 → ProjectCreate（source_site=ulule）。"""
    detail = detail or {}
    currency = detail.get("currency") or "EUR"
    return ProjectCreate(
        title=(detail.get("title") or card.get("title") or "(no title)")[:500],
        source_site=SourceSite.ulule,
        source_url=card.get("href"),
        category=category_label,
        description=detail.get("description"),
        image_url=detail.get("image") or card.get("img"),
        video_url=None,
        currency=currency,
        goal_amount=None,
        raised_amount=detail.get("raised"),
        backers_count=detail.get("backers"),
        start_date=None,
        end_date=None,
        maker_name=detail.get("maker"),
        maker_url=None,
        contact_info=None,
    )


def _looks_blocked(text: str) -> bool:
    low = (text or "")[:3000].lower()
    return any(m in low for m in CHALLENGE_MARKERS)


# ---------------- Playwright レンダラ ----------------
class _Renderer:
    """Ulule 用の最小 Playwright セッション（スクロール・スクショ対応）。"""

    def __init__(self, *, timeout: float, scroll_times: int = 4, wait_ms: int = 2500):
        self.timeout_ms = int(timeout * 1000)
        self.scroll_times = scroll_times
        self.wait_ms = wait_ms
        self._pw = self._browser = self._ctx = self._page = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(
            user_agent=ua_for_attempt(0),
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9,fr;q=0.8"},
        )

    def open(self, url: str, *, scroll: bool = False) -> tuple[int | None, str, str]:
        """URL を開き (status, html, body_text) を返す。前ページは閉じる。"""
        if self._page is not None:
            try:
                self._page.close()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._ctx.new_page()  # type: ignore[union-attr]
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            self._page.wait_for_load_state("networkidle", timeout=min(self.timeout_ms, 15000))
        except Exception:  # noqa: BLE001  networkidle 未達は許容
            pass
        if self.wait_ms:
            self._page.wait_for_timeout(self.wait_ms)
        if scroll:
            for _ in range(self.scroll_times):
                self._page.mouse.wheel(0, 5000)
                self._page.wait_for_timeout(900)
        html = self._page.content()
        body = self._page.evaluate("() => document.body ? document.body.innerText : ''")
        status = resp.status if resp is not None else None
        return status, html, body or ""

    def screenshot(self, path: Path) -> None:
        if self._page is not None:
            try:
                self._page.screenshot(path=str(path), full_page=False)
            except Exception as exc:  # noqa: BLE001
                logger.info("screenshot failed: %s", exc)

    def close(self) -> None:
        for obj in (self._page, self._ctx, self._browser):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


def _dump_debug(html: str | None, renderer: _Renderer | None) -> None:
    """失敗時に HTML 断片とスクリーンショットを保存（開発用・Git 管理外）。"""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        if html is not None:
            (DEBUG_DIR / "ulule_last.html").write_text(html, encoding="utf-8")
        if renderer is not None:
            renderer.screenshot(DEBUG_DIR / "ulule_last.png")
    except Exception as exc:  # noqa: BLE001  デバッグ保存失敗は無視
        logger.info("debug dump failed: %s", exc)


class UluleScraper(BaseScraper):
    site = SourceSite.ulule

    def __init__(
        self,
        *,
        limit: int = 10,
        explore_url: str = DISCOVER_URL,
        category_label: str | None = CATEGORY_LABEL,
        rate_limit_seconds: float = 2.0,
        timeout: float = 45.0,
        retries: int = 2,
        fetch_method: str = "playwright",
        fetcher=None,
        fetch_details: bool = True,
    ) -> None:
        super().__init__(limit=limit)
        self.explore_url = explore_url
        self.category_label = category_label
        self.timeout = timeout
        # fetcher を渡すとそれを使う（テスト用：get_text(url)->html）。
        self._fetcher = fetcher
        self.fetch_details = fetch_details

    # --- 取得（テストでは fetcher 経由、本番は Playwright） ---
    def scrape(self) -> list[ProjectCreate]:
        if self._fetcher is not None:
            return self._scrape_with_fetcher()
        return self._scrape_with_playwright()

    def _scrape_with_fetcher(self) -> list[ProjectCreate]:
        """テスト/簡易経路：fetcher.get_text(url) で list/detail を取得。"""
        try:
            html = self._fetcher.get_text(self.explore_url)
            cards = parse_list_html(html)
            if not cards:
                raise UluleScrapeError("Ulule[empty_result]: 一覧から0件", ErrorKind.empty_result)
            results: list[ProjectCreate] = []
            for c in cards[: self.limit]:
                detail = None
                if self.fetch_details and c.get("href"):
                    try:
                        detail = parse_detail_html(self._fetcher.get_text(c["href"]))
                    except Exception as exc:  # noqa: BLE001  詳細失敗は一覧で代替
                        logger.info("detail fetch failed: %s", exc)
                results.append(normalize(c, detail, self.category_label))
            return results
        finally:
            try:
                self._fetcher.close()
            except Exception:  # noqa: BLE001
                pass

    def _scrape_with_playwright(self) -> list[ProjectCreate]:
        renderer = _Renderer(timeout=self.timeout)
        list_html: str | None = None
        try:
            renderer.start()
            status, list_html, body = renderer.open(self.explore_url, scroll=True)

            if status in (403, 429, 503) or _looks_blocked(body) or _looks_blocked(list_html):
                _dump_debug(list_html, renderer)
                raise UluleScrapeError(
                    f"Ulule[blocked]: アクセスブロック/チャレンジ検出（status={status}）",
                    ErrorKind.blocked,
                )

            cards = parse_list_html(list_html)
            if not cards:
                _dump_debug(list_html, renderer)
                anchors = HTMLParser(list_html).css("a[href]")
                if len(anchors) < 10:
                    raise UluleScrapeError(
                        f"Ulule[empty_result]: 一覧に案件リンクが見つかりません（anchors={len(anchors)}）",
                        ErrorKind.empty_result,
                    )
                raise UluleScrapeError(
                    "Ulule[structure]: アンカーは存在するが案件 URL を抽出できません"
                    "（セレクタ/URL 構造の変化）",
                    ErrorKind.structure,
                )

            results: list[ProjectCreate] = []
            for c in cards[: self.limit]:
                detail = None
                if self.fetch_details and c.get("href"):
                    try:
                        _, dhtml, _ = renderer.open(c["href"], scroll=False)
                        detail = parse_detail_html(dhtml)
                    except Exception as exc:  # noqa: BLE001  詳細失敗は一覧で代替
                        logger.info("detail open failed (%s): %s", c["href"], exc)
                try:
                    results.append(normalize(c, detail, self.category_label))
                except Exception as exc:  # noqa: BLE001  1件失敗は継続
                    logger.warning("normalize failed, skip: %s", exc)
            if not results:
                _dump_debug(list_html, renderer)
                raise UluleScrapeError(
                    "Ulule[parse_error]: カードは取れたが1件も正規化できませんでした",
                    ErrorKind.parse_error,
                )
            return results
        except UluleScrapeError:
            raise
        except ScraperStructureError:
            _dump_debug(list_html, renderer)
            raise
        except Exception as exc:  # noqa: BLE001  goto 失敗等＝取得系
            _dump_debug(list_html, renderer)
            # Playwright のタイムアウト/接続失敗は network 扱い
            raise UluleScrapeError(
                f"Ulule[network]: ページ取得に失敗しました: {exc}", ErrorKind.network
            )
        finally:
            renderer.close()
