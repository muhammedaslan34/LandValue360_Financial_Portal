#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release_artifacts"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lv360-http-") as tmp:
        tmp_path = Path(tmp)
        database = tmp_path / "http.db"
        port = free_port()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "app")
        env["LV360_PORTAL_DATABASE_URL"] = f"sqlite+pysqlite:///{database}"
        env["LV360_PORTAL_MIGRATION_DATABASE_URL"] = f"sqlite+pysqlite:///{database}"
        env["LV360_PORTAL_LOCAL_STORAGE_PATH"] = str(tmp_path / "private")
        env["LV360_PORTAL_SECRET_KEY"] = "live-http-smoke-secret-key-with-sufficient-length"
        env["LV360_PORTAL_TRUSTED_HOSTS"] = "127.0.0.1,localhost"
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if migration.returncode != 0:
            raise RuntimeError(migration.stderr)
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "landvalue360_portal.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        responses: dict[str, int] = {}
        error = None
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    with httpx.Client(timeout=2.0) as client:
                        response = client.get(base + "/api/health/live")
                    if response.status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            with httpx.Client(timeout=5.0, follow_redirects=False) as client:
                for route in ("/", "/register", "/api/health/live", "/api/health/ready", "/openapi.json"):
                    responses[route] = client.get(base + route).status_code
        except Exception as exc:
            error = str(exc)
        finally:
            server.terminate()
            try:
                stdout, stderr = server.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                stdout, stderr = server.communicate()
        expected = {"/": 200, "/register": 200, "/api/health/live": 200, "/api/health/ready": 200, "/openapi.json": 200}
        status = "PASS" if not error and responses == expected else "FAIL"
        report = {
            "status": status,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "server": "uvicorn",
            "host": "127.0.0.1",
            "port": port,
            "routes": responses,
            "database": "fresh SQLite migrated to 0007_admin_governance_and_security",
            "server_returncode": server.returncode,
            "stdout_tail": stdout[-1000:],
            "stderr_tail": stderr[-1000:],
            "error": error,
        }
    output = ARTIFACTS / "live-http-smoke.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
