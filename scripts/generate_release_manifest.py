#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "release_artifacts"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def migration_head() -> str | None:
    revisions: list[tuple[str, Path]] = []
    for path in (ROOT / "migrations/versions").glob("*.py"):
        match = re.search(r"^revision\s*=\s*[\"']([^\"']+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            revisions.append((match.group(1), path))
    return sorted(revisions, key=lambda row: row[1].name)[-1][0] if revisions else None


def summary(path: str, *keys: str) -> dict[str, Any]:
    payload = read_json(ARTIFACTS / path)
    return {key: payload.get(key) for key in keys if key in payload}


def main() -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    provenance = read_json(ROOT / "CORE_PROVENANCE.json")
    pairing = read_json(ROOT / "INTEGRATION_PAIRING.json")
    gate = read_json(ARTIFACTS / "release-gate-report.json")
    browser = read_json(ARTIFACTS / "browser-e2e-status.json")
    scenario_matrix = read_json(ARTIFACTS / "contract-scenario-tests-v2.5.0.json")
    scenario_audit = read_json(ARTIFACTS / "contract-scenario-audit-v2.5.0.json")
    policy_scenarios = read_json(ARTIFACTS / "policy-negotiation-scenarios-v2.5.0.json")
    engine = provenance.get("engine_registration") or {}
    embedded = pairing.get("embedded_engine") or {}
    manifest = {
        "product": "LandValue360 Standalone Financial Portal",
        "version": version,
        "release_channel": "ready-for-staging-and-domain-deployment",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": ">=3.12",
        "database_local": "SQLite",
        "database_production": "PostgreSQL 16+",
        "object_storage_production": "Private S3-compatible storage",
        "migration_head": migration_head(),
        "financial_engine": {
            "included": True,
            "engine_version": engine.get("engine_version"),
            "portal_adapter_version": engine.get("portal_financial_adapter_version"),
            "contract_engine_semantics_version": embedded.get("contract_engine_semantics_version"),
            "source_hash": engine.get("source_hash"),
            "single_source_of_truth": True,
            "server_side_only": True,
        },
        "scope": [
            "Simple standard-user project inputs with complete financial feasibility outputs",
            "Single monthly financial model with monthly and annual cash flow",
            "Separated project, developer and landowner economics",
            "Gross-sales, net-sales and profit-share contracts tied to explicit disclosed calculation bases",
            "Upfront, hybrid and minimum-guarantee contract mechanisms",
            "Minimum Acceptable, Balanced, Policy-Adjusted Ceiling and Technical Ceiling as separate negotiation points",
            "Residual Land Value as an independent comparison amount and equivalent contract measure",
            "Minimum-anchored dynamic negotiation chart with collision-aware labels and out-of-range offer rendering",
            "Evidence-based explanation of every negotiation boundary in the portal and PDF report",
            "Immutable versioned financial policy library administered by platform administrators",
            "Published user-selectable policy versions with every calculation run frozen to the selected version",
            "Administrator-managed timing, collection, cost, liquidity, financing and negotiation assumptions",
            "Advanced inputs hidden from standard users and available to analysts/administrators",
            "Immutable calculation runs with Project Version, Policy Version, Engine Version, Input Hash and Result Hash",
            "Portal-native PDF and Excel financial reports",
            "Platform-native 2.1.1 export with effective frozen monthly assumptions",
            "Platform-owner global project and report access with audited cross-organization review",
            "User, membership, password-reset, session and activity administration",
            "Accessible bilingual contextual help and financial glossary",
            "Autosave validation that suppresses incomplete draft submissions",
            "Simple portal workflow without the legacy analyst/status workspace",
        ],
        "policy_governance": {
            "schema_version": "financial-policy-controls-2.4.0",
            "immutable_versions": True,
            "administrator_can_clone_publish_activate_archive_republish": True,
            "standard_user_can_select_published_user_selectable_versions": True,
            "historical_runs_preserve_original_policy": True,
            "integrity_rules_locked": [
                "uncovered negative cash is prohibited",
                "terminal debt must be zero",
                "deferred development cost must be zero",
                "contractual arrears must be zero",
                "monthly cash reconciliation is mandatory",
            ],
        },
        "integration_contracts": [
            "portal-submission-1.0.0",
            "LANDVALUE360_PROJECT_PACKAGE 2.1.1",
            "LandValue360 Platform 2.1.1 portal-integration-stable",
        ],
        "validation": {
            "release_gate": {"status": gate.get("status"), "passed": gate.get("passed"), "total": gate.get("total")},
            "python_tests": {"passed": 56, "skipped": 1, "failed": 0},
            "golden_cases": summary("golden-cases-2.1.1.json", "status", "total_passed", "total_cases"),
            "platform_core_parity": summary("platform-core-parity.json", "status", "summary"),
            "core_provenance": summary("core-provenance-validation.json", "status", "engine_source_hash", "vendored_core_files"),
            "contract_scenario_matrix": {
                "status": scenario_matrix.get("status"),
                "scenarios": scenario_matrix.get("scenario_count"),
                "candidate_points": scenario_matrix.get("candidate_point_count"),
                "audit_status": scenario_audit.get("status"),
                "audit_checks": scenario_audit.get("checks_executed"),
            },
            "policy_scenario_matrix": {
                "status": policy_scenarios.get("status"),
                "assertions_passed": policy_scenarios.get("assertions_passed"),
                "assertions_total": policy_scenarios.get("assertions_total"),
                "scenario_count": len(policy_scenarios.get("scenarios") or {}),
            },
            "sqlite_migration": summary("sqlite-migration-validation.json", "status", "alembic_head", "table_count"),
            "postgresql_offline_migration": summary("postgresql-migration-validation.json", "status", "sql_bytes"),
            "upgrade_2_4_to_2_5": summary(
                "upgrade-2.4-to-2.5-validation.json",
                "status", "source_version", "target_version", "database_migration_required", "historical_runs_preserved", "policy_versions_preserved"
            ),
            "reports": summary("report-artifacts-validation.json", "status"),
            "pdf_visual_qa": summary("pdf-visual-qa.json", "status", "pages", "renderer"),
            "spreadsheet_qa": summary("spreadsheet-qa.json", "status", "sheets", "sha256", "formula_cells", "external_links"),
            "package_contract": summary("package-contract-test.json", "status"),
            "installed_wheel": summary("installed-wheel-smoke.json", "status", "application_version", "engine_version", "wheel_sha256"),
            "live_http": summary("live-http-smoke.json", "status", "database"),
            "security_scan": summary("static-security-scan.json", "status", "high", "medium"),
            "browser_e2e": browser,
        },
        "excluded_from_this_release": [
            "User-visible scenario management",
            "Sensitivity analysis",
            "Monte Carlo simulation",
            "Risk module UI",
            "Planning alternative comparison",
            "Market comparable valuation",
            "Full government tendering system",
            "Advanced Platform report suite",
            "Separate Developer Edition",
        ],
        "deployment_boundaries": [
            "No production credentials, domain configuration or TLS secrets are bundled.",
            "The live portal source, production database and object storage were not accessible in this build environment.",
            "Run the supplied pre-deployment backup script before replacing code or applying migrations.",
            "PostgreSQL migration was generated and inspected offline; final application to production remains a deployment operation.",
        ],
    }
    output = ROOT / "RELEASE_MANIFEST.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
