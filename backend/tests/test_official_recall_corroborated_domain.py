"""公式サイト未確定時のメール救済（corroborated domain / Phase 3-②）を検証する。

対象: 取得できているのに no_verified_official で捨てられていた正当なメール
（例: info@hr-medical.co.kr / cs@hr-medical.co.kr）を、**メールだけ**救済する。
official_site / effective_domain は確定させないため、Phase2 が潰した公式サイト FP の
面は広がらない。その不変条件も本テストで固定する。

救済経路は2つ:
  - "site"     : root ページ取得済み + verify accepted + 自サイト掲載
  - "siteless" : A レコード無し + MX あり（Web サイトを持たないメーカー）

外部 API / DNS 非依存（dns_fn を必ず注入する）。実行（backend ディレクトリで）:
    python tests/test_official_recall_corroborated_domain.py
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


def emap(*recs):
    """(email, [sources]) から email_map 相当の dict を作る。"""
    return {e.lower(): {"email": e, "sources": list(s)} for e, s in recs}


# --- DNS スタブ（実 DNS は絶対に引かない） ---
def dns_with_site(_d):
    """通常のサイト持ちドメイン（A あり）→ siteless 経路には入らない。"""
    return {"a": True, "mx": True}


def dns_siteless(_d):
    """A 無し + MX あり＝Web サイトを持たないメーカー（hr-medical.co.kr 型）。"""
    return {"a": False, "mx": True}


def dns_dead(_d):
    """A 無し + MX 無し＝完全に死んだドメイン。"""
    return {"a": False, "mx": False}


def dns_unknown(_d):
    """DNS 判定不能。"""
    return None


# 実データ: E11 / project_id 132 / wadiz。maker 名が韓国語のため significant_terms は空。
KO_MAKER = "주식회사 에이치알메디컬"
HR = "hr-medical.co.kr"
# 実測どおり、メールは第三者ディレクトリ由来（自サイトが存在しないため）。
HR_EMAILS_3P = emap(
    ("info@hr-medical.co.kr", ["https://nicebizinfo.com/company/123"]),
    ("cs@hr-medical.co.kr", ["https://www.jobkorea.co.kr/company/456"]),
)
# 自サイト掲載パターン（"site" 経路の検証用）
HR_EMAILS_SELF = emap(
    ("info@hr-medical.co.kr", ["https://hr-medical.co.kr/"]),
    ("cs@hr-medical.co.kr", ["https://hr-medical.co.kr/contact"]),
)
HR_ROOTS = {HR: True}


# ---------------- 1. 許可: siteless 経路（HR Medical 実ケース） ----------------
def test_allow_siteless_hr_medical():
    print("test_allow_siteless_hr_medical")
    check("前提: 韓国語 maker 名は significant_terms が空",
          cds.significant_terms(KO_MAKER) == set())

    doms = w.build_domain_corroboration(HR_EMAILS_3P, {}, KO_MAKER, set(),
                                        dns_fn=dns_siteless)
    check("A無し+MXあり なら root 未取得でも裏付けドメインになる", doms.get(HR) == "siteless")

    owned, reason = w._email_maker_ownership(
        "info@hr-medical.co.kr", ["https://nicebizinfo.com/company/123"], "",
        corroborated_domains=doms)
    check("siteless: info@ が第三者出典でも救済される",
          owned and reason == "corroborated_domain")
    owned2, _ = w._email_maker_ownership(
        "cs@hr-medical.co.kr", ["https://www.jobkorea.co.kr/company/456"], "",
        corroborated_domains=doms)
    check("siteless: cs@ が救済される", owned2)


# ---------------- 2. 許可: site 経路（従来どおり） ----------------
def test_allow_site_path():
    print("test_allow_site_path")
    doms = w.build_domain_corroboration(HR_EMAILS_SELF, HR_ROOTS, KO_MAKER, set(),
                                        dns_fn=dns_with_site)
    check("root取得+verify accepted+自サイト掲載 なら site 経路", doms.get(HR) == "site")

    lat = emap(("info@acmegear.com", ["https://acmegear.com/"]),
               ("support@acmegear.com", ["https://acmegear.com/support"]))
    doms2 = w.build_domain_corroboration(lat, {"acmegear.com": True}, "Acme Gear",
                                         cds.significant_terms("Acme Gear"),
                                         dns_fn=dns_with_site)
    check("maker 名一致のラテン系ドメインも救済", doms2.get("acmegear.com") == "site")

    hv = emap(("sales@acmegear.com", ["https://acmegear.com/"]),
              ("info@acmegear.com", ["https://acmegear.com/about"]))
    check("sales@ + info@ も裏付けになる",
          "acmegear.com" in w.build_domain_corroboration(
              hv, {"acmegear.com": True}, "Acme Gear",
              cds.significant_terms("Acme Gear"), dns_fn=dns_with_site))


# ---------------- 3. siteless 経路の拒否条件 ----------------
def test_siteless_rejections():
    print("test_siteless_rejections")
    check("A あり(root未取得)なら siteless 経路に入らない",
          HR not in w.build_domain_corroboration(HR_EMAILS_3P, {}, KO_MAKER, set(),
                                                 dns_fn=dns_with_site))
    check("MX 無し（完全に死んだドメイン）は救済しない",
          HR not in w.build_domain_corroboration(HR_EMAILS_3P, {}, KO_MAKER, set(),
                                                 dns_fn=dns_dead))
    check("DNS 判定不能なら救済しない",
          HR not in w.build_domain_corroboration(HR_EMAILS_3P, {}, KO_MAKER, set(),
                                                 dns_fn=dns_unknown))
    single = emap(("info@hr-medical.co.kr", ["https://nicebizinfo.com/c"]))
    check("siteless でも単一メールは救済しない",
          HR not in w.build_domain_corroboration(single, {}, KO_MAKER, set(),
                                                 dns_fn=dns_siteless))
    free = emap(("info@gmail.com", ["https://x.com/"]),
                ("support@gmail.com", ["https://y.com/"]))
    check("siteless でも freemail は救済しない",
          "gmail.com" not in w.build_domain_corroboration(free, {}, KO_MAKER, set(),
                                                          dns_fn=dns_siteless))
    mismatch = emap(("info@totally-other.com", ["https://x.com/"]),
                    ("cs@totally-other.com", ["https://y.com/"]))
    check("siteless でも maker 名不一致（terms あり）は救済しない",
          "totally-other.com" not in w.build_domain_corroboration(
              mismatch, {}, "Acme Gear", cds.significant_terms("Acme Gear"),
              dns_fn=dns_siteless))
    person = emap(("tuffselectph@hr-medical.co.kr", ["https://x.com/"]),
                  ("john.doe@hr-medical.co.kr", ["https://y.com/"]))
    check("siteless でも person ロールだけでは裏付けにならない",
          HR not in w.build_domain_corroboration(person, {}, KO_MAKER, set(),
                                                 dns_fn=dns_siteless))


# ---------------- 4. site 経路の拒否条件 ----------------
def test_site_path_rejections():
    print("test_site_path_rejections")
    check("root 未取得（A あり・403型）は救済しない",
          HR not in w.build_domain_corroboration(HR_EMAILS_SELF, {}, KO_MAKER, set(),
                                                 dns_fn=dns_with_site))
    check("verify 拒否なら救済しない",
          HR not in w.build_domain_corroboration(HR_EMAILS_SELF, {HR: False}, KO_MAKER,
                                                 set(), dns_fn=dns_with_site))
    check("source が第三者ドメインなら site 経路にならない",
          HR not in w.build_domain_corroboration(HR_EMAILS_3P, HR_ROOTS, KO_MAKER,
                                                 set(), dns_fn=dns_with_site))
    # site 経路では自サイト掲載を必須にする（_email_maker_ownership 側）
    doms = w.build_domain_corroboration(HR_EMAILS_SELF, HR_ROOTS, KO_MAKER, set(),
                                        dns_fn=dns_with_site)
    owned, _ = w._email_maker_ownership(
        "info@hr-medical.co.kr", ["https://nicebizinfo.com/c"], "",
        corroborated_domains=doms)
    check("site 経路は第三者出典のメールを採用しない", not owned)


# ---------------- 5. 第三者メール help@nicebizinfo.com ----------------
def test_reject_nicebizinfo():
    print("test_reject_nicebizinfo")
    real = emap(
        ("info@hr-medical.co.kr", ["https://nicebizinfo.com/company/123"]),
        ("cs@hr-medical.co.kr", ["https://nicebizinfo.com/company/123"]),
        ("help@nicebizinfo.com", ["https://nicebizinfo.com/company/123"]),
    )

    def dns_mixed(d):
        # nicebizinfo は実在サイト（A あり）、hr-medical はサイト無し
        return {"a": True, "mx": True} if d == "nicebizinfo.com" else {"a": False, "mx": True}

    doms = w.build_domain_corroboration(real, {}, KO_MAKER, set(), dns_fn=dns_mixed)
    check("hr-medical は救済される", doms.get(HR) == "siteless")
    check("nicebizinfo.com は救済されない（A あり・単一メール）",
          "nicebizinfo.com" not in doms)
    owned, reason = w._email_maker_ownership(
        "help@nicebizinfo.com", ["https://nicebizinfo.com/company/123"], "",
        corroborated_domains=doms)
    check("help@nicebizinfo.com は maker-owned にならない",
          (not owned) and reason == "no_verified_official")


# ---------------- 6. Phase2 FP 5件が再浮上しないこと ----------------
_NGO = ('<html><head><title>Transition Network</title>'
        '<script type="application/ld+json">{"@context":"https://schema.org",'
        '"@type":"NGO","name":"Transition Network"}</script></head>'
        '<body>charity community network</body></html>')
_AGENCY = ('<html><head><title>WordPress Care Plans, Hosting &amp; Support UK | Xinc Digital</title>'
           '<script type="application/ld+json">{"@context":"https://schema.org",'
           '"@type":"ProfessionalService","name":"Xinc Digital"}</script></head>'
           '<body>We are a web design agency. Our WordPress care plans and hosting '
           'keep your site running.</body></html>')
_EDITORIAL = ('<html><head><title>The Creative Independent</title></head><body>'
              + ''.join(f'<article><h2>Story {i}</h2><span class="byline">By Writer {i}</span>'
                        f'<time>2026-01-0{i%9+1}</time></article>' for i in range(14))
              + '<link rel="alternate" type="application/rss+xml" href="/rss"></body></html>')


def _verdict(url, html, maker):
    v = cds.verify_official_candidate(url, html, url, maker, cds.significant_terms(maker))
    return bool(v.get("accepted") and not v.get("collision_detected"))


def _fp_case(name, domain, url, html, maker):
    """FP ドメインは (a) verify 拒否 (b) A ありなので siteless 経路にも入らない。"""
    ok_verify = _verdict(url, html, maker) is False
    doms = w.build_domain_corroboration(
        emap((f"info@{domain}", [f"https://{domain}/"]),
             (f"support@{domain}", [f"https://{domain}/c"])),
        {domain: _verdict(url, html, maker)}, maker, cds.significant_terms(maker),
        dns_fn=dns_with_site)
    check(f"{name}: verify 拒否", ok_verify)
    check(f"{name}: 救済されない", domain not in doms)


def test_phase2_fp_not_resurrected():
    print("test_phase2_fp_not_resurrected")
    _fp_case("transitionnetwork.org(NGO)", "transitionnetwork.org",
             "https://transitionnetwork.org", _NGO, "Le Relais Coop")
    _fp_case("xinc.digital(agency)", "xinc.digital",
             "https://xinc.digital", _AGENCY, "Le Relais Coop")
    _fp_case("vercel.link(preview)", "vercel.link",
             "https://vercel.link", "<html><title>Vercel</title></html>", "HR Medical")
    _fp_case("wilson.com(大企業)", "wilson.com",
             "https://wilson.com", "<html><title>Wilson</title></html>", "Ryan Wilson")

    ed = emap(("info@thecreativeindependent.com", ["https://thecreativeindependent.com/"]),
              ("support@thecreativeindependent.com", ["https://thecreativeindependent.com/c"]))
    check("editorial は救済されない",
          "thecreativeindependent.com" not in w.build_domain_corroboration(
              ed, {"thecreativeindependent.com": _verdict(
                  "https://thecreativeindependent.com", _EDITORIAL, "Hottie Hot Comb")},
              "Hottie Hot Comb", set(), dns_fn=dns_with_site))

    rn = emap(("info@rian.ru", ["https://en.rian.ru/"]),
              ("support@rian.ru", ["https://en.rian.ru/c"]))
    check("en.rian.ru（A あり・取得不能）は救済されない",
          "rian.ru" not in w.build_domain_corroboration(rn, {}, "Olly", set(),
                                                        dns_fn=dns_with_site))
    # 念のため: FP ドメインが仮に MX を持っていても A があれば siteless に入らない
    check("A ありドメインは MX があっても siteless にならない",
          "transitionnetwork.org" not in w.build_domain_corroboration(
              emap(("info@transitionnetwork.org", ["https://x.com/"]),
                   ("support@transitionnetwork.org", ["https://y.com/"])),
              {}, "Le Relais Coop", set(), dns_fn=dns_with_site))


# ---------------- 7. official_site へ昇格しない不変条件 ----------------
def test_does_not_promote_official():
    print("test_does_not_promote_official")
    src = (BACKEND / "app/services/web_research_service.py").read_text(encoding="utf-8")
    idx = src.index("def build_domain_corroboration")
    end = src.index("def _form_maker_owned")
    body = src[idx:end]
    for forbidden in ("effective_official =", "effective_domain =", "official_site ="):
        check(f"救済ロジックが {forbidden.strip(' =')} を更新しない", forbidden not in body)

    doms = w.build_domain_corroboration(HR_EMAILS_3P, {}, KO_MAKER, set(),
                                        dns_fn=dns_siteless)
    owned, _ = w._email_maker_ownership(
        "info@hr-medical.co.kr", ["https://nicebizinfo.com/c"], "",
        corroborated_domains=doms)
    check("救済されてもフォームは maker-owned にならない（official 未確定のまま）",
          owned and w._form_maker_owned("https://hr-medical.co.kr/contact", "") is False)


# ---------------- 8. 既存契約の後方互換 ----------------
def test_backward_compatible():
    print("test_backward_compatible")
    owned, reason = w._email_maker_ownership(
        "info@hr-medical.co.kr", ["https://hr-medical.co.kr/"], "")
    check("引数なしなら従来どおり no_verified_official",
          (not owned) and reason == "no_verified_official")
    owned2, reason2 = w._email_maker_ownership(
        "hi@brandco.com", ["https://brandco.com/"], "brandco.com")
    check("official 確定時の挙動は不変", owned2 and reason2 == "official_domain")
    # set を渡す旧形式でも動く（"site" 扱い）
    owned3, _ = w._email_maker_ownership(
        "info@hr-medical.co.kr", ["https://hr-medical.co.kr/"], "",
        corroborated_domains={HR})
    check("set を渡した場合は site 扱いで動作", owned3)


# ---------------- 9. end-to-end 配線 ----------------
_E2E_ROOT = ("<html><head><title>HR Medical</title></head>"
             "<body>info@hr-medical.co.kr</body></html>")
_E2E_CONTACT = "<html><body>cs@hr-medical.co.kr</body></html>"
_E2E_DIR = "<html><body>help@nicebizinfo.com company directory listing</body></html>"


def test_end_to_end_wiring():
    """web_research 全体を通して救済が効くこと（配線が死んでいないことの担保）。"""
    print("test_end_to_end_wiring")
    from types import SimpleNamespace

    def fetch(url):
        if url.rstrip("/") == "https://hr-medical.co.kr":
            return _E2E_ROOT
        if url.startswith("https://hr-medical.co.kr/contact"):
            return _E2E_CONTACT
        if url.startswith("https://nicebizinfo.com"):
            return _E2E_DIR
        return None

    def search(_q):
        return ["https://hr-medical.co.kr/", "https://hr-medical.co.kr/contact",
                "https://nicebizinfo.com/company/123"]

    proj = SimpleNamespace(id=132, maker_name=KO_MAKER, product_name="[굿바이 니코틴!]",
                           maker_url="", source_url="", source_site="wadiz",
                           title="[굿바이 니코틴!]")
    # 実 DNS を引かないようスタブ化（A あり＝site 経路で救済されるケース）
    orig = w.dns_profile
    w.dns_profile = dns_with_site
    try:
        r = w.web_research(proj, fetch_fn=fetch, search_fn=search)
    finally:
        w.dns_profile = orig
    got = {e["email"]: e.get("ownership_reason") for e in (r.get("discovered_emails") or [])}
    tp = {e["email"] for e in (r.get("third_party_emails") or [])}
    check("e2e: info@ が corroborated_domain で採用",
          got.get("info@hr-medical.co.kr") == "corroborated_domain")
    check("e2e: cs@ が corroborated_domain で採用",
          got.get("cs@hr-medical.co.kr") == "corroborated_domain")
    check("e2e: help@nicebizinfo.com は third_party のまま",
          "help@nicebizinfo.com" in tp and "help@nicebizinfo.com" not in got)
    check("e2e: official_site_url は確定しない（昇格しない）",
          not r.get("official_site_url"))
    check("e2e: form は maker-owned にならない", not r.get("discovered_forms"))


def main():
    test_allow_siteless_hr_medical()
    test_allow_site_path()
    test_siteless_rejections()
    test_site_path_rejections()
    test_reject_nicebizinfo()
    test_phase2_fp_not_resurrected()
    test_does_not_promote_official()
    test_backward_compatible()
    test_end_to_end_wiring()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
