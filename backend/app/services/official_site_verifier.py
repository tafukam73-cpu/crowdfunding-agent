"""公式サイト候補の検証（安全な確定判定）。

Zeczec 案件の公式サイト候補（詳細ページ直リンク・検索結果）を「本当にそのメーカー/
ブランドの公式サイトか」を証拠つきで検証する。検索スニペットや無関係な EC モール・
企業ディレクトリ・販売代理店を公式サイトとして確定しないための門番。

判定材料（要件）:
- サイトタイトル / og:site_name
- JSON-LD の Organization / Brand / WebSite（name・legalName）
- 商品名・ブランド名・メーカー名との一致（ドメイン・本文の双方）
- EC モール / マーケットプレイス / 企業ディレクトリ / 集約サイトでないこと

設計:
- HTML 解析は純粋関数（extract_site_identity / verify_candidate）。fetch は注入。
- verdict は official / candidate / rejected。official のみ confidence=high/medium を付ける。
- 確定できなければ candidate のまま（推測で確定しない）。証拠（evidence）を必ず残す。
"""
from __future__ import annotations

import html as _html
import json
import re
from urllib.parse import urlparse

from app.services import source_ownership as _so

# --- ホスト判定の規則 -------------------------------------------------------- #
# かつては「文字列の部分一致」で判定していたため、``x.com`` が ``brandx.com`` /
# ``matrix.com`` / ``lumix.com`` などに誤ヒットし、正当なメーカー公式サイトを
# ディレクトリとして棄却していた。エントリごとに**意図**が異なるため、
# 3 種類の規則を明示して照合する（自動判別はしない）。
#
#   "domain"     … 登録ドメイン/ホスト名そのもの。
#                  host == value または host.endswith("." + value)
#   "brand"      … 任意 TLD で展開するブランド（amazon.com / amazon.co.jp / amazon.de）。
#                  registrable_domain の先頭ラベル == value
#   "hostprefix" … ホストの先頭ラベル列を指定（news.yahoo.co.jp の "news"）。
#                  host == value または host.startswith(value + ".")
_RULE_DOMAIN = "domain"
_RULE_BRAND = "brand"
_RULE_HOSTPREFIX = "hostprefix"

# EC モール / マーケットプレイス（メーカー公式サイトではない）。台湾・韓国・日本・グローバル。
MARKETPLACE_RULES = (
    (_RULE_BRAND, "shopee"),            # shopee.tw / shopee.sg / shopee.co.id
    (_RULE_DOMAIN, "momoshop.com.tw"),
    (_RULE_DOMAIN, "momo.dm"),
    (_RULE_DOMAIN, "pchome.com.tw"),
    (_RULE_HOSTPREFIX, "24h.pchome"),   # 24h.pchome.com.tw
    (_RULE_DOMAIN, "ruten.com.tw"),
    (_RULE_DOMAIN, "books.com.tw"),
    (_RULE_DOMAIN, "pinkoi.com"),
    (_RULE_DOMAIN, "yahoo.com"),
    (_RULE_HOSTPREFIX, "tw.buy.yahoo"),  # tw.buy.yahoo.com
    (_RULE_BRAND, "rakuten"),           # rakuten.co.jp / rakuten.com
    (_RULE_BRAND, "amazon"),            # amazon.com / amazon.co.jp / amazon.de
    (_RULE_BRAND, "ebay"),              # ebay.com / ebay.co.uk
    (_RULE_BRAND, "etsy"),
    (_RULE_BRAND, "aliexpress"),
    (_RULE_BRAND, "taobao"),
    (_RULE_BRAND, "tmall"),
    (_RULE_DOMAIN, "1688.com"),
    (_RULE_BRAND, "coupang"),           # coupang.com / coupang.co.kr
    (_RULE_BRAND, "gmarket"),           # gmarket.co.kr
    (_RULE_BRAND, "11st"),              # 11st.co.kr
    (_RULE_BRAND, "qoo10"),             # qoo10.jp / qoo10.sg
    (_RULE_BRAND, "lazada"),
    (_RULE_DOMAIN, "shopify.com"),
    (_RULE_DOMAIN, "myshopify.com"),
    (_RULE_DOMAIN, "shoplineapp.com"),
    (_RULE_DOMAIN, "cyberbiz.co"),
    (_RULE_DOMAIN, "waca.ec"),
    (_RULE_BRAND, "meepshop"),
    (_RULE_BRAND, "91app"),
    (_RULE_DOMAIN, "shop.line.me"),
    (_RULE_DOMAIN, "page.line.me"),
)

# ニュース / メディア / 情報サイト（案件について書いた記事であって公式サイトではない）。
# 記事タイトルにメーカー名が出るため一致してしまうが、公式サイトとして採用しない。
NEWS_RULES = (
    (_RULE_DOMAIN, "thenewslens.com"),
    (_RULE_DOMAIN, "udn.com"),
    (_RULE_DOMAIN, "ettoday.net"),
    (_RULE_DOMAIN, "chinatimes.com"),
    (_RULE_DOMAIN, "ltn.com.tw"),
    (_RULE_DOMAIN, "setn.com"),
    (_RULE_DOMAIN, "tvbs.com.tw"),
    (_RULE_DOMAIN, "storm.mg"),
    (_RULE_DOMAIN, "cna.com.tw"),
    (_RULE_DOMAIN, "nownews.com"),
    (_RULE_DOMAIN, "businessweekly.com.tw"),
    (_RULE_DOMAIN, "bnext.com.tw"),
    (_RULE_DOMAIN, "technews.tw"),
    (_RULE_DOMAIN, "cool3c.com"),
    (_RULE_DOMAIN, "inside.com.tw"),
    (_RULE_DOMAIN, "mashdigi.com"),
    (_RULE_DOMAIN, "yahoo.com"),
    (_RULE_HOSTPREFIX, "news"),         # news.yahoo.co.jp / news.mynavi.jp
    (_RULE_BRAND, "appledaily"),        # appledaily.com / appledaily.com.tw
    (_RULE_BRAND, "prnewswire"),
    (_RULE_BRAND, "businesswire"),
    (_RULE_DOMAIN, "sportsv.net"),
    (_RULE_DOMAIN, "tsna.com.tw"),
)

# 企業ディレクトリ / 集約 / 百科 / 求人 / SNS / 動画（公式サイトではない）。
DIRECTORY_RULES = (
    (_RULE_DOMAIN, "findcompany.com.tw"),
    (_RULE_DOMAIN, "companyinfotw.com"),
    (_RULE_DOMAIN, "twincn.com"),
    (_RULE_DOMAIN, "iyp.com.tw"),
    (_RULE_DOMAIN, "crunchbase.com"),
    (_RULE_DOMAIN, "wikipedia.org"),
    (_RULE_DOMAIN, "wikiwand.com"),
    (_RULE_DOMAIN, "linkedin.com"),
    (_RULE_DOMAIN, "facebook.com"),
    (_RULE_DOMAIN, "instagram.com"),
    (_RULE_DOMAIN, "youtube.com"),
    (_RULE_DOMAIN, "youtu.be"),
    (_RULE_DOMAIN, "twitter.com"),
    (_RULE_DOMAIN, "x.com"),            # ← 旧・部分一致では brandx.com 等に誤ヒットしていた
    (_RULE_DOMAIN, "tiktok.com"),
    (_RULE_DOMAIN, "medium.com"),
    (_RULE_DOMAIN, "linktr.ee"),
    (_RULE_DOMAIN, "104.com.tw"),
    (_RULE_DOMAIN, "1111.com.tw"),
    (_RULE_BRAND, "glassdoor"),         # glassdoor.com / glassdoor.co.uk
    (_RULE_BRAND, "yelp"),              # yelp.com / yelp.co.jp
    (_RULE_BRAND, "tripadvisor"),       # tripadvisor.com / tripadvisor.jp
    (_RULE_DOMAIN, "google.com"),
    (_RULE_DOMAIN, "bing.com"),
    # サイトビルダーのトップページ（ブランドサイトではなくビルダー自身）
    (_RULE_DOMAIN, "squarespace.com"),
    (_RULE_DOMAIN, "wixsite.com"),
    (_RULE_DOMAIN, "wix.com"),
    (_RULE_DOMAIN, "weebly.com"),
    (_RULE_DOMAIN, "godaddysites.com"),
    (_RULE_BRAND, "webnode"),           # webnode.com / webnode.jp
    (_RULE_DOMAIN, "strikingly.com"),
    (_RULE_DOMAIN, "carrd.co"),
    (_RULE_DOMAIN, "notion.site"),
)


def _legacy_hosts(rules) -> tuple[str, ...]:
    """旧来の文字列タプル表現を規則から復元する（後方互換の参照用）。

    照合には使わない。外部が ``MARKETPLACE_HOSTS`` 等を参照していても壊れないよう
    残すためのもので、規則との二重管理（ドリフト）を避けるため導出する。
    """
    out = []
    for kind, value in rules:
        if kind == _RULE_DOMAIN:
            out.append(value)
        elif kind == _RULE_BRAND:
            out.append(value + ".")
        else:  # hostprefix
            out.append(value if "." in value else value + ".")
    return tuple(out)


# 後方互換：旧定数を維持する（判定には _RULES を使う）。
MARKETPLACE_HOSTS = _legacy_hosts(MARKETPLACE_RULES)
NEWS_HOSTS = _legacy_hosts(NEWS_RULES)
DIRECTORY_HOSTS = _legacy_hosts(DIRECTORY_RULES)

# ブログプラットフォーム（記事であってメーカー公式サイトではない）。
# 記事本文にメーカー名も商品名も出るため素性一致してしまうが、公式サイトにしない。
# **登録ドメインのサフィックス一致**で判定する（部分一致にすると独自ドメインの
# 企業ブログ blog.example.com まで巻き込むため）。
BLOG_PLATFORM_HOSTS = (
    "tistory.com", "blog.naver.com", "blog.me", "post.naver.com",
    "brunch.co.kr", "postype.com", "egloos.com",
    "blogspot.com", "blogger.com", "medium.com", "substack.com",
    "note.com", "ameblo.jp", "hatenablog.com", "hatenadiary.com",
    "hatena.ne.jp", "livedoor.jp", "livedoor.blog", "fc2.com",
    "wordpress.com", "tumblr.com", "exblog.jp", "seesaa.net",
    "goo.ne.jp", "jugem.jp", "cocolog-nifty.com",
    "pixnet.net", "xuite.net", "sina.com.cn", "csdn.net", "jianshu.com",
)

# 小売店・取扱店を示す語（サイト素性に出たら「メーカー本人ではない」証拠）。
# 判定対象は title / og:site_name / Organization 名などの**素性テキストのみ**。
# 本文まで見るとメーカー自社サイトの Distributors ページで誤爆する。
RESELLER_HINTS = (
    "retailer", "reseller", "dealer", "distributor", "stockist",
    "authorized dealer", "authorised dealer", "official distributor",
    "판매점", "판매처", "공식판매점", "공식 판매점", "대리점", "총판",
    "유통사", "입점", "구매처", "취급점",
    "販売店", "取扱店", "正規販売店", "代理店", "総代理店", "販売代理店", "購入先",
)
# 自社EC・公式ストアを示す語（RESELLER_HINTS の誤爆を打ち消す）。
# 「公式販売店(공식판매점)」は小売だが「公式ストア(공식몰)」はメーカー自社。
OFFICIAL_STORE_HINTS = (
    "official store", "official shop", "official site", "official online store",
    "brand store", "공식몰", "공식 몰", "공식스토어", "공식 스토어",
    "공식 온라인몰", "공식홈페이지", "공식 홈페이지",
    "オフィシャルストア", "公式ストア", "公式サイト", "公式オンラインストア",
)
# 「ブランド名 ｜ 別サイト名」形式の区切り文字。
_SEPARATOR_RE = re.compile(r"\s*[|｜/／>»:：·・–—]\s*|\s+-\s+")

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_ORG_TYPES = {
    "organization", "corporation", "localbusiness", "onlinestore", "store",
    "brand", "website", "ngo", "educationalorganization", "sportsorganization",
}


def _host(url: str) -> str:
    """URL からホストを取り出して正規化する。

    小文字化 / 認証情報の除去 / ポート番号の除去 / 末尾ドットの除去を行い、
    既存仕様どおり先頭の ``www.`` を落とす。解析できない場合は空文字を返し、
    呼び出し側では「どのリストにも一致しない（＝棄却しない）」安全側に倒す。
    """
    try:
        net = urlparse(url or "").netloc
    except ValueError:
        return ""
    net = (net or "").strip().lower()
    if not net:
        return ""
    if "@" in net:                       # user:pass@host
        net = net.rsplit("@", 1)[-1]
    if net.startswith("["):              # IPv6 リテラル [::1]:8080
        net = net[1:].split("]", 1)[0]
    else:
        net = net.split(":", 1)[0]       # ポート除去
    net = net.rstrip(".")                # 末尾ドット（FQDN 表記）除去
    return net[4:] if net.startswith("www.") else net


def _domain_token(url: str) -> str:
    """登録ドメインの先頭ラベル（moresie.com → moresie）。"""
    host = _host(url)
    if not host:
        return ""
    parts = host.split(".")
    # co.uk / com.tw 等の二段 TLD を避けて、意味のある最長ラベルを採る
    if len(parts) >= 2:
        return parts[0]
    return host


def _host_matches(host: str, rules) -> bool:
    """正規化済みホストが規則群のいずれかに一致するか。

    **部分一致はしない。** ``x.com`` が ``brandx.com`` に一致するような
    誤判定を構造的に防ぐため、規則の種別ごとに照合方法を分ける。
    """
    if not host:
        return False
    brand = _so.domain_token(host)   # 登録ドメインの先頭ラベル（二段 TLD 対応）
    for kind, value in rules:
        if kind == _RULE_DOMAIN:
            if host == value or host.endswith("." + value):
                return True
        elif kind == _RULE_BRAND:
            if brand and brand == value:
                return True
        elif kind == _RULE_HOSTPREFIX:
            if host == value or host.startswith(value + "."):
                return True
    return False


def _match_any(host: str, hints) -> bool:
    """後方互換のために残す旧 API（部分一致）。

    新しい判定では使わない。``x.com`` が ``brandx.com`` に一致する問題があるため、
    新規コードでは ``_host_matches`` を使うこと。
    """
    return any(h in host for h in hints)


def is_marketplace(url: str) -> bool:
    return _host_matches(_host(url), MARKETPLACE_RULES)


def is_directory(url: str) -> bool:
    return _host_matches(_host(url), DIRECTORY_RULES)


def is_news(url: str) -> bool:
    return _host_matches(_host(url), NEWS_RULES)


def is_blog_platform(url: str) -> bool:
    """URL がブログプラットフォーム上の記事かを返す。

    部分一致は使わない。``blog.mycompany.com`` のような**独自ドメインの
    企業ブログ**を巻き込まないよう、サフィックス一致で判定する。
    全エントリが完全ドメインのため ``_RULE_DOMAIN`` として照合する。
    """
    return _host_matches(
        _host(url), tuple((_RULE_DOMAIN, d) for d in BLOG_PLATFORM_HOSTS)
    )


def _tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for tok in re.findall(r"[a-z0-9]+", (t or "").lower()):
            if len(tok) >= 3:
                out.add(tok)
    return out


def _walk_org(node, orgs: list[dict]) -> None:
    """JSON-LD を再帰し Organization/Brand/WebSite の name/legalName を集める。"""
    if isinstance(node, dict):
        t = node.get("@type")
        types = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
        if any(str(x).lower() in _ORG_TYPES for x in types):
            orgs.append({
                "type": ",".join(str(x) for x in types),
                "name": node.get("name") if isinstance(node.get("name"), str) else None,
                "legalName": node.get("legalName")
                if isinstance(node.get("legalName"), str) else None,
            })
        for v in node.values():
            _walk_org(v, orgs)
    elif isinstance(node, list):
        for v in node:
            _walk_org(v, orgs)


def extract_site_identity(html: str) -> dict:
    """候補サイト HTML から素性（site_name / org名 / 法人名 / title）を抽出する（純粋）。"""
    text = html or ""
    m = re.search(
        r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']*)["\']',
        text, re.IGNORECASE,
    )
    site_name = _html.unescape(m.group(1)).strip() if m else None
    tm = _TITLE_RE.search(text)
    title = _html.unescape(re.sub(r"\s+", " ", tm.group(1))).strip() if tm else None

    orgs: list[dict] = []
    for block in _JSONLD_RE.findall(text):
        raw = block.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        _walk_org(data, orgs)

    org_name = next((o["name"] for o in orgs if o.get("name")), None)
    legal_name = next((o["legalName"] for o in orgs if o.get("legalName")), None)
    return {
        "site_name": site_name or None,
        "title": title or None,
        "org_name": org_name,
        "legal_name": legal_name,
        "org_count": len(orgs),
        "has_org_jsonld": bool(orgs),
    }


def has_reseller_hint(text: str | None) -> bool:
    """素性テキストが小売・取扱店を示すか（公式ストア語は打ち消す）。"""
    if not text:
        return False
    low = text.lower()
    if not any(h.lower() in low for h in RESELLER_HINTS):
        return False
    # 「公式ストア」「공식몰」等の自社EC表現しか無い場合は小売とみなさない。
    # ただし「공식판매점（公式販売店）」のように小売語を含む複合語は小売のまま。
    stripped = low
    for h in OFFICIAL_STORE_HINTS:
        stripped = stripped.replace(h.lower(), " ")
    return any(h.lower() in stripped for h in RESELLER_HINTS)


def _split_identity_segments(text: str | None) -> list[str]:
    """素性テキストを区切りで分割する（'ブランド ｜ 別サイト名' の検出用）。"""
    if not text:
        return []
    return [s.strip() for s in _SEPARATOR_RE.split(text) if s and s.strip()]


def looks_reseller_page(identity_texts, maker_name: str | None) -> str | None:
    """小売・取扱店ページらしさの理由を返す（該当しなければ None）。

    呼び出し側は **ドメインがブランド名と一致しない場合にのみ** 使うこと。
    自社ドメインであれば運営者はメーカー本人なので、この判定は適用しない。
    """
    texts = [t for t in identity_texts if t]
    for t in texts:
        if has_reseller_hint(t):
            return f"サイト素性 '{t}' に小売・取扱店を示す語がある"
    # 「ブランド名 ｜ 別サイト名」形式：maker 名が一部セグメントにしか現れない
    for t in texts:
        segs = _split_identity_segments(t)
        if len(segs) < 2 or not maker_name:
            continue
        hit = [s for s in segs if _contains(s, maker_name)]
        if hit and len(hit) < len(segs):
            others = [s for s in segs if s not in hit]
            return (
                f"サイト素性 '{t}' がブランド名と別の運営者名 "
                f"'{others[0]}' に分かれている（取扱店の可能性）"
            )
    return None


def _contains(haystack: str | None, needle: str | None) -> bool:
    if not haystack or not needle:
        return False
    h = haystack.lower().strip()
    n = needle.lower().strip()
    return bool(n) and (n in h or h in n)


def verify_candidate(
    url: str,
    html: str | None,
    *,
    maker_name: str | None,
    product_name: str | None,
    source_site_domain: str | None = None,
) -> dict:
    """1 候補を検証して verdict/confidence/evidence を返す。

    verdict:
      - "rejected": EC モール/ディレクトリ/取得不能。公式サイトにしない。
      - "official": 素性がメーカー/ブランド/商品名と一致（confidence high/medium）。
      - "candidate": 取得できたが確定に足る一致が無い（confidence low、候補のまま）。
    """
    result = {
        "url": url,
        "verdict": "candidate",
        "confidence": "low",
        "evidence": [],
        "org_name": None,
        "legal_name": None,
        "site_name": None,
        "site_role": "unknown",
        "reasons": [],
    }
    ev: list[str] = result["evidence"]
    rs: list[str] = result["reasons"]

    if is_marketplace(url):
        result["verdict"] = "rejected"
        rs.append("EC モール/マーケットプレイス（公式サイトではない）")
        return result
    if is_blog_platform(url):
        # ブログ記事はメーカー名・商品名を本文に含むため素性一致するが、
        # 企業公式サイトではない。confidence 昇格経路に到達させない。
        result["verdict"] = "rejected"
        result["site_role"] = "blog"
        rs.append("ブログプラットフォーム上の記事（企業公式サイトではない）")
        return result
    if is_directory(url):
        result["verdict"] = "rejected"
        rs.append("企業ディレクトリ/集約/SNS/サイトビルダー（公式サイトではない）")
        return result
    if is_news(url):
        result["verdict"] = "rejected"
        rs.append("ニュース/メディア記事（案件を報じた記事であり公式サイトではない）")
        return result
    if source_site_domain and source_site_domain in _host(url):
        result["verdict"] = "rejected"
        rs.append("収集元プラットフォームのドメイン")
        return result
    if not html:
        result["verdict"] = "rejected"
        rs.append("取得不能（404/非200/ネットワーク）")
        return result

    ident = extract_site_identity(html)
    result["org_name"] = ident["org_name"]
    result["legal_name"] = ident["legal_name"]
    result["site_name"] = ident["site_name"]

    dom_tok = _domain_token(url)
    brand_tokens = _tokens(maker_name or "")
    product_tokens = _tokens(product_name or "")

    # S1: ドメイン先頭ラベルがブランド語と一致（moresie == MORESIE）。
    # 短い部分一致（"step" ⊂ "onestepsoftware"）による誤判定を避け、完全一致か
    # 5 文字以上の包含のみ採用する。
    def _dom_match(t: str) -> bool:
        if not t or not dom_tok:
            return False
        if dom_tok == t:
            return True
        return len(t) >= 5 and len(dom_tok) >= 5 and (t in dom_tok or dom_tok in t)

    s1 = any(_dom_match(t) for t in brand_tokens)
    if s1:
        ev.append(f"ドメイン '{dom_tok}' がブランド名と一致")

    # S2: og:site_name / org名 / title がメーカー名（中文含む）と一致
    identity_texts = [ident["site_name"], ident["org_name"], ident["title"]]
    s2 = any(_contains(x, maker_name) for x in identity_texts)
    if s2:
        hit = next(x for x in identity_texts if _contains(x, maker_name))
        ev.append(f"サイト素性 '{hit}' がメーカー名 '{maker_name}' と一致")

    # S3: 商品名（英字トークン）が title/site_name/org に出現
    id_blob = " ".join(x for x in identity_texts if x).lower()
    s3 = bool(product_tokens) and any(t in id_blob for t in product_tokens)
    if s3:
        ev.append("商品名トークンがサイト素性に出現")

    # S4: Organization/Brand の JSON-LD が存在
    s4 = ident["has_org_jsonld"]
    if s4:
        ev.append(f"Organization/Brand JSON-LD あり（{ident['org_count']} 件）")

    # 判定方針：ドメイン語の一致（S1）だけでは確定しない（"single" 等の一般語や偶然の
    # 一致で無関係サイトを公式にしないため）。素性（og:site_name / Organization 名 = S2）
    # または JSON-LD 法人情報（S4）・商品名一致（S3）による裏付けを必須にする。
    if s2 and (s1 or s4 or s3):
        result["verdict"], result["confidence"] = "official", "high"
    elif s1 and s4:
        result["verdict"], result["confidence"] = "official", "high"
    elif s2:
        # 素性がメーカー名と一致（ドメインが違っても実在の裏付け）→ 中確度で確定
        result["verdict"], result["confidence"] = "official", "medium"
    elif s1 and s3:
        result["verdict"], result["confidence"] = "official", "medium"
    elif s3 and s4:
        result["verdict"], result["confidence"] = "official", "medium"
    else:
        result["verdict"], result["confidence"] = "candidate", "low"
        if s1:
            rs.append("ドメイン語の一致のみで素性の裏付けが無いため確定せず候補扱い")
        else:
            rs.append("素性がメーカー/ブランド/商品名と一致せず確定不可（候補のまま）")

    # 小売・取扱店の降格：**ドメインがブランド名と一致しない（S1 偽）ときだけ**適用する。
    # 自社ドメインなら運営者はメーカー本人であり、小売語があっても降格しない
    # （メーカー自社サイトの Distributors ページ等を落とさないため）。
    if result["verdict"] == "official" and not s1:
        reseller_reason = looks_reseller_page(identity_texts, maker_name)
        if reseller_reason:
            result["verdict"], result["confidence"] = "candidate", "low"
            result["site_role"] = "reseller_like"
            rs.append(
                f"{reseller_reason}。運営者がメーカー本人と確認できないため確定せず候補扱い"
            )
            return result

    if result["verdict"] == "official":
        result["site_role"] = "maker"

    return result
