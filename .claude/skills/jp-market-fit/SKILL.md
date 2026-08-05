---
name: jp-market-fit
description: 海外クラファン商品の日本市場適性を、規制・競合・価格・需要の観点から事実ベースで判定する。「日本で売れる？」「日本市場に合う？」「技適は？」「PSE要る？」「薬機法に触れる？」「日本適性を評価して」と言われたときに使う。sales_assessment_service.score_japan_market_fit を正本とし、規制該当性は必ず根拠URL付きで確認する。
---

# 日本市場適性（Japan Market Fit）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [lead-disqualify](../lead-disqualify/SKILL.md) を通過していることを確認します。

## 正本は既存スコアラ

```python
from app.services import sales_assessment_service as sas

sas.score_japan_market_fit(sig)   # 日本市場適性
sas.score_exclusivity(sig)        # 独占可能性
sas.assess(sig)                   # 総合
sas.missing_data(sig)             # 不足データの列挙 ← 重要
sas.assess_with_japan(db, project, auto_check=True)   # japan_sales_check 込み
```

日本展開状況の実地チェックは `japan_sales_service.run_check()` が担当し、
結果は `japan_sales_checks` に入ります。`sas.interpret_japan_check()` で解釈します。

**`missing_data()` が返す項目は「まだ判定できない」という意味です。**
埋まっていない項目を推測で埋めてスコアを出さないでください。不足のまま報告します。

## 規制該当性は必ず根拠URLを取る

日本の規制は失格に直結します。**該当しそう/しなさそうの印象で判断しないでください。**
`product_facts_service.regulatory_checks(project)` が一次判定を返します。

| 規制 | 該当する商品 | 確認すべき事実 | 根拠として使えるもの |
|---|---|---|---|
| **技適（電波法）** | Wi-Fi / Bluetooth / 無線を持つ全て | 無線モジュールの有無・周波数帯 | 商品ページの仕様表・技術仕様PDF |
| **PSE（電安法）** | AC電源・リチウムイオン電池 | 定格電圧・電池種別・容量 | 仕様表・認証マーク画像 |
| **薬機法** | 効能を謳う・肌に触れる・医療機器的 | 訴求文言そのもの | 商品ページの説明文（該当箇所を引用） |
| **食品衛生法** | 口に入る・食品に触れる | 材質・用途 | 仕様表 |
| **消防法 / 航空輸送** | リチウム電池・可燃物 | Wh数・UN38.3 の有無 | 仕様表・輸送表記 |

### 記録の型

```
技適: 該当（要取得）
  事実: Bluetooth 5.3 / 2.4GHz を搭載
  根拠: https://www.kickstarter.com/projects/xxx/yyy  (Tech Specs セクション)
  確認: 2026-08-05T07:20:00Z / method=playwright_fetch
```

**「Bluetooth があるはず」は禁止です。** 仕様表に書かれていなければ「仕様表に記載なし・要確認」です。

## 競合・既存流通の確認

| 観点 | 確認方法 | 判定 |
|---|---|---|
| 同種商品が日本で既に売られているか | 日本語で商品カテゴリ検索 | 競合URLを列挙（存在＝即失格ではない） |
| 当該商品自体が日本で流通しているか | 商品名 + 日本語 EC | **販売ページが取れたら [lead-disqualify](../lead-disqualify/SKILL.md) へ差し戻し** |
| 価格帯が日本市場と乖離していないか | 現地価格 × 為替 + 関税/送料 | 実売価格URLを根拠に |

検索には Brave Search（`SEARCH_PROVIDER=brave`、疎通確認済み）を使います。
`search_providers.py` 経由で呼び、**検索結果に出ただけのURLを根拠にしないでください**
（実際に取得して内容を確認したものだけが根拠です → evidence-ledger）。

## 保存先

```
japan_opportunity_analyses.evidence_json        ← 根拠URL群（JSON）
japan_opportunity_analyses.opportunity_reasoning ← 判定理由（文章）
japan_opportunity_analyses.confidence_score     ← 確度
japan_sales_checks                              ← 日本展開の実地チェック結果
```

現状 `japan_opportunity_analyses` は **309案件中8件**しか埋まっていません（2026-08-05 実測）。
カバレッジを上げること自体が営業効率に直結します。

## 判定を出せない場合

以下は**判定不能として報告**します。無理にスコアを出さないでください。

- 商品ページから仕様が取得できなかった（→ [product-page-capture](../product-page-capture/SKILL.md) を先に実行）
- `missing_data()` に必須項目が残っている
- 規制該当性が仕様不足で確定できない

## 禁止事項

- 「日本人に受けそう」等の**需要予測を判定根拠にする**（CLAUDE.md §1）
- 規制該当性を仕様表の確認なしに断定する
- 為替・関税を概算で出して価格競争力を断定する（試算と明示し、レートの取得元と日時を書く）

## 次の工程

→ [makuake-fit](../makuake-fit/SKILL.md)
