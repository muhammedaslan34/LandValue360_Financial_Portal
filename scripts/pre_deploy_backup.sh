#!/usr/bin/env sh
set -eu

APP_ROOT=${APP_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
OUTPUT_ROOT=${1:-"$APP_ROOT/../lv360-predeploy-backups"}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$OUTPUT_ROOT/$STAMP"
mkdir -p "$TARGET"

# Preserve the exact deployed source before replacing any file. Runtime data,
# caches and prior backups are excluded because they are captured separately.
tar \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='tests/.runtime' \
  --exclude='data/portal.db*' \
  --exclude='backups' \
  --exclude='lv360-predeploy-backups' \
  -C "$APP_ROOT" -czf "$TARGET/application-source.tar.gz" .

DB_STATUS=not-captured
DB_URL=${LV360_PORTAL_MIGRATION_DATABASE_URL:-${LV360_PORTAL_DATABASE_URL:-}}
if [ -n "$DB_URL" ] && echo "$DB_URL" | grep -q '^postgresql'; then
  if command -v pg_dump >/dev/null 2>&1; then
    pg_dump "$DB_URL" -Fc -f "$TARGET/portal.dump"
    DB_STATUS=postgresql-pg-dump
  fi
fi
if [ "$DB_STATUS" = not-captured ] && command -v docker >/dev/null 2>&1 && [ -f "$APP_ROOT/docker-compose.yml" ]; then
  if docker compose -f "$APP_ROOT/docker-compose.yml" ps -q db 2>/dev/null | grep -q .; then
    POSTGRES_USER=${POSTGRES_USER:-lv360}
    POSTGRES_DB=${POSTGRES_DB:-lv360_portal}
    docker compose -f "$APP_ROOT/docker-compose.yml" exec -T db \
      pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$TARGET/portal.dump"
    DB_STATUS=postgresql-docker-pg-dump
  fi
fi
if [ "$DB_STATUS" = not-captured ]; then
  SQLITE_PATH=${LV360_PORTAL_SQLITE_PATH:-$APP_ROOT/data/portal.db}
  if [ -f "$SQLITE_PATH" ]; then
    cp "$SQLITE_PATH" "$TARGET/portal.db"
    DB_STATUS=sqlite-file-copy
  fi
fi

OBJECT_STATUS=not-captured
LOCAL_STORAGE=${LV360_PORTAL_LOCAL_STORAGE_PATH:-$APP_ROOT/data/private}
if [ -d "$LOCAL_STORAGE" ]; then
  tar -C "$LOCAL_STORAGE" -czf "$TARGET/object-storage.tar.gz" .
  OBJECT_STATUS=local-storage-archive
elif command -v docker >/dev/null 2>&1 && [ -f "$APP_ROOT/docker-compose.yml" ]; then
  if docker compose -f "$APP_ROOT/docker-compose.yml" ps -q minio 2>/dev/null | grep -q .; then
    docker compose -f "$APP_ROOT/docker-compose.yml" exec -T minio \
      sh -c 'tar -C /data -czf - .' > "$TARGET/object-storage.tar.gz"
    OBJECT_STATUS=minio-container-archive
  fi
fi

cat > "$TARGET/manifest.json" <<EOF_JSON
{
  "created_at_utc": "$STAMP",
  "application_root": "$APP_ROOT",
  "database_backup": "$DB_STATUS",
  "object_storage_backup": "$OBJECT_STATUS",
  "purpose": "Pre-deployment immutable backup of live application source, database and private objects"
}
EOF_JSON

find "$TARGET" -maxdepth 1 -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > "$TARGET/SHA256SUMS"
printf '%s\n' "$TARGET"
