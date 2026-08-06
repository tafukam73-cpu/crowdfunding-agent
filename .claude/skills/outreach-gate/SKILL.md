---
name: outreach-gate
description: 営業アウトリーチ直前の最終関門。Gmail 下書き作成と Compose URL の可否を機械的に判定し、営業対象判定が通らない案件を止める。「送っていい？」「下書き作っていい？」「送信前チェック」「アウトリーチ実行」と言われたときに必ず使う。このシステムはメールを直接送信せず Gmail 下書き導線を制御することを踏まえ、疑わしきは進めないことを原則とする。
---

# アウトリーチ前の最終関門（Outreach Gate）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

> **誤ったメールを1通送るコストは、送らなかった場合のコストより遥かに高い。**
> 迷ったら進めない。これが唯一の原則です。

## 最重要：このシステムはメールを直接送信しません

`app/email/providers/base.py` の設計どおり、プロバイダーは **`create_draft()` で
「下書き」を作るだけ**です。Gmail のスコープも `gmail.compose` のみで、
送信 API（`users.messages.send`）は実装されていません。

| 区分 | 対象 |
|---|---|
| **制御できるもの** | Gmail 下書き作成 / Gmail Compose URL の表示 / `mark_sent` の監査記録 |
| **制御できないもの** | **ユーザーがシステム外で Gmail を開いて手動送信する行為** |

**この関門は物理的な送信禁止ではありません。** 止めているのは「アプリが送信を
用意すること」です。実際の送信はユーザーの手元で行われるため、下書きを作る前の
確認がそのまま最後の砦になります。バッチで下書きを作る場合は、件数と宛先一覧を
提示して承認を得てください。

## 関門の位置（正本）

判定は **`app/services/outreach_qualification_gate.py` が正本**です。

| 位置 | 実装 | 挙動 |
|---|---|---|
| **主関門** | `email_delivery_service.create_provider_draft()` の `provider.create_draft()` **直前** | enforce では `clear` 以外で `LeadQualificationBlocked` → **409**。provider を呼ばない |
| **副関門** | `sales_outreach_service.serialize()` | Compose URL の表示制御（**判定は実行しない**。保存済みの最新判定だけ参照） |
| 記録 | `mark_sent()` | **止めない。** 監査情報を timeline に残す |

```python
from app.services import outreach_qualification_gate as gate

payload = gate.require_clear(db, project)   # enforce なら不合格で例外
```

主な公開 API:

```
require_clear / evaluate / audit_note / latest_decision / signals_digest /
allows_compose_url / current_mode / is_enforcing / LeadQualificationBlocked /
DECISION_MAX_AGE_HOURS(=24) / OVERRIDE_MAX_AGE_HOURS(=72)
```

## 適用モード（`OUTREACH_GATE_MODE`）

| | observe（**初期値**） | enforce |
|---|---|---|
| 判定・履歴保存 | **する** | する |
| `provider.create_draft()` | **許可**（blocked / review / 判定不能でも） | `clear` ／有効 override のみ |
| 409 | 返さない | review / blocked / 判定不能で返す |
| Compose URL | **返す** | `clear` のみ |
| ログ | `WARNING observe: would block …` | `INFO outreach blocked …` |
| 監査 payload | `ProviderDraftResult.qualification` に付与 | 409 の `detail.qualification` |

- 設定は `.env` の `OUTREACH_GATE_MODE`
- **未設定・空文字・不正値は observe**（`current_mode()` が丸める）
- 現在のモードは `GET /email/provider` の `outreach_gate_mode` で確認できる
- **observe で下書きが作れることを「送信可能」「安全」「承認済み」とは表現しない**

## 判定と停止条件（enforce）

進めてよいのは **`clear`**、または**人が明示的に override した `clear`** だけです。

**`review` も止めます。** pre_outreach の review は maker 未確認・ブランド所有者不明・
代理店疑いなど、そのまま送ると誤送信になる事項が中心だからです。

**fail closed**: 判定を完了できない場合も止めます（`gather_signals` 失敗 /
`qualify` 失敗 / 履歴取得失敗 / digest 計算失敗 / 保存失敗）。調査ゲート
（pre_research）が fail open なのとは非対称ですが、意図的です。

## override を再利用してよい条件（`gate.valid_override()`）

**1 つでも欠ければ無効**です。

1. 保存済み履歴であること
2. `stage == "pre_outreach"`
3. **最新履歴であること**（後続の recheck があれば自動失効）
4. `overridden == true`
5. `effective_decision == "clear"`
6. `override_reason` が非空
7. `override_evidence_url` が **http(s)**
8. `created_at` が **72 時間以内**
9. **`signals_digest` が一致**（判定入力が変わっていない）
10. `stale` 相当の Finding が無いこと

**AI / Copilot が自動で override してはいけません。** 人の明示操作だけです。

## 判定の使い回し（履歴を増やしすぎない）

判定入力のダイジェスト（`signals_digest`）が一致し、かつ **24 時間以内**なら
保存済み履歴を再利用し、履歴を増やしません。入力が変われば 1 行だけ追加します。
**画面表示だけでは判定しません**（GET 系は履歴を書きません）。

## 必須チェックリスト（1つでも ❌ なら進めない）

### A. 案件の適格性

- [ ] 営業対象判定 pre_outreach が `clear`（[lead-disqualify](../lead-disqualify/SKILL.md)）
- [ ] `projects.archived_at` が NULL（アーカイブ済みに送らない）
- [ ] 日本市場適性を確認済み（[jp-market-fit](../jp-market-fit/SKILL.md)）
- [ ] Makuake 適性を確認済み（[makuake-fit](../makuake-fit/SKILL.md)）
- [ ] **既に日本正規販売でない**ことを確認済み（[japan-distribution-check](../japan-distribution-check/SKILL.md)）
- [ ] 規制の確認事項を把握済み（[regulatory-risk-check](../regulatory-risk-check/SKILL.md)）

### B. 相手の同定

- [ ] maker が同定済み（[maker-identity-verify](../maker-identity-verify/SKILL.md)）
- [ ] `official_site_url` が確定（`v2_official_site_source` が空でない）
- [ ] `official_site_url` ≠ `campaign_url`（**混同していないこと**）
- [ ] 商流上の立場が確認済み（[oem-brand-owner-verify](../oem-brand-owner-verify/SKILL.md)）
      — **代理店・販売店に独占交渉を持ちかけない**

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
- [ ] 判定が 24 時間以内 / override が 72 時間以内

## 判定の出力形式

```
下書き作成可否: ❌ 保留    （適用モード: observe）

  A 案件適格性   : ✅ pre_outreach=clear / 未アーカイブ / jp-fit 済 / makuake-fit 済
  B 相手の同定   : ✅ maker_official / official_site 確定 / entity_role=brand_owner
  C 宛先の妥当性 : ❌ confidence=unverified
       → info@aurora-devices.io は取得元不明（推測生成の疑い）
       → 対応: official-site-verify で contact ページを取得し直す
  D 重複        : ✅ 未送信
  E 本文の事実性 : ⚠️ 「世界最軽量」の記載あり（裏取り未）
  F 鮮度        : ✅

  結論: C が未達のため下書きを作らない。
  注記: 現在は observe モードのため、システム上は下書き作成が可能な状態です。
        判定が通ったという意味ではありません。
```

**全項目 ✅ になって初めて、ユーザーに承認を求めます。**

## 409 レスポンスに載せてよいもの

```json
{"detail": {
  "message": "営業対象判定によりGmail下書きを作成できません",
  "qualification": {
    "stage": "pre_outreach", "decision": "blocked",
    "machine_decision": "blocked", "effective_decision": "blocked",
    "overridden": false, "blocker_codes": ["E"], "review_codes": [],
    "reasons": ["…"], "checked_at": "…", "persisted": true }}}
```

**載せない**: 数値 confidence / score / probability / forecast / 返信率 / 成功率 /
`makuake_fit` / `japan_crowdfunding_score` / メールアドレス / Evidence 本文 /
`internal_db`（`db://`）の URL / 内部例外・stack trace。

## mark_sent は止めない

外部で既に送信済みの**事実記録**なので止めません。止めると履歴と現実が乖離し、
フォロー期限計算も CRM も壊れます。

- blocked でも **成功（200）**
- `sales_status` → `contacted` の自動同期を維持
- `followup_due_at` の計算を維持
- LQE 判定を監査情報として timeline に残す（`gate.audit_note()`）
- 判定できなかった場合は `qualification_unavailable` を記録
- **メールアドレス・証跡本文・`db://` は書かない**

## バッチ処理時の追加ルール

- 件数・宛先一覧・本文サンプルを**提示してから**承認を得る
- 1件でも C/D に該当があれば、その1件を除外して残りを進める（全体を止めない）
- 下書き作成後は `sales_outreach.sent_at` / `sent_subject` / `sent_body_snapshot` /
  `recipient_email` を `mark_sent` で必ず記録する

## 禁止事項

- チェック未完了で下書きを作る / Compose URL を出す
- ユーザー承認なしの下書き作成（テストも含む）
- observe で下書きが作れたことを「判定が通った」と説明する
- **AI が override して通す**
- アドレスの実在確認を目的とした送信
- `reply_intent` が拒否を示す相手への再送
- 409 に秘密情報・証跡本文・内部例外を載せる
- **自動アーカイブ**（判定は archive に触れない）
- 返信率・成功率・可能性スコアの表示

## 関連

- 判定そのもの → [lead-disqualify](../lead-disqualify/SKILL.md)
- 記録規約 → [evidence-ledger](../evidence-ledger/SKILL.md)
- 全体の順序 → [sales-pipeline-run](../sales-pipeline-run/SKILL.md)
- 送信後の計測 → [reply-rate-analytics](../reply-rate-analytics/SKILL.md)
