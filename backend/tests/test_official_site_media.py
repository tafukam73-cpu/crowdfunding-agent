"""公式サイト判定の FP 防止（news/media/review/directory/NPO をメーカー公式にしない）。

外部 API に依存しない fixture/mock のみ。verify_official_candidate（identity＋一般化
シグナル）と web_research の推定公式撤回（root 巡回時検証）を検証する。E08/E12/E16 の
回帰 fixture を含む。

実行（backend ディレクトリで）:
    python tests/test_official_site_media.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import web_research_service as w  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _ldjson(*types_and_names):
    blocks = []
    for typ, name in types_and_names:
        blocks.append(
            f'<script type="application/ld+json">'
            f'{{"@context":"https://schema.org","@type":"{typ}","name":"{name}"}}</script>')
    return "".join(blocks)


def V(candidate, html, maker, terms=None):
    return cds.verify_official_candidate(
        candidate, html, candidate, maker,
        terms if terms is not None else cds.significant_terms(maker))


# ---- fixture HTML ----
_MAKER_HTML = ('<html><head><title>Acme Gear - Official Store</title>'
               + _ldjson(("Organization", "Acme Gear"))
               + '</head><body><a href="/contact">Contact</a><a href="/about">About</a>'
               'Acme Gear premium gear.</body></html>')
_NEWS_HTML = ('<html><head><title>Insider Weekly - Latest News in Business & Tech</title>'
              + _ldjson(("WebSite", "Insider Weekly"))
              + '</head><body><a href="/contact">Contact us</a>'
              'MIRA Dial by MIRA Labs reviewed here.</body></html>')
_NEWSMEDIA_HTML = ('<html><head><title>Yonhap News Agency</title>'
                   + _ldjson(("NewsMediaOrganization", "Yonhap News Agency"))
                   + '</head><body><a href="/contact">Contact</a></body></html>')
_MAGAZINE_HTML = ('<html><head><title>Gadget Magazine - Reviews & Roundups</title>'
                  + _ldjson(("WebSite", "Gadget Magazine"))
                  + '</head><body>reviews</body></html>')
_REVIEW_HTML = ('<html><head><title>BackerViews - Crowdfunding Reviews</title>'
                + _ldjson(("WebSite", "BackerViews"))
                + '</head><body>product reviews</body></html>')
_NGO_HTML = ('<html><head><title>Transition Towns Movement - Transition Network</title>'
             + _ldjson(("NGO", "Transition Network"))
             + '</head><body><a href="/contact">Contact</a></body></html>')


def test_positive_makers_accepted() -> None:
    print("test_positive_makers_accepted")
    # 1. 正常なメーカー公式サイトは採用
    v = V("https://acme-gear.com", _MAKER_HTML, "Acme Gear")
    check("1. 正常メーカー公式を採用", not v["collision_detected"] and v["accepted"])
    # 2. 公式 subdomain は採用（登録ドメイン一致・名称一致）
    v = V("https://shop.acme-gear.com", _MAKER_HTML, "Acme Gear")
    check("2. 公式 subdomain を採用", not v["collision_detected"])
    # 14. 既存正常案件（identity 不足/エラーページ）は壊さない＝過剰拒否しない
    v = V("https://acme-gear.com", "<html><body>Just a moment...</body></html>", "Acme Gear")
    check("14. identity 不足でも過剰拒否しない", not v["collision_detected"])
    # 大企業でない普通ドメイン＋名称一致
    v = V("https://bisan.com",
          '<html><head><title>Bisan Bikes</title></head><body>bikes</body></html>', "Bisan")
    check("14b. 名称一致サイトを採用", not v["collision_detected"])


def test_media_directory_rejected() -> None:
    print("test_media_directory_rejected")
    # 4. news site 除外
    v = V("https://technewsdaily.com", _NEWS_HTML, "Acme Gear")
    check("4. news site を除外", v["collision_detected"] and v["reason"] == "media_or_directory_not_maker")
    # 5. media（NewsMediaOrganization）除外
    v = V("https://somemedia.com", _NEWSMEDIA_HTML, "Acme Gear")
    check("5. NewsMediaOrganization を除外", v["collision_detected"])
    # 5b. magazine 除外
    v = V("https://gadgetmag.com", _MAGAZINE_HTML, "Acme Gear")
    check("5b. magazine を除外", v["collision_detected"])
    # 6. review site 除外
    v = V("https://backerviews.com", _REVIEW_HTML, "Acme Gear")
    check("6. review site を除外", v["collision_detected"])
    # 7. unrelated network/nonprofit（NGO）除外
    v = V("https://somenetwork.org", _NGO_HTML, "Acme Gear")
    check("7. NGO/network を除外", v["collision_detected"])


def test_retailer_marketplace_rejected() -> None:
    print("test_retailer_marketplace_rejected")
    # 8. retailer / marketplace（source_ownership deny クラス）は Rule 1 で除外
    v = V("https://www.amazon.com/dp/B0XYZ", "<html><body>buy</body></html>", "Acme Gear")
    check("8. retailer(amazon) を除外", v["collision_detected"])
    v = V("https://shopee.com/x", "<html><body>buy</body></html>", "Acme Gear")
    check("8b. marketplace(shopee) を除外", v["collision_detected"])


def test_weak_signals_not_enough() -> None:
    print("test_weak_signals_not_enough")
    # 9. maker 名が記事本文に出るだけ（identity/ドメインに無い）では採用しない
    v = V("https://theinsiderweekly.com", _NEWS_HTML, "MIRA Labs")
    check("9. 記事本文の maker 言及だけでは採用しない", v["collision_detected"])
    # 10. Contact ページが存在するだけでは公式にしない（news+contact でも除外）
    check("10. Contact 有りでも media は除外", V("https://x-news.com", _NEWS_HTML, "MIRA Labs")["collision_detected"])
    # maker 名がドメインにあれば media 語があっても採用（正規メーカー保護）
    html = ('<html><head><title>Acme News Hub</title>'
            + _ldjson(("Organization", "Acme Gear")) + '</head><body>x</body></html>')
    v = V("https://acmegear.com", html, "Acme Gear")
    check("正規: maker 名がドメイン/identity にあれば media 語でも採用", not v["collision_detected"])


def test_e08_e12_e16_regression() -> None:
    print("test_e08_e12_e16_regression")
    # 11. E08 theinsiderweekly.com を公式にしない
    check("11. E08 theinsiderweekly.com を除外",
          V("https://theinsiderweekly.com", _NEWS_HTML, "MIRA Labs")["collision_detected"])
    # 12. E12 en.yna.co.kr を公式にしない
    check("12. E12 en.yna.co.kr を除外",
          V("https://en.yna.co.kr", _NEWSMEDIA_HTML, "HOUSE ENM")["collision_detected"])
    # 13. E16 transitionnetwork.org を公式にしない（product 語 "transition" 衝突でも）
    terms = cds.significant_terms("Le Relais Coop") | {"transition", "ecological", "social"}
    check("13. E16 transitionnetwork.org を除外",
          V("https://transitionnetwork.org", _NGO_HTML, "Le Relais Coop", terms)["collision_detected"])


# ---- web_research 推定公式の撤回（3: campaign 直リンク公式は採用 / news は撤回）----
class _Proj:
    id = 1
    title = "Acme Gadget"
    maker_name = "Acme Gear"
    maker_url = None
    source_url = "https://www.kickstarter.com/projects/acme/acme-gadget"
    source_site = "kickstarter"


def test_web_research_adopts_official_rejects_media() -> None:
    print("test_web_research_adopts_official_rejects_media")
    campaign = ("https://www.kickstarter.com/projects/acme/acme-gadget")
    # campaign が公式(acme-gear.com)とニュース(technewsdaily.com)両方にリンク
    cf = ('<html><body>'
          '<a href="https://acme-gear.com">Official Website</a>'
          '<a href="https://technewsdaily.com/acme-review">Press</a></body></html>')
    pages = {
        campaign: cf,
        "https://acme-gear.com": _MAKER_HTML,
        "https://acme-gear.com/contact": '<html><body><a href="mailto:hello@acme-gear.com">c</a></body></html>',
        "https://technewsdaily.com": _NEWS_HTML,
        "https://technewsdaily.com/contact": _NEWS_HTML,
    }

    def fetch(u):
        return pages.get(u.rstrip("/")) or pages.get(u)

    # 3. campaign 直リンクの公式を採用
    res = w.web_research(_Proj(), None, fetch_fn=fetch,
                         search_fn=lambda q: ["https://acme-gear.com"])
    check("3. campaign 直リンク公式(acme-gear.com)を採用",
          (res["official_site_url"] or "").find("acme-gear.com") >= 0)
    forms = res["discovered_forms"]
    check("3b. 公式ドメインの form のみ maker-owned",
          all("acme-gear.com" in f for f in forms))

    # news を公式と誤推定させるケース：campaign が news だけにリンク → root 検証で撤回
    cf2 = ('<html><body><a href="https://technewsdaily.com">technewsdaily.com</a></body></html>')
    pages2 = {campaign: cf2, "https://technewsdaily.com": _NEWS_HTML,
              "https://technewsdaily.com/contact": _NEWS_HTML}

    def fetch2(u):
        return pages2.get(u.rstrip("/")) or pages2.get(u)

    res2 = w.web_research(_Proj(), None, fetch_fn=fetch2, search_fn=lambda q: [])
    check("news を公式に採用しない（撤回）",
          not (res2["official_site_url"] or "").find("technewsdaily") >= 0)
    check("撤回後 news ドメインの form は maker-owned に入らない",
          not any("technewsdaily" in f for f in res2["discovered_forms"]))


def main() -> int:
    test_positive_makers_accepted()
    test_media_directory_rejected()
    test_retailer_marketplace_rejected()
    test_weak_signals_not_enough()
    test_e08_e12_e16_regression()
    test_web_research_adopts_official_rejects_media()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
