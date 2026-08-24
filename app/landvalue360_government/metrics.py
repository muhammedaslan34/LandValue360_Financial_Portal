"""Strictly separated project, developer and public-authority metrics.

The module deliberately preserves zero values, publishes the precise cash-flow
basis behind each metric, and distinguishes a policy-adjusted public value from
market value and contractual consideration.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .hashing import sha256_json
from .manifest import METRIC_DICTIONARY_VERSION

ZERO = Decimal("0")
ONE = Decimal("1")


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(+value, "f")


def _first_present(*values: Any, default: Any = None) -> Any:
    """Return the first value that is not missing while retaining numeric zero."""

    for value in values:
        if value not in (None, ""):
            return value
    return default


def _bounded(value: Decimal, low: Decimal = ZERO, high: Decimal = ONE) -> Decimal:
    return max(low, min(high, value))


def build_metric_snapshot(
    unified_result: dict[str, Any],
    contract_result: dict[str, Any],
    valuation_result: dict[str, Any],
    *,
    risk_score: Any = "0",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    truth = unified_result.get("financial_truth") or {}
    summary = unified_result.get("summary") or {}
    selected = unified_result.get("selected_contract") or {}
    financial_policy = (policy or {}).get("financial_constraints") or {}
    public_policy = (policy or {}).get("public_value_adjustment") or {}

    contract_npv = D(contract_result.get("contractual_consideration_npv"))
    confidence_map = {
        "HIGH": D(public_policy.get("high_confidence_factor"), "0.95"),
        "MODERATE": D(public_policy.get("moderate_confidence_factor"), "0.80"),
        "LOW": D(public_policy.get("low_confidence_factor"), "0.60"),
    }
    confidence = _bounded(
        confidence_map.get(str(valuation_result.get("confidence_grade") or "LOW").upper(), Decimal("0.60"))
    )
    risk = _bounded(D(risk_score) / Decimal("100"))
    risk_loss_share = _bounded(D(public_policy.get("maximum_risk_haircut"), "0.35"))
    policy_adjustment_factor = _bounded(confidence * (ONE - risk * risk_loss_share))
    policy_adjusted_public_npv = contract_npv * policy_adjustment_factor

    market_value_raw = (valuation_result.get("reconciliation") or {}).get("value")
    market_value = None if market_value_raw in (None, "") else D(market_value_raw)
    developer_npv = D(_first_present(truth.get("developer_equity_npv"), truth.get("developer_npv")))
    developer_irr_raw = _first_present(truth.get("developer_equity_irr"), truth.get("developer_irr"))
    developer_irr = None if developer_irr_raw in (None, "") else D(developer_irr_raw)
    required_developer_npv = D(financial_policy.get("minimum_developer_npv"), "0")
    required_developer_irr = D(financial_policy.get("minimum_developer_irr"), "0.18")

    funding_gap = D(_first_present(truth.get("funding_gap"), truth.get("peak_funding_gap")))
    available_equity = D(
        _first_present(
            truth.get("available_equity_capacity"),
            selected.get("available_equity_capacity"),
            summary.get("available_equity_capacity"),
        )
    )
    available_debt = D(
        _first_present(
            truth.get("available_debt_capacity"),
            selected.get("available_debt_capacity"),
            summary.get("available_debt_capacity"),
        )
    )
    ending_cash = D(_first_present(truth.get("ending_cash"), selected.get("ending_cash"), summary.get("ending_cash")))
    minimum_cash = D(_first_present(truth.get("minimum_cash_balance"), summary.get("minimum_cash_balance")))
    liquidity_surplus = max(ZERO, ending_cash - minimum_cash)
    funding_headroom = available_equity + available_debt + liquidity_surplus - funding_gap

    project_profit_raw = _first_present(truth.get("project_profit"), selected.get("project_profit"))
    project_profit_definition = str(
        truth.get("project_profit_definition")
        or "WHOLE_PROJECT_UNLEVERED_COLLECTIONS_LESS_ALL_PARTY_DEVELOPMENT_COSTS_BEFORE_LAND_AND_FINANCE"
    )
    if project_profit_raw in (None, ""):
        # Backward-compatible fallback is disclosed rather than silently relabelled.
        project_profit_raw = truth.get("developer_profit")
        project_profit_definition = "LEGACY_FALLBACK_DEVELOPER_PROFIT"
    project_profit = D(project_profit_raw)
    project_profit_on_revenue_raw = _first_present(truth.get("project_profit_on_revenue"))
    project_profit_on_revenue = None if project_profit_on_revenue_raw in (None, "") else D(project_profit_on_revenue_raw)

    developer_profit = D(
        _first_present(
            truth.get("developer_equity_nominal_profit"),
            truth.get("developer_profit"),
            project_profit,
        )
    )
    developer_profit_on_revenue_raw = _first_present(truth.get("developer_profit_on_revenue"))
    developer_profit_on_revenue = None if developer_profit_on_revenue_raw in (None, "") else D(developer_profit_on_revenue_raw)

    output = {
        "metric_dictionary_version": METRIC_DICTIONARY_VERSION,
        "project": {
            "unlevered_irr": truth.get("project_irr"),
            "unlevered_npv": truth.get("project_npv"),
            "profit": _fmt(project_profit),
            "profit_definition": project_profit_definition,
            "profit_on_cost": _first_present(truth.get("project_profit_on_cost"), truth.get("developer_profit_on_cost")),
            "profit_on_revenue": _fmt(project_profit_on_revenue),
            "terminal_debt": truth.get("terminal_debt"),
            "mandatory_shortfall": truth.get("mandatory_shortfall"),
            "unmodeled_scope": truth.get("unmodeled_scope"),
            "cash_reconciliation_passed": truth.get("cash_reconciliation_passed"),
            "maximum_cash_balance_variance": truth.get("maximum_cash_balance_variance"),
        },
        "developer": {
            "equity_irr": developer_irr_raw,
            "equity_npv": _first_present(truth.get("developer_equity_npv"), truth.get("developer_npv")),
            "moic": _first_present(truth.get("developer_equity_multiple"), truth.get("developer_multiple")),
            "nominal_profit": _fmt(developer_profit),
            "nominal_profit_definition": truth.get("developer_equity_profit_definition"),
            "unlevered_profit": truth.get("developer_profit"),
            "unlevered_profit_definition": truth.get("developer_profit_definition"),
            "profit_on_revenue": _fmt(developer_profit_on_revenue),
            "peak_equity": truth.get("peak_equity"),
            "peak_debt": truth.get("peak_debt"),
            "funding_gap": _fmt(funding_gap),
            "minimum_required_equity_irr": _fmt(required_developer_irr),
            "minimum_required_equity_npv": _fmt(required_developer_npv),
            "return_headroom_irr": None if developer_irr is None else _fmt(developer_irr - required_developer_irr),
            "return_headroom_npv": _fmt(developer_npv - required_developer_npv),
            "available_equity_capacity": _fmt(available_equity),
            "available_debt_capacity": _fmt(available_debt),
            "liquidity_surplus": _fmt(liquidity_surplus),
            "funding_headroom": _fmt(funding_headroom),
            "funding_headroom_definition": "available equity + available debt + cash above minimum reserve - funding gap",
        },
        "public_authority": {
            "contractual_consideration": contract_result.get("contractual_consideration"),
            "contractual_consideration_npv": contract_result.get("contractual_consideration_npv"),
            "scheduled_contractual_consideration_npv": contract_result.get("scheduled_contractual_consideration_npv"),
            "payment_timing_npv_variance": contract_result.get("payment_timing_npv_variance"),
            "public_net_npv_after_costs": contract_result.get("public_net_npv_after_costs"),
            "public_financial_value": contract_result.get("public_financial_value"),
            "policy_adjusted_public_npv": _fmt(policy_adjusted_public_npv),
            # Compatibility alias. Reports should migrate to policy_adjusted_public_npv.
            "risk_adjusted_public_npv": _fmt(policy_adjusted_public_npv),
            "public_value_adjustment": {
                "classification": "POLICY_ADJUSTMENT_NOT_MARKET_VALUE",
                "confidence_factor": _fmt(confidence),
                "normalized_risk_score": _fmt(risk),
                "maximum_risk_haircut": _fmt(risk_loss_share),
                "combined_factor": _fmt(policy_adjustment_factor),
                "formula": "contractual_consideration_npv * confidence_factor * (1 - normalized_risk_score * maximum_risk_haircut)",
            },
            "market_value_benchmark": _fmt(market_value),
            "value_gap_to_market": None if market_value is None else _fmt(contract_npv - market_value),
            "cash_receipts": contract_result.get("cash_receipts"),
            "units_in_kind": contract_result.get("units_in_kind_value"),
            "closing_receivable": contract_result.get("closing_receivable"),
            "contingent_liabilities": (contract_result.get("public_value_layers") or {}).get("contingent_liabilities"),
            "administrative_and_audit_costs": (contract_result.get("public_value_layers") or {}).get("administrative_and_audit_costs"),
        },
    }
    output["metrics_hash"] = sha256_json(output)
    return output
