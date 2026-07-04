#!/usr/bin/env bash
#
# backups/ の pg_dump ファイルから crowdfunding DB を復元する。
#
# 使い方:
#   ./scripts/restore-db.sh                         # backups/latest.sql から復元
#   ./scripts/restore-db.sh backups/xxx.sql         # ファイル指定で復元
#   ./scripts/restore-db.sh backups/xxx.sql --yes   # 確認プロンプトを省略
#
# 安全策:
#   - 復元前に「現在のDB」を必ず backups/pre_restore_*.sql として自動バックアップする。
#   - 破壊的操作なので、--yes を付けない限り確認プロンプトを出す。
#
# 前提: docker compose の db サービス（cfagent-db）が起動していること。
# 復元ファイルは backup-db.sh が作る plain SQL（--clean --if-exists 付き）を想定。

set -euo pipefail

cd "$(dirname "$0")/.."

SERVICE="db"
OUT_DIR="backups"
FILE="${1:-${OUT_DIR}/latest.sql}"
CONFIRM="${2:-}"

if [ ! -f "$FILE" ]; then
  echo "❌ 復元元ファイルが見つかりません: $FILE" >&2
  echo "   利用可能なバックアップ:" >&2
  ls -1 "${OUT_DIR}"/*.sql 2>/dev/null >&2 || echo "   （backups/ にSQLがありません。先に ./scripts/backup-db.sh を実行）" >&2
  exit 1
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "❌ db サービスが起動していません。先に 'docker compose up -d db' を実行してください。" >&2
  exit 1
fi

echo "⚠️  現在の crowdfunding DB を上書きします。"
echo "    復元元: $FILE"

if [ "$CONFIRM" != "--yes" ] && [ "${RESTORE_YES:-}" != "1" ]; then
  printf "    続行しますか？ [y/N]: "
  read -r ans
  case "$ans" in
    y|Y|yes|YES) : ;;
    *) echo "中止しました。"; exit 0 ;;
  esac
fi

# 復元前に現在DBを安全バックアップ（必ず）
mkdir -p "$OUT_DIR"
SAFETY="${OUT_DIR}/pre_restore_$(date +%Y%m%d_%H%M%S).sql"
echo "🛟 復元前の現在DBを保存中: $SAFETY"
docker compose exec -T "$SERVICE" sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' > "$SAFETY"

# 復元（psql に流し込む。--clean --if-exists により既存を DROP → 再作成）
echo "♻️  復元を実行中…"
docker compose exec -T "$SERVICE" sh -lc \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$FILE"

echo "✅ 復元完了: $FILE"
echo "   （復元前のスナップショット: $SAFETY）"
echo "   件数確認例:"
echo "     docker compose exec -T db sh -lc 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -c \"select count(*) from makers;\"'"
