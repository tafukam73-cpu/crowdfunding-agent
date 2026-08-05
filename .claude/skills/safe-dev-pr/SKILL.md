---
name: safe-dev-pr
description: このリポジトリでの安全な変更・commit・PR・main同期・ブランチ整理の手順。「PR出して」「commitして」「マージ後の片付け」「ブランチ整理」「mainに戻して」と言われたときに使う。保護9ファイルを巻き込まない stage、1 PR = 1変更軸、Squash Merge 後のツリー一致確認が要点。
---

# 安全な開発・PR 運用（Safe Dev / PR）

CLAUDE.md §3（Git ルール）の実務手順です。

## 絶対に守る 3 点

1. **main へ直接コミットしない**
2. **`git add -A` / `git add .` / `git commit -a` を使わない**（保護9ファイルを巻き込む）
3. **force push しない**（`--force-with-lease` も明示承認時のみ）

## stage の作法

```bash
git status                     # まず現状確認
git add <path1> <path2>        # ✅ パスを明示
git status                     # 保護9件が ?? のままか再確認
```

保護対象（CLAUDE.md §6 / [ground-truth-audit](../ground-truth-audit/SKILL.md)）:
`backend/tests/contact_intel_eval/` の `WORKLOG_official_site_fp.md` と `_*.py` 8件。

**commit 直前に必ず `git status` で 9 件が `??` のままであることを確認してください。**

## 1 PR = 1 変更軸

以下は**別 PR に分けます**。

| 混ぜてはいけない組み合わせ | 理由 |
|---|---|
| 実装変更 ＋ gold/GT 更新 | 評価が意味をなさなくなる |
| 機能追加 ＋ リファクタ | レビュー不能 |
| migration ＋ ロジック変更 | ロールバック単位が壊れる |
| 複数サービスの独立した修正 | 障害切り分けが困難 |

## 標準フロー

```bash
# 1. main を最新化して feature ブランチを切る
git checkout main && git pull origin main
git checkout -b feat/<簡潔な名前>

# 2. 実装 → テスト（CLAUDE.md §7：pytest ではなくスクリプト実行）
docker compose exec -T backend python tests/test_<対象>.py     # 終了コードで判定
cd frontend && npx tsc --noEmit

# 3. PR 前に差分を確認（意図しない変更が無いか）
git diff main...HEAD --stat
git diff main...HEAD

# 4. stage → commit（パス明示）
git add <明示パス>
git status                     # 保護9件が ?? か確認
git commit -m "<type>: <日本語で変更内容>"

# 5. push → PR
git push -u origin feat/<名前>
gh pr create --base main
```

commit メッセージは既存履歴に合わせて日本語（例: `feat: 営業ステータスの状態管理を一本化`）。

## migration を含む場合

CLAUDE.md §4 に従います。

```bash
./scripts/backup-db.sh pre_migration          # 必ずバックアップ
docker compose exec -T backend alembic current
docker compose exec -T backend alembic heads  # ★ 単一であること
# migration 適用後
docker compose exec -T backend alembic heads  # ★ 再確認
```

**heads が複数になったら PR を出さないでください。** 先に解消します。
破壊的 migration（カラム削除・型変更・NOT NULL 追加）は**別 PR ＋明示承認**。

## Squash Merge 後の後始末

Squash Merge はコミットを潰すため、`git branch -d` が「未マージ」と誤検知します。
**ツリー一致で確認**してください。

```bash
git checkout main && git pull origin main
git diff --stat main <feature-branch>    # 出力が空 = ツリー一致
git branch -d <feature-branch>           # 一致を確認してから削除
git remote prune origin                  # リモート追跡の掃除
```

`git diff --stat` が空でないのに削除しないこと。**未マージの変更が消えます。**

## 障害時の復旧

| 症状 | 対応 |
|---|---|
| backend が無応答 | Chromium 増殖を疑う。`docker compose ps` → 並列 job の二重起動を確認（CLAUDE.md §5） |
| job が終わらない | `contact_intelligence_jobs` の `heartbeat_at` を確認。`job_locks` の残留を確認 |
| DB を壊した | `./scripts/restore-db.sh`（既定は `backups/latest.sql`） |
| migration 失敗 | バックアップから復元 → `alembic current` で状態確認 |
| 誤って保護9件を stage した | `git restore --staged <path>`（**`git reset --hard` は使わない**） |

**`git reset --hard` / `git clean` は禁止されています**（`.claude/settings.json` の deny）。
作業を失う復旧手段を選ばないでください。

## 禁止事項

- `git add -A` / `git add .` / `git commit -a`
- main への直接 commit / push
- `git push --force` / `-f`
- `git reset --hard` / `git clean`
- テスト未実施での PR 作成
- 承認なしの commit・push・PR（ユーザーが明示的に指示するまで待つ）

## 関連

- [ground-truth-audit](../ground-truth-audit/SKILL.md)（gold 更新は別 PR）
- [db-safe-ops](../db-safe-ops/SKILL.md)（DB 操作の作法）
