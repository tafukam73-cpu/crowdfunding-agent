"""評価結果を Markdown レポートにする（`run_eval.py` の出力を読むだけ）。

冒頭に **過剰除外率**、次に **誤送信許可率** を置きます。過剰除外は「営業できる
案件を止めてしまう」誤りで、画面に出ず気付けないため最重要指標です。

率はすべて **分子/分母を先に**表示します。`unresolved` は分母から除外し、
件数を別掲します。**このスクリプトはルールを修正しません**（改善候補を挙げるだけ）。

実行:
    docker compose exec -T backend python tests/lqe_eval/report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT_PATH = HERE / "generated" / "eval_result.json"
REPORT_PATH = HERE / "generated" / "eval_report.md"


def fmt(r: dict) -> str:
    return r.get("display", "N/A")


def build(result: dict) -> str:
    m = result["metrics"]
    t = result["totals"]
    L: list[str] = []
    a = L.append

    a("# LQE 除外判定 評価レポート")
    a("")
    a(f"- 生成: {result['generated_at']}")
    a(f"- 判定ルール: `{result['rule_version']}`")
    a(f"- ケース数: **{t['cases']}** / 集計対象: **{t['counted']}** / "
      f"unresolved: **{t['unresolved']}**（率の分母から除外）")
    a("")
    a("> 率は必ず `分子/分母` を先に示します。N が小さいため小数だけで判断しないでください。")
    a("> 返信率・成功率・可能性予測は算出しません。")
    a("")

    a("## 1. 過剰除外率（最重要）")
    a("")
    a(f"**{fmt(m['1_over_exclusion'])}**")
    a("")
    a("人手で「調査すべき」と判断した案件のうち、LQE が `pre_research=blocked` にした割合。")
    a("**機会損失は画面に出ないため、この指標を最優先で見ます。**")
    a("")

    a("## 2. 誤送信許可率")
    a("")
    a(f"**{fmt(m['2_wrong_outreach_allow'])}**")
    a("")
    a("人手で「送るべきでない」と判断した案件のうち、LQE が `pre_outreach=clear` にした割合。")
    a("")

    a("## 3〜6. 判定の適合率と証跡")
    a("")
    a("| 指標 | 値 |")
    a("|---|---|")
    a(f"| 3. blocker precision | {fmt(m['3_blocker_precision'])} |")
    a(f"| 4. review 適合率 | {fmt(m['4_review_precision'])} |")
    a(f"| 5. clear 適合率 | {fmt(m['5_clear_precision'])} |")
    a(f"| 6. Evidence 充足率 | {fmt(m['6_evidence_sufficiency'])} |")
    a("")
    a("Evidence 充足率は、blocker / review の Finding のうち 4 点セット"
      "（claim / source_url / checked_at / method）が揃っている割合です。")
    a("")

    a("## 7. 停止理由別件数（A〜T）")
    a("")
    counts = m["7_stop_reason_counts"]
    if counts:
        a("| コード | 件数 |")
        a("|---|---|")
        for code, n in counts.items():
            a(f"| {code} | {n} |")
    else:
        a("該当なし")
    a("")

    a("## 8. サイト別集計")
    a("")
    a("| サイト | N | pre_research | pre_outreach | pre_research 一致 | pre_outreach 一致 |")
    a("|---|---|---|---|---|---|")
    for site, d in m["8_by_site"].items():
        a(f"| {site} | {d['n']} | {d['pre_research']} | {d['pre_outreach']} | "
          f"{fmt(d['match_pre_research'])} | {fmt(d['match_pre_outreach'])} |")
    a("")

    a("## 9. stage 別集計")
    a("")
    a("| stage | 分布 | 人手ラベルとの一致 |")
    a("|---|---|---|")
    for stage, d in m["9_by_stage"].items():
        a(f"| {stage} | {d['distribution']} | {fmt(d['match'])} |")
    a("")

    a("## 10. 調査削減量")
    a("")
    red = m["10_research_reduction"]
    a(f"- `pre_research=blocked`: **{red['pre_research_blocked']} 件**")
    a(f"- うち過剰除外: **{red['over_exclusion']} 件**")
    a(f"- **純削減: {red['net_reduction_cases']} 件**")
    a("")
    a(f"> {red['note']}")
    a("")

    a("## 11〜15. その他")
    a("")
    a("| 指標 | 値 |")
    a("|---|---|")
    a(f"| 11. 人手確認必要率 | {fmt(m['11_manual_review_needed'])} |")
    a(f"| 12. internal_db 依存率 | {fmt(m['12_internal_db_dependency'])} |")
    a(f"| 13. stale 率 | {fmt(m['13_stale'])} |")
    a(f"| 14. override 必要候補率 | {fmt(m['14_override_candidate'])} |")
    a(f"| 15. 判定不能率 | {fmt(m['15_undecidable'])} |")
    a("")
    a("`internal_db 依存率` は、blocker / review の根拠が内部 DB 参照だけの案件の割合です。")
    a("高いほど「外部で確認できる証跡が取れていない」ことを意味します。")
    a("")

    def table(title: str, rows: list[dict], extra: str = "") -> None:
        a(f"## {title}（{len(rows)} 件）")
        a("")
        if extra:
            a(extra)
            a("")
        if not rows:
            a("該当なし")
            a("")
            return
        a("| case | サイト | 実際(pre_r/pre_o) | 期待(pre_r/pre_o) | 案件 |")
        a("|---|---|---|---|---|")
        for r in rows:
            a(f"| {r['case_id']} | {r['source_site']} | "
              f"{r['actual']['pre_research']}/{r['actual']['pre_outreach']} | "
              f"{r['expected']['pre_research']}/{r['expected']['pre_outreach']} | "
              f"{str(r['project_name'])[:36]} |")
        a("")

    table("過剰除外ケース", result["over_exclusion_cases"],
          "**営業できる案件を止めてしまったケース。最優先で確認してください。**")
    table("誤送信許可ケース", result["wrong_allow_cases"],
          "送るべきでない案件を `clear` にしてしまったケース。")
    table("判定が人手ラベルと不一致のケース", result["mismatches"])
    table("Evidence 不足のケース", result["evidence_gap_cases"],
          "blocker / review の Finding に 4 点セットが揃っていないもの。")

    a(f"## unresolved 一覧（{len(result['unresolved_cases'])} 件）")
    a("")
    a("人手検証が完了しておらず、**率の分母から除外**したケースです。")
    a("")
    if result["unresolved_cases"]:
        a("| case | サイト | 案件 | 理由 |")
        a("|---|---|---|---|")
        for r in result["unresolved_cases"]:
            a(f"| {r['case_id']} | {r['source_site']} | {str(r['project_name'])[:30]} | "
              f"{str(r['reviewer_reason'])[:60]} |")
    else:
        a("なし")
    a("")

    a("## 改善候補（このPRでは修正しない）")
    a("")
    a("- 過剰除外ケースがあれば、該当カテゴリの severity と証跡要件を見直す")
    a("- Evidence 不足が多いカテゴリは、証跡の取得経路を増やす")
    a("- internal_db 依存率が高い場合、外部証跡を取る工程が足りていない")
    a("- 非ラテン文字（韓国語・中国語）で規制語彙が効いていない場合は語彙を追加する")
    a("")
    a("> **本レポートは判定ルールを変更しません。** 問題は別 Issue / 別 PR として起票し、")
    a("> 修正は明示承認のうえ別 PR で行ってください（評価と修正を同じ PR に混ぜない）。")
    a("")
    return "\n".join(L)


def main() -> int:
    if not RESULT_PATH.exists():
        print(f"ERROR: 評価結果がありません: {RESULT_PATH}")
        print("  先に run_eval.py を実行してください。")
        return 1
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build(result) + "\n", encoding="utf-8")
    print(f"書き出し: {REPORT_PATH}")
    m = result["metrics"]
    print(f"  過剰除外率  : {fmt(m['1_over_exclusion'])}")
    print(f"  誤送信許可率: {fmt(m['2_wrong_outreach_allow'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
