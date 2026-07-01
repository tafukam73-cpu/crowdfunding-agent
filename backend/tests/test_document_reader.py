"""AI Document Reader のオフライン検証（ネットワーク/DB/Claude 不要）。

mock reader の抽出（捏造なし）と service の再検証（出典必須 / platform 除外 / SNS 正規化
/ スコアリング / 営業ランキング統合）を検証する。

実行（backend ディレクトリで）:
    python tests/test_document_reader.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ai.document_reader import (  # noqa: E402
    DocReaderEmail, DocReaderPerson, DocumentReaderContext, DocReaderPage,
    get_document_reader,
)
from app.ai.mock_document_reader import MockDocumentReader  # noqa: E402
from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import document_reader_service as drs  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_mock_no_fabrication():
    print("test_mock_no_fabrication")
    r = MockDocumentReader()
    ctx = DocumentReaderContext(
        title="Vitesy Fruit Bowl", maker_name="Vitesy", source_site="indiegogo",
        official_site_url="https://vitesy.com",
        pages=[DocReaderPage(
            url="https://vitesy.com/contact", page_type="contact",
            text="Reach us at hello@vitesy.com or partnership@vitesy.com.",
            emails=["hello@vitesy.com"],
            socials={"instagram": "https://www.instagram.com/vitesy/"},
        )],
        existing_socials={"linkedin": "https://www.linkedin.com/company/vitesy/"},
    )
    res = r.read(ctx)
    got = {e.email for e in res.emails}
    check("本文の hello@ を抽出", "hello@vitesy.com" in got)
    check("本文の partnership@ を抽出", "partnership@vitesy.com" in got)
    check("各メールに出典", all(e.source_url for e in res.emails))
    check("人名は捏造しない（空）", res.people == [])
    check("公式サイトを返す", res.official_site_url == "https://vitesy.com")
    check("SNS を統合", res.socials.get("instagram") and res.socials.get("linkedin"))
    check("スコア=公式+メール+フォーム+SNS", res.confidence_score == 90)

    # メールが本文に無ければ作らない
    ctx2 = DocumentReaderContext(
        title="X", maker_name="X",
        pages=[DocReaderPage(url="https://x.com", text="No email here.")],
    )
    res2 = r.read(ctx2)
    check("メール未発見なら空（捏造しない）", res2.emails == [])
    check("推奨連絡先 None", res2.recommended_contact is None)


def test_service_validation():
    print("test_service_validation")
    # AI が platform/no-reply/出典なしを混ぜてきても除外される
    emails = [
        DocReaderEmail(email="hello@vitesy.com", purpose="general_contact",
                       confidence=85, source_url="https://vitesy.com/contact"),
        DocReaderEmail(email="support@kickstarter.com", purpose="support",
                       confidence=50, source_url="https://x"),   # platform
        DocReaderEmail(email="no-reply@vitesy.com", purpose="other",
                       confidence=10, source_url="https://x"),   # auto-reply
        DocReaderEmail(email="ghost@vitesy.com", purpose="sales",
                       confidence=99, source_url=""),            # 出典なし
    ]
    out = drs._validate_emails(emails, "vitesy.com", "indiegogo.com")
    got = {e["email"] for e in out}
    check("hello@ は採用", "hello@vitesy.com" in got)
    check("platform(kickstarter) は除外", "support@kickstarter.com" not in got)
    check("no-reply は除外", "no-reply@vitesy.com" not in got)
    check("出典なしは除外", "ghost@vitesy.com" not in got)

    # SNS 正規化 + 運営SNS除外
    socials = drs._validate_socials({
        "instagram": "https://instagram.com/vitesy?hl=en",
        "facebook": "https://www.facebook.com/indiegogo/",  # 運営 → 除外
        "x": "https://x.com/vitesy",
    })
    check("Instagram 正規化", socials.get("instagram") == "https://www.instagram.com/vitesy/")
    check("運営 Facebook 除外", "facebook" not in socials)
    check("x は素通し", socials.get("x") == "https://x.com/vitesy")

    # 人名の再検証（氏名+出典必須、メールは検証）
    people = drs._validate_people([
        DocReaderPerson(name="Jane Doe", title="BD", source_url="https://x/team",
                        email="jane@vitesy.com", confidence=70),
        DocReaderPerson(name="NoSource", title="x", source_url="", confidence=50),
    ], "indiegogo.com")
    check("出典ありの人名のみ採用", len(people) == 1 and people[0]["name"] == "Jane Doe")

    # スコアリング
    check("スコア: メール+フォーム+SNS+公式+担当者=100",
          drs._score(["e"], ["f"], {"instagram": "x"}, "site", ["p"]) == 100)
    check("スコア: メールのみ=40", drs._score(["e"], [], {}, None, []) == 40)


def test_ranking_integration():
    print("test_ranking_integration")

    class Row:
        discovered_emails = None
        web_discovered_emails = None
        ai_candidate_emails = None
        doc_reader_emails = [
            {"email": "hello@vitesy.com", "purpose": "general_contact",
             "confidence": 85, "source_url": "https://vitesy.com/contact",
             "email_owner": "maker"},
        ]

    ranked = cds.build_sales_contacts(Row())
    check("doc reader メールがランキングに統合", any(c["email"] == "hello@vitesy.com" for c in ranked))
    check("hello@ は ★5", ranked and ranked[0]["stars"] == 5)


def test_factory_mock_when_no_key():
    print("test_factory_mock_when_no_key")
    from app.config import settings
    old = settings.anthropic_api_key
    settings.anthropic_api_key = ""
    try:
        check("キー未設定は mock", get_document_reader().name == "mock-document-reader-v1")
    finally:
        settings.anthropic_api_key = old


def main():
    test_mock_no_fabrication()
    test_service_validation()
    test_ranking_integration()
    test_factory_mock_when_no_key()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
