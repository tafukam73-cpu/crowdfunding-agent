"""contact_search_gate の LQE 委譲（PR-3）の互換性検証。

ネットワーク不要・SQLite。pytest には依存しない（CLAUDE.md §7）。

このリファクタの成否は「既存の挙動を壊していないこと」で決まる。したがって
検証の重点は以下に置く。

  - 公開 API（関数 / 定数 / 例外 / 語彙）がすべて残っている
  - evaluate() の戻り値キー集合が変わっていない（LQE 由来キーは既定で出さない）
  - 409 レスポンスのキー集合が変わっていない
  - merge_gate_with_lqe が **純粋関数**（DB / HTTP / commit なし・入力を変更しない）
  - LQE は **決して緩和しない**（never upgrade）
  - evaluate() が履歴（lead_qualifications）を書かない＝二重履歴を作らない

実行: docker compose exec -T backend python tests/test_gate_lqe_compat.py
"""
from __future__ import annotations

import inspect
import os
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "gate_lqe_compat_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.lead_qualification import LeadQualification  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import contact_search_gate as gate  # noqa: E402
from app.services import lead_qualification_service as lqs  # noqa: E402

Base.metadata.create_all(engine)

_passed = _failed = 0
_seq = [0]

# LQE 導入前に evaluate() が返していたキー（実データで実測した 14 個）。
LEGACY_KEYS = {
    "blockers", "campaign_url", "campaign_url_missing",
    "campaign_url_missing_reason", "contact_search_gate_decision",
    "contact_search_gate_reason", "eligible_for_contact_search",
    "gate_checked_at", "japan_crowdfunding_score", "japan_crowdfunding_threshold",
    "official_site_url", "rationale", "reasons", "user_reasons",
}


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


def fake_gate(decision, *, blockers=None, user_reasons=None, reason="base reason"):
    return {
        "eligible_for_contact_search": decision == gate.GATE_ELIGIBLE,
        "contact_search_gate_decision": decision,
        "contact_search_gate_reason": reason,
        "user_reasons": list(user_reasons or []),
        "japan_crowdfunding_score": 50,
        "japan_crowdfunding_threshold": gate.JAPAN_CF_SCORE_THRESHOLD,
        "gate_checked_at": None,
        "reasons": [],
        "blockers": list(blockers or []),
        "rationale": reason,
        "campaign_url": "https://www.kickstarter.com/projects/acme/x",
        "campaign_url_missing": False,
        "campaign_url_missing_reason": None,
        "official_site_url": None,
    }


def qualification(*, decision):
    """狙った decision になる QualificationResult を実際の qualify で作る。"""
    base = {
        "project_id": 1,
        "campaign_url": "https://www.kickstarter.com/projects/acme/x",
        "japanese_summary": "温度管理ができるステンレス製の充電式ボトル。防水の本体を持つ。",
        "title": "Compact Stainless Steel Water Bottle",
        "description": "A rechargeable stainless steel waterproof bottle.",
        "maker_identity": {"verified": True, "source_url": "https://a.example/c",
                           "checked_at": "2026-08-01T00:00:00Z", "method": "x"},
        "official_site": {"url": "https://a.example", "verified": True,
                          "source_url": "https://a.example/c",
                          "checked_at": "2026-08-01T00:00:00Z", "method": "x"},
    }
    if decision == "blocked":
        base.update(title="CloudDesk SaaS",
                    description="Our software only web service for teams.")
    elif decision == "review":
        base.update(title="Coffee snack food kit",
                    description="An edible food snack product.")
    q = lqs.qualify(base, lqs.STAGE_PRE_RESEARCH)
    assert q.decision == decision, f"expected {decision}, got {q.decision}"
    return q


# --------------------------------------------------------------------------- #
#  1. 公開 API の維持
# --------------------------------------------------------------------------- #
def test_public_api_preserved():
    print("test_public_api_preserved")
    for name in ("evaluate", "require_eligible", "is_non_physical",
                 "japan_crowdfunding_score", "merge_gate_with_lqe"):
        check(f"{name} が存在する", callable(getattr(gate, name, None)))
    check("GateBlocked が例外", issubclass(gate.GateBlocked, Exception))
    check("GATE_ELIGIBLE", gate.GATE_ELIGIBLE == "eligible")
    check("GATE_NEEDS_REVIEW", gate.GATE_NEEDS_REVIEW == "needs_review")
    check("GATE_NOT_ELIGIBLE", gate.GATE_NOT_ELIGIBLE == "not_eligible")
    check("makuake_fit 閾値 45 が温存されている",
          gate.JAPAN_CF_SCORE_THRESHOLD == 45)
    check("makuake_fit 下限 30 が温存されている",
          gate.JAPAN_CF_SCORE_REVIEW_FLOOR == 30)
    check("MIN_SUMMARY_LEN が温存されている", gate.MIN_SUMMARY_LEN == 20)
    for name in ("_has_term", "_NON_PHYSICAL_STRONG", "_NON_PHYSICAL_WEAK",
                 "_PHYSICAL_PRODUCT_HINTS", "_NON_PHYSICAL_HINTS"):
        check(f"準公開の {name} が残っている", hasattr(gate, name))
    check("evaluate は persist キーワードを持つ",
          "persist" in inspect.signature(gate.evaluate).parameters)
    check("evaluate は include_lqe_detail キーワードを持つ",
          "include_lqe_detail" in inspect.signature(gate.evaluate).parameters)
    check("include_lqe_detail の既定は False",
          inspect.signature(gate.evaluate).parameters["include_lqe_detail"].default
          is False)


# --------------------------------------------------------------------------- #
#  2. merge_gate_with_lqe（純粋関数）
# --------------------------------------------------------------------------- #
def test_merge_blocked_downgrades():
    print("test_merge_blocked_downgrades")
    q = qualification(decision="blocked")
    for src in (gate.GATE_ELIGIBLE, gate.GATE_NEEDS_REVIEW, gate.GATE_NOT_ELIGIBLE):
        merged = gate.merge_gate_with_lqe(fake_gate(src), q)
        check(f"{src} → LQE blocked で not_eligible へ降格",
              merged["contact_search_gate_decision"] == gate.GATE_NOT_ELIGIBLE)
        check(f"{src} → eligible フラグが False",
              merged["eligible_for_contact_search"] is False)
    merged = gate.merge_gate_with_lqe(fake_gate(gate.GATE_ELIGIBLE), q)
    check("blocker の理由が user_reasons に入る", len(merged["user_reasons"]) > 0)
    check("blocker の理由が blockers に入る", len(merged["blockers"]) > 0)
    check("監査用 reason にも追記される",
          merged["contact_search_gate_reason"] != "base reason")
    check("rationale が更新される", merged["rationale"] != "base reason")


def test_merge_review_keeps_decision():
    print("test_merge_review_keeps_decision")
    q = qualification(decision="review")
    for src in (gate.GATE_ELIGIBLE, gate.GATE_NEEDS_REVIEW, gate.GATE_NOT_ELIGIBLE):
        base = fake_gate(src)
        merged = gate.merge_gate_with_lqe(base, q)
        check(f"{src} → LQE review でも decision は変わらない",
              merged["contact_search_gate_decision"] == src)
        check(f"{src} → eligible フラグも変わらない",
              merged["eligible_for_contact_search"]
              == base["eligible_for_contact_search"])
    merged = gate.merge_gate_with_lqe(fake_gate(gate.GATE_ELIGIBLE), q)
    check("review の理由は user_reasons に追記される",
          len(merged["user_reasons"]) > 0)
    check("review の理由は blockers に入れない", merged["blockers"] == [])
    check("review では監査 reason を書き換えない",
          merged["contact_search_gate_reason"] == "base reason")


def test_merge_clear_is_noop():
    print("test_merge_clear_is_noop")
    q = qualification(decision="clear")
    for src in (gate.GATE_ELIGIBLE, gate.GATE_NEEDS_REVIEW, gate.GATE_NOT_ELIGIBLE):
        base = fake_gate(src, blockers=["既存ブロッカー"], user_reasons=["既存理由"])
        merged = gate.merge_gate_with_lqe(base, q)
        for key in LEGACY_KEYS:
            check(f"{src}/clear: {key} が変わらない", merged[key] == base[key])


def test_merge_never_upgrades():
    print("test_merge_never_upgrades")
    rank = {"not_eligible": 0, "needs_review": 1, "eligible": 2}
    for dec in ("blocked", "review", "clear"):
        q = qualification(decision=dec)
        for src in (gate.GATE_ELIGIBLE, gate.GATE_NEEDS_REVIEW,
                    gate.GATE_NOT_ELIGIBLE):
            merged = gate.merge_gate_with_lqe(fake_gate(src), q)
            after = merged["contact_search_gate_decision"]
            check(f"LQE {dec} × gate {src} は緩和しない",
                  rank[after] <= rank[src])
    check("not_eligible は LQE clear でも eligible にならない",
          gate.merge_gate_with_lqe(
              fake_gate(gate.GATE_NOT_ELIGIBLE), qualification(decision="clear")
          )["contact_search_gate_decision"] == gate.GATE_NOT_ELIGIBLE)


def test_merge_deduplicates_reasons():
    print("test_merge_deduplicates_reasons")
    # LQE の T（情報不足）はゲートの hard blocker と同じ事実を言う。
    q = lqs.qualify({"project_id": 1, "title": "x"}, lqs.STAGE_PRE_RESEARCH)
    check("前提: LQE は blocked", q.decision == "blocked")
    check("前提: T が blocker", "T" in q.blocker_codes)
    base = fake_gate(
        gate.GATE_NOT_ELIGIBLE,
        blockers=["商品ページURL未確認（source_url が未取得）",
                  "商品内容が判別できない（日本語概要を生成できない）"],
        user_reasons=["商品ページURL未確認（source_url が未取得）",
                      "商品内容が判別できない（日本語概要を生成できない）"],
    )
    merged = gate.merge_gate_with_lqe(base, q)
    joined = " / ".join(merged["user_reasons"])
    check("同じ事実が二重に出ない（商品ページURL未確認）",
          joined.count("商品ページURL未確認") == 1)
    check("同じ事実が二重に出ない（商品内容が判別できない）",
          joined.count("商品内容が判別できない") == 1)
    check("正規化は括弧内の差を無視する",
          gate._normalize_reason("商品ページURL未確認（A）")
          == gate._normalize_reason("商品ページURL未確認（B）"))
    check("完全に別の理由は残る",
          not gate._is_duplicate_reason("まったく別の理由",
                                        [gate._normalize_reason("商品ページURL未確認")]))


def test_merge_manages_lqe_keys():
    print("test_merge_manages_lqe_keys")
    q = qualification(decision="blocked")
    merged = gate.merge_gate_with_lqe(fake_gate(gate.GATE_ELIGIBLE), q)
    check("lqe_decision を付与する", merged["lqe_decision"] == "blocked")
    check("lqe_blocker_codes を付与する", merged["lqe_blocker_codes"] == q.blocker_codes)
    check("lqe_review_codes を付与する", merged["lqe_review_codes"] == q.review_codes)
    check("LQE_DETAIL_FIELDS が 3 キー", len(gate.LQE_DETAIL_FIELDS) == 3)
    check("付与キーは LQE_DETAIL_FIELDS と一致",
          set(merged) - LEGACY_KEYS == set(gate.LQE_DETAIL_FIELDS))


def test_merge_is_pure():
    print("test_merge_is_pure")
    # docstring は「commit しない」等の説明を含むため、本体コードだけを走査する。
    body = inspect.getsource(gate.merge_gate_with_lqe).replace(
        gate.merge_gate_with_lqe.__doc__ or "", ""
    )
    for banned in ("db.", "commit", "session", "Session", "http", "HTTP"):
        check(f"merge の本体に {banned} を含まない", banned not in body)

    base = fake_gate(gate.GATE_ELIGIBLE)
    snapshot = {k: (list(v) if isinstance(v, list) else v) for k, v in base.items()}
    gate.merge_gate_with_lqe(base, qualification(decision="blocked"))
    check("入力の dict を変更しない",
          all(base[k] == snapshot[k] for k in snapshot))
    check("入力の list を変更しない",
          base["user_reasons"] == [] and base["blockers"] == [])

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    orig = (socket.socket.connect, urllib.request.urlopen, socket.getaddrinfo)
    socket.socket.connect = boom
    urllib.request.urlopen = boom
    socket.getaddrinfo = boom
    try:
        gate.merge_gate_with_lqe(base, qualification(decision="review"))
        ok = True
    except AssertionError:
        ok = False
    finally:
        (socket.socket.connect, urllib.request.urlopen, socket.getaddrinfo) = orig
    check("merge はネットワークに触れない", ok)

    check("qualification が None ならゲート結果をそのまま返す",
          gate.merge_gate_with_lqe(base, None) == base)


# --------------------------------------------------------------------------- #
#  3. evaluate() の互換性
# --------------------------------------------------------------------------- #
def test_evaluate_response_shape():
    print("test_evaluate_response_shape")
    db = SessionLocal()
    try:
        p = make_project(db)
        r = gate.evaluate(db, p, persist=False)
        check("戻り値キーが従来と完全一致", set(r) == LEGACY_KEYS)
        check("LQE 由来キーは既定で出さない",
              not any(k in r for k in gate.LQE_DETAIL_FIELDS))
        detailed = gate.evaluate(db, p, persist=False, include_lqe_detail=True)
        check("include_lqe_detail=True で 3 キーだけ増える",
              set(detailed) - LEGACY_KEYS == set(gate.LQE_DETAIL_FIELDS))
        check("decision は 3 値のいずれか",
              r["contact_search_gate_decision"] in
              (gate.GATE_ELIGIBLE, gate.GATE_NEEDS_REVIEW, gate.GATE_NOT_ELIGIBLE))
        check("eligible と decision が整合",
              r["eligible_for_contact_search"]
              == (r["contact_search_gate_decision"] == gate.GATE_ELIGIBLE))
        check("スコアが温存されている（内部値）",
              "japan_crowdfunding_score" in r
              and r["japan_crowdfunding_threshold"] == 45)
    finally:
        db.close()


def test_evaluate_matches_gate_only_when_lqe_clear():
    print("test_evaluate_matches_gate_only_when_lqe_clear")
    db = SessionLocal()
    try:
        p = make_project(db)
        only = gate._evaluate_gate_only(db, p)
        full = gate.evaluate(db, p, persist=False, include_lqe_detail=True)
        if full["lqe_decision"] == "clear":
            check("LQE clear なら decision が既存ゲートと一致",
                  full["contact_search_gate_decision"]
                  == only["contact_search_gate_decision"])
            check("LQE clear なら user_reasons も一致",
                  full["user_reasons"] == only["user_reasons"])
        else:
            check("LQE が clear でない場合も緩和しない",
                  {"not_eligible": 0, "needs_review": 1, "eligible": 2}[
                      full["contact_search_gate_decision"]]
                  <= {"not_eligible": 0, "needs_review": 1, "eligible": 2}[
                      only["contact_search_gate_decision"]])

        blocked = make_project(db, source_url=None)
        r = gate.evaluate(db, blocked, persist=False, include_lqe_detail=True)
        check("campaign_url 欠落は従来どおり not_eligible",
              r["contact_search_gate_decision"] == gate.GATE_NOT_ELIGIBLE)
        check("LQE も blocked", r["lqe_decision"] == "blocked")
        check("理由が二重化していない",
              " / ".join(r["user_reasons"]).count("商品ページURL未確認") == 1)
    finally:
        db.close()


def test_evaluate_writes_no_history():
    print("test_evaluate_writes_no_history")
    db = SessionLocal()
    try:
        p = make_project(db)
        before = db.query(LeadQualification).count()
        for _ in range(3):
            gate.evaluate(db, p, persist=False)
        check("evaluate は履歴を書かない（二重履歴を作らない）",
              db.query(LeadQualification).count() == before)
        check("projects の LQE キャッシュ列を書き換えない",
              p.lead_qualification_decision is None
              and p.lead_qualification_at is None)

        gate.evaluate(db, p, persist=True)
        db.refresh(p)
        check("persist=True でも履歴は増えない",
              db.query(LeadQualification).count() == before)
        check("persist=True でも LQE キャッシュ列は触らない",
              p.lead_qualification_decision is None)
        check("persist=True は既存 4 列を更新する",
              p.gate_checked_at is not None
              and p.eligible_for_contact_search is not None)
    finally:
        db.close()


def test_evaluate_persist_false_writes_nothing():
    print("test_evaluate_persist_false_writes_nothing")
    db = SessionLocal()
    try:
        p = make_project(db)
        before = (p.eligible_for_contact_search, p.contact_search_gate_reason,
                  p.japan_crowdfunding_score, p.gate_checked_at)
        gate.evaluate(db, p, persist=False)
        check("persist=False は新規追加なし", len(db.new) == 0)
        check("persist=False は変更なし", len(db.dirty) == 0)
        check("persist=False は削除なし", len(db.deleted) == 0)
        check("既存ゲート列も変わらない",
              (p.eligible_for_contact_search, p.contact_search_gate_reason,
               p.japan_crowdfunding_score, p.gate_checked_at) == before)
    finally:
        db.close()


def test_evaluate_no_network():
    print("test_evaluate_no_network")

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    db = SessionLocal()
    orig = (socket.socket.connect, socket.socket.connect_ex,
            urllib.request.urlopen, socket.getaddrinfo)
    try:
        p = make_project(db)
        socket.socket.connect = boom
        socket.socket.connect_ex = boom
        urllib.request.urlopen = boom
        socket.getaddrinfo = boom
        try:
            gate.evaluate(db, p, persist=False)
            ok = True
        except AssertionError:
            ok = False
        finally:
            (socket.socket.connect, socket.socket.connect_ex,
             urllib.request.urlopen, socket.getaddrinfo) = orig
        check("evaluate はネットワークに触れない", ok)
    finally:
        db.close()


def test_require_eligible_behaviour():
    print("test_require_eligible_behaviour")
    db = SessionLocal()
    try:
        blocked = make_project(db, source_url=None)
        raised = False
        try:
            gate.require_eligible(db, blocked)
        except gate.GateBlocked as exc:
            raised = True
            check("GateBlocked が result を持つ", isinstance(exc.result, dict))
            check("GateBlocked の result は従来キー", set(exc.result) == LEGACY_KEYS)
        check("不合格で GateBlocked を送出する", raised)

        out = gate.require_eligible(db, blocked, override_reason="管理者判断")
        check("override で通過する", out["override"] is True)
        check("override 理由が残る", out["override_reason"] == "管理者判断")
        check("override 時のキーは従来 + override 2 キー",
              set(out) - LEGACY_KEYS == {"override", "override_reason"})
    finally:
        db.close()


def test_409_response_shape():
    print("test_409_response_shape")
    from app.routers import contact_intelligence as router

    db = SessionLocal()
    try:
        blocked = make_project(db, source_url=None)
        try:
            gate.require_eligible(db, blocked)
            result = None
        except gate.GateBlocked as exc:
            result = exc.result
        detail = router._gate_detail(result)
        expected = {
            "eligible_for_contact_search", "contact_search_gate_decision",
            "user_reasons", "blockers", "gate_checked_at", "campaign_url",
            "campaign_url_missing", "campaign_url_missing_reason",
            "official_site_url",
        }
        check("409 の gate キー集合が従来どおり", set(detail) == expected)
        check("内部スコアが漏れない",
              "japan_crowdfunding_score" not in detail
              and "japan_crowdfunding_threshold" not in detail)
        check("LQE 由来キーが漏れない",
              not any(k in detail for k in gate.LQE_DETAIL_FIELDS))
        check("gate_checked_at は ISO 文字列か None",
              detail["gate_checked_at"] is None
              or isinstance(detail["gate_checked_at"], str))
    finally:
        db.close()


def test_lqe_failure_does_not_break_gate():
    print("test_lqe_failure_does_not_break_gate")
    db = SessionLocal()
    original = lqs.gather_signals
    try:
        p = make_project(db)
        expected = gate._evaluate_gate_only(db, p)["contact_search_gate_decision"]

        def boom(*a, **k):
            raise RuntimeError("LQE broken")

        lqs.gather_signals = boom
        try:
            r = gate.evaluate(db, p, persist=False)
        finally:
            lqs.gather_signals = original
        check("LQE が落ちてもゲートは動く",
              r["contact_search_gate_decision"] == expected)
        check("LQE が落ちても戻り値キーは従来どおり", set(r) == LEGACY_KEYS)
    finally:
        lqs.gather_signals = original
        db.close()


def main():
    test_public_api_preserved()
    test_merge_blocked_downgrades()
    test_merge_review_keeps_decision()
    test_merge_clear_is_noop()
    test_merge_never_upgrades()
    test_merge_deduplicates_reasons()
    test_merge_manages_lqe_keys()
    test_merge_is_pure()
    test_evaluate_response_shape()
    test_evaluate_matches_gate_only_when_lqe_clear()
    test_evaluate_writes_no_history()
    test_evaluate_persist_false_writes_nothing()
    test_evaluate_no_network()
    test_require_eligible_behaviour()
    test_409_response_shape()
    test_lqe_failure_does_not_break_gate()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
