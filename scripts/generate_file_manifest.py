#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "FILE_MANIFEST.csv"
SKIP_PARTS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
    ".sample-runtime", ".release-staging", "build", "data",
}
SKIP_FILES = {"RELEASE_CHECKSUMS.sha256", "FILE_MANIFEST.csv"}
CORE_PREFIXES = tuple(
    f"app/{name}/" for name in (
        "landvalue360_common",
        "landvalue360_kernel",
        "landvalue360_government",
        "landvalue360_valuation",
        "landvalue360_finance",
        "landvalue360_risk",
        "landvalue360_server",
    )
)


def excluded(relative: Path) -> bool:
    if any(part in SKIP_PARTS for part in relative.parts):
        return True
    if relative.parts[:2] == ("tests", ".runtime"):
        return True
    if relative.name in SKIP_FILES:
        return True
    return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category(relative: Path) -> str:
    value = relative.as_posix()
    if value.startswith(CORE_PREFIXES):
        return "financial_engine_core"
    if value in {
        "app/landvalue360_portal/financial_engine.py",
        "app/landvalue360_portal/financial_service.py",
        "app/landvalue360_portal/financial_reports.py",
    }:
        return "financial_engine_adapter"
    if value.startswith("app/landvalue360_portal/templates/") or value.startswith("app/landvalue360_portal/static/"):
        return "portal_ui"
    if value.startswith("app/landvalue360_portal/routers/"):
        return "api_routes"
    if value.startswith("app/landvalue360_portal/"):
        return "portal_backend"
    if value.startswith("migrations/") or value == "alembic.ini":
        return "database_migrations"
    if value.startswith("validation/"):
        return "golden_validation"
    if value.startswith("schemas/"):
        return "data_contracts"
    if value.startswith("tests/"):
        return "tests"
    if value.startswith("docs/") or value.startswith("README") or value.endswith("_AR.md"):
        return "documentation"
    if value.startswith("deploy/") or value.startswith("caddy/") or value in {
        "Dockerfile", "docker-compose.yml", ".env.production.example",
    }:
        return "deployment"
    if value.startswith("release_artifacts/") or value.startswith("dist/"):
        return "release_artifacts"
    if value.startswith("scripts/") or value.endswith(".bat") or value.endswith(".sh"):
        return "operations_tools"
    if value in {"CORE_PROVENANCE.json", "INTEGRATION_PAIRING.json", "RELEASE_MANIFEST.json"}:
        return "release_provenance"
    return "project_configuration"


def main() -> int:
    rows = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if excluded(relative):
            continue
        rows.append({
            "path": relative.as_posix(),
            "category": category(relative),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    with OUT.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "category", "size_bytes", "sha256"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{OUT}: {len(rows)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
