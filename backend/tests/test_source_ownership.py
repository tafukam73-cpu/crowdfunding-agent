"""source_ownership（情報源の所有者判定）の単体テスト。

gold 案件の正解をハードコードして特定案件だけ通すことはしない。一般ルール
（ドメイン分類 × 名一致 × 役割）が正しく働くことを検証する。

実行: docker exec cfagent-backend python tests/test_source_ownership.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'ownership.sqlite'}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import source_ownership as so  # noqa: E402

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  - {name}")
    else:
        _f += 1
        print(f"  FAIL- {name}")


def test_registrable_domain():
    print("test_registrable_domain")
    check("com.tw 二段", so.registrable_domain("https://shop.unionchen.com.tw/x") == "unionchen.com.tw")
    check("co.kr 二段", so.registrable_domain("nextbaby.co.kr") == "nextbaby.co.kr")
    check("co.kr sub", so.registrable_domain("mail.makerz.co.kr") == "makerz.co.kr")
    check("通常 .com", so.registrable_domain("https://www.sharge.com/pages/x") == "sharge.com")
    check("email host", so.registrable_domain("info@sharge.com") == "sharge.com")


def test_platform_and_thirdparty_rejected():
    print("test_platform_and_thirdparty_rejected")
    check("kickstarter=platform", so.classify_domain("support@kickstarter.com").ownership_class == "crowdfunding_platform")
    check("zeczec=platform", so.classify_domain("support@zeczec.com").ownership_class == "crowdfunding_platform")
    check("kickbooster=marketing", so.classify_domain("https://hanboost.kickbooster.me/x").ownership_class == "crowdfunding_marketing_service")
    check("reurl=shortener", so.classify_domain("https://reurl.cc/abcd").ownership_class == "url_shortener")
    check("m.me=messenger", so.classify_domain("http://m.me/contact").ownership_class == "messenger")
    check("ideafound=agency", so.classify_domain("apply@ideafound.com").ownership_class == "agency")
    check("brand-kr=agency", so.classify_domain("hi@brand-kr.com").ownership_class == "agency")
    check("makerz=agency", so.classify_domain("real1@makerz.co.kr").ownership_class == "agency")
    check("amazon=retailer", so.classify_domain("https://www.amazon.com/dp/x").ownership_class == "retailer")


def test_maker_official_match():
    print("test_maker_official_match")
    ctx = so.Ctx(maker_name="Sharge Tech", brand_name="Sharge", official_domain="sharge.com")
    check("info@sharge=maker_official", so.classify_domain("info@sharge.com", ctx).ownership_class == "maker_official")
    ctx2 = so.Ctx(maker_name="Arcwave", official_domain="arcwave.com")
    check("care@arcwave=maker_official", so.classify_domain("care@arcwave.com", ctx2).ownership_class == "maker_official")
    # 公式ドメイン未指定でも名一致で maker 候補
    ctx3 = so.Ctx(maker_name="Hanboost")
    check("hanboost.com 名一致=maker", so.classify_domain("https://www.hanboost.com", ctx3).is_maker)
    # subdomain
    check("sub.sharge=maker_subdomain", so.classify_domain("shop.sharge.com", ctx).ownership_class == "maker_subdomain")


def test_unrelated_bigco_not_official_by_name_only():
    print("test_unrelated_bigco_not_official_by_name_only")
    # maker_name が "LG" と誤抽出されても lg.com を maker official にしない。
    ctx = so.Ctx(maker_name="LG")
    o = so.classify_domain("https://www.lg.com/tw/projectors/x", ctx)
    check("lg.com は maker にしない", not o.is_maker)
    check("lg.com は unrelated_company", o.ownership_class == "unrelated_company")
    check("lge.com も maker にしない", not so.classify_domain("zhijian.chen@lge.com", ctx).is_maker)


def test_personal_email():
    print("test_personal_email")
    ctx = so.Ctx(maker_name="Green River Studio")
    o = so.classify_domain("greenriverstudio7777@gmail.com", ctx)
    check("gmail=personal", o.ownership_class == "personal_email")
    check("personal はデフォルト非採用", not so.classify_email("x@gmail.com", ctx)["accepted_as_maker_contact"])
    # 人物名一致のフリーメールは人物連絡先として採用
    r = so.classify_email("chris@gmail.com", ctx, person_names={"Chris"})
    check("人物名一致 gmail は採用", r["accepted_as_maker_contact"] and r["person_match"])


def test_email_role_and_ranking():
    print("test_email_role_and_ranking")
    check("wholesale=high", so.email_role("wholesale@x.com") == "high")
    check("care=support", so.email_role("care@x.com") == "support")
    check("info=mid", so.email_role("info@x.com") == "mid")
    check("noreply=exclude", so.email_role("noreply@x.com") == "exclude")
    check("careers=exclude", so.email_role("careers@x.com") == "exclude")
    check("privacy=exclude", so.email_role("privacy@x.com") == "exclude")
    ctx = so.Ctx(maker_name="Brand", official_domain="brand.com")
    ranked = so.rank_maker_emails(
        ["support@brand.com", "wholesale@brand.com", "info@brand.com", "noreply@brand.com"], ctx)
    check("noreply は除外される", all(r["email"] != "noreply@brand.com" for r in ranked))
    check("wholesale が先頭", ranked[0]["email"] == "wholesale@brand.com")


def test_platform_self_social():
    print("test_platform_self_social")
    check("facebook.com/kickstarter=self", so.is_platform_self_social("http://www.facebook.com/kickstarter"))
    check("instagram zeczec_com=self", so.is_platform_self_social("https://www.instagram.com/zeczec_com/"))
    check("x.com/kickstarter=self", so.is_platform_self_social("http://www.twitter.com/kickstarter"))
    check("facebook.com/Indiegogo=self", so.is_platform_self_social("https://www.facebook.com/Indiegogo"))
    check("正規 maker SNS は self でない", not so.is_platform_self_social("https://www.instagram.com/sharge_official/"))
    check("facebook.com/sharge.fans は self でない", not so.is_platform_self_social("https://www.facebook.com/sharge.fans"))


def test_accept_reject_email_end_to_end():
    print("test_accept_reject_email_end_to_end")
    ctx_arc = so.Ctx(maker_name="Arcwave", official_domain="arcwave.com")
    check("care@arcwave 採用", so.classify_email("care@arcwave.com", ctx_arc)["accepted_as_maker_contact"])
    ctx_sd = so.Ctx(maker_name="StarDome")
    check("ideafound 代理店 不採用", not so.classify_email("apply@ideafound.com", ctx_sd)["accepted_as_maker_contact"])
    ctx_z = so.Ctx(maker_name="親子天下", source_site="zeczec") if False else so.Ctx(maker_name="親子天下")
    check("support@zeczec 運営 不採用", not so.classify_email("support@zeczec.com", ctx_z)["accepted_as_maker_contact"])


def main():
    test_registrable_domain()
    test_platform_and_thirdparty_rejected()
    test_maker_official_match()
    test_unrelated_bigco_not_official_by_name_only()
    test_personal_email()
    test_email_role_and_ranking()
    test_platform_self_social()
    test_accept_reject_email_end_to_end()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
