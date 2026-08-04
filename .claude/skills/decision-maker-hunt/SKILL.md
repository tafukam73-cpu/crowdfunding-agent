---
name: decision-maker-hunt
description: 提携の意思決定ができる人物（創業者・CEO・事業開発・海外展開責任者）を根拠付きで探索する。「誰に送れば決まる？」「意思決定者を探して」「CEOの連絡先」「担当者が知りたい」「info@ しかない」と言われたときに使う。contact_hunter_service を正本とし、探索の停止条件を守り、実在確認できない人物を作り出さないことが要点。
---

# 意思決定者の探索（Decision Maker Hunt）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## なぜ必要か

汎用窓口（`info@` / `support@`）宛の提携提案は転送されずに終わることが多く、返信率を押し下げます。
**意思決定者に直接届くこと**が返信率改善の最大レバーです（→ [reply-rate-analytics](../reply-rate-analytics/SKILL.md)）。

現状 `contact_people` は **8件**しか蓄積がありません（2026-08-05 実測、projects 309件に対して）。
ここを増やすこと自体が営業成果に直結します。

## 正本は既存実装

```python
from app.services import contact_hunter_service      # 人物探索
from app.services import contact_discovery_v2_service # v2 探索パイプライン
from app.services import source_ownership as so       # 所有者・役割判定
```

**重い探索は job 経由で起動します。同期 POST は禁止（CLAUDE.md §5）。**
`contact_intelligence_jobs` を使い、full job と子 job の並列起動をしないこと。

## 探索対象の役職（優先順）

| 順位 | 役職 | 理由 |
|---|---|---|
| 1 | Founder / Co-founder / CEO | 小規模 maker では実質の決裁者 |
| 2 | Head of Business Development / Partnerships | 提携が本務 |
| 3 | International / Export / APAC 責任者 | 海外展開の担当 |
| 4 | Marketing 責任者 | クラファン施策の担当であることが多い |
| 5 | Sales Manager | 決裁権は薄いが窓口にはなる |

**役職名は一次ソースの表記をそのまま記録**してください。意訳・推測での格上げをしないこと。

## 一次ソース（優先順）

| 順位 | ソース | 強度 | 注意 |
|---|---|---|---|
| 1 | 公式サイトの About / Team / 会社概要 | **最強** | 実名＋役職が対で取れる |
| 2 | campaign ページの創業者紹介・動画説明 | 強 | クラファンは創業者が前面に出る |
| 3 | 公式サイトの Press / News リリース | 中 | コメント者の役職が載る |
| 4 | LinkedIn 会社ページ（`contact_discoveries.v2_linkedin_company_url`） | 中 | 在籍の裏取りに使う |
| 5 | LinkedIn 個人（`v2_linkedin_person_url`） | 中 | **本人特定を慎重に** |

**禁止**: 名刺データ販売サイト、メールアドレス推測ツール、リーク由来データベース。

## 停止条件（探索を無限に広げない）

過去に探索の暴走で実行時間が問題になっています。**以下で必ず止めてください。**

- rank `high`（`HIGH_VALUE_LOCALS`）のアドレスが取れた → 十分。停止
- 一次ソース（順位1〜2）を探索して見つからない → **停止**。3以降に無理に広げない
- 探索URL数が予算を超えた → 停止（`contact_discoveries.search_agent_stop_reason` に理由を記録）
- 同一 project で job が既に走っている → **起動しない**

`search_agent_stop_reason` を空のまま終了しないでください。**なぜ止めたかが次の改善材料**です。

## 人物を「作らない」

以下は**絶対に禁止**です。実在しない人物・誤ったアドレスを生成します。

| 禁止行為 | なぜ |
|---|---|
| `firstname.lastname@domain` を組み立てる | 実在確認不能。誤送信になる |
| LinkedIn の氏名からメール形式を推測する | 同上 |
| 役職を「たぶんCEO」と推定する | 一次ソースの表記のみ使う |
| 同姓同名を同一人物とみなす | 会社名の一致を確認すること |
| 退職者を現職として扱う | 情報の `checked_at` を確認する |

**氏名が取れてもメールが取れないなら、氏名だけを記録**してください。
メールは [email-ownership-verify](../email-ownership-verify/SKILL.md) を通ったものだけです。

## 保存

```
contact_people.name          ← 一次ソースの表記のまま
contact_people.title/role    ← 一次ソースの役職表記のまま
contact_people.email         ← 検証済みのもののみ（無ければ NULL）
contact_people.email_source  ← 取得元の種別
contact_people.source_url    ← 実際に取得したページURL（必須）
contact_people.linkedin_url  ← 裏取りに使った場合
contact_people.confidence    ← 信頼度
```

## 記録例

```
決裁者候補:
  name : Jane Doe
  title: Co-founder & CEO           （原文表記のまま）
  source_url: https://aurora-devices.io/about
  確認: 2026-08-05T07:20:00Z / method=playwright_fetch
  email: 不明（About ページに個人アドレスの記載なし）
  → 宛先は partnerships@aurora-devices.io (role=high) を使用し、本文で Jane Doe 宛と明記する
```

**「本人の名前は分かるがアドレスは汎用窓口」は良い成果です。** 本文で宛名を書けば到達率が上がります。

## 禁止事項

- メールアドレスの推測生成
- 同期 POST での重い探索起動 / 並列二重起動
- 停止条件を無視した探索の継続
- `source_url` なしでの `contact_people` 保存

## 次の工程

→ [outreach-gate](../outreach-gate/SKILL.md)
