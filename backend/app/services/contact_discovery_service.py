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
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.contact_researcher import (
    VALID_AI_CHANNELS,
    ContactResearchContext,
    ContactResearcher,
    get_contact_researcher,
)
from app.models.company_research import CompanyResearch, ResearchStatus
from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus
from app.models.crm import ActivityKind, Contact, SalesActivity
from app.models.project import Project
from app.services import crm_service, usage_service

logger = logging.getLogger("contact_discovery")

# --- 安全設計のパラメータ ---
MAX_URLS = 20               # 最大探索 URL 数（既定 20）
FETCH_TIMEOUT = 8.0         # 1 ページのタイムアウト（秒）
FETCH_RETRIES = 0           # 失敗時のレスポンスを速くするためリトライしない
RATE_LIMIT_SECONDS = 1.0    # ページ間隔（過度なアクセスを避ける）

# 公式サイト内で当たりにいく代表パス（Contact Intelligence で拡張）
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
    # 拡張パス
    "/privacy",
    "/privacy-policy",
    "/terms",
    "/terms-of-service",
    "/legal",
    "/faq",
    "/help",
    "/customer-service",
    "/business",
    "/b2b",
    "/retail",
    "/affiliate",
    "/collaboration",
    "/collaborate",
    "/brand",
    "/our-story",
    "/team",
    "/careers",
    "/press-kit",
    "/media-kit",
]

# コンタクト/問い合わせページと判定するパスの語
CONTACT_PATH_HINTS = ("contact", "support", "inquiry", "inquiries", "customer-service")
# Press / Media ページと判定する語
PRESS_HINTS = ("press", "media", "press-kit", "media-kit", "newsroom")
# Wholesale / Distributor / B2B ページと判定する語
WHOLESALE_HINTS = (
    "wholesale", "distributor", "distribution", "b2b", "retail", "reseller", "business"
)
# PDF リンクのうち営業に有用そうなものを示す語
PDF_KEYWORDS = (
    "catalog", "catalogue", "media", "press", "distributor", "wholesale",
    "brand", "deck", "company", "profile", "lookbook", "linesheet", "line-sheet",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"""mailto:([^"'>?\s]+)""", re.IGNORECASE)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# 画像やアセットに紛れる「メールっぽい文字列」を除外する拡張子
_BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")

# --- 営業に使えないメール候補の除外ルール（要件: 連絡先抽出の精度向上） ---
# エラートラッキング / 監視 / プレースホルダー等のドメイン。完全一致または
# サブドメイン（".sentry.io" など）で一致したら除外する。
# 例: 2c2bbb0...@o35514.ingest.sentry.io（Sentry DSN 由来）はここで弾く。
EXCLUDED_EMAIL_DOMAINS = (
    "sentry.io",
    "ingest.sentry.io",   # sentry.io のサブドメインだが意図を明示
    "sentry-next.com",
    "localhost",
    "example.com",
    "example.org",
    "example.net",
    "test.com",
)

# クラウドファンディング運営会社（プラットフォーム）のドメイン。
# support@ulule.com のような運営側のメールは「営業先メーカー」ではないため
# 営業候補から除外する（email_owner=platform）。
PLATFORM_EMAIL_DOMAINS = (
    "ulule.com",
    "kickstarter.com",
    "indiegogo.com",
    "makuake.com",
    "greenfunding.jp",
    "wadiz.kr",
)

# source_site（収集元プラットフォーム）→ そのプラットフォームのドメイン。
# 案件の source_site と一致するプラットフォームのメールを確実に除外するために使う。
SOURCE_SITE_DOMAINS = {
    "ulule": "ulule.com",
    "kickstarter": "kickstarter.com",
    "indiegogo": "indiegogo.com",
    "makuake": "makuake.com",
    "greenfunding": "greenfunding.jp",
    "wadiz": "wadiz.kr",
}


def _domain_matches(domain: str, target: str) -> bool:
    """domain が target と完全一致、または target のサブドメインか。"""
    return domain == target or domain.endswith("." + target)


def source_site_email_domain(source_site: str | None) -> str | None:
    """source_site に対応するプラットフォームのメールドメインを返す。"""
    if not source_site:
        return None
    return SOURCE_SITE_DOMAINS.get(str(source_site).lower())

# 自動送信アドレスのローカル部（前方一致で除外。"noreply2@" 等も弾く）。
_AUTO_REPLY_PREFIXES = (
    "no-reply",
    "noreply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
)

# 技術系 / 配送系 / 監視系のローカル部（完全一致で除外）。
# info / sales / hello / partnership などの営業に使える宛先は含めない。
_TECHNICAL_LOCAL_PARTS = frozenset(
    {
        # 配送・システム
        "mailer-daemon", "mailerdaemon", "postmaster", "bounce", "bounces",
        "abuse", "root", "daemon", "cron", "nobody", "devnull",
        # 監視・エラートラッキング・自動通知
        "sentry", "sentry-next", "alert", "alerts", "monitoring", "monitor",
        "nagios", "datadog", "pagerduty", "statuspage", "notifications",
    }
)


def _looks_like_hash(local: str) -> bool:
    """ローカル部がハッシュ/トークン風（営業に使えない自動生成）かどうか。

    - 24 文字以上の 16 進数（Sentry DSN の公開鍵 32hex などを含む）
    - 区切り（. _ - +）が無く、数字を多く含む 25 文字以上の英数字トークン
    """
    if re.fullmatch(r"[0-9a-f]{24,}", local):
        return True
    if (
        len(local) >= 25
        and local.isalnum()
        and sum(c.isdigit() for c in local) >= 4
    ):
        return True
    return False


def email_exclusion_reason(
    email: str, source_site_domain: str | None = None
) -> str | None:
    """営業に使えないメール候補なら「除外理由」を返す（使えるなら None）。

    理由は機械可読な文字列（テストで検証できるよう "種別:詳細" 形式）。
    sales@ / partnership@ / hello@ / info@ などの営業向け宛先は除外しない。
    source_site_domain を渡すと、その案件の収集元プラットフォームのメールも除外する。
    """
    addr = (email or "").strip().lower()
    if "@" not in addr:
        return "invalid"
    local, domain = addr.split("@", 1)

    # アセットファイル名がメール風に紛れたもの（example.png 等）
    if domain.endswith(_BAD_EMAIL_SUFFIX):
        return "asset_file"

    # クラウドファンディング運営会社（プラットフォーム）のメールは営業先ではない
    for d in PLATFORM_EMAIL_DOMAINS:
        if _domain_matches(domain, d):
            return f"platform_domain:{d}"
    # source_site と一致するプラットフォーム（静的リストに無くても除外）
    if source_site_domain and _domain_matches(domain, source_site_domain):
        return f"platform_domain:{source_site_domain}"

    # 除外ドメイン（完全一致 / サブドメイン）
    for d in EXCLUDED_EMAIL_DOMAINS:
        if _domain_matches(domain, d):
            return f"excluded_domain:{d}"

    # 自動送信アドレス（no-reply 系）
    if any(local.startswith(p) for p in _AUTO_REPLY_PREFIXES):
        return "auto_reply_local_part"

    # 技術系 / 監視系
    if local in _TECHNICAL_LOCAL_PARTS:
        return f"technical_local_part:{local}"

    # ハッシュ/トークン風ローカル部（Sentry DSN の公開鍵など）
    if _looks_like_hash(local):
        return "hash_local_part"

    return None


def classify_email_owner(
    email: str,
    official_domain: str | None = None,
    source_site_domain: str | None = None,
) -> str:
    """メール候補の所有者を分類する。

    - "platform"   : クラウドファンディング運営会社のドメイン
    - "monitoring" : エラートラッキング/監視/自動送信/ハッシュ風など
    - "maker"      : 営業先メーカーの公式サイトと同一ドメイン
    - "unknown"    : 上記いずれにも当てはまらない
    """
    addr = (email or "").strip().lower()
    if "@" not in addr:
        return "unknown"
    local, domain = addr.split("@", 1)

    # プラットフォーム（運営会社）
    for d in PLATFORM_EMAIL_DOMAINS:
        if _domain_matches(domain, d):
            return "platform"
    if source_site_domain and _domain_matches(domain, source_site_domain):
        return "platform"

    # 監視・技術・自動送信系
    for d in EXCLUDED_EMAIL_DOMAINS:
        if _domain_matches(domain, d):
            return "monitoring"
    if (
        local in _TECHNICAL_LOCAL_PARTS
        or _looks_like_hash(local)
        or any(local.startswith(p) for p in _AUTO_REPLY_PREFIXES)
    ):
        return "monitoring"

    # メーカー公式ドメイン一致
    if official_domain and _domain_matches(domain, official_domain):
        return "maker"

    return "unknown"

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
# 注: no-reply / noreply / donotreply 等の自動送信系は extract_emails の段階で
# 除外するため、ここには含めない（_AUTO_REPLY_PREFIXES を参照）。
LOW_PREFIXES = (
    "support",
    "press",
    "media",
)
SCORE_HIGH, SCORE_MID, SCORE_LOW, SCORE_OTHER = 90, 60, 30, 50

# ---------------- 営業向け連絡先ランキング（5 段階の星評価） ----------------
# ローカル部の接頭辞 → (星, カテゴリ, 理由)。上から順に startswith で照合し、最初に
# 一致したものを採用する（より具体的・営業価値の高いものを上に並べる）。
# 星: 5=最適 / 4=営業窓口 / 3=一般・サポート / 2=広報 / 1=営業対象外。
SALES_RANK_RULES: list[tuple[int, str, str, tuple[str, ...]]] = [
    (
        5, "general_contact",
        "一般問い合わせ窓口。営業の最初の連絡先として最も適切",
        ("hello", "hallo", "bonjour", "contact", "contactus", "contact-us",
         "info", "information", "inquiry", "inquiries", "enquiry", "enquiries",
         "hi", "hey", "ask"),
    ),
    (
        4, "sales",
        "営業・取引窓口（Sales / Partnership / Distribution など）",
        ("sales", "sale", "partnership", "partnerships", "partner", "partners",
         "business", "biz", "bd", "b2b", "distribution", "distributor",
         "distributors", "wholesale", "export", "exports", "international",
         "reseller", "resellers", "oem", "trade", "commercial"),
    ),
    (
        3, "support",
        "サポート/一般窓口（営業にも到達可能だが最適ではない）",
        ("support", "help", "helpdesk", "service", "customer", "care",
         "office", "team", "mail", "admin", "general", "shop", "store", "order"),
    ),
    (
        2, "press",
        "広報・メディア窓口（営業には間接的）",
        ("press", "media", "pr", "marketing", "newsletter", "news",
         "communications", "comms"),
    ),
    (
        1, "non_sales",
        "営業対象外（採用/法務/経理/自動送信など）",
        ("career", "careers", "job", "jobs", "recruit", "recruitment",
         "recruiting", "hr", "humanresources", "cv", "apply", "application",
         "applications", "talent", "hiring", "authority", "authorities",
         "privacy", "gdpr", "dpo", "dataprotection", "compliance",
         "billing", "invoice", "invoices", "payment", "payments", "legal",
         "accounting", "finance", "tax", "abuse", "security", "noreply",
         "no-reply", "donotreply", "postmaster", "webmaster", "mailer-daemon"),
    ),
]
# 接頭辞に一致しない個別アドレス（john@ など担当者の可能性）の既定評価。
_SALES_RANK_DEFAULT = (3, "other", "個別アドレス（担当者の可能性。内容を確認のうえ利用）")


def rank_sales_email(email: str, *, email_owner: str | None = None) -> dict:
    """メールアドレスを「営業のしやすさ」で 1〜5 の星に格付けする（理由つき）。

    ローカル部（@ の前）の接頭辞で判定する純粋関数。例：
      hello@      → ★★★★★  partnership@ → ★★★★  support@ → ★★★
      cv@/apply@/authorities@ → ★  （採用/法務など営業対象外）
    email_owner が "maker"（公式ドメイン）なら理由に補足する（星は変えない）。
    """
    local = (email or "").split("@", 1)[0].strip().lower()
    # 最長一致を採用する（"career" を "care" より優先し、採用系を 1★ に落とす等、
    # 短い汎用接頭辞による誤判定を防ぐ）。
    best_len = -1
    stars, category, reason = _SALES_RANK_DEFAULT
    for s, cat, rsn, prefixes in SALES_RANK_RULES:
        for p in prefixes:
            if (local == p or local.startswith(p)) and len(p) > best_len:
                best_len = len(p)
                stars, category, reason = s, cat, rsn
    if email_owner == "maker" and stars >= 3:
        reason = f"{reason}（公式ドメイン）"
    return {"stars": stars, "category": category, "reason": reason}


def _iter_source_emails(row: "ContactDiscovery") -> list[dict]:
    """ContactDiscovery 行の全ソース（自動抽出/Web/AI）からメール候補を集める。"""
    out: list[dict] = []

    def add(email, score, owner, sources):
        if not email or "@" not in str(email):
            return
        out.append({
            "email": str(email),
            "score": int(score) if isinstance(score, (int, float)) else 0,
            "email_owner": owner,
            "sources": sources or [],
        })

    for e in (row.discovered_emails or []):
        if isinstance(e, dict):
            add(e.get("email"), e.get("score", 0), e.get("email_owner"), e.get("sources"))
    for e in (getattr(row, "web_discovered_emails", None) or []):
        if isinstance(e, dict):
            add(e.get("email"), e.get("score", 0), e.get("email_owner"), e.get("sources"))
    for e in (getattr(row, "ai_candidate_emails", None) or []):
        if isinstance(e, dict):
            src = e.get("source_url")
            add(e.get("email"), e.get("score", 0), e.get("email_owner"),
                [src] if src else [])
    return out


def build_sales_contacts(row: "ContactDiscovery") -> list[dict]:
    """営業推奨順に並べた連絡先ランキングを作る（星→スコア降順、重複排除）。

    自動抽出 / Web 調査 / AI 候補のメールを統合し、運営・監視系を除外して
    rank_sales_email で格付けする。Returns:
      [{email, stars, reason, category, score, email_owner, sources}]
    """
    if row is None:
        return []
    best: dict[str, dict] = {}
    for rec in _iter_source_emails(row):
        owner = rec.get("email_owner")
        if owner in ("platform", "monitoring"):
            continue
        # 営業に使えない候補（運営/監視/no-reply/ハッシュ等）は除外
        if email_exclusion_reason(rec["email"]):
            continue
        key = rec["email"].lower()
        cur = best.get(key)
        if cur is None or rec["score"] > cur["score"]:
            best[key] = rec
    ranked: list[dict] = []
    for rec in best.values():
        rk = rank_sales_email(rec["email"], email_owner=rec.get("email_owner"))
        ranked.append({
            "email": rec["email"],
            "stars": rk["stars"],
            "reason": rk["reason"],
            "category": rk["category"],
            "score": rec["score"],
            "email_owner": rec.get("email_owner"),
            "sources": rec.get("sources") or [],
        })
    ranked.sort(key=lambda c: (c["stars"], c["score"], -len(c["email"])), reverse=True)
    return ranked


SOCIAL_PATTERNS = {
    "instagram": re.compile(r"instagram\.com", re.IGNORECASE),
    "facebook": re.compile(r"facebook\.com", re.IGNORECASE),
    "twitter": re.compile(r"(?:twitter\.com|x\.com)", re.IGNORECASE),
    "linkedin": re.compile(r"linkedin\.com", re.IGNORECASE),
    "youtube": re.compile(r"(?:youtube\.com|youtu\.be)", re.IGNORECASE),
    "tiktok": re.compile(r"tiktok\.com", re.IGNORECASE),
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


def extract_emails(html: str, source_site_domain: str | None = None) -> list[str]:
    """HTML から mailto: と本文テキストのメールアドレスを抽出（重複排除）。

    営業に使えない技術系・監視系・自動送信系・プレースホルダー・ハッシュ風
    （Sentry DSN 由来など）や、クラウドファンディング運営会社（プラットフォーム）の
    アドレスは email_exclusion_reason で除外する。source_site_domain を渡すと、
    その案件の収集元プラットフォームのメールも除外する。
    """
    found: list[str] = []
    seen: set[str] = set()
    for m in MAILTO_RE.findall(html or ""):
        addr = m.split("?", 1)[0].strip()
        key = addr.lower()
        if "@" not in addr or key in seen:
            continue
        seen.add(key)
        if email_exclusion_reason(addr, source_site_domain):
            continue
        found.append(addr)
    for m in EMAIL_RE.findall(html or ""):
        addr = m.strip().strip(".")
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        if email_exclusion_reason(addr, source_site_domain):
            continue
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


def extract_pdf_links(html: str, base_url: str) -> list[dict]:
    """HTML から PDF リンクを抽出する（営業に有用そうなものを優先ラベル付け）。

    PDF 本文は解析しない（MVP）。Returns: [{url, label, relevant}]
    """
    out: list[dict] = []
    seen: set[str] = set()
    for link in extract_links(html, base_url):
        low = link.lower()
        if ".pdf" not in low:
            continue
        if link in seen:
            continue
        seen.add(link)
        relevant = any(k in low for k in PDF_KEYWORDS)
        name = urlparse(link).path.rsplit("/", 1)[-1] or "PDF"
        out.append({"url": link, "label": name, "relevant": relevant})
    # 関連性の高い PDF を先に
    out.sort(key=lambda p: not p["relevant"])
    return out


def _is_contact_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(h in path for h in CONTACT_PATH_HINTS)


def _matches_hints(url: str, hints: tuple[str, ...]) -> bool:
    path = urlparse(url).path.lower()
    return any(h in path for h in hints)


def build_search_queries(maker_name: str | None, official_domain: str | None) -> list[str]:
    """手動検索用の Google 検索クエリ候補を生成する（API は使わない）。"""
    queries: list[str] = []
    name = (maker_name or "").strip()
    if name:
        for kw in ("email", "contact", "partnership", "wholesale", "distributor", "press"):
            queries.append(f'"{name}" {kw}')
    if official_domain:
        queries.append(f'"{official_domain}" contact email')
        queries.append(f"site:{official_domain} email")
        queries.append(f"site:{official_domain} partnership")
        queries.append(f"site:{official_domain} wholesale")
        queries.append(f"site:{official_domain} distributor filetype:pdf")
    return queries


def _same_domain(url: str, domain: str) -> bool:
    return urlparse(url).netloc.lower().endswith(domain)


def _domain_of(url: str | None) -> str:
    if not url:
        return ""
    net = urlparse(url).netloc.lower()
    return net[4:] if net.startswith("www.") else net


# ---------------- 公式サイト判定（プラットフォーム URL を公式として採用しない） ----------------
# クラウドファンディング/集約プラットフォームのドメイン。これらは「企業の公式サイト」
# ではないため official_site_url に採用しない（例: kickstarter.com/profile/xxx）。
NON_OFFICIAL_PLATFORM_DOMAINS = (
    "kickstarter.com",
    "indiegogo.com",
    "ulule.com",
    "makuake.com",
    "camp-fire.jp",
    "campfire.jp",
    "greenfunding.jp",
    "readyfor.jp",
    "wadiz.kr",
    "wadiz.co.kr",
    "gofundme.com",
    "patreon.com",
    "crowdfunder.co.uk",
    "fundrazr.com",
    "machi-ya.jp",
    "machiya.jp",
    "for-good.net",
)

# 公式サイト推定時に除外する SNS / マーケット / 集約サイトのホスト断片。
_NON_OFFICIAL_LINK_HINTS = (
    "facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.",
    "youtu.be", "tiktok.", "pinterest.", "reddit.", "medium.com", "linktr.ee",
    "amazon.", "ebay.", "etsy.", "aliexpress.", "wikipedia.", "crunchbase.",
    "apps.apple.com", "play.google.com",
)

# 「公式サイト」を示すアンカーテキスト（英・仏・日）。
_OFFICIAL_TEXT_HINTS = (
    "official website", "official site", "officialsite", "website", "web site",
    "visit website", "visit site", "our website", "company website",
    "site officiel", "公式サイト", "公式", "ウェブサイト", "homepage", "home page",
    "official", "external link", "visit us", "shop now", "learn more",
)

_ANCHOR_TEXT_RE = re.compile(
    r'<a\s[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*?>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAGSTRIP_RE = re.compile(r"<[^>]+>")


def is_platform_url(url: str | None) -> bool:
    """URL がクラファン/集約プラットフォーム（= 企業の公式サイトではない）か。"""
    if not url:
        return False
    host = _domain_of(url)
    return any(_domain_matches(host, d) for d in NON_OFFICIAL_PLATFORM_DOMAINS)


def official_site_or_none(url: str | None) -> str | None:
    """公式サイト候補。プラットフォーム URL（kickstarter/profile 等）なら None。"""
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    return None if is_platform_url(url) else url


def significant_terms(*texts: str) -> set[str]:
    """ドメイン照合用の有意トークン（3 文字以上の英数字）。"""
    terms: set[str] = set()
    for t in texts:
        for tok in re.findall(r"[a-z0-9]+", (t or "").lower()):
            if len(tok) >= 3:
                terms.add(tok)
    return terms


def extract_official_link(
    html: str, base_url: str, terms: set[str] | None = None
) -> str | None:
    """クラファン/プロフィールページ等の HTML から、外部の公式サイト URL(root) を推定。

    外部リンク（プラットフォーム/SNS/マーケット/集約サイト・自ドメインを除く）のうち、
      - アンカーテキストが「Official Website / Website / 公式サイト / External Link」等
      - ドメイン名がメーカー名/タイトル主要語を含む
    を手掛かりにスコアリングし、十分な根拠があるものだけ返す（無ければ None）。
    """
    base_domain = _domain_of(base_url)
    terms = terms or set()
    best: str | None = None
    best_score = 0
    for href, text in _ANCHOR_TEXT_RE.findall(html or ""):
        href = (href or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(base_url, href).split("#", 1)[0]
        if not absu.startswith(("http://", "https://")):
            continue
        host = urlparse(absu).netloc.lower()
        if is_platform_url(absu):
            continue
        if any(h in host for h in _NON_OFFICIAL_LINK_HINTS):
            continue
        dom = _domain_of(absu)
        if not dom or dom == base_domain:
            continue
        txt = re.sub(r"\s+", " ", _TAGSTRIP_RE.sub("", text)).strip().lower()
        score = 0
        if any(h in txt for h in _OFFICIAL_TEXT_HINTS):
            score += 50
        dom_token = dom.split(".")[0]
        if dom_token and any(t in dom_token or dom_token in t for t in terms):
            score += 40
        if score >= 40 and score > best_score:
            best_score = score
            best = f"{urlparse(absu).scheme}://{urlparse(absu).netloc}"
    return best


# ---------------- Contact Intelligence（評価・推奨） ----------------
def _email_flags(emails: list[dict]) -> tuple[bool, bool, bool]:
    """メールのティア有無（high / mid(=mid,other) / low）を返す。"""
    has_high = any(e["tier"] == "high" for e in emails)
    has_mid = any(e["tier"] in ("mid", "other") for e in emails)
    has_low = any(e["tier"] == "low" for e in emails)
    return has_high, has_mid, has_low


def contactability_score(
    emails: list[dict],
    *,
    has_form: bool,
    socials: dict,
    has_official_site: bool,
) -> int:
    """メールが無くても営業可能性を 0〜100 で評価する（複数は加点・最大100）。"""
    has_high, has_mid, has_low = _email_flags(emails)
    vals: list[int] = []
    if has_high:
        vals.append(95)
    if has_mid:
        vals.append(80)
    if has_low:
        vals.append(60)
    if has_form:
        vals.append(70)
    if socials.get("linkedin"):
        vals.append(60)
    if socials.get("instagram"):
        vals.append(45)
    if socials.get("facebook"):
        vals.append(40)
    if socials.get("twitter"):
        vals.append(40)
    if socials.get("youtube"):
        vals.append(35)
    if not vals:
        return 25 if has_official_site else 5
    return min(100, max(vals) + (len(vals) - 1) * 5)


# 推奨チャネルの優先順位（上から評価）
def recommend_channel(
    emails: list[dict],
    *,
    has_form: bool,
    socials: dict,
    press_page: str | None,
    wholesale_page: str | None,
) -> str:
    has_high, has_mid, has_low = _email_flags(emails)
    if has_high or has_mid or has_low:
        return "email"
    if has_form:
        return "contact_form"
    if socials.get("linkedin"):
        return "linkedin"
    if socials.get("instagram"):
        return "instagram"
    if socials.get("facebook"):
        return "facebook"
    if press_page:
        return "press"
    if wholesale_page:
        return "distributor_page"
    return "manual_research"


def recommend_action(channel: str, result: dict) -> str:
    """推奨チャネルに応じた具体的な次アクション文。"""
    emails = result.get("discovered_emails") or []
    if channel == "email" and emails:
        top = emails[0]
        tier_label = {
            "high": "a partnership/sales-related",
            "mid": "a general (info/contact)",
            "other": "a direct",
            "low": "a support/press",
        }.get(top["tier"], "an")
        return (
            f"{tier_label} email was found ({top['email']}). "
            "Use it as the primary outreach address and mention a Japanese "
            "crowdfunding partnership (Makuake / GreenFunding)."
        )
    if channel == "contact_form":
        url = result.get("primary_contact_form_url") or "the official contact form"
        return (
            f"No email was found. Use the official contact form ({url}) and mention a "
            "Japanese crowdfunding partnership and exclusive distribution interest."
        )
    if channel == "linkedin":
        return (
            "No email or form was found. Reach out via the company LinkedIn page "
            "(connect or message a relevant person), then use the generated search "
            "queries for manual research."
        )
    if channel == "instagram":
        return (
            "No email or form was found. Start with an Instagram DM to the official "
            "account, and run the generated search queries to find a business email."
        )
    if channel == "facebook":
        return (
            "No email or form was found. Try messaging the official Facebook page, "
            "and use the generated search queries for manual research."
        )
    if channel == "press":
        return (
            "Only a press/media page was found. Check it for a press contact, and run "
            "the generated search queries to locate a business email."
        )
    if channel == "distributor_page":
        return (
            "A wholesale/distributor page was found. Follow its instructions for B2B "
            "inquiries, and run the generated search queries for a direct email."
        )
    return (
        "No reliable contact channel was found automatically. Use the generated search "
        "queries to research email / contact form / LinkedIn manually."
    )


_CHANNEL_LABELS = {
    "email": "Email",
    "contact_form": "Official contact form",
    "linkedin": "LinkedIn",
    "instagram": "Instagram DM",
    "facebook": "Facebook message",
    "press": "Press / Media page",
    "distributor_page": "Wholesale / Distributor page",
    "pdf": "Document (PDF)",
}


def build_approach_options(
    result: dict,
    *,
    forms: list[str],
    socials: dict,
    press_page: str | None,
    wholesale_page: str | None,
    pdfs: list[dict],
) -> list[dict]:
    """営業アプローチ候補（スコア降順）を組み立てる。"""
    opts: list[dict] = []
    for e in result.get("discovered_emails") or []:
        opts.append({
            "channel": "email",
            "label": f"Email ({e['tier']})",
            "url": f"mailto:{e['email']}",
            "score": e["score"],
            "reason": f"{e['tier']}-tier email found on the site",
        })
    if forms:
        opts.append({
            "channel": "contact_form",
            "label": _CHANNEL_LABELS["contact_form"],
            "url": forms[0],
            "score": 70,
            "reason": "Official contact page/form was found",
        })
    if socials.get("linkedin"):
        opts.append({"channel": "linkedin", "label": _CHANNEL_LABELS["linkedin"],
                     "url": socials["linkedin"], "score": 60,
                     "reason": "Official LinkedIn was linked from the website"})
    if socials.get("instagram"):
        opts.append({"channel": "instagram", "label": _CHANNEL_LABELS["instagram"],
                     "url": socials["instagram"], "score": 55,
                     "reason": "Official Instagram profile was linked from website"})
    if socials.get("facebook"):
        opts.append({"channel": "facebook", "label": _CHANNEL_LABELS["facebook"],
                     "url": socials["facebook"], "score": 45,
                     "reason": "Official Facebook page was linked from website"})
    if press_page:
        opts.append({"channel": "press", "label": _CHANNEL_LABELS["press"],
                     "url": press_page, "score": 40,
                     "reason": "Press/Media page was found"})
    if wholesale_page:
        opts.append({"channel": "distributor_page",
                     "label": _CHANNEL_LABELS["distributor_page"],
                     "url": wholesale_page, "score": 50,
                     "reason": "Wholesale/Distributor page was found"})
    for p in pdfs:
        opts.append({"channel": "pdf", "label": f"PDF: {p['label']}",
                     "url": p["url"], "score": 35 if p["relevant"] else 20,
                     "reason": "Relevant PDF found" if p["relevant"] else "PDF found"})
    opts.sort(key=lambda o: o["score"], reverse=True)
    return opts


def build_checklist(
    *,
    official_checked: bool,
    forms: list[str],
    emails: list[dict],
    socials: dict,
    press_page: str | None,
    wholesale_page: str | None,
) -> dict:
    return {
        "official_site_checked": official_checked,
        "contact_page_found": bool(forms),
        "email_found": bool(emails),
        "contact_form_found": bool(forms),
        "instagram_found": bool(socials.get("instagram")),
        "facebook_found": bool(socials.get("facebook")),
        "linkedin_found": bool(socials.get("linkedin")),
        "press_page_found": bool(press_page),
        "wholesale_page_found": bool(wholesale_page),
        "pdf_checked": True,
        "search_queries_generated": True,
    }


def build_evidence_summary(
    emails: list[dict], forms: list[str], socials: dict, action: str
) -> str:
    """次に取る行動が分かる根拠サマリ（日本語）。"""
    labels = {
        "instagram": "Instagram", "facebook": "Facebook", "twitter": "X / Twitter",
        "linkedin": "LinkedIn", "youtube": "YouTube",
    }
    found = []
    if forms:
        found.append("問い合わせフォーム")
    for k, lbl in labels.items():
        if socials.get(k):
            found.append(lbl)
    if emails:
        top = emails[0]
        return f"メール {top['email']}（{top['tier']}）を主要連絡先として利用できます。"
    if found:
        return (
            "メールは見つかりませんでしたが、"
            + "・".join(found)
            + "が見つかりました。"
        )
    return "有効な連絡手段が見つかりませんでした。検索クエリ候補で手動リサーチしてください。"


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
) -> tuple[list[str], str, str]:
    """探索する URL リスト（上限 MAX_URLS）・公式サイト URL・公式ドメインを返す。"""
    seeds = _seed_urls(project, research)
    # maker_url がプラットフォーム（kickstarter/profile 等）なら公式扱いしない。
    # 公式が不明なら、プラットフォームでない seed（research.sources の外部URL等）を使う。
    official = official_site_or_none(project.maker_url) or next(
        (s for s in seeds if _domain_of(s) and not is_platform_url(s)),
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
    return urls[:MAX_URLS], official, official_domain


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
    urls, official, official_domain = _candidate_urls(project, research)
    # 案件の収集元プラットフォーム（運営会社）のメールドメイン。営業候補から除外する。
    site_domain = source_site_email_domain(getattr(project, "source_site", None))
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
    pdfs: list[dict] = []
    pdf_seen: set[str] = set()
    press_page: str | None = None
    wholesale_page: str | None = None
    official_checked = False
    # maker_url がプラットフォームで公式が未確定なら、クラファン/プロフィールページの
    # 本文リンクから外部公式サイトを推定する（要件）。
    terms = significant_terms(project.title, project.maker_name)
    inferred_official: str | None = None

    def _consider_page_category(u: str) -> None:
        nonlocal press_page, wholesale_page
        if press_page is None and _matches_hints(u, PRESS_HINTS):
            press_page = u
        if wholesale_page is None and _matches_hints(u, WHOLESALE_HINTS):
            wholesale_page = u

    try:
        for url in urls:
            if len(searched) >= MAX_URLS:
                break
            if _blocked(url):
                logger.info("skip by robots: %s", url)
                continue
            html = fetch(url)
            searched.append(url)
            if official_domain and _same_domain(url, official_domain):
                official_checked = True
            if not html:
                continue

            # メール（プラットフォーム/運営会社のメールは extract_emails で除外済み）
            for addr in extract_emails(html, site_domain):
                score, tier = score_email(addr, official_domain)
                owner = classify_email_owner(addr, official_domain, site_domain)
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

            # SNS（最初に見つかったものを優先）
            for platform, link in extract_socials(html, url).items():
                socials.setdefault(platform, link)

            # 問い合わせフォーム・カテゴリ判定（現在 URL）
            if _is_contact_url(url) and url not in forms:
                forms.append(url)
            _consider_page_category(url)

            # 同一ドメインのリンクから フォーム / Press / Wholesale を検出
            links = extract_links(html, url)
            for link in links:
                if official_domain and _same_domain(link, official_domain):
                    if _is_contact_url(link) and link not in forms:
                        forms.append(link)
                    _consider_page_category(link)

            # PDF リンク
            for p in extract_pdf_links(html, url):
                if p["url"] not in pdf_seen:
                    pdf_seen.add(p["url"])
                    pdfs.append(p)

            # 公式サイト未確定なら、このページ（クラファン/プロフィール）本文から推定
            if not official and inferred_official is None:
                cand = extract_official_link(html, url, terms)
                if cand:
                    inferred_official = cand
                    official_domain = _domain_of(cand)
    finally:
        if own_fetcher:
            client = getattr(fetch, "_client", None)
            if client is not None:
                client.close()

    pdfs = pdfs[:6]
    emails = sorted(email_map.values(), key=lambda e: e["score"], reverse=True)
    primary_email = emails[0]["email"] if emails else None
    primary_form = forms[0] if forms else None
    # 公式サイト：maker_url（非プラットフォーム）または本文から推定した外部ドメイン。
    # プラットフォーム URL（kickstarter/profile 等）は公式として採用しない。
    official_site_url = official or inferred_official or None
    has_official_site = bool(official_site_url)

    # confidence（後方互換）: メールが最有力。なければフォーム/SNS の有無で段階評価。
    if emails:
        confidence = emails[0]["score"]
    elif primary_form:
        confidence = 40
    elif socials:
        confidence = 20
    else:
        confidence = 0

    result: dict = {
        "official_site_url": official_site_url,
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
    }

    # --- Contact Intelligence ---
    score = contactability_score(
        emails,
        has_form=bool(forms),
        socials=socials,
        has_official_site=has_official_site,
    )
    channel = recommend_channel(
        emails,
        has_form=bool(forms),
        socials=socials,
        press_page=press_page,
        wholesale_page=wholesale_page,
    )
    action = recommend_action(channel, result)
    queries = build_search_queries(project.maker_name, official_domain or None)
    approach = build_approach_options(
        result, forms=forms, socials=socials, press_page=press_page,
        wholesale_page=wholesale_page, pdfs=pdfs,
    )
    checklist = build_checklist(
        official_checked=official_checked, forms=forms, emails=emails,
        socials=socials, press_page=press_page, wholesale_page=wholesale_page,
    )
    evidence = build_evidence_summary(emails, forms, socials, action)

    result.update({
        "contactability_score": score,
        "recommended_channel": channel,
        "recommended_action": action,
        "discovery_checklist": checklist,
        "approach_options": approach,
        "search_queries": queries,
        "evidence_summary": evidence,
        "discovered_pdfs": pdfs,
    })

    notes_bits = [
        f"searched {len(searched)} url(s)",
        f"{len(emails)} email(s)",
        f"score {score}",
        f"channel {channel}",
    ]
    if disallows:
        notes_bits.append(f"{len(disallows)} robots disallow rule(s) respected")
    result["notes"] = ", ".join(notes_bits)
    return result


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
        # プラットフォーム URL（kickstarter/profile 等）は公式として保存しない
        official_site_url=official_site_or_none(project.maker_url),
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
        # Contact Intelligence
        row.contactability_score = result["contactability_score"]
        row.recommended_channel = result["recommended_channel"]
        row.recommended_action = result["recommended_action"]
        row.discovery_checklist = result["discovery_checklist"]
        # PDF はアプローチ候補に含めて保存（専用カラムは設けない）
        row.approach_options = result["approach_options"] or None
        row.search_queries = result["search_queries"] or None
        row.evidence_summary = result["evidence_summary"]
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


# ---------------- AI 連絡先リサーチ ----------------
def _build_research_context(
    project: Project,
    research: CompanyResearch | None,
    row: ContactDiscovery | None,
) -> ContactResearchContext:
    """AI 連絡先リサーチへ渡す入力を Project/Research/Discovery から組み立てる。"""
    company_sources: list[str] = []
    if research and research.sources:
        company_sources = [str(s) for s in research.sources if s]

    existing_emails: list[dict] = []
    excluded: list[dict] = []
    if row and row.discovered_emails:
        for e in row.discovered_emails:
            if not isinstance(e, dict):
                continue
            # 運営会社（platform）のメールは AI にも渡さない
            if e.get("email_owner") == "platform":
                excluded.append(
                    {"email": e.get("email"), "reason": "platform_domain"}
                )
                continue
            existing_emails.append(
                {
                    "email": e.get("email"),
                    "score": e.get("score"),
                    "tier": e.get("tier"),
                    "sources": e.get("sources") or [],
                }
            )

    return ContactResearchContext(
        title=project.title or "",
        description_clean=(project.description_clean or project.description or "")[:2000],
        source_site=project.source_site or "",
        source_url=project.source_url or "",
        maker_name=project.maker_name or "",
        official_site_url=(row.official_site_url if row else None)
        or official_site_or_none(project.maker_url)
        or "",
        company_sources=company_sources,
        searched_urls=(row.searched_urls if row else None) or [],
        search_queries=(row.search_queries if row else None) or [],
        discovered_socials=(row.discovered_socials if row else None) or {},
        primary_contact_form_url=(row.primary_contact_form_url if row else None) or "",
        existing_candidate_emails=existing_emails,
        excluded_emails=excluded,
        platform_domain=source_site_email_domain(
            getattr(project, "source_site", None)
        )
        or "",
    )


def validate_ai_candidate_emails(
    candidates: list,
    *,
    official_domain: str | None,
    source_site_domain: str | None,
) -> list[dict]:
    """AI が返した候補メールを既存フィルタで再検証する（捏造・運営会社を排除）。

    採用条件（すべて満たすもののみ残す）：
      - 出典 URL（source_url）がある（出典の無い＝推測メールは採用しない）
      - email_exclusion_reason が None（運営会社/監視/no-reply/ハッシュ等でない）
    重複は最初の 1 件のみ残す。所有者分類（email_owner）も付与する。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for c in candidates:
        # AiCandidateEmail / dict の両対応
        email = str(getattr(c, "email", None) or (c.get("email") if isinstance(c, dict) else "")).strip()
        source_url = str(
            getattr(c, "source_url", None)
            or (c.get("source_url") if isinstance(c, dict) else "")
            or ""
        ).strip()
        if not email or "@" not in email:
            continue
        key = email.lower()
        if key in seen:
            continue
        # 出典が無い候補は捏造の疑いがあるため採用しない
        if not source_url:
            continue
        # 既存の除外ルールで再検証（運営会社/監視/no-reply/ハッシュ等）
        if email_exclusion_reason(email, source_site_domain):
            continue
        seen.add(key)
        confidence = str(
            getattr(c, "confidence", None)
            or (c.get("confidence") if isinstance(c, dict) else "")
            or ""
        )
        reason = str(
            getattr(c, "reason", None)
            or (c.get("reason") if isinstance(c, dict) else "")
            or ""
        )
        raw_score = getattr(c, "score", None)
        if raw_score is None and isinstance(c, dict):
            raw_score = c.get("score")
        # スコア未指定/不正なら既存スコアリングで補完
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            score, _ = score_email(email, official_domain)
        out.append(
            {
                "email": email,
                "score": score,
                "confidence": confidence,
                "reason": reason,
                "source_url": source_url,
                "email_owner": classify_email_owner(
                    email, official_domain, source_site_domain
                ),
            }
        )
    out.sort(key=lambda e: e["score"], reverse=True)
    return out


def run_ai_research(
    db: Session, project: Project, researcher: ContactResearcher | None = None
) -> ContactDiscovery:
    """AI 連絡先リサーチを実行し、最新の探索結果（ContactDiscovery）に保存する。

    既存の探索結果が無ければ先に HTML 探索を実行して土台を作る。AI 結果は ai_*
    カラムに分離して保存し、自動抽出（primary_email など）は上書きしない。失敗時は
    ai_notes にエラーを記録し、アプリは落とさない。
    """
    researcher = researcher or get_contact_researcher()
    research = _latest_research(db, project.id)
    row = get_latest(db, project.id)
    if row is None:
        # 土台が無ければ先に自動探索を実行（要件の流れ：探索→AI 調査）
        row = run_discovery(db, project)

    official_domain = _domain_of(
        row.official_site_url or official_site_or_none(project.maker_url)
    )
    site_domain = source_site_email_domain(getattr(project, "source_site", None))

    ctx = _build_research_context(project, research, row)
    try:
        result = researcher.research(ctx)

        # AI が返したメールを既存フィルタで再検証（捏造・運営会社を排除）
        validated = validate_ai_candidate_emails(
            result.candidate_emails,
            official_domain=official_domain or None,
            source_site_domain=site_domain,
        )
        validated_lookup = {e["email"].lower() for e in validated}

        # primary_email は「再検証済みの候補に含まれるもの」だけ採用
        primary = result.primary_email
        if primary and primary.lower() not in validated_lookup:
            # AI が出典なしや除外対象を primary にした場合は採用しない
            if email_exclusion_reason(primary, site_domain):
                primary = None
        if not primary and validated:
            primary = validated[0]["email"]

        # 推奨チャネルの正規化
        channel = result.recommended_channel
        if channel not in VALID_AI_CHANNELS:
            channel = "email" if primary else "manual_research"

        row.ai_researched = True
        row.ai_researched_at = datetime.now(timezone.utc)
        row.ai_model = result.model or researcher.name
        row.ai_primary_email = primary
        row.ai_candidate_emails = validated or None
        row.ai_contact_form_url = result.contact_form_url
        row.ai_instagram_url = result.instagram_url
        row.ai_facebook_url = result.facebook_url
        row.ai_linkedin_url = result.linkedin_url
        row.ai_recommended_channel = channel
        row.ai_confidence_score = max(0, min(100, int(result.confidence_score or 0)))
        row.ai_search_queries = result.search_queries or None
        row.ai_sources = result.sources or None
        row.ai_notes = result.notes or None

        usage_service.record_usage(
            db,
            kind="contact_research",
            model=row.ai_model,
            usage=getattr(researcher, "last_usage", None),
            project_id=project.id,
        )
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("ai contact research failed (project=%s): %s", project.id, exc)
        row.ai_researched = True
        row.ai_researched_at = datetime.now(timezone.utc)
        row.ai_notes = f"AI 連絡先リサーチに失敗しました: {exc}"[:4000]

    db.commit()
    db.refresh(row)
    return row


def _crm_note(row: ContactDiscovery | None) -> str:
    """メールが無い場合でも CRM に残す連絡手段メモを組み立てる。"""
    if row is None:
        return "連絡先探索の結果を反映"
    parts: list[str] = []
    if row.recommended_channel:
        parts.append(f"推奨チャネル: {row.recommended_channel}")
    if row.recommended_action:
        parts.append(f"推奨アクション: {row.recommended_action}")
    if row.primary_contact_form_url:
        parts.append(f"問い合わせフォーム: {row.primary_contact_form_url}")
    socials = row.discovered_socials or {}
    for k, v in socials.items():
        parts.append(f"{k}: {v}")
    if row.contactability_score is not None:
        parts.append(f"営業可能性スコア: {row.contactability_score}")
    return " / ".join(parts) or "連絡先探索の結果を反映"


def apply_to_crm(
    db: Session,
    project: Project,
    *,
    email: str | None = None,
    row: ContactDiscovery | None = None,
) -> tuple[int, int | None]:
    """探索結果を CRM に反映する（自動上書きせず追加のみ）。

    - email があれば担当者（Contact）として追加（重複は追加しない）。
    - email が無くても、推奨チャネル・アクション・フォーム・SNS を営業履歴
      （SalesActivity）として記録する。
    メーカー未登録なら案件から作成する。
    Returns: (maker_id, contact_id | None)
    """
    maker = crm_service.create_from_project(db, project)

    # メールが無くても連絡手段を営業履歴として記録（要件 9）
    note = _crm_note(row)
    db.add(
        SalesActivity(
            maker_id=maker.id,
            project_id=project.id,
            kind=ActivityKind.note.value,
            summary=f"連絡先探索を反映: {note}"[:2000],
        )
    )

    contact_id: int | None = None
    if email:
        # 営業推奨順位・理由を算出して CRM に残す（要件：営業推奨順位/理由も保存）
        owner = None
        if row is not None:
            for e in (row.discovered_emails or []) + (
                getattr(row, "web_discovered_emails", None) or []
            ):
                if isinstance(e, dict) and str(e.get("email", "")).lower() == email.lower():
                    owner = e.get("email_owner")
                    break
        rank = rank_sales_email(email, email_owner=owner)
        rank_note = (
            f"営業推奨 {'★' * rank['stars']}{'☆' * (5 - rank['stars'])}"
            f"（{rank['stars']}/5）: {rank['reason']}"
        )
        # 営業履歴にも推奨順位・理由を記録
        db.add(
            SalesActivity(
                maker_id=maker.id,
                project_id=project.id,
                kind=ActivityKind.note.value,
                summary=f"推奨送信先: {email} / {rank_note}"[:2000],
            )
        )

        existing = db.scalar(
            select(Contact).where(
                Contact.maker_id == maker.id, Contact.email == email
            )
        )
        if existing is not None:
            contact_id = existing.id
        else:
            contact = Contact(
                maker_id=maker.id,
                name=f"{maker.name}（探索）",
                role="discovered",
                email=email,
                notes=f"連絡先探索で発見 / {rank_note}",
            )
            db.add(contact)
            db.flush()
            contact_id = contact.id

    db.commit()
    return maker.id, contact_id
