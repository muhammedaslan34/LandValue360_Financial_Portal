#!/bin/sh
set -eu
python -m alembic upgrade head
exec uvicorn landvalue360_portal.main:app --host 0.0.0.0 --port 8090 --proxy-headers --forwarded-allow-ips='*'
