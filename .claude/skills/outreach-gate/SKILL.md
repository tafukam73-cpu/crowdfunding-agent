---
name: outreach-gate
description: 営業メール送信直前の最終関門。検証工程がすべて完了しているかを機械的に確認し、1件でも欠けたら送信を止める。「送っていい？」「送信前チェック」「このメール出して」「アウトリーチ実行」と言われたときに必ず使う。誤送信は取り返しがつかないため、疑わしきは送らないことを原則とする。
---

# 送信前の最終関門（Outreach Gate）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

> **誤ったメールを1通送るコストは、送らなかった場合のコストより遥かに高い。**
> 迷ったら送らない。これが唯一の原則です。

## 送信は明示承認が必要

**このスキルは「送信可否の判定」までを行います。実際の送信は必ずユーザーの明示承認を得てください。**
外部への送信は取り消せません。バッチ送信は特に、件数と宛先一覧を提示して承認を得ること。

## 必須チェックリスト（1つでも ❌ なら送らない）

### A. 案件の適格性

- [ ] `contact_search_gate` が `eligible`（[lead-disqualify](../lead-disqualify/SKILL.md)）
- [ ] `projects.archived_at` が NULL（アーカイブ済みに送らない）
- [ ] 日本市場適性を確認済み（[jp-market-fit](../jp-market-fit/SKILL.md)）
- [ ] Makuake 適性を確認済み（[makuake-fit](../makuake-fit/SKILL.md)）
- [ ] **既に日本展開済みでない**ことを確認済み（根拠URL付き）

### B. 相手の同定

- [ ] maker が同定済み（[maker-identity-verify](../maker-identity-verify/SKILL.md)）
- [ ] `official_site_url` が確定（`v2_official_site_source` が空でない）
- [ ] `official_site_url` ≠ `campaign_url`（**混同していないこと**）

### C. 宛先の妥当性

- [ ] `ownership.is_maker == True`（[email-ownership-verify](../email-ownership-verify/SKILL.md)）
- [ ] `confidence` が `unverified` / `invalid` **でない**
- [ ] `email_role` が `exclude` **でない**（[email-role-classify](../email-role-classify/SKILL.md)）
- [ ] `contact_people.source_url` が存在する
- [ ] アドレスが**推測生成でない**（取得元が明示されている）

### D. 重複・意思の尊重

- [ ] `sales_outreach.sent_at` が NULL（未送信）または再送が妥当
- [ ] `sales_outreach.replied_at` が NULL（返信済みに新規送信しない）
- [ ] `reply_intent` が拒否・配信停止を示していない
- [ ] 同一 maker の別案件で既に送っていないか（**同じ相手に重複送信しない**）

### E. 本文の事実性

- [ ] 本文中の商品仕様が [product-facts-extract](../product-facts-extract/SKILL.md) の事実に基づく
- [ ] **訴求文（「世界最軽量」等）を裏取りなしに転記していない**
- [ ] **予測値・成功確率・可能性スコアを書いていない**（CLAUDE.md §1）
- [ ] 宛名・社名が一次ソースの表記と一致
- [ ] 送信者情報（`SENDER_NAME` / `SENDER_COMPANY`）が正しい

### F. 鮮度

- [ ] メールの `checked_at` が 180日以内
- [ ] 公式サイトの `checked_at` が 365日以内
- [ ] キャンペーン状態の確認が 7日以内

## 実装

```python
from app.services import sales_outreach_service
from app.services import email_delivery_service   # 送信
from app.email.providers import gmail             # Gmail OAuth
```

送信経路は Gmail API（`GMAIL_REFRESH_TOKEN` 等）です。
`email_settings` / `email_drafts` の既存フローに載せてください。

## 判定の出力形式

```
送信可否: ❌ 保留

  A 案件適格性   : ✅ eligible / 未アーカイブ / jp-fit 済 / makuake-fit 済
  B 相手の同定   : ✅ maker_official / official_site 確定
  C 宛先の妥当性 : ❌ confidence=unverified
       → info@aurora-devices.io は取得元不明（推測生成の疑い）
       → 対応: official-site-verify で contact ページを取得し直す
  D 重複        : ✅ 未送信
  E 本文の事実性 : ⚠️ 「世界最軽量」の記載あり（裏取り未）
  F 鮮度        : ✅

  結論: C が未達のため送信しない。
```

**全項目 ✅ になって初めて、ユーザーに送信承認を求めます。**

## バッチ送信時の追加ルール

- 件数・宛先一覧・本文サンプルを**提示してから**承認を得る
- 1件でも C/D に該当があれば、その1件を除外して残りを送る（全体を止めない）
- 送信レート制限を守る（一斉送信は迷惑メール判定を招く）
- 送信後は `sales_outreach.sent_at` / `sent_subject` / `sent_body_snapshot` / `recipient_email` を必ず記録

## 禁止事項

- チェック未完了での送信
- ユーザー承認なしの送信（テスト送信も含む）
- アドレスの実在確認を目的とした送信
- `reply_intent` が拒否を示す相手への再送

## 次の工程

→ [reply-rate-analytics](../reply-rate-analytics/SKILL.md)（送信後の計測）
