#!/usr/bin/env bash
#
# crowdfunding DB を pg_dump で backups/ に保存する。
#
# 使い方:
#   ./scripts/backup-db.sh            # backups/crowdfunding_YYYYmmdd_HHMMSS.sql を作成
#   ./scripts/backup-db.sh mylabel    # ラベル付き（crowdfunding_YYYYmmdd_HHMMSS_mylabel.sql）
#
# 前提: docker compose の db サービス（cfagent-db）が起動していること。
# 出力は plain SQL（--clean --if-exists 付き）で、restore-db.sh でそのまま復元できる。

set -euo pipefail

# リポジトリルートへ移動（どこから実行しても動くように）
cd "$(dirname "$0")/.."

SERVICE="db"
OUT_DIR="backups"
LABEL="${1:-}"
TS="$(date +%Y%m%d_%H%M%S)"
if [ -n "$LABEL" ]; then
  OUT="${OUT_DIR}/crowdfunding_${TS}_${LABEL}.sql"
else
  OUT="${OUT_DIR}/crowdfunding_${TS}.sql"
fi

mkdir -p "$OUT_DIR"

# db サービスが動いているか確認
if ! docker compose ps --status running --services 2>/dev/null | grep -qx "$SERVICE"; then
  echo "❌ db サービスが起動していません。先に 'docker compose up -d db' を実行してください。" >&2
  exit 1
fi

echo "🗄️  crowdfunding DB をバックアップしています…"
# コンテナ内の POSTGRES_USER / POSTGRES_DB を使う（環境非依存）
docker compose exec -T "$SERVICE" sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' > "$OUT"

BYTES="$(wc -c < "$OUT" | tr -d ' ')"
if [ "$BYTES" -lt 100 ]; then
  echo "⚠️  出力が極端に小さいです（${BYTES} bytes）。pg_dump 失敗の可能性。中身を確認してください: $OUT" >&2
  exit 1
fi

# 直近のバックアップを latest.sql としてもコピー（restore のデフォルト元）
cp "$OUT" "${OUT_DIR}/latest.sql"

echo "✅ バックアップ完了: $OUT (${BYTES} bytes)"
echo "   最新版:        ${OUT_DIR}/latest.sql"
