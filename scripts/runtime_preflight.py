#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REQUIRED = [
    "fastapi", "uvicorn", "sqlalchemy", "alembic", "pydantic", "pydantic_settings",
    "jinja2", "multipart", "argon2", "email_validator", "openpyxl", "PIL",
    "defusedxml", "boto3", "httpx",
]


def main() -> int:
    errors: list[str] = []
    if sys.version_info < (3, 12):
        errors.append(f"Python 3.12+ is required; found {sys.version.split()[0]}")
    imported: list[str] = []
    for name in REQUIRED:
        try:
            __import__(name)
            imported.append(name)
        except Exception as exc:
            errors.append(f"Cannot import {name}: {exc}")
    version = "unknown"
    try:
        from landvalue360_portal import __version__
        from landvalue360_portal.config import get_settings
        from landvalue360_portal.financial_engine import engine_registration_manifest
        from landvalue360_portal.storage import LocalStorage
        settings = get_settings()
        with tempfile.TemporaryDirectory(prefix="lv360-preflight-") as tmp:
            probe = Path(tmp) / "write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            LocalStorage(Path(tmp) / "private")
        manifest = engine_registration_manifest()
        if len(str(manifest.get("source_hash") or "")) != 64:
            errors.append("Financial engine source hash is invalid")
        version = __version__
        environment = settings.env
    except Exception as exc:
        errors.append(f"Application preflight failed: {exc}")
        environment = "unknown"
        manifest = {}
    report = {
        "status": "PASS" if not errors else "FAIL",
        "python": sys.version.split()[0],
        "application_version": version,
        "environment": environment,
        "engine_version": manifest.get("engine_version"),
        "engine_source_hash": manifest.get("source_hash"),
        "imports": imported,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
