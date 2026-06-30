"""AI Web Research Mode の業務ロジック。

既存の Contact Discovery（公式サイト中心のクロール）/ AI Contact Research（既存
結果の整理）に加えて、検索エンジン（DuckDuckGo HTML）の結果と公式サイトの代表
パスを横断クロールし、メール・問い合わせフォーム・SNS・PDF を「実際に取得した
ページから」抽出する。

設計方針：
- メールは推測で作らない。すべて実際に取得したページ本文/mailto から抽出し、
  contact_discovery_service の既存除外フィルタ（platform / sentry / no-reply /
  postmaster / hash 等）を必ず通す。各メールは出典 URL（sources）を持つ。
- 抽出・スコアリング・推奨判定は contact_discovery_service の純粋関数を再利用する。
- ネットワークは fetch_fn（url->html|None）/ search_fn（query->[url]）として注入でき、
  テストはネットワーク無しで検証できる。
- 安全設計：クエリ数・URL 数・タイムアウト・レート制限・重複排除・robots 配慮・
  ログインページ回避。失敗してもアプリは落とさない。

結果は最新の ContactDiscovery 行の web_* カラムに分離保存する（自動抽出/AI 調査を
無条件上書きしない）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote, urlparse

from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch
from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds

logger = logging.getLogger("web_research")

# --- 安全設計のパラメータ ---
MAX_QUERIES = 10            # 実行する検索クエリ数の上限
MAX_RESULTS_PER_QUERY = 5   # 1 クエリあたり採用する検索結果 URL 数の上限
MAX_URLS = 25               # クロールする URL 数の上限
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


def build_web_search_queries(project: Project) -> list[str]:
    """要件 3 の検索クエリ候補を生成する（重複排除・順序維持）。"""
    name = (project.maker_name or "").strip()
    title = (project.title or "").strip()
    official_domain = cds._domain_of(project.maker_url)

    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    if name:
        for kw in ("contact", "email", "partnership", "distributor", "wholesale", "press"):
            add(f'"{name}" {kw}')
    if title:
        add(f'"{title}" contact')
        add(f'"{title}" official site')
    if official_domain:
        add(f"site:{official_domain} contact")
        add(f"site:{official_domain} partnership")
        add(f"site:{official_domain} wholesale")
        add(f"site:{official_domain} distributor")
        add(f"site:{official_domain} filetype:pdf")
    return queries


# ---------------- 検索結果パース ----------------
# DuckDuckGo HTML 版（https://html.duckduckgo.com/html/）の結果リンクは
# //duckduckgo.com/l/?uddg=<encoded-url>&... 形式のリダイレクトになっている。
_DDG_UDDG_RE = re.compile(r"uddg=([^&\"'>]+)")
_DDG_RESULT_A_RE = re.compile(
    r'class="result__a"[^>]*href="(https?://[^"]+)"', re.IGNORECASE
)


def parse_duckduckgo_results(html: str) -> list[str]:
    """DuckDuckGo HTML 検索結果から上位の外部 URL を抽出する（重複排除）。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return
        host = urlparse(url).netloc.lower()
        if any(h in host for h in _SEARCH_ENGINE_HOSTS):
            return
        if url not in seen:
            seen.add(url)
            out.append(url)

    for enc in _DDG_UDDG_RE.findall(html or ""):
        try:
            add(unquote(enc))
        except Exception:  # noqa: BLE001  デコード失敗は無視
            continue
    for direct in _DDG_RESULT_A_RE.findall(html or ""):
        add(direct)
    return out


def _default_search_fn():
    """DuckDuckGo HTML を使った検索関数を返す（query -> [url]）。失敗時は []。"""
    from app.scrapers.http import HttpClient

    client = HttpClient(
        rate_limit_seconds=RATE_LIMIT_SECONDS, timeout=SEARCH_TIMEOUT, retries=0
    )

    def search(query: str) -> list[str]:
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            html = client.get_text(url)
        except Exception as exc:  # noqa: BLE001  検索失敗は graceful に空で返す
            logger.info("web search failed (%s): %s", query, exc)
            return []
        return parse_duckduckgo_results(html)

    search._client = client  # type: ignore[attr-defined]
    return search


# ---------------- URL 分類 ----------------
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
    return any(
        cds._domain_matches(host, d) for d in cds.PLATFORM_EMAIL_DOMAINS
    )


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


def web_research(
    project: Project,
    research: CompanyResearch | None = None,
    *,
    fetch_fn=None,
    search_fn=None,
) -> dict:
    """Web リサーチ本体（DB 非依存）。集計した結果 dict を返す。

    fetch_fn(url)->html|None, search_fn(query)->[url] を注入できる（テスト用）。
    未指定なら DuckDuckGo HTML 検索 + 既存 HTTP 基盤を使う。
    """
    official = project.maker_url or ""
    official_domain = cds._domain_of(official)
    site_domain = cds.source_site_email_domain(getattr(project, "source_site", None))

    own_fetcher = fetch_fn is None
    own_search = search_fn is None
    fetch = fetch_fn or _make_fetcher()
    search = search_fn or _default_search_fn()

    queries = build_web_search_queries(project)
    searched_queries: list[str] = []
    search_failures = 0

    # 1. 検索クエリを実行して候補 URL を集める（安全のため上限つき）
    search_result_urls: list[str] = []
    seen_results: set[str] = set()
    try:
        for q in queries[:MAX_QUERIES]:
            searched_queries.append(q)
            try:
                results = search(q) or []
            except Exception as exc:  # noqa: BLE001  個別失敗は無視
                logger.info("search error (%s): %s", q, exc)
                results = []
            if not results:
                search_failures += 1
            for u in results[:MAX_RESULTS_PER_QUERY]:
                if u not in seen_results:
                    seen_results.add(u)
                    search_result_urls.append(u)
    finally:
        if own_search:
            client = getattr(search, "_client", None)
            if client is not None:
                client.close()

    # 2. クロール対象 URL を決める（公式サイト + 検索結果。social/pdf/login は除外）
    socials: dict[str, str] = {}
    pdfs: list[dict] = []
    pdf_seen: set[str] = set()
    crawl_urls: list[str] = []
    crawl_seen: set[str] = set()

    def add_crawl(u: str) -> None:
        if len(crawl_urls) >= MAX_URLS or u in crawl_seen:
            return
        if not u.startswith(("http://", "https://")):
            return
        if _is_skip_url(u):
            return
        crawl_seen.add(u)
        crawl_urls.append(u)

    # 公式サイト・案件ページ・代表パスを優先
    for u in _seed_and_known_urls(project, research):
        add_crawl(u)

    # 検索結果の振り分け
    for u in search_result_urls:
        platform = _social_platform(u)
        if platform:
            socials.setdefault(platform, u)
            continue
        if _is_pdf_url(u):
            if u not in pdf_seen:
                pdf_seen.add(u)
                name = urlparse(u).path.rsplit("/", 1)[-1] or "PDF"
                pdfs.append({"url": u, "label": name, "relevant": True})
            continue
        # 運営会社（プラットフォーム）のページは案件ページ以外はクロールしない
        if _is_platform_domain(u) and u != (project.source_url or ""):
            continue
        add_crawl(u)

    # 3. クロールして抽出
    searched: list[str] = []
    candidate_pages: list[dict] = []
    email_map: dict[str, dict] = {}
    forms: list[str] = []
    official_checked = False

    try:
        for url in crawl_urls:
            if len(searched) >= MAX_URLS:
                break
            html = fetch(url)
            searched.append(url)
            candidate_pages.append({"url": url, "type": _page_type(url, official_domain)})
            if official_domain and cds._same_domain(url, official_domain):
                official_checked = True
            if not html:
                continue

            # メール（既存フィルタを必ず通す。出典 URL を付与）
            for addr in cds.extract_emails(html, site_domain):
                score, tier = cds.score_email(addr, official_domain)
                owner = cds.classify_email_owner(addr, official_domain, site_domain)
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

            # SNS
            for platform, link in cds.extract_socials(html, url).items():
                socials.setdefault(platform, link)

            # 問い合わせフォーム
            if cds._is_contact_url(url) and url not in forms:
                forms.append(url)
            links = cds.extract_links(html, url)
            for link in links:
                if official_domain and cds._same_domain(link, official_domain):
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

    pdfs = pdfs[:8]
    # 運営会社（platform）のメールは営業候補に含めない
    emails = sorted(
        (e for e in email_map.values() if e["email_owner"] != "platform"),
        key=lambda e: e["score"],
        reverse=True,
    )
    primary_email = emails[0]["email"] if emails else None
    primary_form = forms[0] if forms else None
    has_official_site = bool(project.maker_url)

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

    notes_bits = [
        f"{len(searched_queries)} query(ies)",
        f"{len(searched)} url(s)",
        f"{len(emails)} email(s)",
        f"score {score}",
    ]
    if search_failures:
        notes_bits.append(
            f"{search_failures} search(es) returned no results "
            "(engine may be blocking or rate-limiting)"
        )
    if not search_result_urls:
        notes_bits.append(
            "no search-engine results were usable; relied on official-site crawl"
        )

    return {
        "searched_queries": searched_queries,
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
        row.web_searched_queries = result["searched_queries"] or None
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
