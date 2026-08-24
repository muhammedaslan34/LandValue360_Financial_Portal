"""Ad-hoc, non-persistent scenario and negotiation analysis for release 0.5."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ..audit import record_audit
from ..context import AuthContext
from ..errors import ConflictError, NotFoundError
from ..json_tools import json_merge_patch, sha256_json
from .calculations import compose_calculation_envelope, execute_calculation_envelope
from .policies import require_operational_policy
from .tenant import get_policy_version, get_project_version, get_scenario


def _base_project_snapshot(project_version, scenario=None) -> dict[str, Any]:
    base = deepcopy(project_version.input_snapshot)
    result = deepcopy(base)
    if scenario is not None:
        result = json_merge_patch(result, scenario.override_snapshot)
    result["project_id"] = base.get("project_id")
    result["project_name"] = base.get("project_name")
    return result


def _metric_summary(output: dict[str, Any]) -> dict[str, Any]:
    truth = output.get("financial_truth") or ((output.get("unified_financial_result") or {}).get("financial_truth") or {})
    if truth:
        unified = output.get("unified_financial_result") or {}
        selected = unified.get("selected_contract") or {}
        invariants = unified.get("engine_invariants") or output.get("engine_invariants") or {}
        failed = list(truth.get("failed_constraints") or []) + list(invariants.get("failed_invariant_ids") or [])
        finance_metrics = (output.get("finance_analysis") or {}).get("metrics") or {}
        return {
            "status": "SUCCESS" if truth.get("status") == "PASS" else "SUCCESS_WITH_WARNINGS",
            "feasible": bool(truth.get("feasible")) and not failed,
            "approved_share": truth.get("approved_share"),
            "approved_selection": truth.get("approved_selection"),
            "partnership_method": truth.get("method"),
            "project_npv": truth.get("project_npv"),
            "project_irr": truth.get("project_irr"),
            "developer_npv": truth.get("developer_npv"),
            "developer_unlevered_irr": truth.get("developer_irr"),
            "developer_nominal_profit": truth.get("developer_profit"),
            "developer_profit_on_cost": truth.get("developer_profit_on_cost"),
            "developer_unlevered_multiple": truth.get("developer_multiple"),
            "developer_payback_years": None,
            "peak_unlevered_funding": truth.get("peak_equity"),
            "unlevered_funding_gap": truth.get("funding_gap"),
            "government_cash_total": truth.get("government_consideration"),
            "government_cash_npv": truth.get("government_npv"),
            "government_surplus_over_land_value": selected.get("government_surplus_over_land_value"),
            "government_multiple_on_land_value": selected.get("government_multiple_on_land_value"),
            "developer_equity_irr": truth.get("developer_equity_irr"),
            "developer_equity_npv": truth.get("developer_equity_npv"),
            "developer_equity_multiple": truth.get("developer_multiple"),
            "peak_equity": truth.get("peak_equity"),
            "peak_senior_debt": truth.get("peak_debt"),
            "aggregate_dscr": finance_metrics.get("aggregate_dscr"),
            "minimum_period_dscr": finance_metrics.get("minimum_period_dscr"),
            "loan_to_cost": finance_metrics.get("loan_to_cost"),
            "loan_to_value_proxy": finance_metrics.get("loan_to_value_proxy"),
            "structured_funding_gap": truth.get("funding_gap"),
            "finance_status": "PASS" if truth.get("status") == "PASS" else "FAIL",
            "failed_core_constraints": list(truth.get("failed_constraints") or []),
            "failed_finance_constraints": list(invariants.get("failed_invariant_ids") or []),
            "core_pass_count": 0,
            "core_fail_count": len(truth.get("failed_constraints") or []),
            "finance_pass_count": len(invariants.get("checks") or []) - len(invariants.get("failed_invariant_ids") or []),
            "finance_fail_count": len(invariants.get("failed_invariant_ids") or []),
            "fair_share": output.get("fair_share"),
            "engine_version": (output.get("engine_manifest") or {}).get("engine_version"),
            "calculation_hash": truth.get("calculation_hash"),
        }

    approved = output.get("approved_case") or {}
    metrics = approved.get("metrics") or {}
    finance = output.get("finance_analysis") or {}
    finance_metrics = finance.get("metrics") or {}
    core_constraints = approved.get("constraints") or []
    finance_constraints = finance.get("constraints") or []
    failed_core = [item.get("constraint_id") for item in core_constraints if item.get("mandatory") and not item.get("passed")]
    failed_finance = [item.get("constraint_id") for item in finance_constraints if item.get("mandatory") and not item.get("passed")]
    return {
        "status": output.get("status"),
        "feasible": bool(approved.get("feasible")) and output.get("status") != "FAILED",
        "approved_share": output.get("approved_share"),
        "approved_selection": output.get("approved_selection"),
        "partnership_method": (approved.get("partnership") or {}).get("method"),
        "project_npv": metrics.get("project_npv"),
        "project_irr": metrics.get("project_irr"),
        "developer_npv": metrics.get("developer_npv"),
        "developer_unlevered_irr": metrics.get("developer_irr"),
        "developer_nominal_profit": metrics.get("developer_nominal_profit"),
        "developer_profit_on_cost": metrics.get("developer_profit_on_cost"),
        "developer_unlevered_multiple": metrics.get("developer_capital_multiple"),
        "developer_payback_years": metrics.get("developer_payback_years"),
        "peak_unlevered_funding": metrics.get("peak_developer_funding"),
        "unlevered_funding_gap": metrics.get("funding_gap"),
        "government_cash_total": metrics.get("government_cash_total"),
        "government_cash_npv": metrics.get("government_cash_npv"),
        "government_surplus_over_land_value": metrics.get("government_nominal_surplus_over_land_value"),
        "government_multiple_on_land_value": metrics.get("government_nominal_multiple_on_land_value"),
        "developer_equity_irr": finance_metrics.get("developer_equity_irr"),
        "developer_equity_npv": finance_metrics.get("developer_equity_npv"),
        "developer_equity_multiple": finance_metrics.get("developer_equity_multiple"),
        "peak_equity": finance_metrics.get("peak_equity"),
        "peak_senior_debt": finance_metrics.get("peak_senior_debt"),
        "aggregate_dscr": finance_metrics.get("aggregate_dscr"),
        "minimum_period_dscr": finance_metrics.get("minimum_period_dscr"),
        "loan_to_cost": finance_metrics.get("loan_to_cost"),
        "loan_to_value_proxy": finance_metrics.get("loan_to_value_proxy"),
        "structured_funding_gap": finance_metrics.get("structured_funding_gap"),
        "finance_status": finance.get("status"),
        "failed_core_constraints": failed_core,
        "failed_finance_constraints": failed_finance,
        "core_pass_count": sum(1 for item in core_constraints if item.get("passed")),
        "core_fail_count": sum(1 for item in core_constraints if not item.get("passed")),
        "finance_pass_count": sum(1 for item in finance_constraints if item.get("passed")),
        "finance_fail_count": sum(1 for item in finance_constraints if not item.get("passed")),
        "fair_share": output.get("fair_share"),
    }


def compare_scenarios(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_ids: list[str],
    include_base: bool,
) -> dict[str, Any]:
    version = get_project_version(session, context, project_version_id)
    policy = require_operational_policy(
        get_policy_version(session, context, policy_pack_version_id),
        edition="DEVELOPER",
        expected_type="PROJECT",
    )
    valuation_policy = require_operational_policy(
        get_policy_version(session, context, valuation_policy_pack_version_id),
        edition="DEVELOPER",
        expected_type="VALUATION",
    )
    scenarios = []
    for scenario_id in scenario_ids:
        scenario = get_scenario(session, context, scenario_id)
        if scenario.project_version_id != version.id:
            raise NotFoundError("Scenario does not belong to the selected project version.")
        scenarios.append(scenario)

    rows: list[dict[str, Any]] = []
    if include_base:
        scenarios_with_base: list[Any] = [None, *scenarios]
    else:
        scenarios_with_base = list(scenarios)
    for scenario in scenarios_with_base:
        project = _base_project_snapshot(version, scenario)
        label = "Base case" if scenario is None else scenario.name
        code = "BASE" if scenario is None else scenario.code
        envelope = compose_calculation_envelope(
            project_snapshot=project,
            policy_snapshot=policy.policy_snapshot,
            valuation_policy_snapshot=valuation_policy.policy_snapshot,
            case_id=f"SCENARIO-{code}",
            description=f"Scenario comparison: {label}",
        )
        output = execute_calculation_envelope(envelope, optimize_share=False, include_solver=False)
        rows.append(
            {
                "scenario_id": None if scenario is None else scenario.id,
                "code": code,
                "name": label,
                "status": "BASE" if scenario is None else scenario.status,
                "override_hash": None if scenario is None else scenario.override_hash,
                "summary": _metric_summary(output),
                "validation_messages": output.get("validation_messages") or [],
            }
        )

    record_audit(
        session,
        context=context,
        action="SCENARIO_COMPARISON_EXECUTED",
        entity_type="ProjectVersion",
        entity_id=version.id,
        metadata={
            "policy_version_id": policy.id,
            "valuation_policy_version_id": valuation_policy.id,
            "scenario_ids": scenario_ids,
            "include_base": include_base,
            "result_hash": sha256_json(rows),
        },
    )
    return {
        "project_version_id": version.id,
        "policy_pack_version_id": policy.id,
        "valuation_policy_pack_version_id": valuation_policy.id,
        "rows": rows,
        "result_hash": sha256_json(rows),
    }


def _normalize_negotiation_partnership(project: dict[str, Any], row: dict[str, Any]) -> None:
    partnership = deepcopy(project.get("partnership") or {})
    method = str(row["method"])
    share = Decimal(str(row.get("share_rate") or "0"))
    upfront = Decimal(str(row.get("upfront_amount") or "0"))
    if share < 0 or share > 1:
        raise ConflictError("NEGOTIATION_SHARE_OUT_OF_RANGE", "Negotiation share_rate must be between 0 and 1.")
    if upfront < 0:
        raise ConflictError("NEGOTIATION_UPFRONT_NEGATIVE", "Negotiation upfront_amount cannot be negative.")
    partnership["method"] = method
    partnership["share_rate"] = str(share)
    partnership["manual_share"] = str(share)
    partnership["approved_selection"] = "FIXED_CONSIDERATION" if method == "UPFRONT" else "MANUAL"
    partnership["hybrid_variable_basis"] = str(row.get("hybrid_variable_basis") or "GROSS_SALES")
    partnership.setdefault("net_deduction_treatment", "CUMULATIVE_CARRY_FORWARD")
    partnership["upfront_payments"] = []
    if method in {"UPFRONT", "HYBRID"} and upfront > 0:
        partnership["upfront_payments"] = [
            {
                "date": project.get("valuation_date"),
                "amount": str(upfront),
                "label": str(row.get("label") or "Negotiated upfront land consideration"),
            }
        ]
    if method == "MINIMUM_GUARANTEE":
        partnership["manual_amount"] = str(upfront)
        studio = project.setdefault("landowner_studio", {})
        studio["minimum_guarantee_amount"] = str(upfront)
        studio["minimum_guarantee_underlying_method"] = str(
            row.get("hybrid_variable_basis") or "GROSS_SALES"
        )
        studio["minimum_guarantee_underlying_share"] = str(share)
    project["partnership"] = partnership


def analyze_negotiation_rows(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    version = get_project_version(session, context, project_version_id)
    policy = require_operational_policy(
        get_policy_version(session, context, policy_pack_version_id),
        edition="DEVELOPER",
        expected_type="PROJECT",
    )
    valuation_policy = require_operational_policy(
        get_policy_version(session, context, valuation_policy_pack_version_id),
        edition="DEVELOPER",
        expected_type="VALUATION",
    )
    scenario = get_scenario(session, context, scenario_id) if scenario_id else None
    if scenario is not None and scenario.project_version_id != version.id:
        raise NotFoundError("Scenario does not belong to the selected project version.")
    base_project = _base_project_snapshot(version, scenario)
    results: list[dict[str, Any]] = []
    for raw_row in rows:
        row = deepcopy(raw_row)
        project = deepcopy(base_project)
        _normalize_negotiation_partnership(project, row)
        envelope = compose_calculation_envelope(
            project_snapshot=project,
            policy_snapshot=policy.policy_snapshot,
            valuation_policy_snapshot=valuation_policy.policy_snapshot,
            case_id=f"NEGOTIATION-{row['row_id']}",
            description=f"Negotiation analysis: {row['label']}",
        )
        output = execute_calculation_envelope(envelope, optimize_share=False, include_solver=False)
        approved = output.get("approved_case") or {}
        results.append(
            {
                "row_id": row["row_id"],
                "label": row["label"],
                "method": row["method"],
                "requested_share_rate": row.get("share_rate"),
                "requested_upfront_amount": row.get("upfront_amount"),
                "summary": _metric_summary(output),
                "constraints": approved.get("constraints") or [],
                "finance_constraints": (output.get("finance_analysis") or {}).get("constraints") or [],
                "validation_messages": output.get("validation_messages") or [],
            }
        )

    record_audit(
        session,
        context=context,
        action="NEGOTIATION_ANALYSIS_EXECUTED",
        entity_type="ProjectVersion",
        entity_id=version.id,
        metadata={
            "policy_version_id": policy.id,
            "valuation_policy_version_id": valuation_policy.id,
            "scenario_id": scenario_id,
            "row_count": len(rows),
            "result_hash": sha256_json(results),
        },
    )
    return {
        "project_version_id": version.id,
        "policy_pack_version_id": policy.id,
        "valuation_policy_pack_version_id": valuation_policy.id,
        "scenario_id": scenario_id,
        "rows": results,
        "result_hash": sha256_json(results),
    }
