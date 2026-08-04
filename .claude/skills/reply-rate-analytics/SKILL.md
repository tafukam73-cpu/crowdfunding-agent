---
name: reply-rate-analytics
description: 返信率を実測し、どの条件が返信に効いているかを事実ベースで分析して改善につなげる。「返信率は？」「反応どう？」「どのメールが効いた？」「改善したい」「効果測定」と言われたときに使う。sales_outreach の実測値のみを扱い、母数不足のときに率を出さないこと、予測値をユーザーに見せないことが要点。
---

# 返信率の計測と改善（Reply Rate Analytics）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 現状：まだ率を語れる母数がない

**2026-08-05 実測で `sales_outreach` は 10 件**（`projects` は 309 件）。
この母数で「返信率◯%」を出しても**統計的に無意味**です。

> **母数が小さいときは、率ではなく実数を報告してください。**
> 「10件送って1件返信」は事実。「返信率10%」は誤解を招きます。

**当面の最優先は分析ではなく母数を増やすこと**です。
[outreach-gate](../outreach-gate/SKILL.md) を通過する案件を増やすことが、分析より先です。

## データソース（すべて実測値）

`sales_outreach` テーブルは計測に必要な列が既に揃っています。

| 列 | 用途 |
|---|---|
| `sent_at` | 送信済み母数 |
| `replied_at` / `last_reply_at` | 返信の実績 |
| `reply_intent` | 返信の内容分類（興味/拒否/条件付き等） |
| `reply_summary` / `reply_confidence` | 返信内容の要約と確度 |
| `followup_count` / `last_followup_at` / `followup_due_at` | フォローアップ実績 |
| `generated_language` / `sent_language` | 言語別の比較 |
| `generated_variants` | 文面バリエーション |
| `user_edited` / `edited_at` | **人が手を入れたか**（← 効果検証の重要軸） |
| `sent_subject` / `sent_body_snapshot` | 送信時点の実文面（後から再現可能） |
| `priority_score` | 優先度との相関 |

`sales_activities.occurred_at` と `project_status_events` も併用できます。

## 基本の集計

```sql
-- 実数で見る（率にしない）
SELECT
  count(*) FILTER (WHERE sent_at IS NOT NULL)     AS sent,
  count(*) FILTER (WHERE replied_at IS NOT NULL)  AS replied,
  count(*) FILTER (WHERE followup_count > 0)      AS followed_up
FROM sales_outreach;

-- 返信の内訳（意図別）
SELECT reply_intent, count(*)
FROM sales_outreach
WHERE replied_at IS NOT NULL
GROUP BY reply_intent ORDER BY 2 DESC;
```

DB 接続は `docker compose exec -T db psql -U cfagent -d crowdfunding`（CLAUDE.md §4）。
**読み取りのみ。集計目的で UPDATE をしないでください。**

## 分析軸（母数が貯まったら）

比較するときは**必ず実数を併記**します（`n=` を書く）。

| 軸 | 仮説 |
|---|---|
| 宛先の `email_role` | `high`/`person` は `mid`/`support` より返信が多いか |
| `confidence` | `high` の方が返信が多いか（＝検証工程の価値の実証） |
| 意思決定者の氏名を本文に書いたか | 宛名ありの方が返信が多いか |
| `sent_language` | 英語 / 現地語のどちらが返信が多いか |
| `user_edited` | 人が編集した文面の方が返信が多いか |
| `followup_count` | フォローアップの有無・回数と返信 |
| 送信からの経過日数 | 返信までの日数分布（フォロー時期の設計に使う） |

## 改善サイクル

```
1. 実数を出す（率は母数が十分になってから）
2. 差が出た軸を特定する（n を必ず併記）
3. 差の原因を「検証工程のどこか」に紐づける
   例: confidence=high の返信が多い → email-ownership-verify の徹底が効いている
4. 該当する Skill の手順を更新する
5. 次のバッチで再計測する
```

**「文面を工夫する」より先に「正しい相手に届いているか」を疑ってください。**
返信ゼロの最大要因は、多くの場合コピーではなく宛先です。

## ユーザー向け出力の制約（CLAUDE.md §1）

| 出してよい | 出してはいけない |
|---|---|
| 「10件送信・1件返信（実数）」 | 「返信率10%」（母数不足時） |
| 「role=high は n=4 で 2件返信」 | 「role=high なら返信率50%が見込める」 |
| 「返信までの中央値は3日（n=5）」 | 「次は◯件の返信が期待できる」 |
| 過去の実測値と確認日時 | **予測値・成功確率・可能性スコア** |

## 禁止事項

- 母数不足のまま率・パーセンテージを提示する
- 返信率の**予測値**を出す（CLAUDE.md §1 で明確に禁止）
- 集計のために本番データを UPDATE する
- 返信の有無を推測で埋める（`replied_at` が NULL なら「返信なし」ではなく「未記録」の可能性を考える）

## 関連

- [outreach-gate](../outreach-gate/SKILL.md)（母数を増やす前段）
- [ground-truth-audit](../ground-truth-audit/SKILL.md)（計測の信頼性）
