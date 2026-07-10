"""公式サイト検証・socials 抽出・検証配線のオフライン検証（fixture 中心）。

実ネットワーク不要。EC モール/ディレクトリ/代理店の除外、ブランド一致による確定、
SNS 相互リンク（サイト共通枠の除外）、低信頼データによる上書き防止を検証する。

実行（backend ディレクトリで）:
    python tests/test_official_site_verifier.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.models.project import Project, SourceSite  # noqa: E402
from app.scrapers.zeczec_detail import parse_detail, parse_socials  # noqa: E402
from selectolax.parser import HTMLParser  # noqa: E402
from app.services import official_site_verifier as osv  # noqa: E402
from app.services import zeczec_enrichment_service as zes  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


_OFFICIAL_HTML = (
    '<html><head><title>MORESIE 淨白貼片 官方網站</title>'
    '<meta property="og:site_name" content="MORESIE">'
    '<script type="application/ld+json">{"@type":"Organization",'
    '"name":"MORESIE","legalName":"摩雷斯股份有限公司"}</script>'
    "</head><body>about us</body></html>"
)


def test_identity_extraction():
    print("test_identity_extraction")
    ident = osv.extract_site_identity(_OFFICIAL_HTML)
    check("og:site_name 抽出", ident["site_name"] == "MORESIE")
    check("Organization name 抽出", ident["org_name"] == "MORESIE")
    check("legalName（運営法人名）抽出", ident["legal_name"] == "摩雷斯股份有限公司")
    check("Organization JSON-LD 検出", ident["has_org_jsonld"] is True)


def test_verify_official_match():
    print("test_verify_official_match")
    v = osv.verify_candidate("https://moresie.com", _OFFICIAL_HTML,
                             maker_name="MORESIE", product_name="INOPRO")
    check("公式と確定", v["verdict"] == "official")
    check("high 確度", v["confidence"] == "high")
    check("法人名を証拠に保持", v["legal_name"] == "摩雷斯股份有限公司")
    check("証拠が残る", len(v["evidence"]) >= 1)


def test_verify_marketplace_and_directory():
    print("test_verify_marketplace_and_directory")
    for url in ("https://shopee.tw/xx", "https://www.momoshop.com.tw/x",
                "https://24h.pchome.com.tw/x", "https://www.ruten.com.tw/x",
                "https://pinkoi.com/x"):
        v = osv.verify_candidate(url, "<html>store</html>",
                                 maker_name="巧福", product_name="電風扇")
        check(f"EC モール除外 {url}", v["verdict"] == "rejected")
    for url in ("https://www.findcompany.com.tw/x", "https://companyinfotw.com/x",
                "https://www.crunchbase.com/x", "https://www.squarespace.com"):
        v = osv.verify_candidate(url, "<html>dir</html>",
                                 maker_name="佑淨", product_name="足球")
        check(f"ディレクトリ/ビルダー除外 {url}", v["verdict"] == "rejected")


def test_verify_unrelated_stays_candidate():
    print("test_verify_unrelated_stays_candidate")
    # 無関係な会社サイト（素性が一致しない）→ 確定せず候補のまま
    v = osv.verify_candidate("https://www.onestepsoftware.com",
                             "<title>OneStep Software CRM</title>",
                             maker_name="Single Step", product_name="Mirable 洗浄機")
    check("素性不一致は candidate", v["verdict"] == "candidate")
    check("low 確度", v["confidence"] == "low")


def test_domain_match_alone_not_confirmed():
    print("test_domain_match_alone_not_confirmed")
    # ドメイン語が一致しても、素性の裏付け（og:site_name/Organization）が無ければ確定しない。
    # 例: singlestep.com が識別情報を返さない → candidate のまま（無関係サイト誤採用の防止）。
    v = osv.verify_candidate("https://www.singlestep.com", "<html><body>hi</body></html>",
                             maker_name="Single Step", product_name="Mirable")
    check("ドメイン一致のみは確定しない", v["verdict"] == "candidate")
    check("裏付け無しの理由を残す",
          any("裏付け" in r for r in v["reasons"]))


def test_news_site_rejected():
    print("test_news_site_rejected")
    # 記事タイトルにメーカー名が出て素性一致してもニュース記事は公式にしない。
    html = ('<title>陳佑淨 花式足球世界盃 - TNL The News Lens 關鍵評論網</title>'
            '<script type="application/ld+json">{"@type":"Organization",'
            '"name":"關鍵評論網股份有限公司"}</script>')
    v = osv.verify_candidate("https://www.thenewslens.com/article/266807", html,
                             maker_name="佑淨", product_name="花式足球")
    check("ニュース記事は rejected", v["verdict"] == "rejected")
    check("ニュース理由を残す", any("ニュース" in r for r in v["reasons"]))


def test_verify_fetch_failure_rejected():
    print("test_verify_fetch_failure_rejected")
    v = osv.verify_candidate("https://brand.example.tw", None,
                             maker_name="X", product_name="Y")
    check("取得不能は rejected（推測しない）", v["verdict"] == "rejected")


def test_socials_excludes_site_chrome():
    print("test_socials_excludes_site_chrome")
    html = (
        "<html><body>"
        '<div class="project-body">'
        '<a href="https://www.instagram.com/mybrand_tw">brand IG</a>'
        "</div>"
        '<footer><a href="https://www.youtube.com/channel/UC_k_rE8ln6Q75tcC5uvqu8g">zeczec YT</a>'
        '<a href="https://www.facebook.com/zeczec.com">zeczec FB</a></footer>'
        "</body></html>"
    )
    socials = parse_socials(HTMLParser(html))
    check("フッターの Zeczec YouTube を除外", "youtube" not in socials)
    check("フッターの Zeczec Facebook を除外", "facebook" not in socials)
    check("本文のブランド Instagram は残す",
          socials.get("instagram") == "https://www.instagram.com/mybrand_tw")


def _proj(**kw):
    base = dict(id=1, title="INOPRO 牙齒淨白貼片", source_site=SourceSite.zeczec.value,
                source_url="https://www.zeczec.com/projects/inopro",
                maker_name="MORESIE", maker_url=None, category="挺好店",
                end_date=None, enrichment=None)
    base.update(kw)
    return Project(**base)


def test_verify_candidates_promotes_and_rejects():
    print("test_verify_candidates_promotes_and_rejects")
    p = _proj()
    cands = [
        {"url": "https://moresie.com", "confidence": "low", "source": "search_result"},
        {"url": "https://shopee.tw/moresie", "confidence": "low", "source": "search_result"},
        {"url": "https://www.findcompany.com.tw/x", "confidence": "low", "source": "search_result"},
    ]

    def fetch(url):
        return _OFFICIAL_HTML if "moresie.com" in url else "<html>x</html>"

    out = zes.verify_candidates(p, cands, fetch_fn=fetch)
    by = {c["url"]: c for c in out}
    check("実ブランドサイトを official に昇格",
          by["https://moresie.com"]["verdict"] == "official")
    check("EC モールは rejected", by["https://shopee.tw/moresie"]["verdict"] == "rejected")
    check("ディレクトリは rejected",
          by["https://www.findcompany.com.tw/x"]["verdict"] == "rejected")
    check("evidence/discovered_at を保持",
          by["https://moresie.com"].get("discovered_at") is not None)

    url, reason = zes._pick_official_site(out)
    check("検証済み official を maker_url に採用", url == "https://moresie.com")


def test_no_overwrite_by_low_confidence():
    print("test_no_overwrite_by_low_confidence")
    # 既存 maker_url（別ソース/高信頼）を low 候補で上書きしない
    p = _proj(maker_url="https://existing-official.example.com", enrichment=None)
    cands = [{"url": "https://moresie.com", "confidence": "low", "source": "search_result",
              "verdict": "candidate"}]
    detail = {"challenged": False, "maker_name": "MORESIE", "creator_url": None,
              "category": "挺好店", "project_type": None, "description": None,
              "og_title": None, "status": None, "end_date": None,
              "official_candidates": [], "socials": {}}
    built = zes.build_enrichment_updates(p, detail, verified_candidates=cands)
    check("既存 maker_url を上書きしない", "maker_url" not in built["column_updates"])


if __name__ == "__main__":
    test_identity_extraction()
    test_verify_official_match()
    test_verify_marketplace_and_directory()
    test_verify_unrelated_stays_candidate()
    test_domain_match_alone_not_confirmed()
    test_news_site_rejected()
    test_verify_fetch_failure_rejected()
    test_socials_excludes_site_chrome()
    test_verify_candidates_promotes_and_rejects()
    test_no_overwrite_by_low_confidence()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
