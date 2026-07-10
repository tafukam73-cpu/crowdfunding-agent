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

# EC モール / マーケットプレイス（メーカー公式サイトではない）。台湾・韓国・日本・グローバル。
MARKETPLACE_HOSTS = (
    "shopee.", "momoshop.com.tw", "momo.dm", "pchome.com.tw", "24h.pchome",
    "ruten.com.tw", "books.com.tw", "pinkoi.com", "yahoo.com", "tw.buy.yahoo",
    "rakuten.", "amazon.", "ebay.", "etsy.", "aliexpress.", "taobao.", "tmall.",
    "1688.com", "coupang.", "gmarket.", "11st.", "qoo10.", "lazada.", "shopify.com",
    "myshopify.com", "shoplineapp.com", "cyberbiz.co", "waca.ec", "meepshop.",
    "91app.", "shop.line.me", "page.line.me",
)

# ニュース / メディア / 情報サイト（案件について書いた記事であって公式サイトではない）。
# 記事タイトルにメーカー名が出るため一致してしまうが、公式サイトとして採用しない。
NEWS_HOSTS = (
    "thenewslens.com", "udn.com", "ettoday.net", "chinatimes.com", "ltn.com.tw",
    "setn.com", "tvbs.com.tw", "storm.mg", "cna.com.tw", "nownews.com",
    "businessweekly.com.tw", "bnext.com.tw", "technews.tw", "cool3c.com",
    "inside.com.tw", "mashdigi.com", "yahoo.com", "news.", "appledaily.",
    "prnewswire.", "businesswire.", "sportsv.net", "tsna.com.tw",
)

# 企業ディレクトリ / 集約 / 百科 / 求人 / SNS / 動画（公式サイトではない）。
DIRECTORY_HOSTS = (
    "findcompany.com.tw", "companyinfotw.com", "twincn.com", "iyp.com.tw",
    "crunchbase.com", "wikipedia.org", "wikiwand.com", "linkedin.com",
    "facebook.com", "instagram.com", "youtube.com", "youtu.be", "twitter.com",
    "x.com", "tiktok.com", "medium.com", "linktr.ee", "104.com.tw", "1111.com.tw",
    "glassdoor.", "yelp.", "tripadvisor.", "google.com", "bing.com",
    # サイトビルダーのトップページ（ブランドサイトではなくビルダー自身）
    "squarespace.com", "wixsite.com", "wix.com", "weebly.com", "godaddysites.com",
    "webnode.", "strikingly.com", "carrd.co", "notion.site",
)

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
    net = urlparse(url or "").netloc.lower()
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


def _match_any(host: str, hints) -> bool:
    return any(h in host for h in hints)


def is_marketplace(url: str) -> bool:
    return _match_any(_host(url), MARKETPLACE_HOSTS)


def is_directory(url: str) -> bool:
    return _match_any(_host(url), DIRECTORY_HOSTS)


def is_news(url: str) -> bool:
    return _match_any(_host(url), NEWS_HOSTS)


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
        "reasons": [],
    }
    ev: list[str] = result["evidence"]
    rs: list[str] = result["reasons"]

    if is_marketplace(url):
        result["verdict"] = "rejected"
        rs.append("EC モール/マーケットプレイス（公式サイトではない）")
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

    return result
