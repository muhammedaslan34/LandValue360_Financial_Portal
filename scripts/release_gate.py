#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def base_environment() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    app_path = str(ROOT / "app")
    env["PYTHONPATH"] = app_path + (os.pathsep + existing if existing and existing != app_path else "")
    env.setdefault("LV360_PORTAL_ENV", "development")
    env.setdefault("LV360_PORTAL_SECRET_KEY", "release-gate-secret-key-with-sufficient-length")
    env.setdefault("LV360_PORTAL_TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")
    # Third-party auto-loaded pytest plugins can keep worker threads alive after
    # isolated test-file runs in release automation. The project tests do not
    # require them, so disable auto-loading for deterministic release gates.
    env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    return env


def execute(name: str, command: list[str], *, env: dict[str, str]) -> dict[str, Any]:
    print(f"[RUN] {name}", flush=True)
    started = time.monotonic()
    # Use temporary files instead of PIPEs. Some integration tests start short-lived
    # child processes that inherit stdout/stderr; capture_output can wait for those
    # descendants to close the pipe even after pytest itself has completed.
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdout_file, tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
        proc = subprocess.run(command, cwd=ROOT, env=env, stdout=stdout_file, stderr=stderr_file, text=True)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_text = stdout_file.read()
        stderr_text = stderr_file.read()
    elapsed = round(time.monotonic() - started, 3)
    passed = proc.returncode == 0
    print(f"[{'PASS' if passed else 'FAIL'}] {name} ({elapsed}s)", flush=True)
    return {
        "name": name,
        "command": command,
        "passed": passed,
        "returncode": proc.returncode,
        "duration_seconds": elapsed,
        "stdout_tail": stdout_text[-6000:],
        "stderr_tail": stderr_text[-6000:],
    }


def javascript_checks() -> list[tuple[str, list[str]]]:
    if not shutil.which("node"):
        return [("javascript_syntax", ["node", "--check"])]
    return [
        (f"javascript_{path.name}", ["node", "--check", str(path)])
        for path in sorted((ROOT / "app/landvalue360_portal/static").rglob("*.js"))
    ]


def phase_commands(phase: str) -> list[tuple[str, list[str]]]:
    if phase == "foundation":
        test_files = [
            "tests/test_auth_security.py",
            "tests/test_calculations.py",
            "tests/test_contract_semantics_v230.py",
            "tests/test_end_to_end.py",
            "tests/test_files_and_packages.py",
            "tests/test_financial_portal.py",
            "tests/test_negotiation_policy_v240.py",
            "tests/test_policy_admin_coverage_v240.py",
            "tests/test_policy_versions_v240.py",
            "tests/test_release_contracts.py",
            "tests/test_v250_negotiation_ui_and_reports.py",
            "tests/test_workflow_and_admin.py",
            "tests/browser/test_portal_e2e.py",
        ]
        return [
            ("python_compileall", [sys.executable, "-m", "compileall", "-q", "app", "scripts", "tests"]),
            *[(f"pytest_{Path(test_file).stem}", [sys.executable, "scripts/pytest_isolated.py", "-q", test_file]) for test_file in test_files],
            *javascript_checks(),
        ]
    if phase == "artifacts":
        scripts = [
            ("database_migrations", "scripts/validate_migrations.py"),
            ("runtime_preflight", "scripts/runtime_preflight.py"),
            ("golden_cases", "scripts/validate_golden_cases.py"),
            ("core_provenance", "scripts/validate_core_provenance.py"),
            ("policy_negotiation_scenarios", "scripts/validate_policy_negotiation_v250.py"),
            ("contract_scenario_matrix", "scripts/validate_v250_scenarios.py"),
            ("sample_generation", "scripts/generate_samples.py"),
            ("package_contract", "scripts/package_contract_test.py"),
            ("financial_report_generation", "scripts/generate_financial_report_samples.py"),
            ("financial_report_validation", "scripts/validate_report_artifacts.py"),
            ("portal_schema_generation", "scripts/generate_portal_schema.py"),
            ("deployment_validation", "scripts/validate_deployment.py"),
            ("openapi_generation", "scripts/generate_openapi.py"),
            ("sbom_generation", "scripts/generate_sbom.py"),
            ("static_security_scan", "scripts/security_scan.py"),
        ]
        return [(name, [sys.executable, script]) for name, script in scripts]
    raise ValueError(f"Unknown phase: {phase}")


def write_phase(phase: str, checks: list[dict[str, Any]]) -> Path:
    passed = sum(bool(row["passed"]) for row in checks)
    payload = {
        "phase": phase,
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }
    path = ARTIFACTS / f"release-gate-{phase}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_phase(phase: str, env: dict[str, str]) -> dict[str, Any]:
    checks = [execute(name, command, env=env) for name, command in phase_commands(phase)]
    path = write_phase(phase, checks)
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps({"phase": phase, "status": payload["status"], "passed": payload["passed"], "total": payload["total"]}, indent=2))
    return payload


def collate() -> dict[str, Any]:
    phases: list[dict[str, Any]] = []
    missing: list[str] = []
    for phase in ("foundation", "artifacts"):
        path = ARTIFACTS / f"release-gate-{phase}.json"
        if not path.is_file():
            missing.append(phase)
        else:
            phases.append(json.loads(path.read_text(encoding="utf-8")))
    checks = [row for phase in phases for row in phase.get("checks", [])]
    passed = sum(bool(row.get("passed")) for row in checks)
    status = "PASS" if not missing and phases and all(phase.get("status") == "PASS" for phase in phases) else "FAIL"
    report = {
        "status": status,
        "product": "LandValue360 Standalone Financial Portal",
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_executable": sys.executable,
        "phases": [{"phase": phase.get("phase"), "status": phase.get("status"), "passed": phase.get("passed"), "total": phase.get("total")} for phase in phases],
        "missing_phases": missing,
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }
    path = ARTIFACTS / "release-gate-report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "passed": passed, "total": len(checks), "missing_phases": missing}, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["foundation", "artifacts", "all"], default="all")
    parser.add_argument("--collate", action="store_true")
    args = parser.parse_args()
    if args.collate:
        report = collate()
        return 0 if report["status"] == "PASS" else 1
    env = base_environment()
    phases = ("foundation", "artifacts") if args.phase == "all" else (args.phase,)
    status = "PASS"
    for phase in phases:
        payload = run_phase(phase, env)
        if payload["status"] != "PASS":
            status = "FAIL"
    if args.phase == "all":
        status = collate()["status"]
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
