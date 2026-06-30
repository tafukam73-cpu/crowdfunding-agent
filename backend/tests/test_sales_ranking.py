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
    build_sales_contacts,
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


def main() -> int:
    test_rank_examples()
    test_build_ranking()
    test_empty()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
