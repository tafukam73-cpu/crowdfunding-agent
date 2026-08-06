---
name: japan-distribution-check
description: 対象商品・ブランドが日本で既に正規販売・代理店販売・公式販売されているかを Evidence 付きで確認する。「もう日本で売ってる？」「代理店いる？」「日本法人ある？」「Amazon に出てるけど正規？」「日本未販売って言い切れる？」と言われたときに使う。not_found を「日本未販売」の証明にしないこと、販売ページURL等の一次証拠なしに失格判定しないことが要点。
---

# 日本での流通状況を確認する（Japan Distribution Check）

**「日本で売られていない」は証明が難しい命題です。** 検索で見つからないことは
不在の証明になりません。本スキルは「見つかったこと」を証拠として積み、
見つからなかったことは**見つからなかったという事実のまま**記録します。

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 状態の区別

| 状態 | 意味 |
|---|---|
| `sold_in_japan` | 日本での販売を**実際に確認できた** |
| `not_found` | 主要チャネルを検索したが**確認できなかった**（＝未販売の証明ではない） |
| `inconclusive` | 判定材料が乏しく判断できない |
| `distributor_found` | 正規代理店の存在を確認できた |
| `retailer_only` | 小売の出品のみ確認（正規流通かは不明） |
| `official_japan_site` | ブランド公式の日本向けサイト／日本法人サイトを確認 |
| `marketplace_only` | マーケットプレイス出品のみ（並行輸入・転売の可能性） |

**`retailer_only` / `marketplace_only` を `sold_in_japan`（正規販売）と同一視しないでください。**
Amazon / 楽天の出品は並行輸入・個人転売でも成立します。

## 正本（自前で判定ロジックを作らない）

| 用途 | 正本 |
|---|---|
| 日本販売チェックの実行・保存 | `app/services/japan_sales_service.py`（`get_latest_completed`） |
| 結果の解釈 | `sales_assessment_service.interpret_japan_check()` |
| 除外判定への反映 | `lead_qualification_service` のカテゴリ **F** |

`interpret_japan_check()` は結果を `sold_in_japan` / `not_found_in_japan` /
`inconclusive` と `source_urls` / `confidence` / `checked_at` で返します。
**この語彙をそのまま使ってください。**

## LQE 上の扱い（実装と一致させる）

カテゴリ **F（既に日本正規販売あり）**:

- `sold_in_japan` **かつ販売ページ URL がある**とき → `blocker`
- `sold_in_japan` だが **URL が無い**とき → `review` へ降格（証跡不足）
- `not_found_in_japan` → **`no_hit`**（blocker にしない）
- `inconclusive` / 未実施 → `insufficient_evidence`（severity は `info`）

証跡の鮮度は **90 日**（`lead_qualification_service._FRESHNESS_DAYS["F"]`）。
超えたものは `stale` となり blocker になれません。

> **`not_found_in_japan` は「日本未販売」の証明ではありません。**
> `interpret_japan_check()` 自身も「不在の確証ではない」と明記し、確度に上限を
> 設けています。この設計を上書きしないでください。

## 手順

```
1. japan_sales_service の最新 completed チェックを確認する
   → 無ければ実行を検討（重い処理は job 経由。CLAUDE.md §5）
2. interpret_japan_check() の結果を読む（result / source_urls / checked_at）
3. 見つかったチャネルを種別に分解する
   公式日本サイト / 日本法人 / 正規代理店 / 公式販売ページ / 小売 / マーケットプレイス
4. 正規流通かどうかを、下記 Evidence の型で裏づける
5. evidence-ledger の 4 点セットで記録する
```

## Evidence として強いもの（上ほど強い）

1. **ブランド公式の日本向けサイト**（同一ブランドが運営していることを確認できること）
2. **日本法人のサイト**（登記情報・会社概要で親子関係を確認できること）
3. **公式サイトの Distributors / Where to buy に載る日本の代理店**
4. **メーカー自身の発表**（プレスリリース・ニュース）
5. **日本向け利用規約・特定商取引法表記**
6. **日本のクラファン掲載ページ**（Makuake / CAMPFIRE / GREEN FUNDING）
7. 小売・マーケットプレイスの販売ページ（**単独では正規流通の証拠にならない**）

## やってはいけないこと

- `not_found` を「日本未販売」の**証明として扱う**
- 検索結果ゼロを根拠に「日本未上陸」と**断定する**
- Amazon / 楽天の出品だけで「**正規**販売中」と断定する
- 日本語ページがあるだけで正規流通と断定する（転売業者のページかもしれない）
- 販売ページ URL なしに `F` を `blocker` にする
- 古い販売ページ（90 日超）を根拠に停止する（`stale` として扱う）
- 流通状況を**点数化する**（スコア・確率・％は作らない）

## 出力の形

```
status:       marketplace_only
confidence:   medium
evidence:     Amazon.co.jp に出品あり。出品者はメーカーと無関係の販売業者
source_url:   https://www.amazon.co.jp/dp/XXXXXXX
checked_at:   2026-08-06T09:00:00Z
method:       japan_sales_check
判断:         正規販売の確認には至らない。公式サイトの Distributors を追加確認する
```

確認できなかったときは、そのまま書いてください。

```
status:       not_found
理由:         Amazon / 楽天 / Yahoo / 代理店検索の 4 チャネルで販売を確認できなかった
注意:         これは「日本未販売」の証明ではない
checked_at:   2026-08-06T09:00:00Z
```

## 次の工程

- ブランド所有者・代理店の見分け → [oem-brand-owner-verify](../oem-brand-owner-verify/SKILL.md)
- 競合・価格の把握 → [competitor-intelligence](../competitor-intelligence/SKILL.md)
- 市場性の評価 → [jp-market-fit](../jp-market-fit/SKILL.md)
- 規制の確認 → [regulatory-risk-check](../regulatory-risk-check/SKILL.md)
- 除外判定への反映 → [lead-disqualify](../lead-disqualify/SKILL.md)
