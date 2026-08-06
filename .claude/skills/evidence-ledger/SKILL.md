---
name: evidence-ledger
description: 根拠URL・確認日時の記録規約。営業判断に関わるあらゆる結論を「推測ゼロ」で残すための横断ルール。他の営業系スキル（maker-identity-verify, official-site-verify, email-ownership-verify, lead-disqualify 等）はすべてこの規約に従う。「根拠は」「出典」「確認日時」「なぜそう判断した」と問われたとき、および営業データを書き込む前に必ず参照する。
---

# 証跡台帳（Evidence Ledger）

このプロジェクトの営業判断における**唯一の憲法**です。他のすべての営業系スキルはこの規約に従います。

## 絶対原則

> **推測で判断しない。根拠が無いなら「不明」と記録する。**

「たぶん公式サイト」「おそらく代表者」「一般的にこのドメインは…」は**すべて禁止**です。
判断できないことは、判断できないまま記録するのが正しい成果物です。

**誤った連絡先を1件作るコストは、連絡先を1件見つけられないコストより遥かに高い**
（誤送信は信用を失い、取り返しがつかない）。迷ったら「不明」を選んでください。

## 1件の結論に必須の4点セット

営業判断を記録するときは、**必ず**以下4点が揃っていることを確認します。1つでも欠けたら記録しません。

| 項目 | 内容 | 欠けた場合 |
|---|---|---|
| **claim** | 何を主張するか（例: `official_site_url = https://example.com`） | — |
| **source_url** | その主張を裏付ける**実際に取得したページのURL** | **記録しない**（「不明」にする） |
| **checked_at** | 取得した日時（ISO8601・UTC） | **記録しない** |
| **method** | どう取得したか（`playwright_fetch` / `brave_search` / `campaign_page_parse` / `whois` 等） | **記録しない** |

### source_url に使ってよいもの・いけないもの

| ✅ 使ってよい | ❌ 使ってはいけない |
|---|---|
| 実際に HTTP 取得して内容を確認したページ | 検索結果に出ただけで未取得のURL |
| 取得した HTML から抽出したリンク先（取得済みなら） | LLM が生成・補完したURL |
| クロール済みの sitemap 由来 URL | 「たぶんこうだろう」と組み立てたURL（`https://<maker>.com` の推測など） |

**URLを組み立てて生成することは禁止です。** 必ず「見つけたURL」を使ってください。

## 保存先（既存カラムを使う。新設しない）

証跡インフラは既に DB にあります。**新しいカラムを勝手に作らないでください。**

| 用途 | テーブル.カラム |
|---|---|
| メールの取得元 | `contact_people.email_source` / `.source_url` / `.confidence` |
| 公式サイトの判定元 | `contact_discoveries.v2_official_site_url` / `.v2_official_site_source` / `.v2_primary_source_url` / `.v2_researched_at` |
| 探索した全URL | `contact_discoveries.searched_urls` / `.web_searched_urls` / `.search_agent_searched_urls`（JSON） |
| 根拠の要約 | `contact_discoveries.evidence_summary` / `.web_evidence_summary` / `.doc_reader_evidence_summary` |
| 手法別の確認日時 | `contact_discoveries.*_researched_at`（`ai_` / `web_` / `doc_reader_` / `search_agent_` / `recursive_crawled_at` / `v2_`） |
| 日本市場適性の根拠 | `japan_opportunity_analyses.evidence_json` / `.opportunity_reasoning` / `.confidence_score` |
| 除外ゲートの理由 | `projects.contact_search_gate_reason` / `.gate_checked_at` |
| 営業対象判定の履歴 | `lead_qualifications`（**append-only**。下記参照） |
| 営業対象判定の最新（一覧用） | `projects.lead_qualification_decision` / `.lead_qualification_at`（**pre_research 専用**） |
| 営業対象外アーカイブ | `projects.archived_at` / `.archive_reason` |

### `lead_qualifications`（追記専用の判定履歴）

| カラム | 内容 |
|---|---|
| `stage` | `pre_research` / `pre_outreach` |
| `decision` | `blocked` / `review` / `clear`（人の上書きを含む**実効判定**） |
| `blocker_codes` / `review_codes` | カテゴリ記号（**機械判定のまま**。上書きで書き換えない） |
| `findings_json` | 全 20 カテゴリの Finding。末尾に予約メタ `_qualification_meta` |
| `positive_facts_json` | 確認できた事実（営業する根拠） |
| `evidence_count` | 4 点セットが揃った証跡の総数 |
| `engine` | 判定ルールのバージョン（`lqe-v1`） |
| `override_reason` / `override_evidence_url` | 人が覆した場合のみ |
| `created_at` | 判定日時 |

**UPDATE / DELETE しません。** 再判定も上書きも「新しい 1 行の追加」で表します。
ルールを変えたときに旧判定と比較できることが、この設計の目的です。

`_qualification_meta` には `machine_decision` / `effective_decision` / `overridden` /
`signals_digest` が入ります。**通常の Finding と混同しないでください**
（Finding は必ず `code` を持つ）。読み出しは
`lead_qualification_service.qualification_meta()` / `.findings_of()` を使います。
**メタは `evidence_count` に加算しません。**

**手法ごとに `*_researched_at` が分かれている点が重要です。** どの手法がいつ何を見つけたかを混ぜないでください。

## 判定の正本（Phase C-1 で main へ入った実装）

**独自ロジックを書かず、以下を呼んでください。** いずれも実案件の誤判定を修正したものです。

| 判定 | 正本 | 何を防ぐか |
|---|---|---|
| プラットフォーム URL の除外 | `campaign_url.is_platform_host()` / `official_site_url_of()` | Kickstarter の `/profile/xxx` を公式サイトとして扱う誤り（実測 104件中99件） |
| 非物理商品の判定 | `contact_search_gate.is_non_physical()` | `"companion app"` / 「アプリ連動」を持つ**物理商品**の誤除外 |
| メール役割の判定 | `source_ownership.classify_email_target()` | 個人情報保護責任者・広報窓口への誤送信 |
| ブログの除外 | `official_site_verifier.is_blog_platform()` | Tistory / Naver ブログ記事を公式サイトとする誤り |
| 小売・取扱店の除外 | `official_site_verifier.looks_reseller_page()` / `has_reseller_hint()` | 販売店サイトを公式サイトとする誤り |
| ディレクトリ / EC / ニュースの除外 | `official_site_verifier.is_directory()` / `is_marketplace()` / `is_news()` | 部分一致により `x.com` が `brandx.com` / `lumix.com` に誤ヒットしていた問題（PR-F で厳密照合へ） |

## confidence の割り当て

`email_validation.email_confidence()` の既定に従います。**独自の基準を作らないでください。**

| level | 日本語 | 条件 |
|---|---|---|
| `high` | 高信頼 | 公式ドメイン一致（contact / footer 由来） |
| `medium` | 要確認 | 公式サイトの規約・プライバシーページ由来 |
| `low` | 低信頼 | クラウドファンディングページ由来 |
| `unverified` | 未検証 | **取得元不明（推測を含む）** |
| `invalid` | 無効 | ダミー / no-reply |

**推測は `unverified` です。`low` に格上げしないでください。**

**`confidence` は「Evidence の確からしさ」を表すラベルです。営業の成功可能性ではありません。**
画面に出すのは `high` / `medium` / `low` / `unverified` のラベルだけで、
数値・パーセント・星には**変換しません**。

## 内部ロケータ（`db://`）の契約

DB の状態そのものが根拠になる場合（campaign_url が保存されていない、maker identity が
未確定 など）に限り、内部ロケータを使います。

- **`db://` を使えるのは `method="db_state"` のときだけ**
- その `source_kind` は必ず **`internal_db`**
- 形式は **`db://projects/<project_id>#<anchor>`** のみ（組み立てて増やさない）
- **外部 URL の代用にしない**（外部で確認すべき事実を `db://` で置き換えない）
- **UI でリンク化しない。** 画面には「保存済みデータ（内部参照）」と表示し、
  `db://` の文字列そのものを露出させない
- 外部リンクにしてよいのは **http(s) だけ**（`is_external_link=true` のとき）
- `source_url` は監査用途で保持してよいが、**画面へ `db://` を出さない**

## override（人が判定を覆す）の契約

- **人の明示操作のみ。** AI / Copilot が自動で override してはいけません
- `reason` 必須（空白のみ不可）
- `evidence_url` 必須。**http(s) のみ**（`db://` / `file://` / ローカルパスは不可）
- 機械判定と同じ decision への override も**監査履歴として許可**する
- **機械判定を削除しない。** `machine_decision` と `effective_decision` を区別して残す

## 確認日時の鮮度

古い証跡は証跡ではありません。判断に使う前に `checked_at` を確認します。

| 対象 | 有効期限の目安 | 超過時 |
|---|---|---|
| メールアドレス | 180日 | 再検証してから使う |
| 公式サイトURL | 365日 | 再検証してから使う |
| 在庫・価格・キャンペーン状態 | 7日 | 必ず再取得 |
| 日本展開の有無 | 90日 | 再検証してから使う |

## 報告時の書式

ユーザーに結論を報告するときは、必ず根拠を併記します。

```
official_site_url: https://example.com
  根拠: https://www.kickstarter.com/projects/xxx/yyy （campaign ページ内の外部リンク）
  確認: 2026-08-05T07:20:00Z / method=playwright_fetch
  判定: official (confidence=high) — サイト内 JSON-LD の Organization.name が maker 名と一致

ceo_email: 不明
  理由: 公式サイト内に個人メールの記載が無く、第三者ソースしか見つからなかった
  探索済み: https://example.com/contact, https://example.com/about, https://example.com/privacy
```

**「不明」を報告することは失敗ではありません。** 誤った値を報告することが失敗です。

## 禁止事項

- 予測値・成功確率・可能性スコアを**ユーザー向けに表示しない**（CLAUDE.md §1）
- `official_site_url` で `campaign_url` を代用しない（逆も同様）
- Ground Truth を推測で確定しない（→ [ground-truth-audit](../ground-truth-audit/SKILL.md)）
- 根拠が1つしかない情報を「確定」として扱わない（要 corroboration の場合は2ソース）
