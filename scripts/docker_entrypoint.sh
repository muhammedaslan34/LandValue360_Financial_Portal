#!/bin/sh
set -eu

python - <<'PY'
import os
from urllib.parse import quote_plus

def build_url(user: str, password: str, database: str) -> str:
    return (
        f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
        f"@db:5432/{quote_plus(database)}"
    )

if not os.environ.get("LV360_PORTAL_DATABASE_URL"):
    os.environ["LV360_PORTAL_DATABASE_URL"] = build_url(
        os.environ["APP_DB_USER"],
        os.environ["APP_DB_PASSWORD"],
        os.environ["POSTGRES_DB"],
    )
if not os.environ.get("LV360_PORTAL_MIGRATION_DATABASE_URL"):
    os.environ["LV360_PORTAL_MIGRATION_DATABASE_URL"] = build_url(
        os.environ["POSTGRES_USER"],
        os.environ["POSTGRES_PASSWORD"],
        os.environ["POSTGRES_DB"],
    )

for key in ("LV360_PORTAL_DATABASE_URL", "LV360_PORTAL_MIGRATION_DATABASE_URL"):
    print(f"export {key}={os.environ[key]!r}")
PY
> /tmp/lv360-db-urls.sh
. /tmp/lv360-db-urls.sh

python - <<'PY' || echo "app role sync skipped" >&2
import os
import psycopg
from psycopg import sql

user = os.environ["APP_DB_USER"]
password = os.environ["APP_DB_PASSWORD"]
with psycopg.connect(
    host="db",
    port=5432,
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
) as conn:
    conn.autocommit = True
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s",
        (user,),
    ).fetchone()
    if exists:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(user),
                sql.Literal(password),
            )
        )
    else:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(user),
                sql.Literal(password),
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(os.environ["POSTGRES_DB"]),
                sql.Identifier(user),
            )
        )
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(user)))
PY

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

python - <<'PY'
import os
import psycopg
from psycopg import sql

user = os.environ["APP_DB_USER"]
with psycopg.connect(
    host="db",
    port=5432,
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
) as conn:
    conn.autocommit = True
    ident = sql.Identifier(user)
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(os.environ["POSTGRES_DB"]), ident))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
    conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(ident))
    conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(ident))
    owner = sql.Identifier(os.environ["POSTGRES_USER"])
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(owner, ident)
    )
    conn.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(owner, ident)
    )
PY

exec uvicorn landvalue360_portal.main:app --host 0.0.0.0 --port 8090 --proxy-headers --forwarded-allow-ips='*'
