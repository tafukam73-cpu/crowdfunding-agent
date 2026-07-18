"""営業先連絡先探索の業務ロジック。

公式サイト・問い合わせページ・SNS から、メールアドレス・問い合わせフォーム・
SNS リンクを収集する。クロールは安全のため上限・タイムアウト・重複排除・
robots.txt 配慮つき。取得失敗してもアプリは落とさず status=failed で保存する。

抽出（extract_*）とスコアリング（score_email）は純粋関数として分離し、
HTML を与えればネットワーク無しで検証できるようにしている。
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urljoin, urlparse

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
from app.services import crm_service, source_ownership, usage_service
from app.services.email_validation import (
    business_email_reason,
    email_confidence,
    is_valid_business_email,
)

logger = logging.getLogger("contact_discovery")

# --- 安全設計のパラメータ ---
MAX_URLS = 20               # 最大探索 URL 数（既定 20）
FETCH_TIMEOUT = 8.0         # 1 ページのタイムアウト（秒）
FETCH_RETRIES = 0           # 失敗時のレスポンスを速くするためリトライしない
RATE_LIMIT_SECONDS = 1.0    # ページ間隔（過度なアクセスを避ける）
# second-pass（発見済み maker 自ドメインの contact/about/support リンクの追跡）上限。
# 公式サイトがロケール別ページ（/us/contact 等）にメールを置くケースを拾うための追加取得。
# 総 MAX_URLS も別途尊重するので実際の追加はこれと残枠の小さい方。
MAX_SECOND_PASS_URLS = 5
# second-pass で追跡するパス種別（contact/support に加え about も対象）。
_SECOND_PASS_HINTS = ("contact", "support", "inquiry", "inquiries",
                      "customer-service", "about")


def _is_second_pass_url(url: str) -> bool:
    """second-pass で追跡すべき maker 自ドメインの contact/about/support リンクか。"""
    path = urlparse(url).path.lower()
    return any(h in path for h in _SECOND_PASS_HINTS)

# 公式サイト内で当たりにいく代表パス（Contact Intelligence で拡張）
KNOWN_PATHS = [
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/support",
    "/pages/contact",
    "/pages/contact-us",
    "/pages/about",
    "/pages/about-us",
    "/pages/support",
    "/wholesale",
    "/stockists",
    "/retailers",
    "/imprint",
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
# PDF リンクのうち営業に有用そうなものを示す語（要件6：対象を拡張）
PDF_KEYWORDS = (
    "catalog", "catalogue", "media", "press", "distributor", "wholesale",
    "brand", "deck", "company", "profile", "lookbook", "linesheet", "line-sheet",
    "brochure", "manual", "presskit", "press-kit", "mediakit", "media-kit",
    "pitch", "pitchdeck", "investor", "reseller", "partner", "privacy", "terms",
    "factsheet", "fact-sheet", "onepager", "one-pager",
)

# ReDoS 対策：local-part は RFC 5321 の 64 文字上限で量指定子を閉じ、直前に
# 「local-part 文字でない」ことを要求する negative lookbehind を置く。これにより
# ①各開始位置の後戻りが定数（≤64）に収まり ②巨大な連続トークン（base64 sourcemap 等、
# 215k 文字）でも lookbehind がトークン途中の開始を即座に弾くため findall が O(n) になる
# （従来は開始位置ごとに全長を舐め直して O(n^2) → 60 秒以上 CPU を占有していた）。
# ドメインも 253 文字・TLD 24 文字で上限し、後戻りを有界化する。
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}"
)
MAILTO_RE = re.compile(r"""mailto:([^"'>?\s]+)""", re.IGNORECASE)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# ---- 難読化メールのデコード（改善: 実発見率向上） ----
# サイトはスパム避けにメールを難読化する。実サイト調査で最多だったのは Cloudflare の
# Email Protection（data-cfemail の 16 進 XOR）で、次いで [at]/(at)/＠/[dot] などの
# テキスト難読化。これらを復号して素の user@domain へ戻し、通常の抽出に載せる。
_CF_ATTR_RE = re.compile(r'data-cfemail="([0-9a-fA-F]{8,})"')
_CF_HREF_RE = re.compile(r'/cdn-cgi/l/email-protection#([0-9a-fA-F]{8,})')

# テキスト難読化：local (at) domain (dot) tld。誤検出を避けるため最終的に EMAIL_RE で
# 妥当性を検証し、除外フィルタも通す。
_OBF_AT = (
    r"(?:\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|＠|&#0*64;|&commat;)\s*"
    r"|\s+at\s+)"
)
_OBF_DOT = (
    r"(?:\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|&#0*46;)\s*"
    r"|\s+dot\s+)"
)
_OBF_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9._%+\-]{1,64})"
    + _OBF_AT
    + r"([A-Za-z0-9.\-]{1,63}(?:" + _OBF_DOT + r"[A-Za-z0-9.\-]{1,63}){0,10}"
    + r"(?:" + _OBF_DOT + r"|\.)[A-Za-z]{2,24})",
    re.IGNORECASE,
)
_OBF_AT_SUB = re.compile(_OBF_AT, re.IGNORECASE)
_OBF_DOT_SUB = re.compile(_OBF_DOT, re.IGNORECASE)

# ゼロ幅文字（スパム避けに local/domain の間へ挿入される不可視文字）。抽出前に除去する。
_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None)

# span/タグ分割メール：`user</span>@<span>brand.io` のように local と @、@ と domain の
# 間に HTML タグ・エンティティ・空白が挟まる難読化を復元する（実サイトで頻出）。
# タグ列は短く上限し、無関係テキストの誤結合を避ける。
_SPLIT_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])"
    r"([A-Za-z0-9._%+\-]{1,64})"
    r"(?:<[^>]{0,60}>|&[a-zA-Z]{1,10};|&#0*(?:64|46);|\s){0,8}"
    r"@"
    r"(?:<[^>]{0,60}>|\s){0,8}"
    r"([A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24})"
)


def _strip_zero_width(s: str) -> str:
    return (s or "").translate(_ZERO_WIDTH)


# 1 回のメール抽出で正規表現に渡す入力の絶対上限（文字数）。実ページ HTML は通常
# 数十万文字。base64 を含む巨大ページでも走査コストを有界化するための最終防波堤。
MAX_EMAIL_SCAN_CHARS = 2_000_000
# メールになり得ない「巨大トークン塊」。区切り（'.' '@' 空白等）を含まない
# [A-Za-z0-9+/=]（base64 / hex / 連続英数字）が 80 文字以上連続する塊は、RFC 上限
# （local-part 64・ドメインラベル 63）を超えるため決してメールにならない。事前に
# 空白へ潰して sourcemap / インライン base64 の走査コストを消す（正規メールは無傷）。
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=]{80,}")


def prescrub_for_email(text: str) -> str:
    """メール抽出用に入力を整える：巨大 base64/hex/連続トークン塊を除去し長さを上限。

    メールは局所的（local ≤64・label ≤63・'@' と '.' で区切られる）。区切りを含まない
    80 文字以上の連続塊はメールになり得ないので空白へ潰す。これで Indiegogo 実ページの
    215k 文字 base64 sourcemap のような入力でも regex の走査が爆発しない。正規メールは
    '@' と '.' を含むため塊にならず、この処理の影響を受けない。
    """
    if not text:
        return ""
    text = _LONG_TOKEN_RE.sub(" ", text)
    if len(text) > MAX_EMAIL_SCAN_CHARS:
        text = text[:MAX_EMAIL_SCAN_CHARS]
    return text


def _cf_decode(hexstr: str) -> str:
    """Cloudflare Email Protection の 16 進文字列を復号する（先頭バイトが XOR 鍵）。"""
    try:
        key = int(hexstr[:2], 16)
        raw = bytes(int(hexstr[i:i + 2], 16) ^ key for i in range(2, len(hexstr), 2))
        s = raw.decode("utf-8", "ignore")
        return s if "@" in s else ""
    except Exception:  # noqa: BLE001  不正な hex は無視
        return ""


# JavaScript の文字列連結でメールを組み立てる難読化（"info" + "@" + "brand.com" /
# 'info' + '@' + 'brand' + '.' + 'com' 等）。連結された引用符列を検出して結合する。
_JS_CONCAT_SEQ_RE = re.compile(
    r"""(['"][^'"]{0,64}['"](?:\s*\+\s*['"][^'"]{0,64}['"]){1,50})"""
)
# 引用符閉じ + 連結演算子 + 引用符開き（"..." + "..." の“のり”部分）
_JS_CONCAT_GLUE_RE = re.compile(r"""['"]\s*\+\s*['"]""")


def _js_concat_emails(html: str) -> list[str]:
    """JS 文字列連結で組み立てたメールを復元する（連結列を結合して @ を含むものだけ）。"""
    out: list[str] = []
    for seq in _JS_CONCAT_SEQ_RE.findall(html or ""):
        joined = _JS_CONCAT_GLUE_RE.sub("", seq)  # "a" + "b" -> "ab"
        joined = joined.strip("'\"")
        joined = re.sub(r"\s+", "", joined)
        if "@" in joined:
            out.append(joined)
    return out


def deobfuscate_emails(html: str) -> list[str]:
    """HTML から難読化されたメールを復号して素アドレスの一覧で返す（重複排除なし）。

    - Cloudflare Email Protection（data-cfemail / email-protection#hex）
    - テキスト難読化（support [at] company [dot] com / ＠ / &#64; 等）
    - JavaScript 文字列連結（"info" + "@" + "brand.com" 等）
    妥当性は呼び出し側（extract_emails）で EMAIL_RE + 除外フィルタにより最終検証する。
    """
    out: list[str] = []
    text = prescrub_for_email(_strip_zero_width(html or ""))
    for hx in _CF_ATTR_RE.findall(text) + _CF_HREF_RE.findall(text):
        dec = _cf_decode(hx)
        if dec:
            out.append(dec)
    for local, domain in _OBF_EMAIL_RE.findall(text):
        cand = local + "@" + _OBF_DOT_SUB.sub(".", domain)
        cand = re.sub(r"\s+", "", cand)
        out.append(cand)
    # span/タグ分割メールの復元（local と domain を直結する）。
    for local, domain in _SPLIT_EMAIL_RE.findall(text):
        out.append(local + "@" + domain)
    out.extend(_js_concat_emails(text))
    return out

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


# source_ownership の分類のうち「ドメインだけで常に第三者と確定でき、maker の営業
# メールになり得ない」クラス。email_exclusion_reason で一括除外する（unknown /
# personal_email / maker_* は含めない＝ドメイン名だけで全消ししない）。
_THIRD_PARTY_EMAIL_CLASSES = frozenset({
    "crowdfunding_platform",
    "crowdfunding_marketing_service",
    "agency",
    "url_shortener",
    "messenger",
    "retailer",
})


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

    # 第三者ドメイン（運営/販促支援/代理店/短縮URL/メッセンジャー/小売）は maker の
    # 営業メールになり得ない。source_ownership の deny-list 分類で一括除外する。
    # ここでは ctx を渡さない＝「ドメインだけで常に第三者と確定できるクラス」のみを
    # 対象にし、maker になり得る unknown / personal は消さない（無条件全消しを避ける）。
    third_party = source_ownership.classify_domain(addr)
    if third_party.ownership_class in _THIRD_PARTY_EMAIL_CLASSES:
        return f"third_party_owner:{third_party.ownership_class}:{domain}"

    # 除外ドメイン（完全一致 / サブドメイン）
    for d in EXCLUDED_EMAIL_DOMAINS:
        if _domain_matches(domain, d):
            return f"excluded_domain:{d}"

    # ダミー / プレースホルダー / 形式不正（example / test / dummy / sample など）。
    # 共通バリデーション（email_validation）に委譲して一元管理する。no-reply は既存の
    # auto_reply_local_part 分岐で扱うため、ここでは dummy / format 系のみ採用する。
    shared_reason = business_email_reason(addr)
    if shared_reason and shared_reason.startswith(
        ("dummy_domain", "dummy_local", "format", "asset_file")
    ):
        return shared_reason

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
    # Contact Intelligence v3：公式サイト再帰クロールで見つけたメール
    for e in (getattr(row, "recursive_emails", None) or []):
        if isinstance(e, dict):
            add(e.get("email"), e.get("score", 0), e.get("email_owner"), e.get("sources"))
    for e in (getattr(row, "ai_candidate_emails", None) or []):
        if isinstance(e, dict):
            src = e.get("source_url")
            add(e.get("email"), e.get("score", 0), e.get("email_owner"),
                [src] if src else [])
    # AI Document Reader が読解したメール（confidence をスコアに使う）
    for e in (getattr(row, "doc_reader_emails", None) or []):
        if isinstance(e, dict):
            src = e.get("source_url")
            add(e.get("email"), e.get("confidence", 0), e.get("email_owner"),
                [src] if src else [])
    # AI Search Agent が反復探索で見つけたメール
    for e in (getattr(row, "search_agent_emails", None) or []):
        if isinstance(e, dict):
            src = e.get("source_url")
            add(e.get("email"), e.get("confidence", 0), e.get("email_owner"),
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
        conf = email_confidence(
            email=rec["email"],
            email_owner=rec.get("email_owner"),
            sources=rec.get("sources") or [],
        )
        ranked.append({
            "email": rec["email"],
            "stars": rk["stars"],
            "reason": rk["reason"],
            "category": rk["category"],
            "score": rec["score"],
            "email_owner": rec.get("email_owner"),
            "sources": rec.get("sources") or [],
            # 信頼度（取得元による格付け。UI で「高信頼 / 要確認 / 未検証」表示）
            "confidence": conf["level"],
            "confidence_label": conf["label"],
        })
    ranked.sort(key=lambda c: (c["stars"], c["score"], -len(c["email"])), reverse=True)
    return ranked


# ---------------- 営業可能チャネルの優先順位付け（要件 9・10） ----------------
# 「メールが無い＝終了」ではなく、到達可能な営業チャネルを優先順位付けして提示する。
# priority が小さいほど先に試すべきチャネル（要件 9 の順序）。
# score は営業のしやすさ（要件 10）。
_CHANNEL_PRIORITY = {
    "email": 1,
    "contact_form": 2,
    "linkedin_company": 3,
    "linkedin_person": 4,
    "instagram": 5,
    "facebook": 5,
    "twitter": 6,
    "youtube": 6,
    "tiktok": 6,
    "pinterest": 6,
    "manual_search": 7,
}


def rank_sales_channels(
    *,
    emails: list[dict] | None = None,
    forms: list[str] | None = None,
    linkedin_company_url: str | None = None,
    linkedin_person_url: str | None = None,
    socials: dict | None = None,
    search_queries: list[str] | None = None,
) -> list[dict]:
    """到達可能な営業チャネルを優先順位（要件 9）とスコア（要件 10）で並べて返す。

    メールが見つからなくても探索を打ち切らず、フォーム→LinkedIn 会社→LinkedIn 担当者
    →Instagram/Facebook DM→その他 SNS→手動検索 の順に営業チャネルを提示する。
    各要素は {channel, priority, score, target, reason}。priority 昇順・score 降順。
    """
    emails = emails or []
    forms = forms or []
    socials = socials or {}
    out: list[dict] = []

    # 1. 有効な営業向けメール（confidence_source / sales_stars でスコア調整）
    for e in emails:
        addr = e.get("email") if isinstance(e, dict) else None
        if not addr:
            continue
        src = (e.get("confidence_source") or e.get("tier") or "").lower()
        # 要件 10：公式 Contact/Footer/About は高、Privacy/Terms は低〜中
        if "contact" in src:
            score = 100
        elif "about" in src or "footer" in src:
            score = 88
        elif "legal" in src or "privacy" in src or "terms" in src:
            score = 55
        else:
            score = 70
        # 営業向けローカル部（sales/partnership 等）を加点、非営業（採用/法務）を減点。
        # 取得元（source）のスコアを主軸に保つため加点は控えめ（±8 まで）。
        rk = rank_sales_email(addr, email_owner=e.get("email_owner"))
        score = max(20, min(100, score + (rk["stars"] - 3) * 4))
        out.append({
            "channel": "email", "priority": _CHANNEL_PRIORITY["email"],
            "score": score, "target": addr,
            "reason": f"営業向けメール（{rk['reason']}）",
        })

    # 2. 公式問い合わせフォーム
    for f in forms:
        out.append({
            "channel": "contact_form", "priority": _CHANNEL_PRIORITY["contact_form"],
            "score": 82, "target": f, "reason": "公式問い合わせフォーム",
        })

    # 3. LinkedIn 会社ページ
    if linkedin_company_url:
        out.append({
            "channel": "linkedin_company",
            "priority": _CHANNEL_PRIORITY["linkedin_company"],
            "score": 68, "target": linkedin_company_url,
            "reason": "LinkedIn 会社ページ（メッセージ/求人窓口）",
        })
    # 4. LinkedIn 担当者
    if linkedin_person_url:
        out.append({
            "channel": "linkedin_person",
            "priority": _CHANNEL_PRIORITY["linkedin_person"],
            "score": 72, "target": linkedin_person_url,
            "reason": "LinkedIn 担当者（直接コンタクト可能）",
        })

    # 5〜6. SNS DM（Instagram/Facebook は DM 到達性が高い＝中、その他は低〜中）
    _sns_score = {
        "instagram": 58, "facebook": 55, "twitter": 45,
        "youtube": 38, "tiktok": 42, "pinterest": 35,
    }
    for plat, url in socials.items():
        if not url or plat == "linkedin" or plat not in _CHANNEL_PRIORITY:
            continue
        out.append({
            "channel": plat, "priority": _CHANNEL_PRIORITY[plat],
            "score": _sns_score.get(plat, 35), "target": url,
            "reason": f"{plat} DM",
        })

    # 7. 手動検索候補（最後の手段）
    if not out and search_queries:
        out.append({
            "channel": "manual_search", "priority": _CHANNEL_PRIORITY["manual_search"],
            "score": 15, "target": search_queries[0],
            "reason": "自動発見なし。検索クエリ候補で手動リサーチ",
        })

    out.sort(key=lambda c: (c["priority"], -c["score"]))
    return out


SOCIAL_PATTERNS = {
    "instagram": re.compile(r"instagram\.com", re.IGNORECASE),
    "facebook": re.compile(r"facebook\.com", re.IGNORECASE),
    "twitter": re.compile(r"(?:twitter\.com|x\.com)", re.IGNORECASE),
    "linkedin": re.compile(r"linkedin\.com", re.IGNORECASE),
    "youtube": re.compile(r"(?:youtube\.com|youtu\.be)", re.IGNORECASE),
    "tiktok": re.compile(r"tiktok\.com", re.IGNORECASE),
    "pinterest": re.compile(r"(?:pinterest\.com|pin\.it)", re.IGNORECASE),
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
    html = prescrub_for_email(_strip_zero_width(html or ""))
    for m in MAILTO_RE.findall(html):
        # URL エンコードされた mailto（enc%40brand.io / %20 等）を復号する。
        addr = unquote(m.split("?", 1)[0]).strip()
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
    # 難読化メール（Cloudflare / [at] 等）を復号し、素メールと同じ検証で採用する。
    for dec in deobfuscate_emails(html):
        m = EMAIL_RE.search(dec)
        if not m:
            continue
        addr = m.group(0).strip().strip(".")
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        if email_exclusion_reason(addr, source_site_domain):
            continue
        found.append(addr)
    return found


def extract_emails_with_reasons(
    html: str, source_site_domain: str | None = None
) -> tuple[list[str], list[dict]]:
    """(accepted, excluded) を返す監査用の抽出。

    excluded は [{"email": ..., "reason": ...}]。除外理由（excluded_reason）を追跡可能に
    して、正規のメーカーメールが誤って落ちていないかを検証レポート/UI で確認できるようにする。
    accepted は extract_emails と同じ集合。
    """
    html = prescrub_for_email(_strip_zero_width(html or ""))
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        addr = raw.strip().strip(".")
        key = addr.lower()
        if "@" in addr and key not in seen:
            seen.add(key)
            candidates.append(addr)

    for m in MAILTO_RE.findall(html):
        add(unquote(m.split("?", 1)[0]))
    for m in EMAIL_RE.findall(html):
        add(m)
    for dec in deobfuscate_emails(html):
        mm = EMAIL_RE.search(dec)
        if mm:
            add(mm.group(0))

    accepted: list[str] = []
    excluded: list[dict] = []
    for addr in candidates:
        reason = email_exclusion_reason(addr, source_site_domain)
        if reason:
            excluded.append({"email": addr, "reason": reason})
        else:
            accepted.append(addr)
    return accepted, excluded


def _email_candidates(html: str) -> list[str]:
    """HTML からメール候補（mailto / 素の文字列 / 難読化復元）を重複排除して集める。"""
    html = prescrub_for_email(_strip_zero_width(html or ""))
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        addr = raw.strip().strip(".")
        key = addr.lower()
        if "@" in addr and key not in seen:
            seen.add(key)
            candidates.append(addr)

    for m in MAILTO_RE.findall(html):
        add(unquote(m.split("?", 1)[0]))
    for m in EMAIL_RE.findall(html):
        add(m)
    for dec in deobfuscate_emails(html):
        mm = EMAIL_RE.search(dec)
        if mm:
            add(mm.group(0))
    return candidates


def extract_emails_classified(
    html: str,
    ctx: source_ownership.Ctx | None = None,
    source_site_domain: str | None = None,
    person_names: set[str] | None = None,
) -> dict[str, list[dict]]:
    """メール候補を所有者分類し direct / fallback / rejected / unknown に振り分ける。

    無条件全消しを避け、営業価値のある連絡先を段階的に保持する（要件 Phase 1）:
      - direct   : maker 公式ドメイン / 公式ページ掲載の人物メール（正式連絡先）。
      - fallback : 代理店（agency）・正規販売窓口（distributor）。maker 直通ではないが
                   営業チャネルとして保持（accepted_as_fallback_contact / contact_route 付き）。
      - rejected : 運営(platform)/販促支援(marketing)/短縮URL/メッセンジャー/小売/
                   無関係企業/機能アドレス（noreply 等）/UI 片。fallback にも入れない。
      - unknown  : 所有者証拠が無い候補。低 confidence で保留（削除しない）。
    各要素は source_ownership.classify_email の dict（rejection_reason / evidence 付き）。
    """
    direct: list[dict] = []
    fallback: list[dict] = []
    rejected: list[dict] = []
    unknown: list[dict] = []
    # agency/distributor を指す除外理由は「maker 直通でない」印であり、fallback 保持の
    # 妨げにはしない。それ以外の pre 理由（アセット/ハッシュ/運営/UI 片等）は真の除外。
    _route_pre = ("third_party_owner:agency", "third_party_owner:distributor")
    for addr in _email_candidates(html):
        pre = email_exclusion_reason(addr, source_site_domain)
        info = source_ownership.classify_email(addr, ctx, person_names)
        hard_pre = pre if (pre and not pre.startswith(_route_pre)) else None
        if info["accepted_as_maker_contact"] and not hard_pre:
            direct.append(info)
        elif info["accepted_as_fallback_contact"] and not hard_pre:
            fallback.append(info)
        elif info["ownership_class"] == "unknown" and not hard_pre:
            unknown.append(info)
        else:
            if hard_pre and not info["rejection_reason"]:
                info = {**info, "rejection_reason": hard_pre}
            rejected.append(info)
    return {"direct": direct, "fallback": fallback, "rejected": rejected, "unknown": unknown}


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
    """HTML から SNS リンク（各 1 つ目）を抽出する。

    クラウドファンディング運営自身の公式アカウント（facebook.com/kickstarter,
    instagram.com/zeczec_com 等）は maker の SNS ではないため採用しない（Phase 6）。
    """
    socials: dict[str, str] = {}
    for link in extract_links(html, base_url):
        if _SOCIAL_EXCLUDE.search(link):
            continue
        if source_ownership.is_platform_self_social(link):
            continue  # 運営（KS/Indiegogo/Zeczec/Wadiz）自身のアカウント＝FP
        for platform, pat in SOCIAL_PATTERNS.items():
            if platform in socials:
                continue
            if pat.search(link):
                socials[platform] = link
    return socials


# フォームのユーティリティ/非連絡パス（営業に使えない）。login/検索/購読/カート等。
_NON_CONTACT_FORM_HINTS = (
    "/login", "/signin", "/sign-in", "/register", "/signup", "/sign-up",
    "/account", "/cart", "/checkout", "/search", "/newsletter", "/subscribe",
    "/password", "/wishlist", "/basket", "/my-account", "/auth/",
)
# フォーム候補の canonical 優先順位（同一 intent 内で代表 1 本に畳むため）。短く一般的な
# パスを上位に。ここに無いパスは末尾（rank 大）に回す。
_FORM_PATH_RANK = (
    "/contact", "/pages/contact", "/contact-us", "/pages/contact-us",
    "/contactus", "/wholesale", "/pages/wholesale", "/stockists", "/retailers",
    "/distributors", "/support", "/pages/support", "/help",
)


def _form_intent(url: str) -> str:
    """フォームの営業 intent を返す（同一ドメイン内で intent 単位に集約するためのキー）。"""
    p = urlparse(url).path.lower()
    if any(h in p for h in ("wholesale", "stockist", "retailer", "distributor",
                            "reseller", "b2b", "partner")):
        return "wholesale"
    if any(h in p for h in ("support", "/help", "service", "faq")):
        return "support"
    if any(h in p for h in ("contact", "inquiry", "enquir", "about", "reach")):
        return "contact"
    return "other"


def _is_utility_form(url: str) -> bool:
    return any(h in urlparse(url).path.lower() for h in _NON_CONTACT_FORM_HINTS)


def _form_rank_key(url: str, official_domain: str | None) -> tuple:
    path = urlparse(url).path.lower().rstrip("/")
    try:
        prank = _FORM_PATH_RANK.index(path)
    except ValueError:
        prank = len(_FORM_PATH_RANK)
    official_first = 0 if (official_domain and _same_domain(url, official_domain)) else 1
    return (official_first, prank, len(url), url)


def select_maker_forms(
    forms: list[str], official_domain: str | None = None, limit: int = 4
) -> list[str]:
    """フォーム候補から maker 自身の実フォームだけを選ぶ（Phase 2: フォーム precision）。

    除外:
      - クラファン運営(platform)/販促支援(marketing)/短縮URL/メッセンジャー/小売/代理店の
        ドメイン（source_ownership 分類 + is_platform_url）。maker の問い合わせ窓口でない。
      - login/register/search/newsletter/cart 等のユーティリティフォーム（営業に使えない）。
    集約:
      - soft-404/catch-all 200 で量産される /contact, /contact-us, /pages/contact… の重複を、
        (登録ドメイン × intent) 単位で canonical 1 本に畳む（8 本→contact/support 各1 等）。
    公式ドメインのフォームを優先し、最大 limit 本を返す（先頭が primary 候補）。
    """
    kept: dict[tuple[str, str], str] = {}
    for f in forms or []:
        if not f or "@" in f:
            continue
        if is_platform_url(f) or _is_utility_form(f):
            continue
        cls = source_ownership.classify_domain(f).ownership_class
        if cls in ("crowdfunding_platform", "crowdfunding_marketing_service",
                   "url_shortener", "messenger", "retailer", "agency"):
            continue
        key = (source_ownership.registrable_domain(f), _form_intent(f))
        cur = kept.get(key)
        if cur is None or _form_rank_key(f, official_domain) < _form_rank_key(cur, official_domain):
            kept[key] = f
    ordered = sorted(kept.values(), key=lambda u: _form_rank_key(u, official_domain))
    return ordered[:limit]


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


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDF バイト列からテキストを抽出する（pypdf があれば。無ければ空文字）。"""
    if not pdf_bytes:
        return ""
    try:
        import io

        from pypdf import PdfReader  # 遅延 import（未導入なら空を返す）

        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages[:20]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return re.sub(r"\s+", " ", " ".join(parts))
    except Exception:  # noqa: BLE001  pypdf 未導入/破損 PDF は空
        return ""


def extract_from_pdf(url: str, source_site_domain: str | None = None,
                     timeout: float = 12.0) -> dict:
    """PDF（press kit / catalog 等）を取得し、メール・SNS・会社名を抽出する（要件6）。

    pypdf 未導入時はテキストを取れないため空で返す。メールは既存フィルタを通す。
    Returns: {emails:[...], socials:{...}, text_len:int, text:str}
    （text は担当者候補抽出などの追加解析用。先頭 20000 文字まで。）
    """
    out = {"emails": [], "socials": {}, "text_len": 0, "text": ""}
    try:
        import httpx

        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code >= 400:
            return out
        text = extract_pdf_text(resp.content)
    except Exception:  # noqa: BLE001
        return out
    out["text_len"] = len(text)
    if not text:
        return out
    out["emails"] = extract_emails(text, source_site_domain)
    out["socials"] = extract_socials(text, url)
    out["text"] = text[:20000]
    return out


def _is_contact_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(h in path for h in CONTACT_PATH_HINTS)


def _matches_hints(url: str, hints: tuple[str, ...]) -> bool:
    path = urlparse(url).path.lower()
    return any(h in path for h in hints)


def is_dummy_domain(domain: str | None) -> bool:
    """domain が example / dummy / test / localhost 等のプレースホルダーか。

    site: 検索を生成しても無意味（要件 8）なため、ここで判定して弾く。
    ラベル単位で判定するので example-brand.com のような正規ドメインは弾かない。
    """
    from app.services.url_validation import is_valid_business_url

    if not domain:
        return True
    return not is_valid_business_url(f"https://{domain}")


def build_search_queries(maker_name: str | None, official_domain: str | None) -> list[str]:
    """手動検索用の Google 検索クエリ候補を生成する（API は使わない）。

    メールが見つからない時に営業チャネルを広く探すため、会社名/商品名/ドメインを
    組み合わせて 20 種類以上のクエリを生成する（要件 8）。domain が example/dummy/
    test の場合は site: 検索を生成しない（無意味な検索を避ける）。
    """
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    name = (maker_name or "").strip()
    if name:
        # 連絡先・営業窓口・担当者を広く探す（要件 8：最低 20 種類以上）
        for kw in (
            "email", "contact", "founder", "CEO", "partnership", "distributor",
            "wholesale", "export", "press", "support", "press kit", "media kit",
            "LinkedIn",
        ):
            add(f'"{name}" {kw}')
        # SNS プロフィール（DM 経由の営業チャネル）
        for site in ("linkedin.com", "facebook.com", "instagram.com"):
            add(f'site:{site} "{name}"')

    if official_domain and not is_dummy_domain(official_domain):
        add(f'"{official_domain}" contact email')
        for kw in ("email", "contact", "partnership", "wholesale", "distributor"):
            add(f"site:{official_domain} {kw}")
        add(f"site:{official_domain} filetype:pdf")
        add(f"site:{official_domain} distributor filetype:pdf")
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

# 公式サイトとして自動採用してはいけないホスト（要件 Phase B の拒否ルール）。
# 短縮URL・メッセンジャー・リンク集約・販促/プレッジ管理・EC モール・SNS 単体は、
# 「メーカー公式サイト」ではなく、外部リンク候補を得るための中間ノードに過ぎない。
_REJECT_OFFICIAL_HINTS = (
    # 短縮 URL
    "reurl.cc", "bit.ly", "t.co", "tinyurl.", "ow.ly", "buff.ly", "cutt.ly",
    "rebrand.ly", "is.gd", "lnkd.in", "s.id", "han.gl", "vo.la", "pse.is",
    "reurl.", "goo.gl", "rb.gy",
    # メッセンジャー / チャット
    "m.me", "wa.me", "t.me", "line.me", "pf.kakao.com", "open.kakao.com",
    "instagram.com/direct",
    # リンク集約プロフィール
    "linktr.ee", "lnk.bio", "linkin.bio", "campsite.bio", "beacons.ai",
    "allmylinks.", "taplink.", "solo.to", "linkpop.", "lit.link", "litlink.",
    "bio.link", "potofu.me", "profile.link",
    # 販促 / プレッジ管理 / キャンペーンツール
    "kickbooster.", "backerkit.", "prefinery.", "gleam.io", "viral-loops.",
    "pledgemanager.", "backercamp.", "crowdox.",
    # EC モール / マーケットプレイス
    "amazon.", "ebay.", "etsy.", "aliexpress.", "shopee.", "lazada.",
    "rakuten.", "qoo10.", "coupang.", "gmarket.", "11st.", "auction.co.kr",
    "smartstore.naver.com", "shopping.naver.com", "pchome.", "momoshop.",
    "shopping.",
    # アプリストア / レビュー / Wiki
    "apps.apple.com", "play.google.com", "wikipedia.", "crunchbase.",
)


def _host_hits(url: str, hints: tuple[str, ...]) -> str | None:
    """host がヒントに一致するか（部分文字列でなくラベル境界で判定）。

    - "amazon." のように末尾ドット付き＝ラベル一致（host のいずれかのラベルが "amazon"）。
      例: www.amazon.com / amazon.co.jp は該当、"form.media" は該当しない。
    - "m.me" のようにドット付き完全ドメイン＝完全一致 or サブドメイン一致。
      例: m.me / sub.m.me は該当、"form.media" は該当しない（部分文字列で誤判定しない）。
    """
    host = urlparse(url).netloc.lower().split(":")[0]
    labels = host.split(".")
    for h in hints:
        if h.endswith("."):
            if h[:-1] in labels:
                return h
        else:
            if host == h or host.endswith("." + h):
                return h
    return None

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
    """公式サイト候補。プラットフォーム URL（kickstarter/profile 等）やダミー/
    プレースホルダー URL（example.com / dummy / sample / test / localhost 等）なら None。

    これが「公式サイト」を採用する唯一の入口。ここでダミーを弾くことで、
    Contact Discovery / Document Reader / Search Agent / v2 / CRM 反映のすべての経路で
    example.com のようなプレースホルダーが公式サイトとして表示されないようにする。
    """
    from app.services.url_validation import is_valid_business_url

    if not url or not str(url).startswith(("http://", "https://")):
        return None
    if is_platform_url(url):
        return None
    # 短縮URL・メッセンジャー・リンク集約・販促・EC モール・SNS 単体は公式サイトにしない。
    # （従来 official_site_or_none はこの拒否リストを適用しておらず、reurl.cc / m.me /
    #  kickbooster / linktr.ee / marketplace が公式サイトとして通っていた＝誤採用の主因。）
    if _host_hits(url, _NON_OFFICIAL_LINK_HINTS) or _host_hits(url, _REJECT_OFFICIAL_HINTS):
        return None
    # example.com / dummy / sample / test / localhost / 127.0.0.1 等は公式サイトにしない
    if not is_valid_business_url(url):
        return None
    return url


def confirm_official_site(
    url: str | None,
    *,
    maker_name: str | None = None,
    product_title: str | None = None,
    direct_linked: bool = False,
    jsonld_org: str | None = None,
    site_company_name: str | None = None,
) -> dict:
    """公式サイト候補を「証拠つきで」評価する（単一 URL 即採用をやめる）。

    - 拒否ルール（プラットフォーム/短縮URL/SNS/販促/EC/中間ノード）に該当 → rejected。
    - 独立した証拠を 2 つ以上集められたら accepted（自動確定）。証拠が 1 つなら uncertain
      （探索対象には使ってよいが、確定公式サイトとして上書きしない）。
    独立証拠の例：
      * ドメイン名が maker_name/product のトークンと一致
      * サイト内の会社名/JSON-LD Organization 名が maker_name と一致
      * source campaign から直接リンクされていた（direct_linked）
    Returns: {url, normalized_domain, score, decision, evidence[], rejection_reasons[]}
    """
    reasons: list[str] = []
    evidence: list[str] = []
    cleaned = official_site_or_none(url)
    if cleaned is None:
        # なぜ弾かれたかを具体化する
        if not url or not str(url).startswith(("http://", "https://")):
            reasons.append("not_http_url")
        elif is_platform_url(url):
            reasons.append("crowdfunding_platform")
        else:
            hit = _host_hits(url, _NON_OFFICIAL_LINK_HINTS) or _host_hits(
                url, _REJECT_OFFICIAL_HINTS)
            reasons.append(f"non_official_host:{hit}" if hit else "invalid_business_url")
        return {"url": url, "normalized_domain": _domain_of(url or ""), "score": 0,
                "decision": "rejected", "evidence": evidence,
                "rejection_reasons": reasons}

    dom = _domain_of(cleaned)
    dom_token = dom.split(".")[0] if dom else ""
    terms = significant_terms(maker_name or "", product_title or "")

    score = 0
    if dom_token and any(dom_token in t or t in dom_token for t in terms):
        score += 40
        evidence.append(f"domain_matches_maker:{dom_token}")
    if site_company_name and maker_name and (
        set(significant_terms(site_company_name)) & set(significant_terms(maker_name))
    ):
        score += 35
        evidence.append("site_company_name_matches")
    if jsonld_org and maker_name and (
        set(significant_terms(jsonld_org)) & set(significant_terms(maker_name))
    ):
        score += 35
        evidence.append("jsonld_org_matches")
    if direct_linked:
        score += 30
        evidence.append("linked_from_source_campaign")

    # 独立証拠 2 つ以上（or ドメイン一致＋直リンク等）で自動確定。
    if len(evidence) >= 2:
        decision = "accepted"
    elif len(evidence) == 1:
        decision = "uncertain"
        reasons.append("only_one_evidence")
    else:
        decision = "uncertain"
        reasons.append("no_relevance_evidence")  # 例: 無関係大企業ドメイン(lg.com)
    return {"url": cleaned, "normalized_domain": dom, "score": score,
            "decision": decision, "evidence": evidence,
            "rejection_reasons": reasons}


def vet_official_site(
    candidate: str | None,
    *,
    maker_name: str | None = None,
    product_title: str | None = None,
    direct_linked: bool = False,
    jsonld_org: str | None = None,
    current: str | None = None,
) -> tuple[str | None, dict]:
    """公式サイト候補を **共通ロジックで** 検証し、(保存すべき URL, 判定情報) を返す。

    全 production 保存経路（v2 / Web Research / run_discovery / Search Agent 等）がこれを
    通すことで、経路ごとにバラバラの基準で公式サイトを保存しないようにする。

    - 既に正当な確定公式サイト（current）があれば上書きしない（非破壊）。
    - confirm_official_site が rejected（プラットフォーム/短縮URL/SNS/販促/EC/無関係大企業）
      なら採用しない（None を返す）。
    - accepted（証拠2つ以上）/ uncertain（証拠1つ）は working official site として採用する
      （uncertain は「確定」ではなく候補扱いだが、探索の起点には使う）。
    判定情報は provenance（decision/evidence/rejection_reasons/score）として保存できる。
    """
    info = confirm_official_site(
        candidate, maker_name=maker_name, product_title=product_title,
        direct_linked=direct_linked, jsonld_org=jsonld_org)
    if official_site_or_none(current):
        return current, info  # 既存の正当な公式サイトを尊重（非破壊）
    # 既存が無い or 既存が FP（強化ゲートで弾かれる stale 値）の場合：
    #   - rejected（プラットフォーム/短縮URL/SNS/販促/EC）→ None（FP を残さない）。
    #   - relevance 証拠ゼロ（例: Vitesy 案件の lg.com のような無関係大企業ドメイン）→ None。
    #     確定保存はせず candidate 止まりにする（他候補が無いという理由だけで確定しない）。
    #   - accepted / uncertain かつ証拠が 1 つ以上 → 採用（確定 official として保存可）。
    if info["decision"] == "rejected":
        return None, info
    if not info["evidence"]:
        info.setdefault("rejection_reasons", []).append("insufficient_evidence")
        return None, info
    return info["url"], info


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
        # アンカーテキストが URL/ドメイン自体（例: "lunohelmet.com" や
        # "https://lunohelmet.com"）の場合、その外部サイトが本人の公式サイトである
        # 強いシグナル（Kickstarter のクリエイタープロフィールの Website 表記など）。
        txt_host = (
            txt.replace("https://", "").replace("http://", "").replace("www.", "")
            .strip().strip("/")
        )
        if txt_host and (txt_host == dom or txt_host.split("/")[0] == dom):
            score += 45
        if score >= 40 and score > best_score:
            best_score = score
            best = f"{urlparse(absu).scheme}://{urlparse(absu).netloc}"
    # <a> タグで見つからない場合、Kickstarter 等の埋め込み JSON
    # （"websites":[{"url":...}]）からも公式サイトを探す（要件）。
    if best is None:
        emb = official_from_websites(extract_embedded_websites(html))
        if emb:
            return emb
    return best


# ---------------- 埋め込み JSON の websites 配列（Kickstarter 等） ----------------
# Kickstarter のプロジェクトページはクリエイターの公式サイトを <a> ではなく、
# HTML エンティティ化された埋め込み JSON の "websites":[{"url":"https://..."}] に
# 持つことがある。これをデコードして抽出する。

# 公式サイトに採用しないインフラ/解析/CDN/決済ホスト（要件 4）。
_INFRA_HOST_HINTS = (
    "kck.st", "ksr.io", "ksr-static", "ksr-ugc", "cloudfront", "akamai",
    "stripe.", "js.stripe", "segment.", "siftscience", "sk-diagnostics",
    "tiktok.", "doubleclick", "googletagmanager", "google-analytics",
    "gstatic", "mouseflow", "transcend-cdn", "simpli.fi", "trkn.us",
    "redditstatic", "onetrust", "cookielaw", "fbcdn", "adsystem",
    "ogp.me", "schema.org", "w3.org", "fonts.", "cdn.",
)

_WEBSITES_ARRAY_RE = re.compile(r'"websites"\s*:\s*\[(.*?)\]', re.DOTALL)
_URL_IN_JSON_RE = re.compile(r'https?://[^\s"\'\\<>]+')


# --- 公式サイト候補の identity 照合（Phase 3 Step A: over-judgment FP 抑制）---
# 「同名だが別業種」を判別するための業種語（クラファンのハードウェア/雑貨メーカーが
# これらの業態である可能性は低い）。同名一致のみで product 語が無く、これらを含む場合は
# 別法人と判断する（例: stardome.com = "Stardome Comedy Club"）。固定リストのみに依存せず、
# maker/brand/product 語との一致・不一致と組み合わせて判定する。
_OFFICIAL_CATEGORY_CONFLICT = frozenset({
    "comedy", "club", "nightclub", "restaurant", "cafe", "bar", "pub", "bistro",
    "hotel", "motel", "resort", "church", "temple", "mosque", "school", "academy",
    "university", "college", "clinic", "hospital", "dental", "dentist", "law",
    "attorney", "lawyer", "realty", "realtor", "insurance", "casino", "theatre",
    "theater", "cinema", "museum", "gym", "fitness", "salon", "spa", "bakery",
    "brewery", "winery", "farm", "ranch", "records", "florist", "plumbing",
})
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_OG_SITE_NAME_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE)
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', re.IGNORECASE)
_LDJSON_BLOCK_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)


def _walk_org_website(node, out: dict) -> None:
    """JSON-LD を再帰走査し Organization / WebSite の name / url を集める。"""
    if isinstance(node, list):
        for it in node:
            _walk_org_website(it, out)
        return
    if not isinstance(node, dict):
        return
    types = node.get("@type")
    tset = {types} if isinstance(types, str) else set(types or [])
    if {"Organization", "Corporation", "LocalBusiness", "Brand"} & tset:
        nm = node.get("name")
        if isinstance(nm, str) and nm.strip():
            out["organization_names"].append(nm.strip())
        u = node.get("url")
        if isinstance(u, str) and u.strip():
            out["urls"].append(u.strip())
    if "WebSite" in tset:
        nm = node.get("name")
        if isinstance(nm, str) and nm.strip():
            out["names"].append(nm.strip())
        u = node.get("url")
        if isinstance(u, str) and u.strip():
            out["urls"].append(u.strip())
    for k in ("@graph", "publisher", "author", "brand", "mainEntity"):
        if k in node:
            _walk_org_website(node[k], out)


def extract_site_identity(html: str | None, final_url: str | None = None) -> dict:
    """公式サイト候補ページから identity を安全に抽出する（Phase 3 Step A）。

    Returns: {names, organization_names, urls, canonical_url, registered_domain,
              final_url}。JSON-LD が壊れていても例外で全体を止めない。
    """
    out = {"names": [], "organization_names": [], "urls": [],
           "canonical_url": None, "registered_domain": None, "final_url": final_url}
    html = html or ""
    t = _TITLE_RE.search(html)
    if t:
        title = _html.unescape(re.sub(r"\s+", " ", _TAGSTRIP_RE.sub("", t.group(1)))).strip()
        if title:
            out["names"].append(title)
    for og in _OG_SITE_NAME_RE.findall(html):
        og = _html.unescape(og).strip()
        if og:
            out["names"].append(og)
    canon = _CANONICAL_RE.search(html)
    if canon:
        out["canonical_url"] = canon.group(1).strip()
        out["urls"].append(canon.group(1).strip())
    for block in _LDJSON_BLOCK_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue  # 壊れた JSON-LD は無視（全体を止めない）
        try:
            _walk_org_website(data, out)
        except Exception:  # noqa: BLE001  想定外の構造も無視
            continue
    ref = out["canonical_url"] or final_url
    if ref:
        try:
            from app.services import source_ownership as _so
            out["registered_domain"] = _so.registrable_domain(ref)
        except Exception:  # noqa: BLE001
            out["registered_domain"] = None
    return out


def verify_official_candidate(
    candidate_url: str,
    html: str | None,
    final_url: str | None,
    maker_name: str | None,
    terms: set[str] | None,
    campaign_url: str | None = None,
    source_type: str | None = None,
) -> dict:
    """公式サイト候補が maker のものとして妥当か検証する（内部ヘルパ・Phase 3 Step A）。

    **保守的**：明確な不一致（大企業collision / 同名別業種）がある時のみ collision として
    拒否する。identity が不足する場合は既存動作を維持（accept）。

    Returns: {accepted, reason, evidence, collision_detected, confidence}
    """
    from app.services import source_ownership as _so

    reg = _so.registrable_domain(final_url or candidate_url or "")
    terms = set(terms or set())
    name_terms = significant_terms(maker_name or "")
    result = {"accepted": True, "reason": "insufficient_identity_or_ok",
              "evidence": [], "collision_detected": False, "confidence": 50}

    if not reg:
        return result

    # Rule 1: 明確な第三者ドメイン（運営/販促/短縮/メッセンジャー/小売/代理店）は公式でない。
    own = _so.classify_domain(final_url or candidate_url)
    if own.ownership_class in _so.PERSON_SOURCE_DENY_CLASSES:
        return {"accepted": False, "reason": f"third_party:{own.ownership_class}",
                "evidence": [reg], "collision_detected": True, "confidence": 95}

    # Rule 2: 無関係な大企業ドメイン（lg.com / samsung.com 等）は小型メーカー公式でない。
    if reg in _so.MAJOR_UNRELATED_BRANDS:
        return {"accepted": False, "reason": f"major_unrelated_brand:{reg}",
                "evidence": [reg], "collision_detected": True, "confidence": 90}

    # identity 照合（あれば）。無ければ既存動作維持。
    ident = extract_site_identity(html, final_url)
    id_tokens = tokens_from_identity(ident)
    if not id_tokens:
        return result  # identity 不足 → 過剰拒否しない

    shared_name = id_tokens & name_terms
    product_terms = terms - name_terms
    shared_product = id_tokens & product_terms

    # 注: 「identity が maker と一切一致しない → 別法人」という規則は **採用しない**。
    # エラー/チャレンジページ（"Something Went Wrong" / "Access Denied" / "Just a moment"）を
    # 誤って別法人と判定し正規 official を落とす FP 源になるため。拒否は positive-evidence
    # （大企業ドメイン / 同名かつ別業種語）に限定する。identity 不足・弱一致は既存動作を維持。

    # Rule 3: 名前は一致するが product 語が無く、別業種語（comedy/club/restaurant 等）を含む
    # → 同名別法人（例: stardome.com = "Stardome Comedy Club"）。name 一致を必須にするため
    # エラーページ（名前非一致）では発火しない。
    if shared_name and not shared_product and (id_tokens & _OFFICIAL_CATEGORY_CONFLICT):
        return {"accepted": False, "reason": "same_name_different_business",
                "evidence": sorted(id_tokens & _OFFICIAL_CATEGORY_CONFLICT),
                "collision_detected": True, "confidence": 75}

    result["reason"] = "identity_match"
    result["confidence"] = 70 if shared_product else 60
    result["evidence"] = sorted(id_tokens & terms)[:6]
    return result


def tokens_from_identity(ident: dict) -> set[str]:
    """identity dict の names/organization_names を有意トークン集合にする。"""
    out: set[str] = set()
    for nm in (ident.get("names") or []) + (ident.get("organization_names") or []):
        out |= significant_terms(nm)
    return out


def _is_excluded_official_host(url: str) -> bool:
    """公式サイト候補から除外すべきホスト（プラットフォーム/SNS/CDN/解析）か。"""
    host = urlparse(url).netloc.lower()
    if is_platform_url(url):
        return True
    if any(h in host for h in _NON_OFFICIAL_LINK_HINTS):
        return True
    if any(h in host for h in _INFRA_HOST_HINTS):
        return True
    return False


def extract_embedded_websites(html: str) -> list[str] | None:
    """埋め込み JSON の "websites":[...] から URL 一覧を返す。

    - 配列が 1 つも無ければ None（Kickstarter 以外/JSON 無し）。
    - 配列はあるが URL が無ければ [] を返す（= 公式サイト未登録）。
    HTML エンティティ（&quot; など）はデコードしてから解析する（要件 2）。
    """
    if not html:
        return None
    text = _html.unescape(html)
    arrays = _WEBSITES_ARRAY_RE.findall(text)
    if not arrays:
        return None
    urls: list[str] = []
    seen: set[str] = set()
    for arr in arrays:
        for u in _URL_IN_JSON_RE.findall(arr):
            u = u.rstrip('",\\').strip()
            key = u.lower()
            if u and key not in seen:
                seen.add(key)
                urls.append(u)
    return urls


def official_from_websites(urls: list[str] | None) -> str | None:
    """websites 配列の URL から、外部公式サイトの root を 1 つ選ぶ（無ければ None）。

    kickstarter.com / kck.st / stripe / analytics / CDN 等は除外する（要件 4）。
    """
    if not urls:  # None（配列無し）も []（未登録）も公式サイトは無い
        return None
    for u in urls:
        if not u.startswith(("http://", "https://")):
            continue
        if _is_excluded_official_host(u):
            continue
        p = urlparse(u)
        if not p.netloc:
            continue
        return f"{p.scheme}://{p.netloc}"
    return None


def embedded_websites_debug(html: str) -> dict:
    """埋め込み websites 配列のデバッグ情報（UI 表示用）。

    Returns: {present: 配列があるか, count: URL 件数, registered: 公式サイト登録あり}
    """
    urls = extract_embedded_websites(html)
    if urls is None:
        return {"present": False, "count": 0, "registered": False}
    official = official_from_websites(urls)
    return {"present": True, "count": len(urls), "registered": official is not None}


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


# --- httpx-first / Playwright フォールバックの取得基盤（速度と発見率の両立）--------- #
# フォールバックする goto タイムアウト（秒）。チャレンジ通過に必要な最小限に抑える。
PLAYWRIGHT_FALLBACK_TIMEOUT = 10.0
# httpx 本文が「極端に短い」とみなす閾値（バイト）。JS 描画待ちの疑い。
_MIN_HTML_LEN = 500
# 重要 URL（TOP / contact / support / about）は取得失敗時に Playwright を試す。
_IMPORTANT_URL_HINTS = ("contact", "support", "about")
# Cloudflare / bot チャレンジ・JS 必須の典型マーカー（httpx 本文に出たら PW へ）。
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "/cdn-cgi/challenge-platform",
    "cf-browser-verification",
    "attention required",
    "verifying you are human",
    "please enable javascript",
    "enable javascript to",
    "please turn on javascript",
)


def _is_important_url(url: str) -> bool:
    path = (urlparse(url).path or "/").rstrip("/").lower()
    if path in ("", "/"):
        return True
    return any(h in path for h in _IMPORTANT_URL_HINTS)


def _needs_playwright(html: str | None, status: int | None, important: bool) -> bool:
    """httpx の結果から Playwright フォールバックが必要かを判定する。"""
    # 403 / 429：ボット対策・レート制限 → ブラウザで通す
    if status in (403, 429):
        return True
    if not html:
        # 空本文：重要 URL のみ PW（DNS 失敗等の無駄打ちを避ける）
        return important
    low = html.lower()
    # Cloudflare / チャレンジ / JS 必須マーカー
    if any(m in low for m in _CHALLENGE_MARKERS):
        return True
    # 極端に短い本文（JS 描画待ちの疑い）は重要 URL のみ PW
    if len(html.strip()) < _MIN_HTML_LEN and important:
        return True
    return False


class _HybridFetcher:
    """httpx を優先し、必要時のみ Playwright にフォールバックする取得クライアント。

    - 通常サイトは httpx（~0.1s/URL）で高速取得。
    - 403/429・Cloudflare/JS チャレンジ・空/極端に短い本文（重要 URL）でのみ
      Playwright（~2s/URL）にフォールバック。ブラウザは初回フォールバック時に遅延起動。
    - robots.txt 取得（``get``）は httpx を使う。
    """

    def __init__(self) -> None:
        from app.scrapers.http import HttpClient

        self._http = HttpClient(
            rate_limit_seconds=RATE_LIMIT_SECONDS,
            timeout=FETCH_TIMEOUT,
            retries=FETCH_RETRIES,
        )
        self._pw = None  # 遅延起動（必要になるまで Chromium を立ち上げない）
        self._pw_failed = False
        self.playwright_fallback_count = 0
        self.playwright_fallback_urls: list[str] = []

    # robots など単純取得は httpx を使う（discover が client.get を呼ぶ）
    def get(self, url: str, **kw):
        return self._http.get(url, **kw)

    def get_text(self, url: str) -> str:
        return self._http.get_text(url)

    def _ensure_pw(self):
        if self._pw is None and not self._pw_failed:
            try:
                from app.scrapers.fetcher import get_fetcher

                self._pw = get_fetcher(
                    "playwright",
                    rate_limit_seconds=0.0,
                    timeout=PLAYWRIGHT_FALLBACK_TIMEOUT,
                    retries=0,
                )
            except Exception as exc:  # noqa: BLE001  Playwright 未導入等
                logger.warning("playwright fallback init failed: %s", exc)
                self._pw_failed = True
        return self._pw

    def _playwright_fetch(self, url: str) -> str | None:
        pw = self._ensure_pw()
        if pw is None:
            return None
        try:
            html = pw.get_text(url)
            self.playwright_fallback_count += 1
            self.playwright_fallback_urls.append(url)
            logger.info("playwright fallback used: %s", url)
            return html
        except Exception as exc:  # noqa: BLE001  フォールバック失敗は httpx 結果へ
            logger.info("playwright fallback failed (%s): %s", url, exc)
            return None

    def fetch(self, url: str) -> str | None:
        html: str | None = None
        status: int | None = None
        try:
            html = self._http.get_text(url)
            status = getattr(self._http, "last_status", None)
        except Exception as exc:  # noqa: BLE001  1 URL 失敗（403 含む）は継続
            status = getattr(self._http, "last_status", None)
            logger.debug("httpx fetch issue (%s): %s", url, exc)
        if _needs_playwright(html, status, _is_important_url(url)):
            pw_html = self._playwright_fetch(url)
            if pw_html:
                return pw_html
        return html

    def close(self) -> None:
        for c in (self._http, self._pw):
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass


def _default_fetcher():
    """取得関数（url -> html or None）を返す（httpx 優先・必要時のみ Playwright）。

    速度と発見率の両立：通常サイトは httpx で高速取得し、403/429・Cloudflare/JS
    チャレンジ・空/極端に短い本文（重要 URL）でのみ Playwright にフォールバックする。
    """
    hybrid = _HybridFetcher()

    def fetch(url: str) -> str | None:
        return hybrid.fetch(url)

    fetch._client = hybrid  # type: ignore[attr-defined]
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
    # second-pass frontier（発見済み maker 自ドメインの contact/about/support リンク）。
    second_pass: list[str] = []
    second_seen: set[str] = set()

    def _ingest_emails(html: str, url: str) -> None:
        """1 ページから抽出したメールを email_map へ取り込む（既存の抽出・スコア・
        所有者分類・除外をそのまま利用。メイン loop と second-pass で共通化）。"""
        for addr in extract_emails(html, site_domain):
            score, tier = score_email(addr, official_domain)
            owner = classify_email_owner(addr, official_domain, site_domain)
            key = addr.lower()
            rec = email_map.get(key)
            if rec is None:
                email_map[key] = {
                    "email": addr, "score": score, "tier": tier,
                    "email_owner": owner, "sources": [url],
                }
            else:
                if url not in rec["sources"]:
                    rec["sources"].append(url)
                if score > rec["score"]:
                    rec["score"], rec["tier"] = score, tier
    # maker_url がプラットフォームで公式が未確定なら、クラファン/プロフィールページの
    # 本文リンクから外部公式サイトを推定する（要件）。
    terms = significant_terms(project.title, project.maker_name)
    inferred_official: str | None = None
    official_html: str | None = None  # 公式候補 root ページの HTML（identity 照合用）

    def _consider_page_category(u: str) -> None:
        nonlocal press_page, wholesale_page
        if press_page is None and _matches_hints(u, PRESS_HINTS):
            press_page = u
        if wholesale_page is None and _matches_hints(u, WHOLESALE_HINTS):
            wholesale_page = u

    # メイン探索は MAX_URLS のうち second-pass 用の枠を残して打ち切る（総数は MAX_URLS を
    # 超えない）。発見済み contact リンクの追跡（targeted）を盲目的 KNOWN_PATHS 探索より
    # 優先するための予約。second_pass が空なら残枠は使わない（総取得は減るが無駄打ちを避ける）。
    primary_budget = max(1, MAX_URLS - MAX_SECOND_PASS_URLS)
    try:
        for url in urls:
            if len(searched) >= primary_budget:
                break
            if _blocked(url):
                logger.info("skip by robots: %s", url)
                continue
            # example/dummy/test 等のプレースホルダー URL は取得しない（無駄打ち回避）
            if is_dummy_domain(urlparse(url).netloc):
                logger.info("skip dummy url: %s", url)
                continue
            html = fetch(url)
            searched.append(url)
            if official_domain and _same_domain(url, official_domain):
                official_checked = True
                # 公式候補の identity 照合用に root ページ HTML を 1 度だけ保持（Phase 3）。
                if official_html is None and html:
                    official_html = html
            if not html:
                continue

            # メール（プラットフォーム/運営会社のメールは extract_emails で除外済み）
            _ingest_emails(html, url)

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
                    # second-pass frontier：未取得の maker 自ドメイン contact/about/support
                    # を追加（例: ホームからリンクされた /us/contact）。ここでは取得しない。
                    if (_is_second_pass_url(link) and link not in searched
                            and link not in second_seen):
                        second_seen.add(link)
                        second_pass.append(link)

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

        # --- second-pass：発見済み maker 自ドメイン contact/about/support を上限付きで追跡 ---
        # 公式サイトがロケール別ページ（/us/contact 等）にメールを置くケースを拾う。追加は
        # 同一登録ドメイン・非第三者・robots 尊重・dedup・最大 MAX_SECOND_PASS_URLS 件かつ
        # 総 MAX_URLS を超えない。second-pass ページのリンクは再帰的に辿らない（無限再帰禁止）。
        added = 0
        for link in second_pass:
            if added >= MAX_SECOND_PASS_URLS or len(searched) >= MAX_URLS:
                break
            if link in searched or _blocked(link) or is_dummy_domain(urlparse(link).netloc):
                continue
            # 念のため：official と同一登録ドメインのみ・第三者ドメインは対象外。
            if not (official_domain and _same_domain(link, official_domain)):
                continue
            if source_ownership.classify_domain(link).ownership_class in (
                "crowdfunding_platform", "crowdfunding_marketing_service",
                "url_shortener", "messenger", "retailer", "agency",
            ):
                continue
            html = fetch(link)
            searched.append(link)
            added += 1
            if not html:
                continue
            _ingest_emails(html, link)  # 既存の抽出・除外・所有者分類をそのまま適用
            if _is_contact_url(link) and link not in forms:
                forms.append(link)
            for platform, slink in extract_socials(html, link).items():
                socials.setdefault(platform, slink)
    finally:
        if own_fetcher:
            client = getattr(fetch, "_client", None)
            if client is not None:
                client.close()

    pdfs = pdfs[:6]
    emails = sorted(email_map.values(), key=lambda e: e["score"], reverse=True)
    primary_email = emails[0]["email"] if emails else None
    # 第三者フォーム除去＋ドメイン×intent 単位の canonical 集約（Phase 2 フォーム precision）。
    forms = select_maker_forms(forms, official_domain)
    primary_form = forms[0] if forms else None
    # 公式サイト：maker_url（非プラットフォーム）または本文から推定した外部ドメイン。
    # プラットフォーム URL（kickstarter/profile 等）は公式として採用しない。
    official_site_url = official or inferred_official or None
    # 公式候補の identity 照合（Phase 3 Step A）：明確な collision（大企業/同名別業種/第三者）
    # のみ拒否する。identity 不足時は既存動作を維持。判定は既取得 HTML のみ（新規 fetch なし）。
    if official_site_url:
        _v = verify_official_candidate(
            official_site_url, official_html, official_site_url,
            project.maker_name, terms,
            campaign_url=getattr(project, "source_url", None),
            source_type=getattr(project, "source_site", None),
        )
        if _v["collision_detected"]:
            logger.info("official rejected (%s): %s [evidence=%s]",
                        _v["reason"], official_site_url, _v.get("evidence"))
            official_site_url = None
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

    # Playwright フォールバックした URL 数を記録（httpx-first の効果を可視化）。
    pw_fallbacks = 0
    if own_fetcher:
        _hf = getattr(fetch, "_client", None)
        pw_fallbacks = getattr(_hf, "playwright_fallback_count", 0)

    notes_bits = [
        f"searched {len(searched)} url(s)",
        f"{len(emails)} email(s)",
        f"score {score}",
        f"channel {channel}",
    ]
    if pw_fallbacks:
        notes_bits.append(f"{pw_fallbacks} playwright fallback(s)")
    if disallows:
        notes_bits.append(f"{len(disallows)} robots disallow rule(s) respected")
    result["notes"] = ", ".join(notes_bits)
    result["playwright_fallback_count"] = pw_fallbacks
    logger.info(
        "contact discover done: searched=%s emails=%s pw_fallbacks=%s",
        len(searched), len(emails), pw_fallbacks,
    )
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
    """探索を実行して保存する（実行のたびに履歴を追加）。失敗は failed で保存。

    セッション運用：外部探索（discover=HTTP/Playwright）の前に read を確定して接続を
    解放し、外部処理中は DB トランザクションを保持しない。行の作成・保存は外部処理の
    **後** に短いトランザクションで行う（従来は先に INSERT していたため、外部処理中ずっと
    未コミットのトランザクションを開いたままになっていた）。
    """
    research = _latest_research(db, project.id)
    # 外部処理で使う project のスカラーを先に確保（外部処理中の lazy load を避ける）。
    project_id = project.id
    maker_id = project.maker_id
    maker_url = project.maker_url
    maker_name = project.maker_name
    product_title = project.title
    # read を確定し接続をプールへ返却してから外部処理へ入る。
    release_connection(db)

    result: dict | None = None
    error: str | None = None
    try:
        result = discover(project, research, fetch_fn=fetch_fn)
    except Exception as exc:  # noqa: BLE001  失敗は failed として保存
        logger.warning("contact discovery failed (project=%s): %s", project_id, exc)
        error = str(exc)[:4000]

    # 外部処理の後、短いトランザクションで行を作成・保存する。
    row = ContactDiscovery(
        project_id=project_id,
        maker_id=maker_id,
        status=DiscoveryStatus.pending.value,
        # プラットフォーム URL（kickstarter/profile 等）は公式として保存しない
        official_site_url=official_site_or_none(maker_url),
    )
    db.add(row)
    if result is not None:
        row.status = DiscoveryStatus.completed.value
        row.primary_email = result["primary_email"]
        row.primary_contact_form_url = result["primary_contact_form_url"]
        # 公式サイトは共通検証を通す（誤採用を残さない・既存の maker_url 由来を尊重）。
        vetted_official, _ = vet_official_site(
            result["official_site_url"], maker_name=maker_name,
            product_title=product_title, current=row.official_site_url)
        row.official_site_url = vetted_official
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
    else:
        row.status = DiscoveryStatus.failed.value
        row.error = error

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


def release_connection(db: Session) -> None:
    """外部処理（HTTP/Playwright/Claude）の前にトランザクションを commit して、
    DB 接続をプールへ返却する（＝外部処理中は idle in transaction を発生させない）。

    併せて `expire_on_commit=False` にして、commit 後も既ロードの ORM カラム値へ
    アクセスできるようにする（外部処理中の lazy load＝DB アクセスを避けるため）。
    フェーズは「短時間 read →（この関数で）解放 → 外部処理 → 短時間 save/commit」の
    順で書くこと。read が無く未変更でも commit は安全（no-op）。
    """
    db.expire_on_commit = False
    db.commit()


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
    # read（context 構築）が済んだので接続を解放してから外部（Claude）を呼ぶ。
    release_connection(db)
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
    # ダミー / no-reply / 形式不正のメールは CRM に登録しない（要件 9・F）。
    if email and not is_valid_business_email(email):
        logger.info("apply_to_crm: 無効なメールを無視 %s", email)
        email = None

    maker, _created = crm_service.create_from_project(db, project)

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
