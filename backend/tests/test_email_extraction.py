"""連絡先探索のメール抽出・除外ロジックのオフライン検証（ネットワーク/DB 不要）。

Sentry DSN 由来のような営業に使えない文字列を除外し、sales@ / partnership@ /
info@ などの営業向け宛先は残すことを確認する。除外理由（email_exclusion_reason）
も検証できるようにしている。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_email_extraction.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# app.db.session が import 時に engine を作るため、PostgreSQL ドライバを避けて
# SQLite に差し替える（このテストは実 DB に接続しない）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services.contact_discovery_service import (  # noqa: E402
    classify_email_owner,
    email_exclusion_reason,
    extract_emails,
    score_email,
    source_site_email_domain,
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


# Sentry DSN 由来の文字列（メールではない）
SENTRY_DSN = "2c2bbb0dc8f6deb4cbe5c9175f5c7d02@o35514.ingest.sentry.io"


def test_exclusion_reasons() -> None:
    print("test_exclusion_reasons")
    # Sentry: ドメインで除外（理由に sentry.io を含む）
    r = email_exclusion_reason(SENTRY_DSN)
    check("sentry DSN は除外される", r is not None)
    check("sentry の除外理由はドメイン", r is not None and "sentry.io" in r)

    # ドメイン除外
    check("ingest.sentry.io 除外", email_exclusion_reason("a@o1.ingest.sentry.io") is not None)
    check("sentry-next.com 除外", email_exclusion_reason("hi@sentry-next.com") is not None)
    check("localhost 除外", email_exclusion_reason("dev@localhost") is not None)
    check("example.com 除外", email_exclusion_reason("a@example.com") is not None)
    check("test.com 除外", email_exclusion_reason("a@test.com") is not None)

    # ハッシュ風ローカル部（別ドメインでも除外）
    check(
        "ハッシュ風ローカル部 除外",
        email_exclusion_reason("2c2bbb0dc8f6deb4cbe5c9175f5c7d02@brand.com")
        == "hash_local_part",
    )

    # 自動送信系
    for a in ("no-reply@brand.com", "noreply@brand.com", "donotreply@brand.com",
              "do-not-reply@brand.com"):
        check(f"{a} 除外", email_exclusion_reason(a) == "auto_reply_local_part")

    # 技術系・監視系
    check("postmaster 除外", email_exclusion_reason("postmaster@brand.com") is not None)
    check("sentry@ 除外", email_exclusion_reason("sentry@brand.com") is not None)
    check("bounce 除外", email_exclusion_reason("bounce@brand.com") is not None)

    # --- 除外してはいけない営業向け宛先 ---
    check("sales@ は除外しない", email_exclusion_reason("sales@example-brand.com") is None)
    check("partnership@ は除外しない", email_exclusion_reason("partnership@brand.com") is None)
    check("hello@ は除外しない", email_exclusion_reason("hello@brand.com") is None)
    check("info@ は除外しない", email_exclusion_reason("info@brand.com") is None)
    # example.com ではない似たドメインは除外しない
    check(
        "example-brand.com は除外しない",
        email_exclusion_reason("sales@example-brand.com") is None,
    )


def test_extract_emails_filters() -> None:
    print("test_extract_emails_filters")
    html = f"""
    <html><body>
      <a href="mailto:{SENTRY_DSN}">tracking</a>
      <a href="mailto:sales@example-brand.com">sales</a>
      <p>Contact partnership@brand.com or info@brand.com</p>
      <a href="mailto:no-reply@brand.com">noreply</a>
      <span>postmaster@brand.com</span>
    </body></html>
    """
    emails = [e.lower() for e in extract_emails(html)]
    check("sentry DSN が抽出されない", SENTRY_DSN.lower() not in emails)
    check("sales@example-brand.com が抽出される", "sales@example-brand.com" in emails)
    check("partnership@brand.com が抽出される", "partnership@brand.com" in emails)
    check("info@brand.com が抽出される", "info@brand.com" in emails)
    check("no-reply@brand.com は抽出されない", "no-reply@brand.com" not in emails)
    check("postmaster@brand.com は抽出されない", "postmaster@brand.com" not in emails)


def test_scores() -> None:
    print("test_scores")
    p_score, p_tier = score_email("partnership@brand.com")
    check("partnership@ は high", p_tier == "high")
    check("partnership@ は高スコア(>=90)", p_score >= 90)

    i_score, i_tier = score_email("info@brand.com")
    check("info@ は mid", i_tier == "mid")
    check("info@ は中スコア(40-70)", 40 <= i_score <= 70)

    s_score, s_tier = score_email("sales@example-brand.com")
    check("sales@ は high", s_tier == "high")


def test_platform_exclusion() -> None:
    print("test_platform_exclusion")
    # クラウドファンディング運営会社のメールは除外（理由は platform_domain）
    for a in ("support@ulule.com", "support@kickstarter.com", "hi@indiegogo.com",
              "info@makuake.com", "x@greenfunding.jp", "y@wadiz.kr"):
        r = email_exclusion_reason(a)
        check(f"{a} 除外", r is not None and r.startswith("platform_domain:"))

    # source_site 一致のプラットフォームも除外（静的リストに無いドメインを想定）
    check(
        "source_site 一致で除外",
        email_exclusion_reason("info@newplatform.io", source_site_domain="newplatform.io")
        is not None,
    )

    # メーカーのメールはプラットフォーム扱いしない（採用される）
    for a in ("sales@maker.com", "partnership@maker.com", "info@maker.com"):
        check(f"{a} は除外しない", email_exclusion_reason(a) is None)


def test_email_owner() -> None:
    print("test_email_owner")
    check("ulule は platform", classify_email_owner("support@ulule.com") == "platform")
    check("kickstarter は platform",
          classify_email_owner("support@kickstarter.com") == "platform")
    check("sentry は monitoring", classify_email_owner("a@x.ingest.sentry.io") == "monitoring")
    check("postmaster は monitoring",
          classify_email_owner("postmaster@maker.com") == "monitoring")
    check(
        "公式ドメイン一致は maker",
        classify_email_owner("sales@maker.com", official_domain="maker.com") == "maker",
    )
    check("公式不明は unknown", classify_email_owner("sales@maker.com") == "unknown")
    check("source_site map", source_site_email_domain("ulule") == "ulule.com")


def test_official_domain_score() -> None:
    print("test_official_domain_score")
    base, _ = score_email("info@maker.com")
    boosted, _ = score_email("info@maker.com", official_domain="maker.com")
    check("公式ドメイン一致でスコア上昇", boosted > base)
    sub, _ = score_email("info@shop.maker.com", official_domain="maker.com")
    check("サブドメイン一致でもスコア上昇", sub > base)


def test_extract_excludes_platform() -> None:
    print("test_extract_excludes_platform")
    html = """
    <a href="mailto:support@ulule.com">help</a>
    <a href="mailto:support@kickstarter.com">ks</a>
    <p>sales@maker.com partnership@maker.com info@maker.com</p>
    """
    emails = [e.lower() for e in extract_emails(html, source_site_domain="ulule.com")]
    check("support@ulule.com 除外", "support@ulule.com" not in emails)
    check("support@kickstarter.com 除外", "support@kickstarter.com" not in emails)
    check("sales@maker.com 採用", "sales@maker.com" in emails)
    check("partnership@maker.com 採用", "partnership@maker.com" in emails)
    check("info@maker.com 採用", "info@maker.com" in emails)


def main() -> int:
    test_exclusion_reasons()
    test_extract_emails_filters()
    test_scores()
    test_platform_exclusion()
    test_email_owner()
    test_official_domain_score()
    test_extract_excludes_platform()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
