"""AI Web Research Mode の業務ロジック。

既存の Contact Discovery（公式サイト中心のクロール）/ AI Contact Research（既存
結果の整理）に加えて、検索エンジン（DuckDuckGo HTML）の結果と公式サイトの代表
パスを横断クロールし、メール・問い合わせフォーム・SNS・PDF を「実際に取得した
ページから」抽出する。

設計方針：
- メールは推測で作らない。すべて実際に取得したページ本文/mailto から抽出し、
  contact_discovery_service の既存除外フィルタ（platform / sentry / no-reply /
  postmaster / hash 等）を必ず通す。各メールは出典 URL（sources）を持つ。
- 検索クエリは企業名単体に寄せず、商品名・プロジェクト名・ブランド名・公式ドメイン
  ・SNS 名を複合的に組み合わせて生成する（手動 Google 検索で見つかる SNS を
  ツールでも見つけられるようにする）。
- 検索結果はスコアリングして採用/除外を判定し、SNS URL は正規化する。クラファン
  運営（platform）自身の公式 SNS は誤採用しない。
- 抽出・スコアリング・推奨判定は contact_discovery_service の純粋関数を再利用する。
- ネットワークは fetch_fn（url->html|None）/ search_fn（query->[url]|[{url,...}]）と
  して注入でき、テストはネットワーク無しで検証できる。
- 安全設計：クエリ数・URL 数・タイムアウト・レート制限・重複排除・robots 配慮・
  ログインページ回避。失敗してもアプリは落とさない。

結果は最新の ContactDiscovery 行の web_* カラムに分離保存する（自動抽出/AI 調査を
無条件上書きしない）。デバッグ用に「生成キーワード候補・生成クエリ全体・実行クエリ
・検索結果のスコアと採用/除外理由」も保存する。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch
from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds

logger = logging.getLogger("web_research")

# --- 安全設計のパラメータ ---
MAX_QUERIES = 20            # 実行する検索クエリ数の上限（MVP: 上位 15〜25）
MAX_RESULTS_PER_QUERY = 6   # 1 クエリあたり採用する検索結果 URL 数の上限
MAX_URLS = 25               # クロールする URL 数の上限
MAX_SEARCH_RESULTS_SAVED = 80   # デバッグ保存する検索結果レコード数の上限
SOCIAL_ADOPT_MIN_SCORE = 30     # 検索結果由来の SNS を採用する最低スコア
FETCH_TIMEOUT = 8.0         # 1 ページのタイムアウト（秒）
SEARCH_TIMEOUT = 8.0        # 検索のタイムアウト（秒）
RATE_LIMIT_SECONDS = 1.5    # ページ/検索の間隔（過度なアクセスを避ける）

# 公式サイト内で当たりにいく代表パス（要件 4）
WEB_KNOWN_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/press",
    "/media",
    "/wholesale",
    "/distributor",
    "/distribution",
    "/partnership",
    "/partners",
    "/business",
    "/b2b",
    "/retail",
    "/pages/contact",
    "/pages/about",
]

# ログイン/カート等、入ってはいけない / 営業に無関係なパスの語
_SKIP_URL_HINTS = (
    "login",
    "signin",
    "sign-in",
    "sign_in",
    "/account",
    "/cart",
    "/checkout",
    "wp-login",
    "wp-admin",
    "/admin",
    "/register",
    "/signup",
    "/sign-up",
)

# 検索エンジンのドメイン（検索結果に紛れる自分自身を除外）
_SEARCH_ENGINE_HOSTS = ("duckduckgo.com", "bing.com", "google.com", "yahoo.com")

# 検索結果 URL のうち、本人アカウント/実ページではないため除外する語（要件 4・5）。
_RESULT_EXCLUDE_RE = re.compile(
    r"(sharer|/share|/intent|/dialog|/plugins|/tr\?|oauth|/login|/signin|"
    r"/search|/hashtag|/explore/|/accounts/login|/p/|/reel/|/reels/|/stories/)",
    re.IGNORECASE,
)

# クラファン運営（platform）の SNS ハンドル。運営自身の公式 SNS を誤採用しない。
_PLATFORM_SOCIAL_HANDLES = frozenset(
    {
        "kickstarter",
        "indiegogo",
        "ulule",
        "ululecom",
        "makuake",
        "wadiz",
        "greenfunding",
        "greenfundingjp",
        "campfire",
    }
)

# キーワード抽出時に落とすありふれた語（ブランド名候補のノイズ低減）。
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "your", "you", "our", "are", "this", "that",
        "from", "official", "brand", "new", "now", "pro", "max", "mini", "kit",
        "ltd", "inc", "llc", "gmbh", "the", "all", "best", "get", "buy", "shop",
        "store", "world", "first", "more", "make", "made", "design", "designed",
        "project", "campaign", "kickstarter", "indiegogo", "ulule", "makuake",
        "introducing", "meet", "smart", "ultimate", "premium",
    }
)


# ---------------- キーワード候補（要件 1） ----------------
_BRAND_TOKEN_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&']+(?:\s[A-Z][A-Za-z0-9&']+){0,2})\b"
)
_TITLE_SUBTITLE_SPLIT = re.compile(r"\s[\-–—|｜:：]\s|[:：|｜–—]")
_BRACKETS_RE = re.compile(r"[\(\（\[【].*?[\)\）\]】]")


def _short_title(title: str) -> str:
    """タイトルから記号・副題を除いた短縮名を作る（要件 1）。"""
    if not title:
        return ""
    head = _TITLE_SUBTITLE_SPLIT.split(title, 1)[0]
    head = _BRACKETS_RE.sub("", head)
    head = re.sub(r"\s+", " ", head).strip(" -–—|｜:：")
    return head.strip()


def _extract_brand_names(text: str | None, limit: int = 4) -> list[str]:
    """説明文・リサーチ要約から Title Case のブランド名候補を抽出する（要件 1）。"""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _BRAND_TOKEN_RE.findall(text):
        w = re.sub(r"\s+", " ", m).strip()
        if len(w) < 3:
            continue
        # 1 語のみのときはありふれた語を落とす
        if " " not in w and w.lower() in _STOPWORDS:
            continue
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def build_keyword_candidates(
    project: Project, research: CompanyResearch | None = None
) -> dict:
    """検索語の素材になるキーワード候補を構造化して返す（要件 1）。"""
    title = (project.title or "").strip()
    short = _short_title(title)
    maker = (project.maker_name or "").strip()
    official_domain = cds._domain_of(project.maker_url)
    domain_name = official_domain.split(".")[0] if official_domain else ""
    source_site = str(getattr(project, "source_site", "") or "")

    brand_names: list[str] = []
    seen_brand: set[str] = set()

    def add_brand(name: str | None) -> None:
        if not name:
            return
        name = name.strip()
        key = name.lower()
        if not name or key in seen_brand:
            return
        # タイトル/メーカー名そのものは別枠なので重複登録しない
        if key in (title.lower(), maker.lower(), short.lower()):
            return
        seen_brand.add(key)
        brand_names.append(name)

    if research is not None:
        for name in _extract_brand_names(getattr(research, "brand_summary", None)):
            add_brand(name)
        for name in _extract_brand_names(getattr(research, "product_summary", None)):
            add_brand(name)
        add_brand(getattr(research, "maker_name", None))
    desc = (
        getattr(project, "description_clean", None)
        or getattr(project, "description", None)
        or ""
    )
    for name in _extract_brand_names(desc[:1500]):
        add_brand(name)

    return {
        "project_title": title,
        "short_title": short if short and short != title else "",
        "maker_name": maker,
        "brand_names": brand_names[:4],
        "official_domain": official_domain,
        "domain_name": domain_name,
        "source_site": source_site,
    }


def build_web_search_queries(
    project: Project, research: CompanyResearch | None = None
) -> list[str]:
    """複合検索クエリを優先度順に生成する（要件 2・3）。

    企業名単体に寄せず、商品名/プロジェクト名/ブランド名/公式ドメイン/SNS 名を
    複合的に組み合わせる。SNS 発見を最優先に並べる。重複排除・順序維持。
    """
    kw = build_keyword_candidates(project, research)
    title = kw["project_title"]
    short = kw["short_title"]
    maker = kw["maker_name"]
    domain = kw["official_domain"]
    brands = kw["brand_names"]
    source_site = kw["source_site"]

    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    # 優先度1: タイトル × メーカー の複合 SNS（最も具体的）
    if title and maker:
        for kwd in ("Instagram", "Facebook", "LinkedIn"):
            add(f'"{title}" "{maker}" {kwd}')

    # 優先度2: タイトルの SNS / 公式
    if title:
        for kwd in ("Instagram", "Facebook", "LinkedIn", "official Instagram",
                    "official Facebook", "official", "brand"):
            add(f'"{title}" {kwd}')

    # 優先度3: メーカー名の SNS / 連絡先
    if maker:
        for kwd in ("Instagram", "Facebook", "LinkedIn", "official", "contact"):
            add(f'"{maker}" {kwd}')

    # 優先度4: site: 限定（プロフィール/企業ページを直接狙う）
    if title:
        add(f'site:instagram.com "{title}"')
        add(f'site:facebook.com "{title}"')
        add(f'site:youtube.com "{title}"')
        add(f'site:tiktok.com "{title}"')
    if maker:
        add(f'site:linkedin.com/company "{maker}"')
        add(f'site:linkedin.com/in "{maker}"')
        add(f'site:instagram.com "{maker}"')
        add(f'site:facebook.com "{maker}"')

    # 優先度5: 短縮タイトル・ブランド名の SNS（副題で埋もれた本来名で探す）
    if short:
        for kwd in ("Instagram", "Facebook", "official website"):
            add(f'"{short}" {kwd}')
    for b in brands:
        add(f'"{b}" Instagram')
        add(f'"{b}" official')

    # 優先度6: 公式サイト探索
    if title:
        add(f'"{title}" official website')
        add(f'"{title}" brand official')
    if maker:
        add(f'"{maker}" official website')
    if source_site and title:
        add(f'"{source_site}" "{title}" official')

    # 優先度7: 問い合わせ探索（メールは最後でよい＝要件 9）
    if title:
        for kwd in ("contact", "email", "support", "partnership", "distributor"):
            add(f'"{title}" {kwd}')
    if maker:
        for kwd in ("contact", "email", "partnership", "wholesale", "distributor"):
            add(f'"{maker}" {kwd}')

    # 優先度8: ドメイン site: 限定（メール/PDF）
    if domain:
        add(f"site:{domain} contact")
        add(f"site:{domain} email")
        add(f"site:{domain} partnership")
        add(f"site:{domain} wholesale")
        add(f"site:{domain} distributor")
        add(f"site:{domain} filetype:pdf")
        add(f"site:{domain} distributor filetype:pdf")

    return queries


# ---------------- 検索結果パース ----------------
# DuckDuckGo HTML 版（https://html.duckduckgo.com/html/）の結果リンクは
# //duckduckgo.com/l/?uddg=<encoded-url>&... 形式のリダイレクトになっている。
_DDG_UDDG_RE = re.compile(r"uddg=([^&\"'>]+)")
_DDG_RESULT_A_RE = re.compile(
    r'class="result__a"[^>]*href="(https?://[^"]+)"', re.IGNORECASE
)
# 結果ブロック（タイトル/スニペットを拾えるとスコアリング精度が上がる）。
_DDG_RESULT_BLOCK_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'(?:.*?class="result__snippet"[^>]*>(.*?)</a>)?',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", s or "")).strip()


def _decode_ddg_href(href: str) -> str | None:
    """DDG のリダイレクト href から実 URL を取り出す。"""
    href = (href or "").strip()
    m = _DDG_UDDG_RE.search(href)
    if m:
        try:
            return unquote(m.group(1))
        except Exception:  # noqa: BLE001
            return None
    if href.startswith(("http://", "https://")):
        return href
    return None


def parse_duckduckgo_detailed(html: str) -> list[dict]:
    """DDG HTML から {url,title,snippet} を抽出する（重複排除・エンジン自身は除外）。"""
    out: list[dict] = []
    seen: set[str] = set()

    def keep(url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return False
        host = urlparse(url).netloc.lower()
        if any(h in host for h in _SEARCH_ENGINE_HOSTS):
            return False
        return url not in seen

    for href, title, snippet in _DDG_RESULT_BLOCK_RE.findall(html or ""):
        url = _decode_ddg_href(href)
        if not url or not keep(url):
            continue
        seen.add(url)
        out.append(
            {"url": url, "title": _strip_tags(title), "snippet": _strip_tags(snippet)}
        )
    # ブロック正規表現が外れても URL だけは拾う（後方互換・堅牢性）
    for enc in _DDG_UDDG_RE.findall(html or ""):
        try:
            url = unquote(enc)
        except Exception:  # noqa: BLE001
            continue
        if keep(url):
            seen.add(url)
            out.append({"url": url, "title": "", "snippet": ""})
    return out


def parse_duckduckgo_results(html: str) -> list[str]:
    """DuckDuckGo HTML 検索結果から上位の外部 URL を抽出する（重複排除）。"""
    return [r["url"] for r in parse_duckduckgo_detailed(html)]


def _default_search_fn():
    """DuckDuckGo HTML を使った検索関数を返す（query -> [{url,title,snippet}]）。"""
    from app.scrapers.http import HttpClient

    client = HttpClient(
        rate_limit_seconds=RATE_LIMIT_SECONDS, timeout=SEARCH_TIMEOUT, retries=0
    )

    def search(query: str) -> list[dict]:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            html = client.get_text(url)
        except Exception as exc:  # noqa: BLE001  検索失敗は graceful に空で返す
            logger.info("web search failed (%s): %s", query, exc)
            return []
        return parse_duckduckgo_detailed(html)

    search._client = client  # type: ignore[attr-defined]
    return search


# ---------------- SNS 正規化（要件 5） ----------------
def normalize_instagram(url: str) -> str | None:
    """Instagram のプロフィール URL を https://www.instagram.com/{handle}/ に正規化。

    /p/ /reel/ /explore/ /accounts/login 等は本人プロフィールではないため None。
    """
    p = urlparse(url)
    if "instagram.com" not in p.netloc.lower():
        return None
    seg = [s for s in p.path.split("/") if s]
    if not seg:
        return None
    first = seg[0].lower()
    if first in (
        "p", "reel", "reels", "explore", "accounts", "stories", "tv",
        "about", "directory", "developer", "legal", "privacy",
    ):
        return None
    handle = seg[0]
    if not re.fullmatch(r"[A-Za-z0-9_.]+", handle):
        return None
    return f"https://www.instagram.com/{handle}/"


def normalize_facebook(url: str) -> str | None:
    """Facebook のページ URL を正規化する。/share /login /search /groups 等は除外。"""
    p = urlparse(url)
    if "facebook.com" not in p.netloc.lower():
        return None
    seg = [s for s in p.path.split("/") if s]
    if not seg:
        return None
    first = seg[0].lower()
    if first in (
        "share", "sharer", "sharer.php", "login", "search", "groups", "watch",
        "marketplace", "help", "policies", "privacy", "terms", "events",
        "l.php", "tr", "dialog", "plugins", "story.php", "permalink.php",
    ):
        return None
    if first == "profile.php":
        pid = parse_qs(p.query).get("id", [""])[0]
        return f"https://www.facebook.com/profile.php?id={pid}" if pid else None
    if first == "pages" and len(seg) >= 2:
        return "https://www.facebook.com/" + "/".join(seg[:3])
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", seg[0]):
        return None
    return f"https://www.facebook.com/{seg[0]}"


def normalize_linkedin(url: str) -> str | None:
    """LinkedIn の /company/ と /in/ のみ採用。/login /feed /search 等は除外。"""
    p = urlparse(url)
    if "linkedin.com" not in p.netloc.lower():
        return None
    seg = [s for s in p.path.split("/") if s]
    if len(seg) < 2:
        return None
    kind = seg[0].lower()
    if kind in ("company", "school", "showcase"):
        return f"https://www.linkedin.com/company/{seg[1]}/"
    if kind == "in":
        return f"https://www.linkedin.com/in/{seg[1]}/"
    return None


_NORMALIZERS = {
    "instagram": normalize_instagram,
    "facebook": normalize_facebook,
    "linkedin": normalize_linkedin,
}


def _normalize_social(platform: str, url: str) -> str | None:
    """プラットフォーム別に SNS URL を正規化する（twitter/youtube は素通し）。"""
    fn = _NORMALIZERS.get(platform)
    if fn is not None:
        return fn(url)
    if cds._SOCIAL_EXCLUDE.search(url):
        return None
    return url


def _social_handle(platform: str, url: str) -> str | None:
    """正規化後の SNS URL から照合用ハンドル（小文字）を取り出す。"""
    norm = _normalize_social(platform, url)
    if not norm:
        return None
    seg = [s for s in urlparse(norm).path.split("/") if s]
    if not seg:
        return None
    if platform == "linkedin":
        return seg[1].lower() if len(seg) > 1 else None
    return seg[0].lower()


def _is_platform_social_handle(platform: str, url: str) -> bool:
    """クラファン運営（platform）自身の公式 SNS かどうか（要件 4 の誤採用防止）。"""
    handle = _social_handle(platform, url)
    return bool(handle and handle in _PLATFORM_SOCIAL_HANDLES)


# ---------------- URL 分類・スコアリング ----------------
def _is_skip_url(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in _SKIP_URL_HINTS)


def _is_pdf_url(url: str) -> bool:
    return ".pdf" in url.lower()


def _social_platform(url: str) -> str | None:
    if cds._SOCIAL_EXCLUDE.search(url):
        return None
    for platform, pat in cds.SOCIAL_PATTERNS.items():
        if pat.search(url):
            return platform
    return None


def _is_platform_domain(url: str) -> bool:
    host = cds._domain_of(url)
    return any(cds._domain_matches(host, d) for d in cds.PLATFORM_EMAIL_DOMAINS)


def _terms(*texts: str) -> set[str]:
    """検索語照合に使う有意トークン集合（3 文字以上・ストップワード除外）。"""
    terms: set[str] = set()
    for t in texts:
        for tok in re.findall(r"[a-z0-9]+", (t or "").lower()):
            if len(tok) >= 3 and tok not in _STOPWORDS:
                terms.add(tok)
    return terms


def score_search_result(
    url: str,
    title: str,
    snippet: str,
    *,
    project_terms: set[str],
    maker_terms: set[str],
    official_domain: str | None,
) -> tuple[int, str]:
    """検索結果 URL をスコアリングし、(score, reason) を返す（要件 4）。

    score < 0 は除外（reason に理由）。0 以上は採用候補（reason に採用理由）。
    """
    low_url = url.lower()
    plat = _social_platform(url)

    # --- 除外（低評価） ---
    if _RESULT_EXCLUDE_RE.search(low_url):
        return -1, "excluded:share/login/search/hashtag等のURL"
    if _is_platform_domain(url):
        return -1, "excluded:クラファン運営ドメイン"
    if plat and _is_platform_social_handle(plat, url):
        return -1, f"excluded:運営自身の公式{plat}"

    # --- 加点 ---
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    url_blob = host + " " + path.replace("/", " ").replace("-", " ").replace("_", " ")
    text_blob = f"{title} {snippet}".lower()

    score = 0
    reasons: list[str] = []

    if project_terms and any(t in url_blob for t in project_terms):
        score += 30
        reasons.append("URLにタイトル主要語")
    if maker_terms and any(t in url_blob for t in maker_terms):
        score += 25
        reasons.append("URLにメーカー名")

    has_proj_text = bool(project_terms) and any(t in text_blob for t in project_terms)
    has_maker_text = bool(maker_terms) and any(t in text_blob for t in maker_terms)
    if has_proj_text and has_maker_text:
        score += 25
        reasons.append("タイトル＋メーカー名が本文に")
    elif has_proj_text or has_maker_text:
        score += 10
        reasons.append("関連語が本文に")

    if official_domain and cds._same_domain(url, official_domain):
        score += 30
        reasons.append("公式ドメイン一致")

    if plat == "instagram" and normalize_instagram(url):
        score += 35
        reasons.append("Instagramプロフィール")
    elif plat == "facebook" and normalize_facebook(url):
        score += 30
        reasons.append("Facebookページ")
    elif plat == "linkedin" and normalize_linkedin(url):
        score += 35
        reasons.append("LinkedIn企業/個人ページ")

    if not reasons:
        return 0, "弱い一致（採用しない）"
    return score, ", ".join(reasons)


def _page_type(url: str, official_domain: str | None) -> str:
    """候補ページの種別を URL から推定する（UI 表示用）。"""
    path = urlparse(url).path.lower()
    if official_domain and cds._same_domain(url, official_domain):
        if path in ("", "/"):
            return "official_site"
    if cds._matches_hints(url, cds.PRESS_HINTS):
        return "press"
    if cds._matches_hints(url, cds.WHOLESALE_HINTS):
        return "wholesale"
    if cds._is_contact_url(url):
        return "contact"
    if "about" in path:
        return "about"
    if official_domain and cds._same_domain(url, official_domain):
        return "official_site"
    return "search_result"


# ---------------- 探索本体 ----------------
def _seed_and_known_urls(project: Project, research: CompanyResearch | None) -> list[str]:
    """公式サイト・案件ページ・company_research と公式サイト代表パスを集める。"""
    official = project.maker_url or ""
    seeds: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if u and u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            seeds.append(u)

    add(project.maker_url)
    add(project.source_url)
    if research and research.sources:
        for s in research.sources:
            if isinstance(s, str):
                add(s)
    if official:
        p = urlparse(official)
        root = f"{p.scheme}://{p.netloc}"
        for path in WEB_KNOWN_PATHS:
            add(root + path)
    return seeds


# 公式サイトとは見なさないホスト（マーケットプレイス/SNS/集約/ニュース等）。
# maker_url 未登録時に検索結果から公式ドメインを推定する際、ここに該当する
# ドメインは除外する。
_NON_OFFICIAL_HOST_HINTS = (
    "amazon.", "ebay.", "etsy.", "aliexpress.", "walmart.",
    "youtube.", "youtu.be", "vimeo.",
    "facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "tiktok.",
    "pinterest.", "reddit.", "medium.com", "substack.com", "linktr.ee",
    "wikipedia.", "blogspot.", "wordpress.com", "notion.so", "notion.site",
    "kickstarter.", "indiegogo.", "ulule.", "makuake.", "greenfunding.",
    "wadiz.", "gofundme.", "patreon.", "crunchbase.",
    "news", "press", "magazine", "review", "blog.",
)


def _infer_official_url(
    page_candidates: list[tuple[int, str]],
    project: Project,
    project_terms: set[str],
    maker_terms: set[str],
    official_domain: str,
) -> str:
    """maker_url 未登録でも検索結果ページ群から公式サイト URL を推定する。

    既に maker_url（official_domain）がある場合はそれを返す。無い場合は、検索結果
    のうちマーケットプレイス/SNS/ニュース等でなく、ドメイン名がメーカー名/タイトル
    主要語を含むページを公式サイトとみなす（スコア降順）。該当が無ければ "" を返す。
    """
    if official_domain:
        return project.maker_url or ""
    terms = (maker_terms | project_terms) or set()
    for _score, url in sorted(page_candidates, key=lambda t: t[0], reverse=True):
        host = urlparse(url).netloc.lower()
        if any(h in host for h in _NON_OFFICIAL_HOST_HINTS):
            continue
        domain_token = cds._domain_of(url).split(".")[0]
        if not domain_token:
            continue
        if any(t in domain_token or domain_token in t for t in terms):
            p = urlparse(url)
            return f"{p.scheme}://{p.netloc}"
    return ""


def _as_result(item) -> dict | None:
    """search_fn の戻り値（str か dict）を {url,title,snippet} に正規化する。"""
    if isinstance(item, str):
        url = item.strip()
        return {"url": url, "title": "", "snippet": ""} if url else None
    if isinstance(item, dict):
        url = str(item.get("url") or "").strip()
        if not url:
            return None
        return {
            "url": url,
            "title": str(item.get("title") or ""),
            "snippet": str(item.get("snippet") or ""),
        }
    return None


def web_research(
    project: Project,
    research: CompanyResearch | None = None,
    *,
    fetch_fn=None,
    search_fn=None,
) -> dict:
    """Web リサーチ本体（DB 非依存）。集計した結果 dict を返す。

    fetch_fn(url)->html|None, search_fn(query)->[url]|[{url,title,snippet}] を注入
    できる（テスト用）。未指定なら DuckDuckGo HTML 検索 + 既存 HTTP 基盤を使う。
    """
    official = project.maker_url or ""
    official_domain = cds._domain_of(official)
    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))

    keywords = build_keyword_candidates(project, research)
    project_terms = _terms(keywords["project_title"], keywords["short_title"])
    maker_terms = _terms(
        keywords["maker_name"], keywords["domain_name"], *keywords["brand_names"]
    )

    own_fetcher = fetch_fn is None
    own_search = search_fn is None
    fetch = fetch_fn or _make_fetcher()
    if own_search:
        from app.services import search_providers

        search = search_providers.get_search_fn()
        provider = getattr(search, "provider", "duckduckgo")
    else:
        search = search_fn
        provider = getattr(search_fn, "provider", "injected")

    generated_queries = build_web_search_queries(project, research)
    searched_queries: list[str] = []
    search_failures = 0

    # 1. 検索クエリを実行 → 候補をスコアリングして採用/除外を判定（要件 3・4）
    search_records: list[dict] = []          # デバッグ保存用（採用/除外理由つき）
    socials: dict[str, str] = {}             # platform -> 正規化済み URL
    social_debug: dict[str, dict] = {}       # platform -> {url, score, source}
    pdfs: list[dict] = []
    pdf_seen: set[str] = set()
    page_candidates: list[tuple[int, str]] = []   # (score, url) クロール対象候補
    seen_results: set[str] = set()

    def consider_social(platform: str, raw_url: str, score: int, source: str) -> bool:
        norm = _normalize_social(platform, raw_url)
        if not norm:
            return False
        if _is_platform_social_handle(platform, norm):
            return False
        cur = social_debug.get(platform)
        if cur is None or score > cur["score"]:
            socials[platform] = norm
            social_debug[platform] = {"url": norm, "score": score, "source": source}
        return True

    try:
        for q in generated_queries[:MAX_QUERIES]:
            searched_queries.append(q)
            try:
                raw = search(q) or []
            except Exception as exc:  # noqa: BLE001  個別失敗は無視
                logger.info("search error (%s): %s", q, exc)
                raw = []
            if not raw:
                search_failures += 1
            taken = 0
            for item in raw:
                if taken >= MAX_RESULTS_PER_QUERY:
                    break
                rec = _as_result(item)
                if rec is None or rec["url"] in seen_results:
                    continue
                seen_results.add(rec["url"])
                taken += 1
                url = rec["url"]
                score, reason = score_search_result(
                    url, rec["title"], rec["snippet"],
                    project_terms=project_terms, maker_terms=maker_terms,
                    official_domain=official_domain or None,
                )
                kind = "excluded"
                adopted = False
                platform = _social_platform(url)
                if score < 0:
                    kind = "excluded"
                elif platform:
                    kind = "social"
                    if score >= SOCIAL_ADOPT_MIN_SCORE:
                        adopted = consider_social(
                            platform, url, score, f"search:{q}"
                        )
                    if not adopted:
                        reason = reason + "（スコア不足/正規化不可で不採用）" \
                            if score >= 0 else reason
                elif _is_pdf_url(url):
                    kind = "pdf"
                    if url not in pdf_seen:
                        pdf_seen.add(url)
                        name = urlparse(url).path.rsplit("/", 1)[-1] or "PDF"
                        pdfs.append({"url": url, "label": name, "relevant": True})
                        adopted = True
                else:
                    kind = "page"
                    host = urlparse(url).netloc.lower()
                    non_official = any(h in host for h in _NON_OFFICIAL_HOST_HINTS)
                    if _is_skip_url(url):
                        pass
                    elif non_official:
                        # マーケットプレイス/SNS/ニュース等は巡回せず結果として記録のみ
                        reason = reason + "（非公式ホストのため巡回対象外）"
                    else:
                        page_candidates.append((score, url))
                        adopted = True
                if len(search_records) < MAX_SEARCH_RESULTS_SAVED:
                    search_records.append({
                        "query": q,
                        "url": url,
                        "title": rec["title"][:200] or None,
                        "score": score,
                        "kind": kind,
                        "adopted": adopted,
                        "reason": reason,
                    })
    finally:
        if own_search:
            client = getattr(search, "_client", None)
            if client is not None:
                client.close()

    # 検索フェーズのサマリをログ（要件 1・2・3）
    excluded_count = sum(1 for r in search_records if r["kind"] == "excluded")
    logger.info(
        "web_research[%s] provider=%s: ran %d/%d queries, %d results "
        "(pages=%d, socials=%d, pdfs=%d, excluded=%d)",
        getattr(project, "id", "?"), provider, len(searched_queries),
        len(generated_queries), len(search_records), len(page_candidates),
        len(socials), len(pdfs), excluded_count,
    )
    for kind in ("page", "social", "pdf"):
        for r in search_records:
            if r["kind"] == kind and r["adopted"]:
                logger.info("web_research[%s] adopted %s: %s (score=%s)",
                            getattr(project, "id", "?"), kind, r["url"], r["score"])

    # 2. 公式サイトを確定（maker_url 未登録でも検索結果から推定）。代表パスを展開して
    #    クロール深度を確保する（要件 4：最低 10〜20 ページ）。
    inferred_official = _infer_official_url(
        page_candidates, project, project_terms, maker_terms, official_domain
    )
    effective_official = official or inferred_official
    effective_domain = official_domain or cds._domain_of(inferred_official)
    if inferred_official and not official:
        logger.info(
            "web_research[%s] inferred official site from search: %s",
            getattr(project, "id", "?"), inferred_official,
        )

    # 3. クロール対象 URL を決める（公式サイト＋代表パス優先 → 検索結果ページ）
    crawl_urls: list[str] = []
    crawl_seen: set[str] = set()

    def add_crawl(u: str) -> None:
        if len(crawl_urls) >= MAX_URLS or u in crawl_seen:
            return
        if not u.startswith(("http://", "https://")):
            return
        if _is_skip_url(u):
            return
        if _is_platform_domain(u) and u != (project.source_url or ""):
            return
        crawl_seen.add(u)
        crawl_urls.append(u)

    # 公式サイト・案件ページ・company_research の出典
    for u in _seed_and_known_urls(project, research):
        add_crawl(u)
    # 確定/推定した公式ドメインに代表パス（Contact/About/Press/Wholesale 等）を展開
    if effective_official:
        p = urlparse(effective_official)
        root = f"{p.scheme}://{p.netloc}"
        add_crawl(root)
        for path in WEB_KNOWN_PATHS:
            add_crawl(root + path)
    # 検索結果ページ（スコア順）
    for _score, u in sorted(page_candidates, key=lambda t: t[0], reverse=True):
        add_crawl(u)

    logger.info(
        "web_research[%s] crawl plan: %d url(s) (official=%s)",
        getattr(project, "id", "?"), len(crawl_urls), effective_domain or "-",
    )

    # 4. クロールして抽出
    searched: list[str] = []
    candidate_pages: list[dict] = []
    email_map: dict[str, dict] = {}
    forms: list[str] = []
    ok_count = 0
    fail_count = 0
    email_pages_count = 0

    try:
        for url in crawl_urls:
            if len(searched) >= MAX_URLS:
                break
            html = fetch(url)
            searched.append(url)
            page = {"url": url, "type": _page_type(url, effective_domain), "ok": bool(html), "emails": 0}
            candidate_pages.append(page)
            if not html:
                fail_count += 1
                logger.info("web_research[%s] fetch FAIL: %s",
                            getattr(project, "id", "?"), url)
                continue
            ok_count += 1

            # メール（既存フィルタを必ず通す。出典 URL を付与）
            page_emails = 0
            for addr in cds.extract_emails(html, site_domain):
                page_emails += 1
                score, tier = cds.score_email(addr, effective_domain)
                owner = cds.classify_email_owner(addr, effective_domain, site_domain)
                key = addr.lower()
                rec = email_map.get(key)
                if rec is None:
                    email_map[key] = {
                        "email": addr,
                        "score": score,
                        "tier": tier,
                        "email_owner": owner,
                        "sources": [url],
                    }
                else:
                    if url not in rec["sources"]:
                        rec["sources"].append(url)
                    if score > rec["score"]:
                        rec["score"], rec["tier"] = score, tier
            page["emails"] = page_emails
            if page_emails:
                email_pages_count += 1
            logger.info(
                "web_research[%s] fetch ok: %s (%d chars, %d email(s))",
                getattr(project, "id", "?"), url, len(html), page_emails,
            )

            # SNS（ページ内リンク。正規化 + 運営 SNS 除外）
            for platform, link in cds.extract_socials(html, url).items():
                consider_social(platform, link, 20, f"page:{url}")

            # 問い合わせフォーム
            if cds._is_contact_url(url) and url not in forms:
                forms.append(url)
            for link in cds.extract_links(html, url):
                if effective_domain and cds._same_domain(link, effective_domain):
                    if cds._is_contact_url(link) and link not in forms:
                        forms.append(link)

            # PDF
            for p in cds.extract_pdf_links(html, url):
                if p["url"] not in pdf_seen:
                    pdf_seen.add(p["url"])
                    pdfs.append(p)
    finally:
        if own_fetcher:
            client = getattr(fetch, "_client", None)
            if client is not None:
                client.close()

    logger.info(
        "web_research[%s] done: crawled=%d ok=%d fail=%d emails=%d socials=%d forms=%d",
        getattr(project, "id", "?"), len(searched), ok_count, fail_count,
        len(email_map), len(socials), len(forms),
    )

    pdfs = pdfs[:8]
    # 運営会社（platform）のメールは営業候補に含めない
    emails = sorted(
        (e for e in email_map.values() if e["email_owner"] != "platform"),
        key=lambda e: e["score"],
        reverse=True,
    )
    primary_email = emails[0]["email"] if emails else None
    primary_form = forms[0] if forms else None
    has_official_site = bool(effective_official)

    score = cds.contactability_score(
        emails,
        has_form=bool(forms),
        socials=socials,
        has_official_site=has_official_site,
    )
    channel = cds.recommend_channel(
        emails,
        has_form=bool(forms),
        socials=socials,
        press_page=next(
            (p["url"] for p in candidate_pages if p["type"] == "press"), None
        ),
        wholesale_page=next(
            (p["url"] for p in candidate_pages if p["type"] == "wholesale"), None
        ),
    )
    evidence = cds.build_evidence_summary(emails, forms, socials, "")

    debug_counts = {
        "queries": len(searched_queries),
        "results": len(search_records),
        "crawled": len(searched),
        "ok": ok_count,
        "failed": fail_count,
        "excluded": excluded_count,
        "email_pages": email_pages_count,
    }

    # 探索フローの要約（要件 5）。UI とログで「どこまで進んだか」が分かる。
    flow_bits = [f"{provider}検索"]
    flow_bits.append(f"{len(search_records)}件取得")
    if effective_official:
        flow_bits.append(f"公式サイト({effective_domain})")
    if any(p["type"] == "contact" for p in candidate_pages):
        flow_bits.append("Contact")
    if any(p["type"] == "about" for p in candidate_pages):
        flow_bits.append("About")
    for plat in ("instagram", "facebook", "linkedin"):
        if socials.get(plat):
            flow_bits.append(plat.capitalize())
    if pdfs:
        flow_bits.append(f"PDF{len(pdfs)}件")
    flow_bits.append(f"メール{len(emails)}件抽出")
    flow_bits.append("終了")
    research_flow = " → ".join(flow_bits)
    logger.info("web_research[%s] flow: %s", getattr(project, "id", "?"), research_flow)

    notes_bits = [
        f"provider {provider}",
        f"{len(searched_queries)}/{len(generated_queries)} query(ies) run",
        f"{len(search_records)} result(s)",
        f"crawled {len(searched)} (ok {ok_count}/fail {fail_count})",
        f"{len(emails)} email(s)",
        f"{len(socials)} social(s)",
        f"score {score}",
    ]
    if search_failures:
        notes_bits.append(
            f"{search_failures} search(es) returned no results "
            "(engine may be blocking or rate-limiting)"
        )
    if not any(r["kind"] != "excluded" for r in search_records):
        notes_bits.append(
            "no search-engine results were usable; relied on official-site crawl"
        )

    return {
        "search_provider": provider,
        "keyword_candidates": keywords,
        "generated_queries": generated_queries,
        "searched_queries": searched_queries,
        "search_results": search_records,
        "searched_urls": searched,
        "candidate_pages": candidate_pages,
        "discovered_emails": emails,
        "discovered_forms": forms,
        "discovered_socials": socials,
        "discovered_pdfs": pdfs,
        "primary_email": primary_email,
        "primary_contact_form_url": primary_form,
        "recommended_channel": channel,
        "confidence_score": score,
        "evidence_summary": evidence,
        "debug_counts": debug_counts,
        "research_flow": research_flow,
        "notes": ", ".join(notes_bits),
    }


def _make_fetcher():
    """既存 HTTP 基盤を使った取得関数（url -> html|None）。"""
    from app.scrapers.http import HttpClient

    client = HttpClient(
        rate_limit_seconds=RATE_LIMIT_SECONDS, timeout=FETCH_TIMEOUT, retries=0
    )

    def fetch(url: str) -> str | None:
        try:
            return client.get_text(url)
        except Exception as exc:  # noqa: BLE001  1 URL 失敗は無視
            logger.info("fetch failed (%s): %s", url, exc)
            return None

    fetch._client = client  # type: ignore[attr-defined]
    return fetch


# ---------------- DB 連携 ----------------
def run_web_research(
    db: Session, project: Project, *, fetch_fn=None, search_fn=None
) -> ContactDiscovery:
    """Web リサーチを実行し、最新の探索結果（ContactDiscovery）の web_* に保存する。

    既存の探索結果が無ければ先に自動探索を実行して土台を作る。Web 結果は web_*
    カラムに分離保存し、自動抽出（primary_email 等）/ AI 調査（ai_*）は上書きしない。
    失敗時は web_research_error に記録し、アプリは落とさない。
    """
    research = cds._latest_research(db, project.id)
    row = cds.get_latest(db, project.id)
    if row is None:
        row = cds.run_discovery(db, project)

    now = datetime.now(timezone.utc)
    try:
        result = web_research(project, research, fetch_fn=fetch_fn, search_fn=search_fn)
        row.web_researched = True
        row.web_researched_at = now
        row.web_search_provider = result["search_provider"]
        row.web_debug_counts = result["debug_counts"] or None
        row.web_research_flow = result["research_flow"] or None
        row.web_keyword_candidates = result["keyword_candidates"] or None
        row.web_generated_queries = result["generated_queries"] or None
        row.web_searched_queries = result["searched_queries"] or None
        row.web_search_results = result["search_results"] or None
        row.web_searched_urls = result["searched_urls"] or None
        row.web_candidate_pages = result["candidate_pages"] or None
        row.web_discovered_emails = result["discovered_emails"] or None
        row.web_discovered_forms = result["discovered_forms"] or None
        row.web_discovered_socials = result["discovered_socials"] or None
        row.web_discovered_pdfs = result["discovered_pdfs"] or None
        row.web_primary_email = result["primary_email"]
        row.web_primary_contact_form_url = result["primary_contact_form_url"]
        row.web_recommended_channel = result["recommended_channel"]
        row.web_confidence_score = result["confidence_score"]
        row.web_evidence_summary = result["evidence_summary"]
        row.web_notes = result["notes"]
        row.web_research_error = None
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("web research failed (project=%s): %s", project.id, exc)
        row.web_researched = True
        row.web_researched_at = now
        row.web_research_error = str(exc)[:4000]

    db.commit()
    db.refresh(row)
    return row
