#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release_artifacts"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    wheels = sorted((ROOT / "dist").glob("landvalue360_financial_portal-*.whl"))
    sdists = sorted((ROOT / "dist").glob("landvalue360_financial_portal-*.tar.gz"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected one financial portal wheel, found {len(wheels)}")
    wheel = wheels[0]
    sdist = sdists[0] if len(sdists) == 1 else None
    with tempfile.TemporaryDirectory(prefix="lv360-wheel-") as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "site"
        installed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(target), "--no-deps", "--no-index", str(wheel)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        if installed.returncode != 0:
            raise RuntimeError(installed.stderr)
        probe = tmp_path / "probe.py"
        probe.write_text(
            """
import json
import os
from pathlib import Path
os.environ['LV360_PORTAL_DATABASE_URL'] = 'sqlite+pysqlite:///' + str(Path.cwd() / 'wheel.db')
os.environ['LV360_PORTAL_LOCAL_STORAGE_PATH'] = str(Path.cwd() / 'private')
os.environ['LV360_PORTAL_SECRET_KEY'] = 'installed-wheel-smoke-secret-key-with-sufficient-length'
os.environ['LV360_PORTAL_TRUSTED_HOSTS'] = 'testserver,localhost,127.0.0.1'
from fastapi.testclient import TestClient
from landvalue360_portal import __version__
from landvalue360_portal.database import Base, engine
from landvalue360_portal.financial_engine import engine_registration_manifest
from landvalue360_portal.main import create_app
Base.metadata.create_all(engine)
app = create_app()
with TestClient(app) as client:
    live = client.get('/api/health/live')
    ready = client.get('/api/health/ready')
print(json.dumps({
    'application_version': __version__,
    'route_count': len(app.routes),
    'health_live': live.status_code,
    'health_ready': ready.status_code,
    'engine': engine_registration_manifest(),
}))
""".strip()
            + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(target)
        probe_run = subprocess.run([sys.executable, str(probe)], cwd=tmp_path, env=env, capture_output=True, text=True)
        if probe_run.returncode != 0:
            raise RuntimeError(probe_run.stderr)
        probe_payload = json.loads(probe_run.stdout.strip().splitlines()[-1])

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        bytecode_entries = [name for name in names if name.endswith((".pyc", ".pyo"))]
    report = {
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wheel": wheel.relative_to(ROOT).as_posix(),
        "wheel_sha256": sha256(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "source_distribution": sdist.relative_to(ROOT).as_posix() if sdist else None,
        "source_distribution_sha256": sha256(sdist) if sdist else None,
        "source_distribution_bytes": sdist.stat().st_size if sdist else None,
        "installation": "isolated target directory with no source PYTHONPATH",
        "application_version": probe_payload["application_version"],
        "engine_version": probe_payload["engine"].get("engine_version"),
        "engine_source_hash": probe_payload["engine"].get("source_hash"),
        "route_count": probe_payload["route_count"],
        "health_live": probe_payload["health_live"],
        "health_ready": probe_payload["health_ready"],
        "migration_head": "0007_admin_governance_and_security",
        "bytecode_entries_in_wheel": len(bytecode_entries),
    }
    if report["health_live"] != 200 or report["health_ready"] != 200 or report["bytecode_entries_in_wheel"] != 0:
        report["status"] = "FAIL"
    output = ARTIFACTS / "installed-wheel-smoke.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
