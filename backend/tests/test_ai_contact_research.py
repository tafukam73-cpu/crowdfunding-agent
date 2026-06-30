"""AI 連絡先リサーチの再検証・モック動作のオフライン検証（ネットワーク/DB 不要）。

- AI が返した候補メールを既存フィルタで再検証し、捏造（出典なし）・運営会社・
  監視/no-reply/ハッシュ風を除外することを確認する。
- モックリサーチャーが推測メールを作らず、出典付きの既存メールのみ昇格し、メール
  非公開時はフォーム/SNS/検索クエリを推奨することを確認する。
pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_ai_contact_research.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ai.contact_researcher import (  # noqa: E402
    ContactResearchContext,
)
from app.ai.mock_contact_researcher import MockContactResearcher  # noqa: E402
from app.services.contact_discovery_service import (  # noqa: E402
    validate_ai_candidate_emails,
)

_passed = 0
_failed = 0

SENTRY_DSN = "2c2bbb0dc8f6deb4cbe5c9175f5c7d02@o35514.ingest.sentry.io"


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_validate_drops_unverifiable() -> None:
    print("test_validate_drops_unverifiable")
    candidates = [
        # 出典なし＝推測の疑い → 除外
        {"email": "info@brand.com", "score": 60, "source_url": ""},
        # 出典あり・営業向け → 採用
        {
            "email": "partnership@brand.com",
            "score": 90,
            "source_url": "https://brand.com/contact",
        },
        # 運営会社（プラットフォーム）→ 除外
        {
            "email": "support@kickstarter.com",
            "score": 80,
            "source_url": "https://kickstarter.com/help",
        },
        # Sentry DSN（ハッシュ/監視）→ 除外
        {"email": SENTRY_DSN, "score": 50, "source_url": "https://brand.com"},
        # no-reply → 除外
        {
            "email": "no-reply@brand.com",
            "score": 50,
            "source_url": "https://brand.com",
        },
    ]
    out = validate_ai_candidate_emails(
        candidates, official_domain="brand.com", source_site_domain="kickstarter.com"
    )
    emails = {e["email"].lower() for e in out}
    check("出典なし info@ は除外", "info@brand.com" not in emails)
    check("出典あり partnership@ は採用", "partnership@brand.com" in emails)
    check("運営会社メールは除外", "support@kickstarter.com" not in emails)
    check("sentry DSN は除外", SENTRY_DSN.lower() not in emails)
    check("no-reply は除外", "no-reply@brand.com" not in emails)
    check("採用は 1 件のみ", len(out) == 1)
    if out:
        check("採用候補に出典が付く", bool(out[0]["source_url"]))
        check("採用候補に所有者分類が付く", out[0]["email_owner"] == "maker")


def test_validate_dedup_and_score_fallback() -> None:
    print("test_validate_dedup_and_score_fallback")
    candidates = [
        {"email": "sales@brand.com", "source_url": "https://brand.com/a"},
        {"email": "SALES@brand.com", "source_url": "https://brand.com/b"},
    ]
    out = validate_ai_candidate_emails(
        candidates, official_domain="brand.com", source_site_domain=None
    )
    check("大文字小文字の重複は 1 件に", len(out) == 1)
    check("スコア未指定でも補完される", out and out[0]["score"] > 0)


def test_mock_no_fabrication_with_form() -> None:
    print("test_mock_no_fabrication_with_form")
    ctx = ContactResearchContext(
        title="Cool Gadget",
        maker_name="BrandCo",
        source_site="kickstarter",
        official_site_url="https://brandco.com",
        primary_contact_form_url="https://brandco.com/contact",
        discovered_socials={"instagram": "https://instagram.com/brandco"},
        existing_candidate_emails=[],  # メール未発見
    )
    res = MockContactResearcher().research(ctx)
    check("メール未発見では候補メール 0 件（捏造しない）", res.candidate_emails == [])
    check("primary_email は None", res.primary_email is None)
    check("推奨チャネルはフォーム", res.recommended_channel == "contact_form")
    check("検索クエリを提案する", len(res.search_queries) > 0)
    check("出典にフォームを含む", any("contact" in s["url"] for s in res.sources))
    check("メモにメール未発見の旨", "メール" in res.notes)


def test_mock_promotes_existing_email() -> None:
    print("test_mock_promotes_existing_email")
    ctx = ContactResearchContext(
        title="Cool Gadget",
        maker_name="BrandCo",
        official_site_url="https://brandco.com",
        existing_candidate_emails=[
            {
                "email": "partnership@brandco.com",
                "score": 90,
                "tier": "high",
                "sources": ["https://brandco.com/contact"],
            },
            # 出典なしは昇格しない
            {"email": "ghost@brandco.com", "score": 50, "tier": "other", "sources": []},
        ],
    )
    res = MockContactResearcher().research(ctx)
    emails = {c.email for c in res.candidate_emails}
    check("出典付きメールを昇格", "partnership@brandco.com" in emails)
    check("出典なしメールは昇格しない", "ghost@brandco.com" not in emails)
    check("primary に出典付きメール", res.primary_email == "partnership@brandco.com")
    check("推奨チャネルは email", res.recommended_channel == "email")
    check("候補に出典 URL が付く", all(c.source_url for c in res.candidate_emails))


def main() -> int:
    test_validate_drops_unverifiable()
    test_validate_dedup_and_score_fallback()
    test_mock_no_fabrication_with_form()
    test_mock_promotes_existing_email()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
