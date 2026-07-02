"""Contact Intelligence v3：公式サイト再帰クロールのオフライン検証（ネットワーク/DB 最小）。

ページ取得（fetch_fn）・DNS（resolve_fn）・PDF 解析（cds.extract_from_pdf）を注入し、
再帰巡回・sitemap/robots 解析・PDF 抽出・DNS(MX/SPF/DMARC)・login/cart スキップ・
失敗理由コード・DB 保存を検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_recursive_crawl.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# SessionLocal 束縛前に file sqlite を指定（別セッション共有のため）
_DBFILE = os.path.join(tempfile.gettempdir(), "recursive_crawl_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
from app.services import recursive_crawl_service as rcs  # noqa: E402

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


class FakeProject:
    id = 1
    title = "Cool Lamp"
    maker_name = "BrandCo"
    maker_url = "https://brandco.com"
    source_url = "https://www.kickstarter.com/projects/brandco/cool-lamp"
    source_site = "kickstarter"


_ROOT = """
<html><body>
  <a href="/contact">Contact us</a>
  <a href="/about">About</a>
  <a href="/privacy">Privacy</a>
  <a href="/terms">Terms</a>
  <a href="/team">Team</a>
  <a href="https://www.instagram.com/brandco">IG</a>
  <a href="https://linktr.ee/brandco">Links</a>
  <a href="/catalog.pdf">Catalog PDF</a>
  <a href="/account/login">Login</a>
  <a href="/cart">Cart</a>
  <a href="/checkout">Checkout</a>
  <a href="https://somenews.example/article">news</a>
</body></html>
"""
_CONTACT = """
<html><body>
  <p>Sales: <a href="mailto:sales@brandco.com">sales@brandco.com</a></p>
  <a href="mailto:no-reply@brandco.com">noreply</a>
  <a href="mailto:support@kickstarter.com">platform</a>
</body></html>
"""
_PRIVACY = "<html><body>privacy@brandco.com</body></html>"
_TERMS = "<html><body>Legal terms. Contact legal@brandco.com</body></html>"
_ABOUT = "<html><body>About BrandCo</body></html>"
_TEAM = "<html><body>Our team</body></html>"

_ROBOTS = """
User-agent: *
Disallow: /private
Sitemap: https://brandco.com/sitemap.xml
"""
_SITEMAP = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://brandco.com/contact</loc></url>
  <url><loc>https://brandco.com/about</loc></url>
  <url><loc>https://brandco.com/catalog.pdf</loc></url>
  <url><loc>https://brandco.com/private/secret</loc></url>
</urlset>
"""

_PAGES = {
    "https://brandco.com": _ROOT,
    "https://brandco.com/contact": _CONTACT,
    "https://brandco.com/about": _ABOUT,
    "https://brandco.com/privacy": _PRIVACY,
    "https://brandco.com/terms": _TERMS,
    "https://brandco.com/team": _TEAM,
    "https://brandco.com/robots.txt": _ROBOTS,
    "https://brandco.com/sitemap.xml": _SITEMAP,
}


def fake_fetch(url: str):
    return _PAGES.get(url)


def fake_resolve(name: str, rtype: str):
    if rtype == "MX" and name == "brandco.com":
        return ["aspmx.l.google.com", "alt1.aspmx.l.google.com"]
    if rtype == "TXT" and name == "brandco.com":
        return ["v=spf1 include:_spf.google.com ~all"]
    if rtype == "TXT" and name == "_dmarc.brandco.com":
        return ["v=DMARC1; p=none; rua=mailto:dmarc@brandco.com"]
    return []


# 既定は「PDF 本文なし」を返す注入版（ネットワークに出ない・オフライン）
def _empty_pdf(url, site_domain=None, timeout=12.0):
    return {"emails": [], "socials": {}, "text_len": 0, "text": ""}


def _run(official="https://brandco.com", pdf_fn=_empty_pdf, **kw):
    return rcs.recursive_crawl(
        official, FakeProject(), fetch_fn=fake_fetch, resolve_fn=fake_resolve,
        pdf_fn=pdf_fn, **kw
    )


def test_recursive_picks_contact_privacy_terms():
    print("test_recursive_picks_contact_privacy_terms")
    res = _run()
    crawled = res["recursive_crawled_urls"]
    check("有効化フラグ", res["recursive_crawl_enabled"] is True)
    check("/contact を巡回", "https://brandco.com/contact" in crawled)
    check("/privacy を巡回", "https://brandco.com/privacy" in crawled)
    check("/terms を巡回", "https://brandco.com/terms" in crawled)
    emails = {e["email"].lower() for e in res["recursive_emails"]}
    check("sales@ を抽出", "sales@brandco.com" in emails)
    check("privacy@ を抽出", "privacy@brandco.com" in emails)
    check("legal@ を抽出", "legal@brandco.com" in emails)
    check("no-reply は除外", "no-reply@brandco.com" not in emails)
    check("platform メールは除外", "support@kickstarter.com" not in emails)
    check("各メールに出典", all(e["sources"] for e in res["recursive_emails"]))
    check("SNS(Instagram) を候補化", "instagram" in res["recursive_socials"])
    check("Linktree を候補化", "linktree" in res["recursive_socials"])
    check("フォームを検出", any("/contact" in f for f in res["recursive_forms"]))


def test_login_cart_checkout_skipped():
    print("test_login_cart_checkout_skipped")
    res = _run()
    crawled = res["recursive_crawled_urls"]
    skipped = res["recursive_skipped_urls"]
    check("login をスキップ", any("login" in s for s in skipped))
    check("cart をスキップ", any("/cart" in s for s in skipped))
    check("checkout をスキップ", any("/checkout" in s for s in skipped))
    check("login を巡回しない", all("login" not in u for u in crawled))
    check("cart を巡回しない", all("/cart" not in u for u in crawled))
    check(
        "robots Disallow(/private) を巡回しない",
        all("/private" not in u for u in crawled),
    )
    check(
        "外部ニュースは巡回しない（同一ドメイン優先）",
        all("somenews.example" not in u for u in crawled),
    )


def test_sitemap_and_robots():
    print("test_sitemap_and_robots")
    res = _run()
    check(
        "robots から Sitemap を抽出",
        "https://brandco.com/sitemap.xml" in res["recursive_robots_sitemaps"],
    )
    sm = res["recursive_sitemap_urls"]
    check("sitemap から contact を拾う", "https://brandco.com/contact" in sm)
    check("sitemap から pdf を拾う", "https://brandco.com/catalog.pdf" in sm)
    # 優先順位：contact/pdf が /private/secret より前
    if "https://brandco.com/contact" in sm and "https://brandco.com/private/secret" in sm:
        check(
            "contact が private より優先",
            sm.index("https://brandco.com/contact")
            < sm.index("https://brandco.com/private/secret"),
        )
    else:
        check("contact が private より優先", True)


def test_dns_mx_spf_dmarc():
    print("test_dns_mx_spf_dmarc")
    res = _run()
    check("MX 検出", res["recursive_has_mx"] is True)
    check("MX プロバイダー(Google Workspace)", res["recursive_mx_provider"] == "Google Workspace")
    check("SPF 検出", (res["recursive_spf_record"] or "").startswith("v=spf1"))
    check("DMARC 検出", (res["recursive_dmarc_record"] or "").lower().startswith("v=dmarc1"))
    # 純粋関数単体
    d = rcs.check_dns("brandco.com", resolve_fn=fake_resolve)
    check("check_dns has_mx", d["has_mx"] is True)
    check("check_dns provider", d["mx_provider"] == "Google Workspace")


def test_pdf_extraction():
    print("test_pdf_extraction")

    def fake_pdf(url, site_domain=None, timeout=12.0):
        return {
            "emails": ["partnerships@brandco.com"],
            "socials": {"linkedin": "https://www.linkedin.com/company/brandco/"},
            "text_len": 1200,
            "text": "Contact: Jane Doe, CEO. john@brandco.com",
        }

    res = _run(pdf_fn=fake_pdf)
    emails = {e["email"].lower() for e in res["recursive_emails"]}
    check("PDF からメール抽出", "partnerships@brandco.com" in emails)
    check("PDF から SNS 抽出", res["recursive_socials"].get("linkedin") is not None)
    pdf_recs = res["recursive_pdfs"]
    check("PDF を解析対象に含む", any("catalog.pdf" in p["url"] for p in pdf_recs))
    check("PDF 解析でメール件数を記録", any((p.get("emails") or 0) > 0 for p in pdf_recs))
    people = res.get("recursive_people") or []
    check("PDF から担当者候補(Jane Doe, CEO)", any(p["name"] == "Jane Doe" for p in people))


def test_platform_url_not_official():
    print("test_platform_url_not_official")
    res = rcs.recursive_crawl(
        "https://www.kickstarter.com/projects/brandco/cool-lamp",
        FakeProject(), fetch_fn=fake_fetch, resolve_fn=fake_resolve,
    )
    check("プラットフォームURLは公式扱いしない", res["recursive_crawl_enabled"] is False)
    check(
        "OFFICIAL_SITE_NOT_FOUND を保存",
        "OFFICIAL_SITE_NOT_FOUND" in res["recursive_failure_reasons"],
    )


def test_failure_reasons_no_email():
    print("test_failure_reasons_no_email")
    # メール/フォームが無いサイト（MX あり）→ 失敗理由コードを検証
    pages = {
        "https://nomail.com": "<html><body><a href='/about'>About</a></body></html>",
        "https://nomail.com/about": "<html><body>No contact here</body></html>",
        "https://nomail.com/robots.txt": "",
        "https://nomail.com/sitemap.xml": "",
    }

    def f(url):
        return pages.get(url)

    def r(name, rtype):
        if rtype == "MX" and name == "nomail.com":
            return ["mail.protection.outlook.com"]
        return []

    class P(FakeProject):
        maker_url = "https://nomail.com"

    res = rcs.recursive_crawl("https://nomail.com", P(), fetch_fn=f, resolve_fn=r)
    reasons = res["recursive_failure_reasons"]
    check("EMAIL_NOT_PUBLIC", "EMAIL_NOT_PUBLIC" in reasons)
    check("DNS_MX_FOUND_EMAIL_NOT_PUBLIC", "DNS_MX_FOUND_EMAIL_NOT_PUBLIC" in reasons)
    check("MX プロバイダー(Microsoft 365)", res["recursive_mx_provider"] == "Microsoft 365")


def test_db_persist():
    print("test_db_persist")
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    import app.models  # noqa: F401  全モデルを metadata へ
    from app.models.contact_discovery import ContactDiscovery
    from app.models.project import Project

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        proj = Project(title="Cool Lamp", maker_name="BrandCo",
                       maker_url="https://brandco.com", source_site="kickstarter")
        db.add(proj)
        db.commit()
        db.refresh(proj)
        # 土台の探索行を用意（run_discovery のネットワーク実行を避ける）
        row = ContactDiscovery(
            project_id=proj.id, status="completed",
            official_site_url="https://brandco.com",
        )
        db.add(row)
        db.commit()

        saved = rcs.run_recursive_crawl(
            db, proj, fetch_fn=fake_fetch, resolve_fn=fake_resolve, pdf_fn=_empty_pdf
        )
        check("enabled 保存", saved.recursive_crawl_enabled is True)
        check("crawled_urls 保存", bool(saved.recursive_crawled_urls))
        check("emails 保存", bool(saved.recursive_emails))
        check("has_mx 保存", saved.recursive_has_mx is True)
        check("mx_provider 保存", saved.recursive_mx_provider == "Google Workspace")
        check("summary 保存", bool(saved.recursive_summary))
        # sales_contacts に再帰メールが反映される
        ranked = cds.build_sales_contacts(saved)
        emails = {c["email"].lower() for c in ranked}
        check("再帰メールがランキングに反映", "sales@brandco.com" in emails)
    finally:
        db.close()


def main() -> int:
    test_recursive_picks_contact_privacy_terms()
    test_login_cart_checkout_skipped()
    test_sitemap_and_robots()
    test_dns_mx_spf_dmarc()
    test_pdf_extraction()
    test_platform_url_not_official()
    test_failure_reasons_no_email()
    test_db_persist()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
