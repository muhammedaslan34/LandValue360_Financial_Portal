from __future__ import annotations

import json
from pathlib import Path
import yaml

root = Path(__file__).resolve().parents[1]
compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
services = compose.get("services") or {}
required = {"db", "minio", "minio-init", "app", "notification-worker", "caddy"}
assert required.issubset(services), required - set(services)
assert services["app"]["environment"]["LV360_PORTAL_ENV"] == "production"
assert "postgresql+psycopg" in services["app"]["environment"]["LV360_PORTAL_DATABASE_URL"]
assert services["app"]["environment"]["LV360_PORTAL_STORAGE_BACKEND"] == "s3"
dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
assert "USER lv360" in dockerfile
assert "HEALTHCHECK" in dockerfile
assert "scripts/healthcheck.py" in dockerfile
assert services["app"]["environment"]["LV360_PORTAL_HEALTH_HOST"] == "${DOMAIN}"
assert isinstance(services["minio-init"]["command"], list)
assert "docker_entrypoint.sh" in dockerfile
caddy = (root / "caddy/Caddyfile").read_text(encoding="utf-8")
assert "reverse_proxy app:8090" in caddy
assert "Strict-Transport-Security" in caddy
report = {"status": "PASS", "services": sorted(services), "non_root_container": True, "https_proxy": "caddy"}
out = root / "release_artifacts/deployment-validation.json"
out.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
