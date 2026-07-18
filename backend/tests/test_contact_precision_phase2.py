"""Phase 2 precision（フォーム: maker-owned real form 選別）の単体検証。

select_maker_forms が第三者/ユーティリティフォームを除外し、soft-404 で量産される
同一ドメインの contact パス変種を intent 単位に畳むことを機能別に確認する。
gold 案件 ID や特定案件名で分岐しない。pytest 非依存で単体実行できる。

実行: docker exec cfagent-backend python tests/test_contact_precision_phase2.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.contact_discovery_service import select_maker_forms  # noqa: E402

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  ok  - {name}")
    else:
        _f += 1
        print(f"  FAIL- {name}")


def test_marketing_form_removed():
    print("test_marketing_form_removed")
    forms = ["https://hanboost.kickbooster.me/contact",
             "https://hanboost.kickbooster.me/contact-us"]
    out = select_maker_forms(forms, official_domain="hanboost.com")
    check("kickbooster(marketing) フォームは除外", out == [])


def test_platform_and_messenger_form_removed():
    print("test_platform_and_messenger_form_removed")
    forms = ["https://www.zeczec.com/contact", "https://m.me/107046535517915",
             "https://www.kickstarter.com/contact"]
    out = select_maker_forms(forms, official_domain="maker.com")
    check("platform/messenger フォームは全除外", out == [])


def test_utility_forms_removed():
    print("test_utility_forms_removed")
    forms = ["https://maker.com/login", "https://maker.com/account/register",
             "https://maker.com/search?q=x", "https://maker.com/newsletter",
             "https://maker.com/cart", "https://maker.com/contact"]
    out = select_maker_forms(forms, official_domain="maker.com")
    check("ユーティリティは除外し contact のみ残る", out == ["https://maker.com/contact"])


def test_same_domain_contact_variants_collapsed():
    print("test_same_domain_contact_variants_collapsed")
    # soft-404 で 200 を返す contact 変種が量産されても intent=contact で 1 本に畳む。
    forms = ["https://shop.unionchen.com.tw/contact.php?type=2",
             "https://shop.unionchen.com.tw/contact",
             "https://shop.unionchen.com.tw/contact-us",
             "https://shop.unionchen.com.tw/contactus",
             "https://shop.unionchen.com.tw/pages/contact",
             "https://shop.unionchen.com.tw/pages/contact-us"]
    out = select_maker_forms(forms, official_domain="unionchen.com.tw")
    check("contact 変種は 1 本に集約", len(out) == 1)
    check("残った 1 本は同一ドメインの contact", "unionchen.com.tw" in out[0])


def test_distinct_intents_kept():
    print("test_distinct_intents_kept")
    forms = ["https://maker.com/contact", "https://maker.com/contact-us",
             "https://maker.com/support", "https://maker.com/pages/support",
             "https://maker.com/wholesale"]
    out = select_maker_forms(forms, official_domain="maker.com")
    intents = {("wholesale" in u, "support" in u) for u in out}
    check("contact/support/wholesale の 3 intent が残る", len(out) == 3)
    check("wholesale が含まれる", any("wholesale" in u for u in out))
    check("support が含まれる", any("support" in u for u in out))


def test_official_domain_preferred_first():
    print("test_official_domain_preferred_first")
    forms = ["https://reseller-shop.com/contact", "https://maker.com/contact"]
    out = select_maker_forms(forms, official_domain="maker.com")
    check("公式ドメインのフォームが先頭", out[0] == "https://maker.com/contact")


def test_cross_domain_wholesale_kept():
    print("test_cross_domain_wholesale_kept")
    # 親会社の卸フォーム（別ドメイン）は maker チャネルとして残す（第三者 deny-list でない）。
    forms = ["https://www.arcwave.com/us/contact", "https://wowtech.com/wholesale/"]
    out = select_maker_forms(forms, official_domain="arcwave.com")
    check("arcwave contact が残る", any("arcwave.com" in u for u in out))
    check("wowtech 卸フォームが残る", any("wowtech.com" in u for u in out))


def test_limit_and_dedup_exact():
    print("test_limit_and_dedup_exact")
    forms = ["https://maker.com/contact", "https://maker.com/contact"]
    out = select_maker_forms(forms, official_domain="maker.com")
    check("完全重複は 1 本", out == ["https://maker.com/contact"])
    many = [f"https://maker{i}.com/contact" for i in range(10)]
    check("limit=4 で上限", len(select_maker_forms(many, limit=4)) == 4)


def main():
    test_marketing_form_removed()
    test_platform_and_messenger_form_removed()
    test_utility_forms_removed()
    test_same_domain_contact_variants_collapsed()
    test_distinct_intents_kept()
    test_official_domain_preferred_first()
    test_cross_domain_wholesale_kept()
    test_limit_and_dedup_exact()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
