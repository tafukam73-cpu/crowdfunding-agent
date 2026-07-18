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


# ---- Step B: primary 拒否時の verified fallback ----
import types  # noqa: E402


def _discover(maker_url, source_url, maker, title, pages):
    """合成 HTML（pages: url部分文字列 -> html）で discover() を走らせ結果を返す。ライブ非接続。"""
    def fetch_fn(u):
        for frag, html in pages.items():
            if frag in u:
                return html
        return None
    proj = types.SimpleNamespace(maker_url=maker_url, source_url=source_url,
                                 title=title, maker_name=maker, source_site="indiegogo", id=0)
    return cds.discover(proj, None, fetch_fn=fetch_fn)


_COMEDY = "<html><head><title>Stardome Comedy Club</title></head><body>stand-up comedy</body></html>"


def test_stepb_primary_reject_fallback_success():
    print("test_stepb_primary_reject_fallback_success")
    # primary=stardome.com は collision で拒否。campaign 本文に本物 maker リンクあり → fallback 採用。
    pages = {
        "stardome.com": _COMEDY,
        "indiegogo.com": '<html><body><a href="https://stardomepod.com/">Official Website</a></body></html>',
    }
    res = _discover("https://stardome.com",
                    "https://www.indiegogo.com/en/projects/stardome-pod",
                    "StarDome", "StarDome Pod", pages)
    off = res.get("official_site_url")
    check("collision primary は不採用", not (off and "stardome.com" in off and "pod" not in off))
    check("本物 fallback stardomepod.com を採用", off == "https://stardomepod.com")


def test_stepb_primary_reject_no_fallback():
    print("test_stepb_primary_reject_no_fallback")
    # primary 拒否・本文に有効な maker リンクなし → official=None を維持。
    pages = {
        "stardome.com": _COMEDY,
        "indiegogo.com": "<html><body>no official link here</body></html>",
    }
    res = _discover("https://stardome.com",
                    "https://www.indiegogo.com/en/projects/stardome-pod",
                    "StarDome", "StarDome Pod", pages)
    check("有効 fallback なし → official=None", res.get("official_site_url") is None)


def test_stepb_fallback_also_collision():
    print("test_stepb_fallback_also_collision")
    # primary 拒否・本文 fallback が大企業(lg.com) → fallback も verify で拒否 → None。
    pages = {
        "stardome.com": _COMEDY,
        "indiegogo.com": '<html><body><a href="https://www.lg.com/">Official Website</a></body></html>',
    }
    res = _discover("https://stardome.com",
                    "https://www.indiegogo.com/en/projects/stardome-pod",
                    "StarDome", "StarDome Pod", pages)
    check("collision fallback は verify で拒否 → None", res.get("official_site_url") is None)


def test_stepb_primary_ok_not_overwritten():
    print("test_stepb_primary_ok_not_overwritten")
    # primary=sharge.com が accept。本文に別候補があっても primary を維持。
    pages = {
        "sharge.com": "<html><head><title>SHARGE | Fast Charging Power Bank</title></head></html>",
        "indiegogo.com": '<html><body><a href="https://otherbrand.com/">Official Website</a></body></html>',
    }
    res = _discover("https://sharge.com",
                    "https://www.indiegogo.com/en/projects/sharge",
                    "Sharge", "Sharge Power Bank", pages)
    off = res.get("official_site_url")
    check("primary accept を維持", off == "https://sharge.com")
    check("fallback で上書きしない", off != "https://otherbrand.com")


def test_stepb_dup_domain_no_refetch():
    print("test_stepb_dup_domain_no_refetch")
    # primary=stardome.com は collision 拒否。本文推定 fallback は www.stardome.com（表記URLは
    # 違うが同一 registered domain）。_dup 判定で fallback を再検証せず official=None を維持し、
    # fallback 検証のための追加 fetch も発生しないことを確認する。
    from urllib.parse import urlparse
    pages = {
        "stardome.com": _COMEDY,
        "indiegogo.com": '<html><body><a href="https://www.stardome.com/">Official Website</a></body></html>',
    }
    fetched = []

    def fetch_fn(u):
        fetched.append(u)
        for frag, html in pages.items():
            if frag in u:
                return html
        return None

    proj = types.SimpleNamespace(maker_url="https://stardome.com",
                                 source_url="https://www.indiegogo.com/en/projects/stardome-pod",
                                 title="StarDome Pod", maker_name="StarDome",
                                 source_site="indiegogo", id=0)
    res = cds.discover(proj, None, fetch_fn=fetch_fn)
    check("同一registered domain fallback → official=None 維持",
          res.get("official_site_url") is None)
    # 同一登録ドメインの fallback 候補（www.stardome.com）を復活させない
    check("rejected primary と同一 domain の fallback を採用しない",
          res.get("official_site_url") != "https://www.stardome.com")
    # fallback 検証のための追加 fetch が発生しない（www.stardome.com は fetch されない）
    check("fallback 用 www.stardome.com への追加 fetch なし",
          not any("www.stardome.com" in u for u in fetched))
    # 想定外/外部 URL への fetch が無い（primary domain と campaign のみ）
    hosts = {urlparse(u).netloc for u in fetched}
    check("fetch は primary domain と campaign のみ",
          hosts <= {"stardome.com", "www.indiegogo.com"})


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
    test_stepb_primary_reject_fallback_success()
    test_stepb_primary_reject_no_fallback()
    test_stepb_fallback_also_collision()
    test_stepb_primary_ok_not_overwritten()
    test_stepb_dup_domain_no_refetch()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
