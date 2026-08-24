"""Application snapshot normalization and backwards-compatible migrations.

This module owns application-level compatibility migrations and normalizes
every project to one explicit detailed financial meaning.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from datetime import date
import calendar
from typing import Any


from landvalue360_common.versions import PROJECT_NORMALIZATION_VERSION
CONSTRUCTION_MODES = {"PRODUCT", "CATEGORY"}


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _number(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered




def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _uniform_monthly_curve(base_date: str | None, start_month: int, duration_months: int) -> list[dict[str, str]]:
    try:
        base = date.fromisoformat(str(base_date))
    except (TypeError, ValueError):
        base = date.today().replace(day=1)
    duration = max(1, min(600, int(duration_months)))
    weight = Decimal("1") / Decimal(duration)
    return [
        {
            "date": _add_months(base, max(0, int(start_month) - 1) + index).isoformat(),
            "weight": _number(weight),
        }
        for index in range(duration)
    ]

def _investment_area(project: dict[str, Any], gross: Decimal, net: Decimal) -> Decimal:
    land_uses = (project.get("planning") or {}).get("land_uses") or []
    investment = sum(
        (
            gross * _decimal(item.get("share"))
            for item in land_uses
            if str(item.get("land_use_id") or "").upper() in {"INVESTMENT", "DEVELOPABLE", "PLOTS"}
        ),
        Decimal("0"),
    )
    return investment if investment > 0 else net


def _migrate_legacy_simple_snapshot(project: dict[str, Any]) -> dict[str, Any]:
    """Convert a pre-v2.1 total-only project into explicit detailed rows.

    This is a backwards-compatibility migration only.  It is never exposed as
    a selectable analysis mode.  The source totals are retained for audit and
    the resulting product/cost rows are visible and editable.
    """

    project = deepcopy(project)
    simple = project.get("simple_analysis_inputs")
    if not isinstance(simple, dict):
        simple = {}
    revenue = _decimal(simple.get("total_revenue", project.get("simple_total_revenue")))
    cost = _decimal(simple.get("total_project_cost", project.get("simple_total_project_cost")))
    duration = max(1, min(600, int(simple.get("duration_months") or project.get("simple_duration_months") or 48)))
    sales_start = max(1, min(duration, int(simple.get("sales_start_month") or 1)))
    sales_duration = max(1, min(600, int(simple.get("sales_duration_months") or duration)))
    cost_start = max(1, min(duration, int(simple.get("cost_start_month") or 1)))
    cost_duration = max(1, min(600, int(simple.get("cost_duration_months") or duration)))
    lag = max(0, min(600, int(simple.get("collection_lag_months") or 0)))
    if revenue < 0 or cost < 0:
        raise ValueError("Legacy total revenue and project cost cannot be negative.")

    original = {
        "analysis_mode": str(project.get("analysis_mode") or "SIMPLE"),
        "total_revenue": _number(revenue),
        "total_project_cost": _number(cost),
        "duration_months": duration,
        "sales_start_month": sales_start,
        "sales_duration_months": sales_duration,
        "cost_start_month": cost_start,
        "cost_duration_months": cost_duration,
        "collection_lag_months": lag,
    }
    project["planning"] = {
        "gross_land_area_sqm": "1",
        "excluded_land_area_sqm": "0",
        "far_land_basis": "GROSS",
        "bcr_land_basis": "GROSS",
        "far": "1",
        "bcr": "1",
        "land_uses": [{"land_use_id": "INVESTMENT", "name": "Legacy total-analysis basis", "share": "1"}],
    }
    project["planning_products"] = [{
        "product_id": "LEGACY_TOTAL",
        "name": "Migrated total revenue",
        "area_method": "DIRECT_AREA",
        "direct_sellable_area_sqm": "1",
        "is_sellable": True,
        "efficiency": "1",
    }]
    project["products"] = [{
        "product_id": "LEGACY_TOTAL",
        "name": "Migrated total revenue",
        "description": "Generated once from a legacy total-only project during the v2.1 detailed-only migration.",
        "quantity_basis": "SELLABLE_AREA_SQM",
        "quantity_unit": "project total",
        "unit_price": _number(revenue),
        "construction_cost_per_sqm": "0",
        "commercial_discount_rate": "0",
        "buyer_incentive_rate": "0",
        "refund_rate": "0",
        "buyer_incentive_net_sales_deduction_fraction": "1",
        "refund_net_sales_deduction_fraction": "1",
        "eligible_profit_share_revenue_fraction": "1",
        "sales_start_month": sales_start,
        "sales_duration_months": sales_duration,
        "sales_curve_type": str(simple.get("sales_curve_type") or "LINEAR").upper(),
        "sales_curve": _uniform_monthly_curve(project.get("valuation_date"), sales_start, sales_duration),
        "collection_rules": [{"lag_months": lag, "lag_days": lag * 30, "weight": "1"}],
        "construction_start_month": cost_start,
        "construction_duration_months": cost_duration,
        "construction_curve_type": "LINEAR",
        "construction_developer_responsibility_share": "1",
        "construction_government_responsibility_share": "0",
        "construction_developer_economic_share": "1",
        "construction_government_economic_share": "0",
    }]
    project["costs"] = [{
        "cost_id": "LEGACY-TOTAL-COST",
        "name": "Migrated total project cost",
        "category": "DIRECT_CONSTRUCTION",
        "quantity": "1",
        "unit_cost": _number(cost),
        "fixed_amount": _number(cost),
        "calculation_method": "FIXED_AMOUNT",
        "base_date": project.get("valuation_date"),
        "escalation_rate": "0",
        "contingency_rate": "0",
        "developer_responsibility_share": "1",
        "government_responsibility_share": "0",
        "developer_economic_share": "1",
        "government_economic_share": "0",
        "eligible_net_sales_deduction_fraction": "0",
        "eligible_profit_share_cost_fraction": "1",
        "is_direct_cost": True,
        "expenditure_curve": _uniform_monthly_curve(project.get("valuation_date"), cost_start, cost_duration),
        "monthly_start_month": cost_start,
        "monthly_duration_months": cost_duration,
        "monthly_curve_type": str(simple.get("cost_curve_type") or "LINEAR").upper(),
        "cash_payer": "DEVELOPER",
        "economic_bearer": "DEVELOPER",
        "reimbursable": False,
    }]
    project["construction_cost_entry_mode"] = "PRODUCT"
    project["developer_product_cost_mode"] = "UNIT_RATE"
    studio = project.setdefault("landowner_studio", {})
    studio["horizon_months"] = max(int(studio.get("horizon_months") or 0), duration + lag + 3)
    studio["other_cost_start_month"] = cost_start
    studio["other_cost_duration_months"] = cost_duration
    studio["other_cost_curve_type"] = str(simple.get("cost_curve_type") or "LINEAR").upper()
    project.setdefault("migration_audit", {})["legacy_total_analysis"] = {
        "migrated_to_detailed_only": True,
        "source": original,
        "generated_product_id": "LEGACY_TOTAL",
        "generated_cost_id": "LEGACY-TOTAL-COST",
        "review_required": True,
    }
    return project


def normalize_project_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return one canonical detailed project snapshot.

    Analysis-mode branching was removed in v2.1.  Legacy SIMPLE snapshots are
    migrated once into an explicit detailed representation and their original
    totals are retained only in ``migration_audit``.  New snapshots never store
    an analysis-mode selector or hidden total-only assumptions.
    """

    project = deepcopy(snapshot or {})
    legacy_mode = str(project.get("analysis_mode") or "DETAILED").upper()
    has_legacy_totals = isinstance(project.get("simple_analysis_inputs"), dict) or any(
        project.get(key) not in (None, "")
        for key in ("simple_total_revenue", "simple_total_project_cost", "simple_duration_months")
    )
    if legacy_mode == "SIMPLE" or (has_legacy_totals and not (project.get("products") and project.get("costs"))):
        project = _migrate_legacy_simple_snapshot(project)
    for obsolete in (
        "analysis_mode", "source_analysis_mode", "analysis_mode_assumptions",
        "simple_analysis_inputs", "simple_total_revenue",
        "simple_total_project_cost", "simple_duration_months",
        "legacy_simple_materialized_snapshot", "_calculation_materialized",
        "conversion_manifest",
    ):
        project.pop(obsolete, None)
    planning = project.setdefault("planning", {})
    gross = max(Decimal("0"), _decimal(planning.get("gross_land_area_sqm")))
    excluded = max(Decimal("0"), _decimal(planning.get("excluded_land_area_sqm")))
    net = max(Decimal("0"), gross - excluded)
    investment = _investment_area(project, gross, net)

    # Reference land value is entered as a rate.  Legacy projects keep their
    # historical total and receive an explicit, visible derived rate.
    basis = str(project.get("reference_land_value_basis") or "GROSS").upper()
    if basis not in {"GROSS", "NET", "INVESTMENT"}:
        basis = "GROSS"
    area = {"GROSS": gross, "NET": net, "INVESTMENT": investment}[basis]
    total = _decimal(project.get("reference_land_value_total", project.get("land_value_baseline")))
    rate_supplied = project.get("reference_land_value_per_sqm") not in (None, "")
    rate = _decimal(project.get("reference_land_value_per_sqm"))
    legacy_derived = False
    if not rate_supplied and total > 0 and area > 0:
        rate = total / area
        legacy_derived = True
    if rate_supplied:
        total = rate * area
    project["reference_land_value_basis"] = basis
    project["reference_land_value_area_sqm"] = _number(area)
    project["reference_land_value_per_sqm"] = _number(rate)
    project["reference_land_value_total"] = _number(total)
    project["reference_land_value_legacy_derived"] = legacy_derived
    project["land_value_baseline"] = _number(total)

    # Exactly one construction approach is active.  Existing projects that
    # already contain explicit product rates migrate to product mode.
    mode = str(project.get("construction_cost_entry_mode") or "").upper()
    if mode not in CONSTRUCTION_MODES:
        has_product_rates = any(
            item.get("construction_cost_per_sqm") not in (None, "")
            for item in project.get("products") or []
        )
        mode = "PRODUCT" if has_product_rates else "CATEGORY"
    project["construction_cost_entry_mode"] = mode
    for product in project.get("products") or []:
        product.setdefault("description", "")
        product.setdefault("construction_cost_per_sqm", "0")
        product.pop("construction_rate_confirmed", None)
        product.setdefault("construction_cost_base_date", project.get("valuation_date"))
        product.setdefault("construction_escalation_rate", "0")
        product.setdefault("construction_contingency_rate", "0")
        product.setdefault("market_growth_rate", "0")
        product.setdefault("pricing_notes", "")
        product.setdefault("payment_plan_id", "CUSTOM")

    product_cost_mode = str(project.get("developer_product_cost_mode") or "UNIT_RATE").upper()
    product_cost_mode = {"SIMPLE": "UNIT_RATE", "DETAILED": "WORK_PACKAGES"}.get(product_cost_mode, product_cost_mode)
    if product_cost_mode not in {"UNIT_RATE", "WORK_PACKAGES"}:
        product_cost_mode = "UNIT_RATE"
    project["developer_product_cost_mode"] = product_cost_mode
    plans = project.get("developer_product_cost_plans")
    if not isinstance(plans, dict):
        plans = {}
    project["developer_product_cost_plans"] = plans
    project.setdefault("developer_market_strategy", {
        "pricing_basis": "PER_PRODUCT",
        "market_notes": "",
        "absorption_notes": "",
        "default_payment_plan_id": "CUSTOM",
    })
    project.setdefault("developer_cost_strategy", {
        "procurement_savings_target": "0.05",
        "opening_negotiation_discount": "0.10",
        "final_negotiation_discount": "0.05",
    })

    # Normalize cost rows before validation. Earlier interfaces could persist a
    # fixed-amount row with an empty ``fixed_amount`` while storing the actual
    # value in ``unit_cost``. Treat that legacy representation explicitly
    # instead of failing or silently dropping the row.
    for index, cost in enumerate(project.get("costs") or []):
        cost.setdefault("cost_id", f"COST-{index + 1}")
        cost.setdefault("name", cost.get("cost_id"))
        method = str(cost.get("calculation_method") or "LEGACY_QUANTITY_X_RATE").upper()
        cost["calculation_method"] = method
        if method in {"FIXED_AMOUNT", "MANUAL_AMOUNT"} and cost.get("fixed_amount") in (None, ""):
            cost["fixed_amount"] = _number(_decimal(cost.get("unit_cost")))
        cost.setdefault("cash_payer", "DEVELOPER")
        cost.setdefault("economic_bearer", cost.get("cash_payer") or "DEVELOPER")
        cost.setdefault("reimbursable", False)
        cost.setdefault("eligible_net_sales_deduction_fraction", "0")
        cost.setdefault("eligible_profit_share_cost_fraction", cost.get("developer_responsibility_share", "1"))

    # Opening cash is cash on hand at month one.  Committed additional equity
    # is a separate undrawn ceiling.  Legacy committed_equity is preserved as
    # an alias only for the frozen kernel contract.
    funding = project.setdefault("funding", {})
    studio = project.setdefault("landowner_studio", {})
    opening = _decimal(
        funding.get(
            "opening_cash",
            studio.get("initial_cash", "0"),
        )
    )
    additional = _decimal(
        funding.get(
            "committed_additional_equity",
            funding.get("committed_equity", "0"),
        )
    )
    funding["opening_cash"] = _number(max(opening, Decimal("0")))
    funding["committed_additional_equity"] = _number(max(additional, Decimal("0")))
    funding["committed_equity"] = funding["committed_additional_equity"]
    funding["committed_equity_is_additional"] = True
    studio["initial_cash"] = funding["opening_cash"]
    studio.setdefault("use_committed_financing", True)

    sensitivity = project.setdefault("sensitivity_studio", {})
    sensitivity.setdefault(
        "drivers",
        [
            {"driver": "SALES_PRICE", "unit": "%", "values": ["-0.10", "-0.05", "0", "0.05", "0.10"]},
            {"driver": "DEVELOPMENT_COST", "unit": "%", "values": ["-0.10", "0", "0.05", "0.10", "0.15"]},
            {"driver": "SALES_DELAY", "unit": "months", "values": [0, 6, 12]},
            {"driver": "CONSTRUCTION_DELAY", "unit": "months", "values": [0, 6, 12]},
            {"driver": "INTEREST_RATE", "unit": "% points", "values": ["-0.02", "0", "0.02", "0.04"]},
            {"driver": "LANDOWNER_SHARE", "unit": "% points", "values": ["-0.03", "0", "0.01", "0.03"]},
        ],
    )
    sensitivity.setdefault("metric", "developer_npv")

    project["project_normalization_version"] = PROJECT_NORMALIZATION_VERSION
    return project



def materialize_project_for_calculation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical detailed project used by the financial engine."""

    return normalize_project_snapshot(snapshot)
