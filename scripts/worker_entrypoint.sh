#!/bin/sh
set -eu

python - <<'PY'
import os
from urllib.parse import quote_plus

if not os.environ.get("LV360_PORTAL_DATABASE_URL"):
    user = os.environ["APP_DB_USER"]
    password = os.environ["APP_DB_PASSWORD"]
    database = os.environ["POSTGRES_DB"]
    os.environ["LV360_PORTAL_DATABASE_URL"] = (
        f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
        f"@db:5432/{quote_plus(database)}"
    )

print(f"export LV360_PORTAL_DATABASE_URL={os.environ['LV360_PORTAL_DATABASE_URL']!r}")
PY
> /tmp/lv360-db-urls.sh
. /tmp/lv360-db-urls.sh

echo "notification worker started; polling outbox every 30s"

while true; do
  python scripts/send_notifications.py
  sleep 30
done
