# CLAUDE.md — crowdfunding-agent

このファイルは Claude Code がこのリポジトリで作業する際の運用規約です。
ここに書かれた指示は既定の挙動より優先されます。

---

## 1. プロジェクト目的

海外クラウドファンディング商品を日本で展開するための、**実務営業支援システム**です。

**最優先事項は機能追加ではありません。** 以下を減らすことが価値です。

- 無駄な調査（成果につながらない探索の実行）
- 無駄なメール送信（送るべきでない相手への送信）
- 誤った連絡先（間違ったメールアドレス・誤った担当者）

### 表示してよいもの・いけないもの

**表示しない**（根拠がないため）:
- 返信率の予測値
- 成功確率
- 「可能性スコア」の類

**表示する**（確認可能な事実）:
- 事実そのもの
- 出典 URL
- 確認日時

数値を出す場合は、それが「実測値」なのか「推定値」なのかを必ず区別してください。
推定値をユーザー向け画面に出す変更は、明示承認なしに行わないでください。

---

## 2. 必須作業順序

以下の順序を飛ばさないでください。特に **承認の前に実装しない**こと。

1. 現状調査
2. 影響範囲確認
3. 設計案の提示
4. **ユーザー承認**
5. 実装
6. テスト
7. commit
8. push
9. PR 作成
10. Squash Merge 後の main 同期
11. ブランチ整理
12. DB / API / フロントエンドの動作確認

---

## 3. Git ルール

- **main への直接コミット禁止。** 作業は必ず feature ブランチで行う
- **force push 禁止**
- `--force-with-lease` も**明示承認時のみ**
- **1 PR = 1 変更軸。** 複数の目的を 1 つの PR に混ぜない
- PR 作成前に `git diff main...HEAD` を確認し、意図しない差分がないことを確かめる
- Squash Merge 後は**ツリー一致を確認してから**ブランチ削除する

```bash
# Squash Merge 後の同期・ブランチ整理
git checkout main && git pull origin main
git diff --stat main <feature-branch>   # 差分が空 = ツリー一致
git branch -d <feature-branch>          # 一致を確認してから削除
```

---

## 4. DB ルール

### 接続方法

**ホスト側に `psql` は入っていません。** 必ずコンテナ経由で実行してください。

```bash
docker compose exec -T db psql -U cfagent -d crowdfunding -c "<SQL>"
docker compose exec -T backend alembic current
docker compose exec -T backend alembic heads
```

コンテナ名: `cfagent-db` / `cfagent-backend` / `cfagent-ci-worker` / `cfagent-frontend`

### 更新時の規約

- **DB 更新前に必ず件数確認**（`SELECT count(*)` で対象行数を先に把握する）
- `UPDATE` / `DELETE` は**必ずトランザクション内**で実行する

```sql
BEGIN;
SELECT count(*) FROM <table> WHERE <条件>;  -- 対象件数を確認
UPDATE <table> SET ... WHERE <条件>;
-- 件数が想定どおりなら COMMIT、違えば ROLLBACK
```

- **migration 適用前に必ずバックアップ**を取る。専用スクリプトがあるのでこれを使う

```bash
./scripts/backup-db.sh pre_migration    # backups/crowdfunding_<TS>_pre_migration.sql + latest.sql
./scripts/restore-db.sh                 # 復元（既定は backups/latest.sql）
```

- migration 前後で `alembic current` / `alembic heads` を確認し、**heads が単一**であることを保証する
- **本番 DB への操作は明示承認必須**
- **破壊的 migration は禁止。** 必要な場合は別 PR に切り出し、明示承認を得る
  （カラム削除・型変更・NOT NULL 追加・テーブル削除などが該当）

---

## 5. Contact Intelligence ルール

### 実行の作法

- **重い処理を同期 POST で起動しない。** 必ず job 経由で非同期実行する
  （過去に同期 POST が 12 秒タイムアウト回帰の原因になっている）
- **full job と子 job の並列二重起動禁止。** 同一 project に対して同時に起動すると
  Chromium が増殖し backend が無応答になる

### 概念を混同しない

以下は**すべて別物**です。取り違えると誤った連絡先を生みます。

| 概念 | 意味 |
|---|---|
| 商品ページ | クラファンのキャンペーンページ（`campaign_url`） |
| maker identity | 出品者そのものの同定 |
| 公式サイト | maker の自社サイト（`official_site_url`） |
| メール所有者 | そのメールアドレスを実際に保有する主体 |

- **`official_site_url` で `campaign_url` を代用しない**（逆も同様）
- **推測で Ground Truth を確定しない。** 根拠が取れない場合は「不明」のままにする

---

## 6. 保護対象ファイル

`backend/tests/contact_intel_eval/` の以下の**未追跡 9 件**は、
**明示的に対象指定されない限り**、変更・削除・stage・commit・rename しないでください。

- `WORKLOG_official_site_fp.md`
- `_final_compare.py`
- `_measure_eval30.py`
- `_probe_eval.py`
- `_reanalyze_prefix.py`
- `_report_live.py`
- `_report_phase2.py`
- `_report_v2.py`
- `_select_eval30.py`

### stage の作法

**`git add -A` および `git add .` は使用禁止です。** これらは上記 9 件を巻き込みます。

```bash
git add <path1> <path2>     # ✅ 常にパスを明示する
git add -A                  # ❌ 禁止
git add .                   # ❌ 禁止
git commit -a               # ❌ 禁止（-a も同様に巻き込む）
```

commit 前に `git status` で、9 件が `??`（未追跡）のままであることを必ず確認してください。

---

## 7. テスト

### このリポジトリのテストは pytest を使いません

`backend/tests/` の **64 ファイル / 448 テスト関数**は、**意図的に「pytest 非依存」**で
書かれています（多くの docstring に明記あり）。pytest はインストールされていません。

**実行方法はスクリプト直接実行です。終了コードが失敗件数を表します。**

```bash
# 変更箇所の専用テスト
docker compose exec -T backend python tests/test_<対象>.py

# 全件回帰（失敗したファイルだけを列挙）
docker compose exec -T backend sh -lc \
  'for f in tests/test_*.py; do python "$f" >/dev/null 2>&1 || echo "FAIL: $f"; done'
```

### pytest を安易に導入してはいけない理由

64 ファイル中 **63 ファイルが自前の `check(name, cond)` ヘルパ**を定義しており、
このヘルパは**失敗しても例外を投げません**。カウンタを増やして `FAIL-` と表示するだけで、
最終的な合否は `main()` の戻り値＝プロセス終了コードでのみ表現されます。
`assert` を使っているファイルは **0 件**です。

つまり `pip install pytest && pytest` とすると、**448 関数すべてが常に PASS と報告されます**
（pytest は例外が出ないテストを成功とみなすため）。**偽の緑**になり、実際の失敗を隠します。

pytest を導入する場合は、`check()` を失敗時に `AssertionError` を送出する形へ
改修する作業とセットで行ってください。これは**別タスク＋明示承認**とします。
勝手に `pip install` / Dockerfile 変更をしないこと。

### その他の検証

```bash
cd frontend && npx tsc --noEmit     # 型チェック
cd frontend && npm run build        # ビルド

docker compose exec -T backend alembic current   # migration 状態
docker compose exec -T backend alembic heads     # heads が単一であること
```

API 疎通確認: backend `http://localhost:8000` / frontend `http://localhost:3000`

- migration がある場合は `alembic heads` が**単一**であることを確認する
- **API 疎通確認**を行う（backend: `http://localhost:8000` / frontend: `http://localhost:3000`）

---

## 8. 環境メモ

| 項目 | 値 |
|---|---|
| backend | `http://localhost:8000`（コンテナ `cfagent-backend`） |
| frontend | `http://localhost:3000`（コンテナ `cfagent-frontend`） |
| DB | PostgreSQL / `cfagent-db` / user `cfagent` / db `crowdfunding` |
| 検索 | `SEARCH_PROVIDER=brave`（Brave Search API） |
| メール | Gmail API（OAuth refresh token / `app/email/providers/gmail.py`） |
| スクレイピング | Playwright（Chromium） |

シークレットは `.env` にあります。**`.env` の内容を出力・表示しないでください。**
