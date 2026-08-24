"""Comprehensive, audit-friendly numeric results for Landowner interface.

The results book is a presentation contract over the unified monthly engine.
It does not recalculate the underlying model; it aggregates already-produced
engine rows and publishes explicit reconciliations and value multiples.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
TOLERANCE = Decimal("0.01")


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


def fmt(value: Decimal | Any) -> str:
    return format(+D(value), "f")


def _first(*values: Any, default: Any = None) -> Any:
    """Return the first explicitly supplied value without treating numeric zero as missing."""
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _sum(rows: list[dict[str, Any]], key: str) -> Decimal:
    return sum((D(row.get(key)) for row in rows), ZERO)


def _safe_ratio(numerator: Any, denominator: Any) -> str | None:
    if denominator in (None, ""):
        return None
    bottom = D(denominator)
    if bottom == ZERO:
        return None
    return fmt(D(numerator) / bottom)


def _check(identifier: str, actual: Decimal, required: Decimal = ZERO, *, tolerance: Decimal = TOLERANCE) -> dict[str, Any]:
    variance = actual - required
    return {
        "id": identifier,
        "passed": abs(variance) <= tolerance,
        "actual": fmt(actual),
        "required": fmt(required),
        "variance": fmt(variance),
        "tolerance": fmt(tolerance),
    }


def build_results_book(
    project: dict[str, Any],
    unified: dict[str, Any],
    valuation: dict[str, Any],
    contract: dict[str, Any],
    metrics: dict[str, Any],
    decision_levels: dict[str, Any],
) -> dict[str, Any]:
    """Build a single numerical results surface from the authoritative engine.

    Every monetary value is returned as a decimal string.  The caller is
    responsible only for formatting; no UI layer should recompute these totals.
    """

    planning = project.get("planning") or {}
    summary = unified.get("summary") or {}
    truth = unified.get("financial_truth") or {}
    product_rows = list(unified.get("products") or [])
    cost_rows = list(unified.get("cost_items") or [])
    monthly = list(unified.get("monthly_cashflow") or [])
    distribution_ledger = list(unified.get("distribution_ledger") or unified.get("annual_distributions") or [])
    valuation_reconciliation = (valuation.get("reconciliation") or {})
    public_metrics = (metrics.get("public_authority") or {})
    developer_metrics = (metrics.get("developer") or {})
    project_metrics = (metrics.get("project") or {})

    gross_land = D(planning.get("gross_land_area_sqm"))
    excluded_land = D(planning.get("excluded_land_area_sqm"))
    net_land = gross_land - excluded_land
    total_gfa = sum((D(row.get("gfa_sqm")) for row in product_rows), ZERO)
    sellable = sum((D(row.get("sellable_sqm")) for row in product_rows), ZERO)

    land_uses: list[dict[str, Any]] = []
    for row in planning.get("land_uses") or []:
        share = D(row.get("share"))
        land_uses.append(
            {
                "id": row.get("land_use_id"),
                "name": row.get("name"),
                "share": fmt(share),
                "share_percent": fmt(share * ONE_HUNDRED),
                "area_sqm": fmt(gross_land * share),
                "area_basis": "GROSS_LAND",
            }
        )

    products: list[dict[str, Any]] = []
    for row in product_rows:
        products.append(
            {
                "product_id": row.get("product_id"),
                "name": row.get("name"),
                "gfa_sqm": fmt(row.get("gfa_sqm")),
                "sellable_sqm": fmt(row.get("sellable_sqm")),
                "efficiency": _safe_ratio(row.get("sellable_sqm"), row.get("gfa_sqm")),
                "unit_price": fmt(row.get("unit_price")),
                "gross_potential_revenue": fmt(row.get("gross_potential_revenue")),
                "gross_sales": fmt(row.get("gross_sales")),
                "net_sales": fmt(row.get("net_sales")),
                "construction_cost": fmt(row.get("construction_cost")),
                "developer_cost": fmt(row.get("developer_construction_cost")),
                "government_cost": fmt(row.get("government_construction_contribution")),
                "third_party_cost": fmt(row.get("third_party_construction_contribution")),
            }
        )

    categories: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"gross": ZERO, "developer": ZERO, "government": ZERO, "third_party": ZERO,
                 "developer_economic": ZERO, "public_economic": ZERO}
    )
    costs: list[dict[str, Any]] = []
    for row in cost_rows:
        gross = D(row.get("gross_total"))
        developer = D(row.get("total"))
        government = D(row.get("government_total"))
        third_party = D(row.get("third_party_total"))
        developer_economic_share = D(row.get("developer_economic_share"), str(developer / gross if gross else ZERO))
        developer_economic = gross * developer_economic_share
        public_economic = gross - developer_economic
        category = str(row.get("category") or "OTHER")
        categories[category]["gross"] += gross
        categories[category]["developer"] += developer
        categories[category]["government"] += government
        categories[category]["third_party"] += third_party
        categories[category]["developer_economic"] += developer_economic
        categories[category]["public_economic"] += public_economic
        costs.append(
            {
                "cost_id": row.get("cost_id"),
                "name": row.get("name"),
                "category": category,
                "source": row.get("source"),
                "gross_total": fmt(gross),
                "developer_total": fmt(developer),
                "government_total": fmt(government),
                "third_party_total": fmt(third_party),
                "developer_economic_total": fmt(developer_economic),
                "public_economic_total": fmt(public_economic),
                "developer_share": _safe_ratio(developer, gross),
                "government_share": _safe_ratio(government, gross),
                "third_party_share": _safe_ratio(third_party, gross),
            }
        )

    cost_categories = [
        {
            "category": category,
            "gross_total": fmt(values["gross"]),
            "developer_total": fmt(values["developer"]),
            "government_total": fmt(values["government"]),
            "third_party_total": fmt(values["third_party"]),
            "developer_economic_total": fmt(values["developer_economic"]),
            "public_economic_total": fmt(values["public_economic"]),
        }
        for category, values in sorted(categories.items())
    ]

    total_cost = _sum(cost_rows, "gross_total")
    developer_cost = _sum(cost_rows, "total")
    government_cost = _sum(cost_rows, "government_total")
    third_party_cost = _sum(cost_rows, "third_party_total")
    developer_economic_cost = sum((D(row.get("gross_total")) * D(row.get("developer_economic_share"), str(D(row.get("total")) / D(row.get("gross_total")) if D(row.get("gross_total")) else ZERO)) for row in cost_rows), ZERO)
    public_economic_cost = total_cost - developer_economic_cost

    opening_cash = D(monthly[0].get("opening_cash")) if monthly else ZERO
    incremental_equity = _sum(monthly, "equity_contribution")
    total_equity = D(
        _first(
            truth.get("developer_equity_contributions"),
            opening_cash + incremental_equity,
        )
    )
    cash = {
        "opening_cash": fmt(opening_cash),
        "sales_collections": fmt(_sum(monthly, "sales_collections")),
        "development_cost_paid": fmt(_sum(monthly, "actual_cost")),
        "public_consideration_paid": fmt(_sum(monthly, "government_payment")),
        "finance_cost_paid": fmt(_sum(monthly, "finance_cost_paid")),
        "finance_cost_accrued": fmt(_sum(monthly, "finance_cost_accrued")),
        "interest_accrued": fmt(_sum(monthly, "interest_accrued")),
        "financing_fees_accrued": fmt(_sum(monthly, "financing_fees_accrued")),
        # Presentation compatibility: these two fields reconcile to cash paid.
        "interest_paid": fmt(_sum(monthly, "finance_cost_paid")),
        "financing_fees": "0",
        "debt_draw": fmt(_sum(monthly, "financing_draw")),
        "base_date_equity_contribution": fmt(
            _first(truth.get("developer_base_date_equity_contribution"), opening_cash)
        ),
        "incremental_equity_contribution": fmt(incremental_equity),
        "equity_contribution": fmt(total_equity),
        "principal_repayment": fmt(_sum(monthly, "financing_repayment")),
        "developer_distribution": fmt(_sum(monthly, "developer_distribution")),
        "developer_advance_recovery": fmt(_sum(monthly, "developer_advance_recovery")),
        "developer_equity_receipts": fmt(
            _sum(monthly, "developer_distribution") + _sum(monthly, "developer_advance_recovery")
        ),
        "public_distribution": fmt(_sum(monthly, "landowner_distribution")),
        "landowner_cash_receipt": fmt(_sum(monthly, "landowner_cash_receipt")),
        "closing_cash": fmt(D(monthly[-1].get("ending_cash")) if monthly else ZERO),
    }
    operating_net = D(cash["sales_collections"]) - D(cash["development_cost_paid"]) - D(cash["public_consideration_paid"])
    # Opening cash is developer equity contributed at the base date.  It is
    # already represented by the opening balance and must not be counted as a
    # same-month cash inflow in the roll-forward.
    opening_equity = D(cash["base_date_equity_contribution"])
    after_financing = (
        D(cash["opening_cash"])
        + D(cash["sales_collections"])
        + D(cash["incremental_equity_contribution"])
        + D(cash["debt_draw"])
        - D(cash["development_cost_paid"])
        - D(cash["public_consideration_paid"])
        - D(cash["finance_cost_paid"])
        - D(cash["principal_repayment"])
        - D(cash["developer_distribution"])
        - D(cash["public_distribution"])
    )
    cash["opening_equity_balance"] = fmt(opening_equity)
    cash["total_developer_equity_contributed"] = fmt(total_equity)
    cash["operating_net_after_landowner_consideration"] = fmt(operating_net)
    # Backward-compatible alias; the UI no longer labels this as project operating profit.
    cash["operating_net_before_financing"] = fmt(operating_net)
    cash["calculated_closing_cash"] = fmt(after_financing)

    land_value_raw = valuation_reconciliation.get("value")
    land_value = None if land_value_raw in (None, "") else D(land_value_raw)
    public_nominal = D(_first(public_metrics.get("contractual_consideration"), contract.get("contractual_consideration")))
    public_npv = D(_first(public_metrics.get("contractual_consideration_npv"), contract.get("contractual_consideration_npv")))
    policy_adjusted_npv = D(
        _first(
            public_metrics.get("policy_adjusted_public_npv"),
            public_metrics.get("risk_adjusted_public_npv"),
        )
    )
    residual_capacity = D(decision_levels.get("residual_capacity"))
    gdv = D(_first(truth.get("gross_potential_revenue"), summary.get("gross_potential_revenue")))
    project_operating_profit = gdv - total_cost
    cash["project_operating_profit"] = fmt(project_operating_profit)

    public_cash_receipts = D(_first(public_metrics.get("cash_receipts"), contract.get("cash_receipts")))
    closing_receivable = D(_first(public_metrics.get("closing_receivable"), contract.get("closing_receivable")))
    units_in_kind = D(_first(public_metrics.get("units_in_kind"), contract.get("units_in_kind_value")))

    parties = {
        "developer": {
            "costs_cash_paid": fmt(developer_cost),
            "costs_borne": fmt(developer_economic_cost),
            "base_date_equity_contribution": cash["base_date_equity_contribution"],
            "incremental_equity_contributions": cash["incremental_equity_contribution"],
            "equity_contributions": cash["equity_contribution"],
            "public_consideration_paid": cash["public_consideration_paid"],
            "developer_distributions": cash["developer_distribution"],
            "advance_recoveries": cash["developer_advance_recovery"],
            "equity_receipts": cash["developer_equity_receipts"],
            "nominal_cash_return": cash["developer_equity_receipts"],
            "nominal_net_profit_after_equity": fmt(
                _first(
                    truth.get("developer_equity_nominal_profit"),
                    D(cash["developer_distribution"]) - total_equity,
                )
            ),
            "financing_costs_paid": cash["finance_cost_paid"],
            "equity_nominal_profit": fmt(
                _first(
                    truth.get("developer_equity_nominal_profit"),
                    D(cash["developer_distribution"]) - total_equity,
                )
            ),
            "equity_nominal_profit_definition": truth.get("developer_equity_profit_definition"),
            "unlevered_profit": fmt(truth.get("developer_profit")),
            "unlevered_profit_definition": truth.get("developer_profit_definition"),
            "npv": fmt(_first(developer_metrics.get("equity_npv"), truth.get("developer_equity_npv"), truth.get("developer_npv"))),
            "irr": fmt(_first(developer_metrics.get("equity_irr"), truth.get("developer_equity_irr"), truth.get("developer_irr"))),
            "moic": fmt(_first(developer_metrics.get("moic"), truth.get("developer_equity_multiple"), truth.get("developer_multiple"))),
            "peak_equity": fmt(_first(developer_metrics.get("peak_equity"), truth.get("peak_equity"))),
            "peak_debt": fmt(_first(developer_metrics.get("peak_debt"), truth.get("peak_debt"))),
            "funding_gap": fmt(_first(developer_metrics.get("funding_gap"), truth.get("funding_gap"))),
        },
        "public_authority": {
            "costs_cash_paid": fmt(government_cost),
            "costs_borne": fmt(public_economic_cost),
            "contractual_consideration_nominal": fmt(public_nominal),
            "contractual_consideration_npv": fmt(public_npv),
            "scheduled_contractual_consideration_npv": contract.get("scheduled_contractual_consideration_npv"),
            "payment_timing_npv_variance": contract.get("payment_timing_npv_variance"),
            "net_npv_after_public_costs": contract.get("public_net_npv_after_costs"),
            "policy_adjusted_public_npv": fmt(policy_adjusted_npv),
            "risk_adjusted_public_npv": fmt(policy_adjusted_npv),
            "public_value_adjustment": public_metrics.get("public_value_adjustment"),
            "cash_receipts": fmt(_first(truth.get("landowner_cash_receipts"), public_cash_receipts)),
            "closing_receivable": fmt(closing_receivable),
            "units_in_kind": fmt(units_in_kind),
            "net_nominal_after_public_costs": fmt(public_nominal - government_cost),
        },
        "third_party": {"costs_cash_paid": fmt(third_party_cost)},
    }

    accrued = D(_first(contract.get("nominal_contractual_accrual"), public_nominal))
    paid = D(_first(contract.get("cash_receipts"), public_cash_receipts))
    receivable = D(_first(contract.get("closing_receivable"), closing_receivable))
    reconciliation = [
        _check("COST_RESPONSIBILITY_RECONCILES", developer_cost + government_cost + third_party_cost, total_cost),
        _check("CONSIDERATION_ACCRUAL_RECONCILES", paid + receivable, accrued),
        _check("CLOSING_CASH_RECONCILES", D(cash["calculated_closing_cash"]), D(cash["closing_cash"])),
        _check(
            "MONTHLY_CASH_ROWS_RECONCILE",
            D(_first(truth.get("maximum_cash_balance_variance"), "0")),
        ),
        _check("TERMINAL_DEBT_ZERO", D(truth.get("terminal_debt"))),
        _check("DEFERRED_COST_ZERO", D(truth.get("deferred_development_cost"))),
        _check("CONTRACTUAL_ARREARS_ZERO", D(truth.get("deferred_contractual_payment"))),
        _check("FINANCE_ARREARS_ZERO", D(truth.get("deferred_finance_payment"))),
        _check("MANDATORY_SHORTFALL_ZERO", D(truth.get("mandatory_shortfall"))),
        _check("UNMODELED_SCOPE_ZERO", D(truth.get("unmodeled_scope"))),
    ]

    return {
        "results_book_version": "1.0.0",
        "currency": str((valuation.get("basis") or {}).get("currency") or project.get("reporting_currency") or "USD"),
        "land": {
            "gross_land_area_sqm": fmt(gross_land),
            "excluded_land_area_sqm": fmt(excluded_land),
            "net_land_area_sqm": fmt(net_land),
            "far": fmt(planning.get("far")),
            "bcr": fmt(planning.get("bcr")),
            "total_gfa_sqm": fmt(total_gfa),
            "sellable_area_sqm": fmt(sellable),
            "land_uses": land_uses,
            "products": products,
        },
        "sales": {
            "gross_potential_revenue": fmt(_first(truth.get("gross_potential_revenue"), summary.get("gross_potential_revenue"))),
            "gross_sales": fmt(_first(truth.get("gross_sales"), summary.get("gross_sales"))),
            "net_sales": fmt(_first(truth.get("net_sales"), summary.get("net_sales"))),
            "gross_collections": fmt(_first(truth.get("gross_collections"), summary.get("gross_collections"))),
            "net_collections": fmt(_first(truth.get("net_collections"), summary.get("net_collections"))),
            "closing_sales_receivable": fmt(max(ZERO, D(_first(truth.get("gross_sales"), summary.get("gross_sales"))) - D(_first(truth.get("gross_collections"), summary.get("gross_collections"))))),
        },
        "costs": {
            "planned_total_cost": fmt(total_cost),
            "developer_total": fmt(developer_cost),
            "government_total": fmt(government_cost),
            "third_party_total": fmt(third_party_cost),
            "developer_economic_total": fmt(developer_economic_cost),
            "public_economic_total": fmt(public_economic_cost),
            "interest_total": fmt(_first(truth.get("interest_total"), summary.get("interest_total"))),
            "financing_fees_total": fmt(_first(truth.get("financing_fees_total"), summary.get("financing_fees_total"))),
            "categories": cost_categories,
            "items": costs,
        },
        "cash_flow": cash,
        # One authoritative row per model month. The presentation may expand a
        # row, but it must not create duplicate timeline rows or recalculate it.
        "monthly_cashflow": monthly,
        "distribution_ledger": distribution_ledger,
        "parties": parties,
        "project_metrics": {
            "operating_profit_before_land_and_financing": fmt(project_operating_profit),
            "operating_profit_definition": "Gross potential sales less total planned development cost, before landowner consideration and financing.",
            "project_npv": fmt(_first(project_metrics.get("unlevered_npv"), truth.get("project_npv"))),
            "project_irr": fmt(_first(project_metrics.get("unlevered_irr"), truth.get("project_irr"))),
            "profit": fmt(_first(project_metrics.get("profit"), truth.get("project_profit"))),
            "profit_definition": _first(project_metrics.get("profit_definition"), truth.get("project_profit_definition")),
            "profit_on_cost": fmt(_first(project_metrics.get("profit_on_cost"), truth.get("project_profit_on_cost"))),
            "profit_on_revenue": fmt(project_metrics.get("profit_on_revenue")),
        },
        "value_multiples": {
            "nominal_public_consideration_to_land_value": _safe_ratio(public_nominal, land_value),
            "public_npv_to_land_value": _safe_ratio(public_npv, land_value),
            "policy_adjusted_public_npv_to_land_value": _safe_ratio(policy_adjusted_npv, land_value),
            "risk_adjusted_public_npv_to_land_value": _safe_ratio(policy_adjusted_npv, land_value),
            "residual_capacity_to_land_value": _safe_ratio(residual_capacity, land_value),
            "developer_moic": fmt(_first(developer_metrics.get("moic"), truth.get("developer_equity_multiple"), truth.get("developer_multiple"))),
            "gdv_to_total_cost": _safe_ratio(gdv, total_cost),
            "land_value": None if land_value is None else fmt(land_value),
            "land_value_benchmark": None if land_value is None else fmt(land_value),
            "market_value_available": land_value is not None,
            "market_value_verified": False,
            "benchmark_provisional": False,
            "benchmark_status": "USER_SUPPLIED_ADVISORY_REFERENCE",
            "gross_development_value": fmt(gdv),
            "total_project_cost": fmt(total_cost),
        },
        "reconciliation": {
            "passed": all(row["passed"] for row in reconciliation),
            "checks": reconciliation,
        },
        "source": {
            "financial_truth_version": truth.get("financial_truth_version"),
            "calculation_hash": unified.get("calculation_hash"),
            "ledger_hash": (unified.get("event_ledger") or {}).get("ledger_hash"),
            "display_authority": unified.get("display_authority"),
        },
    }
