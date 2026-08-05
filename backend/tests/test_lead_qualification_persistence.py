"""Lead Qualification Engine の永続化（PR-2）のオフライン検証。

ネットワーク不要・SQLite。pytest には依存しない（CLAUDE.md §7）。

検証の重点:
  - gather_signals は **読み取り専用**（DB を更新せず、外部 HTTP も行わない）
  - lead_qualifications は **append-only**（既存履歴を書き換えない）
  - projects は最新判定キャッシュ 2 列だけが更新され、archived_at には触れない
  - evidence_count は QualificationResult の値をそのまま保存（再計算しない）
  - internal_db 契約（db:// は method=db_state かつ source_kind=internal_db のみ）

実行: docker compose exec -T backend python tests/test_lead_qualification_persistence.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "lqe_persistence_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_person import ContactPerson  # noqa: E402
from app.models.japan_sales_check import JapanSalesCheck  # noqa: E402
from app.models.lead_qualification import LeadQualification  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import lead_qualification_service as lq  # noqa: E402

Base.metadata.create_all(engine)

_passed = _failed = 0
_seq = [0]


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_project(db, **over) -> Project:
    _seq[0] += 1
    n = _seq[0]
    data = {
        "title": "Compact Stainless Steel Water Bottle",
        "source_site": "kickstarter",
        "source_url": f"https://www.kickstarter.com/projects/acme/bottle-{n}",
        "category": "kitchen",
        "description": (
            "A rechargeable stainless steel bottle with a companion app. "
            "Waterproof body for daily use."
        ),
        "description_clean": (
            "A rechargeable stainless steel bottle with a companion app. "
            "Waterproof body for daily use."
        ),
        "maker_name": "Acme Studio",
        "maker_url": "https://acme-bottle.example",
        "currency": "USD",
    }
    data.update(over)
    p = Project(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def add_discovery(db, project, **over) -> ContactDiscovery:
    data = {
        "project_id": project.id,
        "v2_official_site_url": "https://acme-bottle.example",
        "v2_official_site_source": "project_website",
        "v2_primary_source_url": "https://acme-bottle.example/contact",
        "v2_researched_at": _now() - timedelta(days=10),
        "v2_emails": [{
            "email": "hello@acme-bottle.example",
            "source_url": "https://acme-bottle.example/contact",
            "email_owner": "maker_official",
            "confidence_level": "high",
        }],
        "v2_primary_email": "hello@acme-bottle.example",
    }
    data.update(over)
    row = ContactDiscovery(**data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_japan_check(db, project, *, channels, status="completed") -> JapanSalesCheck:
    row = JapanSalesCheck(
        project_id=project.id, status=status, model="test", channels=channels
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
#  gather_signals
# --------------------------------------------------------------------------- #
def test_gather_signals_basic_fields():
    print("test_gather_signals_basic_fields")
    db = SessionLocal()
    try:
        p = make_project(db)
        sig = lq.gather_signals(db, p)
        check("project_id を渡す", sig["project_id"] == p.id)
        check("campaign_url を url_state から取得",
              sig["campaign_url"] == p.source_url)
        check("campaign_url_missing_reason キーがある",
              "campaign_url_missing_reason" in sig)
        check("maker_name を渡す", sig["maker_name"] == "Acme Studio")
        check("category を渡す", sig["category"] == "kitchen")
        check("source_site を渡す", sig["source_site"] == "kickstarter")
        check("japanese_summary を既存関数で生成", bool(sig["japanese_summary"]))
        check("regulatory_checks を保持", "items" in (sig["regulatory_checks"] or {}))
        check("description は clean を優先", "rechargeable" in (sig["description"] or ""))

        nourl = make_project(db, source_url=None)
        sig2 = lq.gather_signals(db, nourl)
        check("campaign_url が無ければ None", sig2["campaign_url"] is None)
        check("欠落理由を保持", sig2["campaign_url_missing_reason"] is not None)
    finally:
        db.close()


def test_gather_signals_is_read_only():
    print("test_gather_signals_is_read_only")
    db = SessionLocal()
    try:
        p = make_project(db)
        add_discovery(db, p)
        before_lq = db.query(LeadQualification).count()
        before_proj = (p.lead_qualification_decision, p.lead_qualification_at)
        before_disc = db.query(ContactDiscovery).count()

        lq.gather_signals(db, p)

        check("新規オブジェクトを追加しない", len(db.new) == 0)
        check("既存オブジェクトを変更しない", len(db.dirty) == 0)
        check("削除しない", len(db.deleted) == 0)
        check("lead_qualifications を増やさない",
              db.query(LeadQualification).count() == before_lq)
        check("contact_discoveries を増やさない",
              db.query(ContactDiscovery).count() == before_disc)
        check("projects のキャッシュ列を書き換えない",
              (p.lead_qualification_decision, p.lead_qualification_at) == before_proj)
    finally:
        db.close()


def test_gather_signals_no_network():
    print("test_gather_signals_no_network")

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    db = SessionLocal()
    orig = (socket.socket.connect, socket.socket.connect_ex,
            urllib.request.urlopen, socket.getaddrinfo)
    try:
        p = make_project(db)
        add_discovery(db, p)
        add_japan_check(db, p, channels=[
            {"channel": "amazon", "status": "not_found", "label": "Amazon.co.jp",
             "search_url": "https://www.amazon.co.jp/s?k=acme"}])
        socket.socket.connect = boom
        socket.socket.connect_ex = boom
        urllib.request.urlopen = boom
        socket.getaddrinfo = boom
        try:
            lq.gather_signals(db, p)
            ok = True
        except AssertionError:
            ok = False
        finally:
            (socket.socket.connect, socket.socket.connect_ex,
             urllib.request.urlopen, socket.getaddrinfo) = orig
        check("gather_signals はネットワークに触れない", ok)
    finally:
        db.close()


def test_gather_signals_official_site_evidence():
    print("test_gather_signals_official_site_evidence")
    db = SessionLocal()
    try:
        p = make_project(db)
        add_discovery(db, p)
        site = lq.gather_signals(db, p)["official_site"]
        check("official_site.url を保持", site["url"] == "https://acme-bottle.example")
        check("判定元 source を保持", site["source"] == "project_website")
        check("取得元 URL を保持",
              site["source_url"] == "https://acme-bottle.example/contact")
        check("確認日時を保持", site["checked_at"] is not None)
        check("取得元と日時が揃えば verified", site["verified"] is True)

        p2 = make_project(db)
        add_discovery(db, p2, v2_official_site_source=None, v2_researched_at=None)
        site2 = lq.gather_signals(db, p2)["official_site"]
        check("URL があっても判定情報が無ければ verified にしない",
              site2["verified"] is False)

        p3 = make_project(db)
        check("探索結果が無ければ official_site は空",
              lq.gather_signals(db, p3)["official_site"] == {})
    finally:
        db.close()


def test_gather_signals_business_emails():
    print("test_gather_signals_business_emails")
    db = SessionLocal()
    try:
        p = make_project(db)
        add_discovery(db, p)
        emails = lq.gather_signals(db, p)["business_emails"]
        check("メールを 1 件以上渡す", len(emails) >= 1)
        first = emails[0]
        check("source_url を保持",
              first["source_url"] == "https://acme-bottle.example/contact")
        check("確認日時を保持", first["checked_at"] is not None)
        check("保存済みの役割情報を保持", first["role"] == "maker_official")
        check("保存済みの信頼度ラベルを保持", first["confidence_label"] == "high")

        p2 = make_project(db)
        add_discovery(db, p2, v2_emails=[{"email": "x@nosource.example"}],
                      v2_primary_email=None, v2_primary_source_url=None)
        e2 = lq.gather_signals(db, p2)["business_emails"]
        check("取得元不明のメールも渡すが source_url は None のまま",
              e2 and e2[0]["source_url"] is None)
        r2 = lq.qualify(lq.gather_signals(db, p2), lq.STAGE_PRE_RESEARCH)
        check("取得元不明のメールは positive_fact に昇格しない",
              "business_contact_found" not in {f.key for f in r2.positive_facts})
    finally:
        db.close()


def test_gather_signals_decision_makers():
    print("test_gather_signals_decision_makers")
    db = SessionLocal()
    try:
        p = make_project(db)
        db.add(ContactPerson(project_id=p.id, name="Jane Doe",
                             title="Head of Business Development",
                             source_url="https://acme-bottle.example/team"))
        db.add(ContactPerson(project_id=p.id, name="No Title",
                             source_url="https://acme-bottle.example/team"))
        db.add(ContactPerson(project_id=p.id, name="No Source", title="CEO"))
        db.add(ContactPerson(project_id=p.id, title="Anonymous CEO",
                             source_url="https://acme-bottle.example/team"))
        db.commit()

        people = lq.gather_signals(db, p)["decision_makers"]
        check("氏名＋役職＋source_url が揃うものだけ渡す", len(people) == 1)
        check("採用されたのは Jane Doe", people[0]["name"] == "Jane Doe")
        check("役職を保持", people[0]["title"] == "Head of Business Development")
        check("取得元 URL を保持",
              people[0]["source_url"] == "https://acme-bottle.example/team")
        check("確認日時を保持", people[0]["checked_at"] is not None)
    finally:
        db.close()


def test_gather_signals_japan_sales_distinction():
    print("test_gather_signals_japan_sales_distinction")
    db = SessionLocal()
    try:
        sold = make_project(db)
        add_japan_check(db, sold, channels=[
            {"channel": "amazon", "status": "found", "label": "Amazon.co.jp",
             "search_url": "https://www.amazon.co.jp/dp/B0TEST"}])
        s = lq.gather_signals(db, sold)["japan_sales"]
        check("sold_in_japan を区別", s["result"] == "sold_in_japan")
        check("source_urls を保持", len(s["source_urls"]) >= 1)
        check("channels 明細を保持", len(s["channels"]) == 1)

        nf = make_project(db)
        add_japan_check(db, nf, channels=[
            {"channel": "amazon", "status": "not_found",
             "search_url": "https://www.amazon.co.jp/s?k=a"},
            {"channel": "rakuten", "status": "not_found",
             "search_url": "https://search.rakuten.co.jp/search/mall/a"},
            {"channel": "distributor", "status": "not_found",
             "search_url": "https://www.google.com/search?q=a"}])
        n = lq.gather_signals(db, nf)["japan_sales"]
        check("not_found_in_japan を区別", n["result"] == "not_found_in_japan")
        rn = lq.qualify(lq.gather_signals(db, nf), lq.STAGE_PRE_RESEARCH)
        f = [x for x in rn.findings if x.code == "F"][0]
        check("not_found は blocker にしない（未販売の証明ではない）",
              f.severity != "blocker" and f.verdict == "no_hit")

        inc = make_project(db)
        add_japan_check(db, inc, channels=[{"channel": "amazon", "status": "unknown"}])
        i = lq.gather_signals(db, inc)["japan_sales"]
        check("inconclusive を区別", i["result"] == "inconclusive")

        none_p = make_project(db)
        check("未実施は not_checked",
              lq.gather_signals(db, none_p)["japan_sales"]["status"] == "not_checked")
    finally:
        db.close()


def test_gather_signals_creator_domain():
    print("test_gather_signals_creator_domain")
    db = SessionLocal()
    try:
        p = make_project(db)
        add_discovery(db, p, v2_official_site_url="https://www.amazon.co.jp/stores/acme")
        cd = lq.gather_signals(db, p)["creator_domain"]
        check("classify_domain の分類を保持", cd["ownership_class"] == "retailer")
        check("判定元 URL を保持", cd["url"].startswith("https://www.amazon.co.jp"))
        check("確認日時を保持", cd["checked_at"] is not None)
        check("method=classify_domain", cd["method"] == "classify_domain")

        r = lq.qualify(lq.gather_signals(db, p), lq.STAGE_PRE_OUTREACH)
        check("小売分類は pre_outreach で D が blocker", "D" in r.blocker_codes)

        p2 = make_project(db, maker_url=None)
        check("判定材料が無ければ creator_domain は空",
              lq.gather_signals(db, p2)["creator_domain"] == {})
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  run（永続化）
# --------------------------------------------------------------------------- #
def test_run_appends_history():
    print("test_run_appends_history")
    db = SessionLocal()
    try:
        p = make_project(db)
        row = lq.run(db, p, lq.STAGE_PRE_RESEARCH)
        check("履歴が 1 行増える",
              db.query(LeadQualification).filter_by(project_id=p.id).count() == 1)
        check("project_id が入る", row.project_id == p.id)
        check("stage が入る", row.stage == "pre_research")
        check("decision が 3 段階のいずれか",
              row.decision in ("blocked", "review", "clear"))
        check("engine=lqe-v1", row.engine == "lqe-v1")
        check("findings が 20 件保存される", len(row.findings_json) == 20)
        check("blocker_codes が list", isinstance(row.blocker_codes, list))
        check("review_codes が list", isinstance(row.review_codes, list))
        check("positive_facts_json が list", isinstance(row.positive_facts_json, list))
        check("override_reason は既定 None", row.override_reason is None)
        check("override_evidence_url は既定 None", row.override_evidence_url is None)
        check("created_at が入る", row.created_at is not None)
    finally:
        db.close()


def test_run_is_append_only():
    print("test_run_is_append_only")
    db = SessionLocal()
    try:
        p = make_project(db)
        first = lq.run(db, p, lq.STAGE_PRE_RESEARCH)
        first_id = first.id
        snapshot = (first.decision, list(first.blocker_codes or []),
                    first.evidence_count, first.created_at)

        second = lq.run(db, p, lq.STAGE_PRE_RESEARCH)
        rows = lq.list_history(db, p.id)
        check("再実行で履歴が 2 行になる", len(rows) == 2)
        check("新しい行が追加される（別 id）", second.id != first_id)

        reloaded = db.get(LeadQualification, first_id)
        check("過去履歴の decision が変わらない", reloaded.decision == snapshot[0])
        check("過去履歴の blocker_codes が変わらない",
              list(reloaded.blocker_codes or []) == snapshot[1])
        check("過去履歴の evidence_count が変わらない",
              reloaded.evidence_count == snapshot[2])
        check("過去履歴の created_at が変わらない", reloaded.created_at == snapshot[3])

        third = lq.run(db, p, lq.STAGE_PRE_OUTREACH)
        check("別ステージも履歴を追加する", len(lq.list_history(db, p.id)) == 3)
        check("get_latest は最新を返す", lq.get_latest(db, p.id).id == third.id)
        check("get_latest(stage) はステージ内の最新を返す",
              lq.get_latest(db, p.id, stage=lq.STAGE_PRE_RESEARCH).id == second.id)
    finally:
        db.close()


def test_run_updates_only_cache_columns():
    print("test_run_updates_only_cache_columns")
    db = SessionLocal()
    try:
        p = make_project(db)
        before = {
            "status": p.status,
            "sales_status": p.sales_status,
            "archived_at": p.archived_at,
            "archive_reason": p.archive_reason,
            "eligible_for_contact_search": p.eligible_for_contact_search,
            "latest_score": p.latest_score,
        }
        row = lq.run(db, p, lq.STAGE_PRE_RESEARCH)
        db.refresh(p)
        check("lead_qualification_decision が最新 decision になる",
              p.lead_qualification_decision == row.decision)
        check("lead_qualification_at が evaluated_at になる",
              p.lead_qualification_at is not None)
        check("archived_at は変更されない（自動アーカイブ禁止）",
              p.archived_at == before["archived_at"] and p.archived_at is None)
        check("archive_reason は変更されない",
              p.archive_reason == before["archive_reason"])
        check("status は変更されない", p.status == before["status"])
        check("sales_status は変更されない", p.sales_status == before["sales_status"])
        check("既存ゲート列は変更されない",
              p.eligible_for_contact_search == before["eligible_for_contact_search"])
        check("latest_score は変更されない", p.latest_score == before["latest_score"])

        prev_at = p.lead_qualification_at
        second = lq.run(db, p, lq.STAGE_PRE_OUTREACH)
        db.refresh(p)
        check("再判定でキャッシュが最新へ更新される",
              p.lead_qualification_decision == second.decision)
        check("キャッシュ日時が進む（または同値）", p.lead_qualification_at >= prev_at)
    finally:
        db.close()


def test_run_commit_responsibility():
    print("test_run_commit_responsibility")
    db = SessionLocal()
    try:
        p = make_project(db)
        lq.run(db, p, lq.STAGE_PRE_RESEARCH)
        other = SessionLocal()
        try:
            check("commit=True（既定）は別セッションから見える",
                  other.query(LeadQualification).filter_by(project_id=p.id).count() == 1)
        finally:
            other.close()

        p2 = make_project(db)
        lq.run(db, p2, lq.STAGE_PRE_RESEARCH, commit=False)
        other2 = SessionLocal()
        try:
            check("commit=False は呼び出し側が commit するまで見えない",
                  other2.query(LeadQualification).filter_by(project_id=p2.id).count()
                  == 0)
        finally:
            other2.close()
        db.commit()
        other3 = SessionLocal()
        try:
            check("呼び出し側 commit 後に永続化される",
                  other3.query(LeadQualification).filter_by(project_id=p2.id).count()
                  == 1)
        finally:
            other3.close()
    finally:
        db.close()


def test_evidence_count_is_stored_not_recomputed():
    print("test_evidence_count_is_stored_not_recomputed")
    db = SessionLocal()
    try:
        p = make_project(db)
        add_discovery(db, p)
        add_japan_check(db, p, channels=[
            {"channel": "amazon", "status": "found", "label": "Amazon.co.jp",
             "search_url": "https://www.amazon.co.jp/dp/B0TEST"}])
        signals = lq.gather_signals(db, p)
        expected = lq.qualify(signals, lq.STAGE_PRE_RESEARCH)
        row = lq.run(db, p, lq.STAGE_PRE_RESEARCH)
        check("evidence_count が QualificationResult 値と一致",
              row.evidence_count == expected.evidence_count)
        manual = sum(
            1 for f in row.findings_json for e in f["evidence"]
            if e["claim"] and e["source_url"] and e["method"] and e["checked_at"]
        ) + sum(
            1 for pf in row.positive_facts_json for e in pf["evidence"]
            if e["claim"] and e["source_url"] and e["method"] and e["checked_at"]
        )
        check("保存された証跡数と一致する", row.evidence_count == manual)
        check("証跡が実際に存在する", row.evidence_count > 0)
    finally:
        db.close()


def test_internal_db_contract_in_storage():
    print("test_internal_db_contract_in_storage")
    db = SessionLocal()
    try:
        p = make_project(db, source_url=None)
        row = lq.run(db, p, lq.STAGE_PRE_OUTREACH)
        evidences = [e for f in row.findings_json for e in f["evidence"]] + [
            e for pf in row.positive_facts_json for e in pf["evidence"]]
        locators = [e for e in evidences if (e["source_url"] or "").startswith("db://")]
        check("保存後も内部ロケータが存在する", len(locators) > 0)
        check("db:// は method=db_state のみ",
              all(e["method"] == "db_state" for e in locators))
        check("db:// の source_kind は internal_db",
              all(e["source_kind"] == "internal_db" for e in locators))
        check("形式は db://projects/<id>#",
              all(e["source_url"].startswith(f"db://projects/{p.id}#")
                  for e in locators))
        check("internal_db を名乗るのは db:// のみ",
              all((e["source_url"] or "").startswith("db://")
                  for e in evidences if e["source_kind"] == "internal_db"))
        check("db:// は http/https ではない",
              not any(e["source_url"].startswith("http") for e in locators))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  スキーマ / migration
# --------------------------------------------------------------------------- #
def test_model_schema():
    print("test_model_schema")
    t = LeadQualification.__table__
    for col in ("id", "project_id", "stage", "decision", "blocker_codes",
                "review_codes", "findings_json", "positive_facts_json",
                "evidence_count", "engine", "override_reason",
                "override_evidence_url", "created_at"):
        check(f"lead_qualifications に {col} がある", col in t.columns)
    fks = list(t.columns["project_id"].foreign_keys)
    check("project_id は projects.id への FK",
          fks and fks[0].column.table.name == "projects")
    check("FK は ondelete=CASCADE", fks and fks[0].ondelete == "CASCADE")
    check("project_id に index", t.columns["project_id"].index is True)
    check("created_at に index", t.columns["created_at"].index is True)
    check("stage は文字列型", "VARCHAR" in str(t.columns["stage"].type).upper())
    check("decision は文字列型", "VARCHAR" in str(t.columns["decision"].type).upper())
    check("override_reason は nullable", t.columns["override_reason"].nullable)
    check("override_evidence_url は nullable",
          t.columns["override_evidence_url"].nullable)
    check("engine は NOT NULL", not t.columns["engine"].nullable)
    check("stage は NOT NULL", not t.columns["stage"].nullable)
    check("decision は NOT NULL", not t.columns["decision"].nullable)
    check("append-only の前提が docstring に明記されている",
          "append-only" in (LeadQualification.__module__ and
                            sys.modules[LeadQualification.__module__].__doc__ or ""))

    pt = Project.__table__
    check("projects に lead_qualification_decision がある",
          "lead_qualification_decision" in pt.columns)
    check("projects に lead_qualification_at がある",
          "lead_qualification_at" in pt.columns)
    check("lead_qualification_decision は nullable",
          pt.columns["lead_qualification_decision"].nullable)
    check("lead_qualification_at は nullable",
          pt.columns["lead_qualification_at"].nullable)
    check("lead_qualification_decision に index",
          pt.columns["lead_qualification_decision"].index is True)
    check("既存の archived_at 列は残っている", "archived_at" in pt.columns)
    check("既存の sales_status 列は残っている", "sales_status" in pt.columns)


def test_migration_file():
    print("test_migration_file")
    path = BACKEND / "alembic" / "versions" / "0050_lead_qualification.py"
    check("migration ファイルが存在する", path.exists())
    src = path.read_text(encoding="utf-8")
    check("revision=0050_lead_qualification",
          'revision: str = "0050_lead_qualification"' in src)
    check("down_revision=0049_project_status_events",
          'down_revision: Union[str, None] = "0049_project_status_events"' in src)
    check("lead_qualifications を作成する", 'create_table(\n        "lead_qualifications"' in src)
    check("projects へ lead_qualification_decision を追加",
          '"lead_qualification_decision"' in src)
    check("projects へ lead_qualification_at を追加",
          '"lead_qualification_at"' in src)
    check("ix_projects_lead_qualification_decision を作成",
          '"ix_projects_lead_qualification_decision"' in src)
    check("ondelete=CASCADE を指定", 'ondelete="CASCADE"' in src)
    check("downgrade が定義されている", "def downgrade()" in src)
    for banned in ("alter_column", "drop_table(\"projects\"", "nullable=False)\n    )\n    op.alter"):
        check(f"破壊的変更 {banned!r} を含まない", banned not in src)
    check("既存データを更新しない（UPDATE 文なし）",
          "op.execute" not in src and "UPDATE" not in src.upper().replace(
              "UPDATE / DELETE", ""))


def test_no_api_or_ui_change():
    print("test_no_api_or_ui_change")
    for rel in ("app/routers/contact_intelligence.py", "app/routers/projects.py",
                "app/schemas/project.py"):
        path = BACKEND / rel
        if not path.exists():
            continue
        check(f"{rel} に lead_qualification を追加していない",
              "lead_qualification" not in path.read_text(encoding="utf-8"))
    front = BACKEND.parent / "frontend" / "lib" / "api.ts"
    if front.exists():
        check("frontend/lib/api.ts に lead_qualification を追加していない",
              "lead_qualification" not in front.read_text(encoding="utf-8"))


def main():
    test_gather_signals_basic_fields()
    test_gather_signals_is_read_only()
    test_gather_signals_no_network()
    test_gather_signals_official_site_evidence()
    test_gather_signals_business_emails()
    test_gather_signals_decision_makers()
    test_gather_signals_japan_sales_distinction()
    test_gather_signals_creator_domain()
    test_run_appends_history()
    test_run_is_append_only()
    test_run_updates_only_cache_columns()
    test_run_commit_responsibility()
    test_evidence_count_is_stored_not_recomputed()
    test_internal_db_contract_in_storage()
    test_model_schema()
    test_migration_file()
    test_no_api_or_ui_change()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
