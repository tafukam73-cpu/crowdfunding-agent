"""Phase 1 precision 改善（SNS 自己アカウント除外 + 第三者メール除外）の単体検証。

今回の変更範囲だけを対象にした小さいテスト群。gold 案件 ID や特定案件名で分岐せず、
一般ルール（source_ownership の deny-list 分類 × extract_socials / email_exclusion_reason
との統合）が正しく働くことを機能別に検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_contact_precision_phase1.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# app.db.session が import 時に engine を作るため SQLite に差し替える（実 DB 非接続）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import source_ownership as so  # noqa: E402
from app.services.contact_discovery_service import (  # noqa: E402
    classify_email_owner,
    email_exclusion_reason,
    extract_emails_classified,
    extract_socials,
)

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


def _link(url: str) -> str:
    return f'<html><body><a href="{url}">x</a></body></html>'


# ======================= SNS 抽出（extract_socials） =======================

def test_sns_excludes_platform_facebook() -> None:
    print("test_sns_excludes_platform_facebook")
    html = _link("https://www.facebook.com/kickstarter")
    s = extract_socials(html, "https://maker.example.com/")
    check("facebook.com/kickstarter は採用しない", "facebook" not in s)


def test_sns_excludes_platform_instagram_zeczec() -> None:
    print("test_sns_excludes_platform_instagram_zeczec")
    html = _link("https://www.instagram.com/zeczec_com/")
    s = extract_socials(html, "https://maker.example.com/")
    check("instagram.com/zeczec_com は採用しない", "instagram" not in s)


def test_sns_excludes_indiegogo_wadiz_ulule_self() -> None:
    print("test_sns_excludes_indiegogo_wadiz_ulule_self")
    for url, plat in (
        ("https://www.facebook.com/Indiegogo", "facebook"),
        ("https://www.facebook.com/wadiz.kr", "facebook"),
        ("https://twitter.com/ulule", "twitter"),
    ):
        s = extract_socials(_link(url), "https://maker.example.com/")
        check(f"{url} は運営自己アカウントとして除外", plat not in s)


def test_sns_keeps_maker_own_social() -> None:
    print("test_sns_keeps_maker_own_social")
    html = _link("https://www.instagram.com/sharge_official/")
    s = extract_socials(html, "https://www.sharge.com/")
    check("maker 自身の instagram は維持", s.get("instagram") == "https://www.instagram.com/sharge_official/")


def test_sns_does_not_over_exclude_substring() -> None:
    print("test_sns_does_not_over_exclude_substring")
    # ハンドルに「kickstarter」を含むが運営ではない正規アカウント（末尾に別語）。
    html = _link("https://www.instagram.com/kickstarterkitchen_shop/")
    s = extract_socials(html, "https://maker.example.com/")
    check(
        "kickstarterkitchen_shop は誤除外しない",
        s.get("instagram") == "https://www.instagram.com/kickstarterkitchen_shop/",
    )


def test_sns_case_slash_query_variants() -> None:
    print("test_sns_case_slash_query_variants")
    variants = [
        "https://www.Facebook.com/KICKSTARTER",
        "https://facebook.com/kickstarter/",
        "https://www.facebook.com/kickstarter?ref=nav",
    ]
    for v in variants:
        s = extract_socials(_link(v), "https://maker.example.com/")
        check(f"大小/スラッシュ/query でも除外: {v}", "facebook" not in s)


# =================== メール除外（email_exclusion_reason） ===================

def test_email_excludes_platform_operator() -> None:
    print("test_email_excludes_platform_operator")
    r = email_exclusion_reason("support@zeczec.com")
    check("support@zeczec.com は除外", r is not None)
    check("除外理由に zeczec を含む", r is not None and "zeczec" in r)


def test_email_excludes_agencies() -> None:
    print("test_email_excludes_agencies")
    for addr in ("apply@ideafound.com", "hi@brand-kr.com", "real1@makerz.co.kr"):
        r = email_exclusion_reason(addr)
        check(f"{addr} は代理店として除外", r is not None)
        check(f"{addr} の理由は agency", r is not None and "agency" in r)


def test_email_excludes_marketing_services() -> None:
    print("test_email_excludes_marketing_services")
    for addr in ("hi@kickbooster.me", "team@backerkit.com", "x@launchboom.com"):
        r = email_exclusion_reason(addr)
        check(f"{addr} は販促支援として除外", r is not None)
        check(f"{addr} の理由は marketing", r is not None and "marketing" in r)


def test_email_excludes_url_shorteners() -> None:
    print("test_email_excludes_url_shorteners")
    for addr in ("a@reurl.cc", "a@bit.ly", "a@tinyurl.com", "a@t.co"):
        r = email_exclusion_reason(addr)
        check(f"{addr} は短縮URL由来として除外", r is not None)


def test_email_keeps_maker_functional_addresses() -> None:
    print("test_email_keeps_maker_functional_addresses")
    # maker 公式ドメイン（deny-list に無い）の営業向け宛先は維持する。
    for addr in ("sales@sharge.com", "contact@arcwave.com", "info@nextbaby.co.kr"):
        check(f"{addr} は維持", email_exclusion_reason(addr) is None)


def test_email_keeps_maker_person_address() -> None:
    print("test_email_keeps_maker_person_address")
    # 公式ドメインの人物メールは維持（役割 person・deny-list に無い）。
    for addr in ("chris@sharge.com", "zhijian.chen@unionchen.com.tw"):
        check(f"{addr} は維持", email_exclusion_reason(addr) is None)


def test_email_source_mismatch_not_immediately_excluded() -> None:
    print("test_email_source_mismatch_not_immediately_excluded")
    # source(プラットフォーム) と一致しない別ドメインの正規メールは即除外しない。
    # 公式サイト brand.com とメール専用 brand.io が別でも deny-list でない限り残す。
    check(
        "brand.io は kickstarter 由来案件でも即除外しない",
        email_exclusion_reason("hello@brand.io", source_site_domain="kickstarter.com") is None,
    )


def test_email_rejection_reason_is_machine_readable() -> None:
    print("test_email_rejection_reason_is_machine_readable")
    r = email_exclusion_reason("apply@ideafound.com")
    check("rejection reason は 種別:詳細 形式", r is not None and r.startswith("third_party_owner:agency:"))
    r2 = email_exclusion_reason("a@reurl.cc")
    check("shortener の rejection reason", r2 is not None and "url_shortener" in r2)


# ============ accepted/rejected 双方に ownership class（classify_email） ============

def test_classify_email_attaches_ownership_both_sides() -> None:
    print("test_classify_email_attaches_ownership_both_sides")
    ctx = so.Ctx(maker_name="Sharge", official_domain="sharge.com")
    acc = so.classify_email("sales@sharge.com", ctx)
    check("採用側に ownership_class", acc["ownership_class"] == "maker_official")
    check("採用側 accepted=True", acc["accepted_as_maker_contact"] is True)
    rej = so.classify_email("support@zeczec.com", ctx)
    check("拒否側に ownership_class", rej["ownership_class"] == "crowdfunding_platform")
    check("拒否側 accepted=False", rej["accepted_as_maker_contact"] is False)
    check("拒否側に rejection_reason", bool(rej["rejection_reason"]))


def test_unrelated_same_name_company_not_accepted() -> None:
    print("test_unrelated_same_name_company_not_accepted")
    # 同名の無関係大企業は名一致だけでは maker 採用しない（低 confidence/非採用）。
    ctx = so.Ctx(maker_name="LG")
    r = so.classify_email("zhijian.chen@lge.com", ctx)
    check("lge.com は maker 採用しない", r["accepted_as_maker_contact"] is False)


# ============ fallback（代理店）チャネル（extract_emails_classified） ============

def test_agency_email_is_fallback_not_direct() -> None:
    print("test_agency_email_is_fallback_not_direct")
    r = so.classify_email("makerlive@brand-kr.com")
    check("brand-kr は maker 直通にしない", r["accepted_as_maker_contact"] is False)
    check("brand-kr は fallback 保持", r["accepted_as_fallback_contact"] is True)
    check("contact_route=agency", r["contact_route"] == "agency")
    check("rejection=not_direct_maker_contact", r["rejection_reason"] == "not_direct_maker_contact")
    check("ownership_class=agency", r["ownership_class"] == "agency")


def test_agency_without_evidence_is_unknown() -> None:
    print("test_agency_without_evidence_is_unknown")
    # deny-list に無い未知ドメインは agency 断定できず unknown（低 confidence 保留）。
    r = so.classify_email("hello@some-unknown-domain-xyz.com")
    check("未知ドメインは unknown", r["ownership_class"] == "unknown")
    check("unknown は maker 直通でない", r["accepted_as_maker_contact"] is False)
    check("unknown は fallback でもない", r["accepted_as_fallback_contact"] is False)
    check("unknown は low confidence", r["confidence"] == "low")


def test_maker_official_still_accepted_direct() -> None:
    print("test_maker_official_still_accepted_direct")
    ctx = so.Ctx(maker_name="Sharge", official_domain="sharge.com")
    r = so.classify_email("sales@sharge.com", ctx)
    check("maker 公式は direct 採用", r["accepted_as_maker_contact"] is True)
    check("contact_route=maker", r["contact_route"] == "maker")
    check("maker は high confidence", r["confidence"] == "high")


def test_classified_buckets_separate_agency_and_platform() -> None:
    print("test_classified_buckets_separate_agency_and_platform")
    html = (
        "메이커 이메일: makerlive@brand-kr.com / "
        "<a href='mailto:hi@brand-kr.com'>x</a> / support@wadiz.kr / "
        "info@realmaker.io"
    )
    cls = extract_emails_classified(html, source_site_domain="wadiz.kr")
    direct = {d["email"] for d in cls["direct"]}
    fallback = {d["email"] for d in cls["fallback"]}
    rejected = {d["email"] for d in cls["rejected"]}
    check("agency は fallback バケット", "hi@brand-kr.com" in fallback)
    check("agency は direct に入れない", "makerlive@brand-kr.com" not in direct)
    check("platform(wadiz) は rejected", "support@wadiz.kr" in rejected)
    check("platform は fallback に入れない", "support@wadiz.kr" not in fallback)
    check("unknown maker ドメインは direct/unknown 側（fallback でない）",
          "info@realmaker.io" not in fallback)


def test_platform_and_shortener_not_fallback() -> None:
    print("test_platform_and_shortener_not_fallback")
    for addr in ("support@zeczec.com", "a@reurl.cc", "team@backerkit.com"):
        r = so.classify_email(addr)
        check(f"{addr} は fallback に入れない", r["accepted_as_fallback_contact"] is False)
        check(f"{addr} は maker 直通でもない", r["accepted_as_maker_contact"] is False)


def test_owner_classifier_consistent() -> None:
    print("test_owner_classifier_consistent")
    # 既存 classify_email_owner がプラットフォームを platform と判定し続ける（回帰なし）。
    check("zeczec は platform 判定にならず除外側", email_exclusion_reason("a@zeczec.com") is not None)
    check("kickstarter は platform", classify_email_owner("a@kickstarter.com") == "platform")


def main() -> int:
    # SNS
    test_sns_excludes_platform_facebook()
    test_sns_excludes_platform_instagram_zeczec()
    test_sns_excludes_indiegogo_wadiz_ulule_self()
    test_sns_keeps_maker_own_social()
    test_sns_does_not_over_exclude_substring()
    test_sns_case_slash_query_variants()
    # Email exclusion
    test_email_excludes_platform_operator()
    test_email_excludes_agencies()
    test_email_excludes_marketing_services()
    test_email_excludes_url_shorteners()
    test_email_keeps_maker_functional_addresses()
    test_email_keeps_maker_person_address()
    test_email_source_mismatch_not_immediately_excluded()
    test_email_rejection_reason_is_machine_readable()
    # ownership 付与
    test_classify_email_attaches_ownership_both_sides()
    test_unrelated_same_name_company_not_accepted()
    # fallback（代理店）チャネル
    test_agency_email_is_fallback_not_direct()
    test_agency_without_evidence_is_unknown()
    test_maker_official_still_accepted_direct()
    test_classified_buckets_separate_agency_and_platform()
    test_platform_and_shortener_not_fallback()
    test_owner_classifier_consistent()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
