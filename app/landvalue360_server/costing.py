"""Application-level cost-basis resolution for LandValue360 Enterprise.

The frozen feasibility kernel 0.2.0 intentionally accepts only explicit
``quantity × unit_cost`` rows.  Release 0.7.1 adds a governed preprocessing
layer that resolves institutional cost bases (land, GFA, sellable area,
product areas, land-use areas, fixed amounts and percentage-of-cost bases)
into that existing kernel contract.  Legacy rows are passed through without
numerical change.

No arbitrary expressions are evaluated.  This avoids formula injection and
keeps every derived amount explainable and reproducible.
"""

from __future__ import annotations

from copy import deepcopy
from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any

from landvalue360_kernel.costs import CostItemInput, calculate_cost_item
from landvalue360_kernel.curves import parse_curve
from landvalue360_kernel.dates import parse_date
from landvalue360_kernel.decimal_utils import ONE, ZERO, as_json_number, decimal
from landvalue360_kernel.exceptions import InputValidationError
from landvalue360_kernel.planning import calculate_planning, planning_input_from_dict
from landvalue360_kernel.validation import strict_boolean
from .project_normalization import normalize_project_snapshot

COST_RESOLUTION_VERSION = "0.4.0"

LEGACY_METHOD = "LEGACY_QUANTITY_X_RATE"
FIXED_METHODS = {"FIXED_AMOUNT", "MANUAL_AMOUNT"}
COMPUTED_METHOD = "COMPUTED_QUANTITY_X_RATE"
PERCENT_METHOD = "PERCENT_OF_COST"
SUPPORTED_METHODS = {LEGACY_METHOD, *FIXED_METHODS, COMPUTED_METHOD, PERCENT_METHOD}

SUPPORTED_QUANTITY_BASES = {
    "GROSS_LAND_AREA_SQM",
    "NET_LAND_AREA_SQM",
    "TOTAL_GFA_SQM",
    "TOTAL_SELLABLE_AREA_SQM",
    "MAXIMUM_FOOTPRINT_SQM",
    "LAND_USE_AREA_SQM",
    "PRODUCT_GFA_SQM",
    "PRODUCT_SELLABLE_AREA_SQM",
    "PRODUCT_UNIT_COUNT",
    "TOTAL_UNIT_COUNT",
}
SUPPORTED_PERCENTAGE_BASES = {
    "COST_ITEM",
    "DIRECT_COST",
    "HARD_COST",
    "SOFT_COST",
    "ALL_COST",
    "CATEGORY",
}
SUPPORTED_STAGES = {"BASE_COST", "CONTINGENCY_INCLUDED", "ESCALATED_TOTAL"}

# These keys are intentionally application-level. They are removed before the
# strict kernel validates the cost object.
COST_EXTENSION_KEYS = {
    "calculation_method",
    "quantity_basis",
    "basis_reference_id",
    "basis_multiplier",
    "basis_addition",
    "fixed_amount",
    "percentage_rate",
    "percentage_basis",
    "percentage_basis_stage",
    "calculation_note",
    "eligible_net_sales_deduction_cap",
    "net_sales_deduction_treatment",
    "net_sales_deduction_basis",
    "net_sales_deduction_category",
    "net_sales_deduction_contract_rule",
    "net_sales_deduction_approval_required",
    "net_sales_deduction_approval_obtained",
    "net_sales_deduction_evidence_required",
    "net_sales_deduction_evidence_status",
    "net_sales_deduction_related_party",
    "net_sales_deduction_market_test_required",
    "net_sales_deduction_market_test_passed",
    "cash_payer",
    "economic_bearer",
    "developer_economic_share",
    "reimbursable",
    "covered_by_product_construction",
    "always_include_in_product_mode",
    "construction_cost_entry_mode",
    "developer_product_cost_mode",
    "developer_product_cost_plans",
}

HARD_COST_CATEGORIES = {
    "DIRECT_CONSTRUCTION",
    "PRODUCT_CONSTRUCTION",
    "INFRASTRUCTURE",
    "PUBLIC_FACILITIES",
}



def cost_inputs_from_dicts(
    raw_costs: list[dict[str, Any]],
    *,
    require_profit_share_eligibility: bool = False,
) -> tuple[CostItemInput, ...]:
    """Parse resolved detailed cost rows into canonical cost inputs.

    This parser belongs to the current cost service.  The detailed-stable
    runtime no longer imports private functions from the removed legacy engine.
    """
    ids = [str(item.get("cost_id") or "").strip() for item in raw_costs]
    if any(not value for value in ids):
        raise InputValidationError("Cost identifiers cannot be empty.", path="project.costs", code="COST_ID_EMPTY")
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise InputValidationError(f"Duplicate cost identifiers are not allowed: {', '.join(duplicates)}.", path="project.costs", code="DUPLICATE_COST_ID")
    parsed: list[CostItemInput] = []
    for index, item in enumerate(raw_costs):
        if require_profit_share_eligibility and "eligible_profit_share_cost_fraction" not in item:
            raise InputValidationError(
                "Every cost item must state the fraction eligible in the contractual cash-profit base.",
                path=f"project.costs[{index}].eligible_profit_share_cost_fraction",
                code="PROFIT_SHARE_COST_ELIGIBILITY_REQUIRED",
            )
        parsed.append(CostItemInput(
            cost_id=str(item["cost_id"]),
            name=str(item.get("name") or item["cost_id"]),
            category=str(item.get("category") or "UNCLASSIFIED"),
            quantity=decimal(item.get("quantity", 0)),
            unit_cost=decimal(item.get("unit_cost", 0)),
            base_date=parse_date(item["base_date"]),
            escalation_rate=decimal(item.get("escalation_rate", 0)),
            contingency_rate=decimal(item.get("contingency_rate", 0)),
            developer_responsibility_share=decimal(item.get("developer_responsibility_share", 1)),
            government_responsibility_share=decimal(item.get("government_responsibility_share", 0)),
            eligible_net_sales_deduction_fraction=decimal(item.get("eligible_net_sales_deduction_fraction", 0)),
            eligible_profit_share_cost_fraction=decimal(item.get("eligible_profit_share_cost_fraction", item.get("developer_responsibility_share", 1))),
            is_direct_cost=strict_boolean(item.get("is_direct_cost", True), path=f"project.costs[{index}].is_direct_cost"),
            expenditure_curve=parse_curve(item["expenditure_curve"], path=f"project.costs[{index}].expenditure_curve"),
        ))
    return tuple(parsed)

def _add_months(value: date, months: int) -> date:
    target = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(target, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _uniform_monthly_curve(start: date, duration: int) -> list[dict[str, str]]:
    duration = max(1, min(int(duration or 1), 600))
    share = ONE / Decimal(duration)
    assigned = ZERO
    rows: list[dict[str, str]] = []
    for index in range(duration):
        weight = ONE - assigned if index == duration - 1 else share
        assigned += weight
        rows.append({"date": _add_months(start, index).isoformat(), "weight": as_json_number(weight)})
    return rows


def _product_construction_rows(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Create explicit kernel rows for product construction.

    Developer Edition supports two governed entry modes:

    ``UNIT_RATE``
        One construction rate per product GFA.

    ``WORK_PACKAGES``
        Multiple cost-package rows inside each product.  Each package may use
        product GFA, product sellable area, product unit count or a fixed
        amount.  General project costs remain in ``project.costs`` and are not
        duplicated here.
    """

    _, references = _planning_context(project)
    planning_products = {
        str(item.get("product_id")): item for item in project.get("planning_products") or []
    }
    valuation = parse_date(project.get("valuation_date") or date.today().isoformat())
    cost_mode = str(project.get("developer_product_cost_mode") or "UNIT_RATE").upper()
    plans = project.get("developer_product_cost_plans") or {}
    rows: list[dict[str, Any]] = []

    def quantity_for(product_id: str, line: dict[str, Any]) -> tuple[Decimal, str]:
        basis = str(line.get("quantity_basis") or "PRODUCT_GFA_SQM").upper()
        metrics = references["products"].get(product_id, {})
        if basis == "PRODUCT_GFA_SQM":
            quantity = metrics.get("PRODUCT_GFA_SQM", ZERO)
        elif basis == "PRODUCT_SELLABLE_AREA_SQM":
            quantity = metrics.get("PRODUCT_SELLABLE_AREA_SQM", ZERO)
        elif basis == "PRODUCT_UNIT_COUNT":
            quantity = metrics.get("PRODUCT_UNIT_COUNT", ZERO)
        elif basis == "FIXED_AMOUNT":
            quantity = ONE
        else:
            raise InputValidationError(
                f"Unsupported product cost-package basis: {basis}.",
                path=f"project.developer_product_cost_plans.{product_id}",
                code="PRODUCT_COST_PACKAGE_BASIS_UNSUPPORTED",
            )
        multiplier = decimal(line.get("quantity_multiplier", 1))
        addition = decimal(line.get("quantity_addition", 0))
        return max(ZERO, quantity * multiplier + addition), basis

    for index, product in enumerate(project.get("products") or []):
        product_id = str(product.get("product_id") or f"PRODUCT-{index + 1}")
        product_name = str(product.get("name") or planning_products.get(product_id, {}).get("name") or product_id)
        plan = plans.get(product_id) if isinstance(plans, dict) else None
        lines = list((plan or {}).get("lines") or []) if isinstance(plan, dict) else []
        use_detailed = cost_mode == "DETAILED" and bool(lines)

        if use_detailed:
            for line_index, line in enumerate(lines):
                if line.get("enabled") is False:
                    continue
                line_id = str(line.get("line_id") or f"LINE-{line_index + 1}")
                quantity, basis = quantity_for(product_id, line)
                unit_cost_value = line.get("fixed_amount") if basis == "FIXED_AMOUNT" else line.get("unit_cost")
                rate = decimal(unit_cost_value or 0, path=f"project.developer_product_cost_plans.{product_id}.{line_id}.unit_cost")
                if rate < ZERO:
                    raise InputValidationError(
                        "Product cost-package rate cannot be negative.",
                        path=f"project.developer_product_cost_plans.{product_id}.{line_id}.unit_cost",
                        code="PRODUCT_COST_PACKAGE_RATE_NEGATIVE",
                    )
                base_date = parse_date(line.get("base_date") or product.get("construction_cost_base_date") or valuation.isoformat())
                start_month = max(1, int(line.get("start_month") or product.get("construction_start_month") or 1))
                duration = max(1, int(line.get("duration_months") or product.get("construction_duration_months") or 30))
                developer_share = decimal(line.get("developer_responsibility_share", product.get("construction_developer_responsibility_share", 1)))
                landowner_share = decimal(line.get("government_responsibility_share", product.get("construction_government_responsibility_share", 0)))
                rows.append({
                    "cost_id": f"PRODUCT-{product_id}-{line_id}",
                    "name": str(line.get("name") or f"{product_name} {line_id}"),
                    "category": "PRODUCT_CONSTRUCTION",
                    "quantity": as_json_number(quantity),
                    "unit_cost": as_json_number(rate),
                    "base_date": base_date.isoformat(),
                    "escalation_rate": as_json_number(decimal(line.get("escalation_rate", product.get("construction_escalation_rate", 0)))),
                    "contingency_rate": as_json_number(decimal(line.get("contingency_rate", product.get("construction_contingency_rate", 0)))),
                    "developer_responsibility_share": as_json_number(developer_share),
                    "government_responsibility_share": as_json_number(landowner_share),
                    "eligible_net_sales_deduction_fraction": as_json_number(decimal(line.get("eligible_net_sales_deduction_fraction", product.get("construction_net_sales_deduction_fraction", 0)))),
                    "eligible_net_sales_deduction_cap": line.get(
                        "eligible_net_sales_deduction_cap",
                        product.get("eligible_net_sales_deduction_cap"),
                    ),
                    "eligible_profit_share_cost_fraction": as_json_number(decimal(line.get("eligible_profit_share_cost_fraction", product.get("construction_profit_share_cost_fraction", 1)))),
                    "is_direct_cost": True,
                    "expenditure_curve": _uniform_monthly_curve(_add_months(valuation, start_month - 1), duration),
                    "calculation_method": "FIXED_AMOUNT" if basis == "FIXED_AMOUNT" else COMPUTED_METHOD,
                    "fixed_amount": as_json_number(rate) if basis == "FIXED_AMOUNT" else None,
                    "quantity_basis": basis,
                    "basis_reference_id": product_id,
                    "basis_multiplier": as_json_number(decimal(line.get("quantity_multiplier", 1))),
                    "basis_addition": as_json_number(decimal(line.get("quantity_addition", 0))),
                    "calculation_note": str(line.get("notes") or f"Detailed {product_name} construction package."),
                })
            continue

        quantity = references["products"].get(product_id, {}).get("PRODUCT_GFA_SQM", ZERO)
        rate = decimal(product.get("construction_cost_per_sqm", 0), path=f"project.products.{product_id}.construction_cost_per_sqm")
        if rate < ZERO:
            raise InputValidationError(
                "Product construction rate cannot be negative.",
                path=f"project.products.{product_id}.construction_cost_per_sqm",
                code="PRODUCT_CONSTRUCTION_RATE_NEGATIVE",
            )
        base_date = parse_date(product.get("construction_cost_base_date") or valuation.isoformat())
        start_month = max(1, int(product.get("construction_start_month") or 1))
        duration = max(1, int(product.get("construction_duration_months") or 30))
        developer_share = decimal(product.get("construction_developer_responsibility_share", 1))
        landowner_share = decimal(product.get("construction_government_responsibility_share", 0))
        rows.append({
            "cost_id": f"PRODUCT-CONSTRUCTION-{product_id}",
            "name": f"{product_name} construction",
            "category": "PRODUCT_CONSTRUCTION",
            "quantity": as_json_number(quantity),
            "unit_cost": as_json_number(rate),
            "base_date": base_date.isoformat(),
            "escalation_rate": as_json_number(decimal(product.get("construction_escalation_rate", 0))),
            "contingency_rate": as_json_number(decimal(product.get("construction_contingency_rate", 0))),
            "developer_responsibility_share": as_json_number(developer_share),
            "government_responsibility_share": as_json_number(landowner_share),
            "eligible_net_sales_deduction_fraction": as_json_number(decimal(product.get("construction_net_sales_deduction_fraction", 0))),
            "eligible_net_sales_deduction_cap": product.get("eligible_net_sales_deduction_cap"),
            "eligible_profit_share_cost_fraction": as_json_number(decimal(product.get("construction_profit_share_cost_fraction", 1))),
            "is_direct_cost": True,
            "expenditure_curve": _uniform_monthly_curve(_add_months(valuation, start_month - 1), duration),
            "calculation_method": COMPUTED_METHOD,
            "quantity_basis": "PRODUCT_GFA_SQM",
            "basis_reference_id": product_id,
            "basis_multiplier": "1",
            "basis_addition": "0",
            "calculation_note": "Product GFA × simple construction rate per GFA m².",
        })
    return rows


def _cost_input(item: dict[str, Any]) -> CostItemInput:
    return CostItemInput(
        cost_id=str(item["cost_id"]),
        name=str(item.get("name") or item["cost_id"]),
        category=str(item.get("category") or "UNCLASSIFIED"),
        quantity=decimal(item.get("quantity", 0)),
        unit_cost=decimal(item.get("unit_cost", 0)),
        base_date=parse_date(item["base_date"]),
        escalation_rate=decimal(item.get("escalation_rate", 0)),
        contingency_rate=decimal(item.get("contingency_rate", 0)),
        developer_responsibility_share=decimal(item.get("developer_responsibility_share", 1)),
        government_responsibility_share=decimal(item.get("government_responsibility_share", 0)),
        eligible_net_sales_deduction_fraction=decimal(
            item.get("eligible_net_sales_deduction_fraction", 0)
        ),
        eligible_profit_share_cost_fraction=decimal(
            item.get("eligible_profit_share_cost_fraction", item.get("developer_responsibility_share", 1))
        ),
        is_direct_cost=strict_boolean(
            item.get("is_direct_cost", True),
            path=f"project.costs.{item.get('cost_id', '')}.is_direct_cost",
        ),
        expenditure_curve=parse_curve(
            item.get("expenditure_curve") or [],
            path=f"project.costs.{item.get('cost_id', '')}.expenditure_curve",
        ),
    )


def _stage_amount(item: dict[str, Any], stage: str, *, currency: str) -> Decimal:
    result = calculate_cost_item(_cost_input(item), currency=currency)
    if stage == "BASE_COST":
        return result.base_cost
    if stage == "CONTINGENCY_INCLUDED":
        return result.base_cost + result.contingency_amount
    if stage == "ESCALATED_TOTAL":
        return result.escalated_total_cost
    raise InputValidationError(
        f"Unsupported percentage basis stage: {stage}.",
        path=f"project.costs.{item.get('cost_id', '')}.percentage_basis_stage",
        code="COST_PERCENTAGE_STAGE_UNSUPPORTED",
    )


def _planning_context(project: dict[str, Any]) -> tuple[dict[str, Decimal], dict[str, dict[str, Decimal]]]:
    planning_raw = deepcopy(project.get("planning") or {})
    planning_raw["products"] = deepcopy(project.get("planning_products") or [])
    planning = calculate_planning(planning_input_from_dict(planning_raw))
    total_units = sum(
        (Decimal(product.unit_count) for product in planning.products if product.unit_count is not None),
        ZERO,
    )
    metrics = {
        "GROSS_LAND_AREA_SQM": planning.gross_land_area_sqm,
        "NET_LAND_AREA_SQM": planning.net_developable_land_sqm,
        "TOTAL_GFA_SQM": planning.total_gfa_sqm,
        "TOTAL_SELLABLE_AREA_SQM": planning.total_sellable_area_sqm,
        "MAXIMUM_FOOTPRINT_SQM": planning.maximum_footprint_sqm,
        "TOTAL_UNIT_COUNT": total_units,
    }
    product_metrics = {
        item.product_id: {
            "PRODUCT_GFA_SQM": item.gfa_sqm,
            "PRODUCT_SELLABLE_AREA_SQM": item.sellable_area_sqm,
            "PRODUCT_UNIT_COUNT": Decimal(item.unit_count or 0),
        }
        for item in planning.products
    }
    references = {
        "land_uses": planning.land_use_areas,
        "products": product_metrics,
    }
    return metrics, references


def _resolve_quantity(
    item: dict[str, Any],
    *,
    metrics: dict[str, Decimal],
    references: dict[str, dict[str, Any]],
) -> tuple[Decimal, str]:
    basis = str(item.get("quantity_basis") or "").upper()
    if basis not in SUPPORTED_QUANTITY_BASES:
        raise InputValidationError(
            f"Unsupported computed quantity basis: {basis or '<empty>'}.",
            path=f"project.costs.{item.get('cost_id', '')}.quantity_basis",
            code="COST_QUANTITY_BASIS_UNSUPPORTED",
        )
    reference_id = str(item.get("basis_reference_id") or "").strip()
    if basis in metrics:
        base = metrics[basis]
        label = basis
    elif basis == "LAND_USE_AREA_SQM":
        values = references["land_uses"]
        if reference_id not in values:
            raise InputValidationError(
                f"Unknown land-use reference {reference_id!r}.",
                path=f"project.costs.{item.get('cost_id', '')}.basis_reference_id",
                code="COST_LAND_USE_REFERENCE_UNKNOWN",
            )
        base = values[reference_id]
        label = f"LAND_USE_AREA_SQM:{reference_id}"
    elif basis in {"PRODUCT_GFA_SQM", "PRODUCT_SELLABLE_AREA_SQM", "PRODUCT_UNIT_COUNT"}:
        values = references["products"]
        if reference_id not in values:
            raise InputValidationError(
                f"Unknown planning-product reference {reference_id!r}.",
                path=f"project.costs.{item.get('cost_id', '')}.basis_reference_id",
                code="COST_PRODUCT_REFERENCE_UNKNOWN",
            )
        base = values[reference_id][basis]
        label = f"{basis}:{reference_id}"
    else:  # pragma: no cover - protected by supported set
        raise InputValidationError("Computed cost basis could not be resolved.")

    multiplier = decimal(
        item.get("basis_multiplier", 1),
        path=f"project.costs.{item.get('cost_id', '')}.basis_multiplier",
    )
    addition = decimal(
        item.get("basis_addition", 0),
        path=f"project.costs.{item.get('cost_id', '')}.basis_addition",
    )
    if multiplier < ZERO:
        raise InputValidationError(
            "Cost basis multiplier cannot be negative.",
            path=f"project.costs.{item.get('cost_id', '')}.basis_multiplier",
            code="COST_BASIS_MULTIPLIER_NEGATIVE",
        )
    quantity = base * multiplier + addition
    if quantity < ZERO:
        raise InputValidationError(
            "Resolved cost quantity cannot be negative.",
            path=f"project.costs.{item.get('cost_id', '')}",
            code="COST_RESOLVED_QUANTITY_NEGATIVE",
        )
    return quantity, label


def _apply_net_sales_deduction_governance(
    source: dict[str, Any],
    resolved: dict[str, Any],
    *,
    currency: str,
) -> dict[str, Any]:
    """Apply contract-level deduction caps before the strict kernel runs.

    The frozen kernel consumes a governed fraction.  A monetary cap is
    translated into the exact effective fraction of the item's escalated total,
    preserving monthly proportionality and exact aggregate reconciliation.
    """

    provisional = calculate_cost_item(_cost_input(resolved), currency=currency)
    requested_fraction = decimal(source.get("eligible_net_sales_deduction_fraction", 0))
    effective_fraction = requested_fraction
    cap_raw = source.get("eligible_net_sales_deduction_cap")
    cap = None if cap_raw in (None, "") else decimal(cap_raw)
    if cap is not None:
        if cap < ZERO:
            raise InputValidationError(
                "Net-sales deduction cap cannot be negative.",
                path=f"project.costs.{source.get('cost_id', '')}.eligible_net_sales_deduction_cap",
                code="NET_SALES_DEDUCTION_CAP_NEGATIVE",
            )
        if provisional.escalated_total_cost > ZERO:
            effective_fraction = min(requested_fraction, cap / provisional.escalated_total_cost)
        else:
            effective_fraction = ZERO
        resolved["eligible_net_sales_deduction_fraction"] = as_json_number(effective_fraction)
    return {
        "requested_fraction": as_json_number(requested_fraction),
        "effective_fraction": as_json_number(effective_fraction),
        "deduction_cap": as_json_number(cap),
        "eligible_deduction_total": as_json_number(provisional.escalated_total_cost * effective_fraction),
        "basis": str(source.get("net_sales_deduction_basis") or "PAID").upper(),
        "treatment": str(source.get("net_sales_deduction_treatment") or "NOT_DEDUCTIBLE").upper(),
        "category": str(source.get("net_sales_deduction_category") or "project_cost"),
        "cash_payer": str(source.get("cash_payer") or "DEVELOPER").upper(),
        "economic_bearer": str(source.get("economic_bearer") or "DEVELOPER").upper(),
        "reimbursable": bool(source.get("reimbursable")),
        "contract_rule": str(source.get("net_sales_deduction_contract_rule") or ""),
    }


def _resolution_record(
    source: dict[str, Any],
    resolved: dict[str, Any],
    *,
    method: str,
    basis_label: str,
    basis_amount: Decimal | None = None,
) -> dict[str, Any]:
    quantity = decimal(resolved.get("quantity", 0))
    unit_cost = decimal(resolved.get("unit_cost", 0))
    contingency = decimal(resolved.get("contingency_rate", 0))
    return {
        "cost_id": str(resolved.get("cost_id") or ""),
        "name": str(resolved.get("name") or resolved.get("cost_id") or ""),
        "category": str(resolved.get("category") or "UNCLASSIFIED"),
        "calculation_method": method,
        "basis_label": basis_label,
        "basis_reference_id": source.get("basis_reference_id"),
        "basis_amount": as_json_number(basis_amount),
        "resolved_quantity": as_json_number(quantity),
        "resolved_unit_cost": as_json_number(unit_cost),
        "resolved_base_cost": as_json_number(quantity * unit_cost),
        "resolved_base_plus_contingency": as_json_number(quantity * unit_cost * (ONE + contingency)),
        "quantity_unit": (
            "sqm"
            if any(token in basis_label for token in ("AREA", "GFA", "LAND", "FOOTPRINT"))
            else "unit"
            if "UNIT" in basis_label
            else "amount"
        ),
        "calculation_note": source.get("calculation_note"),
        "net_sales_deduction": deepcopy(source.get("_net_sales_deduction_resolution") or {}),
    }


def resolve_project_costs(
    project_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve extended cost rows into the frozen kernel cost contract.

    Returns ``(kernel_project, resolution_report)``.  When a project contains
    only legacy rows, the kernel project is numerically identical to the input.
    """

    project = normalize_project_snapshot(project_snapshot)
    mode = str(project.get("construction_cost_entry_mode") or "PRODUCT").upper()
    if mode == "PRODUCT":
        raw_costs = _product_construction_rows(project)
        # Product-level construction rates replace only the legacy structure,
        # MEP and fit-out rows that are explicitly covered by the product rate.
        # All other project costs (infrastructure, public facilities, permits,
        # professional fees, project management, marketing, etc.) remain part
        # of the project cost scope.  Earlier releases retained only rows marked
        # ``always_include_in_product_mode`` and therefore silently omitted the
        # majority of non-product costs from the monthly ledger.
        raw_costs.extend(
            deepcopy(item)
            for item in project.get("costs") or []
            if bool(item.get("always_include_in_product_mode"))
            or not bool(item.get("covered_by_product_construction"))
        )
    else:
        raw_costs = [
            deepcopy(item)
            for item in project.get("costs") or []
            if not bool(item.get("product_mode_only"))
        ]
    if not raw_costs:
        return project, {
            "cost_resolution_version": COST_RESOLUTION_VERSION,
            "status": "NO_COSTS",
            "items": [],
            "construction_cost_entry_mode": mode,
        }

    currency = str(project.get("reporting_currency") or "USD")
    metrics, references = _planning_context(project)
    resolved_by_id: dict[str, dict[str, Any]] = {}
    source_by_id: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    percent_items: list[dict[str, Any]] = []

    for index, source in enumerate(raw_costs):
        cost_id = str(source.get("cost_id") or "").strip()
        if not cost_id:
            raise InputValidationError(
                "Cost identifier cannot be empty.",
                path=f"project.costs[{index}].cost_id",
                code="COST_ID_EMPTY",
            )
        if cost_id in source_by_id:
            raise InputValidationError(
                f"Duplicate cost identifier: {cost_id}.",
                path="project.costs",
                code="DUPLICATE_COST_ID",
            )
        source_by_id[cost_id] = source
        method = str(source.get("calculation_method") or LEGACY_METHOD).upper()
        if method not in SUPPORTED_METHODS:
            raise InputValidationError(
                f"Unsupported cost calculation method: {method}.",
                path=f"project.costs[{index}].calculation_method",
                code="COST_CALCULATION_METHOD_UNSUPPORTED",
            )
        if method == PERCENT_METHOD:
            percent_items.append(source)
            continue

        resolved = {key: deepcopy(value) for key, value in source.items() if key not in COST_EXTENSION_KEYS}
        if method in FIXED_METHODS:
            fixed_value = source.get("fixed_amount")
            if fixed_value in (None, ""):
                fixed_value = source.get("unit_cost", 0)
            amount = decimal(
                fixed_value,
                path=f"project.costs[{index}].fixed_amount",
            )
            if amount < ZERO:
                raise InputValidationError(
                    "Fixed cost amount cannot be negative.",
                    path=f"project.costs[{index}].fixed_amount",
                    code="COST_FIXED_AMOUNT_NEGATIVE",
                )
            resolved["quantity"] = "1"
            resolved["unit_cost"] = as_json_number(amount)
            basis_label = "FIXED_AMOUNT"
        elif method == COMPUTED_METHOD:
            quantity, basis_label = _resolve_quantity(source, metrics=metrics, references=references)
            resolved["quantity"] = as_json_number(quantity)
            resolved["unit_cost"] = as_json_number(
                decimal(source.get("unit_cost", 0), path=f"project.costs[{index}].unit_cost")
            )
        else:
            # Preserve strings and values exactly for the legacy path.
            basis_label = "MANUAL_QUANTITY"

        # Calculation validates the fully resolved row before it enters kernel.
        source["_net_sales_deduction_resolution"] = _apply_net_sales_deduction_governance(
            source, resolved, currency=currency
        )
        calculate_cost_item(_cost_input(resolved), currency=currency)
        resolved_by_id[cost_id] = resolved
        records[cost_id] = _resolution_record(source, resolved, method=method, basis_label=basis_label)

    # Percentage subtotals deliberately exclude percentage-derived rows. This
    # makes the basis order-independent and prevents hidden circular compounding.
    base_pool = dict(resolved_by_id)

    def basis_amount(source: dict[str, Any]) -> tuple[Decimal | None, str]:
        cost_id = str(source.get("cost_id") or "")
        basis = str(source.get("percentage_basis") or "").upper()
        stage = str(source.get("percentage_basis_stage") or "BASE_COST").upper()
        if basis not in SUPPORTED_PERCENTAGE_BASES:
            raise InputValidationError(
                f"Unsupported percentage cost basis: {basis or '<empty>'}.",
                path=f"project.costs.{cost_id}.percentage_basis",
                code="COST_PERCENTAGE_BASIS_UNSUPPORTED",
            )
        if stage not in SUPPORTED_STAGES:
            raise InputValidationError(
                f"Unsupported percentage basis stage: {stage}.",
                path=f"project.costs.{cost_id}.percentage_basis_stage",
                code="COST_PERCENTAGE_STAGE_UNSUPPORTED",
            )
        reference = str(source.get("basis_reference_id") or "").strip()
        if basis == "COST_ITEM":
            referenced = resolved_by_id.get(reference)
            if referenced is None:
                return None, f"COST_ITEM:{reference}:{stage}"
            return _stage_amount(referenced, stage, currency=currency), f"COST_ITEM:{reference}:{stage}"

        eligible: list[dict[str, Any]] = []
        if basis == "DIRECT_COST":
            eligible = [item for item in base_pool.values() if bool(item.get("is_direct_cost", True))]
        elif basis == "HARD_COST":
            eligible = [item for item in base_pool.values() if str(item.get("category")) in HARD_COST_CATEGORIES]
        elif basis == "SOFT_COST":
            eligible = [item for item in base_pool.values() if str(item.get("category")) not in HARD_COST_CATEGORIES]
        elif basis == "CATEGORY":
            if not reference:
                raise InputValidationError(
                    "A category percentage basis requires basis_reference_id.",
                    path=f"project.costs.{cost_id}.basis_reference_id",
                    code="COST_CATEGORY_REFERENCE_REQUIRED",
                )
            eligible = [item for item in base_pool.values() if str(item.get("category")) == reference]
        elif basis == "ALL_COST":
            eligible = list(base_pool.values())
        amount = sum((_stage_amount(item, stage, currency=currency) for item in eligible), ZERO)
        return amount, f"{basis}:{reference or 'ALL'}:{stage}"

    pending = list(percent_items)
    for _ in range(len(pending) + 1):
        if not pending:
            break
        next_pending: list[dict[str, Any]] = []
        progressed = False
        for source in pending:
            cost_id = str(source["cost_id"])
            amount, label = basis_amount(source)
            if amount is None:
                next_pending.append(source)
                continue
            rate = decimal(
                source.get("percentage_rate", 0),
                path=f"project.costs.{cost_id}.percentage_rate",
            )
            if rate < ZERO:
                raise InputValidationError(
                    "Percentage-of-cost rate cannot be negative.",
                    path=f"project.costs.{cost_id}.percentage_rate",
                    code="COST_PERCENTAGE_RATE_NEGATIVE",
                )
            resolved = {key: deepcopy(value) for key, value in source.items() if key not in COST_EXTENSION_KEYS}
            resolved["quantity"] = "1"
            resolved["unit_cost"] = as_json_number(amount * rate)
            source["_net_sales_deduction_resolution"] = _apply_net_sales_deduction_governance(
                source, resolved, currency=currency
            )
            calculate_cost_item(_cost_input(resolved), currency=currency)
            resolved_by_id[cost_id] = resolved
            records[cost_id] = _resolution_record(
                source,
                resolved,
                method=PERCENT_METHOD,
                basis_label=label,
                basis_amount=amount,
            )
            progressed = True
        pending = next_pending
        if not progressed:
            break

    if pending:
        identifiers = ", ".join(str(item.get("cost_id")) for item in pending)
        raise InputValidationError(
            f"Percentage cost references are unresolved or circular: {identifiers}.",
            path="project.costs",
            code="COST_PERCENTAGE_REFERENCE_CYCLE",
        )

    # Preserve source ordering.
    project["costs"] = [resolved_by_id[str(item["cost_id"])] for item in raw_costs]
    ordered_records = [records[str(item["cost_id"])] for item in raw_costs]
    present_amounts: dict[str, Decimal] = {}
    for record in ordered_records:
        category = str(record.get("category") or "UNCLASSIFIED").upper()
        present_amounts[category] = present_amounts.get(category, ZERO) + decimal(
            record.get("resolved_base_plus_contingency", 0)
        )
    land_use_shares = {
        str(item.get("land_use_id") or "").upper(): decimal(item.get("share", 0))
        for item in (project.get("planning") or {}).get("land_uses") or []
    }
    required_scope: list[dict[str, Any]] = [
        {
            "scope_id": "PRODUCT_CONSTRUCTION",
            "required": bool(project.get("products")),
            "categories": ["PRODUCT_CONSTRUCTION", "DIRECT_CONSTRUCTION"],
            "label_ar": "إنشاء المنتجات",
            "label_en": "Product construction",
        },
        {
            "scope_id": "INTERNAL_INFRASTRUCTURE",
            "required": any(land_use_shares.get(key, ZERO) > ZERO for key in ("ROADS", "ROADS_MOVEMENT", "CIRCULATION")),
            "categories": ["INFRASTRUCTURE"],
            "label_ar": "الطرق والبنية التحتية الداخلية",
            "label_en": "Internal roads and infrastructure",
        },
        {
            "scope_id": "PUBLIC_FACILITIES",
            "required": any(land_use_shares.get(key, ZERO) > ZERO for key in ("PUBLIC", "PUBLIC_FACILITIES", "SERVICES")),
            "categories": ["PUBLIC_FACILITIES"],
            "label_ar": "المرافق العامة",
            "label_en": "Public facilities",
        },
    ]
    scope_rows: list[dict[str, Any]] = []
    for scope in required_scope:
        amount = sum((present_amounts.get(category, ZERO) for category in scope["categories"]), ZERO)
        present = amount > ZERO
        scope_rows.append({**scope, "present": present, "amount_before_escalation": as_json_number(amount)})
    missing_required = [row for row in scope_rows if row["required"] and not row["present"]]
    total_base_plus_contingency = sum(
        (decimal(item.get("resolved_base_plus_contingency", 0)) for item in ordered_records), ZERO
    )

    report = {
        "cost_resolution_version": COST_RESOLUTION_VERSION,
        "status": "RESOLVED_WITH_SCOPE_WARNINGS" if missing_required else "RESOLVED",
        "methodology": (
            "Extended cost bases are resolved into explicit quantity × unit-cost rows before the frozen "
            "feasibility kernel runs. Percentage subtotal bases exclude percentage-derived rows to prevent "
            "circular compounding."
        ),
        "planning_bases": {key: as_json_number(value) for key, value in metrics.items()},
        "land_use_areas": {key: as_json_number(value) for key, value in references["land_uses"].items()},
        "product_bases": {
            product_id: {key: as_json_number(value) for key, value in values.items()}
            for product_id, values in references["products"].items()
        },
        "items": ordered_records,
        "construction_cost_entry_mode": mode,
        "total_base_plus_contingency": as_json_number(total_base_plus_contingency),
        "scope_coverage": {
            "status": "INCOMPLETE" if missing_required else "COMPLETE",
            "rows": scope_rows,
            "missing_required_scope_ids": [row["scope_id"] for row in missing_required],
            "message_ar": (
                "توجد بنود نطاق مطلوبة بلا كلفة مدخلة. يجب إدخالها أو توثيق أنها صفر قبل الاعتماد."
                if missing_required else "تم تمثيل نطاقات الكلفة الأساسية المطلوبة في النموذج."
            ),
            "message_en": (
                "Required cost scope is missing. Enter the cost or document a zero amount before reliance."
                if missing_required else "All required core cost scopes are represented in the model."
            ),
        },
    }
    return project, report
