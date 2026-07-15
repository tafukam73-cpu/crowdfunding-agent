"""既存 DB の実データから「gold 候補一覧」を自動抽出する（ユーザー手作業に依存しない）。

projects / contact_discoveries / contact_people / company_researches に既に保存されている
公開メール・公式サイト・問い合わせフォーム・SNS・担当者と、その根拠 URL を集約し、
gold 候補として出力する。**推測値は作らない**（保存済みの実データのみ）。

出力: tests/contact_intel_eval/gold_candidates.json（測定ハーネスの入力雛形）
実行: docker exec cfagent-backend python tests/contact_intel_eval/build_gold_candidates.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("APP_COMPONENT", "cfagent-eval")

from app.db.session import SessionLocal  # noqa: E402
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_person import ContactPerson  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import contact_discovery_service as cds  # noqa: E402

OUT = Path(__file__).resolve().parent / "gold_candidates.json"


def _emails_from(v) -> list[str]:
    """list[str] / list[dict{email}] / str のいずれからでもメール文字列を取り出す。"""
    out: list[str] = []
    if not v:
        return out
    if isinstance(v, str):
        return [v] if "@" in v else []
    if isinstance(v, dict):
        v = [v]
    for item in v if isinstance(v, list) else []:
        if isinstance(item, str) and "@" in item:
            out.append(item)
        elif isinstance(item, dict):
            e = item.get("email") or item.get("value")
            if e and "@" in str(e):
                out.append(str(e))
    return out


def _urls_from(v) -> list[str]:
    out: list[str] = []
    if not v:
        return out
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        for x in v.values():
            if isinstance(x, str) and x.startswith("http"):
                out.append(x)
        return out
    for item in v if isinstance(v, list) else []:
        if isinstance(item, str) and item.startswith("http"):
            out.append(item)
        elif isinstance(item, dict):
            u = item.get("url") or item.get("href") or item.get("value")
            if u and str(u).startswith("http"):
                out.append(str(u))
    return out


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        k = str(x).strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(str(x).strip())
    return out


def collect(db, project: Project) -> dict:
    row = cds.get_latest(db, project.id)
    people = list(
        db.query(ContactPerson).filter(ContactPerson.project_id == project.id)
    )
    emails: list[str] = []
    forms: list[str] = []
    officials: list[str] = []
    socials: list[str] = []
    evidence: list[str] = []
    if row is not None:
        for f in ("primary_email", "web_primary_email", "v2_primary_email",
                  "ai_primary_email"):
            emails += _emails_from(getattr(row, f, None))
        for f in ("discovered_emails", "web_discovered_emails", "v2_emails",
                  "ai_candidate_emails", "doc_reader_emails", "search_agent_emails",
                  "recursive_emails"):
            emails += _emails_from(getattr(row, f, None))
        for f in ("primary_contact_form_url", "web_primary_contact_form_url",
                  "ai_contact_form_url", "discovered_forms", "web_discovered_forms",
                  "v2_forms", "doc_reader_contact_forms", "search_agent_contact_forms",
                  "recursive_forms"):
            forms += _urls_from(getattr(row, f, None))
        for f in ("official_site_url", "v2_official_site_url",
                  "doc_reader_official_site_url", "search_agent_official_site_url"):
            v = getattr(row, f, None)
            if v and cds.official_site_or_none(v):
                officials.append(v)
        for f in ("instagram_url", "facebook_url", "twitter_url", "linkedin_url",
                  "youtube_url", "v2_linkedin_company_url", "v2_linkedin_person_url",
                  "ai_instagram_url", "ai_facebook_url", "ai_linkedin_url",
                  "discovered_socials", "web_discovered_socials", "v2_socials",
                  "doc_reader_socials", "search_agent_socials", "recursive_socials"):
            socials += _urls_from(getattr(row, f, None))
        for f in ("v2_primary_source_url", "ai_sources", "doc_reader_sources",
                  "searched_urls", "web_searched_urls", "search_agent_searched_urls"):
            evidence += _urls_from(getattr(row, f, None))

    people_out = [{
        "name": p.name, "title": p.title, "company": None,
        "source_url": p.source_url, "confidence": p.confidence,
        "email": p.email,
    } for p in people if p.name]

    return {
        "project_id": project.id,
        "source_site": project.source_site,
        "project_url": project.source_url,
        "maker_name": project.maker_name,
        "browser_check_url": project.maker_url or project.source_url,
        "saved_emails": _uniq(emails),
        "saved_official_sites": _uniq(officials),
        "saved_forms": _uniq(forms),
        "saved_socials": _uniq(socials),
        "saved_people": people_out,
        "evidence_urls": _uniq(evidence)[:20],
        # gold set 用（後で人手 or 自動確認で埋める）。推測値は入れない。
        "expected_emails": [],
        "expected_forms": [],
        "expected_official_sites": [],
        "expected_socials": [],
        "expected_people": [],
        "manually_verified": False,
        "verification_note": "",
    }


def main() -> int:
    db = SessionLocal()
    projects = list(db.query(Project).order_by(Project.id))
    cands = []
    for p in projects:
        c = collect(db, p)
        signal = (len(c["saved_emails"]) + len(c["saved_official_sites"])
                  + len(c["saved_forms"]) + len(c["saved_socials"])
                  + len(c["saved_people"]))
        if signal > 0:
            c["_signal"] = signal
            c["_has_evidence"] = bool(c["evidence_urls"])
            # 手動確認が必要な項目（保存されていない/根拠が弱い）
            need = []
            if not c["saved_emails"]:
                need.append("email")
            if not c["saved_people"]:
                need.append("person")
            if not c["saved_official_sites"]:
                need.append("official_site")
            c["manual_check_needed"] = need
            c["reason"] = (
                f"保存済みシグナル {signal} 件"
                + ("（根拠URLあり）" if c["_has_evidence"] else "（根拠URL要確認）")
            )
            cands.append(c)
    db.close()

    cands.sort(key=lambda c: (c["_has_evidence"], c["_signal"]), reverse=True)

    OUT.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 初期レポート（Phase 10）---
    def cnt(pred):
        return sum(1 for c in cands if pred(c))

    by_site: dict[str, int] = {}
    for c in cands:
        by_site[c["source_site"]] = by_site.get(c["source_site"], 0) + 1

    print("=== gold 候補 初期レポート（実 DB 由来・推測なし）===")
    print(f"1. gold 候補件数            : {len(cands)}")
    print(f"2. うち根拠URLあり          : {cnt(lambda c: c['_has_evidence'])}")
    print(f"5. 保存済みメールあり       : {cnt(lambda c: c['saved_emails'])}")
    print(f"6. 保存済みフォームあり     : {cnt(lambda c: c['saved_forms'])}")
    print(f"   保存済み公式サイトあり   : {cnt(lambda c: c['saved_official_sites'])}")
    print(f"   保存済みSNSあり          : {cnt(lambda c: c['saved_socials'])}")
    print(f"7. 保存済み担当者あり       : {cnt(lambda c: c['saved_people'])}")
    print(f"   サイト別                 : {by_site}")
    print(f"   出力                     : {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
