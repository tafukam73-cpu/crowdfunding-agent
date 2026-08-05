"""韓国語ラベルによるメール役割判定と、採否本体への伝播の検証。

30件実測（wadiz 案件）で、個人情報保護責任者のメールが「送信可」と判定された。
原因は 2 つ:
  1. role_from_label に韓国語の語彙が無かった
  2. classify_email がラベルを受け取らず local-part だけで判定していた
本テストは両方が解消されていることを、gold 案件に依存しない一般ルールで検証する。

実行: docker compose exec -T backend python tests/test_email_role_korean.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'email_role_ko.sqlite'}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import source_ownership as so  # noqa: E402

_passed = _failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_korean_exclude_labels():
    print("test_korean_exclude_labels")
    for lbl in ("개인정보보호책임자", "개인정보 보호책임자", "개인정보보호",
                "개인정보 보호", "정보보호책임자", "홍보", "언론", "미디어",
                "보도", "채용", "법무"):
        check(f"'{lbl}' → exclude", so.role_from_label(lbl) == "exclude")


def test_korean_support_labels():
    print("test_korean_support_labels")
    for lbl in ("고객센터", "고객지원", "서비스센터"):
        check(f"'{lbl}' → support", so.role_from_label(lbl) == "support")


def test_korean_high_labels():
    print("test_korean_high_labels")
    for lbl in ("제휴", "사업제휴", "파트너십", "파트너", "리셀러", "대리점",
                "총판", "유통", "도매", "수출", "해외영업", "글로벌영업",
                "비즈니스", "영업"):
        check(f"'{lbl}' → high", so.role_from_label(lbl) == "high")


def test_required_regression_cases():
    """ご指定の必須回帰ケース（30件実測で問題になった実アドレス）。"""
    print("test_required_regression_cases")
    check("parklon@parklonmall.com + 개인정보보호책임자 → exclude",
          so.email_role("parklon@parklonmall.com",
                        label="론 통신판매업신고 개인정보보호책임자 : 이재학 이메일") == "exclude")
    check("miri.kim@earlyance.kr + 개인정보보호책임자 → exclude",
          so.email_role("miri.kim@earlyance.kr",
                        label="개인정보보호책임자. 김미리") == "exclude")
    check("affiliate@xgimi.com + 제휴 파트너 → high",
          so.email_role("affiliate@xgimi.com", label="제휴 파트너") == "high")
    check("media@xgimi.com + PR 문의 → exclude",
          so.email_role("media@xgimi.com", label="PR 문의") == "exclude")
    check("business@xgimi.com + 파트너 및 기업 사업 문의 → high",
          so.email_role("business@xgimi.com",
                        label="파트너 및 기업 사업 문의") == "high")


def test_label_propagates_to_classify_email():
    """classify_email がラベルを受け取り、採否に反映すること（本丸）。"""
    print("test_label_propagates_to_classify_email")
    ctx = so.Ctx(maker_name="Parklon", brand_name="Parklon",
                 official_domain="parklonmall.com")

    without = so.classify_email("parklon@parklonmall.com", ctx)
    with_lbl = so.classify_email("parklon@parklonmall.com", ctx,
                                 label="개인정보보호책임자 : 이재학")
    print(f"      label無: role={without['role_type']} accepted={without['accepted_as_maker_contact']}")
    print(f"      label有: role={with_lbl['role_type']} accepted={with_lbl['accepted_as_maker_contact']}")
    check("ラベル無しでは unknown（person へ昇格しない）",
          without["role_type"] == "unknown")
    check("ラベル有りで exclude になる", with_lbl["role_type"] == "exclude")
    check("ラベル有りで採用されない",
          with_lbl["accepted_as_maker_contact"] is False)
    check("不採用理由が role_exclude",
          (with_lbl.get("rejection_reason") or "").startswith("role_exclude"))


def test_classify_email_target_evidence():
    """証跡（ラベル原文・取得URL・確認日時）が保持されること。"""
    print("test_classify_email_target_evidence")
    ctx = so.Ctx(maker_name="Earlyance", brand_name="Earlyance",
                 official_domain="earlyance.kr")
    src = "https://earlyance.kr"
    at = "2026-08-05T00:00:00Z"

    r = so.classify_email_target("miri.kim@earlyance.kr", ctx,
                                 label="개인정보보호책임자. 김미리",
                                 source_url=src, checked_at=at)
    check("role=exclude", r["role"] == "exclude")
    check("sendable=False", r["sendable"] is False)
    check("role_source=label", r["role_source"] == "label")
    check("label_raw を保持", "개인정보보호책임자" in (r["label_raw"] or ""))
    check("source_url を保持", r["source_url"] == src)
    check("checked_at を保持", r["checked_at"] == at)
    check("理由が入っている", len(r["reasons"]) > 0)

    ok = so.classify_email_target("sales@earlyance.kr", ctx,
                                  label="대리점 문의", source_url=src, checked_at=at)
    check("代理店窓口は sendable=True", ok["sendable"] is True)
    check("role=high", ok["role"] == "high")

    unk = so.classify_email_target("jieun@earlyance.kr", ctx,
                                   source_url=src, checked_at=at)
    check("ラベル無しの個人名は role=unknown", unk["role"] == "unknown")
    check("unknown は sendable=False（勝手に昇格しない）", unk["sendable"] is False)
    check("role_source=unknown", unk["role_source"] == "unknown")

    named = so.classify_email_target("jieun@earlyance.kr", ctx,
                                     person_names={"jieun"},
                                     source_url=src, checked_at=at)
    check("氏名一致があれば person", named["role"] == "person")
    check("氏名一致があれば sendable=True", named["sendable"] is True)
    check("role_source=person_name_match", named["role_source"] == "person_name_match")


def test_rank_maker_emails_labels():
    """rank_maker_emails に labels を渡すと除外が効くこと。"""
    print("test_rank_maker_emails_labels")
    ctx = so.Ctx(maker_name="XGIMI", brand_name="XGIMI", official_domain="xgimi.com")
    emails = ["affiliate@xgimi.com", "media@xgimi.com", "service@xgimi.com"]
    labels = {
        "affiliate@xgimi.com": "제휴 파트너",
        "media@xgimi.com": "PR 문의",
        "service@xgimi.com": "고객센터",
    }
    ranked = so.rank_maker_emails(emails, ctx, labels=labels)
    got = [r["email"] for r in ranked]
    print(f"      ranked={got}")
    check("media@（広報）が除外される", "media@xgimi.com" not in got)
    check("affiliate@ が採用される", "affiliate@xgimi.com" in got)
    check("affiliate@ が先頭（제휴→high）",
          bool(got) and got[0] == "affiliate@xgimi.com")

    ranked_nolabel = so.rank_maker_emails(emails, ctx)
    check("labels 省略時も動作する（後方互換）", isinstance(ranked_nolabel, list))


def test_no_false_exclude():
    """営業向きラベルを誤って除外しないこと。"""
    print("test_no_false_exclude")
    check("'공식몰' は exclude でない", so.role_from_label("공식몰") != "exclude")
    check("'구매 문의' は exclude でない", so.role_from_label("구매 문의") != "exclude")
    check("判定不能は None", so.role_from_label("회사 주소") is None)


def main():
    test_korean_exclude_labels()
    test_korean_support_labels()
    test_korean_high_labels()
    test_required_regression_cases()
    test_label_propagates_to_classify_email()
    test_classify_email_target_evidence()
    test_rank_maker_emails_labels()
    test_no_false_exclude()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
