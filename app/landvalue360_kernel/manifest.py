"""Versioned manifest for the LandValue360 deterministic calculation engine."""
from __future__ import annotations

from copy import deepcopy

from landvalue360_common.versions import ENGINE_VERSION, FORMULA_REGISTRY_VERSION
INPUT_SCHEMA_VERSION = "2.1.1"
LEGACY_INPUT_SCHEMA_VERSIONS = ("0.3.0", "0.2.0")
SUPPORTED_INPUT_SCHEMA_VERSIONS = (INPUT_SCHEMA_VERSION, *LEGACY_INPUT_SCHEMA_VERSIONS)
MONTHLY_LEDGER_VERSION = "2.1.1"
ROUNDING_POLICY_VERSION = "2.1.1"
DAY_COUNT_BASIS = "ACT/365F"
DECIMAL_PRECISION = 50

ENGINE_MANIFEST = {
    "engine_name": "LandValue360 Unified Monthly Deterministic Financial Kernel",
    "engine_version": ENGINE_VERSION,
    "input_schema_version": INPUT_SCHEMA_VERSION,
    "supported_input_schema_versions": list(SUPPORTED_INPUT_SCHEMA_VERSIONS),
    "monthly_ledger_version": MONTHLY_LEDGER_VERSION,
    "formula_registry_version": FORMULA_REGISTRY_VERSION,
    "rounding_policy_version": ROUNDING_POLICY_VERSION,
    "arithmetic": "decimal",
    "decimal_precision": DECIMAL_PRECISION,
    "date_basis": "actual monthly dates",
    "day_count_basis": DAY_COUNT_BASIS,
    "deterministic": True,
    "single_source_of_truth": True,
    "terminal_invariants": [
        "terminal_debt = 0",
        "deferred_development_cost = 0",
        "contractual_arrears = 0",
        "finance_arrears = 0",
        "mandatory_shortfall = 0",
        "unmodeled_scope = 0",
    ],
}


def engine_manifest() -> dict:
    """Return an isolated JSON-safe copy of the engine manifest."""

    return deepcopy(ENGINE_MANIFEST)
