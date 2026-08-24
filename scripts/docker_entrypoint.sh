#!/bin/sh
set -eu

if [ -z "${LV360_PORTAL_DATABASE_URL:-}" ]; then
  export LV360_PORTAL_DATABASE_URL="postgresql+psycopg://${APP_DB_USER}:${APP_DB_PASSWORD}@db:5432/${POSTGRES_DB}"
fi
if [ -z "${LV360_PORTAL_MIGRATION_DATABASE_URL:-}" ]; then
  export LV360_PORTAL_MIGRATION_DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}"
fi

attempt=0
until python -m alembic upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "alembic upgrade failed after ${attempt} attempts" >&2
    exit 1
  fi
  echo "waiting for database before migrations (attempt ${attempt})..." >&2
  sleep 3
done
exec uvicorn landvalue360_portal.main:app --host 0.0.0.0 --port 8090 --proxy-headers --forwarded-allow-ips='*'
