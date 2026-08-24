#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

CORE_PACKAGES = (
    "landvalue360_common",
    "landvalue360_kernel",
    "landvalue360_government",
    "landvalue360_valuation",
    "landvalue360_finance",
    "landvalue360_risk",
    "landvalue360_server",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def optional_archive_check(label: str, expected: str, value: str | None) -> dict[str, Any]:
    if not value:
        return {"label": label, "checked": False, "expected_sha256": expected, "status": "NOT_PROVIDED"}
    path = Path(value).expanduser().resolve()
    actual = sha256_file(path) if path.is_file() else None
    return {
        "label": label,
        "checked": True,
        "path": str(path),
        "expected_sha256": expected,
        "actual_sha256": actual,
        "status": "PASS" if actual == expected else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "release_artifacts/core-provenance-validation.json")
    parser.add_argument("--platform-archive", default=os.environ.get("LV360_PLATFORM_BASELINE_ZIP"))
    parser.add_argument("--portal-archive", default=os.environ.get("LV360_PORTAL_BASELINE_ZIP"))
    args = parser.parse_args()

    from landvalue360_portal.financial_engine import ENGINE_VERSION, engine_source_hash

    errors: list[str] = []
    provenance_path = ROOT / "CORE_PROVENANCE.json"
    pairing_path = ROOT / "INTEGRATION_PAIRING.json"
    parity_path = ROOT / "release_artifacts/platform-core-parity.json"
    golden_path = ROOT / "release_artifacts/golden-cases-2.1.1.json"
    for required in (provenance_path, pairing_path, parity_path, golden_path):
        if not required.is_file():
            errors.append(f"Missing required provenance artifact: {required.relative_to(ROOT)}")

    if errors:
        report = {"status": "FAIL", "errors": errors}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 1

    provenance = load_json(provenance_path)
    pairing = load_json(pairing_path)
    parity = load_json(parity_path)
    golden = load_json(golden_path)
    current_source_hash = engine_source_hash()
    registered_hash = str((provenance.get("engine_registration") or {}).get("source_hash") or "")
    pairing_hash = str((pairing.get("embedded_engine") or {}).get("source_hash") or "")
    if registered_hash != current_source_hash:
        errors.append("CORE_PROVENANCE engine source hash does not match the current code")
    if pairing_hash != current_source_hash:
        errors.append("INTEGRATION_PAIRING engine source hash does not match the current code")
    if str((provenance.get("engine_registration") or {}).get("engine_version")) != str(ENGINE_VERSION):
        errors.append("Registered engine version does not match the imported engine")

    expected_parity_hash = str((provenance.get("parity_report") or {}).get("sha256") or "")
    actual_parity_hash = sha256_file(parity_path)
    if expected_parity_hash != actual_parity_hash:
        errors.append("Platform parity report hash does not match CORE_PROVENANCE")
    if parity.get("status") != "PASS":
        errors.append("Platform core parity report is not PASS")

    matched_rows = parity.get("matched_files") or []
    expected_paths = {str(row.get("path")) for row in matched_rows}
    current_paths: set[str] = set()
    core_mismatches: list[dict[str, str]] = []
    for package in CORE_PACKAGES:
        package_root = APP / package
        for path in package_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            current_paths.add(path.relative_to(APP).as_posix())
    if current_paths != expected_paths:
        missing = sorted(expected_paths - current_paths)
        extra = sorted(current_paths - expected_paths)
        if missing:
            errors.append(f"Vendored core files missing from current tree: {len(missing)}")
        if extra:
            errors.append(f"Unregistered vendored core files found: {len(extra)}")
    for row in matched_rows:
        relative = str(row.get("path"))
        expected = str(row.get("sha256"))
        path = APP / relative
        actual = sha256_file(path) if path.is_file() else ""
        if actual != expected:
            core_mismatches.append({"path": relative, "expected": expected, "actual": actual})
    if core_mismatches:
        errors.append(f"Vendored Platform core changed after parity capture: {len(core_mismatches)} files")

    fixture_hashes = golden.get("fixture_hashes") or {}
    golden_manifest = provenance.get("golden_cases") or {}
    cases_path = ROOT / "validation/golden_cases/cases.json"
    project_cases_path = ROOT / "validation/golden_cases/project_cases.json"
    expected_cases_hash = str(golden_manifest.get("contract_fixture_sha256") or "")
    expected_project_cases_hash = str(golden_manifest.get("project_fixture_sha256") or "")
    actual_cases_hash = sha256_file(cases_path) if cases_path.is_file() else ""
    actual_project_cases_hash = sha256_file(project_cases_path) if project_cases_path.is_file() else ""
    if actual_cases_hash != expected_cases_hash or fixture_hashes.get("cases.json") != actual_cases_hash:
        errors.append("Golden contract fixture hash mismatch")
    if actual_project_cases_hash != expected_project_cases_hash or fixture_hashes.get("project_cases.json") != actual_project_cases_hash:
        errors.append("Golden project fixture hash mismatch")
    if golden.get("status") != "PASS" or golden.get("total_passed") != golden.get("total_cases"):
        errors.append("Golden case validation is not complete")

    archive_checks = [
        optional_archive_check(
            "platform_engine_baseline",
            str((provenance.get("platform_engine_baseline") or {}).get("archive_sha256") or ""),
            args.platform_archive,
        ),
        optional_archive_check(
            "portal_baseline",
            str((provenance.get("portal_baseline") or {}).get("archive_sha256") or ""),
            args.portal_archive,
        ),
    ]
    for row in archive_checks:
        if row["checked"] and row["status"] != "PASS":
            errors.append(f"Baseline archive check failed: {row['label']}")

    report = {
        "status": "PASS" if not errors else "FAIL",
        "engine_version": str(ENGINE_VERSION),
        "engine_source_hash": current_source_hash,
        "registered_source_hash": registered_hash,
        "pairing_source_hash": pairing_hash,
        "parity_report_sha256": actual_parity_hash,
        "vendored_core_files": len(current_paths),
        "vendored_core_hash_mismatches": core_mismatches,
        "golden_cases": {
            "status": golden.get("status"),
            "passed": golden.get("total_passed"),
            "total": golden.get("total_cases"),
        },
        "baseline_archives": archive_checks,
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
