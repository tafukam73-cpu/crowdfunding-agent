"""Zeczec 詳細ページのパース（純粋関数）。

詳細ページ（/projects/{slug}）は Cloudflare の JS チャレンジで保護されており httpx では
403 になる。実ブラウザ（Playwright）で取得した HTML を、この純粋関数群でパースして
「確認できた事実だけ」を抽出する（推測しない）。取得できない項目は None を返し、
呼び出し側が理由を記録する。

確認済みの DOM 構造（実測）:

    <div class="text-xs text-gray-500 mb-2 tracking-wider">
      <a href="/categories?type=1">預購式專案</a>          ← プロジェクト種別（メモ用）
      <div class="inline-block mx-1">\\</div>
      <a href="/categories?category=18">挺好店</a>          ← カテゴリ（確定）
    </div>
    <div class="text-sm text-gray-500">
      <span>提案人</span>
      <a href="/users/3743542">MORESIE</a>                 ← 提案人＝メーカー名（確定）
    </div>

    <meta property="og:description" content="...">          ← 商品説明（確定）
    「已於 2026/07/07 募資成功」/「剩餘時間 21 天」          ← ステータス・終了日

外部リンク（プロジェクト本文の非プラットフォームリンク）は公式サイト候補になる。
Zeczec ページから直接リンク＝high、短縮 URL＝low（未解決のため候補扱い）。
"""
from __future__ import annotations

import html as _html
import re
from datetime import date
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

BASE = "https://www.zeczec.com"

# Cloudflare / チャレンジページの検出マーカー（zh/en 両対応）。
CHALLENGE_MARKERS = (
    "just a moment",
    "verifying you are human",
    "checking your browser",
    "attention required",
    "cf-challenge",
    "/cdn-cgi/challenge-platform",
    "請稍候",
    "正在執行安全驗證",
    "正在执行安全验证",
    "正在验证",
)

# 公式サイト候補から完全に除外するホスト（SNS / メッセージ / プラットフォーム /
# 決済 / マーケットプレイス / フォーム / 解析タグ）。公式サイトになり得ない。
_EXCLUDE_HOST_HINTS = (
    "zeczec.com",
    # SNS / メッセージング
    "facebook.", "fb.com", "messenger.com", "m.me", "instagram.", "twitter.",
    "x.com", "youtube.", "youtu.be", "line.me", "lin.ee", "wa.me",
    "whatsapp.", "t.me", "telegram.", "linktr.ee", "tiktok.", "pinterest.",
    "threads.net", "weibo.", "wechat.",
    # プラットフォーム / マーケットプレイス / 決済
    "google.", "gstatic.", "googletagmanager.", "apple.com", "play.google",
    "amazon.", "shopee.", "ebay.", "etsy.", "aliexpress.", "momoshop.",
    "pchome.", "ruten.", "books.com.tw", "pinkoi.com", "aftee.", "afterpay.",
    "paypal.", "stripe.", "cloudflare.",
    # 他クラファン / メディア配信 / 集約
    "kickstarter.", "indiegogo.", "flyingv.cc", "backme.tw", "soundon.",
    "soundcloud.", "spotify.", "medium.com", "wikipedia.", "notion.site",
    # フォーム
    "docs.google.", "forms.gle", "surveycake.", "typeform.",
)

# 短縮 URL（リダイレクト）。解決していないため公式サイトとして確定せず低確度候補にする。
_SHORTENER_HOSTS = (
    "reurl.cc", "bit.ly", "lihi.cc", "lihi1.cc", "lihi2.cc", "lihi3.cc",
    "pse.is", "tinyurl.com", "ppt.cc", "myppt.cc", "risu.io", "pros.is",
    "cutt.ly", "0rz.tw", "goo.gl", "t.co",
)

# ニュース / メディア / ブログのホスト。プロジェクトページからのリンクでも「公式
# サイト」ではないため、除外はしないが high にせず低確度候補（自動確定しない）にする。
_MEDIA_HOST_HINTS = (
    "thenewslens.com", "udn.com", "ettoday.net", "businessweekly.com.tw",
    "bnext.com.tw", "technews.tw", "cool3c.com", "mashdigi.com", "inside.com.tw",
    "chinatimes.com", "ltn.com.tw", "setn.com", "tvbs.com.tw", "storm.mg",
    "yahoo.", "pixnet.net", "blogspot.", "wordpress.com", "wixsite.com",
    "prnewswire.", "businesswire.",
)

# 記事/投稿らしい深いパスの語（このパスを含む直リンクは公式トップと見なさない）。
_ARTICLE_PATH_HINTS = (
    "/article/", "/articles/", "/news/", "/story/", "/posts/", "/post/",
    "/blog/", "/p/", "/watch", "/video/",
)

_USERS_RE = re.compile(r"^/users/\d+")
_END_DATE_RE = re.compile(
    r"已於\s*(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\s*(?:募資成功|募資結束|結束|成功)"
)
_REMAIN_RE = re.compile(r"剩餘時間\s*([\d,]+)\s*天")


def looks_like_challenge(text: str) -> bool:
    """Cloudflare チャレンジ/ブロックページかどうか（冒頭のみで判定）。"""
    low = (text or "")[:2000].lower()
    return any(m in low for m in CHALLENGE_MARKERS)


def _meta(html: str, key: str, attr: str = "property") -> str | None:
    m = re.search(
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if not m:
        # content が先に来る並びも許容
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+{attr}=["\']{re.escape(key)}["\']',
            html,
            re.IGNORECASE,
        )
    if not m:
        return None
    val = _html.unescape(m.group(1)).strip()
    return val or None


def _clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = " ".join(s.split()).strip()
    return s or None


def parse_creator(tree: HTMLParser) -> tuple[str | None, str | None]:
    """提案人（メーカー名）と creator プロフィール URL を返す。

    /users/{数字} を指す最初のアンカー（sign_in/sign_up は数字でないため自然に除外）。
    """
    for a in tree.css('a[href^="/users/"]'):
        href = a.attributes.get("href") or ""
        if not _USERS_RE.match(href):
            continue
        name = _clean_text(a.text())
        if not name or name in ("登入", "註冊", "登出"):
            continue
        clean_href = href.split("?")[0].split("#")[0]
        return name, urljoin(BASE, clean_href)
    return None, None


def parse_breadcrumb(tree: HTMLParser) -> tuple[str | None, str | None]:
    """(category, project_type) を返す。

    category は `<a href="/categories?category=N">科技</a>` の表示テキスト。
    project_type は `<a href="/categories?type=N">預購式專案</a>`（メモ用）。
    """
    category = project_type = None
    for a in tree.css('a[href*="/categories?"]'):
        href = a.attributes.get("href") or ""
        text = _clean_text(a.text())
        if not text:
            continue
        if "category=" in href and category is None:
            category = text
        elif "type=" in href and project_type is None:
            project_type = text
    return category, project_type


def parse_status_and_end_date(inner_text: str) -> tuple[str | None, date | None]:
    """本文テキストからステータスと終了日（確定できる場合のみ）を返す。

    - 「已於 YYYY/MM/DD 募資成功」→ status=funded, end_date=その日
    - 「募資結束/已結束/過去集資金額」→ status=finished
    - 「剩餘時間 N 天」→ status=live
    どれも無ければ (None, None)。推測しない。
    """
    text = inner_text or ""
    m = _END_DATE_RE.search(text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            ed = date(y, mo, d)
        except ValueError:
            ed = None
        status = "funded" if "成功" in m.group(0) else "finished"
        return status, ed
    if "過去集資金額" in text or "已結束" in text or "募資結束" in text:
        return "finished", None
    if _REMAIN_RE.search(text):
        return "live", None
    return None, None


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _is_shortener(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _SHORTENER_HOSTS)


def _is_excludable(url: str) -> bool:
    host = _host(url)
    return any(h in host for h in _EXCLUDE_HOST_HINTS)


def _is_media(url: str) -> bool:
    host = _host(url)
    return any(h in host for h in _MEDIA_HOST_HINTS)


def _is_article_path(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return any(h in path for h in _ARTICLE_PATH_HINTS)


def classify_external_link(url: str) -> tuple[str, str] | None:
    """外部リンクを (confidence, source) に分類する。除外なら None。

    - SNS/メッセージ/プラットフォーム/決済/フォーム → 除外（公式になり得ない）
    - 短縮 URL（未解決） → low / zeczec_page_shortlink
    - ニュース/メディア/ブログ or 記事パス → low / zeczec_page_media_link（自動確定しない）
    - それ以外の直リンク → high / zeczec_page_direct_link（＝メーカーがページに載せた公式導線）
    """
    if _is_excludable(url):
        return None
    if _is_shortener(url):
        return "low", "zeczec_page_shortlink"
    if _is_media(url) or _is_article_path(url):
        return "low", "zeczec_page_media_link"
    return "high", "zeczec_page_direct_link"


def parse_official_candidates(tree: HTMLParser) -> list[dict]:
    """プロジェクト本文の外部リンクから公式サイト候補を抽出する。

    確度: high=メーカーがページに載せた公式導線 / low=短縮 URL・メディア・記事
    （自動確定しない）。返り値は URL 重複排除済み。確定は呼び出し側が確度で判断する。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for a in tree.css('a[href^="http"]'):
        href = (a.attributes.get("href") or "").split("#")[0]
        if not href.startswith(("http://", "https://")):
            continue
        cls = classify_external_link(href)
        if cls is None:
            continue
        confidence, source = cls
        # 短縮/メディアはクエリ付き URL のまま、直リンクはクエリ除去して正規化。
        url = href if source == "zeczec_page_shortlink" else href.split("?")[0]
        key = url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({"url": url, "confidence": confidence, "source": source})
    return out


def parse_socials(tree: HTMLParser) -> dict[str, str]:
    """ブランドの SNS リンク（Zeczec 自身の公式アカウントは除外）を返す。

    Zeczec のフッターには zeczec 自身の SNS（instagram.com/zeczec_com 等）が並ぶため、
    ハンドルに 'zeczec' を含むもの・共有用（sharer/intent）は除外する。所有者は確定
    できないため候補（低確度）扱いで、呼び出し側は自動確定しない。
    """
    patterns = {
        "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[^/\s\"'<>?]+", re.I),
        "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[^/\s\"'<>?]+", re.I),
        "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/[^\s\"'<>?]+", re.I),
        "twitter": re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^/\s\"'<>?]+", re.I),
    }
    found: dict[str, str] = {}
    for a in tree.css('a[href^="http"]'):
        href = a.attributes.get("href") or ""
        low = href.lower()
        if "zeczec" in low or "sharer" in low or "/intent/" in low or "/tr?" in low:
            continue
        if "/reel/" in low or "/p/" in low or "/share" in low:
            continue
        for plat, pat in patterns.items():
            if plat in found:
                continue
            if pat.match(href):
                found[plat] = href.split("?")[0]
    return found


def parse_detail(html: str, inner_text: str = "") -> dict:
    """詳細ページ HTML（＋body innerText）から確認できた事実だけを抽出する。

    Returns（取得不能項目は None / 空）:
      {
        "challenged": bool,          # チャレンジページなら True（他は無効）
        "maker_name", "creator_url",
        "category", "project_type",
        "description",               # og:description
        "og_title",
        "status", "end_date",
        "official_candidates": [{url, confidence, source}],
        "socials": {platform: url},
      }
    """
    if looks_like_challenge(html) or looks_like_challenge(inner_text):
        return {"challenged": True}

    tree = HTMLParser(html)
    maker_name, creator_url = parse_creator(tree)
    category, project_type = parse_breadcrumb(tree)
    status, end_date = parse_status_and_end_date(inner_text or tree.text() or "")
    description = _meta(html, "og:description") or _meta(html, "description", attr="name")
    og_title = _meta(html, "og:title")

    return {
        "challenged": False,
        "maker_name": maker_name,
        "creator_url": creator_url,
        "category": category,
        "project_type": project_type,
        "description": description,
        "og_title": og_title,
        "status": status,
        "end_date": end_date,
        "official_candidates": parse_official_candidates(tree),
        "socials": parse_socials(tree),
    }
