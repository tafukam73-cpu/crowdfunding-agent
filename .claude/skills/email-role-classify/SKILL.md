---
name: email-role-classify
description: 検証済みメールアドレスの「役割」を判定し、営業として正当な宛先かを決める。「どのアドレスに送るべき？」「info@ でいい？」「宛先の優先順位」「このアドレスは営業窓口？」と言われたときに使う。source_ownership.email_role と _ROLE_RANK を正本とし、送ってはいけない役割（no-reply/press/abuse等）を確実に除外する。
---

# メール役割の判定（Email Role Classify）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [email-ownership-verify](../email-ownership-verify/SKILL.md) で**所有者が maker であること**を確定します。

**所有者が正しくても役割が不適切なら送りません。** 所有者検証と役割判定は別工程です。

## 正本は `classify_email_target()`

```python
from app.services import source_ownership as so

label = so.label_near_email(page_text, email)      # ページ上の用途ラベルを抽出
r = so.classify_email_target(                       # ★ これを正本として使う
    email, ctx,
    label=label,
    source_url=取得したページURL,
    checked_at="2026-08-05T00:00:00Z",
    person_names=公式ページから取れた氏名集合 or None,
)
# r["role"] / r["role_source"] / r["label_raw"] / r["source_url"] /
# r["checked_at"] / r["sendable"] / r["reasons"]
```

**送信可否は `r["sendable"]` を見ます。** role から自分で判断し直さないでください。

低水準 API（必要な場合のみ）:
```python
so.email_role(email, label=..., person_confirmed=...)
so.classify_email(email, ctx, person_names, label=...)
so.rank_maker_emails(emails, ctx, labels={email: label, ...})
so._ROLE_RANK  # {"high":0,"person":1,"mid":2,"support":3,"other":4,"unknown":5,"exclude":9}
```

ランクが**小さいほど優先**です。`exclude` (9) は**送信対象外**を意味します。

## 判定の優先順位（この順序が安全性の要）

1. **local-part の hard exclude が最優先**（`noreply` / `press` / `media` / `privacy` /
   `legal` / `careers` 等）。**ラベルで解除できません**
2. ページ上の**ラベル**（`label_near_email` の戻り値）
3. local-part の既知機能語（`sales` / `info` / `support` 等）
4. `person_confirmed`（公式ページ掲載の氏名と local-part が一致）→ `person`
5. いずれも無ければ **`unknown`**

### なぜ 1 がラベルより強いのか

実装中に、`media@` の近傍ラベル `PR 문의` に含まれる一般語「문의（問い合わせ）」が
`mid` に一致し、**送信不可のはずのアドレスが送信可へ降格する**経路が見つかりました。
ラベルは **exclude への引き上げ**には使いますが、**exclude からの引き下げ**には使いません。
迷ったら送らない側に倒します。

### ラベルなし・氏名一致なしは `unknown`（person に昇格しない）

`ethan@maker.com` のような個人名アドレスでも、**役割の証拠が無ければ `unknown`** です。
`unknown` は `sendable=False` であり、**勝手に送信可へ昇格させないでください**。
`person` になるのは、公式ページから氏名が取れて local-part と一致したときだけです。

## 役割クラスと集合

| role | rank | 集合 | 送信 | 意味 |
|---|---|---|---|---|
| `high` | 0 | `HIGH_VALUE_LOCALS` | ✅ 最優先 | 事業・提携・パートナー窓口 |
| `person` | 1 | 氏名一致（`person_confirmed`）| ✅ | **証拠のある**個人宛 |
| `mid` | 2 | `MID_VALUE_LOCALS`（contact, hello, info, inquiry, enquiries…） | ✅ | 汎用問い合わせ窓口 |
| `support` | 3 | `SUPPORT_LOCALS`（support, help, care, service, cs） | ⚠️ | カスタマーサポート。**提携提案には不向き** |
| `other` | 4 | — | ⚠️ | 分類不能 |
| **`unknown`** | **5** | — | ❌ **送らない** | **役割の証拠なし**。汎用窓口より後ろに置く |
| `exclude` | 9 | `EXCLUDE_LOCALS` | ❌ **送らない** | no-reply / press / media / privacy / abuse / legal / careers 等 |

`email_validation.NOREPLY_PREFIXES` も併用して no-reply を弾きます。

## 多言語ラベル

`role_from_label()` は英語・日本語・**韓国語**に対応しています。

| 群 | 韓国語の例 |
|---|---|
| exclude | `개인정보보호책임자` `개인정보보호` `정보보호책임자` `홍보` `언론` `미디어` `보도` `채용` `법무` |
| support | `고객센터` `고객지원` `서비스센터` |
| high | `제휴` `사업제휴` `파트너십` `파트너` `리셀러` `대리점` `총판` `유통` `도매` `수출` `해외영업` `영업` |

複合ラベル（`"Media & Sales"` 等）は**保守的に exclude 側へ倒す**実装です。意図的な設計です。

**ラベルの原文（`label_raw`）・取得元 URL（`source_url`）・確認日時（`checked_at`）を
必ず保存してください。** 判定根拠が非ラテン文字のとき、原文が無いと後から検証できません
（→ [evidence-ledger](../evidence-ledger/SKILL.md)）。

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
