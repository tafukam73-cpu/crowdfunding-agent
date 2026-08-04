---
name: sales-pipeline-run
description: 1案件を「発見→除外判定→商品把握→maker同定→公式サイト確定→メール検証→意思決定者→送信判定」まで正しい順序で通す統括手順。「この案件を進めて」「営業パイプラインを回して」「最初から最後までやって」「一括で処理して」「どこまで進んでる？」と言われたときに使う。工程を飛ばさないこと、前工程が未確定なら後工程に進まないことが要点。
---

# 営業パイプライン実行（Sales Pipeline Run）

個別スキルを**正しい順序で**通すための統括手順です。
前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 依存関係（前工程が未確定なら進まない）

```
[1] lead-disqualify          除外判定           ← まずここ。無駄な調査を止める
      ↓ eligible のみ通過
[2] product-page-capture     商品ページ取得
      ↓
[3] product-facts-extract    事実抽出
      ↓
[4] jp-market-fit            日本市場適性       ← 仕様が無いと規制判定不能
      ↓
[5] makuake-fit              Makuake 適性
      ↓
[5b] competitor-intelligence 国内競合・流通・代理店  ← 流通/代理店を発見したら [1] へ差し戻し
      ↓
[6] maker-identity-verify    メーカー本人確認
      ↓  （非ラテン語名なら → nonlatin-maker-resolve）
[6b] manufacturer-reputation メーカー信頼性・発送実績 ← maker 同定後にのみ実行
[7] official-site-verify     公式サイト確定     ← ここが確定しないと [8] は不可能
      ↓
[8] email-ownership-verify   メール所有者検証
      ↓
[9] email-role-classify      役割・正当性
      ↓
[10] decision-maker-hunt     意思決定者探索     （任意だが返信率に最も効く）
      ↓
[11] outreach-gate           送信前最終関門     ← 全チェック通過で初めて承認を求める
      ↓  （ユーザーの明示承認）
     送信 → [12] reply-rate-analytics で計測
```

## 工程を飛ばしてはいけない箇所

| 依存 | 理由 |
|---|---|
| [3] → [4] | 仕様表が無いと技適/PSE/薬機を**推測で判定**することになる |
| [7] → [8] | 公式ドメインが未確定だとメール所有者判定が成立しない |
| [8] → [9] | 所有者が maker でないアドレスの役割を論じても無意味 |
| 全て → [11] | 1つでも未確定なら送らない |

**「急ぎだから [7] を飛ばす」は許容しません。** 誤送信のコストの方が高いためです。

## 実行の作法

### 重い処理は job 経由（CLAUDE.md §5）

```
❌ 同期 POST で探索を起動する（12秒タイムアウト回帰の原因になった）
❌ 同一 project で full job と子 job を並列起動する（Chromium 増殖 → backend 無応答）
✅ contact_intelligence_jobs 経由で非同期実行し、heartbeat_at を監視する
```

起動前に**既に走っている job がないか確認**してください。

### 途中で止まったら止まったまま報告する

```
project #123 パイプライン結果: [7] で停止

  [1] lead-disqualify   ✅ eligible (score=52)
  [2] page-capture      ⚠️ 部分取得（Tech Specs タブ未取得）
  [3] facts-extract     ⚠️ 無線方式・Wh が「記載なし」
  [4] jp-market-fit     ⏸ 保留（仕様不足で技適判定不能）
  [5] makuake-fit       ⏸ 未実行
  [6] maker-identity    ✅ maker_official（Aurora Devices Inc.）
  [7] official-site     ❌ 確定できず（candidate 2件どまり）
  [8]-[11]              ⏸ 未実行

  次の手: Tech Specs を Playwright で再取得 → [3][4] をやり直す
```

**「だいたい終わった」と報告しないでください。** どこで何が未確定かを明示します。

## バッチ処理する場合

309案件に対し `japan_opportunity_analyses` は 8件、`contact_people` は 8件しかありません
（2026-08-05 実測）。カバレッジを上げる価値は大きいですが、**一括暴走は禁止**です。

- 件数を提示してユーザーの承認を得てから開始する
- 並列度を上げない（Chromium 増殖の実績あり）
- 1件失敗しても全体を止めず、失敗理由を記録して次へ進む
- レート制限（`SCRAPE_RATE_LIMIT_SECONDS`）を守る
- **送信だけは必ず個別に承認を得る**（[outreach-gate](../outreach-gate/SKILL.md)）

## 状態の確認

```sql
-- どの工程まで進んでいるか
SELECT p.id, p.title,
       p.contact_search_gate_reason, p.gate_checked_at, p.archived_at,
       cd.v2_official_site_url, cd.v2_official_site_source, cd.v2_researched_at,
       (SELECT count(*) FROM contact_people cp WHERE cp.project_id = p.id) AS people,
       so.sent_at, so.replied_at
FROM projects p
LEFT JOIN contact_discoveries cd ON cd.project_id = p.id
LEFT JOIN sales_outreach so ON so.project_id = p.id
WHERE p.id = :id;
```

（列名は実スキーマを `information_schema.columns` で確認してから使ってください。）

## 禁止事項

- 工程を飛ばして送信判定に進む
- 前工程が「不明」のまま後工程を「確定」として扱う
- 同期 POST での重い処理起動 / 並列二重起動
- 承認なしの一括送信
- 予測値・成功確率をパイプライン出力に含める（CLAUDE.md §1）

## 全スキル索引

| # | Skill | 役割 |
|---|---|---|
| 0 | [evidence-ledger](../evidence-ledger/SKILL.md) | 証跡規約（全体の土台） |
| 1 | [lead-disqualify](../lead-disqualify/SKILL.md) | 営業対象外の排除 |
| 2 | [jp-market-fit](../jp-market-fit/SKILL.md) | 日本市場適性 |
| 3 | [makuake-fit](../makuake-fit/SKILL.md) | Makuake 適性 |
| 3b | [competitor-intelligence](../competitor-intelligence/SKILL.md) | 国内競合・流通・代理店・価格・レビュー |
| 3c | [manufacturer-reputation](../manufacturer-reputation/SKILL.md) | メーカー信頼性・過去実績・発送履歴 |
| 4 | [product-page-capture](../product-page-capture/SKILL.md) | 商品ページ取得 |
| 5 | [product-facts-extract](../product-facts-extract/SKILL.md) | 事実抽出 |
| 6 | [maker-identity-verify](../maker-identity-verify/SKILL.md) | メーカー本人確認 |
| 7 | [official-site-verify](../official-site-verify/SKILL.md) | 公式サイト確認 |
| 8 | [nonlatin-maker-resolve](../nonlatin-maker-resolve/SKILL.md) | 非ラテン語 maker 名 |
| 9 | [email-ownership-verify](../email-ownership-verify/SKILL.md) | メール所有者検証 |
| 10 | [email-role-classify](../email-role-classify/SKILL.md) | 役割・正当性 |
| 11 | [decision-maker-hunt](../decision-maker-hunt/SKILL.md) | 意思決定者探索 |
| 12 | [outreach-gate](../outreach-gate/SKILL.md) | 送信前最終関門 |
| 13 | [reply-rate-analytics](../reply-rate-analytics/SKILL.md) | 返信率の計測と改善 |
| 14 | [ground-truth-audit](../ground-truth-audit/SKILL.md) | GT 監査 |
| 15 | [safe-dev-pr](../safe-dev-pr/SKILL.md) / [db-safe-ops](../db-safe-ops/SKILL.md) | 開発・DB 運用 |
