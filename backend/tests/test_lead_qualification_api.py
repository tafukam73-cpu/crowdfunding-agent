"""営業対象除外判定 API（PR-4）のオフライン検証。

ネットワーク不要・SQLite。pytest には依存しない（CLAUDE.md §7）。

検証の重点:
  - GET は履歴を書かない（画面表示で履歴が増えない）
  - projects のスナップショット 2 列は **pre_research 専用**
  - override は履歴 1 行だけ増やし、機械判定と実効判定を区別できる
  - internal_db をリンク化しない（is_external_link）
  - 数値スコア・確率・予測語をレスポンスに出さない（JSON 全体を再帰走査）
  - 既存の contact-search-gate / facts レスポンスが不変

実行: docker compose exec -T backend python tests/test_lead_qualification_api.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "lqe_api_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.main import app  # noqa: E402
from app.models.lead_qualification import LeadQualification  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.schemas.lead_qualification import is_external_link  # noqa: E402
from app.services import lead_qualification_service as lqs  # noqa: E402

Base.metadata.create_all(engine)
client = TestClient(app)

_passed = _failed = 0
_seq = [0]

# レスポンスに現れてはいけないキー名（部分一致で判定）。
BANNED_KEYS = (
    "score", "probability", "forecast", "reply_rate", "success_rate",
    "makuake_fit", "japan_crowdfunding", "confidence_score", "priority_score",
    "stars", "percent",
)
# confidence として許されるラベル（数値は不可）。
ALLOWED_CONFIDENCE = {"high", "medium", "low", "unverified"}


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


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
            "Waterproof compact design solves the daily hydration problem."
        ),
        "description_clean": (
            "A rechargeable stainless steel bottle with a companion app. "
            "Waterproof compact design solves the daily hydration problem."
        ),
        "maker_name": "Acme Studio",
        "currency": "USD",
    }
    data.update(over)
    p = Project(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def history_count(project_id: int) -> int:
    db = SessionLocal()
    try:
        return db.query(LeadQualification).filter_by(project_id=project_id).count()
    finally:
        db.close()


def snapshot(project_id: int):
    db = SessionLocal()
    try:
        p = db.get(Project, project_id)
        return (p.lead_qualification_decision, p.lead_qualification_at,
                p.archived_at, p.archive_reason)
    finally:
        db.close()


def walk(node, path="$"):
    """JSON を再帰走査して (path, key, value) を列挙する。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


# --------------------------------------------------------------------------- #
#  GET /lead-qualification
# --------------------------------------------------------------------------- #
def test_get_does_not_write_history():
    print("test_get_does_not_write_history")
    db = SessionLocal()
    p = make_project(db)
    pid = p.id
    db.close()

    before = history_count(pid)
    for _ in range(3):
        r = client.get(f"/projects/{pid}/lead-qualification")
        check("GET 200", r.status_code == 200)
    check("GET は履歴を書かない", history_count(pid) == before == 0)
    check("GET は projects スナップショットを書かない", snapshot(pid)[0] is None)

    body = r.json()
    check("履歴なしは persisted=false", body["persisted"] is False)
    check("overridden=false", body["overridden"] is False)
    check("machine と effective が一致", body["machine_decision"] == body["effective_decision"])
    check("decision は effective と一致", body["decision"] == body["effective_decision"])
    check("findings が 20 件", len(body["findings"]) == 20)
    check("rule_version がある", body["rule_version"] == "lqe-v1")


def test_get_returns_latest_per_stage():
    print("test_get_returns_latest_per_stage")
    db = SessionLocal()
    p = make_project(db)
    pid = p.id
    lqs.run(db, p, lqs.STAGE_PRE_RESEARCH)
    lqs.run(db, p, lqs.STAGE_PRE_OUTREACH)
    db.close()

    r1 = client.get(f"/projects/{pid}/lead-qualification?stage=pre_research").json()
    r2 = client.get(f"/projects/{pid}/lead-qualification?stage=pre_outreach").json()
    check("pre_research の stage が正しい", r1["stage"] == "pre_research")
    check("pre_outreach の stage が正しい", r2["stage"] == "pre_outreach")
    check("保存済みは persisted=true", r1["persisted"] is True and r2["persisted"] is True)
    check("stage 既定は pre_research",
          client.get(f"/projects/{pid}/lead-qualification").json()["stage"]
          == "pre_research")
    check("GET で履歴は増えない", history_count(pid) == 2)


def test_get_404_and_422():
    print("test_get_404_and_422")
    check("存在しない project は 404",
          client.get("/projects/99999999/lead-qualification").status_code == 404)
    db = SessionLocal()
    pid = make_project(db).id
    db.close()
    check("stage 不正は 422",
          client.get(f"/projects/{pid}/lead-qualification?stage=whenever")
          .status_code == 422)


# --------------------------------------------------------------------------- #
#  POST /recheck
# --------------------------------------------------------------------------- #
def test_recheck_appends_one_row():
    print("test_recheck_appends_one_row")
    db = SessionLocal()
    p = make_project(db)
    pid = p.id
    db.close()

    r = client.post(f"/projects/{pid}/lead-qualification/recheck")
    check("recheck 200", r.status_code == 200)
    check("履歴が 1 行増える", history_count(pid) == 1)
    body = r.json()
    check("persisted=true", body["qualification"]["persisted"] is True)
    check("snapshot_updated=true（pre_research）", body["snapshot_updated"] is True)

    client.post(f"/projects/{pid}/lead-qualification/recheck")
    check("2 回目でさらに 1 行増える（append-only）", history_count(pid) == 2)
    check("存在しない project は 404",
          client.post("/projects/99999999/lead-qualification/recheck").status_code
          == 404)
    check("stage 不正は 422",
          client.post(f"/projects/{pid}/lead-qualification/recheck?stage=x")
          .status_code == 422)


def test_snapshot_is_pre_research_only():
    print("test_snapshot_is_pre_research_only")
    db = SessionLocal()
    p = make_project(db)
    pid = p.id
    db.close()

    check("初期スナップショットは None", snapshot(pid)[0] is None)
    r = client.post(f"/projects/{pid}/lead-qualification/recheck?stage=pre_research")
    decision_pr = r.json()["qualification"]["decision"]
    snap_after_pr = snapshot(pid)
    check("pre_research でスナップショットが入る", snap_after_pr[0] == decision_pr)
    check("スナップショット日時が入る", snap_after_pr[1] is not None)

    r2 = client.post(f"/projects/{pid}/lead-qualification/recheck?stage=pre_outreach")
    check("pre_outreach も履歴は増える", history_count(pid) == 2)
    check("pre_outreach は snapshot_updated=false",
          r2.json()["snapshot_updated"] is False)
    snap_after_po = snapshot(pid)
    check("pre_outreach ではスナップショット decision が不変",
          snap_after_po[0] == snap_after_pr[0])
    check("pre_outreach ではスナップショット日時も不変",
          snap_after_po[1] == snap_after_pr[1])
    check("archived_at は変更されない（自動アーカイブ禁止）",
          snap_after_po[2] is None and snap_after_po[3] is None)


# --------------------------------------------------------------------------- #
#  POST /override
# --------------------------------------------------------------------------- #
def _override_body(**over):
    body = {
        "stage": "pre_research",
        "decision": "clear",
        "reason": "公式サイトで日本未展開を確認した",
        "evidence_url": "https://acme-bottle.example/news/jp",
    }
    body.update(over)
    return body


def test_override_appends_one_row():
    print("test_override_appends_one_row")
    db = SessionLocal()
    p = make_project(db, source_url=None)  # 機械判定は blocked（T 情報不足）
    pid = p.id
    db.close()

    check("前提: 履歴 0 件", history_count(pid) == 0)
    r = client.post(f"/projects/{pid}/lead-qualification/override",
                    json=_override_body(decision="clear"))
    check("override 200（履歴が無くても可）", r.status_code == 200)
    check("履歴増加は 1 行だけ", history_count(pid) == 1)

    body = r.json()
    q = body["qualification"]
    check("changed=true（機械判定と異なる）", body["changed"] is True)
    check("overridden=true", q["overridden"] is True)
    check("machine_decision は機械判定", q["machine_decision"] == "blocked")
    check("effective_decision は人の指定値", q["effective_decision"] == "clear")
    check("decision は effective と一致", q["decision"] == "clear")
    check("override_reason が保存される",
          q["override_reason"] == "公式サイトで日本未展開を確認した")
    check("override_evidence_url が保存される",
          q["override_evidence_url"].startswith("https://acme-bottle.example"))
    check("スナップショットは実効判定になる", snapshot(pid)[0] == "clear")
    check("archived_at は変更されない", snapshot(pid)[2] is None)

    db = SessionLocal()
    try:
        row = db.query(LeadQualification).filter_by(project_id=pid).one()
        check("findings_json に予約メタが 1 要素だけ入る",
              sum(1 for i in row.findings_json if "_qualification_meta" in i) == 1)
        check("通常 Finding は 20 件のまま", len(lqs.findings_of(row)) == 20)
        check("メタは Finding と混同しない（code を持たない）",
              all("code" not in i for i in row.findings_json
                  if "_qualification_meta" in i))
        machine = lqs.qualify(lqs.gather_signals(db, db.get(Project, pid)),
                              "pre_research")
        check("evidence_count はメタで増えない",
              row.evidence_count == machine.evidence_count)
        check("blocker_codes は機械判定のまま",
              row.blocker_codes == machine.blocker_codes)
        check("review_codes は機械判定のまま",
              row.review_codes == machine.review_codes)
    finally:
        db.close()
    check("レスポンスに予約メタが漏れない",
          "_qualification_meta" not in json.dumps(body))


def test_override_same_decision_is_changed_false():
    print("test_override_same_decision_is_changed_false")
    db = SessionLocal()
    p = make_project(db, source_url=None)
    pid = p.id
    db.close()
    machine = client.get(f"/projects/{pid}/lead-qualification").json()["machine_decision"]

    r = client.post(f"/projects/{pid}/lead-qualification/override",
                    json=_override_body(decision=machine))
    check("同じ decision でも 409 にしない", r.status_code == 200)
    check("changed=false", r.json()["changed"] is False)
    check("監査記録として履歴に残る", history_count(pid) == 1)
    check("overridden=true のまま", r.json()["qualification"]["overridden"] is True)


def test_override_validation():
    print("test_override_validation")
    db = SessionLocal()
    pid = make_project(db).id
    db.close()

    cases = [
        ("reason 欠落", {k: v for k, v in _override_body().items() if k != "reason"}),
        ("reason が空", _override_body(reason="")),
        ("reason が空白のみ", _override_body(reason="   ")),
        ("evidence_url 欠落",
         {k: v for k, v in _override_body().items() if k != "evidence_url"}),
        ("evidence_url が db://", _override_body(evidence_url="db://projects/1#x")),
        ("evidence_url が file://", _override_body(evidence_url="file:///tmp/a")),
        ("evidence_url が非 URL", _override_body(evidence_url="not-a-url")),
        ("decision 不正", _override_body(decision="maybe")),
        ("stage 不正", _override_body(stage="whenever")),
    ]
    for name, body in cases:
        r = client.post(f"/projects/{pid}/lead-qualification/override", json=body)
        check(f"{name} は 422", r.status_code == 422)
    check("不正入力では履歴が増えない", history_count(pid) == 0)
    check("存在しない project は 404",
          client.post("/projects/99999999/lead-qualification/override",
                      json=_override_body()).status_code == 404)


def test_override_pre_outreach_keeps_snapshot():
    print("test_override_pre_outreach_keeps_snapshot")
    db = SessionLocal()
    p = make_project(db)
    pid = p.id
    db.close()
    client.post(f"/projects/{pid}/lead-qualification/recheck?stage=pre_research")
    before = snapshot(pid)

    r = client.post(f"/projects/{pid}/lead-qualification/override",
                    json=_override_body(stage="pre_outreach", decision="blocked"))
    check("pre_outreach の override も 200", r.status_code == 200)
    check("履歴は 1 行増える", history_count(pid) == 2)
    after = snapshot(pid)
    check("pre_outreach の override はスナップショットを変えない",
          after[0] == before[0] and after[1] == before[1])


# --------------------------------------------------------------------------- #
#  is_external_link / internal_db
# --------------------------------------------------------------------------- #
def test_external_link_rule():
    print("test_external_link_rule")
    cases = [
        (None, "https://a.example/x", True),
        (None, "http://a.example", True),
        ("internal_db", "db://projects/1#campaign_url", False),
        ("internal_db", "https://a.example", False),
        (None, "db://projects/1#campaign_url", False),
        (None, "", False),
        (None, None, False),
        (None, "file:///tmp/a", False),
        (None, "/local/path", False),
        (None, "not a url", False),
        (None, "ftp://a.example", False),
    ]
    for kind, url, expected in cases:
        check(f"kind={kind} url={url!r} → {expected}",
              is_external_link(kind, url) is expected)


def test_internal_db_not_linkable_in_response():
    print("test_internal_db_not_linkable_in_response")
    db = SessionLocal()
    pid = make_project(db, source_url=None).id  # T が立ち db:// 証跡が出る
    db.close()
    body = client.get(f"/projects/{pid}/lead-qualification").json()
    evidences = [e for f in body["findings"] for e in f["evidence"]] + [
        e for p in body["positive_facts"] for e in p["evidence"]]
    locators = [e for e in evidences if (e["source_url"] or "").startswith("db://")]
    check("内部ロケータが含まれている", len(locators) > 0)
    check("internal_db は is_external_link=false",
          all(e["is_external_link"] is False for e in locators))
    check("internal_db の source_kind が正しい",
          all(e["source_kind"] == "internal_db" for e in locators))
    externals = [e for e in evidences if e["is_external_link"]]
    check("is_external_link=true は http(s) のみ",
          all((e["source_url"] or "").startswith(("http://", "https://"))
              for e in externals))


# --------------------------------------------------------------------------- #
#  一覧フィルタ
# --------------------------------------------------------------------------- #
def test_qualification_list_filter():
    print("test_qualification_list_filter")
    db = SessionLocal()
    blocked_p = make_project(db, source_url=None, title="Blocked Item")
    clear_p = make_project(db, title="Clear Item")
    bid, cid = blocked_p.id, clear_p.id
    db.close()

    client.post(f"/projects/{bid}/lead-qualification/recheck")
    client.post(f"/projects/{cid}/lead-qualification/override",
                json=_override_body(decision="clear"))

    r = client.get("/projects?qualification=blocked&page_size=100")
    check("qualification=blocked が 200", r.status_code == 200)
    ids = [i["id"] for i in r.json()["items"]]
    check("blocked に該当案件が入る", bid in ids)
    check("blocked に clear 案件は入らない", cid not in ids)

    r2 = client.get("/projects?qualification=clear&page_size=100")
    ids2 = [i["id"] for i in r2.json()["items"]]
    check("clear に該当案件が入る", cid in ids2)
    check("clear に blocked 案件は入らない", bid not in ids2)

    check("未判定案件は qualification フィルタに出ない",
          all(i["lead_qualification_decision"] is not None
              for i in r.json()["items"] + r2.json()["items"]))
    check("ProjectOut に lead_qualification_decision がある",
          "lead_qualification_decision" in r.json()["items"][0])
    check("ProjectOut に lead_qualification_at がある",
          "lead_qualification_at" in r.json()["items"][0])

    combo = client.get(
        f"/projects?qualification=blocked&site=kickstarter&page_size=100")
    check("既存フィルタと組み合わせられる（site）", combo.status_code == 200)
    check("組み合わせでも該当する", bid in [i["id"] for i in combo.json()["items"]])
    combo2 = client.get("/projects?qualification=blocked&sales_status=not_started"
                        "&sort=created_at&order=asc&page_size=100")
    check("既存フィルタと直交する（sales_status/sort）", combo2.status_code == 200)
    check("archived=true とも併用できる",
          client.get("/projects?qualification=blocked&archived=true").status_code
          == 200)
    check("qualification 不正値は 422",
          client.get("/projects?qualification=maybe").status_code == 422)
    check("qualification 未指定は従来どおり",
          client.get("/projects?page_size=1").status_code == 200)


# --------------------------------------------------------------------------- #
#  禁止語・禁止キー
# --------------------------------------------------------------------------- #
def test_no_scores_or_forecasts_in_response():
    print("test_no_scores_or_forecasts_in_response")
    db = SessionLocal()
    pid = make_project(db).id
    db.close()
    client.post(f"/projects/{pid}/lead-qualification/recheck")

    bodies = {
        "GET": client.get(f"/projects/{pid}/lead-qualification").json(),
        "recheck": client.post(f"/projects/{pid}/lead-qualification/recheck").json(),
        "override": client.post(f"/projects/{pid}/lead-qualification/override",
                                json=_override_body()).json(),
    }
    for name, body in bodies.items():
        bad_keys = [
            f"{path}.{k}" for path, k, _ in walk(body)
            if any(b in k.lower() for b in BANNED_KEYS)
        ]
        check(f"{name}: 禁止キーが無い（{bad_keys[:3]}）", bad_keys == [])
        confs = [v for _, k, v in walk(body) if k == "confidence"]
        check(f"{name}: confidence はラベルのみ",
              all(isinstance(v, str) and v in ALLOWED_CONFIDENCE for v in confs))
        check(f"{name}: confidence が数値でない",
              not any(isinstance(v, (int, float)) for v in confs))
        text = json.dumps(body, ensure_ascii=False)
        for word in ("返信率", "成功率", "成功確率", "可能性スコア", "予測"):
            check(f"{name}: '{word}' を含まない", word not in text)


def test_existing_endpoints_unchanged():
    print("test_existing_endpoints_unchanged")
    db = SessionLocal()
    pid = make_project(db).id
    db.close()

    gate = client.get(f"/projects/{pid}/contact-search-gate")
    check("contact-search-gate 200", gate.status_code == 200)
    expected = {
        "eligible_for_contact_search", "contact_search_gate_decision",
        "user_reasons", "blockers", "gate_checked_at", "campaign_url",
        "campaign_url_missing", "campaign_url_missing_reason", "official_site_url",
    }
    check("gate のキー集合が不変", set(gate.json()["gate"]) == expected)
    check("gate に LQE キーが漏れない",
          not any(k in json.dumps(gate.json())
                  for k in ("lqe_decision", "lqe_blocker_codes", "lqe_review_codes")))
    check("gate に内部スコアが漏れない",
          "japan_crowdfunding_score" not in json.dumps(gate.json()))

    facts = client.get(f"/projects/{pid}/facts")
    check("facts 200", facts.status_code == 200)
    check("facts のトップレベルキーが不変",
          set(facts.json()) == {"project_id", "product", "funding", "maker",
                                "japan_market", "regulatory", "contact_search",
                                "generated_at"})
    check("既存 2 エンドポイントは履歴を書かない", history_count(pid) == 0)


def test_no_network():
    print("test_no_network")

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    db = SessionLocal()
    pid = make_project(db).id
    db.close()
    orig = (socket.socket.connect, socket.socket.connect_ex,
            urllib.request.urlopen, socket.getaddrinfo)
    socket.socket.connect = boom
    socket.socket.connect_ex = boom
    urllib.request.urlopen = boom
    socket.getaddrinfo = boom
    try:
        codes = [
            client.get(f"/projects/{pid}/lead-qualification").status_code,
            client.post(f"/projects/{pid}/lead-qualification/recheck").status_code,
            client.post(f"/projects/{pid}/lead-qualification/override",
                        json=_override_body()).status_code,
            client.get("/projects?qualification=clear").status_code,
        ]
        ok = codes == [200, 200, 200, 200]
    except AssertionError:
        ok = False
    finally:
        (socket.socket.connect, socket.socket.connect_ex,
         urllib.request.urlopen, socket.getaddrinfo) = orig
    check("LQE API はネットワークに触れない", ok)


def main():
    test_get_does_not_write_history()
    test_get_returns_latest_per_stage()
    test_get_404_and_422()
    test_recheck_appends_one_row()
    test_snapshot_is_pre_research_only()
    test_override_appends_one_row()
    test_override_same_decision_is_changed_false()
    test_override_validation()
    test_override_pre_outreach_keeps_snapshot()
    test_external_link_rule()
    test_internal_db_not_linkable_in_response()
    test_qualification_list_filter()
    test_no_scores_or_forecasts_in_response()
    test_existing_endpoints_unchanged()
    test_no_network()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
