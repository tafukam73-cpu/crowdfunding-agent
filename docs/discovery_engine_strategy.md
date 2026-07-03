# Discovery Engine 戦略設計書 — 日本市場向け商品発掘 AI

> ステータス: **設計ドキュメント（実装なし）**
> 対象バージョン: v1-1 〜 v1-3 実装済み / v1-4 以降は計画
> 最終更新: 2026-07-03

このドキュメントは、Discovery Engine を「**海外クラウドファンディング商品を、日本市場
向けに発掘・評価・営業接続するための AI パイプライン**」として整理し、実装済み範囲・
全体アーキテクチャ・評価軸・ロードマップ・実装原則・次バージョン（v1-4）の仕様案を
まとめる。本書は方針の共通認識づくりが目的で、コードは追加しない。

---

## 1. Discovery Engine の目的

海外クラウドファンディング（Kickstarter / Indiegogo / BackerKit など）には、日本に
まだ入っていない魅力的なプロダクトが日々生まれている。Discovery Engine のゴールは、
それらを**人手の探索に依存せず継続的に発掘し、日本市場でのポテンシャルを定量評価し、
営業アクションにつなげる**ことである。

- **発掘（Discovery）**: 海外 CF 等から商品候補を収集し、正規化・重複排除して蓄積する
- **評価（Scoring）**: 「日本のクラファン／物販で当たるか」を多軸で 0〜100 に定量化する
- **接続（Opportunity → Contact → Sales）**: 有望候補をメーカー特定・連絡先探索・
  営業案件化のパイプラインに引き渡す

つまり Discovery Engine は「**日本市場向け商品発掘 AI の入口**」であり、後段の
Contact Intelligence / Sales Opportunity と一本の線でつながる前提で設計する。

### スコープの原則
- 発掘対象は**掲載中に限らない**。successful / ended / failed / canceled も
  「商品・メーカーの実在シグナル」として保持する（除外しない）。
- 判断は最終的に人間（営業担当）が行う。AI は**優先順位づけと根拠提示**に徹する。

---

## 2. 現在実装済みの範囲

### v1-1 Product Foundation（コミット 425abf8）
発掘商品候補の永続化土台。

- `discovered_products` テーブル / `DiscoveredProduct` モデル
- 発掘元 `DiscoverySourcePlatform`（kickstarter / indiegogo / backerkit / backertracker /
  crowdsupply / gamefound / producthunt / manual / other）
- キャンペーン状態 `DiscoveredProductStatus`（live / successful / ended / failed /
  canceled / preorder / unknown）
- `source_url` をユニークキーとした重複登録防止
- CRUD API（`POST/GET/GET{id}/PATCH /discovery/products`）と一覧フィルタ
  （platform / status / category / min_score）・並び替え（score / created）
- スコア系カラムは nullable（AI 評価は v1-2 で付与）

### v1-2 AI Discovery Scoring Engine（コミット 30bcccd）
発掘候補を多軸評価するスコアリングエンジン。

- `discovery_scoring_service.score(product, ai_fn=None)`
- **AI 注入 + フォールバック設計**: `ai_fn` を注入すれば AI 評価、未指定・例外・
  不正 JSON のときはルールベース評価に自動フォールバック（実 API キー不要で必ず動く）
- 出力は 0〜100 に正規化した 7 軸 + 総合 + reasoning + next_action
- `POST /discovery/products/{id}/score`、および作成時 `auto_score=true`
- カテゴリ辞書（高評価 / 要注意）と支援者数・調達額から加減点するルール

### v1-3 Discovery Crawler Framework（コミット e877b68）
実サイト取得に向けた**共通収集フレームワーク**と adapter 構造。

- 共通型 `DiscoveryCandidate`（発掘元非依存の候補表現）
- `BaseAdapter` の抽出カスケード: **JSON-LD → 埋め込み JSON → meta タグ → 一般 HTML**
  （サイト構造変更に弱すぎない多段フォールバック）
- 4 adapter: `kickstarter` / `indiegogo` / `backerkit` / `manual` + `get_adapter()`
- `discovery_crawler_service.run(...)`: adapter 選択 → URL 正規化 → 実行内/DB 重複排除
  → 保存 → `auto_score` → 実行サマリ返却
- `discovery_runs` テーブルで実行ログ（found / saved / duplicate / error）を記録
- `POST /discovery/run` API
- **`fetch_fn` 注入方式**: 実ネットワークは既定で行わず、注入時のみ取得。テストは
  fixture（HTML / JSON）で完結。外部 API・課金・スクレイピングサービスは不使用。

> 補足: v1-3 時点で実サイトの本番 fetch は未接続（枠組みのみ）。実接続は v1-6 で行う。

---

## 3. 今後の全体アーキテクチャ

Discovery Engine は単体で完結せず、既存の Contact Intelligence / Sales Opportunity と
一本のパイプラインを構成する。データは左から右へ流れ、各段は前段の出力を入力にする。

```
┌─────────────┐   ┌──────────────┐   ┌────────────────────────┐   ┌──────────────────────┐   ┌────────────────────┐
│  Discovery  │──▶│  AI Scoring  │──▶│ Japan Opportunity       │──▶│ Contact Intelligence │──▶│ Sales Opportunity  │
│  (収集)      │   │  (多軸評価)   │   │ Engine (日本機会の判定)  │   │ (メーカー/連絡先探索)  │   │ (営業案件化・追跡)   │
└─────────────┘   └──────────────┘   └────────────────────────┘   └──────────────────────┘   └────────────────────┘
   v1-3            v1-2                v1-5 (foundation)             既存 (CI v3/v5)            既存 (Sales Opp)
   crawler         scoring             ★これから                    official-site crawler       maker/交渉ステータス
```

### 各コンポーネントの責務

| コンポーネント | 責務 | 実装状況 | 主なデータ |
|---|---|---|---|
| **Discovery** | 海外 CF 等から候補収集・正規化・重複排除・蓄積 | v1-3 済（実 fetch は v1-6） | `discovered_products` / `discovery_runs` |
| **AI Scoring** | 候補を日本市場観点で 0〜100 多軸評価 | v1-2 済 | `discovered_products` のスコア列 |
| **Japan Opportunity Engine** | スコア群を「日本での事業機会」として統合判定し、優先度・想定シナリオ（クラファン／総代理店等）を提示 | **v1-5 で新設** | 新テーブル想定（`japan_opportunities`） |
| **Contact Intelligence** | 有望候補のメーカー特定・公式サイト再帰クロール・連絡先探索 | 既存（CI v3/v5） | `contact_discovery` / `contact_people` |
| **Sales Opportunity** | 営業案件化・交渉ステータス・次アクション・リマインダー | 既存 | `sales_opportunities` / `crm` |

### 接続点（ハンドオフ）
- Discovery → Scoring: `discovered_products` 行に対し `auto_score` またはバッチで付与
- Scoring → Japan Opportunity: 総合スコア・リスク軸から機会レコードを生成（v1-5）
- Opportunity → Contact Intelligence: `discovered_products.contact_discovery_id` で緩く接続
  （v1-1 で確保済みのフィールドを活用）
- Contact → Sales: 既存の CI → Sales Opportunity 連携に合流

---

## 4. 日本市場向け評価軸

AI Scoring / Japan Opportunity Engine が用いる評価軸。**スコアはすべて「高いほど
日本参入に有利」で統一**する（リスク軸も “高い = リスクが低い（安全）” とする）。

### 4.1 実装済みの軸（v1-2・`discovered_products` に格納）

| 軸 | カラム | 意味（高い=良い） |
|---|---|---|
| 日本市場適合度 | `japan_fit_score` | 日本の生活・嗜好・住環境への適合。小型・軽量・日用品ほど高い |
| クラファン適性 | `crowdfunding_fit_score` | Makuake / GREEN FUNDING 等で話題化しやすいか。実績（支援者数・調達額）も加味 |
| 日本未進出可能性 | `japan_entry_risk_score` | 日本にまだ入っていない見込み（“未上陸なら高い”）。※ v1-5 で `japan_sales_check` の実判定と統合予定 |
| 物流難易度 | `logistics_score` | 輸入・配送のしやすさ（技適 / PSE / 大型バッテリー等は低い） |
| 法規制リスク | `regulatory_risk_score` | 許認可・輸入規制の軽さ（医療・食品・化粧品・無線・危険物等は低い） |
| 競合リスク | `competition_risk_score` | 日本国内の競合の少なさ |
| （新規性） | `novelty_score` | プロダクトの新しさ・独自性 |
| **総合発掘スコア** | `overall_discovery_score` | 上記の重み付き合成（一覧の既定ソートキー） |
|（根拠）| `discovery_reasoning` / `recommended_next_action` | 評価理由と推奨アクション |

現行の総合スコア重み（v1-2 ルールベース）:
`japan_fit 0.30 / crowdfunding_fit 0.25 / novelty 0.15 / logistics 0.10 /
regulatory_risk 0.10 / competition_risk 0.05 / japan_entry_risk 0.05`。

### 4.2 今後追加を検討する軸（未実装・v1-5 候補）

| 軸 | 想定カラム | 意味 | 主なデータ源 |
|---|---|---|---|
| **利益率見込み** | `profit_margin_score` | 想定仕入（CF 価格帯）と日本想定売価の差から粗利ポテンシャルを推定 | funding_amount / 想定売価・関税・送料モデル |
| **営業成功可能性** | `sales_success_score` | メーカーが日本展開・独占販売に応じる見込み（連絡先の質・企業規模・過去反応） | Contact Intelligence の探索結果 |

> ⚠️ 「利益率見込み」「営業成功可能性」は現行モデルに**未実装**。v1-5 の Japan
> Opportunity Engine で、Contact Intelligence / 価格モデルの入力が揃ってから追加する。
> 追加時も「高い=有利」「AI 失敗時はルールフォールバック」の原則を踏襲する。

### 4.3 評価軸の使い分け
- **一次スクリーニング**（Discovery/Scoring）: 4.1 の軸で機械的に順位づけ
- **機会判定**（Japan Opportunity Engine）: 4.1 + 4.2 を統合し、シナリオ
  （クラファン先行 / 総代理店 / EC 直販）と優先度を提示

---

## 5. 今後のロードマップ

各バージョンは「小さく実装 → テスト → コミット」で 1 段ずつ積む。

### v1-4 Discovery UI（次バージョン・詳細は §7）
- 発掘候補の一覧・詳細・スコア可視化・手動発掘（run）トリガーのフロント画面
- バックエンドは既存 API（v1-1〜v1-3）を利用し、**新規 API は最小限**

### v1-5 Japan Opportunity Engine foundation
- 「機会（opportunity）」を一級概念として導入（`japan_opportunities` 想定）
- 4.2 の追加軸（利益率見込み・営業成功可能性）の土台
- Discovery → Contact Intelligence への自動ハンドオフ（`contact_discovery_id` 連携）

### v1-6 実サイト fetch 連携
- v1-3 の `fetch_fn` に本番フェッチャ（既存 `app/scrapers/fetcher` の httpx/playwright）を注入
- レート制限・UA ローテーション・403/429 バックオフ・構造変化検知
  （既存スクレイパー安定化の知見を再利用）
- robots / 利用規約の遵守チェックを収集前ゲートに組み込む

### v2 自動発掘バッチ
- 既存スケジューラ（APScheduler / `collection_job` / `job_locks` 二重実行防止）に相乗り
- 定期実行で `discovery/run` を各 platform × クエリで回し、`discovery_runs` に蓄積
- 新規高スコア候補を Slack 等（既存 `app/notifications`）に通知

---

## 6. 実装上の原則

Discovery Engine 全バージョンで守る不変の原則。

1. **小さく実装してコミット**
   1 バージョン = 1 つの明確な増分。RED（落ちるテスト）→ GREEN（最小実装）→ commit。
   大規模リファクタリングは行わない。

2. **既存機能を壊さない**
   既存 API・既存テストを無破壊で維持する（例: v1-3 は `/discovery/products` 系・
   `test_discovery` / `test_discovery_scoring` を一切変更せず 20 / 38 passed を維持）。
   スキーマ変更は追加のみ・後方互換を保つ。

3. **`fetch_fn` 注入可能**
   取得は関数注入で差し替え可能にする。実ネットワークは既定で行わず、注入時のみ。
   これによりテスト・DRY_RUN・本番接続を同じコードパスで切り替えられる。

4. **外部 API なしでもテスト可能**
   すべての検証は API キー・ネットワーク不要で完結する。テストは fixture（HTML/JSON）
   と sqlite で行い、`python tests/test_xxx.py` 形式（pytest 非依存）を踏襲する。

5. **AI 失敗時はフォールバック**
   AI 呼び出しは未指定・例外・不正 JSON のいずれでもルールベースに倒し、必ず
   0〜100 の全スコアを返す（例外を投げない）。AI は品質を上げる “オプション” であり、
   可用性の前提にはしない。

6. **安全則（横断）**
   破壊的・外部送信・課金操作は既定で DRY_RUN / 下書き / 人間承認。終了済み案件
   （successful/ended/failed/canceled）も除外しない。不明値は None / unknown で安全に処理。

---

## 7. 次に実装すべき v1-4 の仕様案

### 7.1 目的
実装済みの発掘・評価・収集（v1-1〜v1-3）を**人が使える画面**にする。営業担当が
「今日見るべき有望候補」を素早く把握し、手動での発掘実行と候補精査ができる状態にする。

### 7.2 スコープ（IN）
- **発掘候補 一覧画面** `/discovery`
  - スコア降順（既定）/ 作成日順の並び替え
  - フィルタ: platform / status / category / min_score
  - 各行に総合スコア・主要リスク軸のバッジ・ステータス・発掘元
- **候補 詳細画面** `/discovery/[id]`
  - 基本情報（タイトル・メーカー・国・画像・source_url）
  - スコア 7 軸のビジュアル（バー / レーダー）＋ `discovery_reasoning` ＋
    `recommended_next_action`
  - 「再スコアリング」ボタン（`POST /discovery/products/{id}/score`）
  - 「メーカーを調べる」導線（既存 Contact Intelligence への遷移。
    `contact_discovery_id` があれば結果へ、なければ探索起動）
- **手動発掘（run）パネル**
  - platform / query / limit / auto_score を指定して `POST /discovery/run`
  - 実行結果サマリ（found / saved / duplicate / error）と直近 `discovery_runs` 履歴表示

### 7.3 スコープ（OUT / DEFER）
- 実サイトからの本番 fetch（→ v1-6）。v1-4 の run は既存挙動どおり、
  fetch_fn 未注入で found=0 になり得る旨を UI に明示する。
- 利益率見込み・営業成功可能性の表示（→ v1-5 で軸追加後）
- 自動バッチ・通知（→ v2）

### 7.4 バックエンド追加（最小）
既存 API で概ね賄えるが、UI 利便のため以下の**追加のみ**を検討（後方互換）:
- `GET /discovery/runs`: `discovery_runs` の実行履歴一覧（新しい順・platform フィルタ）
- （任意）`GET /discovery/products` に `q`（タイトル部分一致）パラメータ追加

> 既存の `discovered_products` スキーマ・`/discovery/products` 系の挙動は変更しない。

### 7.5 フロント構成（既存 Next.js 14 / TypeScript / Tailwind に準拠）
- `frontend/app/discovery/page.tsx`（一覧）
- `frontend/app/discovery/[id]/page.tsx`（詳細）
- `frontend/components/discovery/*`（ScoreBars / RunPanel / FilterBar / StatusBadge）
- `frontend/lib/` に API クライアント関数を追加（既存の命名・fetch ラッパに合わせる）

### 7.6 受入基準（Given-When-Then 抜粋）
- **一覧表示**: Given 発掘候補が複数存在 / When `/discovery` を開く /
  Then 総合スコア降順で一覧され、フィルタ操作で件数が絞り込まれる。
- **詳細と再スコア**: Given 候補詳細を開いた / When「再スコアリング」を押す /
  Then `POST /discovery/products/{id}/score` が呼ばれ、7 軸と reasoning が更新表示される。
- **手動 run**: Given run パネルで platform/query を指定 / When 実行 /
  Then サマリ（found/saved/duplicate）が表示され、`discovery_runs` 履歴に 1 行増える。
- **無破壊**: Given 既存の案件一覧・CRM・Contact Intelligence 画面 / When v1-4 追加後 /
  Then いずれも従来どおり動作する（既存テスト・既存画面を壊さない）。

### 7.7 テスト方針
- バックエンド追加分（`GET /discovery/runs` 等）は既存流儀の
  `tests/test_discovery_*.py`（sqlite・pytest 非依存）で検証。
- フロントは既存のフロントテスト方針に合わせる（無ければ最小のコンポーネント表示確認）。
- 実ネットワーク・外部 API・課金は発生させない。

---

## 付録: 関連ファイル早見

| 領域 | 主なファイル |
|---|---|
| モデル | `backend/app/models/discovered_product.py` / `discovery_run.py` |
| スキーマ | `backend/app/schemas/discovery.py` |
| サービス | `backend/app/services/discovery_service.py` / `discovery_scoring_service.py` / `discovery_crawler_service.py` |
| adapter | `backend/app/services/discovery_adapters/`（base / kickstarter / indiegogo / backerkit / manual） |
| API | `backend/app/routers/discovery.py` |
| マイグレーション | `backend/alembic/versions/0035_discovered_products.py` / `0036_discovery_runs.py` |
| テスト | `backend/tests/test_discovery.py` / `test_discovery_scoring.py` / `test_discovery_crawler.py` |
| 連携先（既存） | Contact Intelligence（`contact_discovery` / `contact_people`）・Sales Opportunity（`sales_opportunities` / `crm`） |
