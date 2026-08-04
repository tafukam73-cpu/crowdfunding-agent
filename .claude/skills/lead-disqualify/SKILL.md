---
name: lead-disqualify
description: 営業対象外の案件を早期に徹底排除する。調査コストを掛ける前に「そもそも日本で売れない/売ってはいけない/既に売られている」案件を落とす最初の関門。「この案件やる価値ある？」「対象外を弾きたい」「アーカイブすべき？」「調査する前に絞りたい」と言われたときに使う。contact_search_gate による機械判定を正本とし、推測での失格・通過を禁止する。
---

# 営業対象外の徹底排除（Lead Disqualify）

**このスキルの価値は「見つけること」ではなく「捨てること」です。**
無駄な調査を1件減らすことが、連絡先を1件増やすのと同等の価値を持ちます（CLAUDE.md §1）。

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 正本は機械判定。LLM の印象で覆さない

失格判定は **`app/services/contact_search_gate.py` が正本**です。自前で基準を作らないでください。

```python
from app.services import contact_search_gate
result = contact_search_gate.evaluate(db, project, persist=True)
# → eligible_for_contact_search / contact_search_gate_reason /
#    japan_crowdfunding_score / gate_checked_at / reasons / rationale
```

判定値と閾値（`contact_search_gate.py` の定数。**ここに書き写した値ではなく実装を正とする**）:

| 定数 | 値 | 意味 |
|---|---|---|
| `GATE_ELIGIBLE` | `"eligible"` | 調査に進んでよい |
| `GATE_NEEDS_REVIEW` | `"needs_review"` | 人の判断が要る |
| `GATE_NOT_ELIGIBLE` | `"not_eligible"` | **調査に進まない** |
| `JAPAN_CF_SCORE_THRESHOLD` | 45 | これ以上で eligible |
| `JAPAN_CF_SCORE_REVIEW_FLOOR` | 30 | これ未満は not_eligible |

`require_eligible()` は不適格時に `GateBlocked` を送出します。**この例外を握り潰さないでください。**

## 実行順序

```
1. contact_search_gate.evaluate(db, project) を実行
2. status を確認
   ├─ not_eligible  → 3へ（アーカイブ）
   ├─ needs_review  → 4へ（人に上げる）
   └─ eligible      → 5へ（次工程）
3. projects.archived_at / .archive_reason に理由を記録して終了
4. 判断材料（reasons / rationale）を提示してユーザーに問う。勝手に通さない
5. jp-market-fit → makuake-fit へ進む
```

## 失格カテゴリ（gate 実装が持つ観点）

`contact_search_gate.py` は以下のヒント群で判定します。**この分類を自分で再実装しないでください。**

| 定数 | 落とす対象 |
|---|---|
| `_NON_PHYSICAL_HINTS` | 物理的な商品でない（アプリ・ゲーム・サービス・寄付） |
| `_BULKY_HINTS` | 大型・重量物（輸送コストが成立しない） |
| `_MEDICAL_CLAIM_HINTS` | 医療的効能を謳う（薬機法リスク） |
| `_DANGEROUS_HINTS` | 危険物（輸送・販売規制） |
| `_HEAVY_REGULATION` | medical / supplement / food / cosmetics / nicotine / alcohol 等 |

## gate に無い失格理由（要 evidence）

以下は gate が見ていないため、**根拠URL付きで**追加確認します。推測で失格にしないこと。

| 失格理由 | 確認方法 | 必要な証跡 |
|---|---|---|
| **既に日本で販売中** | 日本語 EC / 公式サイトの日本語ページ / Amazon.co.jp | 販売ページURL + 確認日時 |
| **既に日本のクラファンで実施済み** | Makuake / CAMPFIRE / GREEN FUNDING を商品名で検索 | 該当プロジェクトURL |
| **日本の総代理店が既にいる** | 公式サイトの Distributors / Where to buy ページ | 記載ページURL |
| **キャンペーンが失敗・中止** | `projects` の funding 状態 | campaign_url + 確認日時 |
| **maker が既に廃業・連絡不能** | 公式サイトが消滅（DNS/404） | 確認したURLとHTTPステータス |

**「日本で売られていそう」では失格にできません。** 販売ページのURLを取得できて初めて失格です。
逆に「見つからなかった」ことは「売られていない」証明にはなりません。その場合は
`needs_review` 相当として扱い、失格にはしないでください。

## アーカイブの作法

```sql
BEGIN;
SELECT count(*) FROM projects WHERE id = :id AND archived_at IS NULL;  -- 件数確認（CLAUDE.md §4）
UPDATE projects SET archived_at = now(), archive_reason = :reason WHERE id = :id;
COMMIT;
```

`archive_reason` には**判定理由と根拠URLを書きます**。`"対象外"` だけでは後から検証できません。

```
悪い: "日本向きでない"
良い: "既に日本展開済み: https://www.amazon.co.jp/dp/XXXX (2026-08-05T07:20Z, method=playwright_fetch)"
```

## やってはいけないこと

- gate が `eligible` と言った案件を、LLM の印象で `not_eligible` に落とす
- gate が `not_eligible` と言った案件を、根拠なく通す（`gate_override_reason` を書くなら根拠URL必須）
- 「日本で人気が出なさそう」といった**売れ行き予測**を失格理由にする（CLAUDE.md §1 で予測表示は禁止）
- バッチで大量アーカイブする前に件数確認を省略する

## 次の工程

- 通過 → [jp-market-fit](../jp-market-fit/SKILL.md) → [makuake-fit](../makuake-fit/SKILL.md)
- 商品理解が必要 → [product-page-capture](../product-page-capture/SKILL.md)
