"""Application adapter for LandValue360 Engine 2.1.1.

All financial surfaces call this adapter.  It executes the monthly commercial
model once, reconciles it into the canonical event ledger, evaluates mandatory
engine invariants, and publishes one financial truth contract for live preview,
results, risk, sensitivity, tender and reports.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from landvalue360_kernel.ledger import build_engine_invariants, build_event_ledger
from landvalue360_kernel.manifest import ENGINE_VERSION, engine_manifest

from .financial_truth import build_financial_truth
from .json_tools import sha256_json
from .landowner_studio import run_landowner_studio
from .project_normalization import materialize_project_for_calculation

from landvalue360_common.versions import UNIFIED_ENGINE_ADAPTER_VERSION


def run_unified_financial_engine(
    project_snapshot: dict[str, Any],
    policy_snapshot: dict[str, Any] | None = None,
    *,
    legacy_output: dict[str, Any] | None = None,
    selected_only: bool = False,
) -> dict[str, Any]:
    """Return the authoritative, reconciled financial model result.

    The result preserves the detailed fair-share comparison and monthly rows
    expected by the existing application, while adding the versioned engine
    manifest, event ledger, invariant report and canonical financial truth.
    """

    source_project_snapshot = deepcopy(project_snapshot)
    calculation_project_snapshot = materialize_project_for_calculation(source_project_snapshot)
    model = run_landowner_studio(
        calculation_project_snapshot, deepcopy(policy_snapshot or {}), selected_only=selected_only
    )
    ledger = build_event_ledger(model.get("monthly_cashflow") or [])
    invariants = build_engine_invariants(model, ledger)

    # Contract-policy failure is an economic finding, not a corrupt ledger.
    # Only structural ledger/closure invariants block the calculation itself.
    invariant_failures = [
        row for row in invariants.get("checks") or []
        if bool(row.get("mandatory", True)) and row.get("passed") is False
    ]
    structural_failures = [
        row for row in invariant_failures
        if str(row.get("invariant_id") or row.get("id") or "").upper()
        != "SELECTED_CONTRACT_CONSTRAINTS_PASS"
    ]
    selected = model.get("selected_contract") or {}
    summary = model.setdefault("summary", {})
    original_status = str(summary.get("status") or "FAIL")
    calculation_valid = bool(selected.get("calculation_valid", summary.get("calculation_valid", False)))
    cash_reconciled = bool(selected.get("cash_reconciliation_passed", summary.get("cash_reconciliation_passed", False)))
    authoritative_status = "PASS" if calculation_valid and cash_reconciled and not structural_failures else "FAIL"
    summary["landowner_studio_status"] = original_status
    summary["calculation_status"] = authoritative_status
    summary["structural_invariants_passed"] = not structural_failures
    summary["status"] = authoritative_status
    model["engine_manifest"] = engine_manifest()
    model["engine_version"] = ENGINE_VERSION
    model["unified_engine_adapter_version"] = UNIFIED_ENGINE_ADAPTER_VERSION
    model["single_source_financial_kernel"] = "landvalue360_kernel.monthly_engine"
    model["event_ledger"] = ledger
    model["engine_invariants"] = invariants
    truth = build_financial_truth(model, legacy_output=legacy_output, invariants=invariants)
    model["financial_truth"] = truth
    model["display_authority"] = "financial_truth"
    model["decision_explanation"] = {
        **(model.get("decision_explanation") or {}),
        "status": authoritative_status,
        "economic_feasibility": truth.get("economic_feasibility"),
        "policy_compliant": truth.get("policy_compliant"),
        "engine_failed_invariants": [row.get("invariant_id") or row.get("id") for row in structural_failures],
        "contract_findings": [row.get("invariant_id") or row.get("id") for row in invariant_failures if row not in structural_failures],
        "message": (
            "The monthly calculation and structural ledger invariants passed; economic and policy findings are reported separately."
            if authoritative_status == "PASS"
            else "The monthly calculation or one or more structural ledger/closure invariants failed."
        ),
    }
    model["provenance"] = {
        "engine_version": ENGINE_VERSION,
        "adapter_version": UNIFIED_ENGINE_ADAPTER_VERSION,
        "project_input_hash": sha256_json(project_snapshot),
        "policy_input_hash": sha256_json(policy_snapshot or {}),
        "ledger_hash": ledger.get("ledger_hash"),
        "invariant_hash": invariants.get("invariant_hash"),
        "legacy_result_attached": legacy_output is not None,
        "selected_only_evaluation": selected_only,
    }

    # The fingerprint is calculated over the full result except for the hash
    # fields themselves.  This avoids a self-referential digest while keeping
    # repeated executions of identical inputs byte-for-byte deterministic.
    model.pop("calculation_hash", None)
    model["financial_truth"].pop("calculation_hash", None)
    hash_payload = deepcopy(model)
    calculation_hash = sha256_json(hash_payload)
    model["calculation_hash"] = calculation_hash
    model["financial_truth"]["calculation_hash"] = calculation_hash
    model["provenance"]["hash_scope"] = "Full unified result excluding calculation_hash fields"
    return model
