"""LQE 除外判定 評価ハーネス（PR-8）の検証。

ネットワーク不要・DB 不要。pytest には依存しない（CLAUDE.md §7）。

検証の重点:
  - fixture が 30 件・サイト構成 10/5/10/5・case_id と canonical_maker_key が一意
  - **評価が DB に触れない**（`gather_signals` / `run` を呼ばない）
  - **`qualify()` を直接使う**
  - Ground Truth の必須項目と検証状態
  - **unresolved を率の分母に混ぜない** / 分母 0 は N/A
  - 15 指標がそろい、分子/分母を持つ
  - **再実行で同一結果**
  - LQE 本体・contact_intel_eval を変更していない

実行: docker compose exec -T backend python tests/test_lqe_eval.py
"""
from __future__ import annotations

import inspect
import json
import os
import socket
import sys
import urllib.request
from collections import Counter
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests" / "lqe_eval"))

import run_eval  # noqa: E402
import report as report_mod  # noqa: E402
from app.services import lead_qualification_service as lqs  # noqa: E402

EVAL_DIR = BACKEND / "tests" / "lqe_eval"
CASES = json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))
GT = json.loads((EVAL_DIR / "ground_truth.json").read_text(encoding="utf-8"))

_passed = _failed = 0

BANNED_WORDS = ("返信率", "成功率", "成功確率", "可能性スコア")


def _strip_module_docstring(text: str) -> str:
    """先頭のモジュール docstring を除いた本体を返す（説明文への誤ヒットを避ける）。"""
    marker = '"""'
    first = text.find(marker)
    if first < 0:
        return text
    second = text.find(marker, first + 3)
    return text[second + 3:] if second > 0 else text


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


# --------------------------------------------------------------------------- #
#  1. fixture
# --------------------------------------------------------------------------- #
def test_fixture_shape():
    print("test_fixture_shape")
    check("ケースは 30 件", len(CASES) == 30)
    sites = Counter(c["source_site"] for c in CASES)
    check("サイト構成 10/5/10/5",
          dict(sites) == {"kickstarter": 10, "indiegogo": 5, "wadiz": 10, "zeczec": 5})
    ids = [c["case_id"] for c in CASES]
    check("case_id が一意", len(set(ids)) == len(ids))
    keys = [c["canonical_maker_key"] for c in CASES]
    check("canonical_maker_key が一意", len(set(keys)) == len(keys))
    check("canonical_maker_key が空でない", all(k for k in keys))
    check("key_source が記録されている", all(c.get("key_source") for c in CASES))
    check("canonical_maker_key に project_id を使っていない",
          not any(str(c["source_project_id"]) in c["canonical_maker_key"]
                  for c in CASES))
    for field in ("case_id", "source_project_id", "source_site", "project_snapshot",
                  "signals_snapshot", "canonical_maker_key", "key_source",
                  "campaign_age_bucket", "selected_at", "selector_version"):
        check(f"fixture に {field} がある", all(field in c for c in CASES))
    buckets = Counter(c["campaign_age_bucket"] for c in CASES)
    check("新旧区分は ended/live/unknown のみ",
          set(buckets) <= {"ended", "live", "unknown"})
    check("unknown をどちらかへ丸めていない", buckets.get("unknown", 0) > 0)
    for site in ("kickstarter", "indiegogo", "wadiz"):
        sub = {c["campaign_age_bucket"] for c in CASES if c["source_site"] == site}
        check(f"{site} は新旧が混在している（ended と live）",
              {"ended", "live"} <= sub)
    check("maker_name の重複が無い",
          len({(c["maker_name"] or "").strip().lower()
               for c in CASES if c["maker_name"]})
          == len([c for c in CASES if c["maker_name"]]))


def test_fixture_is_self_contained():
    """fixture だけで qualify() を再実行できる（project_id に依存しない）。"""
    print("test_fixture_is_self_contained")
    ok = True
    for c in CASES:
        try:
            r = lqs.qualify(c["signals_snapshot"], lqs.STAGE_PRE_RESEARCH)
            o = lqs.qualify(c["signals_snapshot"], lqs.STAGE_PRE_OUTREACH)
            if r.decision not in ("blocked", "review", "clear"):
                ok = False
            if len(r.findings) != 20 or len(o.findings) != 20:
                ok = False
        except Exception:
            ok = False
    check("全ケースで qualify() を再実行できる", ok)
    check("signals_snapshot は JSON 化済み（datetime を含まない）",
          all(isinstance(json.dumps(c["signals_snapshot"]), str) for c in CASES))


# --------------------------------------------------------------------------- #
#  2. 副作用が無いこと
# --------------------------------------------------------------------------- #
def test_no_db_and_no_http():
    print("test_no_db_and_no_http")
    # docstring は「gather_signals を呼ばない」等の説明を含むため、本体だけを走査する。
    src_full = (EVAL_DIR / "run_eval.py").read_text(encoding="utf-8")
    src = _strip_module_docstring(src_full)
    for banned in ("gather_signals", "lqs.run(", "SessionLocal", "db.query",
                   "httpx", "requests.", "urllib.request", "playwright"):
        check(f"run_eval の本体に {banned} を含まない", banned not in src)
    check("qualify() を直接使っている", "lqs.qualify(" in src)

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    orig = (socket.socket.connect, socket.getaddrinfo, urllib.request.urlopen)
    socket.socket.connect = boom
    socket.getaddrinfo = boom
    urllib.request.urlopen = boom
    try:
        cases, gt = run_eval.load_inputs()
        run_eval.evaluate(cases, gt)
        ok = True
    except AssertionError:
        ok = False
    finally:
        (socket.socket.connect, socket.getaddrinfo, urllib.request.urlopen) = orig
    check("評価はネットワークに触れない", ok)

    sel = (EVAL_DIR / "_select_cases.py").read_text(encoding="utf-8")
    check("選定ツールは DB 書き込みをしない",
          "db.add(" not in sel and "db.commit(" not in sel)
    check("選定ツールは run() を呼ばない", "lqs.run(" not in sel)
    check("選定ツールは Ground Truth を書き換えない", "ground_truth" not in sel)


# --------------------------------------------------------------------------- #
#  3. Ground Truth
# --------------------------------------------------------------------------- #
def test_ground_truth():
    print("test_ground_truth")
    check("Ground Truth は 30 件", len(GT) == 30)
    check("case_id が cases と一致",
          {r["case_id"] for r in GT} == {c["case_id"] for c in CASES})
    for field in run_eval.GT_REQUIRED:
        check(f"全件に {field} がある", all(field in r for r in GT))
    check("reviewer_reason が空でない",
          all(str(r["reviewer_reason"]).strip() for r in GT))
    check("reviewer が空でない", all(str(r["reviewer"]).strip() for r in GT))
    check("reviewed_at が空でない", all(str(r["reviewed_at"]).strip() for r in GT))
    check("verification_status が妥当",
          all(r["verification_status"] in run_eval.VALID_STATUS for r in GT))
    check("evidence_urls が空なら evidence_notes に理由がある",
          all(r["evidence_urls"] or str(r["evidence_notes"]).strip() for r in GT))
    check("should_research は True/False/None のみ",
          all(r["should_research"] in (True, False, None) for r in GT))
    check("should_allow_outreach は True/False/None のみ",
          all(r["should_allow_outreach"] in (True, False, None) for r in GT))
    check("**人の承認前は verified にしない**",
          not any(r["verification_status"] == "verified" for r in GT))
    check("unresolved が存在する（不明を丸めていない）",
          any(r["verification_status"] == "unresolved" for r in GT))


# --------------------------------------------------------------------------- #
#  4. 指標
# --------------------------------------------------------------------------- #
def test_metrics():
    print("test_metrics")
    cases, gt = run_eval.load_inputs()
    result = run_eval.evaluate(cases, gt)
    m = result["metrics"]
    for i, key in enumerate([
        "1_over_exclusion", "2_wrong_outreach_allow", "3_blocker_precision",
        "4_review_precision", "5_clear_precision", "6_evidence_sufficiency",
        "7_stop_reason_counts", "8_by_site", "9_by_stage", "10_research_reduction",
        "11_manual_review_needed", "12_internal_db_dependency", "13_stale",
        "14_override_candidate", "15_undecidable",
    ], start=1):
        check(f"指標 {i} ({key}) がある", key in m)
    check("15 指標そろっている", len(m) == 15)

    for key in ("1_over_exclusion", "2_wrong_outreach_allow", "3_blocker_precision",
                "4_review_precision", "5_clear_precision", "6_evidence_sufficiency",
                "11_manual_review_needed", "12_internal_db_dependency", "13_stale",
                "14_override_candidate", "15_undecidable"):
        r = m[key]
        check(f"{key} が分子/分母を持つ",
              "numerator" in r and "denominator" in r and "display" in r)

    counted = result["totals"]["counted"]
    unresolved = result["totals"]["unresolved"]
    check("集計対象＋unresolved = 全件", counted + unresolved == len(cases))
    check("unresolved を分母に入れていない",
          all(m[k]["denominator"] <= counted
              for k in ("11_manual_review_needed", "12_internal_db_dependency",
                        "13_stale", "15_undecidable")))
    check("分母0は N/A 表示", run_eval.ratio(0, 0)["display"].startswith("N/A"))
    check("分母0は percent=None", run_eval.ratio(0, 0)["percent"] is None)
    check("ratio は分子/分母を先に出す", run_eval.ratio(3, 4)["display"].startswith("3/4"))
    check("サイト別集計が 4 サイト", len(m["8_by_site"]) == 4)
    check("stage 別集計が 2 stage", len(m["9_by_stage"]) == 2)
    check("純削減は過剰除外を差し引く",
          m["10_research_reduction"]["net_reduction_cases"]
          == m["10_research_reduction"]["pre_research_blocked"]
          - m["10_research_reduction"]["over_exclusion"])
    check("時間換算を主指標にしていない",
          "実測が無い" in m["10_research_reduction"]["note"]
          or "実測" in m["10_research_reduction"]["note"])

    text = json.dumps(result, ensure_ascii=False)
    for w in BANNED_WORDS:
        check(f"結果に '{w}' を含まない", w not in text)


def test_tri_state_not_folded():
    """`None`（人手でも不明）を True/False に丸めない。"""
    print("test_tri_state_not_folded")
    cases, gt = run_eval.load_inputs()
    result = run_eval.evaluate(cases, gt)
    counted = [r for r in result["per_case"] if r["counted"]]
    unknown_research = sum(1 for r in counted
                           if r["expected"]["should_research"] is None)
    unknown_allow = sum(1 for r in counted
                        if r["expected"]["should_allow_outreach"] is None)
    check("should_research の不明件数を報告する",
          result["totals"]["should_research_unknown"] == unknown_research)
    check("should_allow_outreach の不明件数を報告する",
          result["totals"]["should_allow_outreach_unknown"] == unknown_allow)
    m = result["metrics"]
    check("過剰除外率の分母に不明を含めない",
          m["1_over_exclusion"]["denominator"] == len(counted) - unknown_research
          - sum(1 for r in counted if r["expected"]["should_research"] is False))
    check("誤送信許可率の分母に不明を含めない",
          m["2_wrong_outreach_allow"]["denominator"]
          == sum(1 for r in counted
                 if r["expected"]["should_allow_outreach"] is False))


def test_reproducible():
    print("test_reproducible")
    cases, gt = run_eval.load_inputs()
    a = run_eval.evaluate(cases, gt)
    b = run_eval.evaluate(cases, gt)
    a.pop("generated_at"); b.pop("generated_at")
    check("再実行で同一結果",
          json.dumps(a, ensure_ascii=False, sort_keys=True)
          == json.dumps(b, ensure_ascii=False, sort_keys=True))


def test_incomplete_ground_truth_fails():
    """人手レビュー未完了なら run_eval は成功終了しない。"""
    print("test_incomplete_ground_truth_fails")
    verified, reviewed = run_eval.gt_progress({r["case_id"]: r for r in GT})
    check("verified 件数を数えられる", isinstance(verified, int))
    check("現在は未完了（verified < 30）", verified < 30)
    src = (EVAL_DIR / "run_eval.py").read_text(encoding="utf-8")
    check("未完了時に非0終了する分岐がある", "Ground Truth incomplete" in src)
    check("非0終了コードを返す", "return 2" in src)


def test_report_generation():
    print("test_report_generation")
    cases, gt = run_eval.load_inputs()
    result = run_eval.evaluate(cases, gt)
    md = report_mod.build(result)
    check("Markdown が生成される", md.startswith("# LQE 除外判定 評価レポート"))
    check("冒頭に過剰除外率がある",
          md.index("過剰除外率") < md.index("誤送信許可率"))
    for section in ("停止理由別件数", "サイト別集計", "stage 別集計",
                    "調査削減量", "unresolved 一覧", "改善候補"):
        check(f"'{section}' セクションがある", section in md)
    check("ルールを修正しない旨を明記", "判定ルールを変更しません" in md)
    # 「返信率・成功率・可能性予測は算出しません」という**禁止の明記**は許容し、
    # それ以外の箇所に禁止語が出ないことを確認する。
    body = md.replace("返信率・成功率・可能性予測は算出しません。", "")
    for w in BANNED_WORDS:
        check(f"レポート本文に '{w}' を含まない（禁止の明記を除く）", w not in body)


# --------------------------------------------------------------------------- #
#  5. 変更範囲
# --------------------------------------------------------------------------- #
def test_scope_untouched():
    print("test_scope_untouched")
    lqe_src = (BACKEND / "app" / "services"
               / "lead_qualification_service.py").read_text(encoding="utf-8")
    check("LQE 本体に評価用の分岐を入れていない",
          "lqe_eval" not in lqe_src and "ground_truth" not in lqe_src)
    check("評価ディレクトリは contact_intel_eval と別",
          EVAL_DIR.name == "lqe_eval"
          and (BACKEND / "tests" / "contact_intel_eval").exists())
    protected = [
        "WORKLOG_official_site_fp.md", "_final_compare.py", "_measure_eval30.py",
        "_probe_eval.py", "_reanalyze_prefix.py", "_report_live.py",
        "_report_phase2.py", "_report_v2.py", "_select_eval30.py",
    ]
    ci = BACKEND / "tests" / "contact_intel_eval"
    check("保護9件が contact_intel_eval 側に残っている",
          all((ci / p).exists() for p in protected))
    check("lqe_eval 側に保護9件と同名のファイルを作っていない",
          not any((EVAL_DIR / p).exists() for p in protected))
    gen = EVAL_DIR / "generated"
    check("生成物は generated/ 配下に出す",
          "generated" in (EVAL_DIR / "run_eval.py").read_text(encoding="utf-8"))
    if gen.exists():
        check("generated/ は .gitignore で除外されている",
              (EVAL_DIR / ".gitignore").exists()
              and "generated/" in (EVAL_DIR / ".gitignore").read_text(encoding="utf-8"))


def main():
    test_fixture_shape()
    test_fixture_is_self_contained()
    test_no_db_and_no_http()
    test_ground_truth()
    test_metrics()
    test_tri_state_not_folded()
    test_reproducible()
    test_incomplete_ground_truth_fails()
    test_report_generation()
    test_scope_untouched()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
