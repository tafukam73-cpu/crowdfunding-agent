"""情報源の所有者判定（source ownership classification）。

取得した URL / メール / SNS / フォームが「メーカー本人のもの」か、それとも
クラウドファンディング運営・販促支援・代理店・小売・メディア・短縮URL 等の
第三者のものかを **一般化可能なルール** で分類する。特定 gold 案件を通すための
ハードコードは置かない（ドメイン分類 × maker/ブランド/商品名の一致度で判定する）。

分類（ownership_class）:
  maker_official / maker_subdomain / crowdfunding_platform /
  crowdfunding_marketing_service / agency / distributor / retailer /
  press_media / unrelated_company / url_shortener / messenger /
  personal_email / unknown

方針:
- maker_official / maker_subdomain 以外のメールを maker の正式連絡先として自動採用しない。
- 分類はドメイン deny-list（運営/販促/短縮/既知代理店/小売/メディア）＋ maker 名・
  ブランド名・商品名・公式ドメインとの一致で決める（同名無関係大企業は名一致だけでは
  official にしない＝呼び出し側が campaign 証拠を要求する）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# --- ドメイン deny-list（クラス別・一般化） ---
CROWDFUNDING_PLATFORMS = frozenset({
    "kickstarter.com", "indiegogo.com", "zeczec.com", "wadiz.kr", "wadiz.co.kr",
    "ulule.com", "makuake.com", "camp-fire.jp", "campfire.jp", "greenfunding.jp",
    "readyfor.jp", "gofundme.com", "patreon.com", "fundrazr.com",
    "crowdfunder.co.uk", "machi-ya.jp", "machiya.jp", "for-good.net",
})
CROWDFUNDING_MARKETING = frozenset({
    "kickbooster.me", "backerkit.com", "pledgebox.com", "launchboom.com",
    "prefinery.com", "crowdox.com", "backercamp.com", "pledgemanager.com",
    "gleam.io", "viral-loops.com", "backerland.com", "getbackerkit.com",
})
URL_SHORTENERS = frozenset({
    "reurl.cc", "bit.ly", "tinyurl.com", "t.co", "ow.ly", "buff.ly", "cutt.ly",
    "rebrand.ly", "is.gd", "s.id", "han.gl", "vo.la", "pse.is", "goo.gl", "rb.gy",
    "lnkd.in",
})
# メッセンジャー / チャット（営業チャネルにはなり得るが「公式サイト」や「メール」ではない）。
MESSENGERS = frozenset({
    "m.me", "wa.me", "t.me", "line.me", "lin.ee", "pf.kakao.com", "open.kakao.com",
})
# 既知の代理店 / クラファン支援・代行会社（メーカー本人ではない）。拡張可能。
KNOWN_AGENCIES = frozenset({
    "ideafound.com", "brand-kr.com", "makerz.co.kr",
})
# 小売 / マーケットプレイス（メーカー公式ではない）。
RETAILERS = frozenset({
    "amazon.com", "amazon.co.jp", "ebay.com", "etsy.com", "aliexpress.com",
    "taobao.com", "tmall.com", "1688.com", "coupang.com", "gmarket.co.kr",
    "11st.co.kr", "qoo10.com", "lazada.com", "shopee.com", "rakuten.com",
    "rakuten.co.jp", "pchome.com.tw", "momoshop.com.tw", "ruten.com.tw",
    "books.com.tw", "pinkoi.com", "smartstore.naver.com",
})
# フリーメール（個人アドレス。ドメインでは maker 判定できない）。
PERSONAL_EMAIL = frozenset({
    "gmail.com", "googlemail.com", "naver.com", "hanmail.net", "daum.net",
    "nate.com", "kakao.com", "yahoo.com", "yahoo.co.jp", "yahoo.com.tw",
    "hotmail.com", "outlook.com", "live.com", "msn.com", "icloud.com", "me.com",
    "qq.com", "163.com", "126.com", "foxmail.com", "sina.com", "protonmail.com",
    "proton.me", "aol.com", "gmx.com", "gmx.net", "zoho.com",
})
# 無関係になりやすいグローバル大企業（同名一致で公式に誤採用されやすい）。名一致だけでは
# official にせず campaign 証拠を要求する（ここに載っていなくても呼び出し側の証拠要件で守る）。
MAJOR_UNRELATED_BRANDS = frozenset({
    "lg.com", "lge.com", "samsung.com", "sony.com", "apple.com", "google.com",
    "microsoft.com", "amazon.com", "panasonic.com", "philips.com", "bosch.com",
    "xiaomi.com", "huawei.com", "nike.com", "adidas.com",
})

_TWO_LEVEL_TLDS = frozenset({
    "co.kr", "com.tw", "co.uk", "com.au", "co.jp", "ne.jp", "or.jp", "com.hk",
    "com.sg", "co.nz", "com.cn", "com.br", "co.in", "com.mx", "com.tr",
    "com.my", "co.th", "com.ph", "com.vn", "co.id",
})


def host_of(url_or_host: str) -> str:
    s = (url_or_host or "").strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    elif "@" in s:  # email
        s = s.split("@", 1)[1]
    s = s.split(":")[0].split("/")[0]
    return s[4:] if s.startswith("www.") else s


def registrable_domain(url_or_host: str) -> str:
    """登録ドメイン（eTLD+1）。co.kr / com.tw 等の二段 TLD を考慮する。"""
    host = host_of(url_or_host)
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    last2 = ".".join(parts[-2:])
    if last2 in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return last2


def domain_token(url_or_host: str) -> str:
    reg = registrable_domain(url_or_host)
    return reg.split(".")[0] if reg else ""


def tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for tok in re.findall(r"[a-z0-9]+", (t or "").lower()):
            if len(tok) >= 3:
                out.add(tok)
    return out


def _name_matches_domain(dom_tok: str, name_tokens: set[str]) -> bool:
    """ドメイン先頭ラベルが maker/ブランド語と一致（完全一致 or 5 文字以上の包含）。"""
    if not dom_tok:
        return False
    for t in name_tokens:
        if dom_tok == t:
            return True
        if len(t) >= 5 and len(dom_tok) >= 5 and (t in dom_tok or dom_tok in t):
            return True
    return False


@dataclass
class Ctx:
    maker_name: str | None = None
    brand_name: str | None = None
    product_title: str | None = None
    official_domain: str | None = None  # 確定済み公式サイトの登録ドメイン（あれば）

    def name_tokens(self) -> set[str]:
        return tokens(self.maker_name or "", self.brand_name or "")


@dataclass
class Ownership:
    ownership_class: str
    score: int
    evidence: list[str] = field(default_factory=list)
    rejection_reason: str | None = None

    @property
    def is_maker(self) -> bool:
        return self.ownership_class in ("maker_official", "maker_subdomain")


def classify_domain(url_or_host: str, ctx: Ctx | None = None) -> Ownership:
    """ドメイン単位で所有者を分類する（URL/メール/SNS 共通の中核）。"""
    ctx = ctx or Ctx()
    host = host_of(url_or_host)
    if not host:
        return Ownership("unknown", 0, rejection_reason="empty_host")
    reg = registrable_domain(host)

    # 1) 明確な第三者ドメイン（deny-list）を先に確定する。
    if reg in CROWDFUNDING_PLATFORMS:
        return Ownership("crowdfunding_platform", 0, [f"platform:{reg}"],
                         "crowdfunding_platform")
    if reg in CROWDFUNDING_MARKETING:
        return Ownership("crowdfunding_marketing_service", 0, [f"marketing:{reg}"],
                         "crowdfunding_marketing_service")
    if reg in URL_SHORTENERS:
        return Ownership("url_shortener", 0, [f"shortener:{reg}"], "url_shortener")
    if reg in MESSENGERS or host in MESSENGERS:
        return Ownership("messenger", 0, [f"messenger:{reg}"], "messenger")
    if reg in KNOWN_AGENCIES:
        return Ownership("agency", 0, [f"agency:{reg}"], "agency")
    if reg in RETAILERS:
        return Ownership("retailer", 0, [f"retailer:{reg}"], "retailer")

    # 2) 確定公式ドメインとの一致（最も強い maker シグナル）。
    off = ctx.official_domain
    if off:
        off = registrable_domain(off)
        if reg == off:
            if host != reg and not host.startswith("www."):
                return Ownership("maker_subdomain", 90, [f"subdomain_of_official:{reg}"])
            return Ownership("maker_official", 100, [f"matches_official_domain:{reg}"])

    # 3) フリーメール / 個人ドメイン（ドメインでは maker と断定できない）。
    if reg in PERSONAL_EMAIL:
        return Ownership("personal_email", 20, [f"personal_provider:{reg}"],
                         "personal_email_provider")

    # 4) 同名の無関係大企業は「名一致のみ」では official にしない。
    name_tok = ctx.name_tokens()
    dom_tok = domain_token(host)
    name_hit = _name_matches_domain(dom_tok, name_tok)
    if reg in MAJOR_UNRELATED_BRANDS:
        if name_hit:
            # 例: maker_name が "LG" と誤抽出され lg.com に一致してしまうケース。
            return Ownership("unrelated_company", 10, [f"major_brand_name_collision:{reg}"],
                             "major_unrelated_brand_name_only")
        return Ownership("unrelated_company", 0, [f"major_brand:{reg}"], "unrelated_company")

    # 5) maker/ブランド名とドメインが一致 → maker_official 候補（名一致は 1 証拠）。
    if name_hit:
        return Ownership("maker_official", 60, [f"domain_name_match:{dom_tok}"])

    return Ownership("unknown", 0, rejection_reason="no_ownership_evidence")


# --- メールの役割分類（Phase 4） ---
HIGH_VALUE_LOCALS = frozenset({
    "sales", "wholesale", "business", "partnership", "partnerships", "partner",
    "partners", "export", "international", "bd", "reseller", "distributor",
    "distribution", "b2b", "global",
})
MID_VALUE_LOCALS = frozenset({"contact", "hello", "info", "inquiry", "enquiries",
                              "enquiry", "cs", "customerservice"})
SUPPORT_LOCALS = frozenset({"support", "help", "care", "service", "cs"})
# 除外/降格（営業に使えない機能アドレス）。
EXCLUDE_LOCALS = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "privacy",
    "abuse", "webmaster", "postmaster", "security", "careers", "career", "jobs",
    "recruit", "legal", "copyright", "dmca", "dpo", "gdpr", "unsubscribe",
})


def email_role(email: str) -> str:
    """メール local-part の営業役割を返す：high / mid / support / person / exclude / other。"""
    local = (email or "").split("@", 1)[0].strip().lower()
    base = re.split(r"[.+_-]", local)[0] if local else ""
    if local in EXCLUDE_LOCALS or base in EXCLUDE_LOCALS:
        return "exclude"
    if local in HIGH_VALUE_LOCALS or base in HIGH_VALUE_LOCALS:
        return "high"
    if local in MID_VALUE_LOCALS or base in MID_VALUE_LOCALS:
        return "mid"
    if local in SUPPORT_LOCALS or base in SUPPORT_LOCALS:
        return "support"
    # それ以外は人物メールの可能性（founder 名等との一致は呼び出し側で判定）。
    return "person"


# 営業適性ランキング（小さいほど優先）。
_ROLE_RANK = {"high": 0, "person": 1, "mid": 2, "support": 3, "other": 4, "exclude": 9}


# fallback として保持可能なクラス（maker 直通ではないが営業チャネルになり得る）。
# 値は contact_route ラベル。platform / marketing / shortener / messenger / retailer /
# unrelated は fallback にも入れない（営業価値が無い or 第三者運営）。
_FALLBACK_ROUTE = {"agency": "agency", "distributor": "distributor"}


def classify_email(email: str, ctx: Ctx | None = None,
                   person_names: set[str] | None = None) -> dict:
    """メールを ownership × role で評価し採用可否を返す（Phase 1/4）。

    採用は 3 段階に分ける（無条件全消しを避け、営業価値のある連絡先を保持する）:
      - accepted_as_maker_contact : maker 公式ドメイン / 公式ページ掲載の人物メールのみ。
      - accepted_as_fallback_contact : maker 直通ではないが営業チャネルになり得る代理店
        （agency）・正規販売/地域窓口（distributor）。contact_route にルートを入れる。
      - どちらでもない : platform / marketing / shortener / messenger / retailer /
        unrelated は rejected、unknown は低 confidence 保留（削除しない）。
    role が exclude（noreply/careers 等）の機能アドレスは maker/fallback とも不採用。
    """
    addr = (email or "").strip().lower()
    own = classify_domain(addr, ctx)
    role = email_role(addr)
    person_hit = False
    if person_names:
        local = addr.split("@", 1)[0]
        ltok = set(re.split(r"[.+_-]", local))
        person_hit = bool(ltok & {p.lower() for p in person_names})

    rejection: str | None = None
    accepted = False
    fallback = False
    contact_route: str | None = None

    if role == "exclude":
        rejection = f"role_exclude:{role}"
    elif own.is_maker:
        accepted = True
        contact_route = "maker"
    elif own.ownership_class == "personal_email" and person_hit:
        # 公式サイトに掲載された人物名と local-part が一致するフリーメールは人物連絡先。
        accepted = True
        contact_route = "maker"
    elif own.ownership_class in _FALLBACK_ROUTE:
        # 代理店 / 正規販売窓口：maker 直通ではないが営業チャネルとして保持する。
        fallback = True
        contact_route = _FALLBACK_ROUTE[own.ownership_class]
        rejection = "not_direct_maker_contact"
    else:
        rejection = own.rejection_reason or f"ownership:{own.ownership_class}"

    if accepted:
        confidence = "high" if own.is_maker else "medium"
    elif fallback:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "email": addr,
        "source_domain": registrable_domain(addr),
        "ownership_class": own.ownership_class,
        "ownership_score": own.score,
        "role_type": role,
        "person_match": person_hit,
        "official_domain_match": own.ownership_class in ("maker_official", "maker_subdomain"),
        "evidence": own.evidence,
        "accepted_as_maker_contact": accepted,
        "accepted_as_fallback_contact": fallback,
        "contact_route": contact_route,
        "confidence": confidence,
        "rejection_reason": rejection,
        "rank": _ROLE_RANK.get(role, 4) if accepted else 99,
    }


def rank_maker_emails(emails: list[str], ctx: Ctx | None = None,
                      person_names: set[str] | None = None) -> list[dict]:
    """採用可能な maker メールを営業適性順に並べて返す（採用不可は除外）。"""
    scored = [classify_email(e, ctx, person_names) for e in emails]
    accepted = [s for s in scored if s["accepted_as_maker_contact"]]
    accepted.sort(key=lambda s: (s["rank"], s["email"]))
    return accepted


# --- SNS 自己アカウント（プラットフォーム本体）判定（Phase 6） ---
_PLATFORM_HANDLE_RE = re.compile(
    r"(?:^|/|@)(kickstarter|indiegogo|zeczec(?:_com)?|wadiz|ulule|makuake)(?:/|$|\b)",
    re.IGNORECASE,
)


def is_platform_self_social(url: str) -> bool:
    """SNS URL がクラファン運営自身の公式アカウントか（facebook.com/kickstarter 等）。

    SNS ドメイン上で、パス/ハンドルが運営名そのものなら運営の自己アカウント＝maker でない。
    """
    u = (url or "").lower()
    host = host_of(u)
    social_hosts = ("facebook.com", "instagram.com", "twitter.com", "x.com",
                    "youtube.com", "youtu.be", "tiktok.com", "pinterest.com",
                    "linkedin.com", "reddit.com")
    if not any(host == h or host.endswith("." + h) for h in social_hosts):
        return False
    path = urlparse(u).path if "://" in u else "/" + u.split("/", 1)[-1]
    return bool(_PLATFORM_HANDLE_RE.search(path or ""))
