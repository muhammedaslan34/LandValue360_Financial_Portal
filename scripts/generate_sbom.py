#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
components = []
for line in (ROOT / "requirements-runtime-lock.txt").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    name, dependency_version = line.split("==", 1)
    components.append({
        "type": "library",
        "name": name,
        "version": dependency_version,
        "purl": f"pkg:pypi/{name.lower()}@{dependency_version}",
    })
for name in (
    "landvalue360-common",
    "landvalue360-kernel",
    "landvalue360-government",
    "landvalue360-valuation",
    "landvalue360-finance",
    "landvalue360-risk",
    "landvalue360-server-core",
):
    components.append({
        "type": "library",
        "name": name,
        "version": "2.1.1",
        "properties": [
            {"name": "landvalue360:distribution", "value": "vendored-source"},
            {"name": "landvalue360:source", "value": "LandValue360 Platform 2.1.1"},
        ],
    })
sbom = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "version": 1,
    "metadata": {
        "component": {
            "type": "application",
            "name": "landvalue360-financial-portal",
            "version": version,
        }
    },
    "components": components,
}
path = ROOT / "release_artifacts/sbom.cyclonedx.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
print(path)
