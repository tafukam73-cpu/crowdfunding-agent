"""公式サイト FP 第2段: hosting/preview・agency・surname衝突・unfetchable・editorial の一般化検出。

外部 API 非依存の fixture/mock のみ。E01/E11/E16/E29 回帰 ＋ 正常メーカー保護 ＋
E08/E12/E16 の第1段回帰維持を検証する。

実行（backend ディレクトリで）:
    python tests/test_official_site_fp_phase2.py
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

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok  - {name}")
    else:
        _f += 1; print(f"  FAIL- {name}")


def V(candidate, html, maker, terms=None):
    return cds.verify_official_candidate(
        candidate, html, candidate, maker,
        terms if terms is not None else cds.significant_terms(maker))


def _ld(*types):
    import json
    return "".join(
        f'<script type="application/ld+json">{json.dumps({"@context":"https://schema.org","@type":t,"name":n})}</script>'
        for t, n in types)


# ---------------- 1. hosting / deploy / preview ----------------
def test_hosting_preview():
    print("test_hosting_preview")
    check("vercel.link は preview 判定", cds.official_hosting_class("https://foo.vercel.link") == "preview")
    check("vercel.app は preview 判定", cds.official_hosting_class("https://brand.vercel.app") == "preview")
    check("netlify.app は preview 判定", cds.official_hosting_class("https://x.netlify.app") == "preview")
    check("pages.dev は preview 判定", cds.official_hosting_class("https://x.pages.dev") == "preview")
    check("独自ドメインは hosting でない", cds.official_hosting_class("https://acme-gear.com") is None)
    check("myshopify は builder 判定", cds.official_hosting_class("https://brand.myshopify.com") == "builder")
    check("github.io は builder 判定", cds.official_hosting_class("https://brand.github.io") == "builder")

    # verify: preview は採用しない（メーカー名がサブドメインにあっても）
    v = V("https://hrmedical.vercel.link", "<html><title>HR Medical</title></html>", "HR Medical")
    check("preview(vercel.link) を拒否", v["collision_detected"] and "hosting" in v["reason"])
    # E11 回帰: vercel.link
    v = V("https://vercel.link", "<html><title>Vercel</title></html>", "HR Medical")
    check("E11 vercel.link を拒否", v["collision_detected"])
    # builder は hard reject しない（低confidence で保持＝recall 維持）
    v = V("https://acmegear.myshopify.com", "<html><title>Acme Gear Store</title></html>", "Acme Gear")
    check("builder(myshopify) は hard reject しない", not v["collision_detected"])
    check("builder は低confidence", v.get("confidence", 100) <= 40)
    # 独自ドメインの正常メーカーは通す
    v = V("https://acme-gear.com", "<html><title>Acme Gear - Official</title>" + _ld(("Organization", "Acme Gear")) + "</html>", "Acme Gear")
    check("独自ドメイン正常メーカーを採用", not v["collision_detected"])


# ---------------- 2. web agency / 制作会社 ----------------
_XINC = ('<html><head><title>WordPress Care Plans, Hosting & Support UK | Xinc Digital</title>'
         + _ld(("ProfessionalService", "Xinc Digital"))
         + '</head><body>We are a web design agency. Our WordPress care plans and hosting keep your site running.</body></html>')


def test_agency():
    print("test_agency")
    v = V("https://xinc.digital", _XINC, "Le Relais Coop")
    check("E16 xinc.digital(agency) を拒否", v["collision_detected"] and "agency" in v["reason"])
    # 社名に "Design Studio" を持つ正規メーカーは誤拒否しない（名称一致で保護）
    maker_html = ('<html><head><title>Nova Design Studio - Handmade Lamps</title>'
                  + _ld(("Organization", "Nova Design Studio")) + '</head><body>we craft lamps</body></html>')
    v = V("https://novadesignstudio.com", maker_html, "Nova Design Studio")
    check("'Design Studio' を社名に持つメーカーを誤拒否しない", not v["collision_detected"])
    # 単語1個(studio)だけでは agency 拒否しない
    one = '<html><head><title>Lumio</title></head><body>a lighting studio brand</body></html>'
    v = V("https://lumio.com", one, "Lumio")
    check("単語1個では agency 拒否しない", not v["collision_detected"])


# ---------------- 3. surname / major brand collision ----------------
def test_surname_collision():
    print("test_surname_collision")
    check("フルネームは person 判定", cds.maker_is_person_name("Jamila Wilson"))
    check("社名(labs付)は person でない", not cds.maker_is_person_name("MIRA Labs"))
    check("全大文字略語は person でない", not cds.maker_is_person_name("GPD HK"))
    # wilson.com は 403 で identity 取得不可でも拒否（major_brand or surname いずれでも可）
    blocked = "<html><body>Access to this page has been denied</body></html>"
    v = V("https://www.wilson.com", blocked, "Jamila Wilson", terms=cds.significant_terms("Jamila Wilson Comic Book"))
    check("E29 wilson.com 姓衝突を拒否", v["collision_detected"])
    # deny-list に無い一般ドメインでも「姓のみ一致」を一般ルールで拒否する
    v = V("https://fielding.com", blocked, "Alex Fielding", terms=cds.significant_terms("Alex Fielding Board Game"))
    check("deny-list外の姓のみ一致を一般ルールで拒否", v["collision_detected"] and "surname" in v["reason"])
    # 姓+名がドメインに揃う個人メーカーは通す（一般ルールが誤発火しない）
    v = V("https://alexfielding.com", '<html><head><title>Alex Fielding</title></head></html>', "Alex Fielding")
    check("姓名一致の個人ドメインは通す", not v["collision_detected"])
    # フルネームがドメインに入る個人メーカーは通す
    ok = '<html><head><title>Daniela Azconegui - Artbook</title>' + _ld(("Organization", "Daniela Azconegui")) + '</head></html>'
    v = V("https://danielazconegui.com", ok, "Daniela Azconegui")
    check("フルネーム一致の個人メーカーは採用", not v["collision_detected"])
    # 正常な同名ブランド公式（maker名=ブランド名がドメイン/identityに一致）は通す
    b = '<html><head><title>Theodora - Official</title>' + _ld(("Organization", "Theodora")) + '</head></html>'
    v = V("https://theodora.tw", b, "Theodora")
    check("正常な同名ブランド公式は通す", not v["collision_detected"])


# ---------------- 5. editorial / media (構造化情報が弱い) ----------------
def _editorial_html(name):
    cards = "".join(f'<div class="card">by Jane Doe{i}</div>' for i in range(20))
    return (f'<html><head><title>{name}</title>'
            '<link rel="alternate" type="application/rss+xml" href="/feed">'
            '</head><body>Latest interviews, essays and stories. Editorial magazine.'
            + cards + '</body></html>')


def test_editorial():
    print("test_editorial")
    v = V("https://thecreativeindependent.com", _editorial_html("The Creative Independent"), "KIMLA MURRELL")
    check("E01 editorial site を拒否", v["collision_detected"] and "editorial" in v["reason"])
    # 正常なメーカーの blog/news ページは拒否しない（弱シグナル＋名称一致で保護）
    blog = ('<html><head><title>Acme Gear - Blog</title>' + _ld(("Organization", "Acme Gear"))
            + '<link rel="alternate" type="application/rss+xml" href="/feed"></head>'
            '<body>by Acme Team. Read our latest news.</body></html>')
    v = V("https://acme-gear.com", blog, "Acme Gear")
    check("正常メーカーの blog/news は拒否しない", not v["collision_detected"])


# ---------------- 4. unfetchable（web_research 撤回） ----------------
class _Proj:
    id = 1; title = "Acme Gadget"; maker_name = "Acme Gear"; maker_url = None
    source_url = "https://www.kickstarter.com/projects/acme/acme-gadget"; source_site = "kickstarter"


def test_unfetchable_not_confirmed():
    print("test_unfetchable_not_confirmed")
    campaign = "https://www.kickstarter.com/projects/acme/acme-gadget"
    # campaign は news 候補にリンク。候補 root は fetch 不能（None）。
    cf = '<html><body><a href="https://en.rian.ru">en.rian.ru</a></body></html>'
    pages = {campaign: cf}  # en.rian.ru は pages に無い → fetch None

    def fetch(u):
        return pages.get(u.rstrip("/")) or pages.get(u)

    res = w.web_research(_Proj(), None, fetch_fn=fetch, search_fn=lambda q: [])
    check("fetch不能な推定候補は verified official にしない",
          not (res["official_site_url"] or "").find("rian.ru") >= 0)
    check("unfetchable 候補由来の form は maker-owned に入らない",
          not any("rian.ru" in f for f in res["discovered_forms"]))
    # 正常: 候補が fetch 成功すれば採用（recall 維持）
    cf2 = '<html><body><a href="https://acme-gear.com">Official</a></body></html>'
    pages2 = {campaign: cf2,
              "https://acme-gear.com": '<html><head><title>Acme Gear</title>' + _ld(("Organization", "Acme Gear")) + '</head><body><a href="/contact">c</a></body></html>',
              "https://acme-gear.com/contact": '<html><body><a href="mailto:hi@acme-gear.com">c</a></body></html>'}
    res2 = w.web_research(_Proj(), None, fetch_fn=lambda u: pages2.get(u.rstrip("/")) or pages2.get(u), search_fn=lambda q: [])
    check("fetch成功の推定公式は採用（recall維持）", (res2["official_site_url"] or "").find("acme-gear.com") >= 0)


# ---------------- 第1段回帰維持 ----------------
def test_institutional():
    print("test_institutional")
    blocked = "<html><body>x</body></html>"
    check("europa.eu(EU gov) を institutional 判定",
          cds.official_is_institutional("https://transition-pathways.europa.eu", "Le Relais Coop"))
    v = V("https://transition-pathways.europa.eu", blocked, "Le Relais Coop",
          terms=cds.significant_terms("Le Relais Coop") | {"transition"})
    check("E16 EU gov サイトを拒否", v["collision_detected"] and "institutional" in v["reason"])
    check(".gov を拒否", V("https://foo.gov", blocked, "Acme")["collision_detected"])
    check(".edu を拒否", V("https://mit.edu", blocked, "Acme")["collision_detected"])
    # maker 名がホストにある .edu/.gov（大学発スタートアップ等）は誤拒否しない
    check("maker 名がホストにある institutional は保護",
          not cds.official_is_institutional("https://acme.edu", "Acme"))
    # 通常の .com は institutional でない
    check("通常ドメインは institutional でない", not cds.official_is_institutional("https://acme-gear.com", "Acme"))


def test_phase1_regression():
    print("test_phase1_regression")
    # E08 miradial.com は通す（正規メーカー）
    m = '<html><head><title>MIRA Dial - MIRA Labs</title>' + _ld(("Organization", "MIRA Labs")) + '</head></html>'
    v = V("https://miradial.com", m, "MIRA Labs", terms=cds.significant_terms("MIRA Labs MIRA Dial"))
    check("E08 miradial.com は採用（回帰なし）", not v["collision_detected"])
    # E12 en.yna.co.kr（NewsMediaOrganization）拒否維持
    yna = '<html><head><title>Yonhap News Agency</title>' + _ld(("NewsMediaOrganization", "Yonhap News Agency")) + '</head></html>'
    check("E12 en.yna.co.kr 拒否維持", V("https://en.yna.co.kr", yna, "HOUSE ENM")["collision_detected"])
    # E16 transitionnetwork.org（NGO）拒否維持
    ngo = '<html><head><title>Transition Network</title>' + _ld(("NGO", "Transition Network")) + '</head></html>'
    terms = cds.significant_terms("Le Relais Coop") | {"transition"}
    check("E16 transitionnetwork.org 拒否維持", V("https://transitionnetwork.org", ngo, "Le Relais Coop", terms)["collision_detected"])


def main():
    test_hosting_preview()
    test_agency()
    test_surname_collision()
    test_editorial()
    test_institutional()
    test_unfetchable_not_confirmed()
    test_phase1_regression()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
