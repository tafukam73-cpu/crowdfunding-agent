---
name: makuake-fit
description: 商品が Makuake（および日本の主要クラファン）に掲載可能・成立可能かを事実ベースで判定する。「Makuakeに向いてる？」「Makuake適性」「日本のクラファンに出せる？」「掲載できる？」「CAMPFIREとどっち？」と言われたときに使う。sales_assessment_service.score_makuake_fit を正本とし、掲載規約への該当は必ず規約ページの根拠URL付きで判定する。
---

# Makuake 適性（Makuake Fit）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [jp-market-fit](../jp-market-fit/SKILL.md) を通していることを確認します。

## 正本は既存スコアラ

```python
from app.services import sales_assessment_service as sas
sas.score_makuake_fit(sig)     # Makuake 適性
sas.assess(sig)                # 総合（japan_market_fit / exclusivity / makuake_fit）
sas.grade(score)               # スコア→グレード
```

## Makuake の構造的な適性要件

Makuake は「**新商品の応援購入**」です。以下は**商品の良し悪しではなく、場との適合**の問題です。

| 要件 | 適合 | 不適合 |
|---|---|---|
| **日本初上陸か** | 日本未発売・未流通 | 既に日本のECで買える → **失格**（[lead-disqualify](../lead-disqualify/SKILL.md) へ差し戻し） |
| **他社日本クラファン実施済みでないか** | 未実施 | Makuake/CAMPFIRE/GREEN FUNDING で実施済み → **失格** |
| **独占的に扱えるか** | 総代理・独占契約が可能 | 既に日本の代理店がいる → **失格** |
| **物理的な商品か** | 実物が届く | アプリ・サービス・寄付 → 不適合 |
| **量産체制があるか** | 量産済み or 量産計画が明示 | 試作のみ・出荷実績なし → 要確認 |
| **リターン設計が可能か** | 単価・SKU が明確 | 価格未定 → 要確認 |

**「日本初上陸か」の確認は必須です。** これを外すと、掲載できない案件に営業工数を投じます。

### 実施済みチェックの手順

```
1. 商品名（英語・カタカナ表記の両方）で Makuake を検索
2. maker 名でも検索（同一 maker の別商品が出ていることがある）
3. CAMPFIRE / GREEN FUNDING でも同様に検索
4. 見つかった場合 → 該当プロジェクトURLを根拠に失格
5. 見つからない場合 → 「検索した範囲で未実施」と記録（未実施の証明にはならない）
```

`japanese_success_projects` テーブルに日本の成功事例が蓄積されています（`source_url` / `maker_url` 付き）。
`japanese_success_service.py` 経由で参照し、**同一商品・同一 maker の重複を先に潰してください。**

## 掲載規約への該当

Makuake には掲載禁止・制限カテゴリがあります。**規約は変わるため、記憶で判断しないでください。**

```
判定が必要になったら、その時点の掲載規約ページを取得して根拠にする。
根拠URLと確認日時を必ず残す（規約は改定される＝日時が意味を持つ）。
```

規制系（技適・PSE・薬機法等）は [jp-market-fit](../jp-market-fit/SKILL.md) で確認済みのはずです。
Makuake は**認証取得済みであることを求める**ため、jp-market-fit で「要取得」となった項目は
**Makuake 適性でも未解決の課題として引き継ぎます**（解決済みとして扱わない）。

## 規模の妥当性

`japanese_success_projects` の実績データと比較します。**予測ではなく実績との対比**です。

```sql
-- 同カテゴリの日本成功事例（実績値）
SELECT title, source_url, /* 支援額等の実績カラム */ *
FROM japanese_success_projects
WHERE /* カテゴリ条件 */
ORDER BY created_at DESC LIMIT 20;
```

報告は「同カテゴリの実績はこの範囲だった」という**事実の提示**に留めます。
**「◯◯万円集まる見込み」という予測は出力しないでください**（CLAUDE.md §1）。

## 保存先

`sales_assessments`（`confidence` 付き）に保存します。
判定理由は根拠URLを含めて残してください（[evidence-ledger](../evidence-ledger/SKILL.md)）。

## 判定を出せない場合

- 商品仕様が未取得 → [product-page-capture](../product-page-capture/SKILL.md) を先に
- maker が未同定 → [maker-identity-verify](../maker-identity-verify/SKILL.md) を先に
- `sas.missing_data(sig)` に必須項目が残っている

## 禁止事項

- 支援額・成功確率の**予測値を出す**
- 掲載規約を記憶で判断する（必ず規約ページを取得する）
- 「実施済みが見つからなかった」を「未実施である」と断定する

## 次の工程

適合 → [maker-identity-verify](../maker-identity-verify/SKILL.md)（誰に営業するかの同定）
