"""LQE 除外判定の評価を実行する（**DB 非依存・純粋関数のみ**）。

`cases.json`（signals スナップショット）と `ground_truth.json`（人手ラベル）を
突き合わせ、15 指標を算出して JSON で出力します。

制約（テストで固定）:
  - **DB へアクセスしない**（`gather_signals` / `run` を呼ばない）
  - **外部 HTTP を行わない**
  - **`qualify()` を直接呼ぶ**
  - **Ground Truth を書き換えない**
  - 返信率・成功率・可能性予測を算出しない

率はすべて **分子/分母を先に**表示します（N=30 なので小数だけで誤解させない）。
`unresolved` は分母から除外し、件数を別途報告します。

実行:
    docker compose exec -T backend python tests/lqe_eval/run_eval.py
    docker compose exec -T backend python tests/lqe_eval/run_eval.py --allow-incomplete
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND))
# DB を使わないので、接続先はメモリ上の sqlite で十分（実際には接続しない）。
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.services import lead_qualification_service as lqs  # noqa: E402

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.json"
GT_PATH = HERE / "ground_truth.json"
RESULT_PATH = HERE / "generated" / "eval_result.json"

EXPECTED_TOTAL = 30
EXPECTED_SITES = {"kickstarter": 10, "indiegogo": 5, "wadiz": 10, "zeczec": 5}
VALID_STATUS = {"verified", "partially_verified", "unresolved"}
#: 率の分母に入れてよい検証状態（unresolved は除外する）。
COUNTED_STATUS = {"verified", "partially_verified"}

#: レスポンス/結果に現れてはいけない語（予測値の禁止）。
BANNED_WORDS = ("返信率", "成功率", "成功確率", "可能性スコア", "予測値")

GT_REQUIRED = (
    "case_id", "expected_pre_research_decision", "expected_pre_outreach_decision",
    "expected_blocker_codes", "expected_review_codes", "should_research",
    "should_allow_outreach", "evidence_sufficient", "reviewer_reason",
    "reviewed_at", "reviewer", "verification_status", "evidence_urls",
    "evidence_notes",
)


class EvalError(Exception):
    """評価を続行できない（非 0 終了する）。"""


# --------------------------------------------------------------------------- #
#  率の表現（分子/分母を必ず持つ）
# --------------------------------------------------------------------------- #
def ratio(numerator: int, denominator: int) -> dict:
    """分子・分母・表示文字列を持つ率。分母 0 は N/A。"""
    if denominator <= 0:
        return {"numerator": numerator, "denominator": 0, "display": "N/A（分母0）",
                "percent": None}
    pct = round(numerator / denominator * 100, 1)
    return {"numerator": numerator, "denominator": denominator,
            "display": f"{numerator}/{denominator}（{pct}%）", "percent": pct}


# --------------------------------------------------------------------------- #
#  入力の検証
# --------------------------------------------------------------------------- #
def load_inputs() -> tuple[list[dict], dict[str, dict]]:
    if not CASES_PATH.exists():
        raise EvalError(f"fixture がありません: {CASES_PATH}")
    if not GT_PATH.exists():
        raise EvalError(f"Ground Truth がありません: {GT_PATH}")
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    gt_rows = json.loads(GT_PATH.read_text(encoding="utf-8"))
    gt = {r["case_id"]: r for r in gt_rows}

    if len(cases) != EXPECTED_TOTAL:
        raise EvalError(f"ケース数が {len(cases)} 件（{EXPECTED_TOTAL} 件であるべき）")
    sites = Counter(c["source_site"] for c in cases)
    if dict(sites) != EXPECTED_SITES:
        raise EvalError(f"サイト構成が不一致: {dict(sites)} != {EXPECTED_SITES}")
    ids = [c["case_id"] for c in cases]
    if len(set(ids)) != len(ids):
        raise EvalError("case_id が重複している")
    keys = [c["canonical_maker_key"] for c in cases]
    if len(set(keys)) != len(keys):
        dup = [k for k, n in Counter(keys).items() if n > 1]
        raise EvalError(f"canonical_maker_key が重複: {dup}")
    if set(ids) != set(gt):
        raise EvalError(
            f"case_id が cases と ground_truth で不一致 "
            f"(cases のみ: {sorted(set(ids) - set(gt))} / GT のみ: {sorted(set(gt) - set(ids))})"
        )
    for cid, row in gt.items():
        missing = [f for f in GT_REQUIRED if f not in row]
        if missing:
            raise EvalError(f"{cid}: Ground Truth に必須項目が無い {missing}")
        if row["verification_status"] not in VALID_STATUS:
            raise EvalError(f"{cid}: verification_status が不正 {row['verification_status']}")
        if not str(row.get("reviewer_reason") or "").strip():
            raise EvalError(f"{cid}: reviewer_reason が空")
        if not str(row.get("reviewer") or "").strip():
            raise EvalError(f"{cid}: reviewer が空")
        if not str(row.get("reviewed_at") or "").strip():
            raise EvalError(f"{cid}: reviewed_at が空")
        if not (row.get("evidence_urls") or row.get("evidence_notes")):
            raise EvalError(f"{cid}: evidence_urls が空の場合は evidence_notes に理由が必要")
    return cases, gt


# --------------------------------------------------------------------------- #
#  評価
# --------------------------------------------------------------------------- #
def evaluate(cases: list[dict], gt: dict[str, dict]) -> dict:
    now = datetime.now(timezone.utc)
    per_case: list[dict] = []

    for c in cases:
        signals = c["signals_snapshot"]
        pre_r = lqs.qualify(signals, lqs.STAGE_PRE_RESEARCH, now=now)
        pre_o = lqs.qualify(signals, lqs.STAGE_PRE_OUTREACH, now=now)
        g = gt[c["case_id"]]

        blocking = [f for f in pre_r.findings + pre_o.findings
                    if f.severity in (lqs.SEVERITY_BLOCKER, lqs.SEVERITY_REVIEW)]
        complete = [f for f in blocking if f.complete_evidence()]
        internal_only = [
            f for f in blocking
            if f.complete_evidence()
            and all(e.is_state_evidence() for e in f.complete_evidence())
        ]
        stale = [f for f in pre_r.findings + pre_o.findings
                 if f.verdict == lqs.VERDICT_STALE]

        per_case.append({
            "case_id": c["case_id"],
            "source_site": c["source_site"],
            "project_name": c["project_name"],
            "campaign_age_bucket": c["campaign_age_bucket"],
            "verification_status": g["verification_status"],
            "counted": g["verification_status"] in COUNTED_STATUS,
            "actual": {
                "pre_research": pre_r.decision,
                "pre_outreach": pre_o.decision,
                "blocker_codes": list(pre_r.blocker_codes),
                "review_codes": list(pre_r.review_codes),
                "pre_outreach_blocker_codes": list(pre_o.blocker_codes),
                "pre_outreach_review_codes": list(pre_o.review_codes),
                "evidence_count": pre_r.evidence_count,
            },
            "expected": {
                "pre_research": g["expected_pre_research_decision"],
                "pre_outreach": g["expected_pre_outreach_decision"],
                "blocker_codes": list(g["expected_blocker_codes"]),
                "review_codes": list(g["expected_review_codes"]),
                "should_research": g["should_research"],
                "should_allow_outreach": g["should_allow_outreach"],
                "evidence_sufficient": g["evidence_sufficient"],
            },
            "match_pre_research": pre_r.decision == g["expected_pre_research_decision"],
            "match_pre_outreach": pre_o.decision == g["expected_pre_outreach_decision"],
            "blocking_findings": len(blocking),
            "blocking_with_evidence": len(complete),
            "internal_db_only_findings": len(internal_only),
            "stale_findings": len(stale),
            "reviewer_reason": g["reviewer_reason"],
        })

    counted = [r for r in per_case if r["counted"]]
    unresolved = [r for r in per_case if not r["counted"]]
    n = len(counted)

    # --- 1. 過剰除外率（最重要） ---
    # **`None`（人手でも判断できなかった）は分母に入れません。** True/False だけを数えます。
    should_research = [r for r in counted if r["expected"]["should_research"] is True]
    over = [r for r in should_research if r["actual"]["pre_research"] == "blocked"]
    # --- 2. 誤送信許可率 ---
    should_not_allow = [r for r in counted
                        if r["expected"]["should_allow_outreach"] is False]
    wrong_allow = [r for r in should_not_allow if r["actual"]["pre_outreach"] == "clear"]
    # --- 3-5. decision 別の適合率（pre_research） ---
    def agree(dec: str) -> tuple[int, int]:
        got = [r for r in counted if r["actual"]["pre_research"] == dec]
        return sum(1 for r in got if r["expected"]["pre_research"] == dec), len(got)

    b_hit, b_all = agree("blocked")
    r_hit, r_all = agree("review")
    c_hit, c_all = agree("clear")
    # --- 6. Evidence 充足率 ---
    ev_num = sum(r["blocking_with_evidence"] for r in counted)
    ev_den = sum(r["blocking_findings"] for r in counted)
    # --- 7. 停止理由別件数（A〜T） ---
    codes = Counter()
    for r in counted:
        for code in r["actual"]["blocker_codes"] + r["actual"]["review_codes"]:
            codes[code] += 1
    # --- 8. サイト別 ---
    by_site: dict[str, dict] = {}
    for site in EXPECTED_SITES:
        rows = [r for r in counted if r["source_site"] == site]
        by_site[site] = {
            "n": len(rows),
            "pre_research": dict(Counter(r["actual"]["pre_research"] for r in rows)),
            "pre_outreach": dict(Counter(r["actual"]["pre_outreach"] for r in rows)),
            "match_pre_research": ratio(sum(1 for r in rows if r["match_pre_research"]),
                                        len(rows)),
            "match_pre_outreach": ratio(sum(1 for r in rows if r["match_pre_outreach"]),
                                        len(rows)),
        }
    # --- 9. stage 別 ---
    by_stage = {
        "pre_research": {
            "distribution": dict(Counter(r["actual"]["pre_research"] for r in counted)),
            "match": ratio(sum(1 for r in counted if r["match_pre_research"]), n),
        },
        "pre_outreach": {
            "distribution": dict(Counter(r["actual"]["pre_outreach"] for r in counted)),
            "match": ratio(sum(1 for r in counted if r["match_pre_outreach"]), n),
        },
    }
    # --- 10. 調査削減量 ---
    blocked_pre_r = [r for r in counted if r["actual"]["pre_research"] == "blocked"]
    net_reduction = len(blocked_pre_r) - len(over)
    # --- 11. 人手確認必要率 ---
    need_review = [r for r in counted if r["actual"]["pre_research"] == "review"]
    # --- 12. internal_db 依存率 ---
    internal_dep = [r for r in counted if r["internal_db_only_findings"] > 0]
    # --- 13. stale 率 ---
    stale_cases = [r for r in counted if r["stale_findings"] > 0]
    # --- 14. override 必要候補率 ---
    allow_expected = [r for r in counted
                      if r["expected"]["should_allow_outreach"] is True]
    override_needed = [r for r in allow_expected
                       if r["actual"]["pre_outreach"] != "clear"]
    # --- 15. 判定不能率 ---
    undecidable = [r for r in counted if not r["expected"]["evidence_sufficient"]]

    return {
        "generated_at": now.isoformat(),
        "rule_version": lqs.RULE_VERSION,
        "totals": {
            "cases": len(per_case),
            "counted": n,
            "unresolved": len(unresolved),
            "unresolved_case_ids": [r["case_id"] for r in unresolved],
            "should_research_unknown": sum(
                1 for r in counted if r["expected"]["should_research"] is None),
            "should_allow_outreach_unknown": sum(
                1 for r in counted if r["expected"]["should_allow_outreach"] is None),
        },
        "metrics": {
            "1_over_exclusion": ratio(len(over), len(should_research)),
            "2_wrong_outreach_allow": ratio(len(wrong_allow), len(should_not_allow)),
            "3_blocker_precision": ratio(b_hit, b_all),
            "4_review_precision": ratio(r_hit, r_all),
            "5_clear_precision": ratio(c_hit, c_all),
            "6_evidence_sufficiency": ratio(ev_num, ev_den),
            "7_stop_reason_counts": dict(sorted(codes.items())),
            "8_by_site": by_site,
            "9_by_stage": by_stage,
            "10_research_reduction": {
                "pre_research_blocked": len(blocked_pre_r),
                "over_exclusion": len(over),
                "net_reduction_cases": net_reduction,
                "note": ("回避できた調査件数から過剰除外を差し引いた純削減件数。"
                         "処理時間の換算は同一環境での実測が無いため出さない。"),
            },
            "11_manual_review_needed": ratio(len(need_review), n),
            "12_internal_db_dependency": ratio(len(internal_dep), n),
            "13_stale": ratio(len(stale_cases), n),
            "14_override_candidate": ratio(len(override_needed), len(allow_expected)),
            "15_undecidable": ratio(len(undecidable), n),
        },
        "mismatches": [r for r in counted
                       if not (r["match_pre_research"] and r["match_pre_outreach"])],
        "over_exclusion_cases": over,
        "wrong_allow_cases": wrong_allow,
        "evidence_gap_cases": [r for r in counted
                               if r["blocking_findings"] > r["blocking_with_evidence"]],
        "unresolved_cases": unresolved,
        "per_case": per_case,
    }


def gt_progress(gt: dict[str, dict]) -> tuple[int, int]:
    reviewed = sum(1 for r in gt.values()
                   if r.get("verification_status") in VALID_STATUS
                   and str(r.get("reviewer") or "").strip())
    verified = sum(1 for r in gt.values() if r.get("verification_status") == "verified")
    return verified, reviewed


def main() -> int:
    allow_incomplete = "--allow-incomplete" in sys.argv
    try:
        cases, gt = load_inputs()
    except EvalError as exc:
        print(f"ERROR: {exc}")
        return 1

    verified, reviewed = gt_progress(gt)
    total = len(gt)
    print(f"Ground Truth: verified {verified}/{total} / reviewed {reviewed}/{total}")
    if verified < total and not allow_incomplete:
        print(f"Ground Truth incomplete: {verified}/{total} reviewed")
        print("  人手レビューが完了していないため評価を成功終了させません。")
        print("  途中経過を見るには --allow-incomplete を付けてください。")
        return 2

    result = evaluate(cases, gt)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    text = json.dumps(result, ensure_ascii=False)
    leaked = [w for w in BANNED_WORDS if w in text.replace("予測値は出さない", "")]
    if leaked:
        print(f"ERROR: 禁止語が結果に含まれる: {leaked}")
        return 1

    m = result["metrics"]
    t = result["totals"]
    print(f"\n=== LQE 評価（N={t['cases']} / 集計対象 {t['counted']} / "
          f"unresolved {t['unresolved']}） ===")
    print(f"  1. 過剰除外率        : {m['1_over_exclusion']['display']}")
    print(f"  2. 誤送信許可率      : {m['2_wrong_outreach_allow']['display']}")
    print(f"  3. blocker precision : {m['3_blocker_precision']['display']}")
    print(f"  4. review 適合率     : {m['4_review_precision']['display']}")
    print(f"  5. clear 適合率      : {m['5_clear_precision']['display']}")
    print(f"  6. Evidence 充足率   : {m['6_evidence_sufficiency']['display']}")
    print(f" 10. 純削減件数        : {m['10_research_reduction']['net_reduction_cases']} 件"
          f"（blocked {m['10_research_reduction']['pre_research_blocked']} - "
          f"過剰除外 {m['10_research_reduction']['over_exclusion']}）")
    print(f" 11. 人手確認必要率    : {m['11_manual_review_needed']['display']}")
    print(f" 12. internal_db 依存率: {m['12_internal_db_dependency']['display']}")
    print(f" 13. stale 率          : {m['13_stale']['display']}")
    print(f" 14. override 候補率   : {m['14_override_candidate']['display']}")
    print(f" 15. 判定不能率        : {m['15_undecidable']['display']}")
    print(f"\n書き出し: {RESULT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
