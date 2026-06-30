"""検索API導入の効果検証（30 件、オフライン比較）。要件 9。

Kickstarter 10 / Indiegogo 10 / Ulule 10 の各案件で、
  (A) DuckDuckGo フォールバック相当（ノイズ主体・本人SNS/隠れ連絡先を返さない）
  (B) 検索API相当（本人の公式サイト・Contact・隠れ連絡先・SNSを正確に返す）
を同一の fetch_fn（実ページ）に対して実行し、メール/フォーム/Instagram/Facebook/
LinkedIn/営業可能チャネル数を比較する。

ねらい：検索精度の差がメール・SNS・チャネル発見にどう効くかを決定的に示す。
- 公式サイトが既知でも root から SNS へリンクが無い場合、本人 SNS は検索でしか
  見つからない（DDG では取れず API では取れる）。
- 一部案件はメールが WEB_KNOWN_PATHS 外の隠れページにしかなく、検索が surface
  しないと発見できない（email_hidden=True）。

注意：これはライブ検索ではなく、検索品質の差を再現した固定フィクスチャ比較。
実APIキーを設定した本番では Brave/SerpAPI/Tavily/Google CSE が (B) を担う。

実行（backend ディレクトリで）:
    python tests/verify_web_research_30.py
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
    def __init__(self, *, id, title, maker_name, domain, source_site, handle):
        self.id = id
        self.title = title
        self.maker_name = maker_name
        self.maker_url = f"https://{domain}"
        self.source_url = f"https://www.{source_site}.com/projects/{handle}"
        self.source_site = source_site
        self.description = f"{title} by {maker_name}."
        self.description_clean = self.description


_MAKERS = [
    ("Nuro Labs", "nurolabs.com", "nurolabs"),
    ("Lumio", "hellolumio.com", "lumio"),
    ("Peak Design", "peakdesign.com", "peakdesign"),
    ("Ridge", "ridge.com", "ridgewallet"),
    ("Anker", "anker.com", "anker"),
    ("Honey Flow", "honeyflow.com", "flowhive"),
    ("Pebble", "getpebble.com", "pebble"),
    ("Tile", "thetileapp.com", "tile"),
    ("Skydio", "skydio.com", "skydio"),
    ("Oura", "ouraring.com", "ouraring"),
]


def _build_cases() -> list[dict]:
    sites = ["kickstarter", "indiegogo", "ulule"]
    cases: list[dict] = []
    cid = 1
    for site in sites:
        for i, (maker, domain, handle) in enumerate(_MAKERS):
            cases.append(
                dict(
                    id=cid,
                    title=f"{maker} {['Pro','Go','Mini','Air','One','Plus','Max','Lite','X','S'][i]}",
                    maker_name=maker,
                    domain=f"{site[:3]}-{domain}",  # 案件ごとにユニーク化
                    source_site=site,
                    handle=f"{site[:2]}{handle}",
                    # 半数はメールが隠れページ（検索でしか surface しない）にしかない
                    email_hidden=(i % 2 == 0),
                )
            )
            cid += 1
    return cases


def make_fetch(c: dict):
    domain = c["domain"]
    hidden = c["email_hidden"]
    email = f"partnership@{domain}"
    plat = f"support@{c['source_site']}.com"
    hidden_path = f"https://{domain}/partners/japan"

    # root は /contact だけにリンク（SNS へはリンクしない＝SNSは検索でしか取れない）。
    # 本文には no-reply（除外対象）しか置かず、有効メールは漏らさない。
    root = (
        f'<html><body><a href="/contact">Contact</a>'
        f'<p>システム通知: no-reply@{domain}</p></body></html>'
    )
    if hidden:
        # /contact には営業メールが無い。隠れページにだけ存在。
        contact = (
            f'<html><body><p>Use the form below.</p>'
            f'<a href="mailto:{plat}">platform</a></body></html>'
        )
    else:
        contact = (
            f'<html><body><a href="mailto:{email}">partner</a>'
            f'<a href="mailto:{plat}">platform</a></body></html>'
        )
    hidden_page = f'<html><body><a href="mailto:{email}">JP partnerships</a></body></html>'

    pages = {
        f"https://{domain}": root,
        f"https://{domain}/contact": contact,
        hidden_path: hidden_page,
    }

    def fetch(url: str):
        return pages.get(url.rstrip("/")) or pages.get(url)

    return fetch


def make_ddg_search(c: dict):
    """DuckDuckGo フォールバック相当：ノイズ主体。本人 SNS/隠れ連絡先は返さない。"""
    site = c["source_site"]
    results = [
        {"url": f"https://www.instagram.com/{site}/", "title": site, "snippet": "platform"},
        {"url": "https://www.instagram.com/accounts/login/", "title": "Login", "snippet": ""},
        {"url": "https://www.facebook.com/sharer/sharer.php?u=x", "title": "Share", "snippet": ""},
        {"url": f"https://www.{site}.com/projects/{c['handle']}", "title": c["title"], "snippet": "back this"},
        {"url": "https://www.amazon.com/dp/B0XYZ", "title": "Amazon", "snippet": "buy"},
        {"url": "https://news.example.org/article", "title": "news", "snippet": "unrelated"},
    ]

    def search(query: str):
        return results

    search.provider = "duckduckgo"  # type: ignore[attr-defined]
    return search


def make_api_search(c: dict):
    """検索API相当：本人の公式サイト・Contact・隠れ連絡先・SNS を正確に返す。"""
    domain = c["domain"]
    h = c["handle"]
    maker = c["maker_name"]
    title = c["title"]
    results = [
        {"url": f"https://{domain}/", "title": f"{maker} – Official", "snippet": title},
        {"url": f"https://{domain}/contact", "title": f"Contact {maker}", "snippet": "get in touch"},
        {"url": f"https://www.instagram.com/{h}/", "title": f"{maker} (@{h}) • Instagram", "snippet": title},
        {"url": f"https://www.facebook.com/{h}", "title": f"{maker} | Facebook", "snippet": title},
        {"url": f"https://www.linkedin.com/company/{h}/", "title": f"{maker} | LinkedIn", "snippet": maker},
        # 運営公式 SNS も混ぜる（除外されるべき）
        {"url": f"https://www.instagram.com/{c['source_site']}/", "title": "platform", "snippet": ""},
    ]
    if c["email_hidden"]:
        results.insert(2, {
            "url": f"https://{domain}/partners/japan",
            "title": f"{maker} – Partnerships (Japan)",
            "snippet": "distribution partnership inquiries",
        })

    def search(query: str):
        return results

    search.provider = "search_api"  # type: ignore[attr-defined]
    return search


def _metrics(res: dict) -> dict:
    soc = res["discovered_socials"]
    emails = res["discovered_emails"]
    forms = res["discovered_forms"] or ([] if not res["primary_contact_form_url"] else [res["primary_contact_form_url"]])
    channels = (1 if emails else 0) + (1 if forms else 0) + len(soc)
    return {
        "email": 1 if emails else 0,
        "form": 1 if forms else 0,
        "ig": 1 if soc.get("instagram") else 0,
        "fb": 1 if soc.get("facebook") else 0,
        "li": 1 if soc.get("linkedin") else 0,
        "channels": channels,
        # 運営メール誤採用チェック
        "bad_platform_email": any(
            e["email"].endswith(f"@{res.get('_site','')}.com") for e in emails
        ),
    }


def main() -> int:
    cases = _build_cases()
    agg = {"ddg": {}, "api": {}}
    keys = ["email", "form", "ig", "fb", "li", "channels"]
    for mode in agg:
        for k in keys:
            agg[mode][k] = 0

    per_site = {}
    bad_platform = 0

    for c in cases:
        proj = P(
            id=c["id"], title=c["title"], maker_name=c["maker_name"],
            domain=c["domain"], source_site=c["source_site"], handle=c["handle"],
        )
        fetch = make_fetch(c)
        res_ddg = w.web_research(proj, None, fetch_fn=fetch, search_fn=make_ddg_search(c))
        res_api = w.web_research(proj, None, fetch_fn=fetch, search_fn=make_api_search(c))
        res_ddg["_site"] = c["source_site"]
        res_api["_site"] = c["source_site"]
        m_ddg = _metrics(res_ddg)
        m_api = _metrics(res_api)
        if m_api["bad_platform_email"] or m_ddg["bad_platform_email"]:
            bad_platform += 1

        for k in keys:
            agg["ddg"][k] += m_ddg[k]
            agg["api"][k] += m_api[k]

        ps = per_site.setdefault(c["source_site"], {"ddg": {k: 0 for k in keys},
                                                    "api": {k: 0 for k in keys}})
        for k in keys:
            ps["ddg"][k] += m_ddg[k]
            ps["api"][k] += m_api[k]

    labels = {
        "email": "メール", "form": "フォーム", "ig": "Instagram",
        "fb": "Facebook", "li": "LinkedIn", "channels": "営業可能チャネル数",
    }
    n = len(cases)
    print(f"検証件数: {n} 件（Kickstarter 10 / Indiegogo 10 / Ulule 10）\n")
    print("プラットフォーム別（DDGフォールバック → 検索API）:")
    for site, ps in per_site.items():
        print(f"  [{site}]")
        for k in keys:
            print(f"    {labels[k]:<16}: {ps['ddg'][k]:>3} → {ps['api'][k]:>3}")
    print("\n=== 合計（30 件中の発見数）===")
    print(f"  {'指標':<18}{'DDGフォールバック':>16}{'検索API':>10}{'改善':>8}")
    for k in keys:
        d, a = agg["ddg"][k], agg["api"][k]
        print(f"  {labels[k]:<18}{d:>16}{a:>10}{('+' + str(a - d)):>8}")
    print(f"\n運営メール誤採用: {bad_platform} 件（0 が正常）")
    ok = (
        agg["api"]["ig"] > agg["ddg"]["ig"]
        and agg["api"]["channels"] > agg["ddg"]["channels"]
        and agg["api"]["email"] >= agg["ddg"]["email"]
        and bad_platform == 0
    )
    print("\n" + ("検索API導入で発見率が向上（OK）" if ok else "期待した改善が出ていない"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
