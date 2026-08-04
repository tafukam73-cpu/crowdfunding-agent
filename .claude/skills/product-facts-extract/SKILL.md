---
name: product-facts-extract
description: 取得済み商品ページから、営業判断に使える「事実」だけを出典付きで抽出する。「仕様をまとめて」「事実だけ抜き出して」「この商品の要点」「営業メールに使える情報を」と言われたときに使う。product_facts_service を正本とし、ページに書かれていないことを補完・推測しないこと、訴求文（マーケコピー）と検証可能な事実を分離することが要点。
---

# 商品事実の抽出（Product Facts Extract）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [product-page-capture](../product-page-capture/SKILL.md) でページを取得しておきます。

## 正本は既存サービス

```python
from app.services import product_facts_service as pfs

pfs.build(db, project)              # 総合エントリポイント
pfs.product_facts(db, project)      # 商品仕様
pfs.funding_facts(project)          # 資金調達状況
pfs.maker_facts(db, project)        # maker 情報
pfs.japan_market_facts(db, project) # 日本市場関連
pfs.regulatory_checks(project)      # 規制該当性の一次判定
pfs.compact_facts(project)          # 圧縮版
```

日本語要約・特徴抽出は `product_context_service.build(db, project, gate=...)`。

## 事実と訴求を分離する

クラファンページは**大半がマーケティングコピー**です。これを事実として扱うと、
営業メールで誤った主張をすることになります。

| 分類 | 例 | 扱い |
|---|---|---|
| **検証可能な事実** | `Bluetooth 5.3`, `重量 240g`, `定価 $199`, `出荷予定 2026-10` | ✅ 事実として記録 |
| **測定値（出典明記が必要）** | `バッテリー 30時間`（メーカー公称値） | ⚠️ 「メーカー公称」と明記して記録 |
| **訴求文** | `世界最軽量`, `革命的`, `業界初` | ❌ 事実として記録しない |
| **第三者評価** | `TechCrunch 掲載` | ⚠️ 掲載URLを取得できた場合のみ |

**「世界最軽量」を裏取りせずに営業メールへ転記しない**でください。誤った主張は信用を失います。

## 抽出項目と用途の対応

| 抽出項目 | 主な用途 |
|---|---|
| 無線方式・周波数帯 | 技適判定（[jp-market-fit](../jp-market-fit/SKILL.md)） |
| 電源方式・電池種別・Wh | PSE / 輸送規制判定 |
| 効能・用途の訴求文言 | 薬機法判定（**該当箇所を原文で引用**） |
| 材質・口に入るか | 食品衛生法判定 |
| 寸法・重量 | 輸送コスト・`_BULKY_HINTS` 判定 |
| 価格・SKU 構成 | リターン設計（[makuake-fit](../makuake-fit/SKILL.md)） |
| 配送対象国に日本が含まれるか | 既に日本に届いている＝競合状態の示唆 |
| maker 名・所在地 | [maker-identity-verify](../maker-identity-verify/SKILL.md) |

## 抽出できなかった項目の扱い

**空欄は空欄のまま報告します。** 他の商品や一般知識で埋めないでください。

```
仕様抽出結果:
  無線方式    : Bluetooth 5.3          [出典: campaign_url #tech-specs]
  電池        : リチウムイオン 3000mAh  [出典: campaign_url #tech-specs]
  Wh          : 記載なし（要確認）      ← 埋めない
  重量        : 240g                   [出典: campaign_url #tech-specs]
  日本配送    : 記載なし（要確認）      ← 「対象外」と断定しない
  確認: 2026-08-05T07:20:00Z
```

「記載なし」と「該当しない」は**別の意味**です。混同しないでください。

## 数値の単位と通貨

- 通貨は**原文の通貨のまま**記録し、円換算は「試算」と明示する
- 換算する場合はレートの取得元と取得日時を残す（[evidence-ledger](../evidence-ledger/SKILL.md)）
- 単位（g / oz、mm / inch）は原文単位を保持し、変換値は併記に留める

## 禁止事項

- ページに無い仕様を、同カテゴリの一般的な値で補完する
- 訴求文を事実として営業メールに転記する
- メーカー公称値を第三者検証済みの値として扱う
- 予測値・可能性スコアを生成する（CLAUDE.md §1）

## 次の工程

→ [maker-identity-verify](../maker-identity-verify/SKILL.md) / [jp-market-fit](../jp-market-fit/SKILL.md)
