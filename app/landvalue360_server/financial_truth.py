"""Canonical monthly financial truth shared by live preview, results and reports.

Platform 2.1.1 / Engine 2.1.1 stops presentation layers from mixing the retired comparison
feasibility metrics with the governed monthly landowner/finance engine.  The
legacy metrics remain available for audit reconciliation, but every user-facing
headline metric is sourced from this module.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any


from landvalue360_common.versions import FINANCIAL_TRUTH_VERSION
from .constraint_registry import enrich_constraint_rows, visible_failed_constraints


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _s(value: Any) -> str | None:
    parsed = _d(value)
    return None if parsed is None else str(parsed)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _difference(unified: Any, legacy: Any) -> str | None:
    left = _d(unified)
    right = _d(legacy)
    if left is None or right is None:
        return None
    return str(left - right)


def build_financial_truth(
    landowner: dict[str, Any],
    *,
    legacy_output: dict[str, Any] | None = None,
    invariants: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the one canonical set of headline financial metrics.

    ``landowner`` is produced by :func:`run_landowner_studio`, which uses the
    governed monthly kernel.  ``legacy_output`` is optional and is used only to
    publish a transparent reconciliation; it never overrides a headline value.
    """

    summary = landowner.get("summary") or {}
    selected = landowner.get("selected_contract") or {}
    diagnostic_ledger = deepcopy(landowner.get("funding_diagnostic_ledger") or [])
    constraints = enrich_constraint_rows(
        deepcopy(selected.get("constraints") or []),
        diagnostic_ledger,
    )
    failed = visible_failed_constraints(constraints)
    all_failed = [row for row in constraints if not bool(row.get("passed"))]

    rows = landowner.get("monthly_cashflow") or []
    base_date_equity_contribution = (
        (_d(rows[0].get("opening_cash")) or Decimal("0")) if rows else Decimal("0")
    )
    incremental_equity_contributions = sum(
        (_d(row.get("equity_contribution")) or Decimal("0") for row in rows),
        Decimal("0"),
    )
    equity_contributions = base_date_equity_contribution + incremental_equity_contributions
    developer_distributions = sum((_d(row.get("developer_distribution")) or Decimal("0") for row in rows), Decimal("0"))
    developer_advance_recoveries = sum((_d(row.get("developer_advance_recovery")) or Decimal("0") for row in rows), Decimal("0"))
    developer_equity_receipts = developer_distributions + developer_advance_recoveries
    developer_equity_nominal_profit = developer_equity_receipts - equity_contributions
    landowner_cash_receipts = sum((_d(row.get("landowner_cash_receipt")) or Decimal("0") for row in rows), Decimal("0"))
    evaluation_status = str(_first_present(selected.get("evaluation_status"), summary.get("evaluation_status"), "ECONOMICALLY_INFEASIBLE"))
    calculation_valid = bool(_first_present(selected.get("calculation_valid"), summary.get("calculation_valid"), False))
    policy_compliant = bool(_first_present(selected.get("policy_compliant"), summary.get("policy_compliant"), False))
    closure_passed = bool(_first_present(selected.get("closure_passed"), summary.get("closure_passed"), False))
    cash_reconciliation_passed = bool(_first_present(selected.get("cash_reconciliation_passed"), summary.get("cash_reconciliation_passed"), False))
    invariants = invariants or landowner.get("engine_invariants") or {}
    mandatory_invariant_failures = [
        row for row in invariants.get("checks") or []
        if bool(row.get("mandatory", True)) and row.get("passed") is False
    ]
    structural_invariant_failures = [
        row for row in mandatory_invariant_failures
        if str(row.get("invariant_id") or row.get("id") or "").upper() != "SELECTED_CONTRACT_CONSTRAINTS_PASS"
    ]
    ledger_invariants_passed = not structural_invariant_failures
    numerical_resolution_passed = calculation_valid and evaluation_status != "NUMERICALLY_UNRESOLVED"
    economic_feasible = bool(selected.get("economic_feasible", False)) and evaluation_status not in {
        "ECONOMICALLY_INFEASIBLE", "NUMERICALLY_UNRESOLVED", "CONTRACT_DEFINITION_INCOMPLETE"
    }
    calculation_status = "PASS" if numerical_resolution_passed and cash_reconciliation_passed and ledger_invariants_passed else "FAIL"
    # A numerically resolved ledger is not usable until all terminal obligations
    # and closure checks pass.  Economic infeasibility and policy compliance
    # remain separate decision states and do not masquerade as solver failures.
    result_usable = calculation_status == "PASS" and closure_passed
    evidence = landowner.get("evidence_readiness") or {}
    evidence_ready = bool(evidence.get("ready", False))
    report = landowner.get("report_readiness") or {}
    report_ready = bool(report.get("ready", False))

    truth = {
        "financial_truth_version": FINANCIAL_TRUTH_VERSION,
        "source": "LANDVALUE360_UNIFIED_MONTHLY_ENGINE",
        "single_source_financial_kernel": landowner.get("single_source_financial_kernel"),
        "model_version": landowner.get("model_version"),
        "calculation_hash": landowner.get("calculation_hash"),
        "status": calculation_status,
        "calculation_status": calculation_status,
        "result_usable": result_usable,
        "feasible": bool(result_usable and economic_feasible and policy_compliant and closure_passed and not failed),
        "evaluation_status": evaluation_status,
        "economic_feasibility": "FEASIBLE" if economic_feasible else "INFEASIBLE",
        "economic_feasible": economic_feasible,
        "financial_closure": "CLOSED" if closure_passed else "UNCLOSED",
        "calculation_valid": calculation_valid,
        "numerical_resolution_passed": numerical_resolution_passed,
        "policy_compliant": policy_compliant,
        "policy_compliance": "PASS" if policy_compliant else "FAIL",
        "closure_passed": closure_passed,
        "evidence_ready": evidence_ready,
        "evidence_readiness": "READY" if evidence_ready else (evidence.get("status") or "NOT_ASSESSED"),
        "report_ready": report_ready,
        "report_readiness": "READY" if report_ready else (report.get("status") or "SEPARATE_GATE_REQUIRED"),
        "cash_reconciliation_passed": cash_reconciliation_passed,
        "ledger_invariants_passed": ledger_invariants_passed,
        "failed_structural_invariants": [row.get("invariant_id") or row.get("id") for row in structural_invariant_failures],
        "maximum_cash_balance_variance": _s(_first_present(selected.get("maximum_cash_balance_variance"), summary.get("maximum_cash_balance_variance"))),
        "method": selected.get("method"),
        "approved_selection": selected.get("approved_selection"),
        "approved_share": _s(selected.get("measure")),
        "approved_measure_type": selected.get("measure_type"),
        "gross_potential_revenue": _s(summary.get("gross_potential_revenue")),
        "gross_sales": _s(summary.get("gross_sales")),
        "net_sales": _s(summary.get("net_sales")),
        "gross_collections": _s(summary.get("gross_collections")),
        "net_collections": _s(summary.get("net_collections")),
        # ``development_cost`` is the whole-project development cost.  Earlier
        # releases exposed the developer-borne slice under this label, while
        # the cost-entry screen showed gross cost.  That made two correct but
        # differently scoped figures look like an accounting error.
        "planned_total_cost": _s(summary.get("planned_total_cost")),
        "development_cost": _s(summary.get("planned_total_cost")),
        "developer_planned_cost": _s(summary.get("developer_planned_cost")),
        "planned_gross_project_cost": _s(selected.get("planned_gross_project_cost", summary.get("planned_total_cost"))),
        "actual_gross_project_cost": _s(selected.get("actual_gross_project_cost")),
        "project_cost_scope_shortfall": _s(selected.get("project_cost_scope_shortfall")),
        "actual_government_project_cost": _s(selected.get("actual_government_project_cost")),
        "actual_third_party_project_cost": _s(selected.get("actual_third_party_project_cost")),
        "government_cost_contribution": _s(summary.get("government_cost_contribution")),
        "third_party_cost_contribution": _s(summary.get("third_party_cost_contribution")),
        "government_consideration": _s(selected.get("government_value")),
        # Gross consideration and net public value are deliberately separate.
        # The former is the actual modeled cash receipt timing; the latter also
        # deducts the public authority's dated project-cost contribution.
        "government_consideration_npv": _s(selected.get("government_gross_npv", summary.get("government_gross_npv"))),
        "government_npv": _s(selected.get("government_npv", summary.get("government_npv"))),
        "government_net_npv_after_costs": _s(selected.get("government_net_npv_after_costs", selected.get("government_npv"))),
        "government_cost_contribution_npv": _s(selected.get("government_cost_contribution_npv")),
        "government_cost_contribution": _s(selected.get("government_cost_contribution", summary.get("government_cost_contribution"))),
        "project_profit": _s(selected.get("project_profit")),
        "project_profit_on_cost": _s(selected.get("project_profit_on_cost")),
        "project_profit_on_revenue": _s(selected.get("project_profit_on_revenue")),
        "project_profit_definition": "WHOLE_PROJECT_UNLEVERED_COLLECTIONS_LESS_ALL_PARTY_DEVELOPMENT_COSTS_BEFORE_LAND_AND_FINANCE",
        "developer_profit": _s(selected.get("developer_profit")),
        "developer_profit_on_cost": _s(selected.get("developer_profit_on_cost")),
        "developer_profit_on_revenue": _s(selected.get("developer_profit_on_revenue")),
        "developer_net_margin": _s(
            (_d(selected.get("developer_profit")) or Decimal("0")) / (_d(summary.get("net_sales")) or Decimal("1"))
            if (_d(summary.get("net_sales")) or Decimal("0")) > 0 else None
        ),
        "developer_profit_definition": "DEVELOPER_UNLEVERED_COLLECTIONS_LESS_DEVELOPER_COSTS_AND_PUBLIC_CONSIDERATION_BEFORE_FINANCE",
        "developer_unlevered_npv": _s(selected.get("developer_unlevered_npv")),
        "developer_unlevered_irr": _s(selected.get("developer_unlevered_irr")),
        "developer_base_date_equity_contribution": _s(base_date_equity_contribution),
        "developer_incremental_equity_contributions": _s(incremental_equity_contributions),
        "developer_equity_contributions": _s(equity_contributions),
        "developer_equity_distributions": _s(developer_distributions),
        "developer_advance_recoveries": _s(developer_advance_recoveries),
        "developer_equity_receipts": _s(developer_equity_receipts),
        "developer_equity_nominal_profit": _s(developer_equity_nominal_profit),
        "landowner_cash_receipts": _s(landowner_cash_receipts),
        "developer_equity_profit_definition": "EQUITY_DISTRIBUTIONS_LESS_EQUITY_CONTRIBUTIONS",
        "developer_multiple": _s(selected.get("developer_multiple")),
        "developer_equity_multiple": _s(selected.get("developer_multiple")),
        "developer_npv": _s(selected.get("developer_npv", summary.get("developer_npv"))),
        "developer_irr": _s(selected.get("developer_irr", summary.get("developer_irr"))),
        "developer_equity_npv": _s(selected.get("developer_equity_npv")),
        "developer_equity_irr": _s(selected.get("developer_equity_irr", summary.get("developer_equity_irr"))),
        "project_npv": _s(selected.get("project_npv", summary.get("project_npv"))),
        "project_irr": _s(selected.get("project_irr", summary.get("project_irr"))),
        "peak_funding_gap": _s(selected.get("peak_funding_gap", summary.get("peak_funding_gap"))),
        "funding_gap": _s(selected.get("peak_funding_gap", summary.get("peak_funding_gap"))),
        "peak_negative_cash": _s(selected.get("peak_negative_cash", summary.get("peak_negative_cash"))),
        "peak_debt": _s(selected.get("peak_debt", summary.get("peak_debt"))),
        "peak_equity": _s(selected.get("peak_equity", summary.get("peak_equity"))),
        "available_equity_capacity": _s(_first_present(selected.get("available_equity_capacity"), summary.get("available_equity_capacity"))),
        "recognized_equity_policy": deepcopy(_first_present(selected.get("recognized_equity_policy"), summary.get("recognized_equity_policy"))),
        "available_debt_capacity": _s(_first_present(selected.get("available_debt_capacity"), summary.get("available_debt_capacity"))),
        "ending_cash": _s(_first_present(selected.get("ending_cash"), summary.get("ending_cash"))),
        "minimum_cash_balance": _s(_first_present(selected.get("minimum_cash_balance"), summary.get("minimum_cash_balance"))),
        "interest_total": _s(selected.get("interest_total", summary.get("interest_total"))),
        "financing_fees_total": _s(selected.get("financing_fees_total", summary.get("financing_fees_total"))),
        "terminal_debt": _s(selected.get("terminal_debt", summary.get("terminal_debt"))),
        "deferred_development_cost": _s(selected.get("terminal_deferred_cost", summary.get("terminal_deferred_cost"))),
        "deferred_contractual_payment": _s(selected.get("terminal_contractual_arrears", summary.get("terminal_contractual_arrears"))),
        "deferred_finance_payment": _s(selected.get("terminal_finance_arrears", summary.get("terminal_finance_arrears"))),
        "mandatory_shortfall": _s(selected.get("mandatory_shortfall", summary.get("mandatory_shortfall"))),
        "unmodeled_scope": _s(selected.get("unmodeled_scope", summary.get("unmodeled_scope"))),
        "finance_mode": selected.get("finance_mode", summary.get("finance_mode")),
        "spend_policy": selected.get("spend_policy", summary.get("spend_policy")),
        "schedule_extension_months": _s(selected.get("schedule_extension_months", summary.get("schedule_extension_months"))),
        "original_completion_date": selected.get("original_completion_date", summary.get("original_completion_date")),
        "adjusted_completion_date": selected.get("adjusted_completion_date", summary.get("adjusted_completion_date")),
        "configured_horizon_months": summary.get("configured_horizon_months"),
        "required_horizon_months": summary.get("required_horizon_months"),
        "project_duration_months": summary.get("project_duration_months"),
        "constraints": constraints,
        "failed_constraints": [row.get("constraint_id") for row in failed],
        "all_failed_constraints": [row.get("constraint_id") for row in all_failed],
        "failed_constraint_details": deepcopy(failed),
        "funding_diagnostic_ledger": diagnostic_ledger,
        "terminal_funding_diagnostic": deepcopy(landowner.get("terminal_funding_diagnostic") or {}),
    }

    first_failure = next((row for row in failed if row.get("first_failed_month") not in (None, "")), None)
    if first_failure:
        truth["first_failed_month"] = first_failure.get("first_failed_month")
        truth["first_failed_date"] = first_failure.get("first_failed_date")
        truth["first_failure_amount"] = _s(first_failure.get("failure_amount"))
        truth["first_failure_components"] = deepcopy(first_failure.get("failure_components") or {})
        truth["first_failure_constraint"] = first_failure.get("constraint_id")

    # Sum only non-overlapping terminal obligations. ``mandatory_shortfall`` is
    # an aggregate diagnostic that can already contain contractual, finance,
    # and non-deferrable cost arrears; adding it here would double-count the
    # same unpaid amount and overstate the terminal balance.
    terminal_obligations = sum(
        value if value is not None else Decimal("0")
        for value in (
            _d(truth["terminal_debt"]),
            _d(truth["deferred_development_cost"]),
            _d(truth["deferred_contractual_payment"]),
            _d(truth["deferred_finance_payment"]),
            _d(truth["unmodeled_scope"]),
            _d(truth["project_cost_scope_shortfall"]),
        )
    )
    truth["terminal_unpaid_obligations"] = str(terminal_obligations)
    if abs(terminal_obligations) > Decimal("0.01"):
        truth["status"] = "FAIL"
        truth["calculation_status"] = "FAIL"
        truth["result_usable"] = False
        truth["closure_passed"] = False
        truth["financial_closure"] = "UNCLOSED"
        truth["feasible"] = False

    if legacy_output is not None:
        approved = legacy_output.get("approved_case") or {}
        legacy_metrics = approved.get("metrics") or {}
        legacy_finance = (legacy_output.get("finance_analysis") or {}).get("metrics") or {}
        legacy = {
            "gross_sales": (legacy_output.get("revenue") or {}).get("gross_sales"),
            "development_cost": (legacy_output.get("costs") or {}).get("total_escalated_cost"),
            "government_consideration": legacy_metrics.get("government_cash_total"),
            "government_npv": legacy_metrics.get("government_cash_npv"),
            "developer_npv": legacy_metrics.get("developer_npv"),
            "developer_irr": legacy_metrics.get("developer_irr"),
            "funding_gap": legacy_finance.get("structured_funding_gap", legacy_metrics.get("funding_gap")),
            "terminal_debt": legacy_finance.get("terminal_debt_balance"),
        }
        truth["legacy_reconciliation"] = {
            "display_authority": "UNIFIED_MONTHLY_MODEL",
            "legacy_model_is_audit_only": True,
            "legacy_metrics": legacy,
            "differences": {
                key: _difference(truth.get(key), value)
                for key, value in legacy.items()
            },
        }
    return truth
