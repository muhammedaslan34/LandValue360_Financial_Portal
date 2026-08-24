"""Institutional report-readiness gates.

Advisory previews remain available for diagnosis. A report marked ``official``
is produced only from a current, reconciled Financial Truth, a published policy,
and non-demo inputs. The gate is presentation-independent and can therefore be
used by HTML, PDF, XLSX and external API exports.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from .models import CalculationRun, GovernmentCase, PolicyPackVersion, ProjectVersion
from .json_tools import sha256_json


_DEMO_MARKERS = {"DEMO_NOT_VALIDATED", "DEMO_ASSUMPTION", "SAMPLE", "TRAINING_ONLY"}


def _walk_demo_markers(value: Any, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(value, dict):
        status = str(value.get("input_status") or "").upper()
        if status in _DEMO_MARKERS:
            findings.append({"path": f"{path}.input_status", "value": status})
        if value.get("template_is_starting_point") is True and value.get("template_financial_values_confirmed") is not True:
            findings.append({"path": path, "value": "UNCONFIRMED_TEMPLATE_VALUES"})
        classification = str(value.get("classification") or value.get("source_type") or "").upper()
        if classification in _DEMO_MARKERS:
            findings.append({"path": path, "value": classification})
        for key, item in value.items():
            findings.extend(_walk_demo_markers(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_walk_demo_markers(item, f"{path}[{index}]"))
    return findings

def find_demo_inputs(value: Any) -> list[dict[str, str]]:
    """Return demo/training markers that block an official run or report."""
    return _walk_demo_markers(value)


def _policy_status(policy: PolicyPackVersion | None) -> tuple[bool, str]:
    if policy is None:
        return False, "Policy version was not found."
    if str(policy.status).upper() != "PUBLISHED":
        return False, "Policy version is not published."
    now = datetime.now(timezone.utc)
    start = policy.effective_from
    end = policy.effective_to
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start is not None and start > now:
        return False, "Policy version is not yet effective."
    if end is not None and end < now:
        return False, "Policy version is no longer effective."
    return True, "Policy version is published and effective."


def _truth(output: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(output.get("financial_truth") or ((output.get("unified_financial_result") or {}).get("financial_truth") or {}))


def _policy_family(policy: PolicyPackVersion | None) -> str:
    if policy is None:
        return ""
    snapshot = policy.policy_snapshot or {}
    guidance = snapshot.get("policy_guidance") or {}
    return str(
        snapshot.get("policy_type")
        or snapshot.get("policy_family")
        or guidance.get("policy_type")
        or guidance.get("policy_family")
        or ""
    ).upper()


def _gate_common(
    *,
    output: dict[str, Any],
    input_snapshot: dict[str, Any],
    input_hash_current: bool,
    project_policy: PolicyPackVersion | None,
    valuation_policy: PolicyPackVersion | None,
) -> dict[str, Any]:
    """Evaluate whether a run may be labelled an institutional/official report.

    Advisory reports remain available when this gate fails.  The gate never
    changes the financial result; it only prevents a stale, legacy totals-based,
    unreconciled, or incompletely governed result from being labelled official.
    """

    checks: list[dict[str, Any]] = []

    def add(code: str, passed: bool, detail: str) -> None:
        checks.append({"code": code, "passed": bool(passed), "detail": detail})

    truth = _truth(output)
    reconciliation = output.get("financial_reconciliation") or {}
    authority = str(output.get("display_authority") or (output.get("run_context") or {}).get("display_authority") or "").upper()
    project_policy_ok, project_policy_detail = _policy_status(project_policy)
    valuation_policy_ok, valuation_policy_detail = _policy_status(valuation_policy)
    project_family = _policy_family(project_policy)
    valuation_family = _policy_family(valuation_policy)
    demo_findings = find_demo_inputs(input_snapshot)
    structural = list(truth.get("failed_structural_invariants") or [])
    output_status = str(output.get("status") or "").upper()
    try:
        terminal_obligations = abs(Decimal(str(truth.get("terminal_unpaid_obligations") or "0")))
    except (InvalidOperation, ValueError):
        terminal_obligations = Decimal("Infinity")
    add("FINANCIAL_TRUTH_PRESENT", bool(truth), "Canonical Financial Truth is present." if truth else "Canonical Financial Truth is missing.")
    add("FINANCIAL_TRUTH_AUTHORITY", authority == "FINANCIAL_TRUTH", f"Display authority is {authority or 'missing'}.")
    add("RESULT_USABLE", bool(truth.get("result_usable")), "Financial Truth is usable." if truth.get("result_usable") else "Financial Truth is not usable.")
    add("FINANCIAL_CLOSURE", bool(truth.get("closure_passed")), "Financial closure passes." if truth.get("closure_passed") else "Financial closure is not complete.")
    add("TERMINAL_OBLIGATIONS_ZERO", terminal_obligations <= Decimal("0.01"), f"Terminal unpaid obligations are {terminal_obligations:.2f}.")
    add("RUN_COMPLETED", output_status in {"SUCCESS", "SUCCESS_WITH_WARNINGS"}, f"Calculation output status is {output_status or 'missing'}.")
    add("LEDGER_RECONCILED", str(reconciliation.get("status") or "").upper() == "RECONCILED", f"Financial reconciliation status is {reconciliation.get('status') or 'missing'}.")
    add("CASH_RECONCILED", bool(truth.get("cash_reconciliation_passed")), "Monthly cash reconciles." if truth.get("cash_reconciliation_passed") else "Monthly cash does not reconcile.")
    add("LEDGER_INVARIANTS", bool(truth.get("ledger_invariants_passed")) and not structural, "Mandatory ledger invariants pass." if not structural else f"Structural invariant failures: {structural}.")
    add("CURRENT_INPUT_HASH", input_hash_current, "The report uses the current project-version inputs." if input_hash_current else "The calculation is stale relative to the selected project version.")
    add("PUBLISHED_PROJECT_POLICY", project_policy_ok and project_family in {"", "PROJECT"}, project_policy_detail if project_family in {"", "PROJECT"} else f"Expected PROJECT policy, received {project_family}.")
    add("PUBLISHED_VALUATION_POLICY", valuation_policy_ok and valuation_family in {"VALUATION"}, valuation_policy_detail if valuation_family == "VALUATION" else f"Expected VALUATION policy, received {valuation_family or 'missing'}.")
    add("NO_DEMO_INPUTS", not demo_findings, "No demo or unconfirmed template values were detected." if not demo_findings else f"Demo/unconfirmed values detected at {len(demo_findings)} location(s).")
    add("OUTPUT_HASH_PRESENT", bool(output.get("output_hash") or output.get("calculation_hash") or (output.get("unified_financial_result") or {}).get("calculation_hash")), "A calculation/output hash is present." if (output.get("output_hash") or output.get("calculation_hash") or (output.get("unified_financial_result") or {}).get("calculation_hash")) else "No calculation/output hash is present.")

    failures = [row for row in checks if not row["passed"]]
    return {
        "ready": not failures,
        "status": "READY_FOR_OFFICIAL_REPORT" if not failures else "ADVISORY_ONLY",
        "checks": checks,
        "failures": failures,
        "demo_findings": demo_findings[:50],
        "financial_truth_version": truth.get("financial_truth_version"),
        "calculation_hash": truth.get("calculation_hash") or (output.get("unified_financial_result") or {}).get("calculation_hash"),
        "project_policy_version_id": getattr(project_policy, "id", None),
        "valuation_policy_version_id": getattr(valuation_policy, "id", None),
    }

def calculation_report_readiness(session: Session, run: CalculationRun, *, require_locked: bool = False) -> dict[str, Any]:
    version = session.get(ProjectVersion, run.project_version_id)
    project_policy = session.get(PolicyPackVersion, run.policy_pack_version_id)
    valuation_policy = session.get(PolicyPackVersion, run.valuation_policy_pack_version_id) if run.valuation_policy_pack_version_id else None
    output = deepcopy(run.output_snapshot or {})
    output.setdefault("status", run.status)
    output.setdefault("output_hash", run.output_hash)
    run_context = output.get("run_context") or {}
    current = bool(version and run_context.get("project_version_input_hash") == version.input_hash)
    result = _gate_common(
        output=output,
        input_snapshot=run.input_snapshot or {},
        input_hash_current=current,
        project_policy=project_policy,
        valuation_policy=valuation_policy,
    )
    if require_locked:
        lock_check = {
            "code": "RUN_LOCKED",
            "passed": run.locked_at is not None,
            "detail": "Calculation run is locked and immutable." if run.locked_at is not None else "Lock the calculation run before issuing an official report.",
        }
        result["checks"].append(lock_check)
        if not lock_check["passed"]:
            result["failures"].append(lock_check)
            result["ready"] = False
            result["status"] = "ADVISORY_ONLY"
    result["locked_at"] = run.locked_at.isoformat() if run.locked_at else None
    return result


def government_report_readiness(session: Session, record: GovernmentCase) -> dict[str, Any]:
    version = session.get(ProjectVersion, record.project_version_id)
    project_policy = session.get(PolicyPackVersion, record.policy_pack_version_id)
    valuation_policy = session.get(PolicyPackVersion, record.valuation_policy_pack_version_id) if record.valuation_policy_pack_version_id else None
    output = deepcopy(record.output_snapshot or {})
    output.setdefault("status", "SUCCESS" if record.output_snapshot else "FAILED")
    if record.output_hash:
        output.setdefault("output_hash", record.output_hash)
    current = bool(
        version
        and record.input_hash
        and record.input_hash == sha256_json({"mode": record.mode, "input_snapshot": record.input_snapshot or {}})
    )
    # Government-case inputs include the scenario/offer layer; project-version
    # freshness is separately disclosed below because the case hash is not the
    # same value as the project-version hash.
    result = _gate_common(
        output=output,
        input_snapshot={"project": version.input_snapshot if version else {}, "case": record.input_snapshot or {}},
        input_hash_current=current,
        project_policy=project_policy,
        valuation_policy=valuation_policy,
    )
    for row in result["checks"]:
        if row["code"] == "CURRENT_INPUT_HASH":
            row["detail"] = "Government case input is locked; project-version freshness is verified by the stored case link."
    return result
