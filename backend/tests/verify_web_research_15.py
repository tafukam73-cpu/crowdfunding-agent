"""AI Web Research の 15 件オフライン検証ハーネス（要件 8）。

ネットワークと DB を使わず、search_fn / fetch_fn を注入して 15 件
（Kickstarter 5 / Indiegogo 5 / Ulule 5）を再現する。各案件の検索結果には
「手動 Google 検索で簡単に見つかる」本人 SNS（Instagram / Facebook / LinkedIn）と、
ツールが誤採用しがちなノイズ（運営公式 SNS・share/login・無関係ニュース・同名別
ブランド）を混在させ、改善後ロジックが本人 SNS を採用しノイズを除外できるかを測る。

注意：これはライブ検索ではなく固定フィクスチャによる再現検証。実ネット検証は
DuckDuckGo のブロック等で不安定なため、ロジックの妥当性を決定的に確認する目的。

実行（backend ディレクトリで）:
    python tests/verify_web_research_15.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import web_research_service as w  # noqa: E402


class P:
    """テスト用の最小 Project スタブ。"""

    def __init__(self, *, id, title, maker_name, domain, source_site, handle):
        self.id = id
        self.title = title
        self.maker_name = maker_name
        self.maker_url = f"https://{domain}"
        self.source_url = f"https://www.{source_site}.com/projects/{handle}"
        self.source_site = source_site
        self.description = f"{title} by {maker_name}. A new product."
        self.description_clean = self.description


# 15 件（KS5 / IGG5 / Ulule5）。handle は本人 SNS の URL に使う。
CASES = [
    # Kickstarter
    dict(id=1, title="Nuro Sleep Mask", maker_name="Nuro Labs", domain="nurolabs.com", source_site="kickstarter", handle="nurolabs"),
    dict(id=2, title="Lumio Lamp", maker_name="Lumio", domain="hellolumio.com", source_site="kickstarter", handle="lumio"),
    dict(id=3, title="Peak Design Travel Tripod", maker_name="Peak Design", domain="peakdesign.com", source_site="kickstarter", handle="peakdesign"),
    dict(id=4, title="Ridge Wallet", maker_name="Ridge", domain="ridge.com", source_site="kickstarter", handle="ridgewallet"),
    dict(id=5, title="Anker Power Bank", maker_name="Anker", domain="anker.com", source_site="kickstarter", handle="anker"),
    # Indiegogo
    dict(id=6, title="Flow Hive", maker_name="Honey Flow", domain="honeyflow.com", source_site="indiegogo", handle="flowhive"),
    dict(id=7, title="Pebble Watch", maker_name="Pebble", domain="getpebble.com", source_site="indiegogo", handle="pebble"),
    dict(id=8, title="Tile Tracker", maker_name="Tile", domain="thetileapp.com", source_site="indiegogo", handle="tile"),
    dict(id=9, title="Skydio Drone", maker_name="Skydio", domain="skydio.com", source_site="indiegogo", handle="skydio"),
    dict(id=10, title="Oura Ring", maker_name="Oura", domain="ouraring.com", source_site="indiegogo", handle="ouraring"),
    # Ulule
    dict(id=11, title="Loom Backpack", maker_name="Loom", domain="loom-paris.com", source_site="ulule", handle="loomparis"),
    dict(id=12, title="Bivouak Tent", maker_name="Bivouak", domain="bivouak.fr", source_site="ulule", handle="bivouak"),
    dict(id=13, title="Marlette Granola", maker_name="Marlette", domain="marlette.fr", source_site="ulule", handle="marlette"),
    dict(id=14, title="Le Slip Francais", maker_name="Le Slip", domain="leslipfrancais.fr", source_site="ulule", handle="leslipfrancais"),
    dict(id=15, title="Respire Deodorant", maker_name="Respire", domain="respire-care.com", source_site="ulule", handle="respire"),
]


def make_search(c: dict):
    """本人 SNS ＋ ノイズ（運営公式 SNS / share / login / 無関係）を返す search_fn。"""
    h = c["handle"]
    domain = c["domain"]
    title = c["title"]
    maker = c["maker_name"]
    site = c["source_site"]
    results = [
        # 運営公式 SNS（誤採用してはいけない）
        {"url": f"https://www.instagram.com/{site}/", "title": site, "snippet": "crowdfunding"},
        # 本人 SNS（手動検索で簡単に見つかる）
        {"url": f"https://www.instagram.com/{h}/", "title": f"{maker} (@{h}) • Instagram", "snippet": f"{title} official"},
        {"url": f"https://www.facebook.com/{h}", "title": f"{maker} | Facebook", "snippet": f"{title}"},
        {"url": f"https://www.linkedin.com/company/{h}/", "title": f"{maker} | LinkedIn", "snippet": f"{maker} company"},
        # 公式サイト
        {"url": f"https://{domain}/", "title": f"{maker} – Official", "snippet": title},
        # ノイズ：share / login / 無関係ニュース / 同名別ブランド
        {"url": "https://www.facebook.com/sharer/sharer.php?u=x", "title": "Share", "snippet": ""},
        {"url": "https://www.instagram.com/accounts/login/", "title": "Login", "snippet": ""},
        {"url": "https://news.example.org/2026/some-unrelated-article", "title": "Unrelated news", "snippet": "tech"},
        {"url": f"https://{domain}/media-kit.pdf", "title": "Media kit", "snippet": "press"},
    ]

    def search(query: str):
        return results

    return search


def make_fetch(c: dict):
    """公式サイト / contact に営業メール・フォーム・本人 SNS を置いた fetch_fn。"""
    domain = c["domain"]
    h = c["handle"]
    pages = {
        f"https://{domain}": (
            f'<html><body><a href="/contact">Contact</a>'
            f'<a href="https://www.instagram.com/{h}/">IG</a>'
            f'<p>info@{domain}</p></body></html>'
        ),
        f"https://{domain}/contact": (
            f'<html><body>'
            f'<a href="mailto:partnership@{domain}">partner</a>'
            f'<a href="mailto:support@{c["source_site"]}.com">platform</a>'
            f'</body></html>'
        ),
    }

    def fetch(url: str):
        return pages.get(url.rstrip("/")) or pages.get(url)

    return fetch


def main() -> int:
    totals = {"instagram": 0, "facebook": 0, "linkedin": 0, "official": 0,
              "forms": 0, "emails": 0}
    by_platform: dict[str, dict] = {}
    failures = 0

    print(f"{'ID':>2}  {'site':<12} {'maker':<14} IG FB LI OFF FORM MAIL  notes")
    print("-" * 92)
    for c in CASES:
        proj = P(**c)
        res = w.web_research(
            proj, None, fetch_fn=make_fetch(c), search_fn=make_search(c)
        )
        soc = res["discovered_socials"]
        ig = soc.get("instagram", "")
        fb = soc.get("facebook", "")
        li = soc.get("linkedin", "")
        emails = res["discovered_emails"]
        forms = res["discovered_forms"]

        # 検証：本人 SNS を採用し、運営公式 SNS を採用しない
        site = c["source_site"]
        h = c["handle"]
        ok_ig = ig == f"https://www.instagram.com/{h}/"
        ok_no_platform = f"instagram.com/{site}/" not in ig
        ok_fb = fb == f"https://www.facebook.com/{h}"
        ok_li = li == f"https://www.linkedin.com/company/{h}/"
        # メールは出典付き・運営除外（partnership@ のみ採用、platform は除外）
        ok_mail = any(e["email"] == f"partnership@{c['domain']}" for e in emails)
        ok_no_plat_mail = all(
            not e["email"].endswith(f"@{site}.com") for e in emails
        )

        if ok_ig:
            totals["instagram"] += 1
        if ok_fb:
            totals["facebook"] += 1
        if ok_li:
            totals["linkedin"] += 1
        if res["primary_contact_form_url"] or forms:
            totals["forms"] += 1
        if emails:
            totals["emails"] += 1
        # 公式サイトは maker_url があるので常に対象。発見=クロールできた
        if any("official" in p["type"] or c["domain"] in p["url"]
               for p in res["candidate_pages"]):
            totals["official"] += 1

        bp = by_platform.setdefault(site, {"n": 0, "ig": 0, "fb": 0, "li": 0,
                                            "mail": 0})
        bp["n"] += 1
        bp["ig"] += int(ok_ig)
        bp["fb"] += int(ok_fb)
        bp["li"] += int(ok_li)
        bp["mail"] += int(ok_mail)

        problems = []
        if not ok_ig:
            problems.append("IG未採用")
        if not ok_no_platform:
            problems.append("運営IG誤採用")
        if not ok_fb:
            problems.append("FB未採用")
        if not ok_li:
            problems.append("LI未採用")
        if not ok_mail:
            problems.append("営業メール未採用")
        if not ok_no_plat_mail:
            problems.append("運営メール誤採用")
        if problems:
            failures += 1

        print(
            f"{c['id']:>2}  {site:<12} {c['maker_name']:<14} "
            f"{'Y' if ok_ig else '-'}  {'Y' if ok_fb else '-'}  "
            f"{'Y' if ok_li else '-'}  {'Y' if (forms or res['primary_contact_form_url']) else '-'}   "
            f"{'Y' if (forms or res['primary_contact_form_url']) else '-'}    "
            f"{'Y' if emails else '-'}   {','.join(problems) or 'OK'}"
        )

    print("-" * 92)
    print("プラットフォーム別 (発見数 / 件数):")
    for site, bp in by_platform.items():
        print(
            f"  {site:<12} IG {bp['ig']}/{bp['n']}  FB {bp['fb']}/{bp['n']}  "
            f"LI {bp['li']}/{bp['n']}  営業メール {bp['mail']}/{bp['n']}"
        )
    print("\n合計（15 件中）:")
    print(f"  Instagram 発見数 : {totals['instagram']}")
    print(f"  Facebook  発見数 : {totals['facebook']}")
    print(f"  LinkedIn  発見数 : {totals['linkedin']}")
    print(f"  公式サイト発見数 : {totals['official']}")
    print(f"  問い合わせフォーム: {totals['forms']}")
    print(f"  メール発見数     : {totals['emails']}")
    print(f"\n{'ALL OK' if failures == 0 else f'{failures} case(s) had problems'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
