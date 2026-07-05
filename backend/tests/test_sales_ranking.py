"""営業推奨連絡先ランキングのオフライン検証（ネットワーク/DB 不要）。

rank_sales_email（メール → 星評価・理由）と build_sales_contacts（全ソース統合・
営業順ソート）を検証する。要件の期待値（hello=star5 / support<=star3 / cv/apply/
authorities=star1）を満たすことを確認する。

実行（backend ディレクトリで）:
    python tests/test_sales_ranking.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.contact_discovery_service import (  # noqa: E402
    build_search_queries,
    build_sales_contacts,
    is_dummy_domain,
    rank_sales_channels,
    rank_sales_email,
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


def test_rank_examples() -> None:
    print("test_rank_examples")
    expect = {
        "hello@vitesy.com": 5,
        "support@vitesy.com": 3,
        "cv@vitesy.com": 1,
        "apply@vitesy.com": 1,
        "authorities@vitesy.com": 1,
        "sales@x.com": 4,
        "partnership@x.com": 4,
        "business@x.com": 4,
        "bd@x.com": 4,
        "b2b@x.com": 4,
        "distribution@x.com": 4,
        "distributor@x.com": 4,
        "wholesale@x.com": 4,
        "export@x.com": 4,
        "international@x.com": 4,
        "contact@x.com": 5,
        "info@x.com": 5,
        "careers@x.com": 1,
        "recruit@x.com": 1,
        "recruitment@x.com": 1,
        "privacy@x.com": 1,
        "gdpr@x.com": 1,
        "billing@x.com": 1,
        "legal@x.com": 1,
        "accounting@x.com": 1,
        "help@x.com": 3,
        "service@x.com": 3,
        "press@x.com": 2,
        "media@x.com": 2,
    }
    for email, exp in expect.items():
        got = rank_sales_email(email)["stars"]
        check(f"{email} -> star{exp}", got == exp)

    check("reason present", bool(rank_sales_email("hello@x.com")["reason"]))
    r = rank_sales_email("hello@x.com", email_owner="maker")
    check("official-domain note", "公式ドメイン" in r["reason"] and r["stars"] == 5)


class FakeRow:
    discovered_emails = [
        {"email": "support@vitesy.com", "score": 30, "tier": "low", "email_owner": "maker", "sources": ["s1"]},
        {"email": "hello@vitesy.com", "score": 60, "tier": "mid", "email_owner": "maker", "sources": ["s2"]},
        {"email": "cv@vitesy.com", "score": 50, "tier": "other", "email_owner": "maker", "sources": ["s3"]},
        {"email": "support@kickstarter.com", "score": 60, "tier": "mid", "email_owner": "platform", "sources": ["p"]},
    ]
    web_discovered_emails = [
        {"email": "partnership@vitesy.com", "score": 90, "tier": "high", "email_owner": "maker", "sources": ["w"]},
        {"email": "apply@vitesy.com", "score": 50, "tier": "other", "email_owner": "maker", "sources": ["w2"]},
    ]
    ai_candidate_emails = [
        {"email": "authorities@vitesy.com", "score": 40, "source_url": "a", "email_owner": "maker"},
        {"email": "hello@vitesy.com", "score": 55, "source_url": "a2", "email_owner": "maker"},
    ]


def test_build_ranking() -> None:
    print("test_build_ranking")
    ranked = build_sales_contacts(FakeRow())
    emails = [c["email"] for c in ranked]
    star = {c["email"]: c["stars"] for c in ranked}

    check("top is hello (star5)", emails[0] == "hello@vitesy.com" and star["hello@vitesy.com"] == 5)
    check("hello deduped to 1", emails.count("hello@vitesy.com") == 1)
    check("partnership star4", star["partnership@vitesy.com"] == 4)
    check("support star3", star["support@vitesy.com"] == 3)
    check("cv star1", star["cv@vitesy.com"] == 1)
    check("apply star1", star["apply@vitesy.com"] == 1)
    check("authorities star1", star["authorities@vitesy.com"] == 1)
    check("platform email excluded", "support@kickstarter.com" not in emails)
    star_seq = [c["stars"] for c in ranked]
    check("stars descending", star_seq == sorted(star_seq, reverse=True))
    check("hello > support > cv order",
          emails.index("hello@vitesy.com") < emails.index("support@vitesy.com")
          < emails.index("cv@vitesy.com"))


def test_empty() -> None:
    print("test_empty")

    class Empty:
        discovered_emails = None
        web_discovered_emails = None
        ai_candidate_emails = None

    check("empty -> []", build_sales_contacts(Empty()) == [])
    check("None -> []", build_sales_contacts(None) == [])


def test_rank_sales_channels() -> None:
    """要件 9・10：営業可能チャネルの優先順位・スコア。"""
    print("test_rank_sales_channels")
    # メール無し → フォーム→LinkedIn 会社→LinkedIn 担当者→Instagram DM の順
    ch = rank_sales_channels(
        emails=[],
        forms=["https://brandco.com/contact"],
        linkedin_company_url="https://www.linkedin.com/company/brandco/",
        linkedin_person_url="https://www.linkedin.com/in/jane/",
        socials={
            "instagram": "https://www.instagram.com/brandco/",
            "facebook": "https://www.facebook.com/brandco",
            "linkedin": "https://www.linkedin.com/company/brandco/",
            "pinterest": "https://www.pinterest.com/brandco/",
        },
    )
    order = [c["channel"] for c in ch]
    check("メール無しでも終了しない（チャネルあり）", len(ch) > 0)
    check("フォームが最優先", order[0] == "contact_form")
    check("LinkedIn 会社がフォームの次", order[1] == "linkedin_company")
    check("LinkedIn 担当者が会社の次", order[2] == "linkedin_person")
    check("SNS(instagram) を含む", "instagram" in order)
    check("linkedin プラットフォームは SNS 二重計上しない", "linkedin" not in order)
    # priority 昇順で並ぶ
    prio = [c["priority"] for c in ch]
    check("priority 昇順", prio == sorted(prio))

    # 有効メールがあれば最優先・スコア最高（Contact 由来）
    ch2 = rank_sales_channels(
        emails=[{"email": "sales@brandco.com", "email_owner": "maker",
                 "confidence_source": "official_site_contact"}],
        forms=["https://brandco.com/contact"],
    )
    check("メールが最優先", ch2[0]["channel"] == "email")
    check("Contact 由来メールは高スコア", ch2[0]["score"] >= 90)

    # Privacy/Terms 由来のメールは低〜中スコア
    ch3 = rank_sales_channels(
        emails=[{"email": "info@brandco.com", "email_owner": "maker",
                 "confidence_source": "official_site_legal"}],
    )
    check("Privacy/Terms 由来は低〜中スコア", ch3[0]["score"] <= 65)

    # 手動検索のみ（何も無い）
    ch4 = rank_sales_channels(emails=[], search_queries=['"BrandCo" email'])
    check("手動検索候補が最後の手段", ch4 and ch4[0]["channel"] == "manual_search")
    check("完全に空 → []", rank_sales_channels() == [])


def test_search_query_dummy_guard() -> None:
    """要件 8：site: 検索は example/dummy/test ドメインでは生成しない。"""
    print("test_search_query_dummy_guard")
    check("example.com はダミー", is_dummy_domain("example.com") is True)
    check("test.io はダミー", is_dummy_domain("test.io") is True)
    check("dummy.net はダミー", is_dummy_domain("dummy.net") is True)
    check("空はダミー扱い", is_dummy_domain("") is True)
    check("正規ドメインはダミーでない", is_dummy_domain("brandco.com") is False)
    check("example-brand.com は正規", is_dummy_domain("example-brand.com") is False)

    qs = build_search_queries("BrandCo", "example.com")
    check("example.com で site: を生成しない",
          not any(q.startswith("site:example.com") for q in qs))
    # 会社名クエリは 20 種類以上（要件 8）
    check("最低 20 クエリ（会社名+ドメイン）",
          len(build_search_queries("BrandCo", "brandco.com")) >= 20)
    qs2 = build_search_queries("BrandCo", "brandco.com")
    check("正規ドメインで site: を生成する",
          any(q.startswith("site:brandco.com") for q in qs2))
    check("filetype:pdf クエリを含む", any("filetype:pdf" in q for q in qs2))
    check("founder/CEO クエリを含む",
          any("founder" in q for q in qs2) and any("CEO" in q for q in qs2))


def main() -> int:
    test_rank_examples()
    test_build_ranking()
    test_empty()
    test_rank_sales_channels()
    test_search_query_dummy_guard()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
