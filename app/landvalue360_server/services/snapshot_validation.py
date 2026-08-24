"""Server-side structural validation for editable project snapshots.

The browser provides immediate validation, but authoritative integrity must live
on the server so API imports and future clients cannot persist contradictory
percent totals or broken curves.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from ..errors import ConflictError

TOLERANCE = Decimal("0.0001")
ONE = Decimal("1")

COST_METHODS = {"LEGACY_QUANTITY_X_RATE", "FIXED_AMOUNT", "MANUAL_AMOUNT", "COMPUTED_QUANTITY_X_RATE", "PERCENT_OF_COST"}
QUANTITY_BASES = {"GROSS_LAND_AREA_SQM", "NET_LAND_AREA_SQM", "TOTAL_GFA_SQM", "TOTAL_SELLABLE_AREA_SQM", "MAXIMUM_FOOTPRINT_SQM", "LAND_USE_AREA_SQM", "PRODUCT_GFA_SQM", "PRODUCT_SELLABLE_AREA_SQM", "PRODUCT_UNIT_COUNT", "TOTAL_UNIT_COUNT"}
PERCENT_BASES = {"COST_ITEM", "DIRECT_COST", "HARD_COST", "SOFT_COST", "ALL_COST", "CATEGORY"}
PERCENT_STAGES = {"BASE_COST", "CONTINGENCY_INCLUDED", "ESCALATED_TOTAL"}
SPEND_POLICIES = {"SCHEDULE_DRIVEN", "CASH_DRIVEN", "HYBRID"}


def _decimal(value: Any, path: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ConflictError("PROJECT_SNAPSHOT_INVALID_NUMBER", f"{path} must be numeric.") from exc
    if not result.is_finite():
        raise ConflictError("PROJECT_SNAPSHOT_INVALID_NUMBER", f"{path} must be a finite number.")
    return result


def _require_fraction(value: Any, path: str, label: str) -> Decimal:
    result = _decimal(value, path)
    if result < 0 or result > ONE:
        raise ConflictError("PROJECT_PERCENT_OUT_OF_RANGE", f"{label} must be between 0% and 100%.")
    return result


def _require_nonnegative(value: Any, path: str, label: str) -> Decimal:
    result = _decimal(value, path)
    if result < 0:
        raise ConflictError("PROJECT_VALUE_NEGATIVE", f"{label} cannot be negative.")
    return result


def _sum(values: Iterable[Any], path: str) -> Decimal:
    return sum((_decimal(value, path) for value in values), Decimal("0"))


def _require_total_one(values: Iterable[Any], path: str, label: str) -> None:
    materialized = list(values)
    for index, value in enumerate(materialized):
        _require_fraction(value, f"{path}[{index}]", label)
    total = _sum(materialized, path)
    if abs(total - ONE) > TOLERANCE:
        raise ConflictError(
            "PROJECT_PERCENT_TOTAL_INVALID",
            f"{label} must total exactly 100%. Current total: {total * 100}%.",
        )


def _require_unique(rows: list[dict[str, Any]], key: str, path: str) -> None:
    values = [str(row.get(key) or "").strip() for row in rows]
    if any(not value for value in values):
        raise ConflictError("PROJECT_IDENTIFIER_REQUIRED", f"Every row in {path} requires {key}.")
    if len(values) != len(set(values)):
        raise ConflictError("PROJECT_IDENTIFIER_DUPLICATE", f"Duplicate {key} found in {path}.")


def validate_project_snapshot_structure(snapshot: dict[str, Any]) -> None:
    """Reject contradictory project snapshots before they are persisted.

    The function intentionally validates structural invariants only. Business
    feasibility and policy constraints remain the responsibility of the
    calculation kernel so draft projects may still contain uneconomic values.
    """

    if not isinstance(snapshot, dict):
        raise ConflictError("PROJECT_SNAPSHOT_INVALID", "Project snapshot must be a JSON object.")
    planning = snapshot.get("planning") or {}
    land_uses = planning.get("land_uses") or []
    if land_uses:
        _require_unique(land_uses, "land_use_id", "planning.land_uses")
        _require_total_one((row.get("share") for row in land_uses), "planning.land_uses.share", "Land-use shares")

    planning_products = snapshot.get("planning_products") or []
    if planning_products:
        _require_unique(planning_products, "product_id", "planning_products")
    allocated = [row for row in planning_products if str(row.get("area_method") or "GFA_ALLOCATION") == "GFA_ALLOCATION"]
    if allocated:
        _require_total_one(
            (row.get("gfa_allocation_share") for row in allocated),
            "planning_products.gfa_allocation_share",
            "GFA product allocation shares",
        )

    commercial_products = snapshot.get("products") or []
    if commercial_products:
        _require_unique(commercial_products, "product_id", "products")
    planning_ids = {str(row.get("product_id")) for row in planning_products if row.get("is_sellable", True)}
    commercial_ids = {str(row.get("product_id")) for row in commercial_products}
    if planning_products and commercial_products and commercial_ids != planning_ids:
        missing = sorted(planning_ids - commercial_ids)
        extra = sorted(commercial_ids - planning_ids)
        raise ConflictError(
            "PROJECT_PRODUCT_LINKAGE_INVALID",
            f"Commercial products must match sellable planning products. Missing: {missing}; extra: {extra}.",
        )
    for product in commercial_products:
        product_id = str(product.get("product_id"))
        for field, label in (
            ("unit_price", "Unit price"),
            ("construction_cost_per_sqm", "Product construction cost"),
            ("unit_count", "Unit count"),
        ):
            if field in product and product.get(field) not in (None, ""):
                _require_nonnegative(product.get(field), f"products.{product_id}.{field}", f"{label} for {product_id}")
        if "construction_developer_responsibility_share" in product or "construction_government_responsibility_share" in product:
            _require_total_one(
                [
                    product.get("construction_developer_responsibility_share", 1),
                    product.get("construction_government_responsibility_share", 0),
                ],
                f"products.{product_id}.construction_responsibility",
                f"Construction cost responsibility for {product_id}",
            )
        for field, label in (
            ("commercial_discount_rate", "Commercial discount"),
            ("buyer_incentive_rate", "Buyer incentive"),
            ("refund_rate", "Refund rate"),
            ("buyer_incentive_net_sales_deduction_fraction", "Buyer-incentive deduction fraction"),
            ("refund_net_sales_deduction_fraction", "Refund deduction fraction"),
            ("eligible_profit_share_revenue_fraction", "Profit-share revenue eligibility"),
        ):
            if field in product and product.get(field) not in (None, ""):
                _require_fraction(product.get(field), f"products.{product_id}.{field}", f"{label} for {product_id}")
        sales_curve = product.get("sales_curve") or []
        if not sales_curve:
            raise ConflictError("PROJECT_SALES_CURVE_REQUIRED", f"Product {product_id} requires a sales curve.")
        _require_total_one(
            (row.get("weight") for row in sales_curve),
            f"products.{product_id}.sales_curve.weight",
            f"Sales curve for {product_id}",
        )
        collection_rules = product.get("collection_rules") or []
        if collection_rules:
            _require_total_one(
                (row.get("weight") for row in collection_rules),
                f"products.{product_id}.collection_rules.weight",
                f"Collection rules for {product_id}",
            )
            for index, rule in enumerate(collection_rules):
                try:
                    lag_days = int(rule.get("lag_days", 0))
                except (TypeError, ValueError) as exc:
                    raise ConflictError("PROJECT_COLLECTION_LAG_INVALID", f"Collection lag for {product_id} must be a whole number of days.") from exc
                if lag_days < 0 or lag_days > 3650:
                    raise ConflictError("PROJECT_COLLECTION_LAG_INVALID", f"Collection lag for {product_id} must be between 0 and 3,650 days.")

    product_cost_mode = str(snapshot.get("developer_product_cost_mode") or "UNIT_RATE").upper()
    if product_cost_mode not in {"UNIT_RATE", "WORK_PACKAGES"}:
        raise ConflictError(
            "PROJECT_PRODUCT_COST_MODE_INVALID",
            "Product construction-cost mode must be UNIT_RATE or WORK_PACKAGES.",
        )
    plans = snapshot.get("developer_product_cost_plans") or {}
    if plans and not isinstance(plans, dict):
        raise ConflictError("PROJECT_PRODUCT_COST_PLAN_INVALID", "Product cost plans must be an object keyed by product id.")
    if product_cost_mode == "WORK_PACKAGES":
        for product in commercial_products:
            product_id = str(product.get("product_id"))
            plan = plans.get(product_id) or {}
            lines = plan.get("lines") or []
            if not lines:
                raise ConflictError(
                    "PROJECT_PRODUCT_COST_PLAN_REQUIRED",
                    f"Detailed product cost mode requires at least one enabled cost package for {product_id}.",
                )
            line_ids: set[str] = set()
            for index, line in enumerate(lines):
                line_id = str(line.get("line_id") or f"LINE-{index + 1}")
                if line_id in line_ids:
                    raise ConflictError("PROJECT_PRODUCT_COST_LINE_DUPLICATE", f"Duplicate cost-package id {line_id} for {product_id}.")
                line_ids.add(line_id)
                if line.get("enabled") is False:
                    continue
                basis = str(line.get("quantity_basis") or "PRODUCT_GFA_SQM").upper()
                if basis not in {"PRODUCT_GFA_SQM", "PRODUCT_SELLABLE_AREA_SQM", "PRODUCT_UNIT_COUNT", "FIXED_AMOUNT"}:
                    raise ConflictError("PROJECT_PRODUCT_COST_BASIS_INVALID", f"Unsupported product cost basis {basis} for {product_id}.")
                amount_field = "fixed_amount" if basis == "FIXED_AMOUNT" else "unit_cost"
                _require_nonnegative(line.get(amount_field, 0), f"developer_product_cost_plans.{product_id}.{line_id}.{amount_field}", f"Cost-package amount for {product_id}")
                for field in ("contingency_rate",):
                    if line.get(field) not in (None, ""):
                        _require_fraction(line.get(field), f"developer_product_cost_plans.{product_id}.{line_id}.{field}", f"{field} for {product_id}")

    cost_entry_mode = str(snapshot.get("construction_cost_entry_mode") or "CATEGORY").upper()
    if cost_entry_mode not in {"PRODUCT", "CATEGORY"}:
        raise ConflictError(
            "PROJECT_COST_ENTRY_MODE_INVALID",
            "Construction cost entry must be either Product-based or Component-based.",
        )
    # Product mode replaces only product-construction rows. General project
    # costs (infrastructure, authorities, professional fees, management,
    # marketing and other rows explicitly outside product construction) remain
    # active and must be validated and included in the monthly ledger.
    all_costs = snapshot.get("costs") or []
    costs = all_costs if cost_entry_mode == "CATEGORY" else [
        row for row in all_costs
        if bool(row.get("always_include_in_product_mode"))
        or not bool(row.get("covered_by_product_construction"))
    ]
    if costs:
        _require_unique(costs, "cost_id", "costs")
    cost_ids = {str(row.get("cost_id")) for row in costs}
    cost_categories = {str(row.get("category") or "UNCLASSIFIED") for row in costs}
    land_use_ids = {str(row.get("land_use_id")) for row in land_uses}
    planning_product_ids = {str(row.get("product_id")) for row in planning_products}
    percentage_item_edges: dict[str, str] = {}
    for cost in costs:
        cost_id = str(cost.get("cost_id"))
        _require_total_one(
            [cost.get("developer_responsibility_share"), cost.get("government_responsibility_share")],
            f"costs.{cost_id}.responsibility",
            f"Cost responsibility for {cost_id}",
        )
        curve = cost.get("expenditure_curve") or []
        if not curve:
            raise ConflictError("PROJECT_COST_CURVE_REQUIRED", f"Cost {cost_id} requires an expenditure curve.")
        _require_total_one(
            (row.get("weight") for row in curve),
            f"costs.{cost_id}.expenditure_curve.weight",
            f"Expenditure curve for {cost_id}",
        )
        for field, label in (
            ("contingency_rate", "Contingency rate"),
            ("eligible_net_sales_deduction_fraction", "Net-sales deduction eligibility"),
            ("eligible_profit_share_cost_fraction", "Profit-share cost eligibility"),
        ):
            if field in cost and cost.get(field) not in (None, ""):
                _require_fraction(cost.get(field), f"costs.{cost_id}.{field}", f"{label} for {cost_id}")
        for field, label in (("quantity", "Quantity"), ("unit_cost", "Unit cost"), ("fixed_amount", "Fixed amount")):
            if field in cost and cost.get(field) not in (None, ""):
                _require_nonnegative(cost.get(field), f"costs.{cost_id}.{field}", f"{label} for {cost_id}")
        method = str(cost.get("calculation_method") or "LEGACY_QUANTITY_X_RATE").upper()
        if method not in COST_METHODS:
            raise ConflictError("PROJECT_COST_METHOD_INVALID", f"Cost {cost_id} has unsupported calculation method {method}.")
        if method in {"FIXED_AMOUNT", "MANUAL_AMOUNT"}:
            if _decimal(cost.get("fixed_amount", cost.get("unit_cost", 0)), f"costs.{cost_id}.fixed_amount") < 0:
                raise ConflictError("PROJECT_COST_AMOUNT_NEGATIVE", f"Cost {cost_id} fixed/manual amount cannot be negative.")
        if method == "COMPUTED_QUANTITY_X_RATE":
            basis = str(cost.get("quantity_basis") or "").upper()
            if basis not in QUANTITY_BASES:
                raise ConflictError("PROJECT_COST_BASIS_INVALID", f"Cost {cost_id} requires a supported computed quantity basis.")
            reference = str(cost.get("basis_reference_id") or "").strip()
            if basis in {"LAND_USE_AREA_SQM", "PRODUCT_GFA_SQM", "PRODUCT_SELLABLE_AREA_SQM", "PRODUCT_UNIT_COUNT"} and not reference:
                raise ConflictError("PROJECT_COST_REFERENCE_REQUIRED", f"Cost {cost_id} requires a land-use or product reference.")
            if basis == "LAND_USE_AREA_SQM" and reference not in land_use_ids:
                raise ConflictError("PROJECT_COST_REFERENCE_INVALID", f"Cost {cost_id} references unknown land use {reference}.")
            if basis in {"PRODUCT_GFA_SQM", "PRODUCT_SELLABLE_AREA_SQM", "PRODUCT_UNIT_COUNT"} and reference not in planning_product_ids:
                raise ConflictError("PROJECT_COST_REFERENCE_INVALID", f"Cost {cost_id} references unknown planning product {reference}.")
            multiplier = _decimal(cost.get("basis_multiplier", 1), f"costs.{cost_id}.basis_multiplier")
            addition = _decimal(cost.get("basis_addition", 0), f"costs.{cost_id}.basis_addition")
            unit_cost = _decimal(cost.get("unit_cost", 0), f"costs.{cost_id}.unit_cost")
            if multiplier < 0 or unit_cost < 0:
                raise ConflictError("PROJECT_COST_BASIS_NEGATIVE", f"Cost {cost_id} basis multiplier and unit cost cannot be negative.")
            if addition < 0:
                raise ConflictError("PROJECT_COST_BASIS_NEGATIVE", f"Cost {cost_id} basis addition cannot be negative.")
        if method == "PERCENT_OF_COST":
            basis = str(cost.get("percentage_basis") or "").upper()
            stage = str(cost.get("percentage_basis_stage") or "BASE_COST").upper()
            rate = _decimal(cost.get("percentage_rate", 0), f"costs.{cost_id}.percentage_rate")
            if basis not in PERCENT_BASES or stage not in PERCENT_STAGES or rate < 0:
                raise ConflictError("PROJECT_COST_PERCENTAGE_INVALID", f"Cost {cost_id} has an invalid percentage basis, stage, or rate.")
            reference = str(cost.get("basis_reference_id") or "").strip()
            if basis in {"COST_ITEM", "CATEGORY"} and not reference:
                raise ConflictError("PROJECT_COST_REFERENCE_REQUIRED", f"Cost {cost_id} requires a referenced cost item or category.")
            if basis == "COST_ITEM":
                if reference not in cost_ids:
                    raise ConflictError("PROJECT_COST_REFERENCE_INVALID", f"Cost {cost_id} references unknown cost item {reference}.")
                if reference == cost_id:
                    raise ConflictError("PROJECT_COST_REFERENCE_CYCLE", f"Cost {cost_id} cannot reference itself.")
                percentage_item_edges[cost_id] = reference
            if basis == "CATEGORY" and reference not in cost_categories:
                raise ConflictError("PROJECT_COST_REFERENCE_INVALID", f"Cost {cost_id} references unknown cost category {reference}.")

    # Detect percentage-on-percentage reference cycles before persistence.
    for start in percentage_item_edges:
        seen: set[str] = set()
        cursor = start
        while cursor in percentage_item_edges:
            if cursor in seen:
                raise ConflictError("PROJECT_COST_REFERENCE_CYCLE", f"Circular percentage cost reference detected at {cursor}.")
            seen.add(cursor)
            cursor = percentage_item_edges[cursor]

    finance_model = snapshot.get("finance_model") or {}
    if "enabled" in finance_model and not isinstance(finance_model.get("enabled"), bool):
        raise ConflictError("PROJECT_FINANCE_BOOLEAN_INVALID", "finance_model.enabled must be true or false.")
    for field, label, upper in (
        ("annual_interest_rate", "Annual interest rate", Decimal("5")),
        ("upfront_fee_rate", "Upfront fee rate", ONE),
        ("commitment_fee_rate", "Commitment fee rate", ONE),
        ("cash_sweep_share", "Cash sweep share", ONE),
    ):
        if field in finance_model and finance_model.get(field) not in (None, ""):
            value = _decimal(finance_model.get(field), f"finance_model.{field}")
            if value < 0 or value > upper:
                raise ConflictError("PROJECT_FINANCE_RATE_INVALID", f"{label} is outside its permitted range.")
    for field, label in (
        ("opening_cash", "Opening cash"),
        ("committed_additional_equity", "Committed additional equity"),
        ("committed_equity", "Legacy committed equity"),
        ("committed_financing", "Committed financing"),
        ("initial_cash", "Legacy initial cash"),
    ):
        funding = snapshot.get("funding") or {}
        if field in funding and funding.get(field) not in (None, ""):
            _require_nonnegative(funding.get(field), f"funding.{field}", label)
    spend_policy = str(finance_model.get("spend_policy") or "SCHEDULE_DRIVEN").upper()
    if spend_policy not in SPEND_POLICIES:
        raise ConflictError("PROJECT_SPEND_POLICY_INVALID", "Spend policy must be Schedule Driven, Cash Driven, or Hybrid.")
    if spend_policy == "HYBRID":
        share = _decimal(finance_model.get("hybrid_minimum_execution_share", "0.35"), "finance_model.hybrid_minimum_execution_share")
        if share < 0 or share > 1:
            raise ConflictError("PROJECT_HYBRID_SHARE_INVALID", "Hybrid minimum execution share must be between 0% and 100%.")
    reserve_share = _decimal(finance_model.get("future_cost_reserve_share", "1"), "finance_model.future_cost_reserve_share")
    if reserve_share < 0 or reserve_share > 1:
        raise ConflictError("PROJECT_FUTURE_COST_RESERVE_INVALID", "Future cost reserve share must be between 0% and 100%.")
    maximum_extension = finance_model.get("maximum_extension_months", 120)
    try:
        maximum_extension = int(maximum_extension)
    except (TypeError, ValueError) as exc:
        raise ConflictError("PROJECT_MAXIMUM_EXTENSION_INVALID", "Maximum automatic extension must be a whole number of months.") from exc
    if maximum_extension < 0 or maximum_extension > 600:
        raise ConflictError("PROJECT_MAXIMUM_EXTENSION_INVALID", "Maximum automatic extension must be between 0 and 600 months.")
    monthly_execution_share = _decimal(finance_model.get("maximum_monthly_execution_share", "0.15"), "finance_model.maximum_monthly_execution_share")
    if monthly_execution_share < 0 or monthly_execution_share > 1:
        raise ConflictError("PROJECT_MONTHLY_EXECUTION_SHARE_INVALID", "Maximum monthly execution share must be between 0% and 100%.")
    monthly_execution_amount = _decimal(finance_model.get("maximum_monthly_execution_amount", "0"), "finance_model.maximum_monthly_execution_amount")
    if monthly_execution_amount < 0:
        raise ConflictError("PROJECT_MONTHLY_EXECUTION_AMOUNT_INVALID", "Maximum monthly execution amount cannot be negative.")
    for boolean_field in ("allow_negative_cash", "defer_contractual_payments"):
        if boolean_field in finance_model and not isinstance(finance_model.get(boolean_field), bool):
            raise ConflictError("PROJECT_FINANCE_BOOLEAN_INVALID", f"{boolean_field} must be true or false.")

    tender = snapshot.get("tender_studio") or {}
    criteria = tender.get("criteria_weights") or {}
    if criteria:
        _require_total_one(criteria.values(), "tender_studio.criteria_weights", "Tender evaluation weights")
    bids = tender.get("bids") or []
    if bids:
        _require_unique(bids, "bid_id", "tender_studio.bids")
