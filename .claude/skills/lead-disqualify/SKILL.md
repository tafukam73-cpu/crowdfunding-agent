---
name: lead-disqualify
description: 営業対象外の案件を Evidence 付きで早期に排除する。調査コストを掛ける前に「そもそも日本で売れない/売ってはいけない/既に売られている」案件を落とす関門であり、送信直前の関門でもある。「この案件やる価値ある？」「対象外を弾きたい」「アーカイブすべき？」「送っていい案件？」と言われたときに使う。lead_qualification_service による機械判定を正本とし、推測での失格・通過を禁止する。
---

# 営業対象外の判定（Lead Disqualify）

**このスキルの価値は「見つけること」ではなく「捨てること」です。**
無駄な調査を1件減らすことが、連絡先を1件増やすのと同等の価値を持ちます（CLAUDE.md §1）。

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## 正本は機械判定。LLM の印象で覆さない

判定は **`app/services/lead_qualification_service.py`（LQE）が正本**です。
自前で基準を作らないでください。

```python
from app.services import lead_qualification_service as lqs

signals = lqs.gather_signals(db, project)          # 保存済みの事実を読むだけ
result  = lqs.qualify(signals, lqs.STAGE_PRE_RESEARCH)   # 純粋関数（DB/HTTP なし）
# → decision / findings / blocker_codes / review_codes / positive_facts /
#    evidence_count / rule_version / evaluated_at
```

履歴に残す場合は `lqs.run(db, project, stage)`（**append-only で 1 行追加**）。
最新の取得は `lqs.get_latest(db, project_id, stage=...)`。

> `contact_search_gate` は**メール探索専用の狭い関門**として残っており、内部で LQE を
> 参照します（`merge_gate_with_lqe`）。除外判定そのものの正本は LQE です。

## 2 つのステージ

| 定数 | 意味 |
|---|---|
| `lqs.STAGE_PRE_RESEARCH` | 調査（連絡先探索）に進めてよいか |
| `lqs.STAGE_PRE_OUTREACH` | Gmail 下書きを作ってよいか（送信準備） |

**同じ所見でもステージで severity が変わります。** 例: 代理店出品（`D`）は調査前なら
「本当のメーカーを探す価値がある」ので review、送信前なら誤送信そのものなので blocker。

`projects.lead_qualification_decision` / `.lead_qualification_at` は
**pre_research 専用のスナップショット**です。pre_outreach の判定は履歴にのみ残り、
一覧の絞り込み（`GET /projects?qualification=`）にも影響しません。

## 判定値（実装の定数を正とする）

| 種類 | 値 |
|---|---|
| decision | `lqs.DECISION_BLOCKED` / `DECISION_REVIEW` / `DECISION_CLEAR` |
| verdict | `VERDICT_HIT` / `VERDICT_NO_HIT` / `VERDICT_INSUFFICIENT` / `VERDICT_STALE` |
| severity | `SEVERITY_BLOCKER` / `SEVERITY_REVIEW` / `SEVERITY_INFO` |
| confidence | `high` / `medium` / `low` / `unverified`（**ラベルのみ。数値にしない**） |

## 集約の不変条件（LQE が機械的に強制する）

1. **Evidence 4 点セット（claim / source_url / checked_at / method）が揃わない
   blocker は review へ強制降格**する
2. **`stale`（鮮度切れ）は blocker になれない**
3. `verdict` が `hit` 以外は blocker になれない
4. **decision は「最も重い Finding」で決まる。点数の合算はしない**
5. **証拠が無いことを停止根拠にしない**（never infer。不明は不明のまま）

## 20 カテゴリ（`lqs.CATEGORY_LABELS` が正本）

🛑=blocker / ⚠=review / ℹ=info

| | カテゴリ | pre_research | pre_outreach | 要点 |
|---|---|---|---|---|
| A | 日本市場不適合 | ⚠ | ℹ | **日本展開上の負担確認**であり、主観的な市場不適合の断定に使わない。**blocker にしない** |
| B | Makuake 向きではない | 🛑/⚠ | ℹ | 日本CF掲載ページURL があるときだけ blocker |
| C | OEM 商品の可能性 | ⚠ | ⚠ | **常に review。推測で block しない** |
| D | 代理店・販売店のみ | ⚠ | 🛑 | `classify_domain` の分類＋判定元URL が必要 |
| E | メーカー未確認 | ℹ/⚠ | 🛑 | 送信段階で maker 未確定は誤送信そのもの |
| F | 既に日本正規販売あり | 🛑 | 🛑 | **販売ページURL 必須。`not_found` は blocker にしない** |
| G | 規制リスク（総称） | ℹ | ℹ | H〜M の集約フラグ。単独で判定を動かさない |
| H〜M | 食品/医療/化粧品/無線/電源PSE/技適 | ⚠ | ℹ | **blocker にしない**（→ [regulatory-risk-check](../regulatory-risk-check/SKILL.md)） |
| N/O/P | デジタル/ソフトのみ/サービスのみ | 🛑 | 🛑 | **STRONG 一致のみ blocker**。WEAK のみは review、物理商品語があれば no_hit |
| Q | KS 限定で終売 | 🛑/⚠ | ⚠ | 終売の一次情報があるときだけ blocker |
| R | 既に大量流通 | 🛑/⚠ | ℹ | 販売ページURL 3 件以上。ブランド名一致だけでは blocker にしない |
| S | ブランド所有者不明 | ℹ/⚠ | ⚠ | 公式サイト未検証。単独で送信を止めない |
| T | 情報不足 | 🛑 | 🛑 | campaign_url 欠落・日本語概要生成不可（DB 状態が証拠） |

**この分類を自分で再実装しないでください。**

## positive_facts（営業する根拠）

LQE は「止める理由」だけでなく**確認できた事実**も返します
（`lqs.POSITIVE_FACT_LABELS`）。証跡が揃ったものだけが載ります。

```
campaign_url_verified / physical_product_confirmed / maker_name_present /
official_site_verified / maker_identity_verified / business_contact_found /
decision_maker_found / japan_sales_check_completed
```

## entity_role（補助属性）

Finding は `entity_role` を持ちます（`lqs.ENTITY_ROLES`：brand_owner / manufacturer /
factory / oem / odm / private_label / distributor / retailer / importer / agency /
unknown）。**証拠が無ければ `unknown`**。詳細は
[oem-brand-owner-verify](../oem-brand-owner-verify/SKILL.md)。

## 人の判断で覆す（override）

```python
row, changed = lqs.record_override(
    db, project, lqs.STAGE_PRE_OUTREACH, lqs.DECISION_CLEAR,
    reason="公式サイトの会社概要でメーカー本人と確認した",
    evidence_url="https://example.com/company",
)
```

- **append-only**。機械判定を削除せず、履歴を 1 行追加する
- `reason` と `evidence_url` は**両方必須**（`evidence_url` は http(s) のみ）
- 機械判定と同じ decision でも許可（監査記録。`changed=False` が返る）
- `findings_json` 末尾の `lqs.META_KEY`（`_qualification_meta`）に
  `machine_decision` / `effective_decision` / `overridden` が入る
  （`lqs.qualification_meta(row)` で取得、通常 Finding は `lqs.findings_of(row)`）
- **AI / Copilot が自動で override してはいけません。** 人の明示操作だけです

## 実行順序

```
1. gather_signals → qualify（または run で履歴保存）
2. decision を確認
   ├─ blocked → 3 へ（アーカイブを提案）
   ├─ review  → 4 へ（人に上げる）
   └─ clear   → 5 へ（次工程）
3. アーカイブを**提案**する。**自動アーカイブは禁止**
4. blocker_codes / review_codes と各 Finding の reason を提示して人に問う
5. jp-market-fit → makuake-fit へ進む
```

## アーカイブの作法

**自動アーカイブは行いません。** 人が実行するときだけ、理由をプリフィルします。

```sql
BEGIN;
SELECT count(*) FROM projects WHERE id = :id AND archived_at IS NULL;  -- 件数確認（§4）
UPDATE projects SET archived_at = now(), archive_reason = :reason WHERE id = :id;
COMMIT;
```

`archive_reason` には判定理由を書きます。**証跡URL・メールアドレス・`db://` は含めません。**

```
悪い: "日本向きでない"
良い: "営業対象判定：対象外（コード: E, T）"
```

## やってはいけないこと

- LQE が `clear` と言った案件を、LLM の印象で対象外に落とす
- LQE が `blocked` と言った案件を、根拠なく通す（override するなら根拠URL必須）
- **売れ行き予測・返信率・成功率を失格理由にする**（CLAUDE.md §1 で予測表示は禁止）
- 証拠なしに blocker を立てる／`not_found` を「日本未販売」の証明にする
- G〜M（規制）を blocker にする／C（OEM 疑い）で止める
- 判定を**点数化・合算する**
- バッチで大量アーカイブする前に件数確認を省略する

## 次の工程

- 規制の確認 → [regulatory-risk-check](../regulatory-risk-check/SKILL.md)
- ブランド所有者・OEM の確認 → [oem-brand-owner-verify](../oem-brand-owner-verify/SKILL.md)
- 日本での流通状況 → [japan-distribution-check](../japan-distribution-check/SKILL.md)
- 通過 → [jp-market-fit](../jp-market-fit/SKILL.md) → [makuake-fit](../makuake-fit/SKILL.md)
- 商品理解が必要 → [product-page-capture](../product-page-capture/SKILL.md)
- 送信前の関門 → [outreach-gate](../outreach-gate/SKILL.md)
