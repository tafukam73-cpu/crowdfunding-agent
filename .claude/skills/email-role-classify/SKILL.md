---
name: email-role-classify
description: 検証済みメールアドレスの「役割」を判定し、営業として正当な宛先かを決める。「どのアドレスに送るべき？」「info@ でいい？」「宛先の優先順位」「このアドレスは営業窓口？」と言われたときに使う。source_ownership.email_role と _ROLE_RANK を正本とし、送ってはいけない役割（no-reply/press/abuse等）を確実に除外する。
---

# メール役割の判定（Email Role Classify）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [email-ownership-verify](../email-ownership-verify/SKILL.md) で**所有者が maker であること**を確定します。

**所有者が正しくても役割が不適切なら送りません。** 所有者検証と役割判定は別工程です。

## 正本は既存実装

```python
from app.services import source_ownership as so

so.email_role(email)   # → "high" | "person" | "mid" | "support" | "other" | "exclude"
so._ROLE_RANK          # {"high":0, "person":1, "mid":2, "support":3, "other":4, "exclude":9}
```

ランクが**小さいほど優先**です。`exclude` (9) は**送信対象外**を意味します。

## 役割クラスと集合

| role | rank | 集合 | 送信 | 意味 |
|---|---|---|---|---|
| `high` | 0 | `HIGH_VALUE_LOCALS` | ✅ 最優先 | 事業・提携・パートナー窓口 |
| `person` | 1 | （個人名パターン） | ✅ | 個人宛（担当者が特定できている） |
| `mid` | 2 | `MID_VALUE_LOCALS`（contact, hello, info, inquiry, enquiries…） | ✅ | 汎用問い合わせ窓口 |
| `support` | 3 | `SUPPORT_LOCALS`（support, help, care, service, cs） | ⚠️ | カスタマーサポート。**提携提案には不向き** |
| `other` | 4 | — | ⚠️ | 分類不能 |
| `exclude` | 9 | `EXCLUDE_LOCALS` | ❌ **送らない** | no-reply / press / abuse / legal 等 |

`email_validation.NOREPLY_PREFIXES` も併用して no-reply を弾きます。

## 宛先の選び方

1. 検証済みアドレスを `_ROLE_RANK` でソートする
2. `exclude` を**完全に除外**する
3. 最上位（rank 最小）を主宛先にする
4. 同ランク複数なら `confidence` が高い方（[email-ownership-verify](../email-ownership-verify/SKILL.md)）
5. それでも同点なら `source_url` がより一次に近い方

**support 宛しか無い場合**は、[decision-maker-hunt](../decision-maker-hunt/SKILL.md) を先に試します。
サポート窓口に提携提案を送っても転送されずに終わることが多く、返信率を押し下げます。

## 正当性のチェック（役割とは別軸）

役割が適切でも、以下に該当したら送りません。

| チェック | 根拠 |
|---|---|
| 所有者が maker か | [email-ownership-verify](../email-ownership-verify/SKILL.md) で確定済みであること |
| `confidence` が `unverified` / `invalid` でないか | `email_validation.email_confidence()` |
| 既に送信済み・返信済みでないか | `sales_outreach.sent_at` / `.replied_at` |
| 配信停止・拒否の意思表示がないか | `sales_outreach.reply_intent` |
| 個人情報として扱うべきか | 個人名アドレスは業務目的の範囲で扱う |

## 個人宛（person）を扱うときの注意

個人名アドレスは個人情報です。以下を守ってください。

- **一次ソース（公式サイト・公式SNS）に公開されているものだけ**を使う
- 名刺サイト・スクレイピング業者・リーク由来のデータは**使わない**
- 役職と実名の対応が取れていること（→ [decision-maker-hunt](../decision-maker-hunt/SKILL.md)）
- 送信後は `contact_people` の記録を最新に保つ

## 記録

```
宛先選定:
  主宛先: partnerships@aurora-devices.io
    role: high (rank 0) / confidence: high
    source_url: https://aurora-devices.io/contact
    確認: 2026-08-05T07:20:00Z
  副宛先: なし
  除外:
    - no-reply@aurora-devices.io  → exclude (EXCLUDE_LOCALS)
    - support@zeczec.com          → 所有者がプラットフォーム運営
```

## 禁止事項

- `exclude` 判定のアドレスに送る
- 役割を推測で決める（ローカルパートの見た目だけで「たぶん営業窓口」と判断する）
- 複数アドレスへ同時一斉送信する（返信率が落ち、スパム扱いされる）
- 所有者未検証のまま役割判定だけで送信可とする

## 次の工程

→ [decision-maker-hunt](../decision-maker-hunt/SKILL.md)（より上位の宛先を探す）
→ [outreach-gate](../outreach-gate/SKILL.md)（送信前の最終関門）
