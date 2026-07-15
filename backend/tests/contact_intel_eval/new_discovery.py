"""実案件での「新規発見」測定（preexisting vs newly_discovered を厳密に分離）。

各案件について、実行前スナップショット（DB 保存済み）を取り、確定/正当な公式サイトを
実取得して現行抽出器でメール/フォーム/SNS を抽出し、**保存済みに無いものだけ** を
newly_discovered として数える。保存済み値は成果に数えない。bot ブロックは blocked として
正直に記録する。合成 HTML は使わない（実 URL を取得）。

これは worker のフル pipeline ではなく「公式サイト到達＋抽出」の部分測定（時間内に実データで
新規発見を可視化する目的）。プラットフォーム運営メールは extract_emails が除外する。

実行: docker exec cfagent-backend python tests/contact_intel_eval/new_discovery.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_COMPONENT", "cfagent-eval")

from app.db.session import SessionLocal  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import contact_discovery_service as cds  # noqa: E402

HERE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
PATHS = ["", "/contact", "/contact-us", "/about", "/about-us", "/support",
         "/pages/contact", "/문의", "/聯絡我們", "/company"]
_FORM_RE = re.compile(r"<form[^>]*>", re.IGNORECASE)
_CONTACT_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:contact|inquiry|문의|聯絡|联系|お問)[^"\']*)["\']',
    re.IGNORECASE)


def _urlset(v) -> set[str]:
    out = set()
    if not v:
        return out
    if isinstance(v, str):
        return {v.lower()} if v.startswith("http") else set()
    if isinstance(v, dict):
        for x in v.values():
            if isinstance(x, str) and x.startswith("http"):
                out.add(x.lower())
        return out
    for item in v if isinstance(v, list) else []:
        u = item.get("url") if isinstance(item, dict) else item
        if u and str(u).startswith("http"):
            out.add(str(u).lower())
    return out


def _snapshot(row) -> dict:
    """実行前スナップショット（DB 保存済み）。emails/forms/socials を分けて返す。"""
    emails: set[str] = set()
    forms: set[str] = set()
    socials: set[str] = set()
    if row is None:
        return {"emails": emails, "forms": forms, "socials": socials}
    for f in ("primary_email", "web_primary_email", "v2_primary_email",
              "ai_primary_email"):
        v = getattr(row, f, None)
        if v:
            emails.add(str(v).lower())
    for f in ("discovered_emails", "web_discovered_emails", "v2_emails",
              "recursive_emails"):
        for item in (getattr(row, f, None) or []):
            e = item.get("email") if isinstance(item, dict) else item
            if e:
                emails.add(str(e).lower())
    for f in ("primary_contact_form_url", "web_primary_contact_form_url",
              "discovered_forms", "web_discovered_forms", "v2_forms",
              "recursive_forms"):
        forms |= _urlset(getattr(row, f, None))
    for f in ("instagram_url", "facebook_url", "linkedin_url", "youtube_url",
              "twitter_url", "discovered_socials", "web_discovered_socials",
              "v2_socials", "recursive_socials"):
        socials |= _urlset(getattr(row, f, None))
    return {"emails": emails, "forms": forms, "socials": socials}


def fetch_all(base: str) -> tuple[str, str | None]:
    """(combined_html, block_status)。全ページ取得不可なら blocked。"""
    html = ""
    got = False
    blocked = False
    for p in PATHS:
        u = urljoin(base if base.endswith("/") else base + "/", p.lstrip("/")) if p else base
        try:
            r = httpx.get(u, headers={"User-Agent": UA}, timeout=8,
                          follow_redirects=True)
            if r.status_code in (401, 403, 429):
                blocked = True
            elif r.status_code < 400 and "text/html" in r.headers.get("content-type", ""):
                html += "\n" + r.text
                got = True
        except Exception:  # noqa: BLE001
            pass
    if not got:
        return "", ("blocked" if blocked else "unfetchable")
    return html, None


def main() -> int:
    db = SessionLocal()
    projects = list(db.query(Project).order_by(Project.id))
    results = []
    tallies = {"cases": 0, "new_email_cases": 0, "new_form_cases": 0,
               "new_social_cases": 0, "new_channel_cases": 0,
               "blocked": 0, "unfetchable": 0, "platform_email_false": 0}
    for p in projects:
        row = cds.get_latest(db, p.id)
        if row is None:
            continue
        official = cds.official_site_or_none(getattr(row, "official_site_url", None)) or \
            cds.official_site_or_none(getattr(row, "v2_official_site_url", None))
        if not official:
            continue  # 正当な公式サイトが無い案件は本測定の対象外（別途要探索）
        tallies["cases"] += 1
        pre = _snapshot(row)
        html, block = fetch_all(official)
        if block:
            tallies[block] += 1
            results.append({"project_id": p.id, "site": p.source_site,
                            "official": official, "status": block})
            continue
        site_domain = cds.source_site_email_domain(p.source_site)
        found_emails = {e.lower() for e in cds.extract_emails(html, site_domain)}
        for e in list(found_emails):
            if cds.email_exclusion_reason(e, site_domain):
                tallies["platform_email_false"] += 1
                found_emails.discard(e)
        # フォーム候補 URL（同一オリジンの contact 系リンク or <form>）
        found_forms: set[str] = set()
        for href in _CONTACT_LINK_RE.findall(html):
            absu = urljoin(official, href).split("#", 1)[0].lower()
            if absu.startswith("http"):
                found_forms.add(absu)
        if _FORM_RE.search(html) and not found_forms:
            found_forms.add(official.rstrip("/").lower() + "/#form")
        found_socials = {u.lower() for u in cds.extract_socials(html, official).values()}

        # **preexisting を厳密に差し引いた newly_discovered**
        new_emails = sorted(found_emails - pre["emails"])
        new_forms = sorted(found_forms - pre["forms"])
        new_socials = sorted(found_socials - pre["socials"])

        got_new = False
        if new_emails:
            tallies["new_email_cases"] += 1
            got_new = True
        if new_forms:
            tallies["new_form_cases"] += 1
            got_new = True
        if new_socials:
            tallies["new_social_cases"] += 1
            got_new = True
        if got_new:
            tallies["new_channel_cases"] += 1
        results.append({
            "project_id": p.id, "site": p.source_site, "official": official,
            "status": "fetched",
            "preexisting": {"emails": sorted(pre["emails"]),
                            "forms": len(pre["forms"]), "socials": len(pre["socials"])},
            "newly_discovered": {"emails": new_emails, "forms": new_forms,
                                 "socials": new_socials},
        })
    db.close()

    out = HERE / "new_discovery_result.json"
    out.write_text(json.dumps({"tallies": tallies, "per_case": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== 実案件 新規発見測定（正当な公式サイトを実取得）===")
    for k, v in tallies.items():
        print(f"  {k}: {v}")
    print("  新規発見（preexisting 差し引き後）:")
    for r in results:
        nd = r.get("newly_discovered") or {}
        if nd.get("emails") or nd.get("forms") or nd.get("socials"):
            print(f"    p{r['project_id']} [{r['site']}] emails={nd.get('emails')} "
                  f"forms={len(nd.get('forms') or [])} socials={len(nd.get('socials') or [])} "
                  f"({r['official']})")
    print(f"  詳細: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
