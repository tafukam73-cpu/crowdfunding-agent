# 海外クラファン案件発掘・営業支援システム

海外クラウドファンディング（Kickstarter / Indiegogo / Wadiz）案件を収集し、
日本向けクラファン商品の発掘と営業支援を行うローカル Web アプリ。

## 構成

- **backend** … FastAPI（Python 3.12）
- **frontend** … Next.js 14 + TypeScript + Tailwind CSS
- **db** … PostgreSQL 16
- すべて Docker Compose で起動

## Step 2（現状）

案件データモデルと CRUD、案件一覧／詳細画面まで。スクレイピング・AI 評価・メール生成は未実装。

- 要件定義の取得項目に対応した `projects` テーブル
- Alembic マイグレーション管理
- 案件 CRUD API（一覧フィルタ・ソート・ページング／詳細／作成・更新／ステータス更新）
- Next.js 案件一覧・詳細画面
- 起動時にモックデータを自動投入（DB が空のときのみ）

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
| 案件詳細（ステータス変更） | http://localhost:3000/projects/1 |

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
