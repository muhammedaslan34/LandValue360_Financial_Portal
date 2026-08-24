#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3}
MARKER=.venv/lv360-financial-portal-2.5.0.installed
[ -f .env ] || "$PYTHON" scripts/ensure_local_env.py
if [ ! -x .venv/bin/python ] || [ ! -f "$MARKER" ]; then
  [ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
  .venv/bin/python -m pip install --disable-pip-version-check --upgrade pip
  if [ -d wheelhouse ] && [ "$(find wheelhouse -type f -name '*.whl' | wc -l)" -gt 0 ]; then
    .venv/bin/python -m pip install --no-index --find-links wheelhouse -r requirements-runtime-lock.txt
  else
    .venv/bin/python -m pip install -r requirements-runtime-lock.txt
  fi
  APP_WHEEL=$(find dist -maxdepth 1 -type f -name 'landvalue360_financial_portal-*.whl' | sort | tail -n 1)
  [ -n "$APP_WHEEL" ] || { echo "Application wheel is missing from dist." >&2; exit 1; }
  .venv/bin/python -m pip install --no-deps --force-reinstall "$APP_WHEEL"
  rm -f .venv/lv360-*-portal-*.installed
  printf '%s\n' '2.5.0' > "$MARKER"
fi
.venv/bin/python scripts/runtime_preflight.py
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/first_run_bootstrap.py --non-interactive
exec .venv/bin/python -m uvicorn landvalue360_portal.main:app --host 127.0.0.1 --port 8090
