---
name: competitor-intelligence
description: 対象商品の日本国内における競合・販売状況・価格・代理店・レビュー・販売チャネルを事実ベースで収集する。「競合は？」「日本で売ってる？」「いくらで売られてる？」「代理店いる？」「レビューある？」「どこで買える？」と言われたときに使う。販売実績が取れたら lead-disqualify へ差し戻す判断材料になる。推測での「競合なし」判定を禁止する。
---

# 国内競合インテリジェンス（Competitor Intelligence）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## このスキルの2つの役割

1. **失格判定の材料**：日本で既に売られている／代理店がいる → [lead-disqualify](../lead-disqualify/SKILL.md) へ差し戻し
2. **提案材料**：競合価格・チャネル・レビューは、営業本文で「なぜ今 Makuake か」を語る根拠になる

## 収集項目とチェックリスト

### A. 当該商品そのものの日本流通（最優先）

これが見つかったら**その時点で失格判定に直結**します。

- [ ] Amazon.co.jp に同一商品があるか
- [ ] 楽天市場・Yahoo!ショッピングにあるか
- [ ] 公式サイトが日本向け販売・日本語ページを持つか
- [ ] 公式サイトの「Where to buy」「Distributors」に日本の記載があるか
- [ ] 日本のクラファン（Makuake / CAMPFIRE / GREEN FUNDING）で実施済みか
- [ ] 並行輸入品が流通していないか

**同一商品の判定は慎重に。** 型番・仕様・ブランド名を照合してください。
似た商品を「同一」と誤認すると、有望案件を誤って失格にします。

### B. 代理店・総代理の有無

| 確認先 | 見るもの |
|---|---|
| 公式サイト Distributors / Partners / Where to buy | 日本の社名が載っているか |
| 日本語で「◯◯ 正規代理店」「日本総代理店」を検索 | 名乗っている企業があるか |
| Amazon 出品者情報 | 販売元が日本法人か、並行輸入業者か |

`source_ownership.KNOWN_AGENCIES` に既知の代理店集合があります。参照してください。

**代理店の存在は失格に直結します**（独占的に扱えない＝Makuake 適性なし）。
ただし「並行輸入業者がいる」だけでは総代理の存在を意味しません。区別してください。

### C. 競合商品（同一ではない類似品）

- [ ] 同カテゴリで日本で売れている商品は何か（価格帯・チャネル）
- [ ] `japanese_success_projects` に同カテゴリの成功事例があるか
- [ ] 日本のクラファンで同種商品が実施されているか

競合の存在は**即失格ではありません**。むしろ「市場が存在する証拠」として使えます。

### D. 価格

| 項目 | 記録 |
|---|---|
| 現地価格（原文通貨） | 原文のまま |
| 日本での実売価格（あれば） | 販売ページURL付き |
| 競合の日本価格帯 | 複数点の実売URL |

**円換算は「試算」と明示**し、レートの取得元と取得日時を残してください（[evidence-ledger](../evidence-ledger/SKILL.md)）。

### E. レビュー・評判（日本市場側）

- [ ] Amazon.co.jp のレビュー（同一商品または競合）
- [ ] 日本語ブログ・レビュー記事
- [ ] SNS での言及

レビューは**引用元URLと確認日時を必ず残します**。件数・評価の平均値を書くときは
「確認時点の値」と明記してください（変動するため）。

## 収集手段

```python
from app.services import search_providers          # Brave Search（SEARCH_PROVIDER=brave）
from app.services import availability_service      # 在庫・販売状況の既存チェック
from app.services import japanese_success_service  # 日本の成功事例
from app.services import company_research_service  # 企業調査
```

既存テーブル: `availability_checks` / `availability_hits`（`url` 付き）/ `japanese_success_projects`

**検索は日本語で行ってください。** 英語で検索すると日本の流通状況は出ません。

## 「見つからなかった」の扱い

> **競合が見つからないことは「競合が存在しない」証明にはなりません。**

```
日本流通: 検索した範囲では未発見
  検索クエリ: "<商品名>", "<商品名> 日本", "<ブランド名> 正規代理店"
  検索先: Brave Search / Amazon.co.jp / 楽天
  確認: 2026-08-05T07:20:00Z
  結論: 「未発見」であり「存在しない」ではない
```

**「競合なし」「日本未展開」と断定しないでください。** 断定するには販売ページの不在を
証明する必要がありますが、それは不可能です。

## 記録

```
japan_sales_checks           ← 日本展開の実地チェック（japan_sales_service.run_check）
availability_checks / hits   ← 在庫・販売状況（url 付き）
japan_opportunity_analyses.evidence_json  ← 根拠URL群
```

鮮度に注意（[evidence-ledger](../evidence-ledger/SKILL.md)）：**在庫・価格・キャンペーン状態は7日**で失効します。

## 禁止事項

- 「競合なし」「日本未展開」の断定
- 類似品を同一商品と誤認して失格にする
- レビュー件数・評価を確認日時なしで記載する
- 円換算値を実売価格として提示する
- 予測（「日本でも売れるはず」）を収集結果に混ぜる（CLAUDE.md §1）

## 関連

- [lead-disqualify](../lead-disqualify/SKILL.md)（流通発見時の差し戻し先）
- [jp-market-fit](../jp-market-fit/SKILL.md) / [makuake-fit](../makuake-fit/SKILL.md)
- [manufacturer-reputation](../manufacturer-reputation/SKILL.md)
