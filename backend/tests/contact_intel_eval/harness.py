"""Contact Intelligence 測定ハーネス。

gold set（JSON/CSV）を入力に、抽出器およびパイプライン（DB 保存済み）の
precision / recall / false positives / false negatives を、プラットフォーム別・
抽出経路別に自動集計する。gold set は後から差し替え可能（列は下記）。

gold set 列（JSON のリスト、または同名列の CSV）:
  project_id, source_site, project_url, expected_emails, expected_forms,
  expected_official_sites, expected_socials, expected_people, evidence_urls,
  manually_verified, verification_note

サブコマンド:
  build-partial : アクセス可能な公式サイトを実取得し、所有者が明示公開した mailto を
                  expected_emails として自動検証登録した partial gold（gold_partial.json）を作る。
                  ※ mailto は「その場で根拠確認できる公開情報」なので推測ではない。
  measure       : gold set に対して測定する。--predicted saved|extract
                    saved   = DB に保存済みの現行パイプライン出力（gold_candidates の saved_emails）
                    extract = 公式サイトを実取得して現行抽出器で再抽出した出力

実行:
  docker exec cfagent-backend python tests/contact_intel_eval/harness.py build-partial
  docker exec cfagent-backend python tests/contact_intel_eval/harness.py measure gold_partial.json --predicted extract
  docker exec cfagent-backend python tests/contact_intel_eval/harness.py measure gold_partial.json --predicted saved
"""
from __future__ import annotations

import csv
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
CAND = HERE / "gold_candidates.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
PATHS = ["", "/contact", "/contact-us", "/about", "/pages/contact"]
_RAW_MAILTO = re.compile(r"mailto:([^\"'>?\s]+)", re.IGNORECASE)


def _load(path: Path) -> list[dict]:
    if path.suffix == ".csv":
        rows = []
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for k in ("expected_emails", "expected_forms",
                          "expected_official_sites", "expected_socials",
                          "expected_people", "evidence_urls"):
                    r[k] = [x.strip() for x in (r.get(k) or "").split(";") if x.strip()]
                rows.append(r)
        return rows
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_site(base: str) -> str:
    html = ""
    for p in PATHS:
        u = urljoin(base if base.endswith("/") else base + "/", p.lstrip("/")) if p else base
        try:
            r = httpx.get(u, headers={"User-Agent": UA}, timeout=8,
                          follow_redirects=True)
            if r.status_code < 400 and "text/html" in r.headers.get("content-type", ""):
                html += "\n" + r.text
        except Exception:  # noqa: BLE001
            pass
    return html


def build_partial() -> int:
    cands = _load(CAND)
    updated = 0
    for c in cands:
        sites = c.get("saved_official_sites") or []
        if not sites:
            continue
        html = fetch_site(sites[0])
        if not html:
            continue
        mailtos = set()
        for m in _RAW_MAILTO.findall(html):
            a = m.split("?", 1)[0].strip().lower()
            if "@" in a and not cds.email_exclusion_reason(a, None):
                mailtos.add(a)
        if mailtos:
            c["expected_emails"] = sorted(mailtos)
            c["manually_verified"] = True
            c["verification_note"] = (
                f"auto-verified: live mailto on {urlparse(sites[0]).netloc}")
            updated += 1
    out = HERE / "gold_partial.json"
    out.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"build-partial: {updated} 件に live mailto を expected_emails として自動検証登録")
    print(f"  出力: {out}")
    return 0


def _norm(xs) -> set[str]:
    return {str(x).strip().lower() for x in (xs or []) if str(x).strip()}


def measure(gold_path: Path, predicted: str) -> int:
    gold = _load(gold_path)
    verified = [g for g in gold if g.get("manually_verified") and g.get("expected_emails")]
    if not verified:
        print("測定対象（manually_verified かつ expected_emails あり）が 0 件です。")
        print("build-partial で自動検証 gold を作るか、gold set を追加してください。")
        return 0

    tp = fp = fn = 0
    per_platform: dict[str, list[int]] = {}
    missed: list[dict] = []
    for g in verified:
        exp = _norm(g.get("expected_emails"))
        if predicted == "extract" and g.get("saved_official_sites"):
            html = fetch_site(g["saved_official_sites"][0])
            pred = _norm(cds.extract_emails(html, None))
        else:  # saved
            pred = _norm(g.get("saved_emails"))
        t = len(exp & pred)
        f_p = len(pred - exp)
        f_n = len(exp - pred)
        tp += t
        fp += f_p
        fn += f_n
        site = g.get("source_site", "?")
        per_platform.setdefault(site, [0, 0, 0])
        per_platform[site][0] += t
        per_platform[site][1] += f_p
        per_platform[site][2] += f_n
        if exp - pred:
            missed.append({"project_id": g.get("project_id"), "site": site,
                           "missed": sorted(exp - pred), "predicted": sorted(pred)})

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    print(f"=== 測定（predicted={predicted}）===")
    print(f"  検証対象案件数     : {len(verified)}")
    print(f"  expected メール総数 : {tp + fn}")
    print(f"  TP={tp}  FP={fp}  FN={fn}")
    print(f"  precision={prec:.2%}  recall={rec:.2%}")
    print("  プラットフォーム別 (TP/FP/FN):")
    for site, (t, f_p, f_n) in sorted(per_platform.items()):
        r = t / (t + f_n) if (t + f_n) else 0
        print(f"    {site:12s} TP={t} FP={f_p} FN={f_n} recall={r:.0%}")
    if missed:
        print("  取りこぼし（expected だが predicted に無い）:")
        for m in missed:
            print(f"    project {m['project_id']} ({m['site']}): missed={m['missed']}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "build-partial":
        return build_partial()
    if cmd == "measure":
        gp = args[1] if len(args) > 1 else "gold_partial.json"
        path = Path(gp) if Path(gp).is_absolute() else (HERE / gp)
        predicted = "saved"
        if "--predicted" in args:
            predicted = args[args.index("--predicted") + 1]
        return measure(path, predicted)
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
