"""Landowner Fair Share & Monthly Cashflow Studio (Landowner 2.1.1 / Engine 2.1.1).

This module is intentionally isolated from the frozen feasibility kernel.  It
implements a deterministic monthly decision model for comparing landowner
consideration structures while preserving full traceability of sales,
collections, development expenditure, financing, public consideration,
reserves and distributions.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from math import exp, isfinite
from typing import Any, Iterable

from landvalue360_kernel.monthly_engine import run_monthly_kernel
from landvalue360_kernel.cashflow import CashFlowSeries, DatedCashFlow
from landvalue360_kernel.dates import DayCountBasis
from landvalue360_kernel.decimal_utils import DECIMAL_PRECISION, decimal_exp, decimal_power
from landvalue360_kernel.finance import xnpv
from landvalue360_kernel.planning import calculate_planning, planning_input_from_dict

from .costing import resolve_project_costs
from .json_tools import sha256_json
from .project_normalization import normalize_project_snapshot
from .valuation_policy import resolve_valuation_discount

ZERO = Decimal("0")
ONE = Decimal("1")
EPS = Decimal("0.00000001")
MONEY_TOLERANCE = Decimal("0.01")
MAX_HORIZON = 600
from landvalue360_common.versions import LANDOWNER_MODEL_VERSION as MODEL_VERSION


def D(value: Any, default: str = "0") -> Decimal:
    """Parse a numeric value without binary floating-point conversion."""

    try:
        return Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid number: {value!r}") from exc


def _required_policy_decimal(source: dict[str, Any], key: str, *, family: str) -> Decimal:
    value = source.get(key)
    if value in (None, ""):
        raise ValueError(
            f"{family} policy must explicitly define financial_constraints.{key}; "
            "no calculation fallback is permitted."
        )
    return D(value)


def B(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _rate(value: Any, *, name: str, default: str = "0") -> Decimal:
    result = D(value, default)
    if result < ZERO or result > ONE:
        raise ValueError(f"{name} must be between 0 and 1.")
    return result


def _positive_int(value: Any, *, name: str, default: int, maximum: int = MAX_HORIZON) -> int:
    try:
        result = int(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if result < 1 or result > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}.")
    return result


def _norm(weights: list[Decimal]) -> list[Decimal]:
    total = sum(weights, ZERO)
    if total <= ZERO:
        raise ValueError("Curve weights must have a positive total.")
    return [weight / total for weight in weights]


def monthly_curve(kind: str, months: int, intensity: Decimal = Decimal("1")) -> list[Decimal]:
    """Return normalized monthly weights for a supported preset curve."""

    if months < 1 or months > MAX_HORIZON:
        raise ValueError(f"Duration must be between 1 and {MAX_HORIZON} months.")
    key = str(kind or "LINEAR").upper()
    xs = [Decimal(index + 1) / Decimal(months) for index in range(months)]
    if key == "LINEAR":
        weights = [ONE] * months
    elif key == "FRONT_LOADED":
        weights = [(ONE - x + Decimal("0.05")) ** max(ONE, intensity) for x in xs]
    elif key == "BACK_LOADED":
        weights = [(x + Decimal("0.05")) ** max(ONE, intensity) for x in xs]
    elif key == "BELL":
        sigma = max(0.08, 0.22 / float(max(ONE, intensity)))
        weights = [Decimal(str(exp(-0.5 * ((float(x) - 0.5) / sigma) ** 2))) for x in xs]
    elif key in {"S_CURVE", "ACCELERATED_S_CURVE"}:
        steepness = 8.0 * float(max(Decimal("0.25"), intensity))
        if key == "ACCELERATED_S_CURVE":
            steepness *= 1.5
        cumulative = [
            Decimal(str(1 / (1 + exp(-steepness * (float(x) - 0.5)))))
            for x in [ZERO, *xs]
        ]
        weights = [max(ZERO, cumulative[index + 1] - cumulative[index]) for index in range(months)]
    elif key == "DELAYED_RAMP":
        delay = max(0, min(months - 1, int(months * 0.2)))
        weights = [ZERO] * delay + [Decimal(index + 1) for index in range(months - delay)]
    else:
        raise ValueError(f"Unsupported curve type: {kind}")
    return _norm(weights)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    # Month-end precision is unnecessary for monthly feasibility.  The first
    # day produces stable ACT/365 dates and avoids invalid calendar dates.
    return date(year, month, 1)


def _lag_months(rule: dict[str, Any]) -> int:
    """Resolve a collection lag without turning 365 days into 13 months."""

    if rule.get("lag_months") not in (None, ""):
        try:
            result = int(rule.get("lag_months"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Collection lag_months must be an integer.") from exc
    else:
        days = D(rule.get("lag_days"), "0")
        if days < ZERO:
            raise ValueError("Collection lag cannot be negative.")
        # Average Gregorian month.  This maps 365 days to 12 months and keeps
        # legacy 180/360-day commercial rules at 6/12 months.
        result = int((days / Decimal("30.4375")).to_integral_value(rounding="ROUND_HALF_UP"))
    if result < 0 or result > MAX_HORIZON:
        raise ValueError(f"Collection lag must be between 0 and {MAX_HORIZON} months.")
    return result


def _parse_date(value: Any, fallback: date) -> date:
    if value in (None, ""):
        return fallback
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid ISO date: {value!r}") from exc


def _escalate_schedule(
    schedule: list[Decimal],
    *,
    valuation_date: date,
    base_date: date,
    annual_rate: Decimal,
) -> list[Decimal]:
    if annual_rate < Decimal("-1"):
        raise ValueError("Escalation rate must be greater than -100%.")
    if annual_rate == ZERO:
        return list(schedule)
    result: list[Decimal] = []
    base = ONE + annual_rate
    for index, amount in enumerate(schedule):
        payment_date = _add_months(valuation_date, index)
        months = (payment_date.year - base_date.year) * 12 + payment_date.month - base_date.month
        years = Decimal(max(months, 0)) / Decimal("12")
        result.append(amount * decimal_power(base, years))
    return result


def _split_responsibility(
    schedule: list[Decimal],
    *,
    developer_share: Decimal,
    government_share: Decimal,
) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
    if developer_share < ZERO or government_share < ZERO:
        raise ValueError("Cost-responsibility shares cannot be negative.")
    if developer_share + government_share > ONE + EPS:
        raise ValueError("Developer and government cost-responsibility shares cannot exceed 100%.")
    third_party_share = max(ZERO, ONE - developer_share - government_share)
    return (
        [value * developer_share for value in schedule],
        [value * government_share for value in schedule],
        [value * third_party_share for value in schedule],
    )


def _blank(horizon: int) -> list[Decimal]:
    return [ZERO for _ in range(horizon)]


def _add_series(left: list[Decimal], right: list[Decimal]) -> list[Decimal]:
    return [a + b for a, b in zip(left, right)]


def _schedule(
    total: Decimal,
    start: int,
    duration: int,
    kind: str,
    horizon: int,
    *,
    intensity: Decimal = ONE,
) -> tuple[list[Decimal], Decimal]:
    """Schedule a total and return ``(inside_horizon, omitted_amount)``."""

    result = _blank(horizon)
    omitted = ZERO
    for index, weight in enumerate(monthly_curve(kind, duration, intensity)):
        position = max(0, start - 1) + index
        amount = total * weight
        if position < horizon:
            result[position] += amount
        else:
            omitted += amount
    return result, omitted


def _collection_schedule(
    contracted: list[Decimal],
    rules: Iterable[dict[str, Any]],
    horizon: int,
) -> tuple[list[Decimal], Decimal, int]:
    normalized: list[tuple[int, Decimal]] = []
    for rule in rules:
        lag = _lag_months(rule)
        weight = D(rule.get("weight"))
        if weight < ZERO or weight > ONE:
            raise ValueError("Every collection-rule weight must be between 0% and 100%.")
        normalized.append((lag, weight))
    if not normalized:
        normalized = [(0, ONE)]
    total_weight = sum((weight for _, weight in normalized), ZERO)
    if abs(total_weight - ONE) > EPS:
        raise ValueError("Collection-rule weights must sum to 100%.")

    result = _blank(horizon)
    omitted = ZERO
    max_lag = 0
    for sale_month, amount in enumerate(contracted):
        if amount == ZERO:
            continue
        for lag, weight in normalized:
            max_lag = max(max_lag, lag)
            position = sale_month + lag
            collection = amount * weight
            if position < horizon:
                result[position] += collection
            else:
                omitted += collection
    return result, omitted, max_lag


def _required_horizon(project: dict[str, Any], studio: dict[str, Any]) -> int:
    required = 1
    for product in project.get("products") or []:
        sales_start = _positive_int(product.get("sales_start_month"), name="sales_start_month", default=1)
        sales_duration = _positive_int(product.get("sales_duration_months"), name="sales_duration_months", default=36)
        lag = 0
        for rule in product.get("collection_rules") or []:
            lag = max(lag, _lag_months(rule))
        construction_start = _positive_int(product.get("construction_start_month"), name="construction_start_month", default=1)
        construction_duration = _positive_int(product.get("construction_duration_months"), name="construction_duration_months", default=30)
        required = max(
            required,
            sales_start - 1 + sales_duration + lag,
            construction_start - 1 + construction_duration,
        )
    for cost in project.get("costs") or []:
        start = _positive_int(
            cost.get("monthly_start_month", studio.get("other_cost_start_month")),
            name="cost monthly_start_month",
            default=1,
        )
        duration = _positive_int(
            cost.get("monthly_duration_months", studio.get("other_cost_duration_months")),
            name="cost monthly_duration_months",
            default=36,
        )
        required = max(required, start - 1 + duration)
    for payment in studio.get("upfront_payments") or []:
        required = max(required, _positive_int(payment.get("month"), name="upfront payment month", default=1))
    required = max(required, _positive_int(studio.get("upfront_payment_month"), name="upfront payment month", default=1))
    return min(MAX_HORIZON, required)


def _product_rows(project: dict[str, Any], horizon: int) -> tuple[list[dict[str, Any]], dict[str, list[Decimal]], dict[str, Decimal]]:
    product_cost_mode = str(project.get("construction_cost_entry_mode") or "PRODUCT").upper() == "PRODUCT"
    planning_payload = deepcopy(project.get("planning") or {})
    planning_payload["products"] = deepcopy(project.get("planning_products") or [])
    planning_result = calculate_planning(planning_input_from_dict(planning_payload))
    planning_map = {item.product_id: item for item in planning_result.products}

    valuation_date = _parse_date(project.get("valuation_date"), date.today())
    totals = {
        "gross_contracted_sales": _blank(horizon),
        "net_contracted_sales": _blank(horizon),
        "gross_collections": _blank(horizon),
        "net_collections": _blank(horizon),
        "eligible_revenue_deductions": _blank(horizon),
        "eligible_profit_revenue": _blank(horizon),
    }
    omitted = {
        "contracted_sales": ZERO,
        "collections": ZERO,
        "product_cost": ZERO,
    }
    rows: list[dict[str, Any]] = []

    product_ids = [str(item.get("product_id") or "").strip() for item in project.get("products") or []]
    if not product_ids or any(not item for item in product_ids):
        raise ValueError("At least one product is required and every product requires a product_id.")
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("Product IDs must be unique.")

    for product in project.get("products") or []:
        product_id = str(product.get("product_id") or "").strip()
        planning_product = planning_map.get(product_id)
        if planning_product is None:
            raise ValueError(f"Product {product_id!r} has no matching planning allocation.")
        product_gfa = planning_product.gfa_sqm
        sellable = planning_product.sellable_area_sqm
        unit_price = D(product.get("unit_price"))
        if min(product_gfa, sellable, unit_price) < ZERO:
            raise ValueError(f"Product {product_id!r} contains a negative area or price.")

        discount_rate = _rate(product.get("commercial_discount_rate"), name="commercial_discount_rate")
        incentive_rate = _rate(product.get("buyer_incentive_rate"), name="buyer_incentive_rate")
        refund_rate = _rate(product.get("refund_rate"), name="refund_rate")
        discount_amount = D(product.get("commercial_discount_amount"))
        incentive_amount = D(product.get("buyer_incentive_amount"))
        refund_amount = D(product.get("refund_amount"))
        if min(discount_amount, incentive_amount, refund_amount) < ZERO:
            raise ValueError(f"Discount, incentive and refund amounts cannot be negative for {product_id}.")
        gross_potential = sellable * unit_price
        commercial_discounts = gross_potential * discount_rate + discount_amount
        gross_sales = gross_potential - commercial_discounts
        if gross_sales < ZERO:
            raise ValueError(f"Commercial discounts exceed gross potential revenue for {product_id}.")
        incentives = gross_sales * incentive_rate + incentive_amount
        refunds = gross_sales * refund_rate + refund_amount
        net_sales = gross_sales - incentives - refunds
        if net_sales < ZERO:
            raise ValueError(f"Incentives and refunds exceed gross sales for {product_id}.")

        sales_start = _positive_int(product.get("sales_start_month"), name="sales_start_month", default=1)
        sales_duration = _positive_int(product.get("sales_duration_months"), name="sales_duration_months", default=36)
        sales_kind = str(product.get("sales_curve_type") or "S_CURVE")
        intensity = D(product.get("sales_curve_intensity"), "1")
        gross_contracted, gross_omitted = _schedule(gross_sales, sales_start, sales_duration, sales_kind, horizon, intensity=intensity)
        net_contracted, net_omitted = _schedule(net_sales, sales_start, sales_duration, sales_kind, horizon, intensity=intensity)
        incentive_contracted, incentive_omitted = _schedule(incentives, sales_start, sales_duration, sales_kind, horizon, intensity=intensity)
        refund_contracted, refund_omitted = _schedule(refunds, sales_start, sales_duration, sales_kind, horizon, intensity=intensity)
        rules = product.get("collection_rules") or [{"lag_months": 0, "weight": "1"}]
        gross_collections, gross_collection_omitted, max_lag = _collection_schedule(gross_contracted, rules, horizon)
        net_collections, net_collection_omitted, _ = _collection_schedule(net_contracted, rules, horizon)
        incentive_collections, incentive_collection_omitted, _ = _collection_schedule(incentive_contracted, rules, horizon)
        refund_collections, refund_collection_omitted, _ = _collection_schedule(refund_contracted, rules, horizon)

        eligible_incentive_fraction = _rate(
            product.get("buyer_incentive_net_sales_deduction_fraction"),
            name="buyer_incentive_net_sales_deduction_fraction",
            default="1",
        )
        eligible_refund_fraction = _rate(
            product.get("refund_net_sales_deduction_fraction"),
            name="refund_net_sales_deduction_fraction",
            default="1",
        )
        eligible_profit_fraction = _rate(
            product.get("eligible_profit_share_revenue_fraction"),
            name="eligible_profit_share_revenue_fraction",
            default="1",
        )
        eligible_revenue_deductions = [
            incentive_collections[index] * eligible_incentive_fraction
            + refund_collections[index] * eligible_refund_fraction
            for index in range(horizon)
        ]
        eligible_profit_revenue = [amount * eligible_profit_fraction for amount in net_collections]

        cost_rate = D(product.get("construction_cost_per_sqm")) if product_cost_mode else ZERO
        if cost_rate < ZERO:
            raise ValueError(f"Construction cost rate cannot be negative for {product_id}.")
        base_construction_cost = product_gfa * cost_rate
        contingency = _rate(product.get("construction_contingency_rate"), name=f"{product_id}.construction_contingency_rate")
        construction_start = _positive_int(product.get("construction_start_month"), name="construction_start_month", default=1)
        construction_duration = _positive_int(product.get("construction_duration_months"), name="construction_duration_months", default=30)
        construction_kind = str(product.get("construction_curve_type") or "BELL")
        construction_schedule, construction_omitted = _schedule(
            base_construction_cost * (ONE + contingency),
            construction_start,
            construction_duration,
            construction_kind,
            horizon,
            intensity=D(product.get("construction_curve_intensity"), "1"),
        )
        escalation_rate = D(product.get("construction_escalation_rate"), "0")
        construction_schedule = _escalate_schedule(
            construction_schedule,
            valuation_date=valuation_date,
            base_date=_parse_date(product.get("construction_cost_base_date"), valuation_date),
            annual_rate=escalation_rate,
        )
        developer_share = _rate(
            product.get("construction_developer_responsibility_share"),
            name=f"{product_id}.construction_developer_responsibility_share",
            default="1",
        )
        government_share = _rate(
            product.get("construction_government_responsibility_share"),
            name=f"{product_id}.construction_government_responsibility_share",
            default="0",
        )
        developer_economic_share = _rate(
            product.get("construction_developer_economic_share", product.get("developer_economic_share")),
            name=f"{product_id}.construction_developer_economic_share",
            default=str(developer_share),
        )
        government_economic_share = _rate(
            product.get("construction_government_economic_share"),
            name=f"{product_id}.construction_government_economic_share",
            default=str(max(ZERO, ONE - developer_economic_share)),
        )
        developer_schedule, government_schedule, third_party_schedule = _split_responsibility(
            construction_schedule,
            developer_share=developer_share,
            government_share=government_share,
        )
        developer_economic_schedule, government_economic_schedule, third_party_economic_schedule = _split_responsibility(
            construction_schedule,
            developer_share=developer_economic_share,
            government_share=government_economic_share,
        )
        developer_recoverable_schedule = [
            max(ZERO, developer_schedule[index] - developer_economic_schedule[index])
            for index in range(horizon)
        ]

        for key, series in (
            ("gross_contracted_sales", gross_contracted),
            ("net_contracted_sales", net_contracted),
            ("gross_collections", gross_collections),
            ("net_collections", net_collections),
            ("eligible_revenue_deductions", eligible_revenue_deductions),
            ("eligible_profit_revenue", eligible_profit_revenue),
        ):
            totals[key] = _add_series(totals[key], series)

        omitted["contracted_sales"] += gross_omitted + net_omitted + incentive_omitted + refund_omitted
        omitted["collections"] += gross_collection_omitted + net_collection_omitted + incentive_collection_omitted + refund_collection_omitted
        omitted["product_cost"] += construction_omitted
        rows.append(
            {
                "product_id": product_id,
                "name": product.get("name") or product_id,
                "gfa_sqm": product_gfa,
                "sellable_sqm": sellable,
                "unit_price": unit_price,
                "gross_potential_revenue": gross_potential,
                "commercial_discounts": commercial_discounts,
                "gross_sales": gross_sales,
                "buyer_incentives": incentives,
                "refunds": refunds,
                "net_sales": net_sales,
                "construction_cost_per_sqm": cost_rate,
                "construction_cost": sum(construction_schedule, ZERO),
                "developer_construction_cost": sum(developer_schedule, ZERO),
                "government_construction_contribution": sum(government_schedule, ZERO),
                "third_party_construction_contribution": sum(third_party_schedule, ZERO),
                "developer_economic_construction_cost": sum(developer_economic_schedule, ZERO),
                "government_economic_construction_cost": sum(government_economic_schedule, ZERO),
                "developer_recoverable_construction_cost": sum(developer_recoverable_schedule, ZERO),
                "construction_escalation_rate": escalation_rate,
                "sales_curve_type": sales_kind,
                "sales_start_month": sales_start,
                "sales_duration_months": sales_duration,
                "collection_lag_months": max_lag,
                "construction_curve_type": construction_kind,
                "construction_start_month": construction_start,
                "construction_duration_months": construction_duration,
                "gross_construction_schedule": construction_schedule,
                "construction_schedule": developer_schedule,
                "government_construction_schedule": government_schedule,
                "third_party_construction_schedule": third_party_schedule,
                "developer_economic_construction_schedule": developer_economic_schedule,
                "government_economic_construction_schedule": government_economic_schedule,
                "third_party_economic_construction_schedule": third_party_economic_schedule,
                "developer_recoverable_construction_schedule": developer_recoverable_schedule,
                "construction_deferrable": B(product.get("construction_deferrable"), True),
                "construction_priority": int(product.get("construction_priority") or 50),
                "eligible_net_sales_deduction_fraction": _rate(
                    product.get("construction_net_sales_deduction_fraction"),
                    name="construction_net_sales_deduction_fraction",
                    default="0",
                ),
                "eligible_net_sales_deduction_cap": product.get("eligible_net_sales_deduction_cap"),
                "eligible_profit_share_cost_fraction": _rate(
                    product.get("construction_profit_share_cost_fraction"),
                    name="construction_profit_share_cost_fraction",
                    default="1",
                ),
                "net_sales_deduction_treatment": product.get("net_sales_deduction_treatment", "NOT_DEDUCTIBLE"),
                "net_sales_deduction_basis": product.get("net_sales_deduction_basis", "PAID"),
                "net_sales_deduction_category": product.get("net_sales_deduction_category", "building_construction"),
                "net_sales_deduction_contract_rule": product.get("net_sales_deduction_contract_rule", ""),
                "net_sales_deduction_approval_required": B(product.get("net_sales_deduction_approval_required")),
                "net_sales_deduction_approval_obtained": B(product.get("net_sales_deduction_approval_obtained")),
                "net_sales_deduction_evidence_required": B(product.get("net_sales_deduction_evidence_required")),
                "net_sales_deduction_evidence_status": product.get("net_sales_deduction_evidence_status", "NOT_REQUIRED"),
                "net_sales_deduction_related_party": B(product.get("net_sales_deduction_related_party")),
                "net_sales_deduction_market_test_required": B(product.get("net_sales_deduction_market_test_required")),
                "net_sales_deduction_market_test_passed": B(product.get("net_sales_deduction_market_test_passed")),
                "net_sales_deduction_public_borne_authorized": B(product.get("net_sales_deduction_public_borne_authorized")),
                "cash_payer": product.get("cash_payer", "DEVELOPER"),
                "economic_bearer": product.get("economic_bearer", "DEVELOPER"),
                "developer_economic_share": developer_economic_share,
                "government_economic_share": government_economic_share,
                "reimbursable": B(product.get("reimbursable")),
                "developer_advances_landowner_share": B(product.get("developer_advances_landowner_share")),
                "advance_recovery_method": str(product.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper(),
                "handover_collection_rule_indexes": [
                    index
                    for index, rule in enumerate(rules)
                    if B(rule.get("depends_on_completion"), index == len(rules) - 1 and _lag_months(rule) > 0)
                ],
            }
        )
    return rows, totals, omitted

def _legacy_product_cost_covered(cost: dict[str, Any], products: list[dict[str, Any]]) -> bool:
    if B(cost.get("covered_by_product_construction"), False):
        return True
    # Backward-compatible inference for the v0.8.0 seeded project only.  Other
    # DIRECT_CONSTRUCTION rows remain included, avoiding the prior category-wide
    # deletion bug.
    return (
        str(cost.get("cost_id") or "").upper() in {"STRUCTURE", "MEP", "FITOUT"}
        and any(row["construction_cost"] > ZERO for row in products)
    )


def _default_cost_priority(category: str) -> tuple[bool, int]:
    key = category.upper()
    if key in {"TAX", "AUTHORITIES", "STATUTORY", "DEBT_SERVICE"}:
        return False, 10
    if key in {"PUBLIC_FACILITIES", "INFRASTRUCTURE"}:
        return True, 30
    if key == "DIRECT_CONSTRUCTION":
        return True, 50
    if key in {"PROFESSIONAL_FEES", "PROJECT_MANAGEMENT"}:
        return True, 60
    if key in {"SALES_MARKETING", "ADMINISTRATION"}:
        return True, 80
    return True, 70


def _cost_items(
    project: dict[str, Any],
    products: list[dict[str, Any]],
    studio: dict[str, Any],
    horizon: int,
) -> tuple[list[dict[str, Any]], dict[str, Decimal]]:
    resolved_project, resolution = resolve_project_costs(project)
    resolved_map = {str(item.get("cost_id")): item for item in resolved_project.get("costs") or []}
    source_map = {str(item.get("cost_id")): item for item in project.get("costs") or []}
    items: list[dict[str, Any]] = []
    omitted = {"developer_cost": ZERO, "government_cost": ZERO, "third_party_cost": ZERO}
    valuation_date = _parse_date(project.get("valuation_date"), date.today())

    product_cost_mode = str(project.get("construction_cost_entry_mode") or "PRODUCT").upper() == "PRODUCT"
    for product in products if product_cost_mode else []:
        items.append(
            {
                "cost_id": f"PRODUCT:{product['product_id']}",
                "name": f"{product['name']} construction",
                "category": "PRODUCT_CONSTRUCTION",
                "gross_total": product["construction_cost"],
                "total": product["developer_construction_cost"],
                "government_total": product["government_construction_contribution"],
                "third_party_total": product["third_party_construction_contribution"],
                "developer_economic_total": product["developer_economic_construction_cost"],
                "government_economic_total": product["government_economic_construction_cost"],
                "developer_recoverable_total": product["developer_recoverable_construction_cost"],
                "gross_schedule": product["gross_construction_schedule"],
                "schedule": product["construction_schedule"],
                "government_schedule": product["government_construction_schedule"],
                "third_party_schedule": product["third_party_construction_schedule"],
                "developer_economic_schedule": product["developer_economic_construction_schedule"],
                "government_economic_schedule": product["government_economic_construction_schedule"],
                "developer_recoverable_schedule": product["developer_recoverable_construction_schedule"],
                "deferrable": product["construction_deferrable"],
                "priority": product["construction_priority"],
                "eligible_net_sales_deduction_fraction": product["eligible_net_sales_deduction_fraction"],
                "eligible_net_sales_deduction_requested_fraction": product["eligible_net_sales_deduction_fraction"],
                "eligible_net_sales_deduction_cap": product.get("eligible_net_sales_deduction_cap"),
                "eligible_profit_share_cost_fraction": product["eligible_profit_share_cost_fraction"],
                "net_sales_deduction_treatment": product.get("net_sales_deduction_treatment", "NOT_DEDUCTIBLE"),
                "net_sales_deduction_basis": product.get("net_sales_deduction_basis", "PAID"),
                "net_sales_deduction_category": product.get("net_sales_deduction_category", "building_construction"),
                "net_sales_deduction_contract_rule": product.get("net_sales_deduction_contract_rule", ""),
                "net_sales_deduction_approval_required": B(product.get("net_sales_deduction_approval_required")),
                "net_sales_deduction_approval_obtained": B(product.get("net_sales_deduction_approval_obtained")),
                "net_sales_deduction_evidence_required": B(product.get("net_sales_deduction_evidence_required")),
                "net_sales_deduction_evidence_status": product.get("net_sales_deduction_evidence_status", "NOT_REQUIRED"),
                "net_sales_deduction_related_party": B(product.get("net_sales_deduction_related_party")),
                "net_sales_deduction_market_test_required": B(product.get("net_sales_deduction_market_test_required")),
                "net_sales_deduction_market_test_passed": B(product.get("net_sales_deduction_market_test_passed")),
                "net_sales_deduction_public_borne_authorized": B(product.get("net_sales_deduction_public_borne_authorized")),
                "cash_payer": product.get("cash_payer", "DEVELOPER"),
                "economic_bearer": product.get("economic_bearer", "DEVELOPER"),
                "developer_economic_share": product.get("developer_economic_share", ONE),
                "reimbursable": B(product.get("reimbursable")),
                "developer_advances_landowner_share": B(product.get("developer_advances_landowner_share")),
                "advance_recovery_method": str(product.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper(),
                "source": "PRODUCT_RATE",
                "product_id": product["product_id"],
            }
        )

    seen: set[str] = set()
    for cost_id, resolved in resolved_map.items():
        # Product construction rows are already represented above using the
        # product-level monthly schedules.  The resolved project also contains
        # generated PRODUCT-CONSTRUCTION rows for kernel compatibility; adding
        # them here would double-count construction.  Every uncovered source
        # cost (infrastructure, public facilities, permits, professional fees,
        # management, marketing, etc.) must still enter the advisory ledger.
        if product_cost_mode and str(cost_id).startswith("PRODUCT-CONSTRUCTION-"):
            continue
        if cost_id in seen:
            raise ValueError(f"Duplicate cost_id: {cost_id}")
        seen.add(cost_id)
        source = source_map.get(cost_id)
        if source is None:
            # Defensive fallback for a governed resolver-generated non-product
            # row.  Preserve its resolved fields rather than silently dropping
            # it, while generated product rows remain excluded above.
            source = deepcopy(resolved)
        if _legacy_product_cost_covered(source, products):
            continue
        quantity = D(resolved.get("quantity"))
        unit_cost = D(resolved.get("unit_cost"))
        contingency = _rate(resolved.get("contingency_rate"), name=f"{cost_id}.contingency_rate")
        if quantity < ZERO or unit_cost < ZERO:
            raise ValueError(f"Cost quantity and unit rate cannot be negative for {cost_id}.")
        base_total = quantity * unit_cost * (ONE + contingency)
        start = _positive_int(
            source.get("monthly_start_month", studio.get("other_cost_start_month")),
            name=f"{cost_id} monthly_start_month",
            default=1,
        )
        duration = _positive_int(
            source.get("monthly_duration_months", studio.get("other_cost_duration_months")),
            name=f"{cost_id} monthly_duration_months",
            default=36,
        )
        kind = str(source.get("monthly_curve_type") or studio.get("other_cost_curve_type") or "S_CURVE")
        gross_schedule, item_omitted = _schedule(
            base_total,
            start,
            duration,
            kind,
            horizon,
            intensity=D(source.get("monthly_curve_intensity"), "1"),
        )
        escalation_rate = D(
            source.get("escalation_rate", resolved.get("escalation_rate", "0")),
            "0",
        )
        gross_schedule = _escalate_schedule(
            gross_schedule,
            valuation_date=valuation_date,
            base_date=_parse_date(source.get("base_date", resolved.get("base_date")), valuation_date),
            annual_rate=escalation_rate,
        )
        developer_share = _rate(
            source.get("developer_responsibility_share"),
            name=f"{cost_id}.developer_responsibility_share",
            default="1",
        )
        government_share = _rate(
            source.get("government_responsibility_share"),
            name=f"{cost_id}.government_responsibility_share",
            default="0",
        )
        developer_economic_share = _rate(
            source.get("developer_economic_share"),
            name=f"{cost_id}.developer_economic_share",
            default=str(developer_share),
        )
        government_economic_share = _rate(
            source.get("government_economic_share"),
            name=f"{cost_id}.government_economic_share",
            default=str(max(ZERO, ONE - developer_economic_share)),
        )
        developer_schedule, government_schedule, third_party_schedule = _split_responsibility(
            gross_schedule,
            developer_share=developer_share,
            government_share=government_share,
        )
        developer_economic_schedule, government_economic_schedule, third_party_economic_schedule = _split_responsibility(
            gross_schedule,
            developer_share=developer_economic_share,
            government_share=government_economic_share,
        )
        developer_recoverable_schedule = [
            max(ZERO, developer_schedule[index] - developer_economic_schedule[index])
            for index in range(horizon)
        ]
        omitted["developer_cost"] += item_omitted * developer_share
        omitted["government_cost"] += item_omitted * government_share
        omitted["third_party_cost"] += item_omitted * max(ZERO, ONE - developer_share - government_share)
        default_deferrable, default_priority = _default_cost_priority(str(source.get("category") or ""))
        calc_method = str(
            (
                next((r for r in resolution.get("items", []) if str(r.get("cost_id")) == cost_id), {})
                or {}
            ).get("calculation_method")
            or source.get("calculation_method")
            or "LEGACY_QUANTITY_X_RATE"
        )
        items.append(
            {
                "cost_id": cost_id,
                "name": source.get("name") or cost_id,
                "category": source.get("category") or "UNCLASSIFIED",
                "gross_total": sum(gross_schedule, ZERO),
                "total": sum(developer_schedule, ZERO),
                "government_total": sum(government_schedule, ZERO),
                "third_party_total": sum(third_party_schedule, ZERO),
                "developer_economic_total": sum(developer_economic_schedule, ZERO),
                "government_economic_total": sum(government_economic_schedule, ZERO),
                "developer_recoverable_total": sum(developer_recoverable_schedule, ZERO),
                "gross_schedule": gross_schedule,
                "schedule": developer_schedule,
                "government_schedule": government_schedule,
                "third_party_schedule": third_party_schedule,
                "developer_economic_schedule": developer_economic_schedule,
                "government_economic_schedule": government_economic_schedule,
                "developer_recoverable_schedule": developer_recoverable_schedule,
                "deferrable": B(source.get("deferrable"), default_deferrable),
                "priority": int(source.get("cash_priority") or default_priority),
                "eligible_net_sales_deduction_fraction": _rate(
                    resolved.get(
                        "eligible_net_sales_deduction_fraction",
                        source.get("eligible_net_sales_deduction_fraction"),
                    ),
                    name=f"{cost_id}.eligible_net_sales_deduction_fraction",
                ),
                "eligible_net_sales_deduction_requested_fraction": _rate(
                    source.get("eligible_net_sales_deduction_fraction"),
                    name=f"{cost_id}.eligible_net_sales_deduction_requested_fraction",
                ),
                "eligible_net_sales_deduction_cap": source.get("eligible_net_sales_deduction_cap"),
                "eligible_profit_share_cost_fraction": _rate(
                    source.get("eligible_profit_share_cost_fraction"),
                    name=f"{cost_id}.eligible_profit_share_cost_fraction",
                    default=str(developer_share),
                ),
                "developer_responsibility_share": developer_share,
                "government_responsibility_share": government_share,
                "third_party_responsibility_share": max(ZERO, ONE - developer_share - government_share),
                "escalation_rate": escalation_rate,
                "net_sales_deduction_treatment": source.get("net_sales_deduction_treatment", "NOT_DEDUCTIBLE"),
                "net_sales_deduction_basis": source.get("net_sales_deduction_basis", "PAID"),
                "net_sales_deduction_category": source.get("net_sales_deduction_category", source.get("category") or "project_cost"),
                "net_sales_deduction_contract_rule": source.get("net_sales_deduction_contract_rule", ""),
                "net_sales_deduction_approval_required": B(source.get("net_sales_deduction_approval_required")),
                "net_sales_deduction_approval_obtained": B(source.get("net_sales_deduction_approval_obtained")),
                "net_sales_deduction_evidence_required": B(source.get("net_sales_deduction_evidence_required")),
                "net_sales_deduction_evidence_status": source.get("net_sales_deduction_evidence_status", "NOT_REQUIRED"),
                "net_sales_deduction_related_party": B(source.get("net_sales_deduction_related_party")),
                "net_sales_deduction_market_test_required": B(source.get("net_sales_deduction_market_test_required")),
                "net_sales_deduction_market_test_passed": B(source.get("net_sales_deduction_market_test_passed")),
                "net_sales_deduction_public_borne_authorized": B(source.get("net_sales_deduction_public_borne_authorized")),
                "cash_payer": source.get("cash_payer", "DEVELOPER"),
                "economic_bearer": source.get("economic_bearer", "DEVELOPER"),
                "developer_economic_share": developer_economic_share,
                "government_economic_share": government_economic_share,
                "reimbursable": B(source.get("reimbursable")),
                "developer_advances_landowner_share": B(source.get("developer_advances_landowner_share")),
                "advance_recovery_method": str(source.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper(),
                "source": calc_method,
            }
        )
    return items, omitted

def _eligible_item_cost_schedule(item: dict[str, Any], horizon: int, key: str) -> list[Decimal]:
    """Return one governed contract-eligible cost schedule.

    Monetary caps are aggregate contract limits.  The cost resolver calculates
    them against the strict kernel expenditure curve, while the advisory studio
    uses its own month-indexed curve.  Applying only the resolver's effective
    fraction can therefore overshoot a cap by a small timing/escalation
    difference.  Re-scaling the advisory schedule to the exact aggregate cap
    preserves its monthly shape and makes the contractual cap exact.
    """

    if key == "eligible_net_sales_deduction_fraction":
        series = item.get("gross_schedule") or item.get("schedule") or _blank(horizon)
        requested_fraction = D(
            item.get(
                "eligible_net_sales_deduction_requested_fraction",
                item.get(key),
            )
        )
        raw = [D(series[index]) * requested_fraction for index in range(horizon)]
        cap_raw = item.get("eligible_net_sales_deduction_cap")
        if cap_raw not in (None, ""):
            cap = max(ZERO, D(cap_raw))
            raw_total = sum(raw, ZERO)
            if raw_total > cap and raw_total > ZERO:
                scale = cap / raw_total
                raw = [amount * scale for amount in raw]
        return raw

    fraction = D(item.get(key))
    series = item.get("schedule") or _blank(horizon)
    return [D(series[index]) * fraction for index in range(horizon)]


def _eligible_cost_schedule(items: list[dict[str, Any]], horizon: int, key: str) -> list[Decimal]:
    """Return the contract-eligible monthly cost schedule.

    Net-sales deductions are defined against the governed whole-item cost,
    not merely the developer-funded share.  The cap resolver also derives its
    effective fraction from the gross escalated item total, so using the gross
    schedule here is required for exact aggregate reconciliation.  Profit-share
    eligibility retains the developer execution schedule because that base is
    tied to developer-borne costs in the current advisory contract model.

    ``PAID`` and ``ACCRUED`` both follow the modelled dated expenditure curve
    in this release.  Cash-driven deferral is disclosed as a limitation rather
    than silently claiming that the pre-contract deduction base is an audited
    actual-payment ledger.
    """
    result = _blank(horizon)
    for item in items:
        schedule = _eligible_item_cost_schedule(item, horizon, key)
        result = [result[index] + schedule[index] for index in range(horizon)]
    return result


def _net_sales_base(
    gross_collections: list[Decimal],
    deductions: list[Decimal],
    *,
    carry_forward: bool,
) -> tuple[list[Decimal], Decimal]:
    base = _blank(len(gross_collections))
    balance = ZERO
    for index, gross in enumerate(gross_collections):
        if carry_forward:
            balance += deductions[index]
            used = min(max(ZERO, gross), balance)
            base[index] = max(ZERO, gross - used)
            balance -= used
        else:
            base[index] = max(ZERO, gross - deductions[index])
    return base, balance


def _profit_base(revenue: list[Decimal], costs: list[Decimal]) -> tuple[list[Decimal], Decimal]:
    base = _blank(len(revenue))
    loss = ZERO
    for index in range(len(revenue)):
        period_profit = revenue[index] - costs[index]
        if period_profit < ZERO:
            loss += abs(period_profit)
            continue
        offset = min(period_profit, loss)
        loss -= offset
        base[index] = period_profit - offset
    return base, loss


def _explicit_upfront_schedule(
    studio: dict[str, Any],
    horizon: int,
    *,
    amount: Decimal,
    hybrid: bool,
) -> tuple[list[Decimal], Decimal]:
    result = _blank(horizon)
    omitted = ZERO
    payments = studio.get("hybrid_upfront_payments" if hybrid else "upfront_payments") or []
    if payments:
        total_weight = sum((D(item.get("weight")) for item in payments if item.get("weight") not in (None, "")), ZERO)
        use_weights = total_weight > ZERO
        for item in payments:
            month = _positive_int(item.get("month"), name="upfront payment month", default=1)
            payment_amount = amount * D(item.get("weight")) / total_weight if use_weights else D(item.get("amount"))
            if month <= horizon:
                result[month - 1] += payment_amount
            else:
                omitted += payment_amount
        return result, omitted
    month_key = "hybrid_upfront_payment_month" if hybrid else "upfront_payment_month"
    month = _positive_int(studio.get(month_key), name=month_key, default=1)
    if month <= horizon:
        result[month - 1] = amount
    else:
        omitted = amount
    return result, omitted


def _is_amount_method(method: str) -> bool:
    return str(method or "").upper() in {"UPFRONT", "MINIMUM_GUARANTEE"}


def _minimum_guarantee_schedule(
    studio: dict[str, Any],
    horizon: int,
    *,
    guarantee_amount: Decimal,
    underlying_basis: list[Decimal],
) -> list[Decimal]:
    """Return top-up-only consideration for a cumulative minimum guarantee."""

    underlying_rate = _rate(
        studio.get("minimum_guarantee_underlying_share"),
        name="minimum_guarantee_underlying_share",
        default="0",
    )
    underlying = [value * underlying_rate for value in underlying_basis]
    active_guarantee = _blank(horizon)
    schedule = studio.get("minimum_guarantee_schedule") or []
    if schedule:
        cumulative = ZERO
        for row in sorted(schedule, key=lambda item: int(item.get("month") or 1)):
            month = _positive_int(row.get("month"), name="minimum guarantee month", default=horizon)
            if row.get("cumulative_amount") not in (None, ""):
                cumulative = max(cumulative, D(row.get("cumulative_amount")))
            else:
                cumulative += max(ZERO, D(row.get("amount")))
            if month <= horizon:
                for index in range(month - 1, horizon):
                    active_guarantee[index] = max(active_guarantee[index], cumulative)
    else:
        due_month = _positive_int(
            studio.get("minimum_guarantee_payment_month"),
            name="minimum_guarantee_payment_month",
            default=horizon,
        )
        if due_month <= horizon:
            for index in range(due_month - 1, horizon):
                active_guarantee[index] = guarantee_amount
    payments = _blank(horizon)
    cumulative_underlying = ZERO
    cumulative_entitlement = ZERO
    for index in range(horizon):
        cumulative_underlying += underlying[index]
        required_cumulative = max(cumulative_underlying, active_guarantee[index])
        payments[index] = max(required_cumulative - cumulative_entitlement, ZERO)
        cumulative_entitlement += payments[index]
    return payments


def _government_schedule(
    method: str,
    rate_or_amount: Decimal,
    *,
    studio: dict[str, Any],
    horizon: int,
    gross_collections: list[Decimal],
    net_sales_base: list[Decimal],
    profit_base: list[Decimal],
) -> tuple[list[Decimal], list[Decimal], Decimal, str]:
    key = method.upper()
    if key == "GROSS_SALES":
        return [value * rate_or_amount for value in gross_collections], gross_collections, ZERO, "Eligible gross cash collections"
    if key == "NET_SALES":
        return [value * rate_or_amount for value in net_sales_base], net_sales_base, ZERO, "Gross collections after contractually allowed deductions"
    if key == "PROFIT_SHARE":
        return [value * rate_or_amount for value in profit_base], profit_base, ZERO, "Positive distributable cash profit after loss carry-forward"
    if key == "UPFRONT":
        schedule, omitted = _explicit_upfront_schedule(studio, horizon, amount=rate_or_amount, hybrid=False)
        return schedule, _blank(horizon), omitted, "Explicit upfront/deferred land payment amount"
    if key == "HYBRID":
        fixed_amount = D(studio.get("hybrid_upfront_amount"))
        fixed, omitted = _explicit_upfront_schedule(studio, horizon, amount=fixed_amount, hybrid=True)
        variable_basis = str(studio.get("hybrid_variable_basis") or "GROSS_SALES").upper()
        if variable_basis == "NET_SALES":
            basis = net_sales_base
        elif variable_basis == "PROFIT_SHARE":
            basis = profit_base
        else:
            variable_basis = "GROSS_SALES"
            basis = gross_collections
        return [fixed[index] + basis[index] * rate_or_amount for index in range(horizon)], basis, omitted, f"Fixed amount plus {variable_basis} variable share"
    if key == "MINIMUM_GUARANTEE":
        underlying_method = str(studio.get("minimum_guarantee_underlying_method") or "GROSS_SALES").upper()
        if underlying_method == "NET_SALES":
            basis = net_sales_base
        elif underlying_method == "PROFIT_SHARE":
            basis = profit_base
        else:
            underlying_method = "GROSS_SALES"
            basis = gross_collections
        schedule = _minimum_guarantee_schedule(
            studio,
            horizon,
            guarantee_amount=max(ZERO, rate_or_amount),
            underlying_basis=basis,
        )
        return schedule, basis, ZERO, f"Cumulative {underlying_method} entitlement with top-up-only minimum guarantee"
    raise ValueError(f"Unsupported contract method: {method}")



# Cash-flow execution is intentionally delegated exclusively to
# landvalue360_kernel.monthly_engine.  The former local simulator was removed
# in v0.15.1 to prevent a second calculation path from diverging from the
# governed monthly ledger.

def _dated_npv(values: list[Decimal], *, valuation_date: date, annual_rate: Decimal) -> Decimal:
    if annual_rate <= Decimal("-1"):
        raise ValueError("Discount rate must be greater than -100%.")
    series = CashFlowSeries.from_iterable(
        "landowner-studio-npv",
        "USD",
        (
            DatedCashFlow(_add_months(valuation_date, index), value, "monthly-cashflow")
            for index, value in enumerate(values)
            if value != ZERO
        ),
    )
    return xnpv(annual_rate, series, valuation_date=valuation_date, basis=DayCountBasis.ACT_365F)


def _dated_irr(values: list[Decimal], *, valuation_date: date) -> tuple[Decimal | None, str]:
    """Return a deterministic, Decimal-verified XIRR for solver trials.

    The fair-consideration solver evaluates many candidate contracts.  Calling
    the exhaustive high-precision multi-root scanner for every trial made a
    normal project take minutes.  This implementation keeps the same ACT/365F
    economics while separating the work into two stages:

    * bounded IEEE-754 arithmetic is used only to locate sign-changing roots;
    * the unique candidate is refined and residual-checked with Decimal math.

    Money never enters the model as binary floating point, and the reported
    rate is a Decimal.  Multiple roots remain an explicit ``AMBIGUOUS`` result.
    """

    points: list[tuple[Decimal, Decimal]] = []
    float_points: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        if value == ZERO:
            continue
        current = _add_months(valuation_date, index)
        years = Decimal((current - valuation_date).days) / Decimal("365")
        points.append((years, value))
        float_points.append((float(years), float(value)))

    if not points:
        return None, "NOT_CALCULABLE"
    if not any(amount < ZERO for _, amount in points) or not any(amount > ZERO for _, amount in points):
        return None, "NOT_CALCULABLE"

    scale_float = max(abs(amount) for _, amount in float_points) or 1.0
    zero_tolerance = max(1e-9, scale_float * 1e-13)

    def float_npv_log(log_rate: float) -> float:
        total = 0.0
        for years, amount in float_points:
            exponent = -log_rate * years
            if exponent > 700:
                term = float("inf") if amount >= 0 else float("-inf")
            elif exponent < -745:
                term = 0.0
            else:
                term = amount * exp(exponent)
            total += term
            if not isfinite(total):
                return total
        return total

    scan_min = -8.0
    scan_max = 8.0
    scan_step = 0.10
    scan_count = int(round((scan_max - scan_min) / scan_step))
    samples: list[tuple[float, float]] = []
    for index in range(scan_count + 1):
        point = scan_max if index == scan_count else scan_min + index * scan_step
        samples.append((point, float_npv_log(point)))

    brackets: list[tuple[float, float]] = []
    exact_candidates: list[float] = []
    for (left_x, left_y), (right_x, right_y) in zip(samples, samples[1:]):
        if isfinite(left_y) and abs(left_y) <= zero_tolerance:
            exact_candidates.append(left_x)
        if isfinite(right_y) and abs(right_y) <= zero_tolerance:
            exact_candidates.append(right_x)
        if not isfinite(left_y) or not isfinite(right_y):
            continue
        if abs(left_y) <= zero_tolerance or abs(right_y) <= zero_tolerance:
            continue
        if (left_y < 0 < right_y) or (right_y < 0 < left_y):
            brackets.append((left_x, right_x))

    float_roots: list[float] = []
    for candidate in exact_candidates:
        if all(abs(candidate - existing) > 1e-9 for existing in float_roots):
            float_roots.append(candidate)

    for left, right in brackets:
        left_value = float_npv_log(left)
        right_value = float_npv_log(right)
        if not isfinite(left_value) or not isfinite(right_value) or left_value * right_value > 0:
            continue
        for _ in range(90):
            midpoint = (left + right) / 2.0
            mid_value = float_npv_log(midpoint)
            if not isfinite(mid_value):
                break
            if abs(mid_value) <= zero_tolerance or abs(right - left) <= 1e-14:
                left = right = midpoint
                break
            if left_value * mid_value <= 0:
                right = midpoint
                right_value = mid_value
            else:
                left = midpoint
                left_value = mid_value
        candidate = (left + right) / 2.0
        if all(abs(candidate - existing) > 1e-9 for existing in float_roots):
            float_roots.append(candidate)
        if len(float_roots) >= 2:
            return None, "AMBIGUOUS"

    if not float_roots:
        return None, "NOT_CALCULABLE"
    if len(float_roots) > 1:
        return None, "AMBIGUOUS"

    log_rate = Decimal(str(float_roots[0]))
    scale_decimal = max(abs(amount) for _, amount in points)
    value_tolerance = max(Decimal("0.000001"), scale_decimal * Decimal("1e-12"))

    # A small number of high-precision Newton refinements is sufficient after
    # the float stage has already located the root to approximately 1e-14.
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for _ in range(8):
            value = ZERO
            derivative = ZERO
            for years, amount in points:
                factor = decimal_exp(-log_rate * years)
                discounted = amount * factor
                value += discounted
                derivative -= years * discounted
            if abs(value) <= value_tolerance or derivative == ZERO:
                break
            step = value / derivative
            if abs(step) > Decimal("0.25"):
                step = Decimal("0.25") if step > ZERO else Decimal("-0.25")
            log_rate -= step
            if abs(step) <= Decimal("1e-14"):
                break

        rate = decimal_exp(log_rate) - ONE

    if rate <= Decimal("-1"):
        return None, "NOT_CALCULABLE"
    return +rate, "VALID"


def _series_metrics(
    monthly_cashflow: list[Decimal],
    *,
    valuation_date: date,
    annual_discount_rate: Decimal,
    currency: str,
) -> tuple[Decimal, Decimal | None, str]:
    del currency
    return (
        _dated_npv(monthly_cashflow, valuation_date=valuation_date, annual_rate=annual_discount_rate),
        *_dated_irr(monthly_cashflow, valuation_date=valuation_date),
    )


def _government_npv(
    monthly_payments: list[Decimal],
    *,
    valuation_date: date,
    annual_discount_rate: Decimal,
    currency: str,
) -> Decimal:
    del currency
    return _dated_npv(monthly_payments, valuation_date=valuation_date, annual_rate=annual_discount_rate)

def _constraint(
    constraint_id: str,
    label: str,
    actual: Decimal | None,
    operator: str,
    threshold: Decimal,
    *,
    severity: str = "CRITICAL",
    reason: str,
) -> dict[str, Any]:
    if actual is None:
        passed = False
    elif operator == ">=":
        passed = actual >= threshold
    elif operator == "<=":
        passed = actual <= threshold
    elif operator == "==":
        passed = abs(actual - threshold) <= EPS
    else:
        raise ValueError(f"Unsupported constraint operator: {operator}")
    return {
        "constraint_id": constraint_id,
        "label": label,
        "actual": None if actual is None else str(actual),
        "operator": operator,
        "threshold": str(threshold),
        "passed": passed,
        "severity": severity,
        "reason": reason,
    }


def _recognized_equity(
    *,
    project: dict[str, Any],
    policy: dict[str, Any],
    cost_items: list[dict[str, Any]],
) -> tuple[Decimal, dict[str, Any]]:
    funding = project.get("funding") or {}
    funding_policy = policy.get("funding_policy") or {}
    project_mode = str(funding.get("equity_commitment_mode") or "").upper()
    raw_policy_mode = funding_policy.get("equity_commitment_mode")
    if not project_mode and raw_policy_mode in (None, ""):
        raise ValueError(
            "Project policy must explicitly define funding_policy.equity_commitment_mode."
        )
    policy_mode = str(raw_policy_mode or project_mode).upper()
    if project_mode == "POLICY_SCREENING":
        mode = "FIXED_PERCENT"
    else:
        mode = policy_mode if not project_mode else project_mode
    direct_categories = {
        "PRODUCT_CONSTRUCTION",
        "DIRECT_CONSTRUCTION",
        "INFRASTRUCTURE",
        "PUBLIC_FACILITIES",
    }
    direct_cost = sum(
        (item["total"] for item in cost_items if str(item.get("category") or "").upper() in direct_categories),
        ZERO,
    )
    opening_cash = D(funding.get("opening_cash", (project.get("landowner_studio") or {}).get("initial_cash")))
    if mode in {"FIXED", "FIXED_10_PERCENT", "FIXED_PERCENT"}:
        raw_fixed_share = funding_policy.get("fixed_equity_direct_cost_share")
        if raw_fixed_share in (None, ""):
            raise ValueError(
                "Project policy must explicitly define funding_policy.fixed_equity_direct_cost_share "
                "when policy-based equity is selected."
            )
        fixed_share = _rate(
            raw_fixed_share,
            name="fixed_equity_direct_cost_share",
            default=str(raw_fixed_share),
        )
        total_capacity = direct_cost * fixed_share
        amount = max(ZERO, total_capacity - opening_cash)
        source = "POLICY_FIXED_PERCENT"
    elif mode in {"DECLARED_COMMITMENT", "MANUAL", "MANUAL_COMMITMENT"}:
        declared = D(funding.get("committed_additional_equity", funding.get("committed_equity")))
        amount = declared
        total_capacity = opening_cash + declared
        fixed_share = None
        source = "PROJECT_DECLARED_COMMITMENT"
    else:
        raise ValueError(f"Unsupported equity commitment mode: {mode}.")
    if amount < ZERO:
        raise ValueError("Committed equity cannot be negative.")
    return amount, {
        "mode": mode,
        "source": source,
        # Display total recognized capacity while returning only the additional
        # draw capacity to the monthly simulator. This prevents opening cash
        # from being counted twice and keeps the disclosure intuitive.
        "recognized_equity": total_capacity,
        "recognized_total_equity_capacity": total_capacity,
        "opening_cash": opening_cash,
        "opening_cash_included": opening_cash,
        "declared_additional_equity": D(funding.get("committed_additional_equity", funding.get("committed_equity"))),
        "developer_direct_cost": direct_cost,
        "fixed_share": fixed_share,
    }


def _evaluate_contract(
    method: str,
    rate_or_amount: Decimal,
    *,
    project: dict[str, Any],
    policy: dict[str, Any],
    studio: dict[str, Any],
    horizon: int,
    planned_horizon: int,
    products: list[dict[str, Any]],
    revenue: dict[str, list[Decimal]],
    cost_items: list[dict[str, Any]],
    net_sales_base: list[Decimal],
    profit_base: list[Decimal],
    omitted_scope: dict[str, Decimal],
) -> dict[str, Any]:
    valuation_raw = project.get("valuation_date")
    valuation_date = _parse_date(valuation_raw, date.today())
    dates = [_add_months(valuation_date, index) for index in range(horizon)]
    currency = str(project.get("reporting_currency") or "USD")
    financial_policy = policy.get("financial_constraints") or {}
    finance_policy = policy.get("finance_constraints") or {}
    cashflow_basis = str(
        project.get("nominal_or_real")
        or (project.get("valuation_context") or {}).get("nominal_or_real")
        or "NOMINAL"
    ).upper()
    valuation_discount = resolve_valuation_discount(
        policy,
        project_currency=currency,
        cashflow_basis=cashflow_basis,
    )

    finance_model = deepcopy(project.get("finance_model") or {})
    # One governed switch controls both the live finance model and fair-share
    # model.  The legacy studio flags are read only as compatibility fallbacks.
    if "allow_negative_cash" not in finance_model:
        finance_model["allow_negative_cash"] = B(studio.get("allow_negative_cash"), False)
    if "spend_policy" not in finance_model:
        finance_model["spend_policy"] = (
            "SCHEDULE_DRIVEN" if B(finance_model.get("allow_negative_cash"), False) else "CASH_DRIVEN"
        )
    finance_enabled = B(finance_model.get("enabled"), True)
    funding = project.get("funding") or {}
    committed_equity, equity_policy = _recognized_equity(
        project=project,
        policy=policy,
        cost_items=cost_items,
    )
    committed_debt = (
        D(funding.get("committed_financing"))
        if finance_enabled and B(studio.get("use_committed_financing"), True)
        else ZERO
    )
    if committed_debt < ZERO:
        raise ValueError("Committed financing cannot be negative.")

    # Distribution and liquidity rules are governed by the selected project
    # policy.  Project-level values remain a compatibility fallback for older
    # snapshots, but may not silently override the published policy.
    distribution_policy = deepcopy(policy.get("distribution_policy") or {})
    legacy_distribution = studio.get("distribution_policy") or {}
    distribution_policy.setdefault("enabled", B(legacy_distribution.get("enabled", studio.get("enable_distributions")), False))
    distribution_policy.setdefault("frequency_months", int(legacy_distribution.get("frequency_months") or studio.get("distribution_frequency_months") or 12))
    distribution_policy.setdefault("first_distribution_month", int(legacy_distribution.get("first_distribution_month") or distribution_policy.get("frequency_months") or 12))
    distribution_policy.setdefault("reserve_months", int(legacy_distribution.get("reserve_months") or studio.get("distribution_reserve_months") or 12))
    distribution_policy.setdefault(
        "future_cost_reserve_share",
        legacy_distribution.get("future_cost_reserve_share", legacy_distribution.get("remaining_cost_reserve_share", studio.get("remaining_cost_reserve_share") or "0.25")),
    )
    distribution_policy.setdefault("reserve_basis", legacy_distribution.get("reserve_basis") or "ALL_REMAINING_COSTS")
    distribution_policy.setdefault("contractual_payment_timing", legacy_distribution.get("contractual_payment_timing") or "DISTRIBUTION_DATES")
    distribution_policy.setdefault("allocation_method", legacy_distribution.get("allocation_method") or "CONTRACTUAL_ACCRUAL_FIRST")
    distribution_policy.setdefault("recover_developer_advances_before_landowner_cash", True)
    distribution_policy.setdefault("settle_prior_obligations_before_distribution", True)
    distribution_policy.setdefault("prohibit_before_completion", False)
    distribution_policy.setdefault("distribution_share", "1")
    if method.upper() in {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "HYBRID"}:
        distribution_policy.setdefault("landowner_share", str(rate_or_amount))
    else:
        distribution_policy.setdefault("landowner_share", "0")

    def government_for_profit(current_profit_base: list[Decimal]) -> tuple[list[Decimal], list[Decimal], Decimal, str]:
        return _government_schedule(
            method,
            rate_or_amount,
            studio=studio,
            horizon=horizon,
            gross_collections=revenue["gross_collections"],
            net_sales_base=net_sales_base,
            profit_base=current_profit_base,
        )

    government_payments, eligible_base, government_omitted, basis_label = government_for_profit(profit_base)
    simulation: dict[str, Any] | None = None
    actual_profit_base = profit_base
    convergence_iterations = 0
    convergence_status = "NOT_REQUIRED"
    convergence_absolute_tolerance = max(
        Decimal("0.000001"),
        D(studio.get("profit_share_convergence_tolerance"), "0.01"),
    )
    convergence_relative_tolerance = max(
        Decimal("0.000000000001"),
        D(studio.get("profit_share_relative_tolerance"), "0.00000001"),
    )
    convergence_max_iterations = max(
        4,
        min(100, int(studio.get("profit_share_max_iterations") or 40)),
    )
    convergence_damping = min(
        ONE,
        max(Decimal("0.10"), D(studio.get("profit_share_damping"), "1.00")),
    )
    convergence_residual = ZERO
    convergence_relative_residual = ZERO
    convergence_history: list[dict[str, Any]] = []
    previous_residual: Decimal | None = None
    previous_signature: tuple[str, ...] | None = None
    two_iterations_ago_signature: tuple[str, ...] | None = None
    profit_share_required = (
        method.upper() == "PROFIT_SHARE"
        or (
            method.upper() == "HYBRID"
            and str(studio.get("hybrid_variable_basis") or "").upper() == "PROFIT_SHARE"
        )
        or (
            method.upper() == "MINIMUM_GUARANTEE"
            and str(studio.get("minimum_guarantee_underlying_method") or "").upper() == "PROFIT_SHARE"
        )
    )

    def simulate(payments: list[Decimal]) -> dict[str, Any]:
        return run_monthly_kernel(
            dates=dates,
            receipts=revenue["net_collections"],
            cost_items=cost_items,
            contractual_payments=payments,
            committed_equity=committed_equity,
            committed_debt=committed_debt,
            finance_model=finance_model,
            distribution_policy=distribution_policy,
            original_completion_index=max(0, planned_horizon - 1),
            initial_cash=D(funding.get("opening_cash", studio.get("initial_cash")), "0"),
        )

    def profit_schedule_from_simulation(
        model: dict[str, Any],
    ) -> tuple[list[Decimal], list[Decimal], list[Decimal], Decimal, str]:
        actual_eligible_costs = _blank(horizon)
        for item in cost_items:
            paid = model["paid_by_item"][item["cost_id"]]
            fraction = D(item.get("eligible_profit_share_cost_fraction"))
            for index, amount in enumerate(paid):
                actual_eligible_costs[index] += amount * fraction
        next_profit_base, _ = _profit_base(revenue["eligible_profit_revenue"], actual_eligible_costs)
        next_government, next_basis, next_omitted, next_label = government_for_profit(next_profit_base)
        return next_profit_base, next_government, next_basis, next_omitted, next_label

    for iteration in range(1, convergence_max_iterations + 1):
        convergence_iterations = iteration
        simulation = simulate(government_payments)
        if not profit_share_required:
            convergence_status = "NOT_REQUIRED"
            break
        next_profit_base, next_government, next_basis, next_omitted, next_label = profit_schedule_from_simulation(simulation)
        delta = max(
            (abs(next_government[index] - government_payments[index]) for index in range(horizon)),
            default=ZERO,
        )
        scale = max(
            ONE,
            max((abs(value) for value in government_payments), default=ZERO),
            max((abs(value) for value in next_government), default=ZERO),
        )
        relative_delta = delta / scale
        convergence_residual = delta
        convergence_relative_residual = relative_delta
        convergence_history.append(
            {
                "iteration": iteration,
                "maximum_absolute_residual": str(delta),
                "maximum_relative_residual": str(relative_delta),
                "damping": str(convergence_damping),
            }
        )
        actual_profit_base = next_profit_base
        eligible_base = next_basis
        government_omitted = next_omitted
        basis_label = next_label

        if delta <= convergence_absolute_tolerance or relative_delta <= convergence_relative_tolerance:
            # Re-run once with the candidate schedule.  This prevents the old
            # defect where the reported payment vector belonged to a different
            # simulation than the financial metrics.
            government_payments = next_government
            final_simulation = simulate(government_payments)
            final_profit_base, final_government, final_basis, final_omitted, final_label = profit_schedule_from_simulation(final_simulation)
            final_delta = max(
                (abs(final_government[index] - government_payments[index]) for index in range(horizon)),
                default=ZERO,
            )
            final_scale = max(
                ONE,
                max((abs(value) for value in government_payments), default=ZERO),
                max((abs(value) for value in final_government), default=ZERO),
            )
            final_relative = final_delta / final_scale
            simulation = final_simulation
            actual_profit_base = final_profit_base
            eligible_base = final_basis
            government_omitted = final_omitted
            basis_label = final_label
            convergence_residual = final_delta
            convergence_relative_residual = final_relative
            convergence_history.append(
                {
                    "iteration": f"{iteration}-verification",
                    "maximum_absolute_residual": str(final_delta),
                    "maximum_relative_residual": str(final_relative),
                    "damping": "0",
                }
            )
            if final_delta <= convergence_absolute_tolerance or final_relative <= convergence_relative_tolerance:
                convergence_status = "CONVERGED"
                break

        signature = tuple(str(value.quantize(Decimal("0.0001"))) for value in next_government)
        if (
            two_iterations_ago_signature is not None
            and signature == two_iterations_ago_signature
            and signature != previous_signature
        ):
            # A genuine two-cycle requires damping. Repeated rounded values in
            # a monotonically decreasing sequence do not.
            convergence_damping = max(Decimal("0.20"), convergence_damping / Decimal("2"))
        elif previous_residual is not None and delta > previous_residual * Decimal("1.05"):
            convergence_damping = max(Decimal("0.20"), convergence_damping / Decimal("2"))
        elif previous_residual is None or delta <= previous_residual:
            convergence_damping = min(ONE, convergence_damping + Decimal("0.10"))
        government_payments = [
            current + convergence_damping * (candidate - current)
            for current, candidate in zip(government_payments, next_government)
        ]
        previous_residual = delta
        two_iterations_ago_signature = previous_signature
        previous_signature = signature
    else:
        convergence_status = "NOT_CONVERGED"

    if profit_share_required and convergence_status != "CONVERGED":
        # The final metrics must still reconcile to the exact final schedule,
        # even though no economic recommendation may be issued.
        simulation = simulate(government_payments)
        actual_profit_base, final_candidate, eligible_base, government_omitted, basis_label = profit_schedule_from_simulation(simulation)
        convergence_residual = max(
            (abs(final_candidate[index] - government_payments[index]) for index in range(horizon)),
            default=ZERO,
        )
        convergence_relative_residual = convergence_residual / max(
            ONE,
            max((abs(value) for value in government_payments), default=ZERO),
            max((abs(value) for value in final_candidate), default=ZERO),
        )

    assert simulation is not None
    used = len(simulation["rows"])
    executed_costs = simulation["executed_costs"]
    contractual_paid = simulation["contractual_paid"]
    landowner_distributions = simulation["landowner_distributions"]

    # Reconstruct the physical whole-project cost execution from each item's
    # responsibility split. The monthly kernel controls the developer-funded
    # portion; public and third-party shares follow the same execution timing
    # when a developer share exists. Fully public/third-party items remain
    # schedule-driven because they do not consume developer liquidity.
    actual_gross_costs = _blank(used)
    actual_government_costs = _blank(used)
    actual_third_party_costs = _blank(used)
    planned_gross_cost = ZERO
    for item in cost_items:
        gross_total = D(item.get("gross_total"))
        developer_total = D(item.get("total"))
        government_total = D(item.get("government_total"))
        third_party_total = D(item.get("third_party_total"))
        planned_gross_cost += gross_total
        if gross_total <= ZERO:
            continue
        developer_share = developer_total / gross_total
        government_share = government_total / gross_total
        third_party_share = third_party_total / gross_total
        remaining_gross = gross_total
        developer_paid = (simulation.get("paid_by_item") or {}).get(item["cost_id"]) or []
        gross_schedule = item.get("gross_schedule") or []
        for index in range(used):
            if remaining_gross <= ZERO:
                break
            if developer_share > EPS:
                developer_amount = D(developer_paid[index]) if index < len(developer_paid) else ZERO
                gross_executed = developer_amount / developer_share
            else:
                gross_executed = D(gross_schedule[index]) if index < len(gross_schedule) else ZERO
            gross_executed = min(max(gross_executed, ZERO), remaining_gross)
            remaining_gross -= gross_executed
            actual_gross_costs[index] += gross_executed
            actual_government_costs[index] += gross_executed * government_share
            actual_third_party_costs[index] += gross_executed * third_party_share

    actual_gross_cost = sum(actual_gross_costs, ZERO)
    project_cost_scope_shortfall = max(planned_gross_cost - actual_gross_cost, ZERO)
    if project_cost_scope_shortfall <= Decimal("0.01"):
        project_cost_scope_shortfall = ZERO

    # Project economics exclude land consideration and financing. Developer
    # unlevered economics include the consideration transferred to the public
    # authority but still exclude debt/equity funding. Equity economics are
    # taken directly from the kernel contribution/distribution series.
    project_unlevered = [
        revenue["net_collections"][index] - actual_gross_costs[index]
        for index in range(used)
    ]
    developer_unlevered = [
        revenue["net_collections"][index]
        - executed_costs[index]
        - contractual_paid[index]
        - landowner_distributions[index]
        for index in range(used)
    ]
    project_discount = _required_policy_decimal(
        financial_policy, "discount_rate", family="Project"
    )
    government_discount = valuation_discount["effective_annual_rate"]
    project_npv, project_irr, project_irr_status = _series_metrics(
        project_unlevered,
        valuation_date=valuation_date,
        annual_discount_rate=project_discount,
        currency=currency,
    )
    developer_unlevered_npv, developer_unlevered_irr, developer_unlevered_irr_status = _series_metrics(
        developer_unlevered,
        valuation_date=valuation_date,
        annual_discount_rate=project_discount,
        currency=currency,
    )
    equity_cashflows = simulation["equity_cashflows"]
    equity_npv, equity_irr, equity_irr_status = _series_metrics(
        equity_cashflows,
        valuation_date=valuation_date,
        annual_discount_rate=project_discount,
        currency=currency,
    )
    government_gross_monthly = [
        contractual_paid[index] + simulation["landowner_distributions"][index]
        for index in range(used)
    ]
    # Government costs follow actual physical execution. Planned schedules are
    # not a valid NPV basis when developer liquidity defers the shared scope.
    government_contribution_schedule = list(actual_government_costs)
    government_net_monthly = [
        government_gross_monthly[index] - government_contribution_schedule[index]
        for index in range(used)
    ]
    government_gross_npv = _government_npv(
        government_gross_monthly,
        valuation_date=valuation_date,
        annual_discount_rate=government_discount,
        currency=currency,
    )
    government_npv = _government_npv(
        government_net_monthly,
        valuation_date=valuation_date,
        annual_discount_rate=government_discount,
        currency=currency,
    )
    government_cost_contribution_npv = _government_npv(
        government_contribution_schedule,
        valuation_date=valuation_date,
        annual_discount_rate=government_discount,
        currency=currency,
    )
    total_actual_cost = sum(executed_costs, ZERO)
    project_profit = sum(project_unlevered, ZERO)
    project_profit_on_cost = project_profit / actual_gross_cost if actual_gross_cost > ZERO else None
    project_revenue = sum(revenue["net_collections"][:used], ZERO)
    project_profit_on_revenue = project_profit / project_revenue if project_revenue > ZERO else None
    developer_profit = sum(developer_unlevered, ZERO)
    developer_cost_base = (
        total_actual_cost
        + sum(contractual_paid, ZERO)
        + sum(landowner_distributions, ZERO)
    )
    developer_profit_on_cost = (
        developer_profit / developer_cost_base if developer_cost_base > ZERO else None
    )
    developer_profit_on_revenue = (
        developer_profit / project_revenue if project_revenue > ZERO else None
    )
    equity_contributions = sum((-min(value, ZERO) for value in equity_cashflows), ZERO)
    equity_distributions = sum((max(value, ZERO) for value in equity_cashflows), ZERO)
    developer_equity_nominal_profit = equity_distributions - equity_contributions
    developer_multiple = equity_distributions / equity_contributions if equity_contributions > ZERO else None
    terminal_scope = (
        simulation["terminal_backlog"]
        + simulation["contractual_arrears"]
        + sum(omitted_scope.values(), ZERO)
        + government_omitted
        + project_cost_scope_shortfall
    )
    residual_gap = simulation["unsupported_funding_gap"] + simulation["mandatory_shortfall"]
    # Developer return constraints must be evaluated on the developer equity
    # cash flow.  Project IRR/NPV are separate unlevered project metrics and
    # must never be substituted when the equity metric is undefined.
    developer_irr = equity_irr
    developer_irr_status = equity_irr_status
    developer_npv = equity_npv

    return_constraints = [
        _constraint(
            "MIN_DEVELOPER_IRR",
            "Minimum developer equity IRR",
            developer_irr,
            ">=",
            D(financial_policy.get("minimum_developer_irr"), "0.18"),
            reason="The levered developer equity return must remain investable after landowner consideration, finance cost and repayment.",
        ),
        _constraint(
            "MIN_DEVELOPER_NPV",
            "Minimum developer equity NPV",
            developer_npv,
            ">=",
            D(financial_policy.get("minimum_developer_npv"), "0"),
            reason="The landowner structure must not destroy developer economic value at the approved discount rate.",
        ),
        _constraint(
            "MIN_PROFIT_ON_COST",
            "Minimum developer profit on cost",
            developer_profit_on_cost,
            ">=",
            D(financial_policy.get("minimum_profit_on_cost"), "0.20"),
            reason="The nominal development margin must remain above the approved policy threshold.",
        ),
        _constraint(
            "MIN_DEVELOPER_MULTIPLE",
            "Minimum developer equity multiple",
            developer_multiple,
            ">=",
            D(financial_policy.get("minimum_developer_multiple"), "1.50"),
            reason="Timing alone cannot compensate for an insufficient absolute return on committed equity.",
        ),
    ]
    closure_constraints = [
        _constraint(
            "MAX_RESIDUAL_FUNDING_GAP",
            "Maximum residual funding gap",
            residual_gap,
            "<=",
            max(D(financial_policy.get("maximum_funding_gap"), "0"), MONEY_TOLERANCE),
            reason="All required cash deficits must be covered by recognized equity or committed financing capacity.",
        ),
        _constraint(
            "COMPLETE_SCOPE",
            "Complete modeled and executed scope",
            terminal_scope,
            "<=",
            MONEY_TOLERANCE,
            reason="No material planned cost, contractual payment or out-of-horizon scope may remain unexecuted; sub-cent Decimal residuals are ignored.",
        ),
        _constraint(
            "MANDATORY_PAYMENT_SHORTFALL",
            "Mandatory payment shortfall",
            simulation["mandatory_shortfall"],
            "<=",
            MONEY_TOLERANCE,
            reason="Statutory, financing and contractual obligations cannot be silently deferred; sub-cent Decimal residuals are ignored.",
        ),
        _constraint(
            "TERMINAL_DEBT",
            "Terminal senior-debt balance",
            simulation["ending_debt"],
            "<=",
            MONEY_TOLERANCE,
            reason="The project must fully repay every recognized debt balance before financial close; sub-cent Decimal residuals are ignored.",
        ),
    ]
    numerical_constraints = [
        _constraint(
            "PROFIT_SHARE_CONVERGENCE",
            "Profit-share calculation convergence",
            ZERO if convergence_status in {"NOT_REQUIRED", "CONVERGED"} else ONE,
            "<=",
            ZERO,
            reason="Circular profit-share cash flows must converge within the approved numerical tolerance.",
        ),
        _constraint(
            "MONTHLY_CASH_RECONCILIATION",
            "Monthly sources-and-uses cash reconciliation",
            D(simulation.get("maximum_cash_balance_variance")),
            "<=",
            Decimal("0.01"),
            reason="Every monthly ledger row must reconcile opening cash and funding sources to uses and closing cash.",
        ),
    ]
    economic_constraints = [
        _constraint(
            "PROJECT_PROFIT_NONNEGATIVE",
            "Non-negative whole-project profit",
            project_profit,
            ">=",
            ZERO,
            reason="The whole project must not consume more economic resources than its net collections.",
        ),
        _constraint(
            "DEVELOPER_PROFIT_NONNEGATIVE",
            "Non-negative developer profit",
            developer_profit,
            ">=",
            ZERO,
            reason="The developer must retain a non-negative nominal economic surplus before financing.",
        ),
    ]
    constraints = [*economic_constraints, *return_constraints, *closure_constraints, *numerical_constraints]
    calculation_valid = all(item["passed"] for item in numerical_constraints)
    economically_feasible = all(item["passed"] for item in economic_constraints)
    policy_compliant = all(item["passed"] for item in return_constraints)
    closure_passed = all(item["passed"] for item in closure_constraints)
    feasible = calculation_valid and economically_feasible and policy_compliant and closure_passed
    if not calculation_valid:
        evaluation_status = "NUMERICALLY_UNRESOLVED"
    elif not closure_passed:
        evaluation_status = "FINANCIALLY_UNCLOSED"
    elif not economically_feasible:
        evaluation_status = "ECONOMICALLY_INFEASIBLE"
    elif not policy_compliant:
        evaluation_status = "POLICY_NONCOMPLIANT"
    else:
        evaluation_status = "SUPPORTED"
    return {
        "method": method.upper(),
        "measure_type": "AMOUNT" if _is_amount_method(method) else "RATE",
        "measure": rate_or_amount,
        "basis_label": basis_label,
        "eligible_base_total": sum(eligible_base[:used], ZERO),
        "government_value": sum(government_gross_monthly, ZERO),
        "valuation_discount_policy": {
            key: (str(value) if isinstance(value, Decimal) else value)
            for key, value in valuation_discount.items()
        },
        "government_gross_npv": government_gross_npv,
        "government_npv": government_npv,
        "government_net_npv_after_costs": government_npv,
        "government_cost_contribution_npv": government_cost_contribution_npv,
        "government_cost_contribution": sum(government_contribution_schedule, ZERO),
        "government_net_economic_value": sum(government_net_monthly, ZERO),
        "developer_profit": developer_profit,
        "developer_profit_on_cost": developer_profit_on_cost,
        "developer_profit_on_revenue": developer_profit_on_revenue,
        "developer_profit_definition": "Developer unlevered net collections less developer-funded development costs and all public contractual consideration, before financing.",
        "developer_unlevered_npv": developer_unlevered_npv,
        "developer_unlevered_irr": developer_unlevered_irr,
        "developer_unlevered_irr_status": developer_unlevered_irr_status,
        "project_profit": project_profit,
        "project_profit_on_cost": project_profit_on_cost,
        "project_profit_on_revenue": project_profit_on_revenue,
        "project_profit_definition": "Whole-project unlevered net collections less all-party development costs, before land consideration, financing and equity distributions.",
        "planned_gross_project_cost": planned_gross_cost,
        "actual_gross_project_cost": actual_gross_cost,
        "project_cost_scope_shortfall": project_cost_scope_shortfall,
        "actual_government_project_cost": sum(actual_government_costs, ZERO),
        "actual_third_party_project_cost": sum(actual_third_party_costs, ZERO),
        "developer_equity_contributions": equity_contributions,
        "developer_equity_distributions": equity_distributions,
        "developer_equity_nominal_profit": developer_equity_nominal_profit,
        "developer_equity_profit_definition": "Developer equity distributions less developer equity contributions, including opening cash contributed at the base date.",
        "developer_npv": developer_npv,
        "developer_irr": developer_irr,
        "developer_irr_status": developer_irr_status,
        "project_npv": project_npv,
        "project_irr": project_irr,
        "project_irr_status": project_irr_status,
        "developer_equity_npv": equity_npv,
        "developer_equity_irr": equity_irr,
        "developer_equity_irr_status": equity_irr_status,
        "developer_multiple": developer_multiple,
        "peak_funding_gap": residual_gap,
        "peak_negative_cash": simulation["peak_negative_cash"],
        "peak_debt": simulation["peak_debt"],
        "peak_equity": simulation["peak_equity"],
        "interest_total": simulation["total_interest"],
        "financing_fees_total": simulation["total_fees"],
        "terminal_debt": simulation["ending_debt"],
        "terminal_deferred_cost": simulation["terminal_backlog"],
        "terminal_contractual_arrears": simulation["contractual_arrears"],
        "terminal_finance_arrears": simulation.get("finance_arrears", ZERO),
        "unmodeled_scope": sum(omitted_scope.values(), ZERO) + government_omitted,
        "unmodeled_scope_breakdown": {**omitted_scope, "government_payment": government_omitted},
        "project_cost_scope_shortfall": project_cost_scope_shortfall,
        "mandatory_shortfall": simulation["mandatory_shortfall"],
        "funding_diagnostic_ledger": simulation.get("diagnostic_ledger") or [],
        "terminal_funding_diagnostic": simulation.get("terminal_diagnostic") or {},
        "unsupported_funding_gap_components": simulation.get("unsupported_funding_gap_components") or {},
        "mandatory_shortfall_components": simulation.get("mandatory_shortfall_components") or {},
        "schedule_extension_months": simulation["schedule_extension_months"],
        "original_completion_date": _add_months(valuation_date, max(0, planned_horizon - 1)).isoformat(),
        "adjusted_completion_date": dates[simulation["adjusted_completion_index"]].isoformat(),
        "finance_mode": simulation["config"]["finance_mode"],
        "spend_policy": simulation["config"]["spend_policy"],
        "recognized_equity_policy": equity_policy,
        "profit_share_convergence": {
            "status": convergence_status,
            "iterations": convergence_iterations,
            "absolute_tolerance": str(convergence_absolute_tolerance),
            "relative_tolerance": str(convergence_relative_tolerance),
            "maximum_absolute_residual": str(convergence_residual),
            "maximum_relative_residual": str(convergence_relative_residual),
            "history": convergence_history,
        },
        "evaluation_status": evaluation_status,
        "calculation_valid": calculation_valid,
        "economic_feasible": economically_feasible,
        "policy_compliant": policy_compliant,
        "closure_passed": closure_passed,
        "economically_feasible": economically_feasible,
        "feasible": feasible,
        "constraints": constraints,
        "government_payments": government_payments[:used],
        "government_contribution_schedule": government_contribution_schedule,
        "simulation": simulation,
        "actual_profit_base": actual_profit_base[:used],
        "profit_share_basis": {
            "definition": "Positive eligible collected cash profit after eligible paid costs and prior loss carry-forward; the contractual share is solved to the disclosed numerical tolerance.",
            "financing_costs_included": bool(studio.get("profit_share_include_financing_costs", False)),
            "capital_account_waterfall": False,
            "status": "CASH_PROFIT_SHARE_NOT_FULL_JV_WATERFALL",
        },
    }

def _solver_evaluation_view(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Return the scalar/search portion of a contract evaluation.

    A fair-share search can evaluate dozens of candidate measures.  Retaining
    every candidate's monthly rows, paid-by-item schedules and distribution
    ledger previously pushed a repeated analysis into hundreds of megabytes and
    caused later runs to stall.  Boundary search needs only the scalar metrics,
    constraints and numerical diagnostics; the selected measure is recomputed
    once with its full ledger after the range is resolved.
    """

    heavy_keys = {
        "simulation",
        "government_payments",
        "government_contribution_schedule",
        "actual_profit_base",
    }
    return {key: value for key, value in evaluation.items() if key not in heavy_keys}


def _last_feasible(
    evaluator: Any,
    low: Decimal,
    high: Decimal,
    *,
    iterations: int = 14,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    low_eval = evaluator(low)
    if not low_eval.get("calculation_valid", True):
        return None, low_eval
    if not low_eval["feasible"]:
        return None, low_eval
    high_eval = evaluator(high)
    if not high_eval.get("calculation_valid", True):
        return None, high_eval
    if high_eval["feasible"]:
        return high, high_eval
    left, right = low, high
    best = low_eval
    for _ in range(iterations):
        midpoint = (left + right) / Decimal("2")
        result = evaluator(midpoint)
        if not result.get("calculation_valid", True):
            return None, result
        if result["feasible"]:
            left = midpoint
            best = result
        else:
            right = midpoint
    return left, best


def _first_government_value(
    evaluator: Any,
    low: Decimal,
    high: Decimal,
    required_npv: Decimal,
    *,
    iterations: int = 14,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    high_result = evaluator(high)
    if not high_result.get("calculation_valid", True):
        return None, high_result
    if high_result["government_npv"] < required_npv:
        return None, None
    left, right = low, high
    for _ in range(iterations):
        midpoint = (left + right) / Decimal("2")
        result = evaluator(midpoint)
        if not result.get("calculation_valid", True):
            return None, result
        if result["government_npv"] >= required_npv:
            right = midpoint
        else:
            left = midpoint
    return right, None


def _target_boundary(
    evaluator: Any,
    low: Decimal,
    high: Decimal,
    target_irr: Decimal,
    *,
    iterations: int = 14,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    left, right = low, high
    for _ in range(iterations):
        midpoint = (left + right) / Decimal("2")
        result = evaluator(midpoint)
        if not result.get("calculation_valid", True):
            return None, result
        irr_value = result.get("developer_irr")
        if result["feasible"] and irr_value is not None and irr_value >= target_irr:
            left = midpoint
        else:
            right = midpoint
    return left, None


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    return value


def _case_summary(evaluation: dict[str, Any] | None, measure: Decimal | None) -> dict[str, Any] | None:
    if evaluation is None or measure is None:
        return None
    return {
        "measure": measure,
        "government_value": evaluation.get("government_value"),
        "government_gross_npv": evaluation.get("government_gross_npv"),
        "government_npv": evaluation.get("government_npv"),
        "government_cost_contribution": evaluation.get("government_cost_contribution"),
        "developer_irr": evaluation.get("developer_irr"),
        "developer_equity_irr": evaluation.get("developer_equity_irr"),
        "project_irr": evaluation.get("project_irr"),
        "developer_npv": evaluation.get("developer_npv"),
        "developer_multiple": evaluation.get("developer_multiple"),
        "peak_funding_gap": evaluation.get("peak_funding_gap"),
        "peak_debt": evaluation.get("peak_debt"),
        "terminal_debt": evaluation.get("terminal_debt"),
        "terminal_deferred_cost": evaluation.get("terminal_deferred_cost"),
        "schedule_extension_months": evaluation.get("schedule_extension_months"),
        "finance_mode": evaluation.get("finance_mode"),
        "spend_policy": evaluation.get("spend_policy"),
        "feasible": evaluation.get("feasible"),
        "evaluation_status": evaluation.get("evaluation_status"),
        "calculation_valid": evaluation.get("calculation_valid", True),
        "policy_compliant": evaluation.get("policy_compliant"),
        "closure_passed": evaluation.get("closure_passed"),
        "constraints": evaluation.get("constraints"),
    }


def _numeric_unresolved_result(
    method: str,
    evaluation: dict[str, Any],
    *,
    monotonic: bool | None = None,
    feasible_region_count: int = 0,
) -> dict[str, Any]:
    numerical_failures = [
        item
        for item in evaluation.get("constraints", [])
        if not item.get("passed") and item.get("constraint_id") in {
            "PROFIT_SHARE_CONVERGENCE",
            "MONTHLY_CASH_RECONCILIATION",
        }
    ]
    return {
        "method": method.upper(),
        "measure_type": "AMOUNT" if _is_amount_method(method) else "RATE",
        "status": "NUMERICALLY_UNRESOLVED",
        "fair_floor": None,
        "minimum": None,
        "recommended": None,
        "balanced": None,
        "technical_ceiling": None,
        "maximum": None,
        "failure_reasons": numerical_failures or [
            {
                "constraint_id": "NUMERICAL_RESOLUTION",
                "label": "Numerical resolution",
                "actual": evaluation.get("evaluation_status"),
                "operator": "==",
                "threshold": "RESOLVED",
                "passed": False,
                "severity": "CRITICAL",
                "reason": "No economic floor, recommendation, or ceiling is issued while the calculation remains numerically unresolved.",
            }
        ],
        "basis_label": evaluation.get("basis_label"),
        "monotonic": monotonic,
        "feasible_region_count": feasible_region_count,
        "numerical_diagnostics": evaluation.get("profit_share_convergence"),
        "_evaluation": evaluation,
    }


def _comparison_for_method(
    method: str,
    *,
    evaluator: Any,
    studio: dict[str, Any],
    policy: dict[str, Any],
    baseline_land: Decimal,
) -> dict[str, Any]:
    financial_policy = policy.get("financial_constraints") or {}
    share_policy = policy.get("share_policy") or {}
    target_irr = D(financial_policy.get("target_developer_irr"), "0.22")
    required_public_npv = max(
        D(share_policy.get("minimum_government_value_npv"), "0"),
        baseline_land * D(studio.get("land_value_recovery_share"), "1"),
    )

    if _is_amount_method(method):
        low = ZERO
        high = max(
            D(
                studio.get("minimum_guarantee_search_cap" if method.upper() == "MINIMUM_GUARANTEE" else "upfront_search_cap"),
                str(max(baseline_land * Decimal("2"), Decimal("1"))),
            ),
            baseline_land,
        )
    else:
        low = max(ZERO, D(share_policy.get("policy_minimum_share"), "0"))
        high = min(ONE, D(share_policy.get("policy_maximum_share"), "0.50"))

    # Sample the complete domain before refinement. This identifies non-
    # monotonic structures caused by waterfalls, financing limits or staged
    # payments instead of blindly assuming one binary-search interval.
    sample_count = max(8, min(32, int(studio.get("solver_grid_intervals") or 12)))
    samples: list[tuple[Decimal, dict[str, Any]]] = []
    for index in range(sample_count + 1):
        measure = low + (high - low) * Decimal(index) / Decimal(sample_count)
        samples.append((measure, evaluator(measure)))
    unresolved_samples = [
        (measure, result)
        for measure, result in samples
        if not result.get("calculation_valid", True)
    ]
    if unresolved_samples:
        # A numerical gap anywhere in the searched contractual domain can hide
        # a feasible region or create a false ceiling.  The safe response is to
        # withhold the economic range and expose the numerical diagnostics.
        return _numeric_unresolved_result(
            method,
            unresolved_samples[0][1],
            monotonic=None,
            feasible_region_count=0,
        )
    feasible_flags = [item[1]["feasible"] for item in samples]
    feasible_runs = 0
    in_run = False
    for flag in feasible_flags:
        if flag and not in_run:
            feasible_runs += 1
            in_run = True
        elif not flag:
            in_run = False
    monotonic = feasible_runs <= 1

    feasible_samples = [(measure, result) for measure, result in samples if result["feasible"]]
    if not feasible_samples:
        # Diagnose the best tested candidate instead of reporting the first grid
        # point.  This makes a no-range result actionable: users see which
        # mandatory constraints remain broken and how close the best candidate
        # came to each threshold.
        def candidate_score(item: tuple[Decimal, dict[str, Any]]) -> tuple[int, Decimal, Decimal]:
            measure, result = item
            failed_rows = [row for row in result.get("constraints", []) if not row.get("passed")]
            critical = sum(1 for row in failed_rows if str(row.get("severity") or "").upper() in {"CRITICAL", "ERROR"})
            gap = abs(D(result.get("peak_funding_gap"))) + abs(D(result.get("terminal_debt")))
            return (critical * 100 + len(failed_rows), gap, measure)

        best_measure, failed_eval = min(samples, key=candidate_score)
        failed = [item for item in failed_eval.get("constraints", []) if not item.get("passed")]
        diagnostic_cases = [
            {
                "measure": str(measure),
                "evaluation_status": result.get("evaluation_status"),
                "calculation_valid": bool(result.get("calculation_valid", True)),
                "policy_compliant": bool(result.get("policy_compliant", False)),
                "closure_passed": bool(result.get("closure_passed", False)),
                "developer_irr": result.get("developer_equity_irr", result.get("developer_irr")),
                "developer_moic": result.get("developer_multiple"),
                "public_npv": result.get("government_npv"),
                "funding_gap": result.get("peak_funding_gap"),
                "failed_constraints": [row.get("constraint_id") for row in result.get("constraints", []) if not row.get("passed")],
            }
            for measure, result in samples
        ]
        return {
            "method": method.upper(),
            "measure_type": "AMOUNT" if _is_amount_method(method) else "RATE",
            "status": "STRUCTURALLY_INFEASIBLE",
            "fair_floor": None,
            "recommended": None,
            "balanced": None,
            "technical_ceiling": None,
            "failure_reasons": failed,
            "best_candidate": _case_summary(failed_eval, best_measure),
            "diagnostics": {
                "search_low": str(low),
                "search_high": str(high),
                "tested_candidate_count": len(samples),
                "best_tested_measure": str(best_measure),
                "best_failed_constraint_ids": [row.get("constraint_id") for row in failed],
                "tested_candidates": diagnostic_cases,
            },
            "basis_label": failed_eval.get("basis_label"),
            "monotonic": monotonic,
            "feasible_region_count": feasible_runs,
            "_evaluation": failed_eval,
        }

    # Conservatively use the first connected feasible interval. This prevents
    # recommending an isolated high-share pocket caused by a non-linear rule.
    first_feasible_index = next(index for index, flag in enumerate(feasible_flags) if flag)
    last_feasible_index = first_feasible_index
    while last_feasible_index + 1 < len(feasible_flags) and feasible_flags[last_feasible_index + 1]:
        last_feasible_index += 1
    interval_low = samples[first_feasible_index][0]
    interval_high = samples[last_feasible_index][0]
    if first_feasible_index > 0:
        left = samples[first_feasible_index - 1][0]
        right = interval_low
        for _ in range(12):
            midpoint = (left + right) / Decimal("2")
            midpoint_result = evaluator(midpoint)
            if not midpoint_result.get("calculation_valid", True):
                return _numeric_unresolved_result(
                    method,
                    midpoint_result,
                    monotonic=monotonic,
                    feasible_region_count=feasible_runs,
                )
            if midpoint_result["feasible"]:
                right = midpoint
            else:
                left = midpoint
        interval_low = right
    if last_feasible_index + 1 < len(samples):
        left = interval_high
        right = samples[last_feasible_index + 1][0]
        for _ in range(12):
            midpoint = (left + right) / Decimal("2")
            midpoint_result = evaluator(midpoint)
            if not midpoint_result.get("calculation_valid", True):
                return _numeric_unresolved_result(
                    method,
                    midpoint_result,
                    monotonic=monotonic,
                    feasible_region_count=feasible_runs,
                )
            if midpoint_result["feasible"]:
                left = midpoint
            else:
                right = midpoint
        interval_high = left

    ceiling = interval_high
    ceiling_eval = evaluator(ceiling)
    if not ceiling_eval.get("calculation_valid", True):
        return _numeric_unresolved_result(
            method,
            ceiling_eval,
            monotonic=monotonic,
            feasible_region_count=feasible_runs,
        )
    floor, unresolved_floor = _first_government_value(
        evaluator,
        interval_low,
        ceiling,
        required_public_npv,
    )
    if unresolved_floor is not None:
        return _numeric_unresolved_result(
            method,
            unresolved_floor,
            monotonic=monotonic,
            feasible_region_count=feasible_runs,
        )
    if floor is None:
        return {
            "method": method.upper(),
            "measure_type": "AMOUNT" if _is_amount_method(method) else "RATE",
            "status": "PUBLIC_VALUE_FLOOR_EXCEEDS_CEILING",
            "fair_floor": None,
            "recommended": None,
            "balanced": None,
            "technical_ceiling": ceiling,
            "failure_reasons": [
                {
                    "constraint_id": "MIN_GOVERNMENT_VALUE_NPV",
                    "label": "Minimum landowner net economic value NPV",
                    "actual": str(ceiling_eval["government_npv"]),
                    "operator": ">=",
                    "threshold": str(required_public_npv),
                    "passed": False,
                    "severity": "CRITICAL",
                    "reason": "The minimum public-value requirement is above the maximum consideration the project can sustain after public cost contributions.",
                }
            ],
            "basis_label": ceiling_eval["basis_label"],
            "monotonic": monotonic,
            "feasible_region_count": feasible_runs,
            "ceiling_case": _case_summary(ceiling_eval, ceiling),
            "_evaluation": ceiling_eval,
        }

    floor_eval = evaluator(floor)
    if not floor_eval.get("calculation_valid", True):
        return _numeric_unresolved_result(
            method,
            floor_eval,
            monotonic=monotonic,
            feasible_region_count=feasible_runs,
        )
    target, unresolved_target = _target_boundary(evaluator, floor, ceiling, target_irr)
    if unresolved_target is not None or target is None:
        return _numeric_unresolved_result(
            method,
            unresolved_target or floor_eval,
            monotonic=monotonic,
            feasible_region_count=feasible_runs,
        )
    balanced = max(floor, min(ceiling, target))
    balanced_eval = evaluator(balanced)
    if not balanced_eval.get("calculation_valid", True):
        return _numeric_unresolved_result(
            method,
            balanced_eval,
            monotonic=monotonic,
            feasible_region_count=feasible_runs,
        )
    # Determine the first constraint that fails just above the ceiling.
    probe = min(high, ceiling + max((high - low) / Decimal("10000"), Decimal("0.0000001")))
    probe_eval = evaluator(probe)
    governing = (
        next(
            (item["constraint_id"] for item in probe_eval.get("constraints", []) if not item["passed"]),
            None,
        )
        if probe_eval.get("calculation_valid", True)
        else "NUMERICAL_RESOLUTION"
    )
    status = "VALID_RANGE" if monotonic else "NONCONTIGUOUS_FEASIBLE_REGION"
    return {
        "method": method.upper(),
        "measure_type": balanced_eval["measure_type"],
        "status": status,
        "fair_floor": floor,
        "minimum": floor,
        "recommended": balanced,
        "balanced": balanced,
        "technical_ceiling": ceiling,
        "maximum": ceiling,
        "required_public_npv": required_public_npv,
        "basis_label": balanced_eval["basis_label"],
        "eligible_base_total": balanced_eval["eligible_base_total"],
        "government_value": balanced_eval["government_value"],
        "government_gross_npv": balanced_eval["government_gross_npv"],
        "government_npv": balanced_eval["government_npv"],
        "government_cost_contribution": balanced_eval["government_cost_contribution"],
        "government_net_economic_value": balanced_eval["government_net_economic_value"],
        "developer_profit": balanced_eval["developer_profit"],
        "developer_profit_on_cost": balanced_eval["developer_profit_on_cost"],
        "developer_npv": balanced_eval["developer_npv"],
        "developer_irr": balanced_eval["developer_irr"],
        "developer_irr_status": balanced_eval["developer_irr_status"],
        "project_irr": balanced_eval["project_irr"],
        "developer_equity_irr": balanced_eval["developer_equity_irr"],
        "developer_multiple": balanced_eval["developer_multiple"],
        "peak_funding_gap": balanced_eval["peak_funding_gap"],
        "peak_negative_cash": balanced_eval["peak_negative_cash"],
        "peak_debt": balanced_eval["peak_debt"],
        "peak_equity": balanced_eval["peak_equity"],
        "interest_total": balanced_eval["interest_total"],
        "financing_fees_total": balanced_eval["financing_fees_total"],
        "terminal_debt": balanced_eval["terminal_debt"],
        "terminal_deferred_cost": balanced_eval["terminal_deferred_cost"],
        "terminal_contractual_arrears": balanced_eval["terminal_contractual_arrears"],
        "unmodeled_scope": balanced_eval["unmodeled_scope"],
        "unmodeled_scope_breakdown": balanced_eval["unmodeled_scope_breakdown"],
        "mandatory_shortfall": balanced_eval["mandatory_shortfall"],
        "schedule_extension_months": balanced_eval["schedule_extension_months"],
        "original_completion_date": balanced_eval["original_completion_date"],
        "adjusted_completion_date": balanced_eval["adjusted_completion_date"],
        "finance_mode": balanced_eval["finance_mode"],
        "spend_policy": balanced_eval["spend_policy"],
        "governing_constraint_id": governing,
        "monotonic": monotonic,
        "feasible_region_count": feasible_runs,
        "minimum_case": _case_summary(floor_eval, floor),
        "balanced_case": _case_summary(balanced_eval, balanced),
        "ceiling_case": _case_summary(ceiling_eval, ceiling),
        "constraints": balanced_eval["constraints"],
        "_evaluation": balanced_eval,
    }

def run_landowner_studio(
    project: dict[str, Any],
    policy: dict[str, Any] | None = None,
    *,
    selected_only: bool = False,
) -> dict[str, Any]:
    """Execute the governed Landowner 2.1.1 / Engine 2.1.1 monthly model.

    ``selected_only`` is an analytical acceleration path used after a model
    share has already been materialized.  It evaluates the exact selected
    contract through the same monthly kernel but skips re-solving all fair-
    share methods for every sensitivity or Monte Carlo iteration.
    """

    project_snapshot = normalize_project_snapshot(project)
    policy_snapshot = deepcopy(policy or {})
    financial = policy_snapshot.get("financial_constraints")
    if not isinstance(financial, dict):
        financial = {}
        policy_snapshot["financial_constraints"] = financial
    if financial.get("government_discount_rate") in (None, ""):
        raise ValueError(
            "Valuation policy must explicitly define "
            "financial_constraints.government_discount_rate; no preview fallback is permitted."
        )
    required_financial = {
        "discount_rate",
        "government_discount_rate",
        "discount_rate_type",
        "discount_currency",
        "discount_compounding",
        "minimum_developer_irr",
        "target_developer_irr",
        "minimum_profit_on_cost",
        "minimum_developer_multiple",
        "maximum_funding_gap",
    }
    missing_financial = sorted(
        key for key in required_financial if financial.get(key) in (None, "")
    )
    required_sections = {
        "funding_policy",
        "finance_constraints",
        "distribution_policy",
        "share_policy",
        "fair_consideration_policy",
        "valuation_policy",
    }
    missing_sections = sorted(
        key for key in required_sections if not isinstance(policy_snapshot.get(key), dict)
    )
    if missing_financial or missing_sections:
        missing = [
            *(f"financial_constraints.{key}" for key in missing_financial),
            *missing_sections,
        ]
        raise ValueError(
            "The governed project/valuation policy pair is incomplete: "
            + ", ".join(missing)
            + "."
        )
    studio = project_snapshot.get("landowner_studio") or {}
    configured_horizon = _positive_int(studio.get("horizon_months"), name="horizon_months", default=72)
    required_horizon = _required_horizon(project_snapshot, studio)
    auto_extend = B(studio.get("auto_extend_horizon"), True)
    # ``configured_horizon`` controls the visible modelling window, while the
    # planned completion is the last month containing a real sale, collection,
    # construction, cost, or contractual activity.  Treating an empty display
    # window as project duration previously created artificial commitment fees
    # and terminal debt.
    planned_horizon = required_horizon
    base_horizon = max(configured_horizon, required_horizon) if auto_extend else configured_horizon
    finance_model = project_snapshot.get("finance_model") or {}
    maximum_extension = int(finance_model.get("maximum_extension_months") or 120) if auto_extend else 0
    maximum_extension = max(0, min(maximum_extension, MAX_HORIZON - base_horizon))
    horizon = min(MAX_HORIZON, base_horizon + maximum_extension)

    products, revenue, product_omitted = _product_rows(project_snapshot, horizon)
    cost_items, cost_omitted = _cost_items(project_snapshot, products, studio, horizon)
    eligible_cost_deductions = _eligible_cost_schedule(
        cost_items,
        horizon,
        "eligible_net_sales_deduction_fraction",
    )
    eligible_profit_costs = _eligible_cost_schedule(
        cost_items,
        horizon,
        "eligible_profit_share_cost_fraction",
    )
    deductions = [
        revenue["eligible_revenue_deductions"][index] + eligible_cost_deductions[index]
        for index in range(horizon)
    ]
    treatment = str(
        (project_snapshot.get("partnership") or {}).get("net_deduction_treatment")
        or "CUMULATIVE_CARRY_FORWARD"
    )
    net_sales_base, unused_deductions = _net_sales_base(
        revenue["gross_collections"],
        deductions,
        carry_forward=treatment == "CUMULATIVE_CARRY_FORWARD",
    )
    deductions_used = [
        max(ZERO, revenue["gross_collections"][index] - net_sales_base[index])
        for index in range(horizon)
    ]
    profit_base, loss_carryforward = _profit_base(
        revenue["eligible_profit_revenue"],
        eligible_profit_costs,
    )
    omitted_scope = {
        **product_omitted,
        **cost_omitted,
    }

    methods = [
        str(item).upper()
        for item in (
            studio.get("contract_methods")
            or ["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"]
        )
    ]
    allowed_methods = {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"}
    if not methods or any(item not in allowed_methods for item in methods):
        raise ValueError("contract_methods contains an unsupported method.")
    baseline_land = D(project_snapshot.get("land_value_baseline"))
    if baseline_land < ZERO:
        raise ValueError("land_value_baseline cannot be negative.")
    search_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def compute_evaluation(method: str, measure: Decimal) -> dict[str, Any]:
        return _evaluate_contract(
            method,
            measure,
            project=project_snapshot,
            policy=policy_snapshot,
            studio=studio,
            horizon=horizon,
            planned_horizon=planned_horizon,
            products=products,
            revenue=revenue,
            cost_items=cost_items,
            net_sales_base=net_sales_base,
            profit_base=profit_base,
            omitted_scope=omitted_scope,
        )

    def evaluate_search(method: str, measure: Decimal) -> dict[str, Any]:
        key = (method, str(measure))
        if key not in search_cache:
            search_cache[key] = _solver_evaluation_view(compute_evaluation(method, measure))
        return search_cache[key]

    def evaluate_full(method: str, measure: Decimal) -> dict[str, Any]:
        return compute_evaluation(method, measure)

    partnership = project_snapshot.get("partnership") or {}
    fixed_selected_measure: Decimal | None = None
    comparisons: list[dict[str, Any]] = []
    if selected_only:
        fixed_method = str(partnership.get("method") or methods[0] or "GROSS_SALES").upper()
        if _is_amount_method(fixed_method):
            fixed_selected_measure = D(
                partnership.get("manual_amount"),
                str(studio.get("minimum_guarantee_amount" if fixed_method == "MINIMUM_GUARANTEE" else "upfront_amount") or "0"),
            )
        else:
            fixed_selected_measure = D(partnership.get("manual_share"), str(partnership.get("share_rate") or "0"))
        fixed_evaluation = evaluate_full(fixed_method, fixed_selected_measure)
        if not fixed_evaluation.get("calculation_valid", True):
            fixed_status = "NUMERICALLY_UNRESOLVED"
        elif fixed_evaluation.get("feasible"):
            fixed_status = "VALID_RANGE"
        elif not fixed_evaluation.get("closure_passed", True):
            fixed_status = "STRUCTURALLY_INFEASIBLE"
        else:
            fixed_status = "POLICY_NONCOMPLIANT"
        comparisons.append({
            "method": fixed_method,
            "measure_type": fixed_evaluation.get("measure_type"),
            "status": fixed_status,
            "fair_floor": fixed_selected_measure if fixed_evaluation.get("feasible") else None,
            "minimum": fixed_selected_measure if fixed_evaluation.get("feasible") else None,
            "recommended": fixed_selected_measure if fixed_evaluation.get("feasible") else None,
            "balanced": fixed_selected_measure if fixed_evaluation.get("feasible") else None,
            "technical_ceiling": fixed_selected_measure if fixed_evaluation.get("feasible") else None,
            "maximum": fixed_selected_measure if fixed_evaluation.get("feasible") else None,
            "basis_label": fixed_evaluation.get("basis_label"),
            "government_value": fixed_evaluation.get("government_value"),
            "government_gross_npv": fixed_evaluation.get("government_gross_npv"),
            "government_npv": fixed_evaluation.get("government_npv"),
            "government_cost_contribution": fixed_evaluation.get("government_cost_contribution"),
            "government_net_economic_value": fixed_evaluation.get("government_net_economic_value"),
            "developer_profit": fixed_evaluation.get("developer_profit"),
            "developer_profit_on_cost": fixed_evaluation.get("developer_profit_on_cost"),
            "developer_npv": fixed_evaluation.get("developer_npv"),
            "developer_irr": fixed_evaluation.get("developer_irr"),
            "developer_equity_irr": fixed_evaluation.get("developer_equity_irr"),
            "project_irr": fixed_evaluation.get("project_irr"),
            "developer_multiple": fixed_evaluation.get("developer_multiple"),
            "peak_funding_gap": fixed_evaluation.get("peak_funding_gap"),
            "peak_debt": fixed_evaluation.get("peak_debt"),
            "peak_equity": fixed_evaluation.get("peak_equity"),
            "terminal_debt": fixed_evaluation.get("terminal_debt"),
            "terminal_deferred_cost": fixed_evaluation.get("terminal_deferred_cost"),
            "terminal_contractual_arrears": fixed_evaluation.get("terminal_contractual_arrears"),
            "unmodeled_scope": fixed_evaluation.get("unmodeled_scope"),
            "mandatory_shortfall": fixed_evaluation.get("mandatory_shortfall"),
            "schedule_extension_months": fixed_evaluation.get("schedule_extension_months"),
            "original_completion_date": fixed_evaluation.get("original_completion_date"),
            "adjusted_completion_date": fixed_evaluation.get("adjusted_completion_date"),
            "finance_mode": fixed_evaluation.get("finance_mode"),
            "spend_policy": fixed_evaluation.get("spend_policy"),
            "constraints": fixed_evaluation.get("constraints"),
            "evaluation_status": fixed_evaluation.get("evaluation_status"),
            "calculation_valid": fixed_evaluation.get("calculation_valid", True),
            "profit_share_convergence": fixed_evaluation.get("profit_share_convergence"),
            "failure_reasons": [row for row in fixed_evaluation.get("constraints") or [] if not row.get("passed")],
            "monotonic": True,
            "feasible_region_count": 1 if fixed_evaluation.get("feasible") else 0,
            "_evaluation": fixed_evaluation,
        })
    else:
        for method in methods:
            comparison = _comparison_for_method(
                method,
                evaluator=lambda measure, method=method: evaluate_search(method, measure),
                studio=studio,
                policy=policy_snapshot,
                baseline_land=baseline_land,
            )
            comparisons.append(comparison)
            # Each trial contains a full monthly ledger. Retaining every trial
            # across every contract method caused unbounded memory growth and
            # made the final Hybrid comparison appear to hang. The comparison
            # keeps its selected boundary evaluation by reference; all other
            # method-search trials can be released before the next method.
            search_cache.clear()

    valid = [
        item
        for item in comparisons
        if item.get("status") in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"}
    ]
    recommendation_objective = str(studio.get("recommendation_objective") or "BALANCED").upper()
    objective_weights = {
        "BALANCED": {
            "public_npv": Decimal("0.30"),
            "guarantee": Decimal("0.15"),
            "auditability": Decimal("0.20"),
            "funding": Decimal("0.15"),
            "schedule": Decimal("0.10"),
            "developer_return": Decimal("0.10"),
        },
        "MAX_PUBLIC_NPV": {
            "public_npv": Decimal("0.65"),
            "guarantee": Decimal("0.05"),
            "auditability": Decimal("0.10"),
            "funding": Decimal("0.10"),
            "schedule": Decimal("0.05"),
            "developer_return": Decimal("0.05"),
        },
        "MAX_GUARANTEED_VALUE": {
            "public_npv": Decimal("0.20"),
            "guarantee": Decimal("0.50"),
            "auditability": Decimal("0.15"),
            "funding": Decimal("0.05"),
            "schedule": Decimal("0.05"),
            "developer_return": Decimal("0.05"),
        },
        "MIN_FUNDING_GAP": {
            "public_npv": Decimal("0.20"),
            "guarantee": Decimal("0.10"),
            "auditability": Decimal("0.15"),
            "funding": Decimal("0.40"),
            "schedule": Decimal("0.10"),
            "developer_return": Decimal("0.05"),
        },
        "MIN_DISPUTE_RISK": {
            "public_npv": Decimal("0.15"),
            "guarantee": Decimal("0.15"),
            "auditability": Decimal("0.45"),
            "funding": Decimal("0.10"),
            "schedule": Decimal("0.10"),
            "developer_return": Decimal("0.05"),
        },
    }
    if recommendation_objective not in objective_weights:
        raise ValueError("Unsupported recommendation_objective.")

    auditability_quality = {
        "UPFRONT": Decimal("1.00"),
        "MINIMUM_GUARANTEE": Decimal("0.98"),
        "GROSS_SALES": Decimal("0.95"),
        "HYBRID": Decimal("0.85"),
        "NET_SALES": Decimal("0.70"),
        "PROFIT_SHARE": Decimal("0.50"),
    }

    def guaranteed_value(item: dict[str, Any]) -> Decimal:
        method = str(item.get("method") or "").upper()
        public_value = max(ZERO, D(item.get("government_value")))
        if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
            return public_value
        if method == "HYBRID":
            return min(public_value, max(ZERO, D(studio.get("hybrid_upfront_amount"), "0")))
        return ZERO

    def normalized(value: Decimal, low: Decimal, high: Decimal, *, inverse: bool = False) -> Decimal:
        if high - low <= EPS:
            result = ONE
        else:
            result = max(ZERO, min(ONE, (value - low) / (high - low)))
        return ONE - result if inverse else result

    public_values = [D(item.get("government_npv")) for item in valid] or [ZERO]
    guarantee_values = [guaranteed_value(item) for item in valid] or [ZERO]
    funding_values = [max(ZERO, D(item.get("peak_funding_gap"))) for item in valid] or [ZERO]
    extension_values = [max(ZERO, D(item.get("schedule_extension_months"))) for item in valid] or [ZERO]
    target_irr = max(EPS, D((policy_snapshot.get("financial_constraints") or {}).get("target_developer_irr"), "0.22"))
    weights = objective_weights[recommendation_objective]

    for item in valid:
        method = str(item.get("method") or "").upper()
        developer_irr = max(ZERO, D(item.get("developer_irr")))
        components = {
            "public_npv": normalized(D(item.get("government_npv")), min(public_values), max(public_values)),
            "guarantee": normalized(guaranteed_value(item), min(guarantee_values), max(guarantee_values)),
            "auditability": auditability_quality.get(method, Decimal("0.50")),
            "funding": normalized(max(ZERO, D(item.get("peak_funding_gap"))), min(funding_values), max(funding_values), inverse=True),
            "schedule": normalized(max(ZERO, D(item.get("schedule_extension_months"))), min(extension_values), max(extension_values), inverse=True),
            "developer_return": min(ONE, developer_irr / target_irr),
        }
        nonmonotonic_penalty = Decimal("0.10") if not item.get("monotonic", True) else ZERO
        score = sum((components[key] * weights[key] for key in weights), ZERO) - nonmonotonic_penalty
        item["guaranteed_public_value"] = guaranteed_value(item)
        item["recommendation_objective"] = recommendation_objective
        item["recommendation_components"] = components
        item["recommendation_weights"] = weights
        item["recommendation_score"] = max(ZERO, score)
        item["recommendation_rationale"] = [
            "Landowner NPV is measured after landowner cost contributions.",
            "Guaranteed value includes only fixed or minimum contractual consideration.",
            "Auditability reflects definition clarity and expected dispute exposure.",
            "Funding and schedule components penalize liquidity pressure and programme extension.",
            "Developer return is tested against the institutional target, not only the minimum threshold.",
        ]

    ranked_contracts = sorted(
        valid,
        key=lambda item: (
            D(item.get("recommendation_score")),
            D(item.get("government_npv")),
            -D(item.get("peak_funding_gap")),
        ),
        reverse=True,
    )
    for rank, item in enumerate(ranked_contracts, start=1):
        item["recommendation_rank"] = rank
    recommended_contract = ranked_contracts[0] if ranked_contracts else None

    selected_method = str(partnership.get("method") or (recommended_contract or {}).get("method") or "GROSS_SALES").upper()
    selected_comparison = next((item for item in comparisons if item.get("method") == selected_method), None)
    approved_selection = str(partnership.get("approved_selection") or "MODEL_BALANCED").upper()
    selected_measure: Decimal | None = fixed_selected_measure
    selection_reason = "The exact approved consideration was evaluated without re-optimizing the fair-share range." if selected_only else ""
    if not selected_only and selected_comparison and selected_comparison.get("fair_floor") is not None:
        if approved_selection == "MODEL_MINIMUM":
            selected_measure = D(selected_comparison.get("fair_floor"))
            selection_reason = "The approved minimum protects the required public-land net economic value floor."
        elif approved_selection in {"MODEL_BALANCED", "MODEL_RECOMMENDED"}:
            selected_measure = D(selected_comparison.get("balanced", selected_comparison.get("recommended")))
            selection_reason = "The approved balanced value targets the institutional developer return within the feasible range."
        elif approved_selection in {"MODEL_CEILING", "MODEL_MAXIMUM"}:
            selected_measure = D(selected_comparison.get("technical_ceiling"))
            selection_reason = "The approved ceiling is the highest feasible consideration before a mandatory constraint fails."
        else:
            if _is_amount_method(selected_method):
                selected_measure = D(
                    partnership.get("manual_amount"),
                    str(studio.get("minimum_guarantee_amount" if selected_method == "MINIMUM_GUARANTEE" else "upfront_amount") or "0"),
                )
            else:
                selected_measure = D(
                    partnership.get("manual_share"),
                    str(partnership.get("share_rate") or "0"),
                )
            selection_reason = "The approved consideration was entered manually and evaluated against all mandatory constraints."
    if selected_measure is None:
        # A failed or unresolved range must not cause the engine to substitute a
        # boundary candidate or another contract method.  Evaluate the explicitly
        # entered selected contract, and report its own economic/numerical status.
        if _is_amount_method(selected_method):
            selected_measure = D(
                partnership.get("manual_amount"),
                str(studio.get("minimum_guarantee_amount" if selected_method == "MINIMUM_GUARANTEE" else "upfront_amount") or "0"),
            )
        else:
            selected_measure = D(
                partnership.get("manual_share"),
                str(partnership.get("share_rate") or "0"),
            )
    # Search candidates retain scalar summaries only.  Recompute exactly one full
    # selected ledger for all pages, reports and hashes.
    search_cache.clear()
    selected_evaluation = evaluate_full(selected_method, selected_measure)

    # Preserve private evaluations until selection is resolved, then remove
    # them from the public comparison payload.
    for item in comparisons:
        item.pop("_evaluation", None)
    if recommended_contract:
        recommended_contract.pop("_evaluation", None)

    decision_reasons: list[dict[str, Any]] = []
    if selected_evaluation is None:
        for item in comparisons:
            decision_reasons.extend(item.get("failure_reasons") or [])
        status = "FAIL"
    else:
        decision_reasons = [item for item in selected_evaluation["constraints"] if not item["passed"]]
        status = "PASS" if not decision_reasons else "FAIL"
    evaluation_status = (
        "NO_EVALUATION"
        if selected_evaluation is None
        else selected_evaluation.get("evaluation_status", "SUPPORTED" if status == "PASS" else "ECONOMICALLY_INFEASIBLE")
    )

    monthly_rows: list[dict[str, Any]] = []
    distribution_ledger: list[dict[str, Any]] = []
    if selected_evaluation:
        used = len(selected_evaluation["simulation"]["rows"])
        actual_profit = selected_evaluation.get("actual_profit_base") or profit_base
        selected_method = str(selected_evaluation.get("method") or "").upper()
        selected_rate = D(selected_evaluation.get("measure")) if selected_method in {"NET_SALES", "HYBRID"} else ZERO
        hybrid_net = selected_method == "HYBRID" and str(studio.get("hybrid_variable_basis") or "").upper() == "NET_SALES"
        for index, row in enumerate(selected_evaluation["simulation"]["rows"]):
            monthly_rows.append(
                {
                    "month": row["month"],
                    "date": row["date"],
                    "opening_cash": row["opening_cash"],
                    "sales_collections": row["receipts"],
                    "government_payment": row["contractual_payment"],
                    "government_payment_arrears": row["contractual_arrears"],
                    "contractual_accrual": row.get("contractual_accrual", row.get("scheduled_contractual_payment", ZERO)),
                    "landowner_contract_cash": row.get("landowner_contract_cash", ZERO),
                    "landowner_cash_receipt": row.get("landowner_cash_receipt", ZERO),
                    "developer_recoverable_accrual": row.get("developer_recoverable_accrual", ZERO),
                    "developer_advance_recovery": row.get("developer_advance_recovery", ZERO),
                    "developer_recoverable_balance": row.get("developer_recoverable_balance", ZERO),
                    "interest_accrued": row["interest_accrued"],
                    "financing_fees_accrued": row["commitment_fee"] + row["upfront_fee"],
                    "finance_cost_accrued": row.get(
                        "finance_cost_accrued",
                        row["interest_accrued"] + row["commitment_fee"] + row["upfront_fee"],
                    ),
                    "finance_cost_paid": row.get(
                        "finance_cost_paid",
                        row["interest_accrued"] + row["commitment_fee"] + row["upfront_fee"],
                    ),
                    "finance_arrears": row.get("finance_arrears", ZERO),
                    # Legacy aliases retain their accrued interpretation.
                    "interest_paid": row["interest_accrued"],
                    "financing_fees": row["commitment_fee"] + row["upfront_fee"],
                    "planned_cost": row["scheduled_development_cost"],
                    "actual_cost": row["executed_development_cost"],
                    "deferred_cost": row["deferred_development_cost"],
                    "financing_draw": row["debt_draw"],
                    "equity_contribution": row["equity_contribution"],
                    "financing_repayment": row["principal_repayment"],
                    "opening_debt": row["opening_debt"],
                    "ending_debt": row["closing_debt"],
                    "distribution": row["developer_distribution"] + row["landowner_distribution"],
                    "developer_distribution": row["developer_distribution"],
                    "landowner_distribution": row["landowner_distribution"],
                    "required_reserve": row["required_distribution_reserve"],
                    "required_distribution_reserve": row.get("required_distribution_reserve", ZERO),
                    "distribution_due": bool(row.get("distribution_due")),
                    "distribution_block_reason": row.get("distribution_block_reason"),
                    "ending_cash": row["ending_cash"],
                    "cash_sources_total": row.get("cash_sources_total", ZERO),
                    "cash_uses_before_ending_cash": row.get("cash_uses_before_ending_cash", ZERO),
                    "cash_balance_variance": row.get("cash_balance_variance", ZERO),
                    "gross_contracted_sales": revenue["gross_contracted_sales"][index],
                    "net_contracted_sales": revenue["net_contracted_sales"][index],
                    "gross_collections": revenue["gross_collections"][index],
                    "net_collections": revenue["net_collections"][index],
                    "eligible_revenue_deductions": revenue["eligible_revenue_deductions"][index],
                    "eligible_cost_deductions": eligible_cost_deductions[index],
                    "eligible_deductions_total": deductions[index],
                    "deductions_used": deductions_used[index],
                    "net_sales_share_base": net_sales_base[index],
                    "net_sales_public_share": (
                        net_sales_base[index] * selected_rate
                        if selected_method == "NET_SALES" or hybrid_net
                        else ZERO
                    ),
                    "profit_share_base": actual_profit[index] if index < len(actual_profit) else ZERO,
                    "unsupported_funding_gap": row["unsupported_funding_gap"],
                    "mandatory_shortfall": row["mandatory_shortfall"],
                }
            )
        distribution_ledger = selected_evaluation["simulation"]["distribution_ledger"]

    total_government_cost = sum((item.get("government_total", ZERO) for item in cost_items), ZERO)
    total_third_party_cost = sum((item.get("third_party_total", ZERO) for item in cost_items), ZERO)
    deduction_categories: dict[str, Decimal] = {}
    for item in cost_items:
        item_deduction_schedule = _eligible_item_cost_schedule(
            item,
            horizon,
            "eligible_net_sales_deduction_fraction",
        )
        item_deduction_total = sum(item_deduction_schedule, ZERO)
        if item_deduction_total <= ZERO:
            continue
        category = str(item.get("net_sales_deduction_category") or item.get("category") or "project_cost")
        deduction_categories[category] = deduction_categories.get(category, ZERO) + item_deduction_total
    reconciliation_variance = sum(revenue["gross_collections"], ZERO) - sum(deductions_used, ZERO) - sum(net_sales_base, ZERO)
    if abs(reconciliation_variance) <= Decimal("0.01"):
        reconciliation_variance = ZERO
    selected_method_for_reconciliation = str((selected_evaluation or {}).get("method") or "").upper()
    selected_rate_for_reconciliation = (
        D((selected_evaluation or {}).get("measure"))
        if selected_method_for_reconciliation in {"NET_SALES", "HYBRID"}
        else ZERO
    )
    selected_hybrid_net = selected_method_for_reconciliation == "HYBRID" and str(studio.get("hybrid_variable_basis") or "").upper() == "NET_SALES"
    public_net_sales_consideration = (
        sum(net_sales_base[: len(monthly_rows) or horizon], ZERO) * selected_rate_for_reconciliation
        if selected_method_for_reconciliation == "NET_SALES" or selected_hybrid_net
        else ZERO
    )
    net_sales_reconciliation = {
        "treatment": treatment,
        "gross_sales_collections": sum(revenue["gross_collections"], ZERO),
        "cancellations_refunds_and_incentives": sum(revenue["eligible_revenue_deductions"], ZERO),
        "contractually_excluded_taxes": ZERO,
        "eligible_cost_deductions": sum(eligible_cost_deductions, ZERO),
        "eligible_deductions_supplied": sum(deductions, ZERO),
        "eligible_deductions_used": sum(deductions_used, ZERO),
        "unused_deduction_carryforward": unused_deductions,
        "eligible_net_sales": sum(net_sales_base, ZERO),
        "public_share_rate": selected_rate_for_reconciliation if selected_method_for_reconciliation == "NET_SALES" or selected_hybrid_net else None,
        "public_consideration_from_net_sales": public_net_sales_consideration,
        "reconciliation_variance": reconciliation_variance,
        "reconciliation_passed": reconciliation_variance == ZERO,
        "deductions_by_category": [
            {"category": key, "amount": value}
            for key, value in sorted(deduction_categories.items())
        ],
        "monthly": [
            {
                "month": index + 1,
                "gross_sales_collections": revenue["gross_collections"][index],
                "cancellations_refunds_and_incentives": revenue["eligible_revenue_deductions"][index],
                "eligible_cost_deductions": eligible_cost_deductions[index],
                "eligible_deductions_supplied": deductions[index],
                "eligible_deductions_used": deductions_used[index],
                "eligible_net_sales": net_sales_base[index],
                "public_consideration": (
                    net_sales_base[index] * selected_rate_for_reconciliation
                    if selected_method_for_reconciliation == "NET_SALES" or selected_hybrid_net
                    else ZERO
                ),
                "variance": revenue["gross_collections"][index] - deductions_used[index] - net_sales_base[index],
            }
            for index in range(horizon)
        ],
    }
    summary = {
        "gross_potential_revenue": sum((row["gross_potential_revenue"] for row in products), ZERO),
        "gross_sales": sum((row["gross_sales"] for row in products), ZERO),
        "net_sales": sum((row["net_sales"] for row in products), ZERO),
        "gross_collections": sum(revenue["gross_collections"], ZERO),
        "net_collections": sum(revenue["net_collections"], ZERO),
        "eligible_net_sales": sum(net_sales_base, ZERO),
        "eligible_net_sales_deductions": sum(deductions_used, ZERO),
        "unused_net_sales_deduction_carryforward": unused_deductions,
        "planned_total_cost": sum((item["gross_total"] for item in cost_items), ZERO),
        "developer_planned_cost": sum((item["total"] for item in cost_items), ZERO),
        "government_cost_contribution": total_government_cost,
        "third_party_cost_contribution": total_third_party_cost,
        "configured_horizon_months": configured_horizon,
        "required_horizon_months": required_horizon,
        "planned_project_duration_months": required_horizon,
        "project_duration_months": len(monthly_rows) if monthly_rows else planned_horizon,
        "horizon_auto_extended": bool(
            auto_extend
            and (base_horizon > configured_horizon or (selected_evaluation and selected_evaluation["schedule_extension_months"] > 0))
        ),
        "schedule_extension_months": ZERO if not selected_evaluation else selected_evaluation["schedule_extension_months"],
        "original_completion_date": None if not selected_evaluation else selected_evaluation["original_completion_date"],
        "adjusted_completion_date": None if not selected_evaluation else selected_evaluation["adjusted_completion_date"],
        "finance_mode": None if not selected_evaluation else selected_evaluation["finance_mode"],
        "spend_policy": None if not selected_evaluation else selected_evaluation["spend_policy"],
        "unmodeled_scope": sum(omitted_scope.values(), ZERO) if not selected_evaluation else selected_evaluation["unmodeled_scope"],
        "unmodeled_scope_breakdown": omitted_scope if not selected_evaluation else selected_evaluation["unmodeled_scope_breakdown"],
        "unused_net_sales_deductions": unused_deductions,
        "profit_loss_carryforward": loss_carryforward,
        "peak_funding_gap": ZERO if not selected_evaluation else selected_evaluation["peak_funding_gap"],
        "peak_negative_cash": ZERO if not selected_evaluation else selected_evaluation["peak_negative_cash"],
        "peak_debt": ZERO if not selected_evaluation else selected_evaluation["peak_debt"],
        "peak_equity": ZERO if not selected_evaluation else selected_evaluation["peak_equity"],
        "interest_total": ZERO if not selected_evaluation else selected_evaluation["interest_total"],
        "financing_fees_total": ZERO if not selected_evaluation else selected_evaluation["financing_fees_total"],
        "terminal_debt": ZERO if not selected_evaluation else selected_evaluation["terminal_debt"],
        "terminal_deferred_cost": ZERO if not selected_evaluation else selected_evaluation["terminal_deferred_cost"],
        "terminal_contractual_arrears": ZERO if not selected_evaluation else selected_evaluation["terminal_contractual_arrears"],
        "terminal_finance_arrears": ZERO if not selected_evaluation else selected_evaluation.get("terminal_finance_arrears", ZERO),
        "mandatory_shortfall": ZERO if not selected_evaluation else selected_evaluation["mandatory_shortfall"],
        "actual_gross_project_cost": ZERO if not selected_evaluation else selected_evaluation["actual_gross_project_cost"],
        "project_cost_scope_shortfall": ZERO if not selected_evaluation else selected_evaluation["project_cost_scope_shortfall"],
        "developer_profit": None if not selected_evaluation else selected_evaluation["developer_profit"],
        "developer_profit_on_cost": None if not selected_evaluation else selected_evaluation["developer_profit_on_cost"],
        "developer_profit_on_revenue": None if not selected_evaluation else selected_evaluation["developer_profit_on_revenue"],
        "developer_unlevered_npv": None if not selected_evaluation else selected_evaluation["developer_unlevered_npv"],
        "developer_unlevered_irr": None if not selected_evaluation else selected_evaluation["developer_unlevered_irr"],
        "project_profit": None if not selected_evaluation else selected_evaluation["project_profit"],
        "project_profit_on_cost": None if not selected_evaluation else selected_evaluation["project_profit_on_cost"],
        "project_profit_on_revenue": None if not selected_evaluation else selected_evaluation["project_profit_on_revenue"],
        "developer_equity_nominal_profit": None if not selected_evaluation else selected_evaluation["developer_equity_nominal_profit"],
        "developer_equity_contributions": None if not selected_evaluation else selected_evaluation["developer_equity_contributions"],
        "developer_equity_distributions": None if not selected_evaluation else selected_evaluation["developer_equity_distributions"],
        "developer_multiple": None if not selected_evaluation else selected_evaluation["developer_multiple"],
        "developer_irr": None if not selected_evaluation else selected_evaluation["developer_irr"],
        "developer_equity_irr": None if not selected_evaluation else selected_evaluation["developer_equity_irr"],
        "developer_equity_npv": None if not selected_evaluation else selected_evaluation["developer_equity_npv"],
        "project_npv": None if not selected_evaluation else selected_evaluation["project_npv"],
        "project_irr": None if not selected_evaluation else selected_evaluation["project_irr"],
        "developer_npv": None if not selected_evaluation else selected_evaluation["developer_npv"],
        "government_value": None if not selected_evaluation else selected_evaluation["government_value"],
        "government_npv": None if not selected_evaluation else selected_evaluation["government_npv"],
        "government_gross_npv": None if not selected_evaluation else selected_evaluation["government_gross_npv"],
        "government_net_npv_after_costs": None if not selected_evaluation else selected_evaluation["government_net_npv_after_costs"],
        "government_cost_contribution_npv": None if not selected_evaluation else selected_evaluation["government_cost_contribution_npv"],
        "status": status,
        "evaluation_status": evaluation_status,
        "calculation_valid": False if not selected_evaluation else selected_evaluation.get("calculation_valid", True),
        "economic_feasible": False if not selected_evaluation else selected_evaluation.get("economic_feasible", False),
        "policy_compliant": False if not selected_evaluation else selected_evaluation.get("policy_compliant", False),
        "closure_passed": False if not selected_evaluation else selected_evaluation.get("closure_passed", False),
        "cash_reconciliation_passed": False if not selected_evaluation else selected_evaluation["simulation"].get("cash_reconciliation_passed", False),
        "maximum_cash_balance_variance": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("maximum_cash_balance_variance", ZERO),
        "available_equity_capacity": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("available_equity_capacity", ZERO),
        "recognized_equity_policy": None if not selected_evaluation else selected_evaluation.get("recognized_equity_policy"),
        "available_debt_capacity": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("available_debt_capacity", ZERO),
        "base_date_equity_contribution": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("base_date_equity_contribution", ZERO),
        "total_equity_contributed": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("total_equity_contributed", ZERO),
        "total_developer_distributions": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("total_developer_distributions", ZERO),
        "total_landowner_cash_receipts": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("total_landowner_cash_receipts", ZERO),
        "total_developer_recoverable_accrued": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("total_developer_recoverable_accrued", ZERO),
        "total_developer_advance_recovered": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("total_developer_advance_recovered", ZERO),
        "ending_developer_recoverable_balance": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("ending_developer_recoverable_balance", ZERO),
        "distribution_policy": {} if not selected_evaluation else deepcopy(selected_evaluation["simulation"].get("config", {}).get("distribution_policy") or {}),
        "ending_cash": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("ending_cash", ZERO),
        "minimum_cash_balance": ZERO if not selected_evaluation else selected_evaluation["simulation"].get("config", {}).get("minimum_cash_balance", ZERO),
    }

    selected_contract = None
    if selected_evaluation is not None:
        selected_contract = {
            "method": selected_method,
            "approved_selection": approved_selection,
            "measure": selected_measure,
            "measure_type": selected_evaluation["measure_type"],
            "reason": selection_reason,
            "feasible": selected_evaluation["feasible"],
            "evaluation_status": selected_evaluation.get("evaluation_status"),
            "calculation_valid": selected_evaluation.get("calculation_valid", True),
            "economic_feasible": selected_evaluation.get("economic_feasible", False),
            "policy_compliant": selected_evaluation.get("policy_compliant"),
            "closure_passed": selected_evaluation.get("closure_passed"),
            "government_value": selected_evaluation["government_value"],
            "government_npv": selected_evaluation["government_npv"],
            "government_gross_npv": selected_evaluation["government_gross_npv"],
            "government_net_npv_after_costs": selected_evaluation["government_net_npv_after_costs"],
            "government_cost_contribution_npv": selected_evaluation["government_cost_contribution_npv"],
            "government_cost_contribution": selected_evaluation["government_cost_contribution"],
            "developer_profit": selected_evaluation["developer_profit"],
            "developer_profit_on_cost": selected_evaluation["developer_profit_on_cost"],
            "developer_profit_on_revenue": selected_evaluation["developer_profit_on_revenue"],
            "developer_profit_definition": selected_evaluation["developer_profit_definition"],
            "developer_unlevered_npv": selected_evaluation["developer_unlevered_npv"],
            "developer_unlevered_irr": selected_evaluation["developer_unlevered_irr"],
            "developer_unlevered_irr_status": selected_evaluation["developer_unlevered_irr_status"],
            "project_profit": selected_evaluation["project_profit"],
            "project_profit_on_cost": selected_evaluation["project_profit_on_cost"],
            "project_profit_on_revenue": selected_evaluation["project_profit_on_revenue"],
            "project_profit_definition": selected_evaluation["project_profit_definition"],
            "planned_gross_project_cost": selected_evaluation["planned_gross_project_cost"],
            "actual_gross_project_cost": selected_evaluation["actual_gross_project_cost"],
            "project_cost_scope_shortfall": selected_evaluation["project_cost_scope_shortfall"],
            "actual_government_project_cost": selected_evaluation["actual_government_project_cost"],
            "actual_third_party_project_cost": selected_evaluation["actual_third_party_project_cost"],
            "developer_equity_nominal_profit": selected_evaluation["developer_equity_nominal_profit"],
            "developer_equity_contributions": selected_evaluation["developer_equity_contributions"],
            "developer_equity_distributions": selected_evaluation["developer_equity_distributions"],
            "developer_equity_profit_definition": selected_evaluation["developer_equity_profit_definition"],
            "developer_multiple": selected_evaluation["developer_multiple"],
            "developer_irr": selected_evaluation["developer_irr"],
            "developer_npv": selected_evaluation["developer_npv"],
            "developer_equity_irr": selected_evaluation["developer_equity_irr"],
            "developer_equity_npv": selected_evaluation["developer_equity_npv"],
            "project_irr": selected_evaluation["project_irr"],
            "project_npv": selected_evaluation["project_npv"],
            "peak_funding_gap": selected_evaluation["peak_funding_gap"],
            "peak_negative_cash": selected_evaluation["peak_negative_cash"],
            "peak_debt": selected_evaluation["peak_debt"],
            "peak_equity": selected_evaluation["peak_equity"],
            "interest_total": selected_evaluation["interest_total"],
            "financing_fees_total": selected_evaluation["financing_fees_total"],
            "terminal_debt": selected_evaluation["terminal_debt"],
            "terminal_deferred_cost": selected_evaluation["terminal_deferred_cost"],
            "terminal_contractual_arrears": selected_evaluation["terminal_contractual_arrears"],
            "terminal_finance_arrears": selected_evaluation.get("terminal_finance_arrears", ZERO),
            "mandatory_shortfall": selected_evaluation["mandatory_shortfall"],
            "unmodeled_scope": selected_evaluation["unmodeled_scope"],
            "project_cost_scope_shortfall": selected_evaluation["project_cost_scope_shortfall"],
            "finance_mode": selected_evaluation["finance_mode"],
            "spend_policy": selected_evaluation["spend_policy"],
            "original_completion_date": selected_evaluation["original_completion_date"],
            "adjusted_completion_date": selected_evaluation["adjusted_completion_date"],
            "schedule_extension_months": selected_evaluation["schedule_extension_months"],
            "constraints": selected_evaluation["constraints"],
            "profit_share_convergence": selected_evaluation.get("profit_share_convergence"),
            "profit_share_basis": selected_evaluation.get("profit_share_basis"),
            "cash_reconciliation_passed": selected_evaluation["simulation"].get("cash_reconciliation_passed", False),
            "maximum_cash_balance_variance": selected_evaluation["simulation"].get("maximum_cash_balance_variance", ZERO),
            "available_equity_capacity": selected_evaluation["simulation"].get("available_equity_capacity", ZERO),
            "recognized_equity_policy": selected_evaluation.get("recognized_equity_policy"),
            "available_debt_capacity": selected_evaluation["simulation"].get("available_debt_capacity", ZERO),
            "base_date_equity_contribution": selected_evaluation["simulation"].get("base_date_equity_contribution", ZERO),
            "total_equity_contributed": selected_evaluation["simulation"].get("total_equity_contributed", ZERO),
            "total_developer_distributions": selected_evaluation["simulation"].get("total_developer_distributions", ZERO),
            "total_landowner_cash_receipts": selected_evaluation["simulation"].get("total_landowner_cash_receipts", ZERO),
            "total_developer_recoverable_accrued": selected_evaluation["simulation"].get("total_developer_recoverable_accrued", ZERO),
            "total_developer_advance_recovered": selected_evaluation["simulation"].get("total_developer_advance_recovered", ZERO),
            "ending_developer_recoverable_balance": selected_evaluation["simulation"].get("ending_developer_recoverable_balance", ZERO),
            "distribution_policy": deepcopy(selected_evaluation["simulation"].get("config", {}).get("distribution_policy") or {}),
            "ending_cash": selected_evaluation["simulation"].get("ending_cash", ZERO),
            "minimum_cash_balance": selected_evaluation["simulation"].get("config", {}).get("minimum_cash_balance", ZERO),
        }

    output = {
        "model_version": MODEL_VERSION,
        "monthly": True,
        "single_source_financial_kernel": "landvalue360_kernel.monthly_engine",
        "allow_negative_cash": B(finance_model.get("allow_negative_cash"), B(studio.get("allow_negative_cash"), False)),
        "auto_extend_horizon": auto_extend,
        "summary": summary,
        "selected_contract": selected_contract,
        "recommended_contract": recommended_contract,
        "recommendation_summary": {
            "objective": recommendation_objective,
            "weights": weights,
            "ranked_methods": [item.get("method") for item in ranked_contracts],
            "explanation": {
                "BALANCED": "Balances public NPV, guaranteed value, auditability, funding burden, schedule impact and developer target return.",
                "MAX_PUBLIC_NPV": "Prioritizes the highest present value to the public landowner while preserving mandatory feasibility constraints.",
                "MAX_GUARANTEED_VALUE": "Prioritizes fixed and minimum contractual consideration that is less exposed to market or accounting definitions.",
                "MIN_FUNDING_GAP": "Prioritizes structures that minimize residual liquidity pressure and execution delay.",
                "MIN_DISPUTE_RISK": "Prioritizes clear, auditable structures with lower deduction and profit-definition dispute exposure.",
            }[recommendation_objective],
        },
        "contract_comparison": comparisons,
        "products": products,
        "cost_items": [
            {
                key: value
                for key, value in item.items()
                if key not in {"schedule", "gross_schedule", "government_schedule", "third_party_schedule"}
            }
            for item in cost_items
        ],
        "net_sales_reconciliation": net_sales_reconciliation,
        "monthly_cashflow": monthly_rows,
        "funding_diagnostic_ledger": deepcopy(selected_evaluation.get("funding_diagnostic_ledger") or []),
        "terminal_funding_diagnostic": deepcopy(selected_evaluation.get("terminal_funding_diagnostic") or {}),
        "distribution_ledger": distribution_ledger,
        "annual_distributions": distribution_ledger,
        "decision_explanation": {
            "status": status,
            "reasons": decision_reasons,
            "message": (
                "The approved structure passes every mandatory return, funding, debt, payment and completion constraint."
                if status == "PASS"
                else "The approved structure fails one or more mandatory constraints. Each failure is listed with its actual value, threshold and reason."
            ),
        },
        "methodology": {
            "gross_sales_definition": "Eligible gross cash collections after commercial discounts but before buyer incentives, refunds and cost deductions.",
            "net_sales_definition": "Eligible gross cash collections after only the deductions expressly permitted by the contract, with the selected carry-forward treatment.",
            "profit_share_definition": "Positive collected cash profit after eligible paid developer cost and prior loss carry-forward, solved to numerical convergence.",
            "public_value_definition": "Public net economic value equals land consideration and public distributions less public-funded project costs.",
            "fair_floor_definition": "The lowest feasible consideration meeting the required public net economic value NPV.",
            "recommended_definition": "The balanced consideration meeting the target developer return within the first connected feasible range.",
            "technical_ceiling_definition": "The highest economically feasible consideration before a return, liquidity, debt, or completion constraint fails. Numerical non-convergence never defines or substitutes for an economic ceiling.",
            "recommendation_method": "Objective-driven multi-criteria score combining public net NPV, guaranteed value, auditability, funding burden, schedule extension and developer target return; the full ranking and components remain visible.",
            "date_basis": "Actual monthly dates with ACT/365F XNPV and XIRR.",
        },
        "explanation": (
            "Schedule-driven mode executes the entered programme and discloses any negative cash or unsupported funding gap."
            if B(finance_model.get("allow_negative_cash"), False)
            else "Cash-controlled mode prohibits negative cash, extends the programme within policy limits, and cannot pass with unpaid cost, contractual arrears or terminal debt."
        ),
    }
    serialized = _serialize(output)
    serialized["calculation_hash"] = sha256_json(serialized)
    return serialized
