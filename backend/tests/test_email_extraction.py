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
    deobfuscate_emails,
    email_exclusion_reason,
    extract_emails,
    score_email,
    source_site_email_domain,
)
from app.services.email_validation import (  # noqa: E402
    build_fallback_search_queries,
    business_email_reason,
    is_valid_business_email,
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


def test_business_email_validation() -> None:
    print("test_business_email_validation")
    # ダミー / プレースホルダーは無効
    for a in ("example@example.com", "maker@example.com", "test@example.com",
              "info@example.org", "hello@example.net", "a@test.com",
              "x@dummy.com", "y@sample.com", "z@domain.com", "user@yourdomain.com"):
        check(f"{a} は無効", is_valid_business_email(a) is False)
        check(f"{a} は除外される", email_exclusion_reason(a) is not None)

    # ローカル部がダミートークン（別ドメインでも無効）
    for a in ("example@brand.com", "dummy@brand.com", "sample@brand.com",
              "test@brand.com", "test.user@brand.com"):
        check(f"{a} は無効(ローカル)", is_valid_business_email(a) is False)
        check(f"{a} は除外される", email_exclusion_reason(a) is not None)

    # no-reply 系は無効
    for a in ("noreply@brand.com", "no-reply@brand.com", "donotreply@brand.com",
              "notifications@brand.com"):
        check(f"{a} は無効(noreply)", is_valid_business_email(a) is False)

    # 形式不正は無効
    for a in ("plainaddress", "a@@b.com", "a@b", "@b.com", "a@.com"):
        check(f"{a!r} は形式不正", is_valid_business_email(a) is False)

    # --- 正常な business email は有効（残す） ---
    for a in ("sales@vitesy.com", "partnership@brand.co", "hello@example-brand.com",
              "contact@my-company.io", "info@maker.com", "latest.news@brand.com",
              "contest@brand.com"):
        check(f"{a} は有効", is_valid_business_email(a) is True)
        check(f"{a} は除外されない", email_exclusion_reason(a) is None)

    # business_email_reason の理由が機械可読
    check("example.com の理由は dummy_domain",
          business_email_reason("a@example.com") == "dummy_domain:example")
    check("dummy@ の理由は dummy_local",
          business_email_reason("dummy@brand.com") == "dummy_local:dummy")


def test_extract_filters_dummy() -> None:
    print("test_extract_filters_dummy")
    html = """
    <p>Reach us at example@example.com or maker@example.com</p>
    <a href="mailto:test@example.com">test</a>
    <a href="mailto:hello@vitesy.com">real</a>
    <span>partnership@brand.com dummy@brand.com sample@brand.com</span>
    """
    emails = [e.lower() for e in extract_emails(html)]
    check("example@example.com 除外", "example@example.com" not in emails)
    check("maker@example.com 除外", "maker@example.com" not in emails)
    check("test@example.com 除外", "test@example.com" not in emails)
    check("dummy@brand.com 除外", "dummy@brand.com" not in emails)
    check("sample@brand.com 除外", "sample@brand.com" not in emails)
    check("hello@vitesy.com 採用", "hello@vitesy.com" in emails)
    check("partnership@brand.com 採用", "partnership@brand.com" in emails)


def _cf_encode(email: str, key: int = 0x42) -> str:
    """Cloudflare Email Protection 形式にエンコード（テスト用の逆変換）。"""
    out = "%02x" % key
    for ch in email:
        out += "%02x" % (ord(ch) ^ key)
    return out


def test_deobfuscate_emails() -> None:
    print("test_deobfuscate_emails")
    # Cloudflare Email Protection（data-cfemail / email-protection#hex）
    enc = _cf_encode("hello@brandco.com")
    html_cf = (
        f'<a href="/cdn-cgi/l/email-protection" class="__cf_email__" '
        f'data-cfemail="{enc}">[email&#160;protected]</a>'
    )
    emails = [e.lower() for e in extract_emails(html_cf)]
    check("Cloudflare 難読化を復号して抽出", "hello@brandco.com" in emails)

    # 実サイト由来の hex（Loftie）でも '@' を含む妥当な文字列に復号できる
    real = deobfuscate_emails('data-cfemail="cdbeb8bdbda2bfb98da1a2abb9a4a8e3aea2a0"')
    check("実 Cloudflare hex を復号（@ を含む）", any("@" in x for x in real))

    # テキスト難読化：[at] / (at) / ＠ / [dot] / spaced
    check("[at]/[dot] 形式を復号",
          "sales@brandco.com" in
          [e.lower() for e in extract_emails("Reach sales [at] brandco [dot] com now")])
    check("(at) 形式を復号",
          "info@brandco.com" in
          [e.lower() for e in extract_emails("mail: info(at)brandco.com")])
    check("全角＠ を復号",
          "hi@brandco.com" in
          [e.lower() for e in extract_emails("<p>hi＠brandco.com</p>")])
    check("spaced 'at'/'dot' を復号",
          "team@brandco.com" in
          [e.lower() for e in extract_emails("Email team at brandco dot com")])
    # 除外フィルタは難読化経由でも効く（no-reply は復号しても除外）
    check("難読化 no-reply は除外",
          "no-reply@brandco.com" not in
          [e.lower() for e in extract_emails("no-reply [at] brandco.com")])
    # 通常の散文を誤検出しない
    check("散文を誤検出しない（'meet at noon'）",
          extract_emails("Let's meet at noon today") == [])


def test_fallback_queries() -> None:
    print("test_fallback_queries")
    qs = build_fallback_search_queries(
        company_name="Vitesy", product_name="Natede",
        official_domain="vitesy.com",
    )
    check("fallback は複数返る", len(qs) >= 4)
    types = {q["type"] for q in qs}
    check("公式サイト検索を含む", "official_site" in types)
    check("LinkedIn 検索を含む", "linkedin" in types)
    check("site: 検索を含む", "site_search" in types)
    check("全項目に url がある", all(q.get("url") for q in qs))
    # 会社名も商品もドメインも無ければ空
    check("情報無しなら空", build_fallback_search_queries() == [])


def main() -> int:
    test_exclusion_reasons()
    test_extract_emails_filters()
    test_scores()
    test_platform_exclusion()
    test_email_owner()
    test_official_domain_score()
    test_extract_excludes_platform()
    test_business_email_validation()
    test_extract_filters_dummy()
    test_deobfuscate_emails()
    test_fallback_queries()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
