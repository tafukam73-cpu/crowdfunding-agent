---
name: regulatory-risk-check
description: 日本展開時に確認が必要になり得る規制・認証（技適・PSE・電気用品安全法・食品衛生法・薬機法・医療機器・化粧品・食品・サプリ・Bluetooth/Wi-Fi/無線・電源・バッテリー・充電器）の材料を根拠URL付きで整理する。「技適いる？」「PSE必要？」「薬機法に触れる？」「規制リスクを確認して」「認証は何が要る？」と言われたときに使う。法令該当を断定せず、確認が必要な領域と次アクションを出すことが要点。
---

# 規制・認証の確認材料を整える（Regulatory Risk Check）

**このスキルは法令判断をしません。** 出すのは「確認が必要な可能性のある領域」と
「誰に何を聞けば確定するか」だけです。断定は行政・認証機関・専門家の仕事です。

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 絶対原則

> **商品名やキーワードだけで法令対象と断定しない。**

商品ページに `bluetooth` と書いてあることは「技適が必要」の証明ではありません。
それは**確認が必要な領域を示す手がかり**にすぎません。周波数帯・出力・モジュールの
認証状況を確認して初めて判断できます。

## LQE 上の扱い（実装と一致させる）

`lead_qualification_service` のカテゴリ **G / H / I / J / K / L / M** が規制系です。

| コード | 領域 | LQE の severity |
|---|---|---|
| `G` | 規制リスク（総称・集約フラグ） | **常に info**（単独で判定を動かさない） |
| `H` | 食品・サプリメント（食品衛生法） | pre_research=review / pre_outreach=info |
| `I` | 医療（薬機法） | pre_research=review / pre_outreach=info |
| `J` | 化粧品（薬機法） | pre_research=review / pre_outreach=info |
| `K` | Bluetooth・Wi-Fi・無線（電波法） | pre_research=review / pre_outreach=info |
| `L` | 電源・バッテリー・充電器（電気用品安全法／PSE） | pre_research=review / pre_outreach=info |
| `M` | 技適（技術基準適合証明。K の帰結） | pre_research=review / pre_outreach=info |

**G〜M はどのステージでも `blocker` になりません。** 規制の該当性は本スキルの
手順で人が確認するものであり、機械判定で営業を止める根拠にはしないためです。
この severity を変えたい場合は LQE 側の別 PR ＋明示承認で行ってください。

判定の入力語彙は `category_keywords.CAUTION_KEYWORDS` が正本です。
自前でキーワード表を作らないでください。

## 手順

```
1. product-facts-extract で商品ページ上の記載事実を集める
2. lead-disqualify（pre_research）で G〜M の Finding を確認する
   → どのカテゴリが立っているか / 根拠語（evidence の excerpt）は何か
3. 一次資料または信頼できる根拠URLを探す
4. 足りない仕様をメーカーへ確認する項目として列挙する
5. evidence-ledger の 4 点セットで記録する
```

`lead_qualification_service.qualify()` の Finding には、そう判断した
**商品ページ上の記載語**が `evidence[].excerpt` に入っています。まずそこを見てください。

## 出力の形

1 領域につき 1 行。**証跡が無い項目は `verdict: unverified` のままにします。**

```
category:     PSE（電気用品安全法）
finding:      AC アダプタ同梱の記載があり、電気用品に該当する可能性がある
verdict:      needs_confirmation   # confirmed_applicable / not_applicable /
                                   # needs_confirmation / unverified
confidence:   low                  # high / medium / low / unverified（ラベルのみ）
evidence:     商品ページに "100-240V AC adapter included" の記載
source_url:   https://www.kickstarter.com/projects/xxx/yyy
checked_at:   2026-08-06T09:00:00Z
method:       campaign_page_parse
next_action:  メーカーへ定格電圧・PSE 取得状況・型番を確認する
```

`confidence` は **証跡の確からしさ**を表すラベルです。規制に該当する確率ではありません。

## 次アクション（確認先の型）

| 確認内容 | 確認先 |
|---|---|
| 型番・モデル名 | メーカー |
| 周波数帯・空中線電力・無線モジュール型番 | メーカー |
| 技適マークの有無・取得済み認証番号 | メーカー |
| 定格電圧・電流・PSE 取得状況 | メーカー |
| バッテリーのセル種別・容量・輸送区分（UN38.3 等） | メーカー |
| 原材料・添加物・製造所 | メーカー |
| 効能表現の有無 | 自社（表示チェック） |
| 制度そのものの要件 | 行政・認証機関の一次資料 |

## やってはいけないこと

- 「技適が必要です」「PSE 対象です」と**断定する**
- 「日本で販売できません」と**販売可否を断定する**
- 証拠なしに LQE で `blocker` を立てる（G〜M は blocker にしない）
- **規制リスクを点数化する**（スコア・確率・％は作らない）
- 法律相談としての結論を出す
- 一次資料を読まずに「一般的には〜」で済ませる

## 次の工程

- ブランド所有者・製造主体の確認 → [oem-brand-owner-verify](../oem-brand-owner-verify/SKILL.md)
- 日本での流通状況 → [japan-distribution-check](../japan-distribution-check/SKILL.md)
- 市場性の評価 → [jp-market-fit](../jp-market-fit/SKILL.md) / [makuake-fit](../makuake-fit/SKILL.md)
- 除外判定への反映 → [lead-disqualify](../lead-disqualify/SKILL.md)
