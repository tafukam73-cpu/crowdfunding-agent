# 海外クラファン案件発掘・営業支援システム

海外クラウドファンディング（Kickstarter / Indiegogo / Wadiz）案件を収集し、
日本向けクラファン商品の発掘と営業支援を行うローカル Web アプリ。

## 構成

- **backend** … FastAPI（Python 3.12）
- **frontend** … Next.js 14 + TypeScript + Tailwind CSS
- **db** … PostgreSQL 16
- すべて Docker Compose で起動

## 現状（Step 5：Makuake 連携まで）

海外案件の収集・AI 評価・営業メール下書き・コスト管理に加え、日本クラファン
（Makuake）の成功事例を比較用に蓄積し、海外案件ごとに「類似する日本の成功事例」を
自動提示する。

- 要件定義の取得項目に対応した `projects` テーブル
- Alembic マイグレーション管理
- 案件 CRUD API（一覧フィルタ・ソート・ページング／詳細／作成・更新／ステータス更新）
- スクレイピング（Kickstarter / Indiegogo は実装、他はダミー配線）
- AI 評価・営業メール下書き生成・AI 利用コスト集計
- **日本クラファン成功事例（`japanese_success_projects`）**
  - Makuake / GreenFunding スクレイパー（現状モック。実スクレイパーへ差し替え可能な構造）
  - 成功事例の収集・一覧 API（プラットフォーム絞り込み対応）
  - 海外案件への類似事例提示 API（カテゴリ一致・達成率・共通キーワードで類似度算出）
- Next.js 案件一覧・詳細画面、日本成功事例一覧画面（プラットフォーム絞り込み・収集）
- 起動時にモックデータを自動投入（DB が空のときのみ）
- **日次自動収集スケジューラ（APScheduler）＋ ジョブ堅牢化**
  - 毎日決まった時刻に 4 サイト（Kickstarter / Indiegogo / Makuake / GreenFunding）を収集
  - 実行時刻は cron 式で設定（`SCRAPE_SCHEDULE_CRON`、既定 `0 3 * * *` / `Asia/Tokyo`）
  - 手動「今すぐ実行」と日次実行は**同一ジョブ**（`collection_job.run_collection`）を使用
  - **二重実行防止**：`job_locks`（PK 一意制約）による DB ロックで多重ワーカー／同時起動でも 1 実行に収束。古いロックは奪取して再実行可
  - **ジョブ実行履歴**：`job_runs`（trigger・status・成功/失敗サイト数・エラー・時刻）
  - **サイト単位の結果**：`scrape_runs`（`job_run_id` でジョブに紐づく）
  - ダッシュボードに最新ジョブ・次回予定・サイト別の最終実行とエラー内容を表示

- **CRM（営業管理）**
  - メーカー（企業）管理：交渉ステータス（リード/連絡済み/交渉中/成約/見送り）・次回アクション・リマインダー
  - 担当者管理（複数）、営業履歴（メール/電話/打合せ/メモ）の記録
  - 海外案件（projects）からワンクリックでメーカー登録・紐づけ（`maker_id`）
  - 次回アクション日に基づくリマインダー（期限切れ表示）

### CRM の主な API

| メソッド | パス | 説明 |
| --- | --- | --- |
| GET/POST | `/crm/makers` | メーカー一覧（status/検索）／作成 |
| GET/PATCH/DELETE | `/crm/makers/{id}` | メーカー詳細（担当者・履歴・紐づく案件）／更新／削除 |
| POST | `/crm/makers/from-project/{project_id}` | 海外案件からメーカー作成・紐づけ |
| POST/PATCH/DELETE | `/crm/makers/{id}/contacts`・`/crm/contacts/{id}` | 担当者の追加／更新／削除 |
| GET/POST | `/crm/makers/{id}/activities` | 営業履歴の参照／追加 |
| GET | `/crm/reminders` | 次回アクション日のリマインダー（期限切れ判定） |

### スケジューラの主な API

| メソッド | パス | 説明 |
| --- | --- | --- |
| GET | `/scrape/last` | スケジューラ状態（有効/cron/次回予定）＋最新ジョブ＋サイト別の最終実行 |
| GET | `/scrape/jobs` | 収集ジョブの実行履歴（新しい順） |
| POST | `/scrape/run-all` | 4 サイトを一括バックグラウンド収集（日次ジョブの手動トリガ） |

### 日本成功事例の主な API

| メソッド | パス | 説明 |
| --- | --- | --- |
| GET | `/japanese-success` | 成功事例一覧（`platform`・カテゴリ・検索・ソート・ページング） |
| POST | `/japanese-success/collect` | 成功事例を収集（`platform` 指定で個別、未指定で Makuake + GreenFunding 一括／同期・現状モック） |
| GET | `/projects/{id}/similar-japanese` | 海外案件に類似する日本の成功事例 |

収集の例：

```bash
curl -X POST "http://localhost:8000/japanese-success/collect?platform=makuake"
curl -X POST "http://localhost:8000/japanese-success/collect?platform=greenfunding"
curl -X POST "http://localhost:8000/japanese-success/collect"   # 両方一括
```

### マイグレーション

スキーマは Alembic で管理します（コンテナ起動時に `alembic upgrade head` が自動実行）。
モデルを変更したら新しいリビジョンを作成してください。

```bash
docker compose exec backend alembic revision --autogenerate -m "説明"
docker compose exec backend alembic upgrade head
```

## 起動方法

```bash
# 1. 環境変数ファイルを作成
cp .env.example .env

# 2. 起動（初回はビルドが走る）
docker compose up --build
```

## アクセス先

| 対象 | URL |
| --- | --- |
| フロントエンド | http://localhost:3000 |
| バックエンド API | http://localhost:8000 |
| API ドキュメント (Swagger) | http://localhost:8000/docs |
| ヘルスチェック | http://localhost:8000/health |

## 画面

| 画面 | URL |
| --- | --- |
| 案件一覧（フィルタ・ソート・ページング） | http://localhost:3000/ |
| 案件詳細（ステータス変更・AI評価・類似日本成功事例・メール下書き） | http://localhost:3000/projects/1 |
| 日本の成功事例一覧 | http://localhost:3000/japanese-success |

## 動作確認

- `http://localhost:8000/health` が `{"status":"ok","database":"ok"}` を返す
- `http://localhost:8000/projects` がモック案件（5件）を返す
- `http://localhost:3000` の一覧でフィルタ・並び替え・ページングが動く
- 案件詳細でステータスボタンを押すと `PATCH /projects/{id}/status` が走り表示が更新される

## 停止

```bash
docker compose down        # コンテナ停止
docker compose down -v     # DB データも削除
```
