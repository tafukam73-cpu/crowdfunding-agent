"""Contact Intelligence v3：公式サイト内の再帰クロール（発見率強化）。

AI を増やすのではなく「公式サイト内の収集力」を強化する。公式サイトが見つかったら、
Contact/About だけでなくサイト全体を安全に再帰クロールして、メール・フォーム・
SNS・PDF・営業窓口を抽出する。加えて sitemap.xml / robots.txt を読み、DNS(MX/SPF/
DMARC) を確認して「メール運用の有無」を把握する。

設計方針（既存レイヤーと同じ）：
- メールは推測で作らない。すべて実際に取得したページ本文/mailto/PDF から抽出し、
  contact_discovery_service の除外フィルタ（platform / sentry / no-reply / hash 等）を
  必ず通す。各メールは出典 URL（sources）を持つ。
- 安全設計：URL 数・深さ・1URL タイムアウト・同一ドメイン優先・login/cart/checkout/
  account/admin スキップ・robots の Disallow を強引に破らない。失敗してもアプリは
  落とさない。
- ネットワークは fetch_fn（url->html|None）/ resolve_fn（(name,rtype)->[str]）として
  注入でき、テストはネットワーク無しで検証できる。
- 1URL 巡回ごと / 各フェーズで progress_cb にログを流し、じっくり調査へ進捗を出す。

結果は最新の ContactDiscovery 行の recursive_* カラムに分離保存する。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from sqlalchemy.orm import Session

from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds
from app.services import web_research_service as wrs

logger = logging.getLogger("recursive_crawl")

# --- 安全設計のパラメータ（.env で上書き可能。既定 50 URL / 深さ 2） ---
DEFAULT_MAX_URLS = 50
DEFAULT_MAX_DEPTH = 2
FETCH_TIMEOUT = 12.0        # 1 URL のタイムアウト（秒）
RATE_LIMIT_SECONDS = 1.0    # ページ間隔（過度なアクセスを避ける）
MAX_PDF_PARSE = 6           # 本文解析する PDF の上限
MAX_SITEMAP_URLS = 40       # sitemap から取り込む優先 URL の上限

# 優先巡回パス（要件 2）。sitemap.xml / robots.txt は HTML クロールせず別処理。
PRIORITY_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/press",
    "/media",
    "/wholesale",
    "/distributor",
    "/distributors",
    "/partners",
    "/partnership",
    "/affiliate",
    "/support",
    "/faq",
    "/privacy",
    "/privacy-policy",
    "/terms",
    "/legal",
    "/careers",
    "/jobs",
    "/pages/contact",
    "/pages/about",
]

# ページ内リンク再帰で優先するアンカー語（要件 3）。
LINK_PRIORITY_KEYWORDS = (
    "contact", "about", "team", "press", "media", "wholesale", "distributor",
    "partner", "privacy", "terms", "legal", "careers", "jobs", "faq", "support",
    "pdf", "catalog", "brochure", "manual", "pitch", "investor", "press-kit",
    "media-kit",
)

# sitemap / PDF で優先する URL 語（要件 4）。
SITEMAP_PRIORITY_KEYWORDS = (
    "contact", "about", "press", "privacy", "terms", ".pdf", "media",
    "wholesale", "distributor", "team", "support",
)

# 入ってはいけない / 営業に無関係なパス語（要件 1）。login/cart/checkout/account/admin。
SKIP_URL_HINTS = (
    "login", "signin", "sign-in", "sign_in", "signup", "sign-up",
    "/cart", "/checkout", "/account", "/admin", "wp-admin", "wp-login",
    "/register", "/my-account", "/basket", "/bag", "/wishlist", "/logout",
)

# 例外的に候補化する外部サービス（要件 1：PDF / Linktree / SNS）。
_LINK_AGGREGATOR_HOSTS = (
    "linktr.ee", "linktree.com", "beacons.ai", "carrd.co", "lnk.bio",
    "campsite.bio", "bio.link", "linkin.bio", "linkpop.com", "solo.to",
)

# 失敗理由コード（要件 8）。保存・表示に使う語彙。
FAILURE_CODES = (
    "OFFICIAL_SITE_NOT_FOUND",
    "OFFICIAL_SITE_NOT_REGISTERED",
    "SEARCH_PROVIDER_FAILED",
    "SEARCH_PROVIDER_NO_RESULTS",
    "CRAWL_BLOCKED",
    "CONTACT_FORM_ONLY",
    "EMAIL_NOT_PUBLIC",
    "SOCIAL_ONLY",
    "PDF_NO_EMAIL",
    "DNS_MX_FOUND_EMAIL_NOT_PUBLIC",
    "LOGIN_REQUIRED",
    "TIMEOUT",
    "RATE_LIMITED",
)


# ---------------- URL 判定 ----------------
def _is_skip_url(url: str) -> bool:
    low = url.lower()
    return any(h in low for h in SKIP_URL_HINTS)


def _is_pdf_url(url: str) -> bool:
    return ".pdf" in url.lower()


def _is_aggregator(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(h in host for h in _LINK_AGGREGATOR_HOSTS)


def _link_priority(url: str) -> int:
    """アンカー URL の巡回優先度（大きいほど先に巡回）。要件 3 の語で加点。"""
    low = url.lower()
    return sum(1 for k in LINK_PRIORITY_KEYWORDS if k in low)


# ---------------- robots.txt / sitemap.xml ----------------
def parse_robots(text: str) -> dict:
    """robots.txt をパースし、Sitemap 行と User-agent:* の Disallow を返す。

    Returns: {sitemaps:[url], disallows:[path]}
    Disallow は「強引に破らない」ために巡回除外に使う（要件 5）。
    """
    sitemaps: list[str] = []
    disallows: list[str] = []
    active = False
    # 一部の取得基盤（Playwright）は robots.txt を HTML で包む（<pre>…</pre>）。
    # 各行から HTML タグを除き、値は最初の空白/山括弧までを採用して汚染を防ぐ。
    for raw in (text or "").splitlines():
        line = re.sub(r"<[^>]+>", " ", raw).strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("sitemap:"):
            sm = line.split(":", 1)[1].strip().split()[0] if line.split(":", 1)[1].strip() else ""
            if sm and sm not in sitemaps:
                sitemaps.append(sm)
        elif low.startswith("user-agent:"):
            active = low.split(":", 1)[1].strip() == "*"
        elif active and low.startswith("disallow:"):
            path = line.split(":", 1)[1].strip().split()[0] if line.split(":", 1)[1].strip() else ""
            if path:
                disallows.append(path)
    return {"sitemaps": sitemaps, "disallows": disallows}


_SITEMAP_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


def parse_sitemap_locs(xml: str) -> list[str]:
    """sitemap.xml / sitemap index から <loc> URL を抽出する（重複排除）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _SITEMAP_LOC_RE.findall(xml or ""):
        u = m.strip()
        if u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _prioritize_sitemap_urls(urls: list[str], domain: str) -> list[str]:
    """sitemap URL のうち contact/about/press/privacy/terms/pdf を優先して返す。"""
    same = [u for u in urls if cds._same_domain(u, domain)] if domain else urls
    priority = [
        u for u in same
        if any(k in u.lower() for k in SITEMAP_PRIORITY_KEYWORDS)
    ]
    seen = set(priority)
    rest = [u for u in same if u not in seen]
    return (priority + rest)[:MAX_SITEMAP_URLS]


def read_sitemaps(
    fetch, root: str, robots_sitemaps: list[str], domain: str,
    *, max_docs: int = 4
) -> list[str]:
    """robots の Sitemap: と /sitemap.xml を読み、優先 URL 群を返す（要件 4）。

    sitemap index（sitemap の sitemap）も 1 段だけ辿る。取得失敗は無視。
    """
    candidates = list(dict.fromkeys(robots_sitemaps + [urljoin(root, "/sitemap.xml")]))
    collected: list[str] = []
    seen_docs: set[str] = set()
    docs_read = 0
    for sm in candidates:
        if docs_read >= max_docs or sm in seen_docs:
            continue
        seen_docs.add(sm)
        xml = fetch(sm)
        docs_read += 1
        if not xml:
            continue
        locs = parse_sitemap_locs(xml)
        # sitemap index（子 sitemap）を 1 段だけ展開
        child_maps = [u for u in locs if u.lower().endswith(".xml")]
        page_urls = [u for u in locs if not u.lower().endswith(".xml")]
        collected.extend(page_urls)
        for child in child_maps:
            if docs_read >= max_docs or child in seen_docs:
                continue
            seen_docs.add(child)
            cxml = fetch(child)
            docs_read += 1
            if cxml:
                collected.extend(
                    u for u in parse_sitemap_locs(cxml)
                    if not u.lower().endswith(".xml")
                )
    return _prioritize_sitemap_urls(list(dict.fromkeys(collected)), domain)


# ---------------- DNS / MX / SPF / DMARC（要件 7） ----------------
def _classify_mx_provider(exchanges: list[str]) -> str:
    """MX ホスト名からメール基盤プロバイダーを推定する。"""
    blob = " ".join(x.lower() for x in exchanges)
    if "google" in blob or "googlemail" in blob or "aspmx" in blob:
        return "Google Workspace"
    if "outlook" in blob or "microsoft" in blob or "protection.outlook" in blob:
        return "Microsoft 365"
    if "zoho" in blob:
        return "Zoho"
    if not exchanges:
        return ""
    return "Other"


def _default_resolve(name: str, rtype: str) -> list[str]:
    """dnspython で DNS を引く（未導入/失敗時は空）。resolve_fn 未注入時の既定。"""
    try:
        import dns.resolver  # 遅延 import（未導入なら空を返す）

        resolver = dns.resolver.Resolver()
        resolver.lifetime = 6.0
        resolver.timeout = 6.0
        answers = resolver.resolve(name, rtype)
        out: list[str] = []
        for a in answers:
            if rtype == "MX":
                out.append(str(getattr(a, "exchange", a)).rstrip("."))
            else:
                # TXT はクォート付きの chunk 集合になる
                txt = getattr(a, "strings", None)
                if txt is not None:
                    out.append(b"".join(txt).decode("utf-8", "ignore"))
                else:
                    out.append(str(a).strip('"'))
        return out
    except Exception:  # noqa: BLE001  未導入 / NXDOMAIN / タイムアウト等
        return []


def check_dns(domain: str, *, resolve_fn=None) -> dict:
    """公式ドメインの MX / SPF / DMARC を確認する（要件 7）。

    resolve_fn(name, rtype)->[str] を注入できる（テスト用）。失敗しても落とさない。
    Returns: {has_mx, mx_provider, spf_record, dmarc_record}
    """
    out = {"has_mx": None, "mx_provider": None, "spf_record": None, "dmarc_record": None}
    if not domain:
        return out
    resolve = resolve_fn or _default_resolve
    try:
        mx = resolve(domain, "MX")
        out["has_mx"] = bool(mx)
        out["mx_provider"] = _classify_mx_provider(mx) or None
    except Exception:  # noqa: BLE001
        pass
    try:
        for txt in resolve(domain, "TXT"):
            if txt.lower().startswith("v=spf1"):
                out["spf_record"] = txt[:500]
                break
    except Exception:  # noqa: BLE001
        pass
    try:
        for txt in resolve(f"_dmarc.{domain}", "TXT"):
            if txt.lower().startswith("v=dmarc1"):
                out["dmarc_record"] = txt[:500]
                break
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------- 取得関数 ----------------
def _make_fetcher():
    """取得関数（url -> html|None）。1URL タイムアウト 12 秒。

    web_research と同じ設定済み fetcher（既定 Playwright→httpx フォールバック）を使う。
    HTTP ステータスを記録し、429=RATE_LIMITED / タイムアウト=TIMEOUT の判定に使う。
    """
    from app.config import settings

    method = getattr(settings, "scrape_fetcher", "httpx") or "httpx"
    try:
        from app.scrapers.fetcher import get_fetcher

        client = get_fetcher(
            method, rate_limit_seconds=RATE_LIMIT_SECONDS,
            timeout=FETCH_TIMEOUT, retries=0,
        )
    except Exception as exc:  # noqa: BLE001  Playwright 未導入等は httpx に退避
        logger.warning("recursive_crawl fetcher init failed (%s); httpx にフォールバック", exc)
        from app.scrapers.http import HttpClient

        client = HttpClient(
            rate_limit_seconds=RATE_LIMIT_SECONDS, timeout=FETCH_TIMEOUT, retries=0
        )

    statuses: list[int] = []
    flags = {"timed_out": False}

    def fetch(url: str) -> str | None:
        try:
            html = client.get_text(url)
        except Exception as exc:  # noqa: BLE001  1 URL 失敗は無視
            status = getattr(client, "last_status", None)
            if status is not None:
                statuses.append(status)
            if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
                flags["timed_out"] = True
            logger.info("recursive_crawl fetch FAILED %s: status=%s err=%s",
                        url, status, exc)
            return None
        status = getattr(client, "last_status", None)
        if status is not None:
            statuses.append(status)
        return html

    fetch._client = client  # type: ignore[attr-defined]
    fetch.statuses = statuses  # type: ignore[attr-defined]
    fetch.flags = flags  # type: ignore[attr-defined]
    return fetch


# ---------------- 抽出（人物候補・会社名）：PDF/ページ本文から ----------------
_PERSON_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s[A-Z]\.)?\s[A-Z][a-z]+)\s*[,\-–—|]\s*"
    r"(CEO|CTO|COO|CMO|Founder|Co-Founder|Cofounder|President|Director|Manager|"
    r"Head of [A-Za-z ]+|VP [A-Za-z ]+|Owner|Partner)",
    re.IGNORECASE,
)


def extract_people(text: str, limit: int = 8) -> list[dict]:
    """本文から「氏名 + 役職」の担当者候補を抽出する（PDF 強化・要件 6）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for name, title in _PERSON_RE.findall(text or ""):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name.strip(), "title": title.strip()})
        if len(out) >= limit:
            break
    return out


# ---------------- 本体（DB 非依存） ----------------
def recursive_crawl(
    official_url: str | None,
    project: Project,
    *,
    fetch_fn=None,
    resolve_fn=None,
    pdf_fn=None,
    progress_cb=None,
    max_urls: int = DEFAULT_MAX_URLS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict:
    """公式サイトを再帰クロールして連絡先を抽出する（DB 非依存）。集計 dict を返す。

    official_url が公式サイト（非プラットフォーム）でなければクロールせず、
    OFFICIAL_SITE_NOT_FOUND を返す。fetch_fn(url)->html|None, resolve_fn(name,rtype)->
    [str], pdf_fn(url,site_domain)->{emails,socials,text_len,text} を注入できる（テスト用）。
    """
    result: dict = {
        "recursive_crawl_enabled": False,
        "recursive_crawled_urls": [],
        "recursive_skipped_urls": [],
        "recursive_emails": [],
        "recursive_forms": [],
        "recursive_socials": {},
        "recursive_pdfs": [],
        "recursive_sitemap_urls": [],
        "recursive_robots_sitemaps": [],
        "recursive_has_mx": None,
        "recursive_mx_provider": None,
        "recursive_spf_record": None,
        "recursive_dmarc_record": None,
        "recursive_failure_reasons": [],
        "recursive_summary": "",
        "recursive_people": [],
    }

    def emit(msg: str, pct: float | None = None) -> None:
        if progress_cb:
            progress_cb(msg, pct=pct)

    official = cds.official_site_or_none(official_url)
    if not official:
        result["recursive_failure_reasons"] = ["OFFICIAL_SITE_NOT_FOUND"]
        result["recursive_summary"] = (
            "公式サイトが未発見のため再帰クロールをスキップしました。"
        )
        emit("公式サイト未発見のため再帰クロールをスキップ", pct=1.0)
        return result

    result["recursive_crawl_enabled"] = True
    p = urlparse(official)
    root = f"{p.scheme}://{p.netloc}"
    domain = cds._domain_of(official)
    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))

    own_fetcher = fetch_fn is None
    fetch = fetch_fn or _make_fetcher()

    try:
        # 1. DNS / MX / SPF / DMARC（要件 7）
        emit(f"MX確認中: {domain}", pct=0.02)
        dns = check_dns(domain, resolve_fn=resolve_fn)
        result["recursive_has_mx"] = dns["has_mx"]
        result["recursive_mx_provider"] = dns["mx_provider"]
        result["recursive_spf_record"] = dns["spf_record"]
        result["recursive_dmarc_record"] = dns["dmarc_record"]

        # 2. robots.txt（要件 5：Sitemap 抽出・Disallow を尊重）
        emit("robots確認中", pct=0.05)
        robots_txt = fetch(urljoin(root, "/robots.txt"))
        robots = parse_robots(robots_txt or "")
        result["recursive_robots_sitemaps"] = robots["sitemaps"]
        disallows = robots["disallows"]

        def _robots_blocked(u: str) -> bool:
            path = urlparse(u).path or "/"
            return any(d and path.startswith(d) for d in disallows)

        # 3. sitemap.xml（要件 4：contact/about/press/privacy/terms/pdf を優先）
        emit("sitemap確認中", pct=0.08)
        sitemap_urls = read_sitemaps(fetch, root, robots["sitemaps"], domain)
        result["recursive_sitemap_urls"] = sitemap_urls

        # 4. クロール待ち行列（BFS・(url, depth)）。同一ドメイン優先。
        email_map: dict[str, dict] = {}
        forms: list[str] = []
        socials: dict[str, str] = {}
        pdf_map: dict[str, dict] = {}
        crawled: list[str] = []
        skipped: list[str] = []
        queue: list[tuple[str, int]] = []
        queued: set[str] = set()
        skip_seen: set[str] = set()

        def add_skip(u: str, reason: str) -> None:
            if u not in skip_seen:
                skip_seen.add(u)
                skipped.append(u)
                logger.info("recursive_crawl skip (%s): %s", reason, u)

        def enqueue(u: str, depth: int, *, front: bool = False) -> None:
            u = (u or "").split("#", 1)[0]
            if not u.startswith(("http://", "https://")) or u in queued:
                return
            if depth > max_depth:
                return
            # PDF / Linktree / SNS は「例外的に候補化」（HTML クロールはしない）
            if _is_pdf_url(u):
                if u not in pdf_map:
                    name = urlparse(u).path.rsplit("/", 1)[-1] or "PDF"
                    pdf_map[u] = {
                        "url": u, "label": name,
                        "relevant": any(k in u.lower() for k in cds.PDF_KEYWORDS),
                        "emails": 0, "text_len": 0,
                    }
                return
            plat = wrs._social_platform(u)
            if plat:
                wrs_norm = wrs._normalize_social(plat, u)
                if wrs_norm and not wrs._is_platform_social_handle(plat, wrs_norm):
                    socials.setdefault(plat, wrs_norm)
                return
            if _is_aggregator(u):
                socials.setdefault("linktree", u)
                return
            # 同一ドメイン以外は巡回しない（要件 1：同一ドメイン優先）
            if not cds._same_domain(u, domain):
                return
            if _is_skip_url(u):
                add_skip(u, "login/cart/checkout/account/admin")
                return
            if _robots_blocked(u):
                add_skip(u, "robots disallow")
                return
            queued.add(u)
            if front:
                queue.insert(0, (u, depth))
            else:
                queue.append((u, depth))

        # seeds：root（深さ0）＋ 優先巡回パス（深さ1）＋ sitemap 優先 URL（深さ1）
        enqueue(root, 0)
        for path in PRIORITY_PATHS:
            enqueue(root + path, 1)
        for u in sitemap_urls:
            enqueue(u, 1)

        emit(f"再帰クロール開始（最大{max_urls}URL / 深さ{max_depth}）", pct=0.1)

        ok_count = 0
        fail_count = 0
        found_email_before_forms = False
        i = 0
        while queue and len(crawled) < max_urls:
            url, depth = queue.pop(0)
            i += 1
            emit(f"巡回中 ({len(crawled) + 1}/{max_urls}): {url}",
                 pct=0.1 + 0.7 * (len(crawled) / max_urls))
            html = fetch(url)
            crawled.append(url)
            if not html:
                fail_count += 1
                continue
            ok_count += 1

            # メール抽出（既存フィルタを必ず通す・出典付き）
            page_emails = 0
            for addr in cds.extract_emails(html, site_domain):
                page_emails += 1
                score, tier = cds.score_email(addr, domain)
                owner = cds.classify_email_owner(addr, domain, site_domain)
                key = addr.lower()
                rec = email_map.get(key)
                if rec is None:
                    email_map[key] = {
                        "email": addr, "score": score, "tier": tier,
                        "email_owner": owner, "sources": [url],
                    }
                elif url not in rec["sources"]:
                    rec["sources"].append(url)
            if page_emails:
                emit(f"メール抽出: {url} から {page_emails} 件", pct=None)

            # フォーム
            if cds._is_contact_url(url) and url not in forms:
                forms.append(url)
                emit(f"フォーム抽出: {url}", pct=None)

            # リンク抽出 → SNS / PDF / 再帰
            links = cds.extract_links(html, url)
            # 巡回優先度の高い順に並べて enqueue（contact/about/press 等を先に）
            for link in sorted(links, key=_link_priority, reverse=True):
                if cds._same_domain(link, domain) and cds._is_contact_url(link):
                    if link not in forms:
                        forms.append(link)
                enqueue(link, depth + 1)

        # 5. PDF 強化（要件 6：本文抽出→email/SNS/会社名/担当者候補）
        pdfs = sorted(pdf_map.values(), key=lambda x: not x["relevant"])
        parse_targets = [x for x in pdfs if x["relevant"]][:MAX_PDF_PARSE]
        if not parse_targets:
            parse_targets = pdfs[:2]
        parse_pdf = pdf_fn or cds.extract_from_pdf
        pdf_parsed = 0
        for pdf in parse_targets:
            emit(f"PDF解析中: {pdf['url']}", pct=0.85)
            got = parse_pdf(pdf["url"], site_domain)
            pdf["text_len"] = got.get("text_len", 0)
            pdf["emails"] = len(got.get("emails") or [])
            pdf_parsed += 1
            for addr in got.get("emails") or []:
                key = addr.lower()
                if key not in email_map:
                    score, tier = cds.score_email(addr, domain)
                    email_map[key] = {
                        "email": addr, "score": score, "tier": tier,
                        "email_owner": cds.classify_email_owner(addr, domain, site_domain),
                        "sources": [pdf["url"]],
                    }
            for plat, link in (got.get("socials") or {}).items():
                norm = wrs._normalize_social(plat, link)
                if norm and not wrs._is_platform_social_handle(plat, norm):
                    socials.setdefault(plat, norm)
            people = extract_people(got.get("text") or "") if got.get("text") else []
            for person in people:
                if person not in result["recursive_people"]:
                    result["recursive_people"].append(person)

        # 運営会社（platform）のメールは営業候補に含めない
        emails = sorted(
            (e for e in email_map.values() if e["email_owner"] != "platform"),
            key=lambda e: e["score"], reverse=True,
        )

        result["recursive_crawled_urls"] = crawled
        result["recursive_skipped_urls"] = skipped
        result["recursive_emails"] = emails
        result["recursive_forms"] = forms
        result["recursive_socials"] = socials
        result["recursive_pdfs"] = pdfs[:12]

        # 6. 失敗理由コード（要件 8）
        reasons: list[str] = []
        has_email = bool(emails)
        if ok_count == 0 and crawled:
            reasons.append("CRAWL_BLOCKED")
        if not has_email:
            reasons.append("EMAIL_NOT_PUBLIC")
        if dns.get("has_mx") and not has_email:
            reasons.append("DNS_MX_FOUND_EMAIL_NOT_PUBLIC")
        if forms and not has_email:
            reasons.append("CONTACT_FORM_ONLY")
        if socials and not has_email and not forms:
            reasons.append("SOCIAL_ONLY")
        if pdf_parsed and not any(x["emails"] for x in pdfs):
            reasons.append("PDF_NO_EMAIL")
        # ネットワーク由来（実 fetcher のみ判定できる）
        statuses = getattr(fetch, "statuses", []) or []
        flags = getattr(fetch, "flags", {}) or {}
        if 429 in statuses:
            reasons.append("RATE_LIMITED")
        if flags.get("timed_out"):
            reasons.append("TIMEOUT")
        # login/account へしか辿れず本体が取れていない場合
        if ok_count == 0 and any(_is_skip_url(u) for u in skipped):
            reasons.append("LOGIN_REQUIRED")
        result["recursive_failure_reasons"] = list(dict.fromkeys(reasons))

        # 7. 要約（要件 9・11）
        mx_bit = ""
        if dns.get("has_mx"):
            mx_bit = f"MXあり({dns.get('mx_provider') or 'Other'})"
            if not has_email:
                mx_bit += "・メール運用あり/公開メール未発見"
        bits = [
            f"巡回{len(crawled)}URL(成功{ok_count})",
            f"メール{len(emails)}件",
            f"フォーム{len(forms)}件",
            f"SNS{len(socials)}件",
            f"PDF解析{pdf_parsed}件",
        ]
        if sitemap_urls:
            bits.append(f"sitemap{len(sitemap_urls)}URL")
        if robots["sitemaps"]:
            bits.append("robots確認済")
        if mx_bit:
            bits.append(mx_bit)
        result["recursive_summary"] = " / ".join(bits)
        emit(f"再帰クロール完了: {result['recursive_summary']}", pct=1.0)
        logger.info("recursive_crawl[%s] %s", getattr(project, "id", "?"),
                    result["recursive_summary"])
    finally:
        if own_fetcher:
            client = getattr(fetch, "_client", None)
            if client is not None and hasattr(client, "close"):
                client.close()

    return result


# ---------------- DB 連携 ----------------
def _resolve_official(row: ContactDiscovery, project: Project) -> str | None:
    """保存済みの各レイヤーから公式サイト URL を決める（プラットフォームは除外）。"""
    for cand in (
        getattr(row, "official_site_url", None),
        getattr(row, "search_agent_official_site_url", None),
        getattr(row, "doc_reader_official_site_url", None),
        getattr(project, "maker_url", None),
    ):
        official = cds.official_site_or_none(cand)
        if official:
            return official
    return None


def _web_layer_failure_codes(row: ContactDiscovery) -> list[str]:
    """Web 調査レイヤーの状況から検索プロバイダー系の失敗コードを導く（要件 8）。"""
    codes: list[str] = []
    if getattr(row, "web_research_error", None):
        codes.append("SEARCH_PROVIDER_FAILED")
    dc = getattr(row, "web_debug_counts", None) or {}
    if isinstance(dc, dict):
        if dc.get("results") == 0 and getattr(row, "web_researched", False):
            codes.append("SEARCH_PROVIDER_NO_RESULTS")
        # Kickstarter の websites:[]（クリエイターが公式サイト未登録）
        if dc.get("ks_websites_present") and not dc.get("ks_websites_registered"):
            codes.append("OFFICIAL_SITE_NOT_REGISTERED")
    return codes


def run_recursive_crawl(
    db: Session, project: Project, *,
    fetch_fn=None, resolve_fn=None, pdf_fn=None, progress_cb=None,
) -> ContactDiscovery:
    """再帰クロールを実行し、最新の探索結果（ContactDiscovery）の recursive_* に保存する。

    既存の探索結果が無ければ先に自動探索を実行して土台を作る。失敗してもアプリは
    落とさない（recursive_summary にエラーを記録）。progress_cb(message, pct) を渡すと
    各フェーズ・1URL 巡回ごとに進捗を通知する。
    """
    from app.config import settings

    row = cds.get_latest(db, project.id)
    if row is None:
        row = cds.run_discovery(db, project)

    official = _resolve_official(row, project)
    now = datetime.now(timezone.utc)
    try:
        result = recursive_crawl(
            official, project,
            fetch_fn=fetch_fn, resolve_fn=resolve_fn, pdf_fn=pdf_fn,
            progress_cb=progress_cb,
            max_urls=int(getattr(settings, "recursive_crawl_max_urls", DEFAULT_MAX_URLS)),
            max_depth=int(getattr(settings, "recursive_crawl_max_depth", DEFAULT_MAX_DEPTH)),
        )
        # Web 調査レイヤー由来の失敗コードも統合（検索プロバイダー系・KS 未登録）
        merged = list(result["recursive_failure_reasons"]) + _web_layer_failure_codes(row)
        result["recursive_failure_reasons"] = list(dict.fromkeys(merged))

        row.recursive_crawl_enabled = result["recursive_crawl_enabled"]
        row.recursive_crawled_urls = result["recursive_crawled_urls"] or None
        row.recursive_skipped_urls = result["recursive_skipped_urls"] or None
        row.recursive_emails = result["recursive_emails"] or None
        row.recursive_forms = result["recursive_forms"] or None
        row.recursive_socials = result["recursive_socials"] or None
        row.recursive_pdfs = result["recursive_pdfs"] or None
        row.recursive_sitemap_urls = result["recursive_sitemap_urls"] or None
        row.recursive_robots_sitemaps = result["recursive_robots_sitemaps"] or None
        row.recursive_has_mx = result["recursive_has_mx"]
        row.recursive_mx_provider = result["recursive_mx_provider"]
        row.recursive_spf_record = result["recursive_spf_record"]
        row.recursive_dmarc_record = result["recursive_dmarc_record"]
        row.recursive_failure_reasons = result["recursive_failure_reasons"] or None
        row.recursive_summary = result["recursive_summary"] or None
        row.recursive_crawled_at = now
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("recursive crawl failed (project=%s): %s", project.id, exc)
        row.recursive_crawl_enabled = True
        row.recursive_crawled_at = now
        row.recursive_summary = f"再帰クロールに失敗しました: {exc}"[:2000]

    db.commit()
    db.refresh(row)
    return row
