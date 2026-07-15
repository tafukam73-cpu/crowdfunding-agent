"""実案件 partial ベースライン：アクセス可能な公式サイトで現行メール抽出を実測する。

gold_candidates.json のアクセス可能な公式サイトを実際に取得し、現行の
contact_discovery_service.extract_emails が何を拾えるかを測る。ground truth の代替として、
サイト所有者が明示公開した `mailto:` アドレス（＝拾えて当然）を参照にし、
「mailto があるのに抽出器が取りこぼした」件数（＝実バグ）を可視化する。

合成 HTML ではなく実ページを取得する。ブロック/接続不可は「未取得理由」に計上する。

実行: docker exec cfagent-backend python tests/contact_intel_eval/baseline.py
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

from app.services import contact_discovery_service as cds  # noqa: E402

HERE = Path(__file__).resolve().parent
GOLD = HERE / "gold_candidates.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
PATHS = ["", "/contact", "/contact-us", "/about", "/about-us", "/pages/contact"]
_RAW_MAILTO = re.compile(r"mailto:([^\"'>?\s]+)", re.IGNORECASE)


def fetch(url: str) -> str | None:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=8,
                      follow_redirects=True)
        if r.status_code < 400 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:  # noqa: BLE001
        return None
    return None


def raw_mailtos(html: str, site_domain: str | None) -> set[str]:
    """所有者が明示した mailto アドレス（除外対象を除く）＝ground truth 代替。"""
    out = set()
    for m in _RAW_MAILTO.findall(html or ""):
        addr = m.split("?", 1)[0].strip().lower()
        if "@" not in addr:
            continue
        if cds.email_exclusion_reason(addr, site_domain):
            continue
        out.add(addr)
    return out


def run(target_field: str = "saved_official_sites") -> dict:
    cands = json.loads(GOLD.read_text(encoding="utf-8"))
    sites = []
    for c in cands:
        for u in c.get(target_field, [])[:1]:
            sites.append((c["project_id"], c["source_site"], u))

    fetched = blocked = 0
    sites_with_prod_email = 0
    total_prod = 0
    total_mailto_ref = 0
    missed_mailto = 0  # mailto があるのに抽出器が取りこぼした件数（実バグ）
    per_site = []
    for pid, site, base in sites:
        html_all = ""
        got = False
        host = urlparse(base).netloc
        for p in PATHS:
            u = urljoin(base if base.endswith("/") else base + "/", p.lstrip("/")) if p else base
            h = fetch(u)
            if h:
                html_all += "\n" + h
                got = True
        if not got:
            blocked += 1
            per_site.append({"project_id": pid, "site": site, "url": base,
                             "status": "unfetchable"})
            continue
        fetched += 1
        dom = host.replace("www.", "")
        prod = set(e.lower() for e in cds.extract_emails(html_all, None))
        ref = raw_mailtos(html_all, None)
        miss = ref - prod
        if prod:
            sites_with_prod_email += 1
        total_prod += len(prod)
        total_mailto_ref += len(ref)
        missed_mailto += len(miss)
        per_site.append({
            "project_id": pid, "site": site, "url": base, "status": "fetched",
            "prod_emails": sorted(prod), "mailto_ref": sorted(ref),
            "missed_by_extractor": sorted(miss),
        })

    summary = {
        "target_field": target_field,
        "sites_total": len(sites),
        "sites_fetched": fetched,
        "sites_unfetchable": blocked,
        "sites_with_extracted_email": sites_with_prod_email,
        "total_extracted_emails": total_prod,
        "total_mailto_reference": total_mailto_ref,
        "mailto_missed_by_extractor": missed_mailto,
    }
    return {"summary": summary, "per_site": per_site}


def main() -> int:
    result = run("saved_official_sites")
    out = HERE / "baseline_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    s = result["summary"]
    print("=== 実案件 partial ベースライン（公式サイト実取得）===")
    for k, v in s.items():
        print(f"  {k}: {v}")
    print("  取りこぼし例（mailto あるが抽出器が返さなかった）:")
    for r in result["per_site"]:
        if r.get("missed_by_extractor"):
            print(f"    project {r['project_id']} ({r['site']}): "
                  f"{r['missed_by_extractor']}  [{r['url']}]")
    print(f"  詳細: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
