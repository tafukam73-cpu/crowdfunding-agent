---
name: ground-truth-audit
description: Contact Intelligence の評価用 Ground Truth を人手検証ベースで健全に保ち、gold 汚染（実装の出力を正解として取り込む）を防ぐ。「精度を測って」「評価して」「gold set」「ground truth」「eval を回して」「本当に改善した？」と言われたときに使う。backend/tests/contact_intel_eval/ の既存ハーネスを正本とし、未追跡9ファイルには触れない。
---

# Ground Truth 監査（Ground Truth Audit）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 最重要：未追跡9ファイルに触れない

`backend/tests/contact_intel_eval/` の以下は、**明示指定がない限り変更・削除・stage・commit・rename 禁止**（CLAUDE.md §6）。

```
WORKLOG_official_site_fp.md  _final_compare.py     _measure_eval30.py
_probe_eval.py               _reanalyze_prefix.py  _report_live.py
_report_phase2.py            _report_v2.py         _select_eval30.py
```

**`git add -A` / `git add .` / `git commit -a` は使用禁止**です。パスを明示して stage してください。

## 既存ハーネス（追跡済み・これを使う）

```
backend/tests/contact_intel_eval/
  harness.py                      # 実行基盤
  build_ground_truth.py           # GT 構築（人手検証ベース）
  gold_set_v1.py                  # gold セット
  build_gold_candidates.py        # gold 候補の生成
  eval_v2.py                      # 評価本体
  eval_phase1_beforeafter.py      # 改善前後比較
  baseline.py                     # ベースライン
  build_real100_sample.py         # 実データサンプル
  review_real100_ground_truth.py  # 人手レビュー
  new_discovery.py
```

## gold 汚染とは（過去に実際に起きた失敗）

> **実装の出力を、そのまま正解（gold）として取り込むこと。**

こうすると、実装が間違っていても評価は満点になります。**精度が上がったように見えて、実際は何も改善していない**という最悪の状態です。

### 汚染を防ぐ規律

| 禁止 | 正しいやり方 |
|---|---|
| 実装が出した `official_site_url` を gold にコピーする | **人が一次ソースを見て**正解を決める |
| 評価に落ちた項目を「実装が正しいので gold を直す」 | まず実装を疑う。gold を直すなら**根拠URLを添えて**別途記録 |
| gold の由来を記録しない | 誰が・いつ・どのURLを見て決めたかを残す |
| 実装変更と同じ PR で gold を更新する | **別 PR に分ける**（1 PR = 1変更軸、CLAUDE.md §3） |

**gold を更新するときは、必ずユーザーの明示承認を得てください。**
gold の書き換えは評価結果を作り変える行為です。

## GT の1レコードに必要なもの

[evidence-ledger](../evidence-ledger/SKILL.md) の4点セットに加えて:

- **verified_by**: 誰が検証したか（人手 / 自動のどちらか）
- **verified_at**: 検証日時
- **source_url**: 一次ソースのURL
- **verdict の理由**: なぜそれが正解と言えるか

「正解が分からない」項目は **`unknown` として残す**のが正しい扱いです。
埋めるために推測すると、それ自体が汚染になります（CLAUDE.md §5「推測で Ground Truth を確定しない」）。

## 評価の実行

テストは pytest ではなくスクリプト直接実行です（CLAUDE.md §7）。

```bash
docker compose exec -T backend python tests/contact_intel_eval/eval_v2.py
docker compose exec -T backend python tests/test_gold_ground_truth.py
```

関連テスト: `test_gold_ground_truth.py` / `test_official_recall_corroborated_domain.py` /
`test_email_extraction_fixtures.py` / `test_source_ownership.py` / `test_web_research_ownership.py`

**注意（CLAUDE.md §7）**: これらのテストは自前の `check()` ヘルパを使い、失敗しても例外を投げません。
**合否は終了コードで判定してください。** 出力の "ok" だけを見て通ったと判断しないこと。

## 指標の読み方

| 指標 | 意味 | 営業上の影響 |
|---|---|---|
| **precision** | 出したもののうち正しい割合 | 低い＝**誤った連絡先を送る**。最優先で守る |
| **recall** | 正解のうち拾えた割合 | 低い＝機会損失。precision より優先度は下 |

**このプロジェクトでは precision を優先します。**
誤送信のコストが取りこぼしのコストより高いためです（CLAUDE.md §1）。
recall を上げるために precision を犠牲にする変更は、明示承認なしに行わないでください。

## 改善を主張するときの作法

```
主張: official_site 判定の precision が改善した

  前: precision X (n=NN)   ← baseline.py / eval_phase1_beforeafter.py の実測
  後: precision Y (n=NN)   ← 同一 GT・同一サンプルで再実行
  GT: 変更なし（gold 更新を伴わないこと）
  実行: 2026-08-05T07:20:00Z / eval_v2.py
```

**GT を変えた前後の数値を比較しないでください。** 比較になりません。

## 禁止事項

- 未追跡9ファイルへの変更・stage・commit
- 実装出力の gold への取り込み
- 承認なしの gold 更新
- 実装変更と gold 更新の同一 PR 化
- 終了コードを見ずに「テスト通過」と報告する
- precision を下げる変更を無承認で入れる

## 関連

- [official-site-verify](../official-site-verify/SKILL.md) / [email-ownership-verify](../email-ownership-verify/SKILL.md)
- [safe-dev-pr](../safe-dev-pr/SKILL.md)（PR 分割の作法）
