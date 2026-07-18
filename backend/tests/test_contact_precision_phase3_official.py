"""Phase 3 Step A（公式サイト over-judgment FP 抑制）の単体検証。

保守的な collision 拒否（大企業/同名別業種/第三者のみ）と、identity 抽出・不足時 fallback を
機能別に検証する。gold 案件 ID や正解ドメインで分岐しない。pytest 非依存で実行できる。

実行: docker exec cfagent-backend python tests/test_contact_precision_phase3_official.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import contact_discovery_service as cds  # noqa: E402

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  - {name}")
    else:
        _f += 1
        print(f"  FAIL- {name}")


def V(cand, html, final, maker, *terms_src):
    return cds.verify_official_candidate(cand, html, final, maker, cds.significant_terms(*terms_src))


# ---- collision 拒否 ----
def test_reject_stardome_collision():
    print("test_reject_stardome_collision")
    html = "<html><head><title>Stardome Comedy Club</title></head><body>stand-up comedy</body></html>"
    v = V("https://stardome.com", html, "https://www.stardome.com/", "StarDome",
          "StarDome Pod", "StarDome Transparent Suite Pod")
    check("stardome.com 拒否", not v["accepted"] and v["collision_detected"])
    check("理由=same_name_different_business", v["reason"] == "same_name_different_business")


def test_reject_lg_major_brand():
    print("test_reject_lg_major_brand")
    v = V("https://www.lg.com/tw/x", "<title>Access Denied</title>", "https://www.lg.com/tw/x",
          "LG", "LG MoodMate", "MoodMate")
    check("lg.com 拒否", not v["accepted"] and v["collision_detected"])
    check("理由=major_unrelated_brand", v["reason"].startswith("major_unrelated_brand"))
    # 短い略称のみで大企業を採用しない（HP / Sony 等）
    for dom, mk in (("https://www.sony.com/x", "Sony"), ("https://www.samsung.com/x", "Samsung")):
        vv = V(dom, "", dom, mk, mk)
        check(f"{dom} 拒否", not vv["accepted"])


def test_keep_correct_official():
    print("test_keep_correct_official")
    v = V("https://sharge.com", "<title>Sharge - Fast Charging Power Bank</title>",
          "https://sharge.com", "Sharge", "Sharge", "Sharge Power Bank")
    check("正規 official 維持", v["accepted"] and not v["collision_detected"])


# ---- identity 抽出 ----
def test_extract_og_site_name():
    print("test_extract_og_site_name")
    html = '<html><head><meta property="og:site_name" content="Arcwave Official"></head></html>'
    ident = cds.extract_site_identity(html, "https://arcwave.com")
    check("og:site_name 抽出", "Arcwave Official" in ident["names"])


def test_extract_title():
    print("test_extract_title")
    ident = cds.extract_site_identity("<title>NOCFREE Store</title>", "https://nocfree.kr")
    check("title 抽出", any("nocfree" in n.lower() for n in ident["names"]))


def test_extract_jsonld_organization():
    print("test_extract_jsonld_organization")
    html = ('<script type="application/ld+json">'
            '{"@type":"Organization","name":"Hanboost","url":"https://hanboost.com"}</script>')
    ident = cds.extract_site_identity(html, "https://hanboost.com")
    check("JSON-LD Organization name", "Hanboost" in ident["organization_names"])
    check("JSON-LD Organization url", "https://hanboost.com" in ident["urls"])


def test_extract_canonical():
    print("test_extract_canonical")
    html = '<link rel="canonical" href="https://maker.com/home">'
    ident = cds.extract_site_identity(html, "https://maker.com/x")
    check("canonical 抽出", ident["canonical_url"] == "https://maker.com/home")


def test_broken_jsonld_no_exception():
    print("test_broken_jsonld_no_exception")
    html = '<script type="application/ld+json">{ broken json ,,, }</script><title>Maker</title>'
    try:
        ident = cds.extract_site_identity(html, "https://maker.com")
        check("壊れた JSON-LD でも例外にならない", "Maker" in "".join(ident["names"]) or True)
        check("title は取得できる", any("maker" in n.lower() for n in ident["names"]))
    except Exception as e:  # noqa: BLE001
        check(f"例外が出た: {e}", False)


# ---- fallback（過剰拒否しない）----
def test_insufficient_identity_not_rejected():
    print("test_insufficient_identity_not_rejected")
    # identity なし → 既存動作維持（accept）
    v = V("https://newbrand.io", "", "https://newbrand.io", "New Brand", "New Brand", "Gadget")
    check("identity 不足は accept", v["accepted"] and not v["collision_detected"])
    # title が短い/一致弱いだけでは拒否しない
    v2 = V("https://newbrand.io", "<title>Home</title>", "https://newbrand.io", "New Brand",
           "New Brand", "Gadget")
    check("title=Home でも拒否しない（identity不足扱い or 弱一致で維持）", v2["accepted"])


def test_multilingual_brand_name():
    print("test_multilingual_brand_name")
    # 多言語表記でも brand token が一致すれば維持
    html = '<title>ノックフリー NOCFREE 公式</title>'
    v = V("https://nocfree.kr", html, "https://nocfree.kr", "NOCFREE", "NOCFREE", "nocfree")
    check("多言語表記は拒否しない", v["accepted"])


def test_brand_differs_from_legal_name():
    print("test_brand_differs_from_legal_name")
    # 法人名（Organization）とブランド名が異なるが、product/brand と一致 → 維持
    html = ('<script type="application/ld+json">{"@type":"Organization","name":"WOW Tech GmbH"}'
            '</script><title>Arcwave - Pleasure Air</title>')
    v = V("https://arcwave.com", html, "https://arcwave.com", "Arcwave", "Arcwave", "Arcwave Ion")
    check("法人名≠ブランドでも brand 一致で維持", v["accepted"])


def test_redirect_final_domain_used():
    print("test_redirect_final_domain_used")
    # final_url の登録ドメインで大企業判定する
    v = cds.verify_official_candidate("https://short/x", "", "https://www.lg.com/tw/x", "LG",
                                      cds.significant_terms("LG"))
    check("redirect 後の lg.com で拒否", not v["accepted"])


# ---- 第三者ドメイン拒否 ----
def test_reject_platform_shortener_retailer_agency():
    print("test_reject_platform_shortener_retailer_agency")
    for dom in ("https://www.kickstarter.com/projects/x", "https://reurl.cc/abc",
                "https://www.amazon.com/dp/x", "https://ideafound.com/x"):
        v = cds.verify_official_candidate(dom, "", dom, "Maker", cds.significant_terms("Maker"))
        check(f"第三者 {dom} 拒否", not v["accepted"] and v["collision_detected"])


# ---- email / form / person が変わらない ----
def test_email_form_person_unchanged():
    print("test_email_form_person_unchanged")
    # email 抽出は Step A と無関係（サンプルで健全性確認）
    emails = cds.extract_emails('<a href="mailto:info@brand.com">x</a>', None)
    check("email 抽出は従来どおり", "info@brand.com" in emails)
    forms = cds.select_maker_forms(["https://brand.com/contact", "https://brand.com/contact-us"],
                                   "brand.com")
    check("form 選別は従来どおり（同一intent集約）", len(forms) == 1)
    from app.ai.mock_contact_hunter import extract_people_from_html
    ppl = extract_people_from_html("<div>John Smith, CEO</div>", "https://brand.com/about")
    check("person 抽出は従来どおり", any(p.name == "John Smith" for p in ppl))


def main():
    test_reject_stardome_collision()
    test_reject_lg_major_brand()
    test_keep_correct_official()
    test_extract_og_site_name()
    test_extract_title()
    test_extract_jsonld_organization()
    test_extract_canonical()
    test_broken_jsonld_no_exception()
    test_insufficient_identity_not_rejected()
    test_multilingual_brand_name()
    test_brand_differs_from_legal_name()
    test_redirect_final_domain_used()
    test_reject_platform_shortener_retailer_agency()
    test_email_form_person_unchanged()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
