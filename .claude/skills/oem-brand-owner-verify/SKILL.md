---
name: oem-brand-owner-verify
description: 営業対象がブランド所有者・メーカー本人なのか、OEM/ODM/private label/代理店/販売店/輸入元なのかを Evidence 付きで見分ける。「このブランドの持ち主は誰？」「OEM じゃない？」「ODM 疑い」「作ってる会社と売ってる会社が違う」「代理店に送りそう」と言われたときに使う。証拠が無ければ unknown のままにし、OEM 疑いだけで営業を止めないことが要点。
---

# ブランド所有者と製造主体を見分ける（OEM / Brand Owner Verify）

**「誰に営業するか」を間違えないためのスキルです。** 製造だけしている工場、
名前を貼っているだけの販売者、代理店に独占交渉を持ちかけても話は進みません。

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 区別する役割（`lead_qualification_service.ENTITY_ROLES` が正本）

```
brand_owner / manufacturer / factory / oem / odm / private_label /
distributor / retailer / importer / agency / unknown
```

**証拠が無ければ `unknown`。** 推測で役割を埋めないでください。

## site_role と entity_role を混同しない

| 概念 | 意味 | 正本 |
|---|---|---|
| **site_role** | その**サイト**が何か（公式 / 小売 / ニュース / ブログ / ディレクトリ） | `official_site_verifier` / `source_ownership.classify_domain` |
| **entity_role** | その**法人**が商流上どの立場か（上記 11 種） | `lead_qualification_service` の Finding の `entity_role` |

「小売サイトが見つかった」＝「その法人が retailer」ではありません。ブランド所有者が
自社 EC を持っていることもあります。**サイトの性質と法人の立場は別に判断します。**

## LQE 上の扱い（実装と一致させる）

| コード | カテゴリ | severity | 備考 |
|---|---|---|---|
| `C` | OEM 商品の可能性 | **常に review**（pre_research / pre_outreach とも） | **推測で block しない** |
| `D` | 代理店・販売店のみ | pre_research=review / **pre_outreach=blocker** | `classify_domain` の分類＋判定元URLが必要 |
| `E` | メーカー未確認 | pre_research=info または review / **pre_outreach=blocker** | maker identity 未確定 |
| `S` | ブランド所有者不明 | pre_research=info または review / **pre_outreach=review** | 公式サイト未検証 |

`C` の Finding は `entity_role` を補助属性として返します。証拠が無ければ `unknown` です。

### STOP ではなくレビュー対象

- OEM 疑い / ODM 疑い / private label 疑い
- ブランド所有関係が未確認

**「OEM かもしれない」だけで営業を止めません。** OEM でもブランド所有者であれば
交渉相手として正しいことがあり、断定できないものを停止根拠にしないためです。

### pre_outreach で停止対象になり得るもの

- maker identity 未確認（`E`）
- ブランド所有者不明（`S` は review。ただし `E` と重なると blocked になり得る）
- 代理店・販売店しか確認できない（`D`）

## 確認の突き合わせ先

1 つの一致では確定しません。**2 つ以上の独立した根拠**を揃えてください。

| 見るもの | 何が分かるか |
|---|---|
| 公式サイトの会社概要 / About | 法人名・所在地・設立 |
| 利用規約・特定商取引法表記 | 販売主体（誰が売っているか） |
| プライバシーポリシーの事業者名 | 法人の実体 |
| 商標表記（® / ™ / 「〜は◯◯社の登録商標です」） | ブランド所有者 |
| クラファンの creator 情報 | 出品者（＝ブランド所有者とは限らない） |
| 製品パッケージ・取説の記載 | 製造者 / 販売者 / 輸入者の別 |
| 別ブランドで同一製品が流通しているか | OEM / ODM / private label の手がかり |

**maker 名とサイト名が似ているだけでは確定しません**（同名の無関係企業がある）。
`source_ownership.classify_domain()` はドメイン分類の正本です。自前で判定しないでください。

## 既存スキルとの役割分担

| スキル | 担当 |
|---|---|
| [maker-identity-verify](../maker-identity-verify/SKILL.md) | **同名の別法人と取り違えない**（同定そのもの） |
| [official-site-verify](../official-site-verify/SKILL.md) | **どのサイトが公式か**（site_role の確定） |
| **本スキル** | **商流上の立場**（entity_role）と**ブランド所有関係** |
| [email-ownership-verify](../email-ownership-verify/SKILL.md) | そのメールアドレスの**所有者** |

重複したら本スキルは entity_role の判定に徹し、同定・公式サイト確定は上記へ委ねます。

## 出力の形

```
entity_role:  odm
confidence:   medium
evidence:     ページに "ODM partner in Shenzhen" の記載
source_url:   https://www.kickstarter.com/projects/xxx/yyy
checked_at:   2026-08-06T09:00:00Z
method:       campaign_page_parse
判断:         OEM/ODM の可能性あり。**断定しない**（review 扱い）
```

役割が決められないときは、堂々と次のように書いてください。

```
entity_role:  unknown
理由:         公式サイトが未確定で、法人を特定できる記載を確認できなかった
探索済み:     https://example.com/about, https://example.com/terms
```

## やってはいけないこと

- 証拠なしに `oem` / `odm` / `private_label` と**断定する**
- OEM 疑いだけで案件を `blocked` にする（`C` は常に review）
- 名前の一致だけで brand_owner と決める
- サイトの性質（site_role）から法人の立場（entity_role）を推測する
- 役割の確からしさを**点数化する**（confidence はラベルのみ）
- AI が判定を自動で上書きする（override は人の明示操作のみ）

## 次の工程

- 日本での流通状況 → [japan-distribution-check](../japan-distribution-check/SKILL.md)
- 規制の確認 → [regulatory-risk-check](../regulatory-risk-check/SKILL.md)
- 連絡先の所有者確認 → [email-ownership-verify](../email-ownership-verify/SKILL.md)
- 意思決定者の探索 → [decision-maker-hunt](../decision-maker-hunt/SKILL.md)
- 除外判定への反映 → [lead-disqualify](../lead-disqualify/SKILL.md)
