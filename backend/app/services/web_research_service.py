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
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch
from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.services import contact_discovery_service as cds
from app.services import source_ownership as so

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

# --- per-case ハード時間予算（オプトイン）。既定 None = 無制限（従来挙動を維持）。 ---
# 呼び出し側（バッチ/評価）が time_budget を渡したときのみ有効化する。検索フェーズには
# 予算の SEARCH_BUDGET_FRAC までを割り当て、残りをクロールに使う。十分な公式連絡先
# （検証済み公式サイト＋maker所有メール/フォーム）を得たら early_exit で打ち切る。
SEARCH_BUDGET_FRAC = 0.55   # time_budget のうち検索フェーズに充てる割合


# --- 公式サイト未確定時の限定的救済（corroborated domain / Phase 3-②） ---
# 公式サイトを確定できないと、取得済みの正当なメール（info@ / cs@ 等）まで
# no_verified_official で捨ててしまう。特に非ラテン文字の maker 名では
# significant_terms() が空集合になり _infer_official_url が構造的に候補を返せないため、
# 韓国語/日本語圏の案件で recall が出ない（例: 주식회사 에이치알메디컬 / hr-medical.co.kr）。
#
# そこで「同一ドメインが自分自身のページで複数の役割アドレスを掲載している」という
# 裏付け（corroboration）がある場合に限り、**メールだけ** maker 所有として救済する。
# official_site / effective_domain は更新しない＝公式サイト判定へは昇格させないため、
# Phase2 が潰した公式サイト FP の面は一切広がらない。
_CORROBORATION_MIN_ROLE_EMAILS = 2
# 役割アドレス（組織の窓口）。person は個人名の可能性があり裏付けに数えない。
_CORROBORATION_ROLES = frozenset({"high", "mid", "support"})
# 裏付けに使えないドメインクラス（フリーメール・明確な第三者）。
_CORROBORATION_DENY_CLASSES = frozenset({"personal_email"})


def _corroboration_role_ok(email: str) -> bool:
    """裏付けに数えられる役割アドレスか（info/cs/support/sales 等）。"""
    return so.email_role(email) in _CORROBORATION_ROLES


def _corroboration_self_sourced(sources: list[str] | None, reg: str) -> bool:
    """そのメールが「そのドメイン自身のページ」から採れたか（条件2）。

    ディレクトリ/まとめサイトが第三者のメールを転載しているケースを排除する。
    """
    return any(so.registrable_domain(s) == reg for s in (sources or []))


# --- DNS プロファイル（Web サイトを持たないメーカーの識別） ---
# 実在する形態として「Web サイトは無いがメールは生きている」メーカーがある
# （例: hr-medical.co.kr は A レコード無し / MX = mailapp.hiworks.co.kr）。
# この場合 root ページを永久に取得できないため content 検証を課すと救済できない。
# 一方 A レコードを持つドメイン（transitionnetwork.org / xinc.digital / en.rian.ru 等）は
# 従来どおり root fetch + verify を必須にする＝Phase2 の FP 面は広がらない。
DNS_TIMEOUT = 3.0
_dns_cache: dict[str, dict | None] = {}


def dns_profile(domain: str) -> dict | None:
    """登録可能ドメインの {"a": bool, "mx": bool} を返す（失敗時 None）。

    A が引けない（NXDOMAIN / NoAnswer）かつ MX がある = 「サイト無し・メールあり」。
    プロセス内でキャッシュする（同一 run で同じドメインを何度も引かない）。
    """
    if not domain:
        return None
    if domain in _dns_cache:
        return _dns_cache[domain]
    prof: dict | None = None
    try:
        import dns.resolver  # noqa: PLC0415

        res = dns.resolver.Resolver()
        res.lifetime = DNS_TIMEOUT
        res.timeout = DNS_TIMEOUT

        def _has(rtype: str) -> bool:
            try:
                return bool(res.resolve(domain, rtype))
            except Exception:  # noqa: BLE001  NXDOMAIN/NoAnswer/Timeout は「無し」扱い
                return False

        has_a = _has("A") or _has("AAAA")
        prof = {"a": has_a, "mx": _has("MX")}
    except Exception:  # noqa: BLE001  dnspython 未導入/解決不能は判定不能
        prof = None
    _dns_cache[domain] = prof
    return prof


def build_domain_corroboration(
    email_map: dict,
    root_verdicts: dict,
    maker_name: str | None,
    terms: set[str] | None,
    dns_fn=None,
) -> dict:
    """救済してよい登録可能ドメインを {domain: mode} で返す。

    mode は救済の根拠:
      - "site"     : root ページを取得でき、verify_official_candidate が accepted
                     （条件2 の自サイト掲載も必須）
      - "siteless" : A レコード無し + MX あり＝Web サイトを持たないメーカー。
                     root を永久に取得できないため content 検証は課さず、
                     出典も第三者ページを許す（自サイトが存在しないため）

    A レコードを持つのに root を取得できなかったドメイン（403 等の en.rian.ru 型）は
    どちらにも該当せず、従来どおり救済しない。
    """
    terms = set(terms or set())
    dns_fn = dns_fn or dns_profile
    # 自サイト掲載のみ（"site" 経路用）/ 出典を問わない（"siteless" 経路用）の2通りで数える。
    counts_self: dict[str, int] = {}
    counts_any: dict[str, int] = {}
    for rec in (email_map or {}).values():
        addr = rec.get("email") or ""
        reg = so.registrable_domain(addr)
        if not reg or not _corroboration_role_ok(addr):
            continue
        counts_any[reg] = counts_any.get(reg, 0) + 1
        if _corroboration_self_sourced(rec.get("sources"), reg):
            counts_self[reg] = counts_self.get(reg, 0) + 1

    out: dict[str, str] = {}
    for reg, n_any in counts_any.items():
        if n_any < _CORROBORATION_MIN_ROLE_EMAILS:
            continue  # 条件3(=役割メール2件以上)。siteless/site 共通の前提
        # 条件4: フリーメール等は対象外
        if so.classify_domain(reg).ownership_class in _CORROBORATION_DENY_CLASSES:
            continue
        # 条件5: maker 名一致、または非ラテン名で照合語が作れない場合のみ緩和する。
        if terms:
            dom_tok = reg.split(".")[0]
            if not any(t in dom_tok or dom_tok in t for t in terms):
                continue
        # 経路1: root を取得でき verify accepted（自サイト掲載が必須）
        if root_verdicts.get(reg) and counts_self.get(reg, 0) >= _CORROBORATION_MIN_ROLE_EMAILS:
            out[reg] = "site"
            continue
        # 経路2: A 無し + MX あり＝サイトを持たないメーカー（条件1・2）
        prof = dns_fn(reg)
        if prof and not prof.get("a") and prof.get("mx"):
            out[reg] = "siteless"
    return out


def _email_maker_ownership(
    email: str, sources: list[str] | None, official_domain: str | None,
    corroborated_domains: set[str] | None = None,
) -> tuple[bool, str]:
    """検証済み公式ドメインに対する maker 所有判定。(owned, reason) を返す。

    採用（maker-owned）:
      - 検証済み公式サイトと同一の登録可能ドメイン / 正当なサブドメイン
      - フリーメール（Gmail 等）で、かつ出典ページが検証済み公式ドメインに属する場合のみ
    不採用（第三者 / manual review へ分離）:
      - レビュー / ニュース / 紹介 / ディレクトリ / 代理店 / 小売 / マーケットプレイス /
        名前衝突ドメイン / unknown / 検証不能 / 検索スニペットのみ / 第三者ページ由来
      - 公式サイトが未確定（official_domain 無し）の場合は unknown 系は一切採用しない
    ドメイン分類は既存の source_ownership を再利用する（二重実装しない）。
    """
    if not official_domain:
        # 公式サイト未確定でも、裏付けの取れたドメイン（build_domain_corroboration）の
        # 役割アドレスで、かつ自サイト掲載のものだけは救済する。official_site は
        # 確定させない＝メール採用のみ。
        reg = so.registrable_domain(email)
        if corroborated_domains and reg and reg in corroborated_domains and \
                _corroboration_role_ok(email):
            # "site" 経路は自サイト掲載を必須にする。"siteless"（A 無し + MX あり）は
            # 自サイトが存在しないため出典を問わない。
            mode = (corroborated_domains.get(reg)
                    if isinstance(corroborated_domains, dict) else "site")
            if mode == "siteless" or _corroboration_self_sourced(sources, reg):
                return True, "corroborated_domain"
        return False, "no_verified_official"
    off_reg = so.registrable_domain(official_domain)
    if not off_reg:
        return False, "no_verified_official"
    email_reg = so.registrable_domain(email)
    if email_reg and email_reg == off_reg:
        # 同一登録ドメイン / サブドメイン（source_ownership も maker_official/subdomain）。
        return True, "official_domain"
    own = so.classify_domain(email)
    if own.ownership_class == "personal_email":
        # フリーメールは「検証済み公式ドメインのページに掲載」されている場合のみ採用。
        for src in sources or []:
            if so.registrable_domain(src) == off_reg:
                return True, "personal_on_official_page"
        return False, "personal_off_official_page"
    # それ以外（review/news/directory/retailer/agency/unknown/名前衝突）は maker-owned にしない。
    return False, f"third_party:{own.ownership_class}"


def _form_maker_owned(form_url: str, official_domain: str | None) -> bool:
    """問い合わせフォーム URL が検証済み公式ドメイン（同一登録ドメイン）に属するか。"""
    if not official_domain:
        return False
    off_reg = so.registrable_domain(official_domain)
    return bool(off_reg) and so.registrable_domain(form_url) == off_reg


def _is_domain_root(url: str, domain: str | None) -> bool:
    """url が domain（同一登録ドメイン）のトップページ（path が空/`/`）か。"""
    if not domain:
        return False
    p = urlparse(url)
    if p.path not in ("", "/"):
        return False
    off_reg = so.registrable_domain(domain)
    return bool(off_reg) and so.registrable_domain(url) == off_reg

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

# クラファン運営/集金代行（platform）の SNS ハンドル。運営自身の公式 SNS や
# BackerKit 等のプレッジマネージャの SNS を「メーカーの SNS」として誤採用しない。
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
        "gofundme",
        "patreon",
        "backerkit",
        "pledgemanager",
        "crowdox",
        "gamefound",
        "pledgebox",
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


# クラファン URL のパス上、slug の直前に来る既知プレフィックス。
_SLUG_PREFIXES = ("projects", "profile", "project", "individuals", "creators", "p")
# slug として採用しない汎用語・純数値。
_SLUG_STOP = frozenset({"projects", "project", "profile", "www", "en", "discover"})


def _slug_ok(seg: str) -> bool:
    seg = (seg or "").strip().lower()
    if not seg or seg in _SLUG_STOP:
        return False
    if seg.isdigit():           # indiegogo /individuals/12345 のような数値 ID は除外
        return False
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._\-]{1,}", seg))


def extract_slugs(project: Project) -> tuple[str, str]:
    """source_url / maker_url からクリエイター slug・プロジェクト slug を抽出する。

    例:
      https://www.kickstarter.com/projects/lunosama/narrationos
        → creator_slug=lunosama, project_slug=narrationos
      https://www.kickstarter.com/profile/lunosama → creator_slug=lunosama
      https://www.indiegogo.com/projects/vitesy-fruit-bowl → project_slug=vitesy-fruit-bowl
      https://www.ulule.com/narrationos/ → project_slug=narrationos
    """
    creator_slug = ""
    project_slug = ""
    for url in (getattr(project, "source_url", None), getattr(project, "maker_url", None)):
        if not url:
            continue
        parts = [p for p in urlparse(url).path.split("/") if p]
        low = [p.lower() for p in parts]
        if "profile" in low:
            i = low.index("profile")
            if i + 1 < len(parts) and _slug_ok(parts[i + 1]):
                creator_slug = creator_slug or parts[i + 1]
        for key in ("projects", "project"):
            if key in low:
                i = low.index(key)
                rest = [p for p in parts[i + 1:]]
                if len(rest) >= 2 and _slug_ok(rest[0]) and _slug_ok(rest[1]):
                    creator_slug = creator_slug or rest[0]
                    project_slug = project_slug or rest[1]
                elif len(rest) == 1 and _slug_ok(rest[0]):
                    project_slug = project_slug or rest[0]
                break
        # Ulule 等：/<slug>/ 形式（既知プレフィックス無し）
        if not creator_slug and not project_slug and len(parts) == 1 and _slug_ok(parts[0]):
            project_slug = parts[0]
    return creator_slug, project_slug


def guess_domains(brand: str, slug: str, project_slug: str = "") -> list[str]:
    """ブランド名/slug から公式サイト候補ドメインを生成する（要件2）。

    例: RiseFit AI → risefitai.com / risefit.ai / getrisefit.com / risefitapp.com /
        risefit.io ...（実在確認は verify_official_domain で行う）
    """
    tokens: list[str] = []
    seen_tok: set[str] = set()
    for name in (slug, project_slug, brand):
        if not name:
            continue
        t = re.sub(r"[^a-z0-9]", "", name.lower())
        if len(t) >= 3 and t not in seen_tok:
            seen_tok.add(t)
            tokens.append(t)
    out: list[str] = []
    seen: set[str] = set()

    def add(host: str) -> None:
        u = f"https://{host}"
        if host and u not in seen:
            seen.add(u)
            out.append(u)

    for t in tokens:
        for tld in (".com", ".ai", ".io", ".co", ".app", ".net"):
            add(t + tld)
        add("get" + t + ".com")
        add(t + "app.com")
        add(t + "hq.com")
    return out[:18]


def _default_domain_get(url: str, timeout: float = 8.0):
    """(status, final_url, body) を返す既定の取得関数（httpx）。失敗時 None。"""
    try:
        import httpx

        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return resp.status_code, str(resp.url), (resp.text or "")
    except Exception:  # noqa: BLE001  取得失敗＝実在扱いしない
        return None


def verify_official_domain(
    url: str, terms: set[str], *, get_fn=None, timeout: float = 8.0,
    maker_name: str | None = None,
) -> str | None:
    """候補ドメインを GET で実在確認し、本文に関連語があれば公式 root を返す。

    - クラファン/プラットフォーム URL は採用しない。
    - 実在しない/関連語が無い（スクワッター等）は None。
    get_fn(url)->(status, final_url, body)|None を注入できる（テスト用）。
    """
    if cds.is_platform_url(url):
        return None
    got = (get_fn or _default_domain_get)(url)
    if not got:
        return None
    status, final, body = got
    if status is None or status >= 400:
        return None
    if cds.is_platform_url(final):
        return None
    low = (body or "").lower()
    if terms and not any(t in low for t in terms):
        return None  # ページに関連語が無い＝本物とみなさない
    # ニュース/メディア/レビュー/ディレクトリ/NPO 等（メーカー本体でない）は採用しない。
    ident = cds.extract_site_identity(body, final)
    reg = cds.source_ownership.registrable_domain(final)
    media_collision, _ev = cds.official_media_or_directory_collision(ident, reg, maker_name)
    if media_collision:
        return None
    p = urlparse(final)
    return f"{p.scheme}://{p.netloc}"


def guess_and_verify_official(
    brand: str, slug: str, project_slug: str, terms: set[str],
    *, get_fn=None, max_candidates: int = 10, timeout: float = 6.0,
    maker_name: str | None = None,
) -> str | None:
    """候補ドメインを順に実在確認し、最初に確認できた公式サイト root を返す。

    同期エンドポイントで使うため、候補数とタイムアウトを絞る（速さ優先）。
    """
    for u in guess_domains(brand, slug, project_slug)[:max_candidates]:
        verified = verify_official_domain(
            u, terms, get_fn=get_fn, timeout=timeout,
            maker_name=maker_name or brand)
        if verified:
            logger.info("guessed & verified official site: %s", verified)
            return verified
    return None


# --- 韓国語メーカー向けクエリ（Recall 改善） ---
# 実測（p96 놀로）: 生成 28 本のうち実行されるのは MAX_QUERIES=20 本まで。その 15〜20
# 位を占める「スタートアップ系ソース」（Product Hunt / YC / Crunchbase / GitHub 等）は
# 韓国の中小メーカーには構造的に無効で、収穫 0 の死に枠だった。一方、公式サイトに到達
# する韓国語の言い回し（공식몰 / 공식 홈페이지 等）は 1 本も生成されていなかった。
# そこでハングル maker のときだけ、死に枠を韓国語クエリへ **置換**する（単純追加だと
# ゼロサムの 20 本枠から生産的な先頭クエリが押し出されて既存成功ケースが壊れる）。
_HANGUL_RE = re.compile(r"[가-힣]")

# maker 名に付けて公式サイトを狙う汎用韓国語サフィックス（業種語は入れない＝
# 案件固有の語はゼロサム枠を潰すだけで他案件に効かない）。
_KR_OFFICIAL_QUERY_SUFFIXES = (
    "공식몰", "공식 홈페이지", "브랜드", "회사", "공식 스토어",
)


def _has_hangul(text: str | None) -> bool:
    """文字列にハングル音節が含まれるか（韓国語クエリ／死に枠抑制の発火条件）。"""
    return bool(text) and bool(_HANGUL_RE.search(text))


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

    creator_slug, project_slug = extract_slugs(project)
    # maker_name が短く曖昧（"Luno" 等）なら単体検索の優先度を下げる。
    maker_ambiguous = bool(maker) and len(maker.replace(" ", "")) <= 5

    return {
        "project_title": title,
        "short_title": short if short and short != title else "",
        "maker_name": maker,
        "brand_names": brand_names[:4],
        "official_domain": official_domain,
        "domain_name": domain_name,
        "source_site": source_site,
        "creator_slug": creator_slug,
        "project_slug": project_slug,
        "maker_ambiguous": maker_ambiguous,
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
    creator_slug = kw["creator_slug"]
    project_slug = kw["project_slug"]
    maker_ambiguous = kw["maker_ambiguous"]

    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    def add_slug_queries() -> None:
        """クリエイター/プロジェクト slug による探索（曖昧な maker 名より強い手掛かり）。"""
        # タイトル × creator_slug（最も具体的）
        if title and creator_slug:
            add(f"{title} {creator_slug}")
            add(f"{creator_slug} {title}")
        for slug in [s for s in (creator_slug, project_slug) if s]:
            # 短い bare / dash 変種も必ず試す（risefit-ai → "risefit ai" / "risefitai"）
            add(slug)
            if "-" in slug:
                add(slug.replace("-", " "))
                add(slug.replace("-", ""))
            for kwd in ("contact", "email", "Instagram", "Facebook",
                        "LinkedIn", "YouTube", "official website"):
                add(f"{slug} {kwd}")
            for site in ("youtube.com", "instagram.com", "facebook.com",
                         "linkedin.com", "github.com", "linktr.ee",
                         "carrd.co", "beacons.ai"):
                add(f"site:{site} {slug}")

    # 優先度1: タイトル × メーカー の複合 SNS（最も具体的）
    if title and maker:
        for kwd in ("Instagram", "Facebook", "LinkedIn"):
            add(f'"{title}" "{maker}" {kwd}')

    # 優先度2: タイトルの SNS / 公式
    if title:
        for kwd in ("Instagram", "Facebook", "LinkedIn", "official Instagram",
                    "official Facebook", "official", "brand"):
            add(f'"{title}" {kwd}')

    # maker が短く曖昧なら、slug 検索を maker 単体検索より優先する
    if maker_ambiguous:
        add_slug_queries()

    # 優先度3: メーカー名の SNS / 連絡先（曖昧な場合は上で slug を先行済み）
    if maker:
        for kwd in ("Instagram", "Facebook", "LinkedIn", "official", "contact"):
            add(f'"{maker}" {kwd}')

    # 優先度3.5: ハングル maker 名の韓国語クエリ（公式サイト到達率の改善）。
    # 実行される先頭 MAX_QUERIES 本に確実に入るよう、site: 限定より前に置く。
    # 見返りは優先度8 のスタートアップ系ブロック抑制で相殺する（下記参照）。
    kr_maker = _has_hangul(maker)
    if kr_maker:
        for kwd in _KR_OFFICIAL_QUERY_SUFFIXES:
            add(f'"{maker}" {kwd}')

    # 曖昧でなければ slug 検索はここで追加（補助）
    if not maker_ambiguous:
        add_slug_queries()

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

    # 優先度8: スタートアップ系ソース（Product Hunt / GitHub / YC / Crunchbase /
    # LinkedIn company / App Store 等）。会社・創業者・SNS の発見率を上げる（要件3・7）。
    # ただしハングル maker では実測 収穫 0（韓国の中小メーカーはこれらに載らない）で、
    # MAX_QUERIES のゼロサム枠を占有するだけなので抑制し、その枠を優先度3.5 の
    # 韓国語クエリに充てる。ラテン/日本語/中国語 maker の生成列は一切変わらない。
    brand_terms = [t for t in (title, maker, short, creator_slug, project_slug) if t]
    # ブランド語は最初の 2 つに絞る（クエリ爆発を防ぐ）
    for b in (list(dict.fromkeys(brand_terms))[:2] if not kr_maker else []):
        for src in ("Product Hunt", "Crunchbase", "GitHub", "YC", "founder",
                    "LinkedIn company"):
            add(f'"{b}" {src}')
        for site in ("producthunt.com", "github.com", "ycombinator.com",
                     "crunchbase.com", "linkedin.com/company", "linkedin.com/in",
                     "apps.apple.com", "play.google.com", "medium.com", "substack.com"):
            add(f'site:{site} "{b}"')

    # 優先度9: ドメイン site: 限定（メール/PDF）
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
        from app.services.search_providers import sanitize_query

        # NFKC 正規化＋スマートクォート変換のうえ UTF-8 で percent-encode する
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(
            sanitize_query(query), encoding="utf-8"
        )
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
    """クラファン運営/集金代行（platform）自身の公式 SNS かどうか（誤採用防止）。

    ハンドル一致（instagram.com/kickstarter）に加え、URL パスにプラットフォーム名を
    含む運営アカウント（youtube.com/user/kickstarter, facebook.com/Backerkit 等）も除外。
    """
    handle = _social_handle(platform, url)
    if handle and handle in _PLATFORM_SOCIAL_HANDLES:
        return True
    path = urlparse(url).path.lower()
    return any(h in path for h in _PLATFORM_SOCIAL_HANDLES)


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
    # クラファン/集約プラットフォーム（kickstarter/indiegogo/ulule/makuake/
    # camp-fire/greenfunding/readyfor 等）を網羅判定する。
    return cds.is_platform_url(url)


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
    "camp-fire.jp", "campfire.jp", "readyfor.jp", "machi-ya.jp", "for-good.net",
    "news", "press", "magazine", "review", "blog.",
)

# タイトル照合フォールバック候補**専用**の追加 deny-list（Phase 3-③）。
# 小売モール/求人/書店/企業ディレクトリ/portfolio は「maker 名を掲載するが公式ではない」。
# ドメイン語照合では引っかからないが、タイトルには maker 名が載るため title 候補に紛れる。
# ここは title 候補の選定にのみ使う（メイン crawl の巡回可否＝_NON_OFFICIAL_HOST_HINTS には
# 足さない。既存の巡回・メール収集の挙動を変えないため）。
_TITLE_CANDIDATE_DENY = (
    # 小売モール / マーケットプレイス（韓国系を含む）
    "ssg.com", "shinsegae", "gmarket.", "auction.co.kr", "11st.co.kr",
    "coupang.", "lotteon.com", "interpark.", "tmon.", "wemakeprice.",
    "smartstore.naver.", "brand.naver.", "shopping.naver.", "storefarm.naver.",
    "kakao.com", "kakaomaker",
    # 書店（出版社 maker が本屋として現れる）
    "kyobobook", "aladin.co.kr", "yes24.com", "book1st", "ridibooks", "millie.co.kr",
    # 求人 / 企業情報ディレクトリ
    "jobkorea", "saramin", "wanted.co.kr", "incruit", "jobplanet", "rocketpunch",
    "catch.co.kr", "bizno.net", "moneypin", "marketbz", "cookiedeal", "linkonbiz",
    "thevc.kr", "innoforest", "nicebizinfo", "kipris", "stockplus",
    # ポートフォリオ / まとめ / 百科
    "behance.net", "dribbble.com", "artstation.", "pinterest.",
    "namu.wiki", "namu.moe", "encykorea", "pillyze", "shoppinghow",
)


# --- 非ラテン maker 名のためのタイトル照合フォールバック（Phase 3-③） ---
# significant_terms() は ASCII トークン前提のため、韓国語/日本語/中国語の maker 名では
# 空集合になる。_infer_official_url の照合条件 `t in domain_token or domain_token in t`
# は terms が空だと常に False なので、非ラテン圏の案件では公式サイト候補を **一件も**
# 生成できない（実測: 対象26件で recall 0%）。
#
# ドメイン文字列は ASCII なので、名前をローマ字化しても届かない正解が多い
# （퍼시몬→monshop / 경성건강원→gswon / 도서출판 무지개→rainbowbooks / 나노랩→nanowt）。
# 一方、検索結果の **タイトル**には maker 名がそのままの表記で載る。現行コードは
# タイトルを候補選定に使っていないため、ここを補う。
#
# ただしタイトル一致だけでは小売・媒体・別会社を拾う（実測 precision 73%）。
# 採用は root を取得し identity（<title>/og:site_name/JSON-LD name）に maker 名が
# 現れることを必須にする（_official_identity_matches_maker）。実測で FP 0 / precision 100%。

# 法人格の表記（照合前に除去する）。
_CORP_FORMS = (
    "주식회사", "㈜", "(주)", "（주）", "유한회사", "유한책임회사",
    "사단법인", "재단법인", "합자회사", "합명회사",
    "株式会社", "有限会社", "合同会社", "一般社団法人", "公益財団法人",
    "股份有限公司", "有限公司", "股份公司", "公司",
)

# identity として受理してはいけない文字列（エラー/チャレンジページのタイトル）。
# これらを identity と認めると「Access Denied」ページが照合を通してしまう。
_IDENTITY_ERROR_MARKERS = (
    "access denied", "just a moment", "not found", "404", "403", "forbidden",
    "error", "attention required", "are you human", "bot verification",
    "service unavailable", "bad gateway", "site not found", "page not found",
    "under construction", "coming soon", "security check", "cloudflare",
)


def _strip_corp_forms(name: str | None) -> str:
    """maker 名から法人格表記を除去し、空白を詰めた照合用の文字列を返す。"""
    s = name or ""
    for form in _CORP_FORMS:
        s = s.replace(form, " ")
    return re.sub(r"\s+", "", s).strip()


def _title_mentions_maker(title: str | None, maker_core: str) -> bool:
    """検索結果タイトルに maker 名（法人格除去後）が含まれるか。"""
    if not maker_core or len(maker_core) < 2:
        return False
    return maker_core in re.sub(r"\s+", "", title or "")


def _identity_strings(html: str | None, final_url: str | None) -> list[str]:
    """root ページの identity 文字列（<title>/og:site_name/JSON-LD name）を返す。"""
    ident = cds.extract_site_identity(html, final_url)
    return [s for s in ((ident.get("names") or [])
                        + (ident.get("organization_names") or [])) if s]


# 外部大手ブランドの別名（同一ブランドは 1 グループ。韓国語/英語表記を併記）。
# title fallback 候補の identity が maker 以外のこれらブランドに支配されている場合、
# 別事業ライン/リセラーの店舗（例: "LG전자 B2B 공식커머셜 전문점 올음"）なので採用しない。
_MAJOR_BRAND_ALIASES = (
    ("lg", "lg전자", "엘지"),
    ("samsung", "삼성"),
    ("apple", "애플"),
    ("hyundai", "현대"),
    ("kia", "기아"),
    ("sk",),
    ("lotte", "롯데"),
    ("coupang", "쿠팡"),
    ("naver", "네이버"),
    ("kakao", "카카오"),
)


def _external_brand_in_identity(
    html: str | None, final_url: str | None, maker_name: str | None
) -> str | None:
    """candidate identity が maker 以外の外部大手ブランドに支配されているか。

    支配している外部ブランドの検出語を返す（無ければ None）。maker 名自身にその
    ブランド語が含まれる場合は「支配」とみなさない（現대バイオ 等の正規メーカーを守る）。
    - 英語別名（lg / samsung / sk 等）は identity のラテン語トークン単位で一致判定する
      （task/desk 等への部分一致誤爆を避ける）。
    - 韓国語別名（엘지 / 삼성 / lg전자 等）は空白除去した identity への部分一致で判定する
      （"LG전자" のように韓国語と地続きになるため）。
    """
    idents = _identity_strings(html, final_url)
    if not idents:
        return None
    joined = " ".join(idents).lower()
    joined_norm = re.sub(r"\s+", "", joined)
    latin_tokens = set(re.findall(r"[a-z0-9]+", joined))
    maker_low = (maker_name or "").lower()
    maker_norm = re.sub(r"\s+", "", maker_low)
    maker_latin = set(re.findall(r"[a-z0-9]+", maker_low))

    def _hit(alias: str, tokens: set[str], norm: str) -> bool:
        # 英数字のみの別名はトークン一致、韓国語を含む別名は部分一致。
        if alias.isascii():
            return alias in tokens
        return alias in norm

    for group in _MAJOR_BRAND_ALIASES:
        present = next(
            (a for a in group if _hit(a, latin_tokens, joined_norm)), None)
        if not present:
            continue
        # maker 名自身がこのブランド語を含むなら支配とみなさない。
        if any(_hit(a, maker_latin, maker_norm) for a in group):
            continue
        return present
    return None


def _official_identity_matches_maker(
    html: str | None, final_url: str | None, maker_name: str | None
) -> bool:
    """root の identity に maker 名が現れるか（採用の必須条件）。

    identity を取得できない場合は False（＝採用しない）。エラー/チャレンジページの
    タイトルは identity として認めない。本文一致では小売サイト（取扱商品として
    maker 名が本文に出る）を弾けないため、identity に限定する。
    """
    core = _strip_corp_forms(maker_name)
    if not core or len(core) < 2:
        return False
    for s in _identity_strings(html, final_url):
        low = s.strip().lower()
        if any(m in low for m in _IDENTITY_ERROR_MARKERS):
            continue
        if core in re.sub(r"\s+", "", s):
            return True
    return False


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


# タイトル照合フォールバックで一度に検証する候補ドメインの上限（実行時間の保険）。
_TITLE_INFER_MAX_CANDIDATES = 4


def _crawl_seen_has(seen: set[str], url: str) -> bool:
    """crawl_seen に url が含まれるか（末尾スラッシュの有無を同一視する）。

    main crawl は検索結果の URL をそのまま積むため "https://x.example/" が入る一方、
    タイトル照合の候補 root は scheme://netloc 形式で末尾スラッシュを持たない。素の
    `in` 判定だと同じページを「未訪問」と誤判定し、取得済みで URL 予算を消費しない
    候補まで予算切れ扱いで捨ててしまう（p96 놀로 の実害）。
    """
    n = (url or "").rstrip("/")
    return url in seen or n in seen or (n + "/") in seen


def _title_official_candidates(
    page_candidates: list[tuple[int, str]],
    page_titles: dict[str, str],
    maker_name: str | None,
) -> list[str]:
    """検索結果タイトルに maker 名を含む候補 root URL を、スコア降順・重複排除で返す。

    ドメイン語照合（_infer_official_url）とは独立。呼び出し側は「他の手段で公式が
    全く確定しなかった場合のみ」最終手段としてこれを使い、各 root を取得して
    identity 一致と verify を通ったものだけ採用する（小売/媒体/別会社は identity で落ちる）。
    """
    maker_core = _strip_corp_forms(maker_name)
    if not maker_core or not page_titles:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for _score, url in sorted(page_candidates, key=lambda t: t[0], reverse=True):
        host = urlparse(url).netloc.lower()
        if any(h in host for h in _NON_OFFICIAL_HOST_HINTS):
            continue
        if any(h in host for h in _TITLE_CANDIDATE_DENY):
            continue
        reg = cds._domain_of(url)
        if not reg or reg in seen:
            continue
        if _title_mentions_maker(page_titles.get(url), maker_core):
            p = urlparse(url)
            seen.add(reg)
            out.append(f"{p.scheme}://{p.netloc}")
    return out


# <a href="...">テキスト</a> から (絶対URL, アンカーテキスト小文字) を取り出す。
_ANCHOR_RE = re.compile(
    r'<a\s[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*?>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# 「公式サイト」を示すアンカーテキスト（英・仏・日）。
_OFFICIAL_TEXT_HINTS = (
    "official website", "official site", "officialsite", "official",
    "website", "web site", "visit website", "visit site", "our website",
    "site officiel", "公式サイト", "公式", "ウェブサイト", "homepage", "home page",
    "company website", "shop now", "visit us",
)


def extract_links_with_text(html: str, base_url: str) -> list[tuple[str, str]]:
    """HTML の <a> から (絶対URL, アンカーテキスト) を返す（http/https のみ・重複可）。"""
    out: list[tuple[str, str]] = []
    from urllib.parse import urljoin

    for href, text in _ANCHOR_RE.findall(html or ""):
        href = href.strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(base_url, href)
        if not absu.startswith(("http://", "https://")):
            continue
        absu = absu.split("#", 1)[0]
        out.append((absu, _strip_tags(text)))
    return out


def extract_official_from_page(
    html: str,
    base_url: str,
    project_terms: set[str],
    maker_terms: set[str],
) -> str | None:
    """クラファンページ等の HTML から公式サイト URL（root）を推定する（要件1・3）。

    外部リンク（運営/SNS/マーケットプレイス/集約サイトを除く）のうち、
      - アンカーテキストが「Official Website / Website / 公式サイト」等
      - ドメイン名がメーカー名/タイトル主要語を含む
    を手掛かりにスコアリングし、十分な根拠があるものだけ返す（無ければ None）。
    """
    # プラットフォーム/SNS 除外・アンカーテキスト判定は cds の共通ロジックに委譲する
    # （kickstarter/profile 等を公式として採用しないため）。
    return cds.extract_official_link(
        html, base_url, (maker_terms | project_terms) or set()
    )


def build_fallback_queries(
    project: Project, research: CompanyResearch | None = None
) -> list[str]:
    """検索が振るわない時の短縮フォールバッククエリ（要件）。

    例: "Vitesy Fruit Bowl: Reinventing Fruit Freshness"
        → "Vitesy" → "Vitesy Facebook" → "Vitesy Instagram" → "Vitesy LinkedIn"
    メーカー名 → 短縮タイトル → タイトル先頭語 の順で短いベース語を決める。
    """
    kw = build_keyword_candidates(project, research)
    base = (kw["maker_name"] or "").strip()
    if not base:
        # メーカー名が無ければ短縮タイトル（無ければ件名）の先頭語をブランド語とみなす
        head = (kw["short_title"] or kw["project_title"] or "").strip()
        base = head.split(" ")[0].strip() if head else ""
    base = base.strip()
    out: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        if q and q not in seen:
            seen.add(q)
            out.append(q)

    # slug 検索を優先（曖昧な maker 名より強い手掛かり）
    title = kw["project_title"]
    for slug in [s for s in (kw["creator_slug"], kw["project_slug"]) if s]:
        if title:
            add(f"{title} {slug}")
        for kwd in ("contact", "email", "Instagram", "YouTube"):
            add(f"{slug} {kwd}")
        for site in ("instagram.com", "youtube.com", "linktr.ee", "linkedin.com"):
            add(f"site:{site} {slug}")
    if base:
        for kwd in ("", " Facebook", " Instagram", " LinkedIn", " official website"):
            add(f"{base}{kwd}".strip())
    return out


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
    progress_cb=None,
    time_budget: float | None = None,
    max_queries: int | None = None,
    max_urls: int | None = None,
    early_exit: bool = False,
) -> dict:
    """Web リサーチ本体（DB 非依存）。集計した結果 dict を返す。

    fetch_fn(url)->html|None, search_fn(query)->[url]|[{url,title,snippet}] を注入
    できる（テスト用）。未指定なら DuckDuckGo HTML 検索 + 既存 HTTP 基盤を使う。

    per-case 時間制御（すべてオプトイン。未指定なら従来挙動を維持）:
      time_budget : 1 案件あたりのハード総時間上限（秒）。超過で探索を打ち切り、
                    それまでの部分結果を返す（stop_reason="timeout"）。
      max_queries : 実行検索クエリ数の上限（既定 MAX_QUERIES を上書き）。
      max_urls    : クロール URL 数の上限（既定 MAX_URLS を上書き）。
      early_exit  : 検証済み公式サイト＋maker所有メール/フォームを得たら即終了。
    1 案件の超過・失敗はこの関数内で吸収し、例外はバッチ全体を止めない。
    """
    _start = time.monotonic()
    _q_cap = max_queries if max_queries is not None else MAX_QUERIES
    _u_cap = max_urls if max_urls is not None else MAX_URLS
    stop_reason: str | None = None

    def _expired(frac: float = 1.0) -> bool:
        return time_budget is not None and (time.monotonic() - _start) > time_budget * frac
    # maker_url がクラファン/集約プラットフォーム（kickstarter/profile 等）なら
    # 公式サイトとして採用しない。実際の外部公式サイトはクラファン/プロフィール
    # ページの本文リンクから推定する（要件）。
    official = cds.official_site_or_none(project.maker_url) or ""
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
    page_titles: dict[str, str] = {}              # url -> 検索結果タイトル（推定に使う）
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

    def run_query(q: str) -> None:
        """1 クエリを検索し、結果をスコアリングして各構造に振り分ける。"""
        nonlocal search_failures
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
                    adopted = consider_social(platform, url, score, f"search:{q}")
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
                    page_titles.setdefault(url, rec["title"] or "")
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

    try:
        _qn = min(len(generated_queries), _q_cap)
        for _qi, q in enumerate(generated_queries[:_q_cap]):
            # 検索フェーズには time_budget の SEARCH_BUDGET_FRAC までを割り当てる。
            if _expired(SEARCH_BUDGET_FRAC):
                stop_reason = "timeout"
                logger.info("web_research[%s] search budget reached at query %d/%d",
                            getattr(project, "id", "?"), _qi, _qn)
                break
            if progress_cb:
                progress_cb(f"検索中 ({_qi + 1}/{_qn}): {q[:60]}",
                            pct=(_qi / max(1, _qn)) * 0.4)  # 検索フェーズは全体の 0〜40%
            run_query(q)
        # フォールバック：公式サイト/SNS/巡回ページを全く拾えなかったら短縮クエリで
        # 再検索する（フル件名 → メーカー名 → "<名> Facebook/Instagram/LinkedIn"）。
        if not page_candidates and not socials:
            for q in build_fallback_queries(project, research):
                if q in searched_queries:
                    continue
                logger.info("web_research[%s] simplified-query fallback: %s",
                            getattr(project, "id", "?"), q)
                run_query(q)
                if page_candidates or socials:
                    break
    finally:
        if own_search:
            if hasattr(search, "close"):
                search.close()
            else:
                client = getattr(search, "_client", None)
                if client is not None:
                    client.close()

    # 検索の診断（status/reason/fallback/URL）を収集（UI 表示・原因究明用）
    search_diagnostics = list(getattr(search, "diagnostics", []) or [])

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
    # 検索ベースの公式サイト候補（補助）。確定はクロール中（クラファンページからの
    # 推定を優先）に行う。検索が 0 件でも探索は終了しない（要件 3・6）。
    search_inferred_official = _infer_official_url(
        page_candidates, project, project_terms, maker_terms, official_domain
    )

    # maker_url が登録済みならそれを公式として確定。無ければクロール中に推定する。
    effective_official = official
    effective_domain = official_domain

    # 3. クロール待ち行列（クロール中に動的拡張する）
    crawl_urls: list[str] = []
    crawl_seen: set[str] = set()
    # 案件ページとメーカープロフィール（kickstarter.com/profile/... 等）は、外部の
    # 公式サイトリンクを抽出するために、プラットフォームでも巡回を許可する。
    _platform_seed_ok = {project.source_url or "", project.maker_url or ""}

    def add_crawl(u: str, *, front_at: int | None = None) -> bool:
        if u in crawl_seen or not u.startswith(("http://", "https://")):
            return False
        if _is_skip_url(u):
            return False
        if _is_platform_domain(u) and u not in _platform_seed_ok:
            return False
        crawl_seen.add(u)
        if front_at is not None:
            crawl_urls.insert(front_at, u)
        else:
            crawl_urls.append(u)
        return True

    # 代表パスを展開済みの公式 root（同一 root への二重展開を防ぐ）。
    known_paths_expanded: set[str] = set()

    def expand_known_paths(root: str, insert_at: int) -> int:
        """公式 root の代表パスをクロール待ち行列へ 1 回だけ差し込む。展開件数を返す。

        同じ root で二度呼ばれても 2 回目以降は何もしない（0 を返す）。
        """
        if not root or root in known_paths_expanded:
            return 0
        known_paths_expanded.add(root)
        pos = insert_at
        added = 0
        for path in WEB_KNOWN_PATHS:
            if add_crawl(root + path, front_at=pos):
                pos += 1
                added += 1
        logger.info("web_research[%s] known paths expanded: %s (+%d)",
                    getattr(project, "id", "?"), root, added)
        return added

    def expand_official(root_url: str, insert_at: int, *, inferred: bool = False) -> None:
        """公式サイトを確定し、クロール待ち行列の先頭側に差し込む。

        inferred=True（検索/本文からの **未検証** 推定）では root だけを積み、代表パスは
        identity / 大企業ブランドガードを通過してから expand_known_paths() で展開する。
        未検証候補に代表パス 16 本を先払いすると、その候補が撤回されても積んだ URL は
        待ち行列に残り、MAX_URLS の予算を食い潰すため（実測 p96 놀로: 誤推定した
        support.google.com / gemini.google.com が 25 枠中 17 枠を占有した）。

        maker_url 由来（inferred=False）は登録済み＝信頼できるので従来どおり即時展開する。
        """
        nonlocal effective_official, effective_domain, official_inferred, official_verified
        p = urlparse(root_url)
        root = f"{p.scheme}://{p.netloc}"
        effective_official = root
        effective_domain = cds._domain_of(root)
        # maker_url 由来（登録済み）は信頼して検証しない。検索/本文推定は root 巡回時に検証する。
        official_inferred = inferred
        official_verified = not inferred
        pos = insert_at
        if add_crawl(root, front_at=pos):
            pos += 1
        if inferred:
            logger.info(
                "web_research[%s] official site set: %s "
                "(known paths DEFERRED until verified, inferred=True)",
                getattr(project, "id", "?"), root,
            )
            return
        expand_known_paths(root, pos)
        logger.info(
            "web_research[%s] official site set: %s (+%d known paths, inferred=%s)",
            getattr(project, "id", "?"), root, len(WEB_KNOWN_PATHS), inferred,
        )

    # 推定公式の検証状態（maker_url 由来は検証不要＝True 扱い）。
    official_inferred = False
    official_verified = bool(official)

    # クラファンページ・公式サイト（既知なら）・research.sources を最優先で巡回
    for u in _seed_and_known_urls(project, research):
        add_crawl(u)
    # 既知の公式サイトは即展開（maker_url 由来＝信頼）
    if official:
        expand_official(official, len(crawl_urls))
    # 検索結果ページ（スコア順）も候補に追加（補助）
    for _score, u in sorted(page_candidates, key=lambda t: t[0], reverse=True):
        add_crawl(u)

    logger.info(
        "web_research[%s] crawl start: %d url(s) queued (official=%s)",
        getattr(project, "id", "?"), len(crawl_urls), effective_domain or "未確定",
    )

    # 4. クロールして抽出（待ち行列を動的に消化。公式サイトを途中で発見したら拡張）
    searched: list[str] = []
    candidate_pages: list[dict] = []
    email_map: dict[str, dict] = {}
    # 公式サイト未確定時の救済用（Phase 3-②）。root ページを取得できたドメインについて
    # verify_official_candidate の結果を控える（reg_domain -> accepted）。追加の fetch は
    # 一切行わず、既に取得済みの html を使い回すだけ（実行時間に影響しない）。
    root_verdicts: dict[str, bool] = {}
    forms: list[str] = []
    ok_count = 0
    fail_count = 0
    email_pages_count = 0
    pid = getattr(project, "id", "?")
    # Kickstarter 等の埋め込み JSON "websites":[...] のデバッグ情報（最初に見つけた
    # 配列を採用）。present=配列あり / count=URL件数 / registered=公式サイト登録あり。
    ks_websites: dict | None = None

    def _has_official_contact() -> bool:
        """maker 所有メールを確保できたか（early_exit 判定）。

        フォームだけでは早期終了しない：フォームは実際の連絡先メールより先に見つかり
        やすく、推定公式ドメインが後で覆るとメールを取り逃す。高価値かつ曖昧さの少ない
        「maker 所有メール」を得た時点でのみ打ち切る（誤って早く止めない）。
        """
        if not effective_domain:
            return False
        for rec in email_map.values():
            owned, _ = _email_maker_ownership(rec["email"], rec.get("sources"), effective_domain)
            if owned:
                return True
        return False

    try:
        i = 0
        while i < len(crawl_urls) and len(searched) < _u_cap:
            if _expired():
                stop_reason = "timeout"
                logger.info("web_research[%s] crawl time budget reached (%d crawled)",
                            pid, len(searched))
                break
            url = crawl_urls[i]
            i += 1
            if progress_cb:
                # 1 URL 巡回ごとに進捗・現在URLを通知（DB へ随時 flush される）。
                # 検索フェーズが 0〜40% なので、巡回は 40〜100% に割り当てる。
                total = min(len(crawl_urls), MAX_URLS)
                progress_cb(f"巡回中 ({len(searched) + 1}/{total}): {url}",
                            pct=0.4 + 0.6 * (len(searched) / max(1, total)))
            html = fetch(url)
            searched.append(url)
            page = {"url": url, "type": _page_type(url, effective_domain),
                    "ok": bool(html), "emails": 0}
            candidate_pages.append(page)
            if not html:
                fail_count += 1
                logger.info("web_research[%s] fetch FAIL: %s", pid, url)
                # 推定公式の root を取得できないと identity 検証も代表パス展開もできず、
                # /contact だけ生きているサイトの recall を落とす。root が落ちた場合に
                # 限り従来どおり代表パスを展開して救済する（二重展開は関数側で防止）。
                if (effective_official and official_inferred
                        and url.rstrip("/") == effective_official.rstrip("/")):
                    expand_known_paths(effective_official, i)
                continue
            ok_count += 1

            # 公式サイト未確定のときだけ、取得できた root ページを検証して控えておく
            # （Phase 3-② の救済判定用）。html が手元にあるこの時点でしか Phase2 の
            # content ルール（媒体/NGO/代理店/editorial）を評価できないため、ここで行う。
            # 追加の fetch は発生しない。公式が既に確定している場合は何もしない。
            if not effective_domain:
                _rp = urlparse(url)
                _rreg = so.registrable_domain(url)
                if _rreg and _rp.path in ("", "/") and _rreg not in root_verdicts:
                    _rv = cds.verify_official_candidate(
                        url, html, url, getattr(project, "maker_name", None),
                        project_terms | maker_terms,
                        campaign_url=getattr(project, "source_url", None),
                        source_type=getattr(project, "source_site", None))
                    root_verdicts[_rreg] = bool(
                        _rv.get("accepted") and not _rv.get("collision_detected"))

            # 推定公式（検索/本文由来）の同一ドメインページを最初に取得できたら identity 検証する。
            # ニュース/メディア/レビュー/ディレクトリ/NPO/代理店/editorial 等（メーカー本体でない）
            # なら公式を撤回し、そのドメインの form/email は最終フィルタで第三者に落とす。
            # 取得できたページが 1 つも無ければ official_verified=False のまま → 最終で撤回する。
            # 同一ドメインの各ページで検証する（最初の1ページだけだと、homepage の
            # NewsMediaOrganization/NGO/Government 等の構造化シグナルを持たない下位ページで
            # 通過してしまうため。homepage が後から巡回されても捕捉できるようにする）。
            if (effective_official and official_inferred
                    and cds._same_domain(url, effective_domain or "")):
                official_verified = True
                v = cds.verify_official_candidate(
                    url, html, url, getattr(project, "maker_name", None),
                    project_terms | maker_terms,
                    campaign_url=getattr(project, "source_url", None),
                    source_type=getattr(project, "source_site", None))
                if v.get("collision_detected"):
                    logger.info("web_research[%s] official REJECTED (%s): %s [%s]",
                                pid, v.get("reason"), effective_official, v.get("evidence"))
                    effective_official = None
                    effective_domain = None
                    official_inferred = False
                else:
                    # 検証を通過した推定公式だけ、ここで初めて代表パスを展開する。
                    # 撤回された候補は展開されないので URL 予算を浪費しない。
                    expand_known_paths(effective_official, i)

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

            # このページの全リンクを一度だけ抽出して各処理で使い回す。
            links = cds.extract_links(html, url)
            logger.info(
                "web_research[%s] page loaded %s: chars=%d hrefs=%d email(s)=%d",
                pid, url, len(html), len(links), page_emails,
            )
            # href 一覧（先頭 20 件）をログに出す（要件 3）
            logger.info("web_research[%s] hrefs(first20) of %s: %s",
                        pid, url, links[:20])

            # SNS（ページ内の全リンクを走査。運営 SNS は除外、メーカー名一致を優先）。
            # 検索エンジンに頼らず HTML から取得する（要件 5）。
            sns_counts: dict[str, int] = {}
            before_socials = len(socials)
            for link in links:
                plat = _social_platform(link)
                if not plat:
                    continue
                sns_counts[plat] = sns_counts.get(plat, 0) + 1
                handle = _social_handle(plat, link) or ""
                sc = 25 + (15 if any(t in handle for t in maker_terms) else 0)
                consider_social(plat, link, sc, f"page:{url}")

            # 公式サイト候補（外部・非運営・非SNS）を数える
            official_links = [
                lk for lk in links
                if not _social_platform(lk) and not _is_platform_domain(lk)
                and not any(h in urlparse(lk).netloc.lower() for h in _NON_OFFICIAL_HOST_HINTS)
                and cds._domain_of(lk) and cds._domain_of(lk) != cds._domain_of(url)
            ]
            logger.info(
                "web_research[%s] extracted from %s: facebook=%d instagram=%d "
                "linkedin=%d youtube=%d tiktok=%d official_candidates=%d "
                "(socials added now=%d)",
                pid, url, sns_counts.get("facebook", 0), sns_counts.get("instagram", 0),
                sns_counts.get("linkedin", 0), sns_counts.get("youtube", 0),
                sns_counts.get("tiktok", 0), len(official_links),
                len(socials) - before_socials,
            )

            # Kickstarter 等の埋め込み JSON "websites":[...] を確認（最初の1件を採用）
            if ks_websites is None:
                dbg = cds.embedded_websites_debug(html)
                if dbg["present"]:
                    ks_websites = dbg
                    logger.info(
                        "web_research[%s] embedded websites on %s: count=%d registered=%s",
                        pid, url, dbg["count"], dbg["registered"],
                    )

            # 問い合わせフォーム
            if cds._is_contact_url(url) and url not in forms:
                forms.append(url)
            for link in links:
                if effective_domain and cds._same_domain(link, effective_domain):
                    if cds._is_contact_url(link) and link not in forms:
                        forms.append(link)

            # PDF
            for p in cds.extract_pdf_links(html, url):
                if p["url"] not in pdf_seen:
                    pdf_seen.add(p["url"])
                    pdfs.append(p)

            # 公式サイト未確定なら、このページ（クラファンページ等）内のリンクから推定
            # して代表パスを展開する（要件 1・3・4）。検索結果が 0 件でも機能する。
            if not effective_official:
                cand = extract_official_from_page(
                    html, url, project_terms, maker_terms
                ) or search_inferred_official
                if cand:
                    before_queue = len(crawl_urls)
                    logger.info("web_research[%s] inferred official from %s -> %s",
                                pid, url, cand)
                    expand_official(cand, i, inferred=True)
                    logger.info("web_research[%s] added crawl URLs: %d (queue now %d)",
                                pid, len(crawl_urls) - before_queue, len(crawl_urls))
                else:
                    logger.info(
                        "web_research[%s] no official site link found on %s "
                        "(html may be JS-rendered/blocked; %d external candidates)",
                        pid, url, len(official_links),
                    )

            # 十分な公式連絡先（検証済み公式＋maker所有メール/フォーム）を得たら早期終了。
            if early_exit and _has_official_contact():
                stop_reason = stop_reason or "early_exit_sufficient"
                logger.info("web_research[%s] early exit: official contact found (%d crawled)",
                            pid, len(searched))
                break
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

    # 推定公式（検索/本文由来）の同一ドメインページを 1 つも「取得できなかった」場合は
    # identity 検証不能＝unverified。official 確定にせず撤回する（en.rian.ru のような fetch
    # 不能候補を verified official にしない）。ただし推定確定より前に候補として取得済みで
    # 検証ブロックが走らなかっただけのケース（official_verified=False でも実際には取得成功）
    # は撤回しない＝正規メーカーの recall を守る（例: miradial.com）。maker_url 由来は対象外。
    _official_fetched = bool(effective_domain) and any(
        p.get("ok") and cds._same_domain(p["url"], effective_domain) for p in candidate_pages)
    # 公式ドメインと同一登録ドメインの maker 所有メール/フォームが得られていれば、それ自体が
    # 「そのドメインは maker のもの」という強い証拠なので撤回しない（miradial.com のように
    # 案件本文に記載のメールで確定するケースの recall を守る）。
    _has_maker_contact = _has_official_contact() or any(
        _form_maker_owned(f, effective_domain) for f in forms)
    if (official_inferred and not official_verified
            and not _official_fetched and not _has_maker_contact):
        logger.info("web_research[%s] official DROPPED (unfetchable/unverified): %s",
                    getattr(project, "id", "?"), effective_official)
        effective_official = None
        effective_domain = None
        official_inferred = False

    # ドメインレベル最終ガード: 推定公式が全ページ 403/取得不能で crawl 中に検証できなかった
    # 場合でも、ドメインのみで判定できる collision（大企業ブランド/hosting-preview/第三者/姓衝突）
    # は html 無しで捕捉して撤回する（wilson.com のように全ページがブロックされるケース）。
    if official_inferred and effective_official:
        _fg = cds.verify_official_candidate(
            effective_official, None, effective_official,
            getattr(project, "maker_name", None), project_terms | maker_terms,
            campaign_url=getattr(project, "source_url", None),
            source_type=getattr(project, "source_site", None))
        if _fg.get("collision_detected"):
            logger.info("web_research[%s] official DROPPED (domain-guard %s): %s",
                        getattr(project, "id", "?"), _fg.get("reason"), effective_official)
            effective_official = None
            effective_domain = None
            official_inferred = False

    # 非ラテン maker 名のタイトル照合フォールバック（最終手段・Phase 3-③）。
    # ここまでで公式が全く確定していない場合のみ動く＝page 由来 / ドメイン語照合の候補が
    # 1 件でもあれば入らない（page candidate 優先）。検索結果タイトルに maker 名を含む
    # 候補を順に root 取得し、identity 一致（<title>/og:site_name/JSON-LD name）と
    # verify_official_candidate accepted の **両方**を満たしたものだけ採用する。
    # 小売モール/求人/書店/portfolio/別会社は identity で落ちる。identity は候補ドメイン
    # 単位で判定するため、途中の撤回・再評価に依存しない（単一 bool のバイパス問題を回避）。
    if not effective_official and not _expired():
        title_cands = _title_official_candidates(
            page_candidates, page_titles, getattr(project, "maker_name", None))
        identity_rejected_domains: set[str] = set()
        mkr = getattr(project, "maker_name", None)
        for root in title_cands[:_TITLE_INFER_MAX_CANDIDATES]:
            if _expired():
                break
            reg = cds._domain_of(root)
            if not reg or reg in identity_rejected_domains:
                continue
            # root は main crawl で page candidate として既訪問のことがある。その場合でも
            # identity 検証のため取得し直す（注入 fetcher はキャッシュ、実 fetcher も許容範囲）。
            # 既訪問なら searched を増やさない＝URL 予算を消費しないので、予算チェックは
            # 「新規に予算を使う候補」だけに適用する。判定順を逆にすると（予算 → 既訪問）、
            # 予算ゼロ消費で評価できる本命候補まで捨ててしまう。実測 p96 놀로 では
            # knollo.co.kr / knollo.store を取得済みなのに 1 件も評価されず未回収だった。
            already_seen = _crawl_seen_has(crawl_seen, root)
            if not already_seen and len(searched) >= _u_cap:
                continue
            thtml = fetch(root)
            if not already_seen:
                crawl_seen.add(root)
                searched.append(root)
                candidate_pages.append({
                    "url": root, "type": _page_type(root, reg),
                    "ok": bool(thtml), "emails": 0})
            if not thtml:
                identity_rejected_domains.add(reg)  # 取得できない＝検証不能
                continue
            # identity 一致（必須）。小売/求人/書店/別会社/エラーページはここで落ちる。
            if not _official_identity_matches_maker(thtml, root, mkr):
                identity_rejected_domains.add(reg)
                logger.info("web_research[%s] title-match REJECTED (identity mismatch): %s",
                            pid, root)
                continue
            # 既存の公式検証（媒体/NGO/代理店/preview/大企業/姓衝突）も必須。
            tv = cds.verify_official_candidate(
                root, thtml, root, mkr, project_terms | maker_terms,
                campaign_url=getattr(project, "source_url", None),
                source_type=getattr(project, "source_site", None))
            if tv.get("collision_detected"):
                identity_rejected_domains.add(reg)
                logger.info("web_research[%s] title-match REJECTED (%s): %s",
                            pid, tv.get("reason"), root)
                continue
            # 外部大手ブランド支配ガード（title fallback 専用）。maker 名は含むが identity の
            # 主体が別の大手ブランド（LG전자 等）＝別事業ライン/リセラー店舗は採用しない。
            _brand = _external_brand_in_identity(thtml, root, mkr)
            if _brand:
                identity_rejected_domains.add(reg)
                logger.info(
                    "web_research[%s] title-match REJECTED "
                    "(external_brand_identity_conflict brand=%s): %s", pid, _brand, root)
                continue
            # 採用。official_verified=True（root を取得し identity+verify 済み）。
            effective_official = root
            effective_domain = reg
            official_inferred = True
            official_verified = True
            logger.info("web_research[%s] title-match official ADOPTED: %s", pid, root)
            # 採用ページ + 代表パスからメール/フォーム/SNS を回収。追加 fetch は既存の
            # 注入 fetcher を使う（別 fetcher を作らない＝注入・レート制御を尊重）。
            base = root.rstrip("/")
            for path in ["", "/contact", "/contact-us", "/about",
                         "/pages/contact", "/company", "/about-us"]:
                if len(searched) >= _u_cap or _expired():
                    break
                murl = base + path
                if murl in crawl_seen:
                    continue
                mhtml = thtml if path == "" else fetch(murl)
                if path != "":
                    crawl_seen.add(murl)
                    searched.append(murl)
                    candidate_pages.append({
                        "url": murl, "type": _page_type(murl, effective_domain),
                        "ok": bool(mhtml), "emails": 0})
                if not mhtml:
                    continue
                for addr in cds.extract_emails(mhtml, site_domain):
                    if addr.lower() not in email_map:
                        _sc, _ti = cds.score_email(addr, effective_domain)
                        email_map[addr.lower()] = {
                            "email": addr, "score": _sc, "tier": _ti,
                            "email_owner": cds.classify_email_owner(
                                addr, effective_domain, site_domain),
                            "sources": [murl]}
                for link in cds.extract_links(mhtml, murl):
                    plat = _social_platform(link)
                    if plat:
                        consider_social(plat, link, 25, f"title:{murl}")
                    elif cds._same_domain(link, effective_domain) \
                            and cds._is_contact_url(link) and link not in forms:
                        forms.append(link)
                if cds._is_contact_url(murl) and murl not in forms:
                    forms.append(murl)
            break

    # 公式サイトがどこからも見つからない場合の最終手段：候補ドメインを生成して
    # 実在確認（GET＋本文の関連語チェック）し、確認できたら代表パスをミニクロール
    # してメール/SNS/フォームを抽出する（要件2）。
    domain_guess_used = False
    if not effective_official and own_fetcher and not _expired():
        guessed = guess_and_verify_official(
            keywords["maker_name"] or keywords["project_title"],
            keywords["creator_slug"], keywords["project_slug"],
            project_terms | maker_terms,
            maker_name=getattr(project, "maker_name", None),
        )
        if guessed:
            domain_guess_used = True
            effective_official = guessed
            effective_domain = cds._domain_of(guessed)
            mini = _make_fetcher()
            try:
                root = guessed.rstrip("/")
                for path in ["", "/contact", "/contact-us", "/about",
                             "/pages/contact", "/team", "/press"]:
                    if len(searched) >= _u_cap or _expired():
                        break
                    url = root + path
                    if url in crawl_seen:
                        continue
                    crawl_seen.add(url)
                    html = mini(url)
                    searched.append(url)
                    candidate_pages.append({
                        "url": url, "type": _page_type(url, effective_domain),
                        "ok": bool(html), "emails": 0,
                    })
                    if not html:
                        continue
                    for addr in cds.extract_emails(html, site_domain):
                        if addr.lower() not in email_map:
                            score, tier = cds.score_email(addr, effective_domain)
                            email_map[addr.lower()] = {
                                "email": addr, "score": score, "tier": tier,
                                "email_owner": cds.classify_email_owner(
                                    addr, effective_domain, site_domain),
                                "sources": [url]}
                    for link in cds.extract_links(html, url):
                        plat = _social_platform(link)
                        if plat:
                            consider_social(plat, link, 25, f"guessed:{url}")
                        elif cds._same_domain(link, effective_domain) and cds._is_contact_url(link):
                            if link not in forms:
                                forms.append(link)
                    if cds._is_contact_url(url) and url not in forms:
                        forms.append(url)
            finally:
                client = getattr(mini, "_client", None)
                if client is not None:
                    client.close()
            logger.info("web_research[%s] domain-guess official: %s", pid, guessed)

    pdfs = pdfs[:8]
    # 有用そうな PDF（press kit / catalog 等）からもメールを抽出（要件6）。
    # pypdf 未導入時は no-op。負荷を抑えるため上位 2 件まで。
    pdf_email_pages = 0
    for p in [x for x in pdfs if x.get("relevant")][:2]:
        got = cds.extract_from_pdf(p["url"], site_domain)
        if got["emails"]:
            pdf_email_pages += 1
        for addr in got["emails"]:
            if addr.lower() not in email_map:
                score, tier = cds.score_email(addr, effective_domain)
                email_map[addr.lower()] = {
                    "email": addr, "score": score, "tier": tier,
                    "email_owner": cds.classify_email_owner(
                        addr, effective_domain, site_domain),
                    "sources": [p["url"]]}
        for plat, link in (got["socials"] or {}).items():
            consider_social(plat, link, 20, f"pdf:{p['url']}")

    # 運営会社（platform）のメールは営業候補に含めない
    _non_platform = sorted(
        (e for e in email_map.values() if e["email_owner"] != "platform"),
        key=lambda e: e["score"],
        reverse=True,
    )
    # 検証済み公式ドメインに対する maker 所有関係を厳格に検証し、maker-owned と
    # 第三者（レビュー/ニュース/紹介/ディレクトリ/代理店/小売/unknown/検証不能）を
    # 分離する。第三者は discovered_emails には入れず third_party_emails へ（manual review）。
    # 公式サイト未確定のときだけ、裏付けの取れたドメインを算出する（Phase 3-②）。
    # effective_domain / official_site はここでも更新しない＝公式判定へは昇格させない。
    corroborated_domains: set[str] = set()
    if not effective_domain:
        corroborated_domains = build_domain_corroboration(
            email_map, root_verdicts, getattr(project, "maker_name", None),
            project_terms | maker_terms)
        if corroborated_domains:
            logger.info("web_research[%s] corroborated domain(s) for email rescue: %s",
                        pid, sorted(corroborated_domains))

    emails: list[dict] = []
    third_party_emails: list[dict] = []
    for e in _non_platform:
        owned, reason = _email_maker_ownership(
            e["email"], e.get("sources"), effective_domain,
            corroborated_domains=corroborated_domains,
        )
        rec = {**e, "maker_owned": owned, "ownership_reason": reason}
        (emails if owned else third_party_emails).append(rec)

    # フォームも同様に、検証済み公式ドメインに属するもののみ maker-owned とする。
    maker_forms: list[str] = []
    third_party_forms: list[str] = []
    for f in forms:
        (maker_forms if _form_maker_owned(f, effective_domain) else third_party_forms).append(f)
    forms = maker_forms

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
        # maker 所有検証で第三者として分離した件数（誤検出防止の可視化）。
        "third_party_emails": len(third_party_emails),
        "third_party_forms": len(third_party_forms),
        "elapsed_sec": round(time.monotonic() - _start, 1),
        "stop_reason": stop_reason,
        # Kickstarter 等の埋め込み websites 配列（要件 6）
        "ks_websites_present": bool(ks_websites and ks_websites["present"]),
        "ks_websites_count": (ks_websites["count"] if ks_websites else None),
        "ks_websites_registered": bool(ks_websites and ks_websites["registered"]),
    }

    # 探索フローの要約（要件 5）。UI とログで「どこまで進んだか」が分かる。
    flow_bits = [f"{provider}検索"]
    flow_bits.append(f"{len(search_records)}件取得")
    if effective_official:
        flow_bits.append(f"公式サイト({effective_domain})")
    elif ks_websites and ks_websites["present"] and not ks_websites["registered"]:
        # Kickstarter の埋め込み websites:[] = クリエイター未登録（公式サイト未発見の根拠）
        flow_bits.append("公式サイト未登録(KS websites:[])")
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
        "search_diagnostics": search_diagnostics,
        # 確定/推定した公式サイト（プラットフォーム URL は採用しない）。未発見なら None。
        "official_site_url": cds.official_site_or_none(effective_official),
        "keyword_candidates": keywords,
        "generated_queries": generated_queries,
        "searched_queries": searched_queries,
        "search_results": search_records,
        "searched_urls": searched,
        "candidate_pages": candidate_pages,
        # maker 所有と検証できたもののみ（誤検出防止）。
        "discovered_emails": emails,
        "discovered_forms": forms,
        # maker 所有と確認できなかった第三者連絡先（削除せず manual review 用に分離保持）。
        "third_party_emails": third_party_emails,
        "third_party_forms": third_party_forms,
        "discovered_socials": socials,
        "discovered_pdfs": pdfs,
        "primary_email": primary_email,
        "primary_contact_form_url": primary_form,
        "recommended_channel": channel,
        "confidence_score": score,
        "evidence_summary": evidence,
        "debug_counts": debug_counts,
        "research_flow": research_flow,
        "stop_reason": stop_reason,
        "notes": ", ".join(notes_bits),
    }


def _make_fetcher():
    """取得関数（url -> html|None）。

    クラウドファンディングページ（Kickstarter/Indiegogo/Ulule 等）は Cloudflare/JS で
    httpx だと空/403 になりやすい。そこでスクレイパーと同じ設定済み fetcher
    （既定 Playwright）を使う。初期化に失敗したら httpx にフォールバックする。
    取得ごとに HTTP ステータス・Content-Type・文字数をログに出す（要件 2）。
    """
    from app.config import settings

    method = getattr(settings, "scrape_fetcher", "httpx") or "httpx"
    try:
        from app.scrapers.fetcher import get_fetcher

        client = get_fetcher(
            method, rate_limit_seconds=RATE_LIMIT_SECONDS,
            timeout=FETCH_TIMEOUT, retries=1,
        )
    except Exception as exc:  # noqa: BLE001  Playwright 未導入等は httpx に退避
        logger.warning(
            "web_research fetcher init failed for '%s' (%s); falling back to httpx",
            method, exc,
        )
        from app.scrapers.http import HttpClient

        client = HttpClient(
            rate_limit_seconds=RATE_LIMIT_SECONDS, timeout=FETCH_TIMEOUT, retries=1
        )
    logger.info("web_research fetcher: %s (method=%s)", type(client).__name__, method)

    def fetch(url: str) -> str | None:
        try:
            html = client.get_text(url)
        except Exception as exc:  # noqa: BLE001  1 URL 失敗は無視
            logger.info(
                "web_research fetch FAILED %s: status=%s err=%s",
                url, getattr(client, "last_status", None), exc,
            )
            return None
        # 404/410/5xx 等のページからはメールを拾わない（要件: 404 URL 由来を採用しない）。
        # httpx は raise_for_status で弾くが、Playwright はステータスを見ず本文を返すため、
        # ここで last_status を確認して非 200 系の本文は破棄する。
        status = getattr(client, "last_status", None)
        if status is not None and status >= 400:
            logger.info(
                "web_research skip non-200 %s: status=%s (本文を採用しない)", url, status
            )
            return None
        logger.info(
            "web_research loaded %s: status=%s content-type=%s chars=%d",
            url, status,
            getattr(client, "last_content_type", None), len(html or ""),
        )
        return html

    fetch._client = client  # type: ignore[attr-defined]
    return fetch


# ---------------- DB 連携 ----------------
def run_web_research(
    db: Session, project: Project, *, fetch_fn=None, search_fn=None, progress_cb=None
) -> ContactDiscovery:
    """Web リサーチを実行し、最新の探索結果（ContactDiscovery）の web_* に保存する。

    既存の探索結果が無ければ先に自動探索を実行して土台を作る。Web 結果は web_*
    カラムに分離保存し、自動抽出（primary_email 等）/ AI 調査（ai_*）は上書きしない。
    失敗時は web_research_error に記録し、アプリは落とさない。
    progress_cb(message, pct) を渡すと 1 URL 巡回ごとに進捗を通知する。
    """
    research = cds._latest_research(db, project.id)
    row = cds.get_latest(db, project.id)
    if row is None:
        row = cds.run_discovery(db, project)

    now = datetime.now(timezone.utc)
    # read が済んだので接続をプールへ返却してから外部処理へ入る（外部処理中は
    # トランザクションを保持しない）。以降 project/research/row は既ロード値のみ参照。
    cds.release_connection(db)
    try:
        if progress_cb:
            progress_cb("検索エンジンで候補を収集中…", pct=0.0)
        result = web_research(
            project, research, fetch_fn=fetch_fn, search_fn=search_fn,
            progress_cb=progress_cb,
        )
        row.web_researched = True
        row.web_researched_at = now
        row.web_search_provider = result["search_provider"]
        row.web_search_diagnostics = result["search_diagnostics"] or None
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
        # 表示用の公式サイトが未設定/プラットフォームのままなら、Web 調査が推定した
        # 実際の企業ドメインで更新する（kickstarter/profile は採用しない）。
        if not cds.official_site_or_none(row.official_site_url) and result.get(
            "official_site_url"
        ):
            row.official_site_url = result["official_site_url"]
    except Exception as exc:  # noqa: BLE001  失敗してもアプリは落とさない
        logger.warning("web research failed (project=%s): %s", project.id, exc)
        row.web_researched = True
        row.web_researched_at = now
        row.web_research_error = str(exc)[:4000]

    db.commit()
    db.refresh(row)
    return row
