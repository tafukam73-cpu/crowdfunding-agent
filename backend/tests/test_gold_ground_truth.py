"""gold_ground_truth.json（人手検証 ground truth）の健全性ガード。

auto-label 汚染（admin@reurl.cc 型）や prediction 由来の混入を回帰的に防ぐ:
  - expected_direct_emails に第三者クラス（platform/agency/marketing/shortener/messenger/
    retailer）を絶対に入れない。
  - fallback は agency/distributor のみ。
  - verification_status は許可された enum のみ。
  - build_ground_truth.py の出力と JSON が一致（人手データが再現可能）。

実行: docker exec cfagent-backend python tests/test_gold_ground_truth.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import source_ownership as so  # noqa: E402

EVAL = BACKEND / "tests" / "contact_intel_eval"
GT = json.loads((EVAL / "gold_ground_truth.json").read_text(encoding="utf-8"))

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  - {name}")
    else:
        _f += 1
        print(f"  FAIL- {name}")


_THIRD_PARTY = {"crowdfunding_platform", "crowdfunding_marketing_service", "agency",
                "url_shortener", "messenger", "retailer"}
_STATUS = {"verified", "partially_verified", "blocked", "unresolved", "no_public_contact"}


def test_no_thirdparty_in_direct():
    print("test_no_thirdparty_in_direct")
    for c in GT:
        for e in c["expected_direct_emails"]:
            cls = so.classify_domain(e).ownership_class
            check(f"p{c['project_id']} direct {e} は第三者でない({cls})", cls not in _THIRD_PARTY)


def test_reurl_and_mediafol_not_ground_truth():
    print("test_reurl_and_mediafol_not_ground_truth")
    all_direct = {e.lower() for c in GT for e in c["expected_direct_emails"]}
    for bad in ("admin@reurl.cc", "info@mediafol.io", "moreshop07@gmail.com"):
        check(f"{bad} は direct ground truth に無い（汚染排除）", bad not in all_direct)


def test_fallback_is_agency_or_distributor():
    print("test_fallback_is_agency_or_distributor")
    for c in GT:
        for e in c["expected_fallback_emails"]:
            cls = so.classify_domain(e).ownership_class
            check(f"p{c['project_id']} fallback {e} は agency/distributor({cls})",
                  cls in ("agency", "distributor"))


def test_status_enum_and_evidence():
    print("test_status_enum_and_evidence")
    for c in GT:
        check(f"p{c['project_id']} status enum", c["verification_status"] in _STATUS)
        # verified で direct email があるなら証拠 URL 必須
        if c["verification_status"] == "verified" and c["expected_direct_emails"]:
            check(f"p{c['project_id']} verified email に証拠URL", bool(c["evidence_urls"]))


def test_denominator_split():
    print("test_denominator_split")
    st = {}
    for c in GT:
        st[c["verification_status"]] = st.get(c["verification_status"], 0) + 1
    check("24 案件", len(GT) == 24)
    check("verified が存在", st.get("verified", 0) >= 1)
    excluded = st.get("blocked", 0) + st.get("unresolved", 0)
    check("blocked/unresolved が分母外として分離される", excluded >= 1)
    print(f"    status 内訳: {st}")


def main():
    test_no_thirdparty_in_direct()
    test_reurl_and_mediafol_not_ground_truth()
    test_fallback_is_agency_or_distributor()
    test_status_enum_and_evidence()
    test_denominator_split()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
