---
name: db-safe-ops
description: このプロジェクトの PostgreSQL を安全に読み書きする手順。「DBを見て」「件数を確認」「データを直して」「SQLを流して」「バックアップ」「migration」と言われたときに使う。ホストに psql が無いためコンテナ経由で実行すること、更新は必ず件数確認→トランザクションで行うことが要点。
---

# DB 安全操作（DB Safe Ops）

CLAUDE.md §4 の実務手順です。

## 接続（ホストに psql は無い）

```bash
docker compose exec -T db psql -U cfagent -d crowdfunding -c "<SQL>"
docker compose exec -T backend alembic current
docker compose exec -T backend alembic heads
```

| 項目 | 値 |
|---|---|
| コンテナ | `cfagent-db` / `cfagent-backend` / `cfagent-ci-worker` / `cfagent-frontend` |
| user / db | `cfagent` / `crowdfunding` |
| PostgreSQL | 16.14 |
| テーブル数 | 30 |

`.claude/settings.json` で psql は **ask**（都度確認）になっています。これは意図的な設計です。

## 読み取りの作法

```sql
-- 主要テーブルの件数把握（2026-08-05 実測: projects=309, sales_outreach=10,
--                          contact_people=8, japan_opportunity_analyses=8）
SELECT 'projects', count(*) FROM projects
UNION ALL SELECT 'sales_outreach', count(*) FROM sales_outreach
UNION ALL SELECT 'contact_people', count(*) FROM contact_people;
```

大きな結果を無条件に SELECT しないこと。`LIMIT` を付けてください。

## 更新の作法（必ずこの順序）

```sql
BEGIN;

-- ① 対象件数を先に確認する（これを飛ばさない）
SELECT count(*) FROM projects WHERE <条件>;

-- ② 想定と一致したら実行
UPDATE projects SET <列> = <値> WHERE <条件>;

-- ③ 影響行数を確認 → 想定どおりなら COMMIT、違えば ROLLBACK
COMMIT;   -- または ROLLBACK;
```

**件数確認を飛ばした UPDATE / DELETE は禁止**です。
`WHERE` を書き忘れた全件更新は、この手順を守っていれば防げます。

## バックアップと復元

```bash
./scripts/backup-db.sh                # backups/crowdfunding_<TS>.sql + latest.sql
./scripts/backup-db.sh pre_migration  # ラベル付き
./scripts/restore-db.sh               # 既定は backups/latest.sql
```

`backup-db.sh` は `--clean --if-exists --no-owner` で出力し、サイズ検証も行います。
**自前で pg_dump コマンドを組まず、このスクリプトを使ってください。**

## migration

```bash
./scripts/backup-db.sh pre_migration          # ★ 必ず先に
docker compose exec -T backend alembic current
docker compose exec -T backend alembic heads  # ★ 単一であること（現在: 0049_project_status_events）
docker compose exec -T backend alembic upgrade head
docker compose exec -T backend alembic heads  # ★ 再確認
```

- **heads が複数なら先に解消**してから進む
- 破壊的 migration（カラム削除・型変更・NOT NULL 追加・テーブル削除）は
  **別 PR ＋明示承認**（CLAUDE.md §4）
- alembic の `upgrade` / `downgrade` は `.claude/settings.json` で **ask**

## テスト実行時の DB 安全ガード

`backend/tests/conftest.py` が pytest 実行時に本番 DB 接続を `os._exit(99)` で阻止します。
`app/db_safety.py` の `evaluate()` が判定本体です。

ただし**テストはスクリプト直接実行が正**（CLAUDE.md §7）で、その場合 conftest は読まれません。
各テストファイルが自分で `DATABASE_URL` に一時 sqlite を設定しています。
**テストを書くときはこの慣習に従ってください**（本番 DB を指さないこと）。

## 本番 DB

**本番 DB への操作は明示承認必須**です（CLAUDE.md §4）。
`.claude/settings.json` で `docker-compose.prod.yml` 系は deny になっています。

## セキュリティ上の注意

DB は現在 `0.0.0.0:5432` で公開されています（ホストの全 NIC で待受）。
ローカル開発のみなら `127.0.0.1:5432:5432` に絞るのが安全です（**変更は要承認**）。

## 禁止事項

- 件数確認なしの `UPDATE` / `DELETE`
- トランザクション外での `UPDATE` / `DELETE`
- バックアップなしの migration 適用
- `DROP` / `TRUNCATE`（`.claude/settings.json` の deny 対象。実行しない）
- ホスト側 `psql` を探しに行く（存在しない）
- 集計・分析目的での本番データ更新

## 関連

- [safe-dev-pr](../safe-dev-pr/SKILL.md)（migration を含む PR の作法）
- [reply-rate-analytics](../reply-rate-analytics/SKILL.md)（読み取り専用の集計）
