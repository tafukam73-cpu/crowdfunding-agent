"""メールアドレスの共通バリデーション（営業に使えるメールかどうかの判定）。

このモジュールは「ダミー / プレースホルダー / 自動送信 / 形式不正」なメールを
確実に弾くための単一の入口を提供する。connect discovery / contact hunter /
web research など、メールを扱うすべての経路がここを通ることで、
example@example.com のようなダミーや no-reply 系が本番 UI に出ないようにする。

主な公開 API:
  - is_valid_business_email(email) -> bool
  - business_email_reason(email)   -> str | None   （無効なら機械可読な理由、有効なら None）
  - email_confidence(...)          -> dict          （信頼度ラベル: 高信頼 / 要確認 など）
  - build_fallback_search_queries(...) -> list[dict]（見つからない時の手動検索導線）
  - OFFICIAL_CONTACT_PATHS         -> tuple          （公式サイトで優先探索するパス）
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

# メール形式（表示・抽出後の最終チェック用の保守的なパターン）。
EMAIL_FORMAT_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+$"
)

# ダミー / プレースホルダーのドメイン「ラベル」（ドット区切りのいずれかが一致で無効）。
# 例: example.com / test.org / dummy.io / sample.net / mail@domain.com を弾く。
# 注意: example-brand.com のような正規ドメインは「example-brand」ラベルなので弾かない。
DUMMY_DOMAIN_LABELS = frozenset(
    {
        "example",
        "test",
        "tests",
        "testing",
        "dummy",
        "sample",
        "samples",
        "placeholder",
        "domain",
        "yourdomain",
        "mydomain",
        "yourcompany",
        "mycompany",
        "company",
        "email",
        "mail",
        "invalid",
        "localhost",
        "none",
        "no",
        "acme",
    }
)

# ローカル部（@ の前）にセグメントとして含まれると無効なトークン。
# 区切り（. _ - + や数字）で分割した各セグメントが一致したら無効にする。
# 例: test@brand.com / example@brand.com / dummy@brand.com / sample.user@brand.com。
# "latest" や "contest" のような正規ローカル部を誤って弾かないようセグメント一致で判定する。
DUMMY_LOCAL_TOKENS = frozenset(
    {
        "example",
        "dummy",
        "sample",
        "test",
        "placeholder",
        "yourname",
        "youremail",
        "firstname",
        "lastname",
    }
)

# 自動送信（返信不可）のローカル部プレフィックス。前方一致で弾く。
NOREPLY_PREFIXES = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "notifications",
    "notification",
)

# 画像・アセットファイル名がメール風に紛れたもの（logo@2x.png など）を弾く拡張子。
_ASSET_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
    ".ico",
)

# 公式サイトで連絡先を優先的に探すパス（要件 5）。
OFFICIAL_CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/contactus",
    "/contact-me",
    "/about",
    "/about-us",
    "/aboutus",
    "/support",
    "/help",
    "/pages/contact",
    "/pages/contact-us",
    "/pages/about",
    "/pages/about-us",
    "/pages/support",
    "/contactez-nous",
    "/nous-contacter",
    "/contacto",
    "/kontakt",
    "/impressum",
    "/privacy",
    "/privacy-policy",
    "/pages/privacy-policy",
    "/legal",
    "/terms",
    "/terms-of-service",
    "/imprint",
)


def _split_local_segments(local: str) -> list[str]:
    """ローカル部を区切り・数字で分割してセグメント一覧を返す（小文字）。"""
    return [s for s in re.split(r"[._+\-0-9]+", local.lower()) if s]


def business_email_reason(email: str | None) -> str | None:
    """営業に使えないダミー/自動送信/形式不正なメールなら理由を返す（使えるなら None）。

    理由は機械可読（"種別:詳細" 形式）。ここでは「捏造・プレースホルダー・no-reply・
    形式不正」だけを対象にする。プラットフォーム/監視系など文脈依存の除外は呼び出し側で行う。
    """
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        return "format:no_at"
    # 表示・抽出後は 1 つの @ のみを想定
    if addr.count("@") != 1:
        return "format:multiple_at"

    local, domain = addr.split("@", 1)
    if not local or not domain:
        return "format:empty_part"

    # アセットファイル名（logo@2x.png など）
    if domain.endswith(_ASSET_SUFFIXES):
        return "asset_file"

    # 形式チェック（TLD を含む妥当なドメインか）
    if not EMAIL_FORMAT_RE.match(addr):
        return "format:invalid"

    labels = domain.split(".")

    # ダミー / プレースホルダーのドメインラベル（example.com / test.org / domain.com …）
    for label in labels:
        if label in DUMMY_DOMAIN_LABELS:
            return f"dummy_domain:{label}"

    # ローカル部のダミートークン（example@ / test@ / dummy@ / sample.user@ …）
    for seg in _split_local_segments(local):
        if seg in DUMMY_LOCAL_TOKENS:
            return f"dummy_local:{seg}"

    # 自動送信（no-reply 系）
    if any(local.startswith(p) for p in NOREPLY_PREFIXES):
        return "noreply"

    return None


def is_valid_business_email(email: str | None) -> bool:
    """営業に使える実在しうるビジネスメールかどうか（ダミー/no-reply/形式不正でない）。"""
    return business_email_reason(email) is None


# --- 信頼度（取得元による格付け。UI で「高信頼 / 要確認 / 未検証」を表示するため） ---
CONFIDENCE_LABELS = {
    "high": "高信頼",
    "medium": "要確認",
    "low": "低信頼",
    "unverified": "未検証",
    "invalid": "無効",
}


def _path_of(url: str | None) -> str:
    try:
        return (urlparse(url or "").path or "").lower()
    except ValueError:
        return ""


def email_confidence(
    *,
    email: str | None = None,
    email_owner: str | None = None,
    sources: list[str] | None = None,
) -> dict:
    """メール候補の信頼度を取得元・所有者から判定する。

    - official_site_contact / official_site_footer 相当（公式ドメイン一致）: high
    - privacy_policy / terms など公式サイトの規約ページ: medium
    - crowdfunding ページ由来: low
    - 取得元不明（推測含む）: unverified
    - ダミー/no-reply: invalid
    戻り値: {"level": <key>, "label": <日本語>}
    """
    if email is not None and not is_valid_business_email(email):
        return {"level": "invalid", "label": CONFIDENCE_LABELS["invalid"]}

    srcs = [s for s in (sources or []) if s]
    paths = [_path_of(s) for s in srcs]

    def _has(*keys: str) -> bool:
        return any(any(k in p for k in keys) for p in paths)

    # 公式ドメイン一致（maker）は最も信頼できる
    if email_owner == "maker":
        return {"level": "high", "label": CONFIDENCE_LABELS["high"]}

    # 公式サイトの contact/about/support ページ由来
    if _has("/contact", "/about", "/support", "/impressum", "/kontakt"):
        return {"level": "high", "label": CONFIDENCE_LABELS["high"]}

    # 規約・プライバシーページ由来
    if _has("/privacy", "/terms", "/legal", "/imprint"):
        return {"level": "medium", "label": CONFIDENCE_LABELS["medium"]}

    # クラウドファンディングページ由来
    if _has("kickstarter.com", "indiegogo.com", "ulule.com", "wadiz.kr", "makuake.com"):
        return {"level": "low", "label": CONFIDENCE_LABELS["low"]}

    if srcs:
        return {"level": "medium", "label": CONFIDENCE_LABELS["medium"]}

    return {"level": "unverified", "label": CONFIDENCE_LABELS["unverified"]}


def build_fallback_search_queries(
    *,
    company_name: str | None = None,
    product_name: str | None = None,
    official_domain: str | None = None,
    founder_name: str | None = None,
) -> list[dict]:
    """メールが見つからない時の「次の手動検索候補」を組み立てる（要件 8）。

    Google / LinkedIn 検索リンクや site: クエリなど、人が続きを探せる導線を返す。
    戻り値: [{"label": 表示名, "type": 種別, "query": クエリ文字列, "url": 検索URL}]
    """
    out: list[dict] = []
    seen: set[str] = set()

    company = (company_name or "").strip()
    product = (product_name or "").strip()
    domain = (official_domain or "").strip().lower().lstrip("@")
    if domain.startswith("www."):
        domain = domain[4:]
    # 公式ドメインが example.com / dummy / sample / test / localhost 等の
    # プレースホルダーなら site: クエリを一切生成しない（要件4）。
    if domain:
        from app.services.url_validation import is_valid_business_url

        if not is_valid_business_url(f"https://{domain}"):
            domain = ""

    def add(label: str, kind: str, query: str, url: str) -> None:
        key = url
        if not query or key in seen:
            return
        seen.add(key)
        out.append({"label": label, "type": kind, "query": query, "url": url})

    def google(q: str) -> str:
        return f"https://www.google.com/search?q={quote_plus(q)}"

    def linkedin(q: str) -> str:
        return f"https://www.linkedin.com/search/results/all/?keywords={quote_plus(q)}"

    if company:
        add("公式サイトを検索", "official_site", f"{company} official site",
            google(f"{company} official site"))
        add("会社名 + email", "google", f"{company} email",
            google(f"{company} contact email"))
        add("会社名 + contact", "google", f"{company} contact",
            google(f"{company} contact"))
        add("LinkedInで会社を検索", "linkedin", company, linkedin(company))

    if product:
        add("商品名 + founder", "google", f"{product} founder",
            google(f"{product} founder contact"))
        if not company:
            add("商品名で公式サイトを検索", "official_site", f"{product} official site",
                google(f"{product} official site"))

    if founder_name:
        add("代表者をLinkedInで検索", "linkedin", founder_name, linkedin(founder_name))

    if domain:
        add("公式ドメイン内のメールを検索", "site_search",
            f"site:{domain} email", google(f"site:{domain} email"))
        add("公式ドメイン内の問い合わせを検索", "site_search",
            f"site:{domain} contact", google(f"site:{domain} contact"))

    return out
