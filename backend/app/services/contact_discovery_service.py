"""営業先連絡先探索の業務ロジック。

公式サイト・問い合わせページ・SNS から、メールアドレス・問い合わせフォーム・
SNS リンクを収集する。クロールは安全のため上限・タイムアウト・重複排除・
robots.txt 配慮つき。取得失敗してもアプリは落とさず status=failed で保存する。

抽出（extract_*）とスコアリング（score_email）は純粋関数として分離し、
HTML を与えればネットワーク無しで検証できるようにしている。
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch, ResearchStatus
from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus
from app.models.crm import Contact
from app.models.project import Project
from app.services import crm_service

logger = logging.getLogger("contact_discovery")

# --- 安全設計のパラメータ ---
MAX_URLS = 12               # 最大探索 URL 数（10〜15 の範囲）
FETCH_TIMEOUT = 8.0         # 1 ページのタイムアウト（秒）
FETCH_RETRIES = 0           # 失敗時のレスポンスを速くするためリトライしない
RATE_LIMIT_SECONDS = 1.0    # ページ間隔（過度なアクセスを避ける）

# 公式サイト内で当たりにいく代表パス
KNOWN_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/support",
    "/pages/contact",
    "/pages/about",
    "/wholesale",
    "/distributors",
    "/distributor",
    "/partnership",
    "/partners",
    "/press",
    "/media",
]

# コンタクト/問い合わせページと判定するパスの語
CONTACT_PATH_HINTS = ("contact", "support", "inquiry", "inquiries", "wholesale")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"""mailto:([^"'>?\s]+)""", re.IGNORECASE)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# 画像やアセットに紛れる「メールっぽい文字列」を除外する拡張子
_BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

# メールアドレスのローカル部によるスコア（要件 4）
HIGH_PREFIXES = (
    "partnership",
    "partner",
    "sales",
    "wholesale",
    "distributor",
    "distribution",
    "business",
    "bd",
    "international",
)
MID_PREFIXES = ("hello", "contact", "info")
LOW_PREFIXES = (
    "support",
    "press",
    "media",
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
)
SCORE_HIGH, SCORE_MID, SCORE_LOW, SCORE_OTHER = 90, 60, 30, 50

SOCIAL_PATTERNS = {
    "instagram": re.compile(r"instagram\.com", re.IGNORECASE),
    "facebook": re.compile(r"facebook\.com", re.IGNORECASE),
    "twitter": re.compile(r"(?:twitter\.com|x\.com)", re.IGNORECASE),
    "linkedin": re.compile(r"linkedin\.com", re.IGNORECASE),
    "youtube": re.compile(r"(?:youtube\.com|youtu\.be)", re.IGNORECASE),
}
# 共有/インテント等は本人アカウントではないので除外
_SOCIAL_EXCLUDE = re.compile(
    r"(sharer|/share|/intent|/dialog|plugins|/tr\?|oauth)", re.IGNORECASE
)


# ---------------- 純粋関数（抽出・スコア） ----------------
def _local_part(email: str) -> str:
    return email.split("@", 1)[0].lower()


def score_email(email: str, official_domain: str | None = None) -> tuple[int, str]:
    """メールアドレスにスコア(0-100)と tier(high/mid/low/other)を付ける。"""
    local = _local_part(email)
    domain = email.split("@", 1)[1].lower() if "@" in email else ""

    if any(local.startswith(p) for p in LOW_PREFIXES):
        score, tier = SCORE_LOW, "low"
    elif any(local.startswith(p) for p in HIGH_PREFIXES):
        score, tier = SCORE_HIGH, "high"
    elif any(local.startswith(p) for p in MID_PREFIXES):
        score, tier = SCORE_MID, "mid"
    else:
        score, tier = SCORE_OTHER, "other"

    # 公式ドメイン一致は信頼度を上げる
    if official_domain and domain and (
        domain == official_domain or domain.endswith("." + official_domain)
    ):
        score = min(100, score + 10)
    return score, tier


def extract_emails(html: str) -> list[str]:
    """HTML から mailto: と本文テキストのメールアドレスを抽出（重複排除）。"""
    found: list[str] = []
    seen: set[str] = set()
    for m in MAILTO_RE.findall(html or ""):
        addr = m.split("?", 1)[0].strip()
        key = addr.lower()
        if "@" in addr and key not in seen:
            seen.add(key)
            found.append(addr)
    for m in EMAIL_RE.findall(html or ""):
        addr = m.strip().strip(".")
        key = addr.lower()
        if key in seen:
            continue
        if key.endswith(_BAD_EMAIL_SUFFIX):
            continue
        seen.add(key)
        found.append(addr)
    return found


def extract_links(html: str, base_url: str) -> list[str]:
    """HTML の href を絶対 URL 化して返す（http/https のみ・重複排除）。"""
    out: list[str] = []
    seen: set[str] = set()
    for href in HREF_RE.findall(html or ""):
        href = href.strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(base_url, href)
        if not absu.startswith(("http://", "https://")):
            continue
        absu = absu.split("#", 1)[0]
        if absu not in seen:
            seen.add(absu)
            out.append(absu)
    return out


def extract_socials(html: str, base_url: str) -> dict[str, str]:
    """HTML から SNS リンク（各 1 つ目）を抽出する。"""
    socials: dict[str, str] = {}
    for link in extract_links(html, base_url):
        if _SOCIAL_EXCLUDE.search(link):
            continue
        for platform, pat in SOCIAL_PATTERNS.items():
            if platform in socials:
                continue
            if pat.search(link):
                socials[platform] = link
    return socials


def _is_contact_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(h in path for h in CONTACT_PATH_HINTS)


def _same_domain(url: str, domain: str) -> bool:
    return urlparse(url).netloc.lower().endswith(domain)


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    net = urlparse(url).netloc.lower()
    return net[4:] if net.startswith("www.") else net


# ---------------- クロール ----------------
def _seed_urls(project: Project, research: CompanyResearch | None) -> list[str]:
    """探索の起点 URL を優先順位順に集める（重複排除）。"""
    seeds: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if u and u.startswith(("http://", "https://")) and u not in seen:
            seen.add(u)
            seeds.append(u)

    # 1. 公式サイト  2. 案件ページ
    add(project.maker_url)
    add(project.source_url)
    # 3. company_research.sources
    if research and research.sources:
        for s in research.sources:
            if isinstance(s, str):
                add(s)
    return seeds


def _candidate_urls(
    project: Project, research: CompanyResearch | None
) -> tuple[list[str], str]:
    """探索する URL リスト（上限 MAX_URLS）と公式ドメインを返す。"""
    seeds = _seed_urls(project, research)
    official = project.maker_url or next(
        (s for s in seeds if _domain_of(s) and "kickstarter" not in s and "indiegogo" not in s),
        "",
    )
    official_domain = _domain_of(official)

    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if u and u not in seen and len(urls) < MAX_URLS:
            seen.add(u)
            urls.append(u)

    for s in seeds:
        add(s)
    # 公式サイトの代表パスを当てにいく
    if official:
        root = f"{urlparse(official).scheme}://{urlparse(official).netloc}"
        for path in KNOWN_PATHS:
            add(root + path)
    return urls[:MAX_URLS], official_domain


def _robots_disallows(client, root: str) -> list[str]:
    """robots.txt の User-agent:* の Disallow パス接頭辞を返す（取得失敗時は空）。"""
    try:
        resp = client.get(urljoin(root, "/robots.txt"))
        text = resp.text
    except Exception:  # noqa: BLE001  robots 取得失敗は配慮対象外として通常探索
        return []
    disallows: list[str] = []
    active = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("user-agent:"):
            active = low.split(":", 1)[1].strip() == "*"
        elif active and low.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                disallows.append(path)
    return disallows


def _default_fetcher():
    """既存 HTTP 基盤を使った取得関数を返す（url -> html or None）。"""
    from app.scrapers.http import HttpClient

    client = HttpClient(
        rate_limit_seconds=RATE_LIMIT_SECONDS,
        timeout=FETCH_TIMEOUT,
        retries=FETCH_RETRIES,
    )

    def fetch(url: str) -> str | None:
        try:
            return client.get_text(url)
        except Exception as exc:  # noqa: BLE001  1 URL 失敗は無視
            logger.info("fetch failed (%s): %s", url, exc)
            return None

    fetch._client = client  # type: ignore[attr-defined]
    return fetch


def discover(
    project: Project,
    research: CompanyResearch | None = None,
    fetch_fn=None,
) -> dict:
    """連絡先探索の本体（DB 非依存）。集計した結果 dict を返す。

    fetch_fn は url->html|None。未指定なら既存 HTTP 基盤を使う（テストでは差し替え）。
    """
    urls, official_domain = _candidate_urls(project, research)
    own_fetcher = fetch_fn is None
    fetch = fetch_fn or _default_fetcher()

    # robots.txt 配慮（公式サイトのみ簡易チェック）
    disallows: list[str] = []
    if own_fetcher and official_domain:
        client = getattr(fetch, "_client", None)
        official = project.maker_url or ""
        if client and official:
            root = f"{urlparse(official).scheme}://{urlparse(official).netloc}"
            disallows = _robots_disallows(client, root)

    def _blocked(u: str) -> bool:
        path = urlparse(u).path or "/"
        return any(path.startswith(d) for d in disallows)

    searched: list[str] = []
    email_map: dict[str, dict] = {}     # email_lower -> {email, score, tier, sources}
    forms: list[str] = []
    socials: dict[str, str] = {}

    try:
        for url in urls:
            if len(searched) >= MAX_URLS:
                break
            if _blocked(url):
                logger.info("skip by robots: %s", url)
                continue
            html = fetch(url)
            searched.append(url)
            if not html:
                continue

            # メール
            for addr in extract_emails(html):
                score, tier = score_email(addr, official_domain)
                key = addr.lower()
                rec = email_map.get(key)
                if rec is None:
                    email_map[key] = {
                        "email": addr,
                        "score": score,
                        "tier": tier,
                        "sources": [url],
                    }
                else:
                    if url not in rec["sources"]:
                        rec["sources"].append(url)
                    if score > rec["score"]:
                        rec["score"], rec["tier"] = score, tier

            # SNS（最初に見つかったものを優先）
            for platform, link in extract_socials(html, url).items():
                socials.setdefault(platform, link)

            # 問い合わせフォーム/コンタクトページ
            if _is_contact_url(url) and url not in forms:
                forms.append(url)
            for link in extract_links(html, url):
                if (
                    official_domain
                    and _same_domain(link, official_domain)
                    and _is_contact_url(link)
                    and link not in forms
                ):
                    forms.append(link)
    finally:
        if own_fetcher:
            client = getattr(fetch, "_client", None)
            if client is not None:
                client.close()

    emails = sorted(email_map.values(), key=lambda e: e["score"], reverse=True)
    primary_email = emails[0]["email"] if emails else None
    primary_form = forms[0] if forms else None

    # confidence: メールが最有力。なければフォーム/SNS の有無で段階評価。
    if emails:
        confidence = emails[0]["score"]
    elif primary_form:
        confidence = 40
    elif socials:
        confidence = 20
    else:
        confidence = 0

    notes_bits = [f"searched {len(searched)} url(s)", f"{len(emails)} email(s)"]
    if disallows:
        notes_bits.append(f"{len(disallows)} robots disallow rule(s) respected")

    return {
        "official_site_url": project.maker_url,
        "primary_email": primary_email,
        "primary_contact_form_url": primary_form,
        "instagram_url": socials.get("instagram"),
        "facebook_url": socials.get("facebook"),
        "twitter_url": socials.get("twitter"),
        "linkedin_url": socials.get("linkedin"),
        "youtube_url": socials.get("youtube"),
        "discovered_emails": emails,
        "discovered_forms": forms,
        "discovered_socials": socials,
        "searched_urls": searched,
        "confidence_score": confidence,
        "notes": ", ".join(notes_bits),
    }


# ---------------- DB 連携 ----------------
def _latest_research(db: Session, project_id: int) -> CompanyResearch | None:
    stmt = (
        select(CompanyResearch)
        .where(
            CompanyResearch.project_id == project_id,
            CompanyResearch.research_status == ResearchStatus.completed.value,
        )
        .order_by(desc(CompanyResearch.created_at), desc(CompanyResearch.id))
        .limit(1)
    )
    return db.scalar(stmt)


def run_discovery(
    db: Session, project: Project, fetch_fn=None
) -> ContactDiscovery:
    """探索を実行して保存する（実行のたびに履歴を追加）。失敗は failed で保存。"""
    research = _latest_research(db, project.id)
    row = ContactDiscovery(
        project_id=project.id,
        maker_id=project.maker_id,
        status=DiscoveryStatus.pending.value,
        official_site_url=project.maker_url,
    )
    db.add(row)
    try:
        result = discover(project, research, fetch_fn=fetch_fn)
        row.status = DiscoveryStatus.completed.value
        row.primary_email = result["primary_email"]
        row.primary_contact_form_url = result["primary_contact_form_url"]
        row.official_site_url = result["official_site_url"]
        row.instagram_url = result["instagram_url"]
        row.facebook_url = result["facebook_url"]
        row.twitter_url = result["twitter_url"]
        row.linkedin_url = result["linkedin_url"]
        row.youtube_url = result["youtube_url"]
        row.discovered_emails = result["discovered_emails"] or None
        row.discovered_forms = result["discovered_forms"] or None
        row.discovered_socials = result["discovered_socials"] or None
        row.searched_urls = result["searched_urls"] or None
        row.confidence_score = result["confidence_score"]
        row.notes = result["notes"]
    except Exception as exc:  # noqa: BLE001  失敗は failed として保存
        logger.warning("contact discovery failed (project=%s): %s", project.id, exc)
        row.status = DiscoveryStatus.failed.value
        row.error = str(exc)[:4000]

    db.commit()
    db.refresh(row)
    return row


def get_latest(db: Session, project_id: int) -> ContactDiscovery | None:
    stmt = (
        select(ContactDiscovery)
        .where(ContactDiscovery.project_id == project_id)
        .order_by(desc(ContactDiscovery.created_at), desc(ContactDiscovery.id))
        .limit(1)
    )
    return db.scalar(stmt)


def apply_to_crm(
    db: Session, project: Project, email: str
) -> tuple[int, int]:
    """探索結果のメールを CRM に反映する（自動上書きせず追加のみ）。

    メーカー未登録なら案件から作成し、その担当者として email を追加する。
    既に同じ email の担当者があれば重複追加しない。
    Returns: (maker_id, contact_id)
    """
    maker = crm_service.create_from_project(db, project)
    existing = db.scalar(
        select(Contact).where(
            Contact.maker_id == maker.id, Contact.email == email
        )
    )
    if existing is not None:
        return maker.id, existing.id

    contact = Contact(
        maker_id=maker.id,
        name=f"{maker.name}（探索）",
        role="discovered",
        email=email,
        notes="連絡先探索で発見",
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return maker.id, contact.id
