#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return digest(encoded)


def check_portal(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        required = {
            "manifest.json", "submission.json", "schema.json",
            "documents-manifest.json", "declarations.json", "checksums.json",
        }
        assert required.issubset(names), required - names
        manifest = json.loads(archive.read("manifest.json"))
        checksums = json.loads(archive.read("checksums.json"))
        assert manifest["format"] == "LANDVALUE360_PORTAL_SUBMISSION"
        for name, expected in checksums.items():
            assert digest(archive.read(name)) == expected, name
        submission = json.loads(archive.read("submission.json"))
        assert submission["schema_version"] == "portal-submission-1.0.0"
        assert submission["declarations"]["advanced_results_included"] is False
    return {"bytes": len(payload), "files": len(names)}


def check_internal(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        required = {"manifest.json", "project.json", "versions.json", "scenarios.json"}
        assert required.issubset(names), required - names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "LANDVALUE360_PROJECT_PACKAGE"
        assert manifest["format_version"] == "2.1.1"
        assert manifest["source_platform_version"] == "financial-portal-2.5.0"
        for name, metadata in manifest["files"].items():
            assert digest(archive.read(name)) == metadata["sha256"], name
        versions = json.loads(archive.read("versions.json"))
        assert len(versions) == 1
        project = json.loads(archive.read("project.json"))
        assert project["project_kind"] == "SHARED"
        version = versions[0]
        snapshot = version["input_snapshot"]
        assert version["input_hash"] == canonical_hash(snapshot)
        assert manifest["compatibility"]["effective_input_hash"] == version["input_hash"]
        assert manifest["compatibility"]["source_input_hash"] == version["source_input_hash"]
        assert manifest["compatibility"]["monthly_financial_inputs_included"] is True
        assert snapshot["portal_submission"]["requires_analyst_completion"] is True
        assert snapshot["portal_submission"]["target_internal_contract"] == "2.1.1"
        assert snapshot["planning_products"]
        assert snapshot["products"]
        assert "gfa_allocation_share" not in snapshot["products"][0]
        finance = snapshot["finance_model"]
        assert finance["enabled"] is True
        assert finance["allow_negative_cash"] is False
        assert finance["spend_policy"] == "CASH_DRIVEN"
        assert finance["funding_draw_order"] == "DEBT_FIRST"
        assert snapshot["funding"]["opening_cash"] == "500000"
        assert snapshot["funding"]["committed_financing"] == "7500000"
        assert snapshot["partnership"]["method"] == "GROSS_SALES"
        assert snapshot["partnership"]["share_rate"] == "0.08"
        source_model = snapshot["portal_financial_model"]
        assert source_model["sales"]["collection_rules"]
        assert source_model["finance"]["allow_negative_cash"] is False
    return {
        "bytes": len(payload),
        "files": len(names),
        "effective_input_hash": version["input_hash"],
        "finance_enabled": finance["enabled"],
        "allow_negative_cash": finance["allow_negative_cash"],
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    report = {
        "status": "PASS",
        "portal": check_portal(root / "release_artifacts/sample-portal-submission.lv360"),
        "internal": check_internal(root / "release_artifacts/sample-internal-import.lv360"),
    }
    output = root / "release_artifacts/package-contract-test.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
