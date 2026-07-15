"""Contact Discovery v2：人間の検索手順に近い連絡先探索。

人が「メーカーの担当者を探す」ときの手順をそのまま自動化する探索エンジン。
既存のレイヤー（web_research / recursive_crawl / doc_reader / search_agent）とは
独立して、以下の一本道フローを「どこを探索しているか」を逐次報告しながら実行する。

    1. 案件から company_name / product_name / campaign_url を取得
    2. 公式サイト候補を探索（project.website → 会社サイト → Google/Bing 検索）
    3. 公式サイト発見後、優先クロール（/contact /about /team /support /privacy ...）
       さらに header / footer / meta / schema.org(JSON-LD) も解析
    4. LinkedIn 探索（会社 / 創業者 / 担当者）
    5. メール抽出
    6. メール検証（ダミー/例/テスト/no-reply を排除）
    7. CRM 更新（呼び出し側 API）

取得元によって信頼度（★1〜5）を付ける：
    ★★★★★ 公式サイト Contact       (official_site_contact)
    ★★★★☆ 公式サイト Footer         (official_site_footer)
    ★★★★☆ 会社 About               (company_about)
    ★★★☆☆ 公式サイト 規約/プライバシー (official_site_legal)
    ★★★☆☆ LinkedIn                 (linkedin)
    ★★☆☆☆ クラファンページ          (crowdfunding_page)
    ★☆☆☆☆ 推測                     (guess)

品質規約：
    - 404/非200 ページは解析しない（fetch_fn が None を返す）
    - example / dummy / test / no-reply 等は email_validation で必ず排除
    - 推測メール（guess）は生成しない（信頼度定義のみ保持）

抽出（extract_*）とフロー（discover_v2）は純粋関数に近い形で分離し、fetch_fn /
search_fn を注入すればネットワーク無しで検証できる。取得失敗してもアプリは落とさず
v2_status=failed / v2_error に記録して 200 で返す。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch
from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds
from app.services.email_validation import (
    email_confidence,
    is_valid_business_email,
)

logger = logging.getLogger("contact_discovery_v2")

# --- 安全設計のパラメータ ---
MAX_OFFICIAL_CANDIDATES = 8   # 公式サイト候補の検討上限
MAX_CRAWL_PAGES = 12          # 公式サイトで巡回する最大ページ数
MAX_SEARCH_QUERIES = 8        # 実行する検索クエリ上限

# 公式サイト発見後に優先クロールするパス（要件の順序）。
PRIORITY_PATHS = (
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/company",
    "/support",
    "/privacy",
    "/terms",
)

# --- 信頼度（取得元 → 星・ラベル） ---
CONFIDENCE_TIERS: dict[str, dict] = {
    "official_site_contact": {"stars": 5, "label": "公式サイト Contact"},
    "official_site_footer": {"stars": 4, "label": "公式サイト Footer"},
    "company_about": {"stars": 4, "label": "会社 About"},
    "official_site_legal": {"stars": 3, "label": "公式サイト 規約/プライバシー"},
    "linkedin": {"stars": 3, "label": "LinkedIn"},
    "crowdfunding_page": {"stars": 2, "label": "クラファンページ"},
    "guess": {"stars": 1, "label": "推測"},
}


def tier_meta(source: str) -> dict:
    """信頼度ソース名 → {stars, label}。未知なら未検証扱い（★2）。"""
    return CONFIDENCE_TIERS.get(source, {"stars": 2, "label": "未検証"})


# ---------------- HTML 領域・構造化データの抽出（純粋関数） ----------------
_FOOTER_RE = re.compile(r"<footer[^>]*>(.*?)</footer>", re.IGNORECASE | re.DOTALL)
_HEADER_RE = re.compile(r"<header[^>]*>(.*?)</header>", re.IGNORECASE | re.DOTALL)
_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_META_EMAIL_RE = re.compile(
    r'<meta[^>]*(?:property|name)=["\'][^"\']*email[^"\']*["\'][^>]*'
    r'content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def extract_region(html: str, region_re: re.Pattern) -> str:
    """<footer>/<header> など指定領域の内側 HTML を連結して返す（無ければ空）。"""
    return "\n".join(region_re.findall(html or ""))


def _walk_jsonld(node, emails: set[str], socials: list[str]) -> None:
    """JSON-LD ノードを再帰的に走査し、email / sameAs(SNS) / contactPoint を集める。"""
    if isinstance(node, dict):
        for key, val in node.items():
            k = str(key).lower()
            if k == "email" and isinstance(val, str):
                emails.add(val.replace("mailto:", "").strip())
            elif k == "sameas":
                if isinstance(val, str):
                    socials.append(val)
                elif isinstance(val, list):
                    socials.extend(str(v) for v in val if isinstance(v, str))
            else:
                _walk_jsonld(val, emails, socials)
    elif isinstance(node, list):
        for item in node:
            _walk_jsonld(item, emails, socials)


def extract_jsonld(html: str) -> dict:
    """schema.org JSON-LD から email / socials(sameAs) を抽出する。

    Returns: {"emails": [str], "socials": [url]}（壊れた JSON は無視）。
    """
    emails: set[str] = set()
    socials: list[str] = []
    for block in _JSONLD_RE.findall(html or ""):
        raw = block.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        _walk_jsonld(data, emails, socials)
    return {"emails": sorted(emails), "socials": socials}


def extract_meta_emails(html: str) -> list[str]:
    """<meta ... email ... content="..."> 形式のメールを抽出する。"""
    return [m.strip() for m in _META_EMAIL_RE.findall(html or "") if "@" in m]


def _path_kind(url: str) -> str:
    """URL パスから公式サイト上のページ種別を判定する（信頼度ソースの決定に使う）。"""
    path = (urlparse(url or "").path or "/").lower().rstrip("/")
    if path in ("", "/"):
        return "root"
    if any(h in path for h in ("contact", "support", "help", "impressum", "kontakt",
                               "customer-service", "inquiry")):
        return "contact"
    if any(h in path for h in ("about", "company", "team", "our-story", "who-we-are")):
        return "about"
    if any(h in path for h in ("privacy", "terms", "legal", "imprint", "policy")):
        return "legal"
    return "other"


def _kind_to_source(kind: str) -> str:
    """ページ種別 → 本文（非フッター）メールの信頼度ソース。"""
    return {
        "contact": "official_site_contact",
        "about": "company_about",
        "legal": "official_site_legal",
        "root": "official_site_footer",   # ルート本文は控えめに Footer 扱い
        "other": "official_site_footer",
    }.get(kind, "official_site_footer")


# ---------------- 公式サイト候補のスコアリング（純粋関数） ----------------
_AGGREGATOR_HINTS = (
    "facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.",
    "youtu.be", "tiktok.", "pinterest.", "reddit.", "medium.com", "linktr.ee",
    "amazon.", "ebay.", "etsy.", "aliexpress.", "wikipedia.", "crunchbase.",
    "apps.apple.com", "play.google.com", "notion.site", "google.com",
    "bing.com", "duckduckgo.com", "yahoo.",
)


def _is_official_candidate_url(url: str) -> bool:
    """公式サイト候補になりうる URL か（プラットフォーム/SNS/集約サイトを除く）。"""
    if not url or not url.startswith(("http://", "https://")):
        return False
    if cds.is_platform_url(url):
        return False
    host = urlparse(url).netloc.lower()
    return not any(h in host for h in _AGGREGATOR_HINTS)


def score_official_candidate(url: str, terms: set[str]) -> int:
    """公式サイト候補 URL を会社名/商品名の語との一致でスコアリングする。"""
    dom = cds._domain_of(url)
    if not dom:
        return 0
    token = dom.split(".")[0]
    score = 0
    for t in terms:
        if not t or len(t) < 3:
            continue
        if t == token:
            score += 50
        elif t in token or token in t:
            score += 25
    # トップページ（浅い階層）ほど公式サイトらしい
    path = urlparse(url).path.strip("/")
    if not path:
        score += 10
    return score


def _root_url(url: str) -> str:
    """URL をスキーム+ホストのルートに正規化する。"""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ---------------- LinkedIn 抽出（純粋関数） ----------------
def classify_linkedin(url: str) -> str | None:
    """LinkedIn URL を company / person に分類する（それ以外は None）。"""
    if not url:
        return None
    low = url.lower()
    if "linkedin.com" not in low:
        return None
    if "/company/" in low or "/school/" in low:
        return "company"
    if "/in/" in low or "/pub/" in low:
        return "person"
    return None


# ---------------- 本体（DB 非依存・注入可能） ----------------
class _StepLog:
    """探索ステップ（UI 進捗表示用）を積む小さなヘルパー。"""

    def __init__(self, progress_cb=None) -> None:
        self.steps: list[dict] = []
        self._cb = progress_cb

    def add(self, phase: str, label: str, *, status: str = "done",
            detail: str | None = None, urls: list[str] | None = None) -> None:
        n = len(self.steps) + 1
        rec = {
            "step": n,
            "phase": phase,
            "label": label,
            "status": status,
            "detail": detail,
            "urls": [u for u in (urls or []) if u][:10],
        }
        self.steps.append(rec)
        if self._cb:
            try:
                self._cb(label, pct=None)
            except Exception:  # noqa: BLE001  進捗通知の失敗は探索を止めない
                pass


def _add_email(
    bucket: dict[str, dict],
    email: str,
    source: str,
    source_url: str | None,
    *,
    official_domain: str | None,
    source_site_domain: str | None,
) -> None:
    """検証済みメールを信頼度ソース付きで bucket に統合する（最良ソースを保持）。"""
    addr = (email or "").strip().strip(".")
    if not addr or "@" not in addr:
        return
    # ダミー/例/テスト/no-reply/プラットフォーム/監視系を排除（品質規約）
    if not is_valid_business_email(addr):
        return
    if cds.email_exclusion_reason(addr, source_site_domain):
        return
    key = addr.lower()
    owner = cds.classify_email_owner(addr, official_domain, source_site_domain)
    if owner in ("platform", "monitoring"):
        return
    meta = tier_meta(source)
    cur = bucket.get(key)
    if cur is None or meta["stars"] > cur["stars"]:
        rk = cds.rank_sales_email(addr, email_owner=owner)
        bucket[key] = {
            "email": addr,
            "stars": meta["stars"],
            "confidence_source": source,
            "confidence_label": meta["label"],
            "source_url": source_url,
            "email_owner": owner,
            "sales_stars": rk["stars"],
            "sales_reason": rk["reason"],
            "sources": [source_url] if source_url else [],
        }
    elif source_url and source_url not in cur["sources"]:
        cur["sources"].append(source_url)


def _default_search_fn():
    """設定に基づく検索ランナー（query -> [{url,title,snippet}]）。"""
    from app.services.search_providers import get_search_fn

    return get_search_fn()


def _default_fetch_fn():
    """404/非200 を None にする取得関数（recursive_crawl と同じ設定済み fetcher）。"""
    from app.services.recursive_crawl_service import _make_fetcher

    return _make_fetcher()


def discover_v2(
    project: Project,
    research: CompanyResearch | None = None,
    *,
    fetch_fn=None,
    search_fn=None,
    progress_cb=None,
    known_official: str | None = None,
) -> dict:
    """人間の検索手順に近い連絡先探索を実行して結果を返す（DB 非依存）。

    fetch_fn(url) -> html | None（404/非200 は None）。
    search_fn(query) -> [{"url","title","snippet"}]。
    どちらも未指定なら実ネットワーク版を生成する。
    """
    fetch = fetch_fn or _default_fetch_fn()
    search = search_fn or _default_search_fn()
    log = _StepLog(progress_cb)

    company_name = (project.maker_name or "").strip()
    product_name = (project.title or "").strip()
    campaign_url = (project.source_url or project.maker_url or "").strip()
    source_site_domain = cds.source_site_email_domain(
        getattr(project, "source_site", None)
    )

    # 検索・スコアリング用の有意語（会社名/商品名/短縮タイトル）
    terms = cds.significant_terms(company_name, product_name)

    # ---- 1. 案件から基本情報を取得 ----
    log.add(
        "collect", "案件情報を取得",
        detail=f"会社: {company_name or '不明'} / 商品: {product_name or '不明'}",
        urls=[campaign_url] if campaign_url else [],
    )

    emails: dict[str, dict] = {}
    socials: dict[str, str] = {}
    forms: list[str] = []
    crawled: list[dict] = []
    candidates: list[dict] = []
    linkedin: list[dict] = []
    searched_queries: list[str] = []

    def add_social(url: str) -> None:
        plat = None
        for p, pat in cds.SOCIAL_PATTERNS.items():
            if pat.search(url or ""):
                plat = p
                break
        if not plat or cds._SOCIAL_EXCLUDE.search(url or ""):
            return
        socials.setdefault(plat, url)

    def add_linkedin(url: str, source: str, name: str | None = None) -> None:
        kind = classify_linkedin(url)
        if not kind:
            return
        clean = url.split("?", 1)[0]
        if any(li["url"] == clean for li in linkedin):
            return
        linkedin.append({"type": kind, "url": clean, "name": name, "source": source})

    # ---- 2. 公式サイト候補を探索 ----
    official_url: str | None = None
    official_source: str | None = None

    # 優先度 1: project.website（maker_url）＋ 既に確定済みの公式サイト（known_official）。
    # 既知の公式サイトを候補に含めることで、検索が失敗しても既知サイトを直接クロールして
    # 連絡先を再取得できる（検索依存で「既知サイトを巡回しない」取りこぼしを防ぐ）。
    _seeded: set[str] = set()
    for seed_url, seed_reason in (
        (cds.official_site_or_none(project.maker_url), "案件に登録された公式サイト"),
        (cds.official_site_or_none(known_official), "既に確定済みの公式サイト"),
    ):
        if seed_url and _root_url(seed_url) not in _seeded:
            _seeded.add(_root_url(seed_url))
            candidates.append({
                "url": _root_url(seed_url), "score": 100, "source": "project_website",
                "adopted": False, "reason": seed_reason,
            })

    # 優先度 2〜4: 会社サイト / Google / Bing 検索
    def run_search(query: str) -> list[dict]:
        if not query or query in searched_queries:
            return []
        if len(searched_queries) >= MAX_SEARCH_QUERIES:
            return []
        searched_queries.append(query)
        try:
            return search(query) or []
        except Exception as exc:  # noqa: BLE001  1 クエリ失敗は無視
            logger.info("v2 search failed %r: %s", query, exc)
            return []

    search_queries: list[str] = []
    if company_name:
        search_queries += [
            f"{company_name} official site",
            f"{company_name} contact",
        ]
    if product_name:
        search_queries.append(f"{product_name} official site")
    if company_name:
        search_queries.append(f"{company_name} official website")

    for q in search_queries:
        for r in run_search(q):
            url = r.get("url") if isinstance(r, dict) else str(r)
            if not _is_official_candidate_url(url or ""):
                continue
            root = _root_url(url)
            if any(c["url"] == root for c in candidates):
                continue
            sc = score_official_candidate(url, terms)
            candidates.append({
                "url": root, "score": sc, "source": "search",
                "adopted": False, "reason": f"検索候補（一致スコア {sc}）",
                "query": q, "title": r.get("title") if isinstance(r, dict) else None,
            })
            if len(candidates) >= MAX_OFFICIAL_CANDIDATES:
                break
        if len(candidates) >= MAX_OFFICIAL_CANDIDATES:
            break

    provider = getattr(search, "provider", None)
    log.add(
        "official_site", "公式サイト候補を探索",
        detail=f"{len(candidates)} 件の候補（provider={provider or 'n/a'}）",
        urls=[c["url"] for c in candidates],
    )

    # 候補を優先度順（project_website→スコア降順）に並べ、取得できた最初を採用
    candidates.sort(
        key=lambda c: (c["source"] == "project_website", c["score"]), reverse=True
    )
    root_html: str | None = None
    for c in candidates:
        # 共通の公式サイト検証を通す。プラットフォーム/短縮URL/SNS/販促/EC/中間ノードは
        # ここで除外し、そもそもクロール対象にしない（誤サイトを巡回して誤連絡先を拾わない）。
        vetted, info = cds.vet_official_site(
            c["url"], maker_name=company_name, product_title=product_name,
            direct_linked=(c["source"] == "project_website"))
        c["decision"] = info["decision"]
        c["evidence"] = info["evidence"]
        c["rejection_reasons"] = info["rejection_reasons"]
        if vetted is None:
            c["reason"] = (c.get("reason") or "") + (
                f"（除外: {','.join(info['rejection_reasons']) or 'rejected'}）")
            continue
        html = fetch(c["url"])
        if html:
            official_url = c["url"]
            official_source = c["source"]
            c["adopted"] = True
            root_html = html
            break
        c["reason"] = (c.get("reason") or "") + "（取得失敗/404）"

    if official_url:
        log.add(
            "official_site", "公式サイトを確定",
            detail=f"{official_url}（{official_source}）", urls=[official_url],
        )
    else:
        log.add(
            "official_site", "公式サイト未発見",
            status="empty",
            detail="候補が取得できませんでした。クラファンページと検索導線で補完します。",
        )

    official_domain = cds._domain_of(official_url) if official_url else None

    # ---- 3. 公式サイト優先クロール（Contact/About/... + header/footer/meta/schema） ----
    def process_page(url: str, html: str, kind: str) -> int:
        """1 ページを解析し、メール/SNS/フォーム/構造化データを取り込む。返り値=メール数。"""
        n_before = len(emails)
        body_source = _kind_to_source(kind)

        # footer 領域のメールは footer 信頼度で登録（本文より控えめ）
        footer_html = extract_region(html, _FOOTER_RE)
        footer_emails = set(cds.extract_emails(footer_html, source_site_domain))
        for e in footer_emails:
            _add_email(emails, e, "official_site_footer", url,
                       official_domain=official_domain,
                       source_site_domain=source_site_domain)
        # 本文（footer 以外）のメールはページ種別に応じた信頼度で登録
        for e in cds.extract_emails(html, source_site_domain):
            src = "official_site_footer" if e in footer_emails else body_source
            _add_email(emails, e, src, url,
                       official_domain=official_domain,
                       source_site_domain=source_site_domain)
        # schema.org(JSON-LD)：構造化された連絡先は Contact 相当の高信頼
        ld = extract_jsonld(html)
        for e in ld["emails"]:
            _add_email(emails, e, "official_site_contact", url,
                       official_domain=official_domain,
                       source_site_domain=source_site_domain)
        for s in ld["socials"]:
            add_social(s)
            add_linkedin(s, "schema.org")
        # meta タグのメール
        for e in extract_meta_emails(html):
            _add_email(emails, e, body_source, url,
                       official_domain=official_domain,
                       source_site_domain=source_site_domain)
        # SNS（header/footer/本文いずれも）
        for plat, purl in cds.extract_socials(html, url).items():
            socials.setdefault(plat, purl)
            if plat == "linkedin":
                add_linkedin(purl, "official_site")
        # 問い合わせフォーム（contact ページ URL 自体をフォーム候補に）
        if kind == "contact":
            forms.append(url)
        return len(emails) - n_before

    if official_url:
        visit_urls: list[str] = [official_url]
        seen_urls: set[str] = {official_url}
        # 優先パスを候補に追加
        for path in PRIORITY_PATHS:
            u = urljoin(official_url + "/", path.lstrip("/"))
            if u not in seen_urls:
                seen_urls.add(u)
                visit_urls.append(u)

        # ルートページのリンクから優先パスに該当する実在 URL も拾う
        if root_html:
            for link in cds.extract_links(root_html, official_url):
                if cds._domain_of(link) != official_domain:
                    continue
                lp = urlparse(link).path.lower()
                if any(h in lp for h in (
                    "contact", "about", "team", "company", "support",
                    "privacy", "terms",
                )):
                    link = link.split("#", 1)[0]
                    if link not in seen_urls:
                        seen_urls.add(link)
                        visit_urls.append(link)

        for url in visit_urls[:MAX_CRAWL_PAGES]:
            kind = _path_kind(url)
            # ルートは取得済み HTML を再利用
            html = root_html if url == official_url else fetch(url)
            ok = bool(html)
            n_emails = process_page(url, html, kind) if html else 0
            crawled.append({
                "url": url, "kind": kind, "ok": ok, "emails": n_emails,
            })

        crawl_ok = [c["url"] for c in crawled if c["ok"]]
        log.add(
            "crawl", "公式サイトを優先クロール",
            detail=f"{len(crawl_ok)}/{len(crawled)} ページ取得・"
                   f"メール {sum(c['emails'] for c in crawled)} 件",
            urls=crawl_ok,
        )

    # ---- 4. LinkedIn 探索（会社 / 創業者 / 担当者） ----
    if company_name:
        for q in (f"{company_name} linkedin",
                  f"{company_name} founder linkedin"):
            for r in run_search(q):
                url = r.get("url") if isinstance(r, dict) else str(r)
                title = r.get("title") if isinstance(r, dict) else None
                if url and "linkedin.com" in url.lower():
                    add_linkedin(url, "search", title)
    li_company = next((li["url"] for li in linkedin if li["type"] == "company"), None)
    li_person = next((li["url"] for li in linkedin if li["type"] == "person"), None)
    log.add(
        "linkedin", "LinkedIn を探索",
        status="done" if linkedin else "empty",
        detail=f"会社: {'有' if li_company else '無'} / 担当者: {'有' if li_person else '無'}",
        urls=[li["url"] for li in linkedin],
    )

    # ---- 5. メール抽出（クラファンページからの補完） ----
    if campaign_url and not any(c["stars"] >= 5 for c in emails.values()):
        camp_html = fetch(campaign_url)
        if camp_html:
            for e in cds.extract_emails(camp_html, source_site_domain):
                _add_email(emails, e, "crowdfunding_page", campaign_url,
                           official_domain=official_domain,
                           source_site_domain=source_site_domain)

    # ---- 6. メール検証は _add_email 内で完了済み（ダミー/例/テスト排除） ----
    email_list = sorted(
        emails.values(),
        key=lambda e: (e["stars"], e["sales_stars"], -len(e["email"])),
        reverse=True,
    )
    # 取得元による信頼度ラベル（高信頼/要確認 等）も併記
    for e in email_list:
        conf = email_confidence(
            email=e["email"], email_owner=e.get("email_owner"),
            sources=e.get("sources") or [],
        )
        e["confidence_level"] = conf["level"]

    log.add(
        "extract", "メールを抽出・検証",
        status="done" if email_list else "empty",
        detail=f"有効メール {len(email_list)} 件"
               + (f"（最有力 {email_list[0]['email']} ★{email_list[0]['stars']}）"
                  if email_list else "（見つかりませんでした）"),
        urls=[e["source_url"] for e in email_list if e.get("source_url")],
    )

    # ---- 代表値・信頼度スコア ----
    primary = email_list[0] if email_list else None
    primary_email = primary["email"] if primary else None
    primary_source_url = primary["source_url"] if primary else None
    primary_stars = primary["stars"] if primary else 0

    if primary:
        confidence_score = {5: 95, 4: 80, 3: 60, 2: 40, 1: 20}.get(primary_stars, 30)
    elif official_url:
        confidence_score = 30
    elif li_company or li_person:
        confidence_score = 20
    else:
        confidence_score = 10

    # 推奨チャネル
    if primary_email:
        recommended_channel = "email"
    elif forms:
        recommended_channel = "contact_form"
    elif li_company or li_person:
        recommended_channel = "linkedin"
    elif socials.get("instagram"):
        recommended_channel = "instagram"
    else:
        recommended_channel = "manual_research"

    summary = _build_summary(
        company_name, official_url, official_source, email_list, forms,
        li_company, li_person,
    )

    return {
        "company_name": company_name or None,
        "product_name": product_name or None,
        "campaign_url": campaign_url or None,
        "official_site_url": official_url,
        "official_site_source": official_source,
        "official_site_candidates": candidates or None,
        "crawled_pages": crawled or None,
        "emails": email_list or None,
        "socials": socials or None,
        "forms": _dedup(forms) or None,
        "linkedin_company_url": li_company,
        "linkedin_person_url": li_person,
        "linkedin_candidates": linkedin or None,
        "searched_queries": searched_queries or None,
        "search_provider": provider,
        "primary_email": primary_email,
        "primary_source_url": primary_source_url,
        "primary_stars": primary_stars,
        "confidence_score": confidence_score,
        "recommended_channel": recommended_channel,
        "steps": log.steps,
        "summary": summary,
    }


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _build_summary(
    company: str, official_url, official_source, emails, forms,
    li_company, li_person,
) -> str:
    """UI・ログ用の探索サマリ（次に取る行動が分かる説明文）。"""
    parts: list[str] = []
    if official_url:
        parts.append(f"公式サイト {official_url} を特定")
    else:
        parts.append("公式サイトは未特定")
    if emails:
        top = emails[0]
        stars = "★" * top["stars"] + "☆" * (5 - top["stars"])
        parts.append(
            f"最有力メール {top['email']}（{stars} {top['confidence_label']}）"
        )
    elif forms:
        parts.append("メールは未発見。問い合わせフォームから連絡可能")
    elif li_company or li_person:
        parts.append("メールは未発見。LinkedIn から接触可能")
    else:
        parts.append("メール・フォーム未発見。手動検索を推奨")
    return " / ".join(parts)


# ---------------- DB 保存 ----------------
def run_contact_discovery_v2(
    db: Session, project: Project, *, fetch_fn=None, search_fn=None, progress_cb=None
) -> ContactDiscovery:
    """v2 探索を実行し、最新の探索結果（ContactDiscovery）の v2_* に保存する。

    既存の探索結果が無ければ先に自動探索を実行して土台を作る。v2 結果は v2_*
    カラムに分離保存し、自動抽出（primary_email 等）・他レイヤーは上書きしない。
    失敗時は v2_status=failed / v2_error に記録し、アプリは落とさない。
    """
    research = cds._latest_research(db, project.id)
    row = cds.get_latest(db, project.id)
    if row is None:
        row = cds.run_discovery(db, project)
    # 既知の確定公式サイトを控えておき、検索が失敗しても直接クロールできるようにする。
    known_official = cds.official_site_or_none(row.official_site_url) if row else None

    now = datetime.now(timezone.utc)
    # read が済んだので接続を解放してから外部処理（v2 探索＝HTTP/Playwright）へ入る。
    cds.release_connection(db)
    try:
        result = discover_v2(
            project, research, fetch_fn=fetch_fn, search_fn=search_fn,
            progress_cb=progress_cb, known_official=known_official,
        )
        row.v2_researched = True
        row.v2_researched_at = now
        row.v2_status = "completed"
        row.v2_error = None
        row.v2_company_name = result["company_name"]
        row.v2_product_name = result["product_name"]
        row.v2_campaign_url = result["campaign_url"]
        # v2 が確定した公式サイトも共通検証を通してから保存（誤採用を残さない）。
        v2_official, _v2_info = cds.vet_official_site(
            result["official_site_url"], maker_name=project.maker_name,
            product_title=project.title,
            direct_linked=(result["official_site_source"] == "project_website"))
        row.v2_official_site_url = v2_official
        row.v2_official_site_source = result["official_site_source"]
        row.v2_official_site_candidates = result["official_site_candidates"]
        row.v2_crawled_pages = result["crawled_pages"]
        row.v2_emails = result["emails"]
        row.v2_socials = result["socials"]
        row.v2_forms = result["forms"]
        row.v2_linkedin_company_url = result["linkedin_company_url"]
        row.v2_linkedin_person_url = result["linkedin_person_url"]
        row.v2_linkedin_candidates = result["linkedin_candidates"]
        row.v2_searched_queries = result["searched_queries"]
        row.v2_search_provider = result["search_provider"]
        row.v2_primary_email = result["primary_email"]
        row.v2_primary_source_url = result["primary_source_url"]
        row.v2_primary_stars = result["primary_stars"]
        row.v2_confidence_score = result["confidence_score"]
        row.v2_recommended_channel = result["recommended_channel"]
        row.v2_steps = result["steps"]
        row.v2_summary = result["summary"]
        # 表示用（共有）公式サイトも共通検証を通して非破壊更新する。既存の正当な確定値は
        # 上書きせず、rejected 候補（短縮URL/SNS/platform 等）は採用しない。
        shared_official, _ = cds.vet_official_site(
            result["official_site_url"], maker_name=project.maker_name,
            product_title=project.title,
            direct_linked=(result["official_site_source"] == "project_website"),
            current=row.official_site_url)
        # 検証済み値で置換（正当な既存は維持、stale な誤採用は除去）。メール/フォーム/
        # SNS は別カラムなので消えない。
        row.official_site_url = shared_official
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("contact discovery v2 failed (project=%s): %s", project.id, exc)
        row.v2_researched = True
        row.v2_researched_at = now
        row.v2_status = "failed"
        row.v2_error = str(exc)[:4000]

    db.commit()
    db.refresh(row)
    return row
