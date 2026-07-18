# 実案件100件 official-site Ground Truth 判定基準（Phase C2）

このドキュメントは `official_site_real100_cases.csv`（Phase C1 生成・**gitignore 済み**）の
Ground Truth（GT）を人手で入力・レビューするための基準を定める。入力は
`review_real100_ground_truth.py`（CLI）で行う。

**個人情報を GT に記録しない**（メール・電話・担当者名・認証情報を書かない）。
evidence_url は**公開 URL のみ**。CSV は git に入れない。

---

## 1. official site の定義
公式サイトとは、次をすべて満たす Web サイト:
- **maker または brand owner が自ら管理**するサイト
- **対象製品またはブランドとの関係が確認できる**（製品名・ブランド名・campaign 一致）
- **campaign platform / 販売代理店 / marketplace / SNS だけ**のページではない

## 2. 優先順位（複数候補がある場合、上位を official に採る）
1. ブランド公式サイト
2. ブランドオーナーの公式サイト
3. 親会社公式サイト
4. 公式ストアサイト（Shopify 等でもそれが唯一の公式接点なら可）
5. official なし

## 3. `official_status`（許可値）
- `confirmed` — 強い証拠で公式サイトを確定
- `none` — 公式サイトが存在しない / 公開されていない
- `ambiguous` — 曖昧（複数候補・所有関係不明）
- `unreachable` — 到達不能で検証できない
- `excluded` — 評価対象外（非メーカー案件・重複等）

## 4. confirmed 条件（最低 1 つ以上の強い証拠が必要）
- campaign からの公式リンク（`campaign_outbound_link`）
- official site 上に maker 名・brand 名・製品名（`product_brand_page`）
- legal / company / about ページで運営主体確認（`legal_company_page`）
- verified SNS から公式サイトへのリンク（`official_social_link`）
- 商標 / ブランド所有情報（`trademark_brand_record`）
- campaign creator との明確な一致

## 5. `evidence_type`（許可値）
`campaign_outbound_link` / `legal_company_page` / `product_brand_page` /
`official_social_link` / `trademark_brand_record` / `marketplace_profile` / `manual_other`

## 6. `confidence`（許可値）
`high` / `medium` / `low` — **`low` はレビュー必須**（`needs_second_review=1`）。

## 7. official なし（原則 `none`）
次のみの場合は公式サイトなし:
- Kickstarter / Indiegogo / Wadiz / Ulule / Zeczec（campaign platform）
- Amazon / marketplace / distributor / reseller / 日本代理店
- Linktree 等のリンク集約
- SNS のみ
- campaign 専用ページのみ

## 8. 曖昧ケース（`ambiguous`・主要 Recall/Precision から除外）
- 同名企業が複数
- parent company と brand owner の関係が不明
- manufacturer と seller のどちらが official か不明
- campaign creator との所有関係が確認できない
- 複数ドメインが同等に公式に見える

## 9. `unreachable`（主要指標から除外）
- DNS 失敗 / 長期間アクセス不能 / domain 失効
- challenge ページのみで内容確認不能
- geography block 等で検証不能

## 10. registered domain の扱い
URL 完全一致ではなく **registered domain（eTLD+1・二段 TLD 考慮）で比較**する。
`www.example.com` / `example.com` / `example.com/about` は**同一**。
次は**別 domain**として扱う:
- separate Shopify store / parent-company site / sub-brand site
- marketplace / campaign platform / distributor / Linktree / SNS

## 11. レビュー方針
- `confidence=low` はレビュー必須。
- `ambiguous` / `unreachable` は主要 Recall/Precision から除外（別集計）。
- `reviewer_note` に判断理由を必ず記載。
- `evidence_url` を最低 1 件記載（公開 URL のみ）。
- `confirmed` の場合、`gt_official_url` と `gt_registered_domain` **必須**。
- `none` の場合、`gt_official_url` と `gt_registered_domain` は**空**。
- **PII（メール・電話・担当者名等）を記録しない**。

## 12. 入力必須マトリクス
| status | gt_official_url | gt_registered_domain | evidence_type | evidence_url | reviewer_note | confidence |
|---|---|---|---|---|---|---|
| confirmed | 必須 | 必須 | 必須 | 必須 | 必須 | 必須 |
| none | 空 | 空 | 任意 | 推奨 | 必須 | 推奨 |
| ambiguous | 任意 | 任意 | 任意 | 推奨 | 必須 | 推奨 |
| unreachable | 任意 | 任意 | 任意 | 推奨 | 必須 | 推奨 |
| excluded | 空 | 空 | 任意 | 任意 | 必須 | 任意 |

## 13. CLI（`review_real100_ground_truth.py`）
```bash
# 進捗表示
python tests/contact_intel_eval/review_real100_ground_truth.py --status
# 未レビュー一覧
python tests/contact_intel_eval/review_real100_ground_truth.py --list-unreviewed
# 1 件レビュー（対話入力・即時 atomic 保存・自動バックアップ）
python tests/contact_intel_eval/review_real100_ground_truth.py --sample-id R001
# バリデーションのみ
python tests/contact_intel_eval/review_real100_ground_truth.py --validate-only
```
既定 CSV: `tests/contact_intel_eval/official_site_real100_cases.csv`（`--csv` で変更可）。
入力のたびに **atomic write（tmp→rename）** + **`.bak` バックアップ**。既存 20 カラム互換を維持し、
`reviewed_at` / `reviewer_version` / `needs_second_review` の 3 列のみ後方互換で追加する。
