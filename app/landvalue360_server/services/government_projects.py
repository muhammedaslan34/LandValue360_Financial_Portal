"""Government-owned project workflow built on the shared LandValue360 engine.

The Landowner interface accepts a governed, detailed input model.
This service converts that input into the canonical shared-project snapshot used
by the Developer Edition and unified monthly engine, while preserving the
validated Landowner input for traceability and future editing.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from landvalue360_government.decision import run_government_decision
from landvalue360_kernel.costs import calculate_costs

from ..audit import record_audit
from ..context import AuthContext
from ..costing import cost_inputs_from_dicts, resolve_project_costs
from ..enums import ProjectKind
from ..errors import ConflictError, NotFoundError
from ..json_tools import sha256_json
from ..models import PolicyPackVersion, Project, ProjectVersion
from ..project_normalization import normalize_project_snapshot
from ..web_defaults import default_project_snapshot
from ..unified_engine import run_unified_financial_engine
from .government import create_government_case
from .policies import policy_applies_to, policy_is_effective, policy_product_scope, policy_type, require_operational_policy
from .projects import clone_project_version, create_project, create_project_version
from .snapshot_validation import validate_project_snapshot_structure
from .tenant import get_policy_version, get_project, get_project_version, require_tenant_context, tenant_clause

from landvalue360_common.versions import LANDOWNER_INPUT_SCHEMA
CURRENT_LANDOWNER_INPUT_SCHEMA = LANDOWNER_INPUT_SCHEMA
LEGACY_LANDOWNER_INPUT_SCHEMAS = {f"government-simple-v{number}" for number in range(1, 17)}
SUPPORTED_LANDOWNER_INPUT_SCHEMAS = {CURRENT_LANDOWNER_INPUT_SCHEMA, *LEGACY_LANDOWNER_INPUT_SCHEMAS}
ZERO = Decimal("0")
ONE = Decimal("1")
ONE_HUNDRED = Decimal("100")

COST_TREATMENT_KEYS = (
    "BUILDING", "INFRA_INTERNAL", "INFRA_EXTERNAL", "PUBLIC_FACILITIES",
    "PERMITS", "DESIGN", "PROJECT_MANAGEMENT", "MARKETING",
)
RESPONSIBILITY_FIELD_BY_TREATMENT = {
    "BUILDING": "building_developer_share_percent",
    "INFRA_INTERNAL": "internal_infrastructure_developer_share_percent",
    "INFRA_EXTERNAL": "external_infrastructure_developer_share_percent",
    "PUBLIC_FACILITIES": "public_facilities_developer_share_percent",
    "PERMITS": "permits_developer_share_percent",
    "DESIGN": "professional_fees_developer_share_percent",
    "PROJECT_MANAGEMENT": "project_management_developer_share_percent",
    "MARKETING": "marketing_developer_share_percent",
}
COST_TREATMENT_BY_COST_ID = {
    "STRUCTURE": "BUILDING", "MEP": "BUILDING", "FITOUT": "BUILDING",
    "INFRA_INTERNAL": "INFRA_INTERNAL", "INFRA_EXTERNAL": "INFRA_EXTERNAL",
    "PUBLIC_FACILITIES": "PUBLIC_FACILITIES", "PERMITS": "PERMITS",
    "DESIGN": "DESIGN", "PM": "PROJECT_MANAGEMENT", "MARKETING": "MARKETING",
}


def _default_cost_treatments(simple: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source = simple or {}
    rows: list[dict[str, Any]] = []
    for key in COST_TREATMENT_KEYS:
        developer_share = _d(source.get(RESPONSIBILITY_FIELD_BY_TREATMENT[key]), "100")
        cash_payer = "DEVELOPER" if developer_share == ONE_HUNDRED else "PUBLIC_AUTHORITY" if developer_share == ZERO else "SHARED"
        rows.append({
            "cost_key": key,
            "cash_payer": cash_payer,
            "economic_bearer": cash_payer,
            "developer_cash_share_percent": _fmt(developer_share),
            "developer_economic_share_percent": _fmt(developer_share),
            "reimbursable": False,
            "developer_advances_landowner_share": False,
            "advance_recovery_method": "FIRST_LANDOWNER_DISTRIBUTIONS",
            "advance_recovery_priority": 50,
            "deduction_treatment": "NOT_DEDUCTIBLE",
            "deduction_percentage": "0",
            "deduction_cap": None,
            "deduction_basis": "PAID",
            "approval_required": False,
            "approval_obtained": False,
            "evidence_required": False,
            "evidence_status": "NOT_REQUIRED",
            "related_party": False,
            "market_test_required": False,
            "market_test_passed": False,
            "public_borne_deduction_authorized": False,
            "include_in_profit_share_base": True,
            "deduction_category": "project_cost",
            "contract_rule": "",
            "notes": "",
        })
    return rows


def _normalize_cost_treatments(simple: dict[str, Any]) -> list[dict[str, Any]]:
    supplied = {str(row.get("cost_key") or "").upper(): deepcopy(row) for row in (simple.get("cost_treatments") or [])}
    defaults = {row["cost_key"]: row for row in _default_cost_treatments(simple)}
    result: list[dict[str, Any]] = []
    for key in COST_TREATMENT_KEYS:
        row = defaults[key]
        supplied_row = supplied.get(key) or {}
        row.update(supplied_row)
        # Accept the two short-lived v0.25/v0.26 browser field names while
        # persisting only the canonical advance_* contract.  This prevents
        # previously saved drafts from silently reverting to the default
        # recovery waterfall.
        if "advance_recovery_method" not in supplied_row and supplied_row.get("recovery_method") not in (None, ""):
            row["advance_recovery_method"] = supplied_row.get("recovery_method")
        if "advance_recovery_priority" not in supplied_row and supplied_row.get("recovery_priority") not in (None, ""):
            row["advance_recovery_priority"] = supplied_row.get("recovery_priority")
        row.pop("recovery_method", None)
        row.pop("recovery_priority", None)
        row["cost_key"] = key
        # Compatibility rule: templates before v0.16 already contained a
        # default all-developer treatment list.  If a legacy responsibility
        # percentage was edited without touching that default row, the legacy
        # percentage remains authoritative.  Explicit non-default v0.16 rows
        # remain authoritative and the UI writes both representations.
        legacy_share = _d(simple.get(RESPONSIBILITY_FIELD_BY_TREATMENT[key]), "100")
        supplied_is_default = bool(supplied_row) and all([
            str(supplied_row.get("cash_payer") or "DEVELOPER") == "DEVELOPER",
            str(supplied_row.get("economic_bearer") or "DEVELOPER") == "DEVELOPER",
            _d(supplied_row.get("developer_cash_share_percent"), "100") == ONE_HUNDRED,
            _d(supplied_row.get("developer_economic_share_percent"), "100") == ONE_HUNDRED,
            str(supplied_row.get("deduction_treatment") or "NOT_DEDUCTIBLE") == "NOT_DEDUCTIBLE",
            not bool(supplied_row.get("reimbursable")),
            not bool(supplied_row.get("developer_advances_landowner_share")),
            not bool(supplied_row.get("approval_required")),
            not bool(supplied_row.get("evidence_required")),
            not bool(supplied_row.get("related_party")),
            not bool(supplied_row.get("public_borne_deduction_authorized")),
        ])
        if not supplied_row or (supplied_is_default and legacy_share != ONE_HUNDRED):
            payer = "DEVELOPER" if legacy_share == ONE_HUNDRED else "PUBLIC_AUTHORITY" if legacy_share == ZERO else "SHARED"
            row["cash_payer"] = payer
            row["economic_bearer"] = payer
            row["developer_cash_share_percent"] = _fmt(legacy_share)
            row["developer_economic_share_percent"] = _fmt(legacy_share)
        for field in ("developer_cash_share_percent", "developer_economic_share_percent", "deduction_percentage", "deduction_cap"):
            if isinstance(row.get(field), Decimal):
                row[field] = _fmt(row[field])
        row["developer_advances_landowner_share"] = bool(row.get("developer_advances_landowner_share"))
        row["advance_recovery_method"] = str(row.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper()
        row["advance_recovery_priority"] = int(row.get("advance_recovery_priority") or 50)
        treatment = str(row.get("deduction_treatment") or "NOT_DEDUCTIBLE").upper()
        row["deduction_treatment"] = treatment
        if treatment == "FULL":
            row["deduction_percentage"] = "100"
        elif treatment == "NOT_DEDUCTIBLE":
            row["deduction_percentage"] = "0"
            row["deduction_cap"] = None
        result.append(row)
    return result


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConflictError("GOVERNMENT_INPUT_INVALID_NUMBER", f"Invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ConflictError("GOVERNMENT_INPUT_INVALID_NUMBER", "Landowner project inputs must be finite numbers.")
    return result


def _fmt(value: Decimal) -> str:
    return format(+value, "f")


def _optional_d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return _d(value)


def _first_present(*values: Any) -> Any:
    """Return the first non-missing value while preserving a valid numeric zero."""

    for value in values:
        if value not in (None, ""):
            return value
    return None


def _fraction(percent: Any) -> str:
    return _fmt(_d(percent) / ONE_HUNDRED)


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(value.day, month_lengths[month - 1]))


def _dated_curve(base: date, start_month: int, duration_months: int) -> list[dict[str, str]]:
    """Build a normalized dated curve that remains inside the entered duration."""

    start = _add_months(base, max(0, start_month - 1))
    duration = max(1, int(duration_months))
    if duration == 1:
        points = ((0, "1"),)
    elif duration == 2:
        points = ((0, "0.50"), (1, "0.50"))
    elif duration == 3:
        points = ((0, "0.25"), (1, "0.50"), (2, "0.25"))
    else:
        last = duration - 1
        offsets = (0, max(1, last // 3), max(2, (last * 2) // 3), last)
        points = tuple(zip(offsets, ("0.15", "0.30", "0.35", "0.20")))
    return [{"date": _add_months(start, offset).isoformat(), "weight": weight} for offset, weight in points]


def _product_name(code: str) -> str:
    return {
        "RESIDENTIAL": "Residential",
        "RETAIL": "Retail",
        "OFFICE": "Office",
        "HOSPITALITY": "Hospitality / serviced units",
        "INDUSTRIAL": "Industrial",
        "OTHER": "Other",
    }.get(code, code.title())



def _spread_installments(*, upfront_percent: Decimal, months: int) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    if upfront_percent > ZERO:
        rules.append({"lag_months": 0, "weight_percent": _fmt(upfront_percent), "label": "Upfront"})
    remaining = ONE_HUNDRED - upfront_percent
    if remaining <= ZERO:
        return rules
    monthly = remaining / Decimal(months)
    allocated = ZERO
    for month in range(1, months + 1):
        weight = remaining - allocated if month == months else monthly
        allocated += weight
        rules.append({"lag_months": month, "weight_percent": _fmt(weight), "label": f"Installment {month}"})
    return rules


def _collection_plan_rows(simple: dict[str, Any]) -> list[dict[str, str]]:
    code = str(simple.get("collection_plan_code") or "LEGACY_THREE_POINT").upper()
    construction_end = int(simple.get("construction_start_month") or 1) + int(simple.get("construction_duration_months") or 1) - 1
    sales_start = int(simple.get("sales_start_month") or 1)
    delivery_lag = max(0, construction_end - sales_start)
    if code == "CASH":
        rows = [{"lag_months": 0, "weight_percent": "100", "label": "Cash"}]
    elif code == "DOWN_20_INSTALLMENTS_24":
        rows = _spread_installments(upfront_percent=Decimal("20"), months=24)
    elif code == "DOWN_30_HANDOVER_70":
        rows = [{"lag_months": 0, "weight_percent": "30", "label": "Down payment"}, {"lag_months": delivery_lag, "weight_percent": "70", "label": "Handover"}]
    elif code == "DOWN_30_MONTH6_HANDOVER_40":
        rows = [{"lag_months": 0, "weight_percent": "30", "label": "Down payment"}, {"lag_months": 6, "weight_percent": "30", "label": "Month 6"}, {"lag_months": delivery_lag, "weight_percent": "40", "label": "Handover"}]
    elif code == "DOWN_40_INSTALLMENTS_12":
        rows = _spread_installments(upfront_percent=Decimal("40"), months=12)
    elif code == "DOWN_50_INSTALLMENTS_24":
        rows = _spread_installments(upfront_percent=Decimal("50"), months=24)
    elif code == "CONSTRUCTION_LINKED":
        duration = max(1, int(simple.get("construction_duration_months") or 1))
        rows = [
            {"lag_months": 0, "weight_percent": "20", "label": "Contract"},
            {"lag_months": max(1, duration // 4), "weight_percent": "20", "label": "25% construction"},
            {"lag_months": max(1, duration // 2), "weight_percent": "25", "label": "50% construction"},
            {"lag_months": max(1, (duration * 3) // 4), "weight_percent": "20", "label": "75% construction"},
            {"lag_months": max(delivery_lag, duration), "weight_percent": "15", "label": "Handover"},
        ]
    elif code == "CUSTOM":
        rows = [
            {
                "lag_months": max(0, int(row.get("lag_months") or 0)),
                "weight_percent": _fmt(_d(row.get("weight_percent"))),
                "label": str(row.get("label") or "Custom payment"),
            }
            for row in (simple.get("collection_custom_rules") or [])
        ]
    else:
        rows = [
            {"lag_months": 0, "weight_percent": _fmt(_d(simple.get("collection_upfront_percent"))), "label": "Immediate"},
            {"lag_months": 6, "weight_percent": _fmt(_d(simple.get("collection_six_month_percent"))), "label": "Month 6"},
            {"lag_months": 12, "weight_percent": _fmt(_d(simple.get("collection_twelve_month_percent"))), "label": "Month 12"},
        ]
    combined: dict[int, Decimal] = {}
    labels: dict[int, list[str]] = {}
    for row in rows:
        lag = max(0, int(row.get("lag_months") or 0))
        weight = _d(row.get("weight_percent"))
        combined[lag] = combined.get(lag, ZERO) + weight
        labels.setdefault(lag, []).append(str(row.get("label") or ""))
    normalized = [
        {"lag_months": lag, "weight_percent": _fmt(weight), "label": " / ".join(filter(None, labels.get(lag, [])))}
        for lag, weight in sorted(combined.items()) if weight > ZERO
    ]
    total = sum((_d(row["weight_percent"]) for row in normalized), ZERO)
    if abs(total - ONE_HUNDRED) > Decimal("0.01"):
        raise ConflictError("GOVERNMENT_COLLECTION_TOTAL_INVALID", f"Collection plan must total 100%; current total is {total}%.")
    return normalized


def _reference_land_area(simple: dict[str, Any]) -> Decimal:
    gross = _d(simple.get("gross_land_area_sqm"))
    net = max(ZERO, gross - _d(simple.get("excluded_land_area_sqm")))
    investment = gross * _d(simple.get("investment_land_share_percent")) / ONE_HUNDRED
    basis = str(simple.get("reference_land_value_basis") or "GROSS").upper()
    return {"GROSS": gross, "NET": net, "INVESTMENT": investment}.get(basis, gross)


def _policy_public_discount_rate(policy_snapshot: dict[str, Any]) -> Decimal:
    financial = policy_snapshot.get("financial_constraints") or {}
    raw = financial.get("government_discount_rate")
    if raw in (None, ""):
        raise ConflictError(
            "VALUATION_POLICY_DISCOUNT_RATE_REQUIRED",
            "The selected valuation policy must define financial_constraints.government_discount_rate.",
        )
    value = _d(raw)
    return value / ONE_HUNDRED if value > ONE else value

def government_project_template(*, today: date | None = None) -> dict[str, Any]:
    """Return the governed detailed Landowner project input template used by the API and UI."""

    stamp = (today or date.today()).isoformat()
    template = {
        "input_status": "DEMO_NOT_VALIDATED",
        "valuation_date": stamp, "base_date": stamp, "currency": "USD", "basis_of_value": "MARKET_VALUE",
        "gross_land_area_sqm": "10000", "excluded_land_area_sqm": "0", "far_land_basis": "NET", "bcr_land_basis": "NET", "far": "2.25", "bcr_percent": "35", "maximum_storeys": "0",
        "reference_land_value_per_sqm": "250", "reference_land_value_basis": "GROSS", "reference_land_value_total": "2500000",
        "land_value_baseline": "2500000", "existing_use_value": "2500000", "alternative_use_value": "2500000",
        "land_value_evidence_classification": "DEMO_ASSUMPTION", "existing_use_evidence_classification": "DEMO_ASSUMPTION", "alternative_use_evidence_classification": "DEMO_ASSUMPTION",
        "investment_land_share_percent": "55", "roads_land_share_percent": "22", "green_land_share_percent": "13", "public_land_share_percent": "10",
        "products": [
            {"product_code": "RESIDENTIAL", "name": "Residential", "gfa_share_percent": "68", "efficiency_percent": "82", "unit_price_per_sqm": "1440", "construction_cost_per_sqm": "560"},
            {"product_code": "RETAIL", "name": "Retail", "gfa_share_percent": "12", "efficiency_percent": "88", "unit_price_per_sqm": "2160", "construction_cost_per_sqm": "720"},
            {"product_code": "OFFICE", "name": "Office", "gfa_share_percent": "12", "efficiency_percent": "84", "unit_price_per_sqm": "1740", "construction_cost_per_sqm": "640"},
            {"product_code": "HOSPITALITY", "name": "Hospitality / serviced units", "gfa_share_percent": "8", "efficiency_percent": "78", "unit_price_per_sqm": "1920", "construction_cost_per_sqm": "850"},
        ],
        "sales_start_month": 7, "sales_duration_months": 36, "construction_start_month": 1, "construction_duration_months": 30,
        "collection_plan_code": "DOWN_30_MONTH6_HANDOVER_40", "collection_custom_rules": [],
        "collection_upfront_percent": "30", "collection_six_month_percent": "30", "collection_twelve_month_percent": "40",
        "annual_escalation_percent": "4", "contingency_percent": "10",
        "internal_infrastructure_cost_per_sqm": "250",
        "internal_infrastructure_quantity_basis": "ROADS_AREA",
        "internal_infrastructure_fixed_quantity_sqm": "0",
        # Site works are measured on public-facility land. Building works are
        # entered separately on public-facility built GFA.
        "public_facility_cost_per_sqm": "750",
        "public_facility_building_cost_per_sqm": "0",
        "public_facility_far": "0", "public_facility_built_area_sqm": "0",
        "external_infrastructure_amount": "400000", "permits_and_fees_amount": "300000",
        "professional_fees_percent": "4.5", "project_management_percent": "5.9", "marketing_percent_of_revenue": "3",
        "building_developer_share_percent": "100",
        "internal_infrastructure_developer_share_percent": "100",
        "external_infrastructure_developer_share_percent": "100",
        "public_facilities_developer_share_percent": "100",
        "permits_developer_share_percent": "100",
        "professional_fees_developer_share_percent": "100",
        "project_management_developer_share_percent": "100",
        "marketing_developer_share_percent": "100",
        "opening_cash": "0", "committed_equity": "3500000", "equity_commitment_mode": "DECLARED_COMMITMENT",
        "committed_financing": "0", "annual_interest_rate_percent": "0",
        "partnership_method": "GROSS_SALES", "offered_share_percent": "10", "upfront_amount": "0",
        "minimum_guarantee_amount": "2500000", "minimum_guarantee_payment_month": 48,
        "minimum_guarantee_underlying_method": "GROSS_SALES", "minimum_guarantee_underlying_share_percent": "5",
        "public_discount_rate_percent": None, "data_confidence_percent": "65", "contract_enforceability_percent": "65",
        "planning_status": "Current planning assumptions supplied by the landowner.",
        "title_assumptions": "Landowner title is assumed valid and subject to legal verification.",
        "encumbrances": "No undisclosed encumbrances assumed; legal verification required.",
        "infrastructure_obligations": "Developer obligations are modeled from the stated cost and responsibility inputs.",
        "hybrid_variable_basis": "GROSS_SALES",
    }
    template["cost_treatments"] = _default_cost_treatments(template)
    return template


def _normalize_landowner_input(simple: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(simple)
    # Ensure JSON-stable scalar representation for hashes and persistence.
    for key, value in list(normalized.items()):
        if isinstance(value, Decimal):
            normalized[key] = _fmt(value)
        elif isinstance(value, date):
            normalized[key] = value.isoformat()
    products: list[dict[str, Any]] = []
    for row in normalized.get("products") or []:
        item = deepcopy(row)
        for key, value in list(item.items()):
            if isinstance(value, Decimal):
                item[key] = _fmt(value)
        products.append(item)
    normalized["products"] = products
    gross_land = _d(normalized.get("gross_land_area_sqm"))
    legacy_total = _d(normalized.get("land_value_baseline"))
    normalized.setdefault("reference_land_value_basis", "GROSS")
    basis = str(normalized.get("reference_land_value_basis") or "GROSS").upper()
    normalized["reference_land_value_basis"] = basis
    rate = _optional_d(normalized.get("reference_land_value_per_sqm"))
    direct_total = _optional_d(normalized.get("reference_land_value_total"))
    area = _reference_land_area(normalized)
    if basis == "DIRECT_TOTAL":
        total = max(ZERO, direct_total if direct_total is not None else legacy_total)
        rate = total / area if area > ZERO else ZERO
    else:
        if rate is None:
            rate = legacy_total / area if area > ZERO else ZERO
            normalized["reference_land_value_legacy_derived"] = True
        total = max(ZERO, rate) * max(ZERO, area)
    normalized["reference_land_value_per_sqm"] = _fmt(max(ZERO, rate or ZERO))
    normalized["reference_land_value_total"] = _fmt(total)
    normalized["land_value_baseline"] = _fmt(total)
    # These compatibility values remain in the stored schema because the
    # valuation engine consumes them; they are no longer separate UI inputs.
    normalized["existing_use_value"] = _fmt(total)
    normalized["alternative_use_value"] = _fmt(total)
    normalized.setdefault("data_confidence_percent", "65")
    normalized.setdefault("contract_enforceability_percent", "65")
    normalized.setdefault("internal_infrastructure_quantity_basis", "ROADS_AREA")
    normalized.setdefault("internal_infrastructure_fixed_quantity_sqm", "0")
    normalized.setdefault("public_facility_building_cost_per_sqm", "0")
    normalized.setdefault("public_facility_far", "0")
    normalized.setdefault("public_facility_built_area_sqm", "0")
    normalized.setdefault("collection_plan_code", "LEGACY_THREE_POINT")
    normalized.setdefault("collection_custom_rules", [])
    normalized["internal_infrastructure_quantity_basis"] = str(
        normalized.get("internal_infrastructure_quantity_basis") or "ROADS_AREA"
    ).upper()
    normalized["cost_treatments"] = _normalize_cost_treatments(normalized)
    normalized["hybrid_variable_basis"] = str(normalized.get("hybrid_variable_basis") or "GROSS_SALES").upper()
    return normalized


def _validate_landowner_input(simple: dict[str, Any]) -> None:
    try:
        valuation_date = date.fromisoformat(str(simple.get("valuation_date"))[:10])
        base_date = date.fromisoformat(str(simple.get("base_date"))[:10])
    except (TypeError, ValueError) as exc:
        raise ConflictError("GOVERNMENT_DATES_INVALID", "Valuation date and base date must be valid ISO dates.") from exc
    if base_date > valuation_date:
        raise ConflictError("GOVERNMENT_BASE_DATE_INVALID", "Base date cannot be after the valuation date.")
    currency = str(simple.get("currency") or "").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ConflictError("GOVERNMENT_CURRENCY_INVALID", "Currency must be a three-letter code.")
    gross = _d(simple.get("gross_land_area_sqm"))
    excluded = _d(simple.get("excluded_land_area_sqm"))
    if gross <= ZERO or excluded < ZERO or excluded >= gross:
        raise ConflictError("GOVERNMENT_LAND_AREA_INVALID", "Gross land area must be positive and greater than excluded land area.")
    land_total = sum(
        (
            _d(simple.get("investment_land_share_percent")),
            _d(simple.get("roads_land_share_percent")),
            _d(simple.get("green_land_share_percent")),
            _d(simple.get("public_land_share_percent")),
        ),
        ZERO,
    )
    if abs(land_total - ONE_HUNDRED) > Decimal("0.01"):
        raise ConflictError("GOVERNMENT_LAND_USE_TOTAL_INVALID", f"Land-use shares must total 100%; current total is {land_total}%.")
    far = _d(simple.get("far"))
    if far <= ZERO or far > Decimal("20"):
        raise ConflictError("GOVERNMENT_FAR_INVALID", "FAR must be greater than zero and no more than 20.")
    for field in ("investment_land_share_percent", "roads_land_share_percent", "green_land_share_percent", "public_land_share_percent"):
        value = _d(simple.get(field))
        if value < ZERO or value > ONE_HUNDRED:
            raise ConflictError("GOVERNMENT_LAND_USE_SHARE_INVALID", f"{field} must be between 0 and 100%.")
    bcr = _d(simple.get("bcr_percent"))
    if bcr <= ZERO or bcr > ONE_HUNDRED:
        raise ConflictError("GOVERNMENT_BCR_INVALID", "BCR must be greater than zero and no more than 100%.")
    for field in ("far_land_basis", "bcr_land_basis"):
        basis = str(simple.get(field) or "NET").upper()
        if basis not in {"GROSS", "NET", "INVESTMENT"}:
            raise ConflictError(
                "GOVERNMENT_LAND_BASIS_INVALID",
                f"{field} must be GROSS, NET, or INVESTMENT.",
            )
    maximum_storeys = _d(simple.get("maximum_storeys"))
    if maximum_storeys < ZERO or maximum_storeys > Decimal("300"):
        raise ConflictError("GOVERNMENT_STOREYS_INVALID", "maximum_storeys must be between 0 and 300; zero means not evidenced.")
    responsibility_fields = (
        "building_developer_share_percent",
        "internal_infrastructure_developer_share_percent",
        "external_infrastructure_developer_share_percent",
        "public_facilities_developer_share_percent",
        "permits_developer_share_percent",
        "professional_fees_developer_share_percent",
        "project_management_developer_share_percent",
        "marketing_developer_share_percent",
    )
    for field in responsibility_fields:
        value = _d(simple.get(field), "100")
        if value < ZERO or value > ONE_HUNDRED:
            raise ConflictError(
                "GOVERNMENT_COST_RESPONSIBILITY_INVALID",
                f"{field} must be between 0 and 100%.",
            )
    schedule_limits = {
        "sales_start_month": (1, 240),
        "sales_duration_months": (1, 360),
        "construction_start_month": (1, 240),
        "construction_duration_months": (1, 360),
    }
    for field, (low, high) in schedule_limits.items():
        try:
            value = int(simple.get(field))
        except (TypeError, ValueError) as exc:
            raise ConflictError("GOVERNMENT_SCHEDULE_INVALID", f"{field} must be an integer.") from exc
        if value < low or value > high:
            raise ConflictError("GOVERNMENT_SCHEDULE_INVALID", f"{field} must be between {low} and {high}.")

    for field in (
        "reference_land_value_per_sqm", "land_value_baseline", "existing_use_value", "alternative_use_value",
        "internal_infrastructure_cost_per_sqm", "internal_infrastructure_fixed_quantity_sqm",
        "public_facility_cost_per_sqm", "public_facility_building_cost_per_sqm",
        "public_facility_built_area_sqm", "public_facility_far", "external_infrastructure_amount", "permits_and_fees_amount",
        "opening_cash", "committed_equity", "committed_financing", "upfront_amount", "minimum_guarantee_amount",
    ):
        if _d(simple.get(field)) < ZERO:
            raise ConflictError("GOVERNMENT_INPUT_NEGATIVE", f"{field} cannot be negative.")
    for field in (
        "collection_upfront_percent", "collection_six_month_percent", "collection_twelve_month_percent",
        "annual_escalation_percent", "contingency_percent", "professional_fees_percent",
        "project_management_percent", "marketing_percent_of_revenue", "annual_interest_rate_percent",
        "offered_share_percent", "minimum_guarantee_underlying_share_percent", "public_discount_rate_percent", "data_confidence_percent", "contract_enforceability_percent",
    ):
        value = _d(simple.get(field))
        if value < ZERO or value > ONE_HUNDRED:
            raise ConflictError("GOVERNMENT_PERCENTAGE_INVALID", f"{field} must be between 0 and 100%.")
    method = str(simple.get("partnership_method") or "").upper()
    if method not in {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"}:
        raise ConflictError("GOVERNMENT_PARTNERSHIP_METHOD_INVALID", f"Unsupported Government partnership method: {method or 'empty'}.")
    try:
        guarantee_month = int(simple.get("minimum_guarantee_payment_month") or 48)
    except (TypeError, ValueError) as exc:
        raise ConflictError("GOVERNMENT_GUARANTEE_MONTH_INVALID", "Minimum-guarantee payment month must be an integer.") from exc
    if guarantee_month < 1 or guarantee_month > 600:
        raise ConflictError("GOVERNMENT_GUARANTEE_MONTH_INVALID", "Minimum-guarantee payment month must be between 1 and 600.")
    if str(simple.get("minimum_guarantee_underlying_method") or "GROSS_SALES").upper() not in {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE"}:
        raise ConflictError("GOVERNMENT_GUARANTEE_METHOD_INVALID", "Unsupported minimum-guarantee underlying method.")
    internal_basis = str(simple.get("internal_infrastructure_quantity_basis") or "ROADS_AREA").upper()
    if internal_basis not in {"ROADS_AREA", "GROSS_LAND", "NET_LAND", "INVESTMENT_LAND", "FIXED_QUANTITY"}:
        raise ConflictError("GOVERNMENT_INFRA_BASIS_INVALID", "Unsupported internal-infrastructure quantity basis.")
    if internal_basis == "FIXED_QUANTITY" and _d(simple.get("internal_infrastructure_fixed_quantity_sqm")) <= ZERO:
        raise ConflictError("GOVERNMENT_INFRA_QUANTITY_REQUIRED", "A positive fixed infrastructure quantity is required when FIXED_QUANTITY is selected.")
    if _d(simple.get("public_facility_building_cost_per_sqm")) > ZERO and _d(simple.get("public_facility_far")) <= ZERO and _d(simple.get("public_facility_built_area_sqm")) <= ZERO:
        raise ConflictError("GOVERNMENT_PUBLIC_FACILITY_BUILT_AREA_REQUIRED", "A positive public-facility built area is required when a building cost rate is entered.")

    products = list(simple.get("products") or [])
    if not products:
        raise ConflictError("GOVERNMENT_PRODUCTS_REQUIRED", "At least one development product is required.")
    codes = [str(row.get("product_code") or "").upper() for row in products]
    if any(not code for code in codes) or len(codes) != len(set(codes)):
        raise ConflictError("GOVERNMENT_PRODUCT_CODE_INVALID", "Product codes must be non-empty and unique.")
    positive_products = [row for row in products if _d(row.get("gfa_share_percent")) > ZERO]
    if not positive_products:
        raise ConflictError("GOVERNMENT_PRODUCTS_REQUIRED", "At least one product must have a positive GFA share.")
    for row in products:
        code = str(row.get("product_code") or "product")
        share = _d(row.get("gfa_share_percent"))
        efficiency = _d(row.get("efficiency_percent"))
        if share < ZERO or share > ONE_HUNDRED:
            raise ConflictError("GOVERNMENT_PRODUCT_SHARE_INVALID", f"Product {code} GFA share must be between 0 and 100%.")
        if efficiency <= ZERO or efficiency > ONE_HUNDRED:
            raise ConflictError("GOVERNMENT_PRODUCT_EFFICIENCY_INVALID", f"Product {code} efficiency must be greater than 0 and no more than 100%.")
    for row in positive_products:
        code = str(row.get("product_code") or "product")
        if _d(row.get("unit_price_per_sqm")) <= ZERO:
            raise ConflictError("GOVERNMENT_PRODUCT_PRICE_INVALID", f"Product {code} must have a positive unit price.")
        if _d(row.get("construction_cost_per_sqm")) <= ZERO:
            raise ConflictError("GOVERNMENT_PRODUCT_COST_INVALID", f"Product {code} must have a positive construction cost.")
    product_total = sum((_d(row.get("gfa_share_percent")) for row in products), ZERO)
    if abs(product_total - ONE_HUNDRED) > Decimal("0.01"):
        raise ConflictError("GOVERNMENT_PRODUCT_TOTAL_INVALID", f"Product GFA shares must total 100%; current total is {product_total}%.")
    collection_rows = _collection_plan_rows(simple)
    collection_total = sum((_d(row.get("weight_percent")) for row in collection_rows), ZERO)
    if abs(collection_total - ONE_HUNDRED) > Decimal("0.01"):
        raise ConflictError("GOVERNMENT_COLLECTION_TOTAL_INVALID", f"Collection percentages must total 100%; current total is {collection_total}%.")
    cost_rows = _normalize_cost_treatments(simple)
    if {row["cost_key"] for row in cost_rows} != set(COST_TREATMENT_KEYS):
        raise ConflictError("GOVERNMENT_COST_TREATMENTS_INVALID", "All Government cost-treatment categories must be present exactly once.")
    equity_mode = str(simple.get("equity_commitment_mode") or "DECLARED_COMMITMENT").upper()
    if equity_mode not in {"DECLARED_COMMITMENT", "POLICY_SCREENING"}:
        raise ConflictError("GOVERNMENT_EQUITY_MODE_INVALID", "Equity commitment mode must be DECLARED_COMMITMENT or POLICY_SCREENING.")
    method = str(simple.get("partnership_method") or "GROSS_SALES").upper()
    hybrid_basis = str(simple.get("hybrid_variable_basis") or "GROSS_SALES").upper()
    if hybrid_basis not in {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE"}:
        raise ConflictError("GOVERNMENT_HYBRID_BASIS_INVALID", "Unsupported hybrid variable basis.")
    net_sales_active = method == "NET_SALES" or (method == "HYBRID" and hybrid_basis == "NET_SALES")
    for row in cost_rows:
        cash_share = _d(row.get("developer_cash_share_percent"), "100")
        economic_share = _d(row.get("developer_economic_share_percent"), "100")
        if cash_share < ZERO or cash_share > ONE_HUNDRED or economic_share < ZERO or economic_share > ONE_HUNDRED:
            raise ConflictError("GOVERNMENT_COST_SHARE_INVALID", f"Cash and economic shares for {row['cost_key']} must be between 0 and 100%.")
        row["developer_advances_landowner_share"] = bool(row.get("developer_advances_landowner_share"))
        row["advance_recovery_method"] = str(row.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper()
        row["advance_recovery_priority"] = int(row.get("advance_recovery_priority") or 50)
        treatment = str(row.get("deduction_treatment") or "NOT_DEDUCTIBLE").upper()
        if treatment not in {"NOT_DEDUCTIBLE", "FULL", "PERCENTAGE", "CAPPED", "CONDITIONAL"}:
            raise ConflictError("NET_SALES_DEDUCTION_TREATMENT_INVALID", f"Unsupported deduction treatment for {row['cost_key']}.")
        percentage = _d(row.get("deduction_percentage"))
        if percentage < ZERO or percentage > ONE_HUNDRED:
            raise ConflictError("NET_SALES_DEDUCTION_PERCENT_INVALID", f"Deduction percentage for {row['cost_key']} must be between 0 and 100%.")
        cap = _optional_d(row.get("deduction_cap"))
        if treatment == "CAPPED" and cap is None:
            raise ConflictError("NET_SALES_DEDUCTION_CAP_REQUIRED", f"A capped deduction for {row['cost_key']} requires a cap.")
        active = treatment != "NOT_DEDUCTIBLE" and percentage > ZERO
        if active and bool(row.get("approval_required")) and not bool(row.get("approval_obtained")):
            raise ConflictError("NET_SALES_DEDUCTION_APPROVAL_MISSING", f"Required approval is missing for {row['cost_key']}.")
        evidence_status = str(row.get("evidence_status") or "NOT_REQUIRED").upper()
        if active and bool(row.get("evidence_required")) and evidence_status not in {"PROVIDED", "VERIFIED"}:
            raise ConflictError("NET_SALES_DEDUCTION_EVIDENCE_MISSING", f"Required evidence is missing for {row['cost_key']}.")
        if active and bool(row.get("related_party")) and not bool(row.get("market_test_passed")):
            raise ConflictError("NET_SALES_RELATED_PARTY_MARKET_TEST_REQUIRED", f"A related-party deductible cost requires a passed market test for {row['cost_key']}.")
        developer_economic = _d(row.get("developer_economic_share_percent"), "100")
        if active and developer_economic < ONE_HUNDRED and not bool(row.get("public_borne_deduction_authorized")):
            raise ConflictError("PUBLIC_BORNE_COST_DOUBLE_BURDEN", f"{row['cost_key']} is borne partly by the landowner and cannot reduce the public net-sales base without explicit authorization.")


def preview_government_project_input(
    session: Session,
    *,
    context: AuthContext,
    project_name: str,
    landowner_input: dict[str, Any],
    policy_pack_version_id: str | None,
    partnership_method: str = "GROSS_SALES",
    hybrid_variable_basis: str = "GROSS_SALES",
    offered_share_percent: Decimal = ZERO,
) -> dict[str, Any]:
    """Return an authoritative live preview without persisting a project version."""

    snapshot, summary = build_government_project_snapshot(
        project_name=project_name, landowner_input=landowner_input
    )
    resolved_project, cost_report = resolve_project_costs(snapshot)
    cost_result = calculate_costs(
        cost_inputs_from_dicts(resolved_project.get("costs") or [], require_profit_share_eligibility=False),
        currency=str(resolved_project.get("reporting_currency") or "USD"),
    )

    opening_cash = _d(landowner_input.get("opening_cash"))
    manual_additional = _d(landowner_input.get("committed_equity"))
    equity_mode = str(landowner_input.get("equity_commitment_mode") or "DECLARED_COMMITMENT").upper()
    policy_payload: dict[str, Any] = {
        "status": "MANUAL",
        "version_id": None,
        "version_label": None,
        "policy_hash": None,
        "product_scope": None,
        "rule_mode": "MANUAL",
        "direct_cost_share": None,
    }
    recognized_total = opening_cash + manual_additional
    recognized_additional = manual_additional

    if equity_mode == "POLICY_SCREENING":
        if not policy_pack_version_id:
            policy_payload["status"] = "POLICY_REQUIRED"
            recognized_total = ZERO
            recognized_additional = ZERO
        else:
            policy = get_policy_version(session, context, policy_pack_version_id)
            if not policy_is_effective(policy):
                raise ConflictError(
                    "POLICY_NOT_OPERATIONAL",
                    "Select a published policy inside its effective period.",
                )
            if not policy_applies_to(policy.policy_snapshot, "LANDOWNER"):
                raise ConflictError(
                    "POLICY_SCOPE_MISMATCH",
                    "The selected policy does not apply to the Landowner Edition.",
                )
            funding_policy = policy.policy_snapshot.get("funding_policy")
            if not isinstance(funding_policy, dict):
                funding_policy = {}
            # POLICY_SCREENING is an explicit Landowner-project choice. It uses
            # the selected policy percentage even when the same policy allows
            # verified manual commitments in Developer Edition.
            rule_mode = "FIXED_PERCENT"
            configured_share = _d(
                funding_policy.get(
                    "fixed_equity_direct_cost_share",
                    (policy.policy_snapshot.get("financial_constraints") or {}).get(
                        "available_equity_direct_cost_share", "0.10"
                    ),
                ),
                "0.10",
            )
            policy_payload.update({
                "status": "READY",
                "version_id": policy.id,
                "version_label": policy.version_label,
                "policy_hash": policy.policy_hash,
                "product_scope": policy_product_scope(policy.policy_snapshot),
                "rule_mode": rule_mode,
                "direct_cost_share": _fmt(configured_share),
            })
            recognized_total = cost_result.developer_direct_cost * configured_share
            recognized_additional = max(recognized_total - opening_cash, ZERO)

    gross_sales = _d(summary.get("estimated_gross_revenue"))
    eligible_deductions = cost_result.eligible_net_sales_deduction_series.total
    eligible_net_sales = max(gross_sales - eligible_deductions, ZERO)
    method = str(partnership_method or "GROSS_SALES").upper()
    hybrid_basis = str(hybrid_variable_basis or "GROSS_SALES").upper()
    uses_net_sales = method == "NET_SALES" or (method == "HYBRID" and hybrid_basis == "NET_SALES")
    share_rate = _d(offered_share_percent) / ONE_HUNDRED
    share_base = eligible_net_sales if uses_net_sales else gross_sales

    return {
        "currency": resolved_project.get("reporting_currency") or "USD",
        "costs": {
            "total_escalated_cost": _fmt(cost_result.total_escalated_cost),
            "developer_total_cost": _fmt(cost_result.developer_total_cost),
            "developer_direct_cost": _fmt(cost_result.developer_direct_cost),
            "eligible_net_sales_deductions": _fmt(eligible_deductions),
            "resolution_status": cost_report.get("status"),
        },
        "equity": {
            "input_mode": equity_mode,
            "opening_cash": _fmt(opening_cash),
            "manual_additional_commitment": _fmt(manual_additional),
            "recognized_total_equity_capacity": _fmt(recognized_total),
            "recognized_additional_commitment": _fmt(recognized_additional),
            "policy": policy_payload,
        },
        "net_sales": {
            "contract_uses_net_sales": uses_net_sales,
            "eligible_gross_sales": _fmt(gross_sales),
            "eligible_deductions": _fmt(eligible_deductions),
            "eligible_net_sales": _fmt(eligible_net_sales),
            "offered_share_percent": _fmt(_d(offered_share_percent)),
            "estimated_landowner_share": _fmt(share_base * share_rate),
        },
        "derived_summary": summary,
    }


def build_government_project_snapshot(*, project_name: str, landowner_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Expand the governed Landowner input into a canonical engine snapshot."""

    simple = _normalize_landowner_input(landowner_input)
    _validate_landowner_input(simple)
    valuation_date = date.fromisoformat(str(simple["valuation_date"])[:10])
    base_date = date.fromisoformat(str(simple["base_date"])[:10])
    collection_plan_rows = _collection_plan_rows(simple)
    latest_collection_lag = max((int(row.get("lag_months") or 0) for row in collection_plan_rows), default=0)
    sales_end = int(simple.get("sales_start_month") or 1) + int(simple.get("sales_duration_months") or 1) - 1
    construction_end = int(simple.get("construction_start_month") or 1) + int(simple.get("construction_duration_months") or 1) - 1
    last_collection_month = sales_end + latest_collection_lag
    horizon_months = min(600, max(72, construction_end, last_collection_month) + 3)

    snapshot = default_project_snapshot(today=valuation_date)
    input_status = str(simple.get("input_status") or "UNVALIDATED").upper()
    snapshot["input_status"] = input_status
    snapshot["template_is_starting_point"] = input_status == "DEMO_NOT_VALIDATED"
    snapshot["template_financial_values_confirmed"] = input_status in {"VALIDATED", "APPROVED"}
    snapshot["project_name"] = project_name
    snapshot["reporting_currency"] = str(simple.get("currency") or "USD").upper()
    snapshot["valuation_date"] = valuation_date.isoformat()
    snapshot["land_value_baseline"] = _fmt(_d(simple.get("land_value_baseline")))
    reference_basis = str(simple.get("reference_land_value_basis") or "GROSS").upper()
    reference_area = _reference_land_area(simple)
    snapshot["reference_land_value_per_sqm"] = _fmt(_d(simple.get("reference_land_value_per_sqm")))
    snapshot["reference_land_value_basis"] = reference_basis
    snapshot["reference_land_value_area_sqm"] = _fmt(reference_area)
    snapshot["reference_land_value_total"] = _fmt(_d(simple.get("reference_land_value_total"), snapshot["land_value_baseline"]))
    snapshot["land_value_baseline"] = snapshot["reference_land_value_total"]
    snapshot["landowner_studio"]["horizon_months"] = horizon_months
    snapshot["valuation_context"] = {
        "basis_of_value": str(simple.get("basis_of_value") or "MARKET_VALUE"),
        "purpose": "ADVISORY_LAND_PARTNERSHIP_ANALYSIS",
        "cost_estimate_class": "CLASS_4",
        "design_maturity": "CONCEPT",
        "measurement_basis": "LANDOWNER_USER_INPUT",
        "valuation_standard": "ADVISORY_REFERENCE_INPUT",
        "base_date": base_date.isoformat(),
        "collection_plan_code": str(simple.get("collection_plan_code") or "LEGACY_THREE_POINT"),
        "collection_plan_rows": deepcopy(collection_plan_rows),
        "cash_flow_horizon_months": horizon_months,
        "source_input_schema": CURRENT_LANDOWNER_INPUT_SCHEMA,
    }

    planning = snapshot["planning"]
    planning["gross_land_area_sqm"] = _fmt(_d(simple["gross_land_area_sqm"]))
    planning["excluded_land_area_sqm"] = _fmt(_d(simple.get("excluded_land_area_sqm")))
    far_land_basis = str(simple.get("far_land_basis") or "NET").upper()
    bcr_land_basis = str(simple.get("bcr_land_basis") or "NET").upper()
    planning["far_land_basis"] = far_land_basis
    planning["bcr_land_basis"] = bcr_land_basis
    planning["far"] = _fmt(_d(simple["far"]))
    planning["bcr"] = _fraction(simple["bcr_percent"])
    planning["land_uses"] = [
        {"land_use_id": "INVESTMENT", "name": "Investment plots", "share": _fraction(simple["investment_land_share_percent"])},
        {"land_use_id": "ROADS", "name": "Roads and circulation", "share": _fraction(simple["roads_land_share_percent"])},
        {"land_use_id": "GREEN", "name": "Green and open space", "share": _fraction(simple["green_land_share_percent"])},
        {"land_use_id": "PUBLIC", "name": "Public facilities", "share": _fraction(simple["public_land_share_percent"])},
    ]

    sales_curve = _dated_curve(base_date, int(simple.get("sales_start_month") or 7), int(simple.get("sales_duration_months") or 36))
    collection_rules = [
        {"lag_days": int(row["lag_months"]) * 30, "weight": _fraction(row["weight_percent"])}
        for row in collection_plan_rows
    ]
    treatment_by_key = {row["cost_key"]: row for row in _normalize_cost_treatments(simple)}
    building_treatment = treatment_by_key["BUILDING"]
    building_economic_percent = _d(
        _first_present(building_treatment.get("developer_economic_share_percent"), simple.get("building_developer_share_percent")),
        "100",
    )
    building_advance = bool(building_treatment.get("developer_advances_landowner_share"))
    building_cash_percent = (
        ONE_HUNDRED
        if building_advance
        else _d(building_treatment.get("developer_cash_share_percent"), str(building_economic_percent))
    )
    building_deduction_treatment = str(building_treatment.get("deduction_treatment") or "NOT_DEDUCTIBLE").upper()
    building_deduction_fraction = (
        ZERO
        if building_deduction_treatment == "NOT_DEDUCTIBLE"
        else _d(building_treatment.get("deduction_percentage")) / ONE_HUNDRED
    )
    building_deduction_cap_total = (
        None
        if building_treatment.get("deduction_cap") in (None, "")
        else _d(building_treatment.get("deduction_cap"))
    )
    building_cost_weight_total = sum(
        (
            _d(item.get("gfa_share_percent"))
            * _d(item.get("construction_cost_per_sqm"))
            for item in simple["products"]
            if _d(item.get("gfa_share_percent")) > ZERO
        ),
        ZERO,
    )
    planning_products: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    for row in simple["products"]:
        if _d(row.get("gfa_share_percent")) <= ZERO:
            continue
        code = str(row["product_code"]).upper()
        name = str(row.get("name") or _product_name(code)).strip()
        planning_products.append(
            {
                "product_id": code,
                "name": name,
                "area_method": "GFA_ALLOCATION",
                "is_sellable": True,
                "efficiency": _fraction(row["efficiency_percent"]),
                "gfa_allocation_share": _fraction(row["gfa_share_percent"]),
            }
        )
        products.append(
            {
                "product_id": code,
                "name": name,
                "quantity_basis": "SELLABLE_AREA_SQM",
                "quantity_unit": "sqm",
                "unit_price": _fmt(_d(row["unit_price_per_sqm"])),
                "construction_cost_per_sqm": _fmt(_d(row["construction_cost_per_sqm"])),
                # The Landowner form is itself the explicit manual entry and
                # confirmation surface for these product rates.
                "construction_rate_confirmed": True,
                "sales_curve_type": "S_CURVE",
                "sales_start_month": int(simple.get("sales_start_month") or 7),
                "sales_duration_months": int(simple.get("sales_duration_months") or 36),
                "construction_curve_type": "BELL",
                "construction_start_month": int(simple.get("construction_start_month") or 1),
                "construction_duration_months": int(simple.get("construction_duration_months") or 30),
                "construction_developer_responsibility_share": _fraction(building_cash_percent),
                "construction_government_responsibility_share": _fraction(ONE_HUNDRED - building_cash_percent),
                "construction_developer_economic_share": _fraction(building_economic_percent),
                "construction_government_economic_share": _fraction(ONE_HUNDRED - building_economic_percent),
                "developer_advances_landowner_share": building_advance,
                "advance_recovery_method": str(building_treatment.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper(),
                "construction_net_sales_deduction_fraction": _fmt(building_deduction_fraction),
                # The Landowner form defines one aggregate construction cap.
                # Product construction is represented by several cost rows, so
                # allocate that cap proportionally instead of applying the
                # whole cap once per product.
                "eligible_net_sales_deduction_cap": (
                    None
                    if building_deduction_cap_total is None
                    else _fmt(
                        building_deduction_cap_total
                        * _d(row.get("gfa_share_percent"))
                        * _d(row.get("construction_cost_per_sqm"))
                        / building_cost_weight_total
                    )
                    if building_cost_weight_total > ZERO
                    else "0"
                ),
                "construction_profit_share_cost_fraction": (
                    "1" if bool(building_treatment.get("include_in_profit_share_base", True)) else "0"
                ),
                "net_sales_deduction_treatment": building_deduction_treatment,
                "net_sales_deduction_basis": str(building_treatment.get("deduction_basis") or "PAID").upper(),
                "net_sales_deduction_category": str(building_treatment.get("deduction_category") or "building_construction"),
                "net_sales_deduction_contract_rule": str(building_treatment.get("contract_rule") or ""),
                "net_sales_deduction_approval_required": bool(building_treatment.get("approval_required")),
                "net_sales_deduction_approval_obtained": bool(building_treatment.get("approval_obtained")),
                "net_sales_deduction_evidence_required": bool(building_treatment.get("evidence_required")),
                "net_sales_deduction_evidence_status": str(building_treatment.get("evidence_status") or "NOT_REQUIRED").upper(),
                "net_sales_deduction_related_party": bool(building_treatment.get("related_party")),
                "net_sales_deduction_market_test_required": bool(building_treatment.get("market_test_required")),
                "net_sales_deduction_market_test_passed": bool(building_treatment.get("market_test_passed")),
                "net_sales_deduction_public_borne_authorized": bool(building_treatment.get("public_borne_deduction_authorized")),
                "cash_payer": str(building_treatment.get("cash_payer") or "DEVELOPER").upper(),
                "economic_bearer": str(building_treatment.get("economic_bearer") or "DEVELOPER").upper(),
                "developer_economic_share": _fraction(
                    building_treatment.get("developer_economic_share_percent", "100")
                ),
                "reimbursable": bool(building_treatment.get("reimbursable")),
                "commercial_discount_rate": "0",
                "buyer_incentive_rate": "0",
                "refund_rate": "0",
                "buyer_incentive_net_sales_deduction_fraction": "1",
                "refund_net_sales_deduction_fraction": "1",
                "eligible_profit_share_revenue_fraction": "1",
                "sales_curve": deepcopy(sales_curve),
                "collection_rules": deepcopy(collection_rules),
            }
        )
    snapshot["planning_products"] = planning_products
    snapshot["products"] = products

    escalation = _fraction(simple.get("annual_escalation_percent"))
    contingency = _fraction(simple.get("contingency_percent"))
    # Product-mode construction is scheduled directly from the product rows in
    # the unified monthly engine.  Carry the global construction escalation and
    # contingency assumptions onto those rows; applying them only to legacy
    # category costs understated the dominant construction scope.
    for product in products:
        product["construction_escalation_rate"] = escalation
        product["construction_contingency_rate"] = contingency
        product["construction_cost_base_date"] = base_date.isoformat()
    expenditure_curve = _dated_curve(base_date, int(simple.get("construction_start_month") or 1), int(simple.get("construction_duration_months") or 30))
    gross_land = _d(simple["gross_land_area_sqm"])
    net_land = gross_land - _d(simple.get("excluded_land_area_sqm"))
    investment_land = gross_land * _d(simple["investment_land_share_percent"]) / ONE_HUNDRED
    roads_area = gross_land * _d(simple["roads_land_share_percent"]) / ONE_HUNDRED
    public_facility_land_area = gross_land * _d(simple["public_land_share_percent"]) / ONE_HUNDRED
    responsibility_field_by_cost = {
        cost_id: RESPONSIBILITY_FIELD_BY_TREATMENT[key]
        for cost_id, key in COST_TREATMENT_BY_COST_ID.items()
    }
    for cost in snapshot["costs"]:
        cost_id = str(cost.get("cost_id") or "")
        treatment_key = COST_TREATMENT_BY_COST_ID.get(cost_id)
        treatment = treatment_by_key.get(treatment_key or "") or {}
        responsibility_field = responsibility_field_by_cost.get(cost_id)
        if responsibility_field:
            developer_economic_percent = _d(
                _first_present(treatment.get("developer_economic_share_percent"), simple.get(responsibility_field)),
                "100",
            )
            developer_advance = bool(treatment.get("developer_advances_landowner_share"))
            developer_cash_percent = (
                ONE_HUNDRED
                if developer_advance
                else _d(treatment.get("developer_cash_share_percent"), str(developer_economic_percent))
            )
            cost["developer_responsibility_share"] = _fraction(developer_cash_percent)
            cost["government_responsibility_share"] = _fraction(ONE_HUNDRED - developer_cash_percent)
            cost["developer_economic_share"] = _fraction(developer_economic_percent)
            cost["government_economic_share"] = _fraction(ONE_HUNDRED - developer_economic_percent)
            cost["developer_advances_landowner_share"] = developer_advance
            cost["advance_recovery_method"] = str(treatment.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper()
            cost["advance_recovery_priority"] = int(treatment.get("advance_recovery_priority") or 50)
        deduction_treatment = str(treatment.get("deduction_treatment") or "NOT_DEDUCTIBLE").upper()
        deduction_percent = _d(treatment.get("deduction_percentage"))
        deduction_fraction = ZERO if deduction_treatment == "NOT_DEDUCTIBLE" else deduction_percent / ONE_HUNDRED
        cost["eligible_net_sales_deduction_fraction"] = _fmt(deduction_fraction)
        cost["eligible_net_sales_deduction_cap"] = (
            None if treatment.get("deduction_cap") in (None, "") else _fmt(_d(treatment.get("deduction_cap")))
        )
        cost["net_sales_deduction_treatment"] = deduction_treatment
        cost["net_sales_deduction_basis"] = str(treatment.get("deduction_basis") or "PAID").upper()
        cost["net_sales_deduction_category"] = str(treatment.get("deduction_category") or "project_cost")
        cost["net_sales_deduction_contract_rule"] = str(treatment.get("contract_rule") or "")
        cost["net_sales_deduction_approval_required"] = bool(treatment.get("approval_required"))
        cost["net_sales_deduction_approval_obtained"] = bool(treatment.get("approval_obtained"))
        cost["net_sales_deduction_evidence_required"] = bool(treatment.get("evidence_required"))
        cost["net_sales_deduction_evidence_status"] = str(treatment.get("evidence_status") or "NOT_REQUIRED").upper()
        cost["net_sales_deduction_related_party"] = bool(treatment.get("related_party"))
        cost["net_sales_deduction_market_test_required"] = bool(treatment.get("market_test_required"))
        cost["net_sales_deduction_market_test_passed"] = bool(treatment.get("market_test_passed"))
        cost["cash_payer"] = str(treatment.get("cash_payer") or "DEVELOPER").upper()
        cost["economic_bearer"] = str(treatment.get("economic_bearer") or "DEVELOPER").upper()
        cost["developer_economic_share"] = cost.get("developer_economic_share") or _fraction(treatment.get("developer_economic_share_percent", "100"))
        cost["government_economic_share"] = cost.get("government_economic_share") or _fraction(ONE_HUNDRED - _d(treatment.get("developer_economic_share_percent"), "100"))
        cost["developer_advances_landowner_share"] = bool(treatment.get("developer_advances_landowner_share"))
        cost["advance_recovery_method"] = str(treatment.get("advance_recovery_method") or "FIRST_LANDOWNER_DISTRIBUTIONS").upper()
        cost["advance_recovery_priority"] = int(treatment.get("advance_recovery_priority") or 50)
        cost["reimbursable"] = bool(treatment.get("reimbursable"))
        cost["eligible_profit_share_cost_fraction"] = "1" if bool(treatment.get("include_in_profit_share_base", True)) else "0"
        cost["base_date"] = base_date.isoformat()
        cost["escalation_rate"] = escalation
        cost["contingency_rate"] = contingency
        cost["expenditure_curve"] = deepcopy(expenditure_curve)
        if cost["cost_id"] == "INFRA_INTERNAL":
            cost["unit_cost"] = _fmt(_d(simple.get("internal_infrastructure_cost_per_sqm")))
            infra_basis = str(simple.get("internal_infrastructure_quantity_basis") or "ROADS_AREA").upper()
            if infra_basis == "ROADS_AREA":
                cost["calculation_method"] = "COMPUTED_QUANTITY_X_RATE"
                cost["quantity_basis"] = "LAND_USE_AREA_SQM"
                cost["basis_reference_id"] = "ROADS"
                cost["calculation_note"] = "Roads/circulation land area × adopted internal-infrastructure rate."
            elif infra_basis == "GROSS_LAND":
                cost["calculation_method"] = "COMPUTED_QUANTITY_X_RATE"
                cost["quantity_basis"] = "GROSS_LAND_AREA_SQM"
                cost.pop("basis_reference_id", None)
                cost["calculation_note"] = "Gross land area × adopted internal-infrastructure rate."
            elif infra_basis == "NET_LAND":
                cost["calculation_method"] = "COMPUTED_QUANTITY_X_RATE"
                cost["quantity_basis"] = "NET_LAND_AREA_SQM"
                cost.pop("basis_reference_id", None)
                cost["calculation_note"] = "Net land area × adopted internal-infrastructure rate."
            elif infra_basis == "INVESTMENT_LAND":
                cost["calculation_method"] = "COMPUTED_QUANTITY_X_RATE"
                cost["quantity_basis"] = "LAND_USE_AREA_SQM"
                cost["basis_reference_id"] = "INVESTMENT"
                cost["calculation_note"] = "Investment-land area × adopted internal-infrastructure rate."
            else:
                cost["calculation_method"] = "LEGACY_QUANTITY_X_RATE"
                cost["quantity"] = _fmt(_d(simple.get("internal_infrastructure_fixed_quantity_sqm")))
                cost.pop("quantity_basis", None)
                cost.pop("basis_reference_id", None)
                cost["calculation_note"] = "Entered fixed infrastructure quantity × adopted rate."
        elif cost["cost_id"] == "INFRA_EXTERNAL":
            amount = _fmt(_d(simple.get("external_infrastructure_amount")))
            cost["fixed_amount"] = amount
            cost["unit_cost"] = amount
        elif cost["cost_id"] == "PUBLIC_FACILITIES":
            cost["name"] = "Public-facility site works"
            cost["unit_cost"] = _fmt(_d(simple.get("public_facility_cost_per_sqm")))
            cost["calculation_method"] = "COMPUTED_QUANTITY_X_RATE"
            cost["quantity_basis"] = "LAND_USE_AREA_SQM"
            cost["basis_reference_id"] = "PUBLIC"
            cost["calculation_note"] = "Public-facility land area × site/land-works rate. Public buildings are modeled separately."
        elif cost["cost_id"] == "PERMITS":
            amount = _fmt(_d(simple.get("permits_and_fees_amount")))
            cost["fixed_amount"] = amount
            cost["unit_cost"] = amount
        elif cost["cost_id"] == "DESIGN":
            cost["percentage_rate"] = _fraction(simple.get("professional_fees_percent"))
        elif cost["cost_id"] == "PM":
            cost["percentage_rate"] = _fraction(simple.get("project_management_percent"))

    public_building_rate = _d(simple.get("public_facility_building_cost_per_sqm"))
    public_facility_far = _d(simple.get("public_facility_far"))
    legacy_public_built_area = _d(simple.get("public_facility_built_area_sqm"))
    public_built_area = public_facility_land_area * public_facility_far if public_facility_far > ZERO else legacy_public_built_area
    if public_building_rate > ZERO and public_built_area > ZERO:
        site_row = next((row for row in snapshot["costs"] if row.get("cost_id") == "PUBLIC_FACILITIES"), None)
        building_row = deepcopy(site_row or {})
        building_row.update({
            "cost_id": "PUBLIC_FACILITIES_BUILDINGS",
            "name": "Public-facility buildings",
            "category": "PUBLIC_FACILITIES",
            "calculation_method": "LEGACY_QUANTITY_X_RATE",
            "quantity": _fmt(public_built_area),
            "unit_cost": _fmt(public_building_rate),
            "calculation_note": "Public-facility land area × public-facility FAR × building construction rate.",
            "covered_by_product_construction": False,
            "always_include_in_product_mode": True,
        })
        building_row.pop("quantity_basis", None)
        building_row.pop("basis_reference_id", None)
        snapshot["costs"].append(building_row)

    basis_areas = {"GROSS": gross_land, "NET": net_land, "INVESTMENT": investment_land}
    far_basis_area = basis_areas[far_land_basis]
    bcr_basis_area = basis_areas[bcr_land_basis]
    total_gfa = far_basis_area * _d(simple["far"])
    maximum_footprint = bcr_basis_area * _d(simple["bcr_percent"]) / ONE_HUNDRED
    indicative_storeys = total_gfa / maximum_footprint if maximum_footprint > ZERO else ZERO
    maximum_storeys = _d(simple.get("maximum_storeys"))
    planning_feasibility_status = (
        "NOT_EVIDENCED"
        if maximum_storeys == ZERO
        else ("PASS" if indicative_storeys <= maximum_storeys + Decimal("0.000001") else "FAIL")
    )
    sellable_area = ZERO
    estimated_revenue = ZERO
    for row in simple["products"]:
        gfa = total_gfa * _d(row["gfa_share_percent"]) / ONE_HUNDRED
        sellable = gfa * _d(row["efficiency_percent"]) / ONE_HUNDRED
        sellable_area += sellable
        estimated_revenue += sellable * _d(row["unit_price_per_sqm"])
    marketing_amount = estimated_revenue * _d(simple.get("marketing_percent_of_revenue")) / ONE_HUNDRED
    marketing = next((row for row in snapshot["costs"] if row["cost_id"] == "MARKETING"), None)
    if marketing is not None:
        marketing["fixed_amount"] = _fmt(marketing_amount)
        marketing["unit_cost"] = _fmt(marketing_amount)

    snapshot["funding"] = {
        "committed_equity": _fmt(_d(simple.get("committed_equity"))),
        "equity_commitment_mode": str(simple.get("equity_commitment_mode") or "DECLARED_COMMITMENT").upper(),
        "committed_equity_is_additional": True,
        # Landowner Edition assesses delivery from opening cash, equity commitment
        # and sales collections. Bank debt belongs to Developer Edition only.
        "committed_financing": "0",
    }
    snapshot["finance_model"]["annual_interest_rate"] = "0"
    snapshot["finance_model"]["spend_policy"] = "HYBRID"
    snapshot["finance_model"]["allow_negative_cash"] = False
    snapshot["finance_model"]["defer_contractual_payments"] = True

    method = str(simple.get("partnership_method") or "GROSS_SALES").upper()
    share = _fraction(simple.get("offered_share_percent"))
    upfront = _fmt(_d(simple.get("upfront_amount")))
    snapshot["partnership"]["method"] = method
    snapshot["partnership"]["share_rate"] = share
    snapshot["partnership"]["manual_share"] = share
    snapshot["partnership"]["approved_selection"] = "MANUAL"
    snapshot["partnership"]["hybrid_variable_basis"] = str(simple.get("hybrid_variable_basis") or "GROSS_SALES").upper()
    snapshot["partnership"]["upfront_payments"] = ([{"date": base_date.isoformat(), "amount": upfront}] if _d(upfront) > ZERO else [])
    snapshot["landowner_studio"]["upfront_amount"] = upfront
    snapshot["landowner_studio"]["hybrid_upfront_amount"] = upfront
    snapshot["landowner_studio"]["minimum_guarantee_amount"] = _fmt(_d(simple.get("minimum_guarantee_amount")))
    snapshot["landowner_studio"]["minimum_guarantee_payment_month"] = int(simple.get("minimum_guarantee_payment_month") or horizon_months)
    snapshot["landowner_studio"]["minimum_guarantee_underlying_method"] = str(simple.get("minimum_guarantee_underlying_method") or "GROSS_SALES").upper()
    snapshot["landowner_studio"]["minimum_guarantee_underlying_share"] = _fraction(simple.get("minimum_guarantee_underlying_share_percent"))
    # Opening cash is a paid balance at the base date. Committed equity is an
    # undrawn funding capacity. Treating the commitment as opening cash counted
    # the same equity twice and overstated IRR/funding headroom.
    snapshot["landowner_studio"]["initial_cash"] = _fmt(_d(simple.get("opening_cash")))
    snapshot["landowner_studio"]["horizon_months"] = horizon_months
    snapshot = normalize_project_snapshot(snapshot)
    validate_project_snapshot_structure(snapshot)
    summary = {
        "net_land_area_sqm": _fmt(net_land),
        "total_gfa_sqm": _fmt(total_gfa),
        "estimated_sellable_area_sqm": _fmt(sellable_area),
        "estimated_gross_revenue": _fmt(estimated_revenue),
        "estimated_marketing_amount": _fmt(marketing_amount),
        "roads_area_sqm": _fmt(roads_area),
        "public_facilities_area_sqm": _fmt(public_facility_land_area),
        "internal_infrastructure_quantity_basis": str(simple.get("internal_infrastructure_quantity_basis") or "ROADS_AREA").upper(),
        "internal_infrastructure_fixed_quantity_sqm": _fmt(_d(simple.get("internal_infrastructure_fixed_quantity_sqm"))),
        "public_facility_site_cost_per_sqm": _fmt(_d(simple.get("public_facility_cost_per_sqm"))),
        "public_facility_far": _fmt(public_facility_far),
        "public_facility_built_area_sqm": _fmt(public_built_area),
        "public_facility_building_cost_per_sqm": _fmt(public_building_rate),
        "far_land_basis": far_land_basis,
        "bcr_land_basis": bcr_land_basis,
        "far_basis_area_sqm": _fmt(far_basis_area),
        "bcr_basis_area_sqm": _fmt(bcr_basis_area),
        "investment_land_area_sqm": _fmt(investment_land),
        "maximum_footprint_sqm": _fmt(maximum_footprint),
        "indicative_storeys": _fmt(indicative_storeys),
        "maximum_storeys": _fmt(maximum_storeys),
        "planning_feasibility_status": planning_feasibility_status,
        "opening_cash": _fmt(_d(simple.get("opening_cash"))),
        "committed_equity_capacity": _fmt(_d(simple.get("committed_equity"))),
        "land_use_area_basis": "GROSS",
        "product_count": len(products),
        "land_use_total_percent": "100",
        "product_allocation_total_percent": "100",
        "collection_plan_code": str(simple.get("collection_plan_code") or "LEGACY_THREE_POINT"),
        "collection_plan_rows": deepcopy(collection_plan_rows),
        "cash_flow_horizon_months": snapshot["landowner_studio"]["horizon_months"],
        "source_input_schema": CURRENT_LANDOWNER_INPUT_SCHEMA,
        "cost_treatments": deepcopy(_normalize_cost_treatments(simple)),
        "cost_responsibility": {
            key: _fmt(_d(simple.get(key), "100"))
            for key in (
                "building_developer_share_percent",
                "internal_infrastructure_developer_share_percent",
                "external_infrastructure_developer_share_percent",
                "public_facilities_developer_share_percent",
                "permits_developer_share_percent",
                "professional_fees_developer_share_percent",
                "project_management_developer_share_percent",
                "marketing_developer_share_percent",
            )
        },
    }
    return snapshot, summary




def _landowner_input_from_canonical_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project the canonical project snapshot into the governed Landowner form.

    The projection is deliberately lossless for the shared fields. Developer-only
    detail remains in the canonical snapshot and is preserved when the Landowner
    form is saved back through ``_merge_landowner_into_canonical``.
    """

    simple = government_project_template()
    planning = deepcopy(snapshot.get("planning") or {})
    land_uses = {str(row.get("land_use_id") or "").upper(): row for row in planning.get("land_uses") or []}
    products_by_id = {str(row.get("product_id") or ""): row for row in snapshot.get("products") or []}
    planning_products = snapshot.get("planning_products") or []
    simple.update({
        "input_status": str(snapshot.get("input_status") or simple.get("input_status") or "UNVALIDATED").upper(),
        "valuation_date": str(snapshot.get("valuation_date") or simple["valuation_date"])[:10],
        "base_date": str(snapshot.get("valuation_date") or simple["base_date"])[:10],
        "currency": str(snapshot.get("reporting_currency") or "USD"),
        "gross_land_area_sqm": str(planning.get("gross_land_area_sqm") or "0"),
        "excluded_land_area_sqm": str(planning.get("excluded_land_area_sqm") or "0"),
        "far_land_basis": str(planning.get("far_land_basis") or "NET"),
        "bcr_land_basis": str(planning.get("bcr_land_basis") or "NET"),
        "far": str(planning.get("far") or "0"),
        "bcr_percent": _fmt(_d(planning.get("bcr")) * ONE_HUNDRED),
        "reference_land_value_per_sqm": str(snapshot.get("reference_land_value_per_sqm") or "0"),
        "reference_land_value_basis": str(snapshot.get("reference_land_value_basis") or "GROSS"),
        "reference_land_value_total": str(snapshot.get("reference_land_value_total") or snapshot.get("land_value_baseline") or "0"),
        "land_value_baseline": str(snapshot.get("land_value_baseline") or snapshot.get("reference_land_value_total") or "0"),
        "investment_land_share_percent": _fmt(_d((land_uses.get("INVESTMENT") or land_uses.get("DEVELOPABLE") or {}).get("share")) * ONE_HUNDRED),
        "roads_land_share_percent": _fmt(_d((land_uses.get("ROADS") or {}).get("share")) * ONE_HUNDRED),
        "green_land_share_percent": _fmt(_d((land_uses.get("GREEN") or {}).get("share")) * ONE_HUNDRED),
        "public_land_share_percent": _fmt(_d((land_uses.get("PUBLIC") or {}).get("share")) * ONE_HUNDRED),
    })
    rows: list[dict[str, Any]] = []
    for planned in planning_products:
        pid = str(planned.get("product_id") or "PRODUCT")
        commercial = products_by_id.get(pid) or {}
        rows.append({
            "product_code": pid,
            "name": str(planned.get("name") or commercial.get("name") or pid),
            "gfa_share_percent": _fmt(_d(planned.get("gfa_allocation_share")) * ONE_HUNDRED),
            "efficiency_percent": _fmt(_d(planned.get("efficiency"), "1") * ONE_HUNDRED),
            "unit_price_per_sqm": str(commercial.get("unit_price") or "0"),
            "construction_cost_per_sqm": str(commercial.get("construction_cost_per_sqm") or "0"),
        })
    if rows:
        simple["products"] = rows
    all_products = list(products_by_id.values())
    if all_products:
        simple["sales_start_month"] = min(int(row.get("sales_start_month") or 1) for row in all_products)
        simple["sales_duration_months"] = max(int(row.get("sales_duration_months") or 1) for row in all_products)
        simple["construction_start_month"] = min(int(row.get("construction_start_month") or 1) for row in all_products)
        simple["construction_duration_months"] = max(int(row.get("construction_duration_months") or 1) for row in all_products)
        first_rules = all_products[0].get("collection_rules") or []
        if first_rules:
            simple["collection_plan_code"] = "CUSTOM"
            simple["collection_custom_rules"] = [
                {
                    "lag_months": max(0, int(round(int(rule.get("lag_days") or 0) / 30))),
                    "weight_percent": _fmt(_d(rule.get("weight")) * ONE_HUNDRED),
                    "label": f"Month {max(0, int(round(int(rule.get('lag_days') or 0) / 30)))}",
                } for rule in first_rules
            ]
    costs = {str(row.get("cost_id") or "").upper(): row for row in snapshot.get("costs") or []}
    def fixed(cost_id: str, default: str = "0") -> str:
        row = costs.get(cost_id) or {}
        return str(_first_present(row.get("fixed_amount"), row.get("unit_cost"), default))
    def pct(cost_id: str, default: str = "0") -> str:
        row = costs.get(cost_id) or {}
        return _fmt(_d(row.get("percentage_rate"), default) * ONE_HUNDRED)
    infra = costs.get("INFRA_INTERNAL") or {}
    public = costs.get("PUBLIC_FACILITIES") or {}
    simple.update({
        "internal_infrastructure_cost_per_sqm": str(infra.get("unit_cost") or "0"),
        "internal_infrastructure_quantity_basis": "ROADS_AREA" if str(infra.get("basis_reference_id") or "ROADS").upper()=="ROADS" else "FIXED_QUANTITY",
        "internal_infrastructure_fixed_quantity_sqm": str(infra.get("quantity") or "0"),
        "public_facility_cost_per_sqm": str(public.get("unit_cost") or "0"),
        "external_infrastructure_amount": fixed("INFRA_EXTERNAL"),
        "permits_and_fees_amount": fixed("PERMITS"),
        "professional_fees_percent": pct("DESIGN"),
        "project_management_percent": pct("PM"),
        "marketing_percent_of_revenue": pct("MARKETING"),
    })
    funding = snapshot.get("funding") or {}
    simple["opening_cash"] = str(funding.get("opening_cash") or "0")
    simple["committed_equity"] = str(_first_present(funding.get("committed_additional_equity"), funding.get("committed_equity"), "0"))
    simple["equity_commitment_mode"] = str(funding.get("equity_commitment_mode") or "DECLARED_COMMITMENT")
    simple["committed_financing"] = str(funding.get("committed_financing") or "0")
    partnership = snapshot.get("partnership") or {}
    simple["partnership_method"] = str(partnership.get("method") or "GROSS_SALES")
    simple["offered_share_percent"] = _fmt(_d(_first_present(partnership.get("manual_share"), partnership.get("share_rate"), "0")) * ONE_HUNDRED)
    simple["upfront_amount"] = str(partnership.get("manual_amount") or "0")
    simple["hybrid_variable_basis"] = str(partnership.get("hybrid_variable_basis") or "GROSS_SALES")
    treatments = []
    for row in snapshot.get("costs") or []:
        key = COST_TREATMENT_BY_COST_ID.get(str(row.get("cost_id") or "").upper())
        if not key:
            continue
        dev_economic = _d(row.get("developer_responsibility_share"), "1") * ONE_HUNDRED
        gov_economic = ONE_HUNDRED - dev_economic
        dev_cash = _d(row.get("developer_cash_share"), _fmt(dev_economic / ONE_HUNDRED)) * ONE_HUNDRED
        treatments.append({
            "cost_key": key,
            "cash_payer": "DEVELOPER" if dev_cash >= ONE_HUNDRED else "PUBLIC_AUTHORITY" if dev_cash <= ZERO else "SHARED",
            "economic_bearer": "DEVELOPER" if dev_economic >= ONE_HUNDRED else "PUBLIC_AUTHORITY" if dev_economic <= ZERO else "SHARED",
            "developer_cash_share_percent": _fmt(dev_cash),
            "developer_economic_share_percent": _fmt(dev_economic),
            "developer_advances_landowner_share": bool(dev_cash > dev_economic and gov_economic > ZERO),
            "advance_recovery_method": "FIRST_LANDOWNER_DISTRIBUTIONS",
            "deduction_treatment": "FULL" if _d(row.get("eligible_net_sales_deduction_fraction")) >= ONE else "NOT_DEDUCTIBLE",
            "deduction_percentage": _fmt(_d(row.get("eligible_net_sales_deduction_fraction")) * ONE_HUNDRED),
            "deduction_basis": "PAID",
            "reimbursable": False,
            "approval_required": False,
            "evidence_required": False,
            "related_party": False,
            "public_borne_deduction_authorized": False,
            "include_in_profit_share_base": bool(_d(row.get("eligible_profit_share_cost_fraction"), "1") > ZERO),
            "deduction_category": "project_cost",
            "contract_rule": "",
            "notes": "Projected from shared project workspace.",
        })
    if treatments:
        simple["cost_treatments"] = treatments
    return _normalize_landowner_input(simple)



def _unique_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Return deterministic unique rows while preserving first-seen order.

    Browser auto-save can submit the same aggregate row more than once.  The
    financial kernel correctly rejects duplicate identifiers, so the shared
    workspace normalizer collapses repeated rows before persistence.
    """

    order: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for raw in rows or []:
        row = deepcopy(raw)
        identifier = str(row.get(key) or "").strip()
        if not identifier:
            anonymous.append(row)
            continue
        if identifier not in by_id:
            order.append(identifier)
            by_id[identifier] = row
        else:
            by_id[identifier].update(row)
    return [by_id[identifier] for identifier in order] + anonymous


def _merge_product_rows(
    existing_rows: list[dict[str, Any]],
    derived_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        str(row.get("product_id") or ""): deepcopy(row)
        for row in _unique_rows(existing_rows, "product_id")
        if str(row.get("product_id") or "")
    }
    merged: list[dict[str, Any]] = []
    for row in _unique_rows(derived_rows, "product_id"):
        product_id = str(row.get("product_id") or "")
        base = deepcopy(existing.get(product_id) or {})
        base.update(deepcopy(row))
        merged.append(base)
    return merged


def _merge_planning_rows(
    existing_rows: list[dict[str, Any]],
    derived_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        str(row.get("product_id") or ""): deepcopy(row)
        for row in _unique_rows(existing_rows, "product_id")
        if str(row.get("product_id") or "")
    }
    developer_area_fields = {
        "area_method",
        "unit_count",
        "average_net_unit_area_sqm",
        "direct_gfa_sqm",
        "direct_sellable_area_sqm",
    }
    merged: list[dict[str, Any]] = []
    for row in _unique_rows(derived_rows, "product_id"):
        product_id = str(row.get("product_id") or "")
        old = deepcopy(existing.get(product_id) or {})
        preserved = {
            key: deepcopy(old[key])
            for key in developer_area_fields
            if key in old and str(old.get("area_method") or "").upper() in {"UNIT_MIX", "DIRECT_AREA"}
        }
        old.update(deepcopy(row))
        old.update(preserved)
        merged.append(old)
    return merged


def _merge_cost_rows(
    existing_rows: list[dict[str, Any]],
    derived_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        str(row.get("cost_id") or "").upper(): deepcopy(row)
        for row in _unique_rows(existing_rows, "cost_id")
        if str(row.get("cost_id") or "").strip()
    }
    order = list(existing)
    for row in _unique_rows(derived_rows, "cost_id"):
        identifier = str(row.get("cost_id") or "").upper()
        if not identifier:
            continue
        if identifier not in existing:
            order.append(identifier)
            existing[identifier] = deepcopy(row)
        else:
            existing[identifier].update(deepcopy(row))
    return [existing[identifier] for identifier in order]


def _merge_landowner_into_canonical(existing: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    """Merge explicit detailed Landowner edits into the shared project workspace."""

    existing = normalize_project_snapshot(existing or {})
    derived = normalize_project_snapshot(derived or {})
    result = deepcopy(existing)
    result.update({
        key: deepcopy(value)
        for key, value in derived.items()
        if key not in {
            "products", "planning_products", "costs", "risk_register",
            "sensitivity_studio", "tender_studio", "developer_product_cost_plans",
            "developer_cost_plans", "developer_market_strategy", "developer_cost_strategy",
        }
    })
    result["products"] = _merge_product_rows(existing.get("products") or [], derived.get("products") or [])
    result["planning_products"] = _merge_planning_rows(existing.get("planning_products") or [], derived.get("planning_products") or [])
    result["costs"] = _merge_cost_rows(existing.get("costs") or [], derived.get("costs") or [])
    if "developer_product_cost_plans" not in result and isinstance(result.get("developer_cost_plans"), dict):
        result["developer_product_cost_plans"] = deepcopy(result["developer_cost_plans"])
    result.pop("developer_cost_plans", None)
    for key in (
        "risk_register", "sensitivity_studio", "tender_studio",
        "developer_product_cost_plans", "developer_market_strategy", "developer_cost_strategy",
    ):
        if key in existing:
            result[key] = deepcopy(existing[key])
    return normalize_project_snapshot(result)


def _latest_version(session: Session, *, context: AuthContext, project_id: str) -> ProjectVersion:
    version = session.scalar(
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project_id, *tenant_clause(ProjectVersion, context))
        .order_by(ProjectVersion.version_number.desc())
        .limit(1)
    )
    if version is None:
        raise NotFoundError("Landowner project version not found.")
    return version


def list_government_projects(session: Session, *, context: AuthContext) -> list[dict[str, Any]]:
    projects = list(
        session.scalars(
            select(Project)
            .where(*tenant_clause(Project, context), Project.project_kind.in_([ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value]))
            .order_by(Project.updated_at.desc(), Project.name)
        ).all()
    )
    rows: list[dict[str, Any]] = []
    for project in projects:
        version = _latest_version(session, context=context, project_id=project.id)
        rows.append(
            {
                "id": project.id,
                "name": project.name,
                "code": project.code,
                "description": project.description,
                "status": project.status,
                "project_kind": project.project_kind,
                "latest_version_id": version.id,
                "version_number": version.version_number,
                "version_status": version.status,
                "input_hash": version.input_hash,
                "source_input_hash": version.source_input_hash,
                "updated_at": project.updated_at,
            }
        )
    return rows


def _government_project(session: Session, *, context: AuthContext, project_id: str) -> Project:
    project = get_project(session, context, project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Landowner project not found.")
    return project


def _summary_for_version(project: Project, version: ProjectVersion) -> dict[str, Any]:
    source = deepcopy(version.source_input_snapshot or {})
    if not source:
        source = _landowner_input_from_canonical_snapshot(version.input_snapshot or {})
    _snapshot, summary = build_government_project_snapshot(project_name=project.name, landowner_input=source)
    return summary


def get_government_project_detail(session: Session, *, context: AuthContext, project_id: str) -> tuple[Project, ProjectVersion, dict[str, Any]]:
    project = _government_project(session, context=context, project_id=project_id)
    version = _latest_version(session, context=context, project_id=project.id)
    return project, version, _summary_for_version(project, version)


def list_government_project_versions(session: Session, *, context: AuthContext, project_id: str) -> list[ProjectVersion]:
    project = _government_project(session, context=context, project_id=project_id)
    return list(
        session.scalars(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id, *tenant_clause(ProjectVersion, context))
            .order_by(ProjectVersion.version_number.desc())
        ).all()
    )


def clone_government_project_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    label: str | None,
    notes: str | None,
) -> tuple[Project, ProjectVersion, dict[str, Any]]:
    source = get_project_version(session, context, version_id)
    project = _government_project(session, context=context, project_id=source.project_id)
    clone = clone_project_version(
        session,
        context=context,
        version_id=source.id,
        label=label or f"Government working revision from v{source.version_number}",
        notes=notes or "Created from an immutable Landowner project baseline.",
    )
    if clone.source_input_schema not in SUPPORTED_LANDOWNER_INPUT_SCHEMAS or not clone.source_input_snapshot:
        clone.source_input_schema = CURRENT_LANDOWNER_INPUT_SCHEMA
        clone.source_input_snapshot = _landowner_input_from_canonical_snapshot(clone.input_snapshot or {})
    upgraded_source = deepcopy(government_project_template())
    upgraded_source.update(deepcopy(clone.source_input_snapshot or {}))
    clone.source_input_schema = CURRENT_LANDOWNER_INPUT_SCHEMA
    clone.source_input_snapshot = _normalize_landowner_input(upgraded_source)
    clone.source_input_hash = sha256_json(clone.source_input_snapshot)
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_PROJECT_REVISION_CREATED",
        entity_type="ProjectVersion",
        entity_id=clone.id,
        metadata={"project_id": project.id, "source_version_id": source.id},
    )
    return project, clone, _summary_for_version(project, clone)


def create_government_project(
    session: Session,
    *,
    context: AuthContext,
    name: str,
    code: str,
    description: str | None,
    landowner_input: dict[str, Any],
) -> tuple[Project, ProjectVersion, dict[str, Any]]:
    normalized = _normalize_landowner_input(landowner_input)
    snapshot, summary = build_government_project_snapshot(project_name=name.strip(), landowner_input=normalized)
    project = create_project(
        session,
        context=context,
        name=name,
        code=code,
        description=description,
        portfolio_id=None,
        project_kind=ProjectKind.SHARED.value,
    )
    version = create_project_version(
        session,
        context=context,
        project_id=project.id,
        input_snapshot=snapshot,
        label="Government simple assessment baseline",
        notes="Generated from validated Landowner interface simple inputs.",
        supersedes_version_id=None,
        source_input_schema=CURRENT_LANDOWNER_INPUT_SCHEMA,
        source_input_snapshot=normalized,
        source_input_hash=sha256_json(normalized),
    )
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_PROJECT_CREATED",
        entity_type="Project",
        entity_id=project.id,
        after={"project_kind": project.project_kind, "version_id": version.id, "input_hash": version.input_hash, "source_input_hash": version.source_input_hash},
    )
    return project, version, summary


def update_government_project(
    session: Session,
    *,
    context: AuthContext,
    project_id: str,
    name: str | None,
    description: str | None,
    landowner_input: dict[str, Any] | None,
    expected_input_hash: str | None = None,
) -> tuple[Project, ProjectVersion, dict[str, Any]]:
    project = get_project(session, context, project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Landowner project not found.")
    version = _latest_version(session, context=context, project_id=project.id)
    if version.status != "DRAFT":
        raise ConflictError("GOVERNMENT_PROJECT_VERSION_LOCKED", "Approved government project baselines are immutable; create a new version before editing.")
    if expected_input_hash is not None and expected_input_hash != version.input_hash:
        raise ConflictError(
            "PROJECT_VERSION_CONCURRENCY_CONFLICT",
            "The project version changed after it was loaded. Reload before saving.",
        )
    before = {"name": project.name, "description": project.description, "input_hash": version.input_hash, "source_input_hash": version.source_input_hash}
    if name is not None:
        project.name = name.strip()
    if description is not None:
        project.description = description
    source = _normalize_landowner_input(landowner_input) if landowner_input is not None else deepcopy(version.source_input_snapshot or _landowner_input_from_canonical_snapshot(version.input_snapshot or {}))
    derived_snapshot, summary = build_government_project_snapshot(project_name=project.name, landowner_input=source)
    derived_snapshot["project_id"] = project.id
    version.input_snapshot = _merge_landowner_into_canonical(version.input_snapshot or {}, derived_snapshot)
    version.input_hash = sha256_json(version.input_snapshot)
    version.source_input_schema = CURRENT_LANDOWNER_INPUT_SCHEMA
    version.source_input_snapshot = source
    version.source_input_hash = sha256_json(source)
    version.row_version = int(version.row_version or 0) + 1
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_PROJECT_UPDATED",
        entity_type="ProjectVersion",
        entity_id=version.id,
        before=before,
        after={"name": project.name, "description": project.description, "input_hash": version.input_hash, "source_input_hash": version.source_input_hash},
    )
    return project, version, summary


def _default_policy(
    session: Session,
    *,
    context: AuthContext,
    policy_version_id: str | None,
    expected_type: str = "PROJECT",
) -> PolicyPackVersion:
    organization_id, workspace_id = require_tenant_context(context)
    expected = str(expected_type or "PROJECT").upper()
    if policy_version_id:
        policy = session.scalar(
            select(PolicyPackVersion).where(
                PolicyPackVersion.id == policy_version_id,
                PolicyPackVersion.organization_id == organization_id,
                (PolicyPackVersion.workspace_id == workspace_id) | (PolicyPackVersion.workspace_id.is_(None)),
            )
        )
        if policy is None:
            raise ConflictError("GOVERNMENT_POLICY_REQUIRED", "The selected institutional policy is unavailable.")
        return require_operational_policy(policy, edition="LANDOWNER", expected_type=expected)

    candidates = list(
        session.scalars(
            select(PolicyPackVersion)
            .where(
                PolicyPackVersion.organization_id == organization_id,
                (PolicyPackVersion.workspace_id == workspace_id) | (PolicyPackVersion.workspace_id.is_(None)),
                PolicyPackVersion.status == "PUBLISHED",
            )
            .order_by(PolicyPackVersion.published_at.desc(), PolicyPackVersion.created_at.desc())
        ).all()
    )
    for policy in candidates:
        if (
            policy_is_effective(policy)
            and policy_applies_to(policy.policy_snapshot, "LANDOWNER")
            and policy_type(policy.policy_snapshot) == expected
        ):
            return policy
    raise ConflictError(
        "GOVERNMENT_POLICY_REQUIRED",
        f"No published and currently effective Landowner {expected.lower()} policy is available for this workspace.",
    )


def _compose_assessment_policy(project_policy: dict[str, Any], valuation_policy: dict[str, Any]) -> dict[str, Any]:
    """Overlay valuation-only policy fields onto the governed project policy."""

    merged = deepcopy(project_policy or {})
    valuation = valuation_policy or {}
    for key in ("share_policy", "fair_consideration_policy", "public_value_adjustment", "valuation_policy"):
        if isinstance(valuation.get(key), dict):
            merged[key] = deepcopy(valuation[key])
    project_financial = deepcopy(merged.get("financial_constraints") or {})
    valuation_financial = valuation.get("financial_constraints") if isinstance(valuation.get("financial_constraints"), dict) else {}
    for key in ("government_discount_rate", "discount_rate"):
        if valuation_financial.get(key) not in (None, ""):
            project_financial[key] = valuation_financial[key]
    for key in ("discount_rate_type", "discount_currency", "discount_compounding"):
        if valuation_financial.get(key) not in (None, ""):
            project_financial[key] = valuation_financial[key]
    merged["financial_constraints"] = project_financial
    merged["assessment_policy_sources"] = {
        "project_policy_id": project_policy.get("policy_id"),
        "project_policy_version": project_policy.get("version"),
        "valuation_policy_id": valuation_policy.get("policy_id"),
        "valuation_policy_version": valuation_policy.get("version"),
        "valuation_policy_effective_date": valuation_policy.get("effective_date"),
    }
    merged["valuation_policy_context"] = {
        "policy_id": valuation_policy.get("policy_id"),
        "policy_version": valuation_policy.get("version"),
        "effective_date": valuation_policy.get("effective_date"),
    }
    return merged


def _case_input_from_landowner(
    simple: dict[str, Any],
    *,
    mode: str,
    offered_share_percent: Any | None = None,
    upfront_amount: Any | None = None,
    public_discount_rate_percent: Any | None = None,
    partnership_method: str | None = None,
    policy_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate governed Landowner inputs into the explicit decision contract.

    This payload is preserved on the GovernmentCase and becomes part of the
    calculation input hash.  No financial term is inferred in the browser.
    """

    simple = _normalize_landowner_input(simple)
    _validate_landowner_input(simple)
    share = _d(offered_share_percent, str(simple.get("offered_share_percent") or "0"))
    upfront = _d(upfront_amount, str(simple.get("upfront_amount") or "0"))
    effective_policy = deepcopy(policy_snapshot or {})
    if not effective_policy:
        raise ConflictError(
            "VALUATION_POLICY_REQUIRED",
            "Select a complete valuation-policy version before running the assessment.",
        )
    discount_source = "VERSIONED_VALUATION_POLICY"
    policy_discount = _policy_public_discount_rate(effective_policy)
    method = str(partnership_method or simple.get("partnership_method") or "GROSS_SALES").upper()
    guarantee_amount = upfront if upfront_amount not in (None, "") else _d(simple.get("minimum_guarantee_amount"))
    guarantee_date = _add_months(
        date.fromisoformat(str(simple["base_date"])[:10]),
        max(0, int(simple.get("minimum_guarantee_payment_month") or 48) - 1),
    ).isoformat()
    underlying_method = str(simple.get("minimum_guarantee_underlying_method") or "GROSS_SALES").upper()
    underlying_type = {
        "GROSS_SALES": "GROSS_SALES_SHARE",
        "NET_SALES": "NET_SALES_SHARE",
        "PROFIT_SHARE": "PROFIT_SHARE",
    }[underlying_method]
    contract_map: dict[str, dict[str, Any]] = {
        "GROSS_SALES": {"type": "GROSS_SALES_SHARE", "rate": _fraction(share), "basis": "COLLECTED"},
        "NET_SALES": {"type": "NET_SALES_SHARE", "rate": _fraction(share), "basis": "COLLECTED"},
        "PROFIT_SHARE": {"type": "PROFIT_SHARE", "rate": _fraction(share), "loss_carryforward": True},
        "UPFRONT": {"type": "OUTRIGHT_SALE", "upfront_amount": _fmt(upfront)},
        "HYBRID": {
            "type": "HYBRID",
            "components": [
                {"component_id": "upfront", "type": "OUTRIGHT_SALE", "upfront_amount": _fmt(upfront)},
                {
                    "component_id": "variable",
                    "type": {
                        "GROSS_SALES": "GROSS_SALES_SHARE",
                        "NET_SALES": "NET_SALES_SHARE",
                        "PROFIT_SHARE": "PROFIT_SHARE",
                    }.get(str(simple.get("hybrid_variable_basis") or "GROSS_SALES").upper(), "GROSS_SALES_SHARE"),
                    "rate": _fraction(share),
                    "basis": "COLLECTED",
                },
            ],
        },
        "MINIMUM_GUARANTEE": {
            "type": "MINIMUM_GUARANTEE",
            "guarantee_amount": _fmt(guarantee_amount),
            "guarantee_date": guarantee_date,
            "underlying": {
                "type": underlying_type,
                "rate": _fraction(simple.get("minimum_guarantee_underlying_share_percent")),
                "basis": "COLLECTED",
                "loss_carryforward": True,
            },
        },
    }
    if method not in contract_map:
        raise ConflictError("GOVERNMENT_PARTNERSHIP_METHOD_INVALID", f"Unsupported Government partnership method: {method}.")

    valuation_date = str(simple["valuation_date"])[:10]
    base_date = str(simple["base_date"])[:10]
    land_value = _d(simple.get("land_value_baseline"))
    existing_input = _optional_d(simple.get("existing_use_value"))
    alternative_input = _optional_d(simple.get("alternative_use_value"))
    existing = existing_input if existing_input is not None else land_value * Decimal("0.65")
    alternative = alternative_input if alternative_input is not None else land_value
    existing_classification = str(
        simple.get("existing_use_evidence_classification")
        or ("TECHNICAL_PROXY" if existing_input is None else "USER_INPUT")
    ).upper()
    alternative_classification = str(
        simple.get("alternative_use_evidence_classification")
        or ("TECHNICAL_PROXY" if alternative_input is None else "USER_INPUT")
    ).upper()
    land_value_classification = str(
        simple.get("land_value_evidence_classification") or "USER_INPUT"
    ).upper()
    # Landowner Edition is an advisory financial model based on disclosed user
    # inputs. Evidence confidence remains available in the audit layer but does
    # not block the financial recommendation or create a synthetic LOW grade.
    confidence = "1"
    return {
        "mode": str(mode or "STRUCTURING").upper(),
        "partnership_method": method,
        "offered_share_percent": _fmt(share),
        "upfront_amount": _fmt(upfront),
        "public_discount_rate": _fmt(policy_discount),
        "public_discount_rate_source": discount_source,
        "valuation_policy_id": effective_policy.get("policy_id"),
        "valuation_policy_version": effective_policy.get("version"),
        "contract_enforceability_score": _fraction(simple.get("contract_enforceability_percent", "65")),
        "input_status": str(simple.get("input_status") or "UNVALIDATED").upper(),
        "valuation_basis": {
            "valuation_date": valuation_date,
            "base_date": base_date,
            "basis_of_value": str(simple.get("basis_of_value") or "MARKET_VALUE"),
            "currency": str(simple.get("currency") or "USD").upper(),
            "nominal_or_real": "NOMINAL",
            "tax_basis": "EXCLUSIVE_OF_TRANSACTION_TAXES",
            "title_and_ownership_assumptions": simple.get("title_assumptions"),
            "encumbrances": simple.get("encumbrances"),
            "planning_and_zoning_status": simple.get("planning_status"),
            "development_rights": "Development rights are limited to the entered FAR, BCR and product allocation.",
            "permitted_density": str(simple.get("far")),
            "infrastructure_obligations": simple.get("infrastructure_obligations"),
            "existing_use": "Existing-use value entered by the authority or derived as a disclosed proxy.",
            "alternative_use": "Proposed development programme represented by the approved project inputs.",
            "highest_and_best_use": "Conditionally supported subject to legal, physical, market and financial feasibility.",
            "special_assumptions": "No unconfirmed planning approval is represented as current market fact.",
            "extraordinary_assumptions": "None unless recorded in the case override register.",
            "market_evidence_date": valuation_date,
            "data_confidence": confidence,
            "material_valuation_uncertainty": "Material uncertainty remains where market, title, cost or planning evidence has not been independently verified.",
        },
        "valuation_methods": {
            "existing_use_value": {
                "value": _fmt(existing),
                "evidence_strength": "0.40",
                "details": {
                    "classification": existing_classification,
                    "proxy_used": existing_input is None,
                },
            },
            "alternative_use_value": {
                "value": _fmt(alternative),
                "evidence_strength": "0.45",
                "details": {
                    "classification": alternative_classification,
                    "proxy_used": alternative_input is None,
                },
            },
            "independent_appraisal": {
                "value": _fmt(land_value),
                "low": _fmt(land_value),
                "high": _fmt(land_value),
                "evidence_strength": confidence,
                "verified": False,
                "details": {
                    "classification": land_value_classification or "USER_ADVISORY_REFERENCE",
                    "synthetic_range": False,
                    "range_basis": "User-entered advisory reference benchmark; not represented as an independent market appraisal.",
                },
            },
            "reconciliation_weights": {
                "EXISTING_USE_VALUE": "0.35",
                "ALTERNATIVE_USE_VALUE": "0.45",
                "INDEPENDENT_APPRAISAL": "0.60",
            },
            "weight_reasons": {
                "EXISTING_USE_VALUE": "Existing-use evidence or an explicitly disclosed technical proxy.",
                "ALTERNATIVE_USE_VALUE": "Alternative-use input is conditional on legal and planning rights.",
                "INDEPENDENT_APPRAISAL": "Authority benchmark; the synthetic range is not treated as independent evidence unless the classification is independently verified.",
            },
        },
        "contract": contract_map[method],
    }


def _participation_grid(
    project_snapshot: dict[str, Any],
    policy_snapshot: dict[str, Any],
    simple: dict[str, Any],
    *,
    offered_share_percent: Any | None,
    upfront_amount: Any | None,
    partnership_method: str | None = None,
    native_negotiation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate a concise contract-native curve through the shared engine.

    The exact range boundaries are supplied by the Government decision engine.
    Additional midpoint/probe rows make the trade-off visible without running a
    large arbitrary grid.
    """

    simple = _normalize_landowner_input(simple)
    _validate_landowner_input(simple)
    method = str(partnership_method or simple.get("partnership_method") or "GROSS_SALES").upper()
    offered_share = _d(offered_share_percent, str(simple.get("offered_share_percent") or "0"))
    offered_upfront = _d(upfront_amount, str(simple.get("upfront_amount") or "0"))
    native = native_negotiation or {}
    boundary_names = ("minimum", "balanced", "risk_adjusted_ceiling", "technical_ceiling", "offer")
    boundary_values: dict[str, Decimal] = {
        name: _d((native.get(name) or {}).get("value"))
        for name in boundary_names
        if (native.get(name) or {}).get("value") not in (None, "")
    }

    if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        baseline = max(ZERO, _d(simple.get("land_value_baseline")))
        minimum = boundary_values.get("minimum", baseline)
        balanced = boundary_values.get("balanced", minimum)
        risk = boundary_values.get("risk_adjusted_ceiling", balanced)
        technical = boundary_values.get("technical_ceiling", risk)
        offered = boundary_values.get("offer", offered_upfront)
        raw_points = {
            ZERO,
            minimum,
            (minimum + balanced) / Decimal("2"),
            balanced,
            (balanced + risk) / Decimal("2"),
            risk,
            technical,
            offered,
            technical * Decimal("1.05") if technical > ZERO else baseline,
        }
        measures = sorted({point.quantize(Decimal("0.01")) for point in raw_points if point >= ZERO})
        measure_type = "AMOUNT"
        unit = str(simple.get("currency") or "USD").upper()
    else:
        minimum = boundary_values.get("minimum", ZERO)
        balanced = boundary_values.get("balanced", minimum)
        risk = boundary_values.get("risk_adjusted_ceiling", balanced)
        technical = boundary_values.get("technical_ceiling", risk)
        offered = boundary_values.get("offer", offered_share)
        probe = min(Decimal("100"), technical + max(Decimal("0.50"), technical * Decimal("0.05")))
        raw_points = {
            ZERO,
            minimum,
            (minimum + balanced) / Decimal("2"),
            balanced,
            (balanced + risk) / Decimal("2"),
            risk,
            technical,
            offered,
            probe,
        }
        measures = sorted({point.quantize(Decimal("0.0001")) for point in raw_points if ZERO <= point <= Decimal("100")})
        measure_type = "HYBRID_PERCENT" if method == "HYBRID" else "SHARE_PERCENT"
        unit = "%"

    boundary_lookup: dict[str, list[str]] = {}
    for name, value in boundary_values.items():
        key = str(value.quantize(Decimal("0.01") if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else Decimal("0.0001")))
        boundary_lookup.setdefault(key, []).append(name)

    rows: list[dict[str, Any]] = []
    for measure in measures:
        candidate = deepcopy(project_snapshot)
        partnership = candidate.setdefault("partnership", {})
        partnership["method"] = method
        partnership["approved_selection"] = "MANUAL"
        studio = candidate.setdefault("landowner_studio", {})
        methods = [str(value).upper() for value in studio.get("contract_methods") or []]
        if method not in methods:
            methods.append(method)
        studio["contract_methods"] = methods
        if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
            amount = _fmt(measure)
            partnership["manual_amount"] = amount
            if method == "UPFRONT":
                studio["upfront_amount"] = amount
            else:
                studio["minimum_guarantee_amount"] = amount
        else:
            rate = _fmt(measure / ONE_HUNDRED)
            partnership["share_rate"] = rate
            partnership["manual_share"] = rate
            if method == "HYBRID":
                studio["hybrid_upfront_amount"] = _fmt(offered_upfront)
                studio["hybrid_variable_basis"] = "GROSS_SALES"
        result = run_unified_financial_engine(candidate, policy_snapshot, selected_only=True)
        truth = result.get("financial_truth") or {}
        invariants = result.get("engine_invariants") or {}
        comparison = next((item for item in result.get("contract_comparison") or [] if item.get("method") == method), {})
        failed = list(truth.get("failed_constraints") or [])
        failed.extend([item.get("constraint_id") for item in comparison.get("constraints") or [] if item.get("passed") is False])
        failed.extend(list(invariants.get("failed_invariant_ids") or []))
        key = str(measure.quantize(Decimal("0.01") if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else Decimal("0.0001")))
        rows.append(
            {
                "measure": _fmt(measure),
                "boundary_keys": boundary_lookup.get(key, []),
                "government_consideration": _first_present(truth.get("government_consideration"), comparison.get("government_value")),
                "government_nominal": _first_present(truth.get("government_consideration"), comparison.get("government_value")),
                "government_npv": _first_present(truth.get("government_npv"), comparison.get("government_npv")),
                "developer_nominal_distributions": _first_present(truth.get("developer_equity_distributions"), truth.get("total_developer_distributions")),
                "developer_nominal_net_profit_after_equity": _first_present(truth.get("developer_equity_nominal_profit"), truth.get("developer_profit"), comparison.get("developer_profit")),
                "developer_unlevered_profit": _first_present(truth.get("developer_profit"), comparison.get("developer_profit")),
                "developer_irr": _first_present(truth.get("developer_equity_irr"), truth.get("developer_irr"), comparison.get("developer_irr")),
                "developer_moic": _first_present(truth.get("developer_equity_multiple"), truth.get("developer_multiple"), comparison.get("developer_multiple")),
                "developer_npv": _first_present(truth.get("developer_equity_npv"), truth.get("developer_npv"), comparison.get("developer_npv")),
                "profit_on_cost": _first_present(truth.get("developer_profit_on_cost"), comparison.get("developer_profit_on_cost")),
                "peak_debt": _first_present(truth.get("peak_debt"), comparison.get("peak_debt")),
                "funding_gap": _first_present(truth.get("funding_gap"), comparison.get("peak_funding_gap")),
                "terminal_debt": _first_present(truth.get("terminal_debt"), comparison.get("terminal_debt")),
                "schedule_extension_months": _first_present(truth.get("schedule_extension_months"), comparison.get("schedule_extension_months")),
                "feasible": bool(truth.get("feasible")) and bool(invariants.get("passed")),
                "failed_constraints": list(dict.fromkeys(value for value in failed if value)),
            }
        )
    feasible = [row for row in rows if row["feasible"]]
    return {
        "method": method,
        "measure_type": measure_type,
        "measure_label_en": native.get("measure_label_en"),
        "measure_label_ar": native.get("measure_label_ar"),
        "contract_label_en": native.get("contract_label_en"),
        "contract_label_ar": native.get("contract_label_ar"),
        "fixed_component": native.get("fixed_component"),
        "unit": unit,
        "offered_measure": _fmt(offered_upfront if method == "UPFRONT" else offered_share),
        "rows": rows,
        "first_feasible_measure": feasible[0]["measure"] if feasible else None,
        "last_feasible_measure": feasible[-1]["measure"] if feasible else None,
    }


def assess_government_project(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    policy_version_id: str | None,
    valuation_policy_version_id: str | None,
    mode: str,
    offered_share_percent: Any | None,
    upfront_amount: Any | None,
    public_discount_rate_percent: Any | None,
    partnership_method: str | None = None,
) -> dict[str, Any]:
    version = get_project_version(session, context, version_id)
    project = get_project(session, context, version.project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Landowner project version not found.")
    simple = deepcopy(version.source_input_snapshot or _landowner_input_from_canonical_snapshot(version.input_snapshot or {}))
    project_policy = _default_policy(session, context=context, policy_version_id=policy_version_id, expected_type="PROJECT")
    valuation_policy = _default_policy(session, context=context, policy_version_id=valuation_policy_version_id, expected_type="VALUATION")
    assessment_policy = _compose_assessment_policy(project_policy.policy_snapshot, valuation_policy.policy_snapshot)
    case_input = _case_input_from_landowner(
        simple,
        mode=mode,
        offered_share_percent=offered_share_percent,
        upfront_amount=upfront_amount,
        public_discount_rate_percent=None,
        partnership_method=partnership_method,
        policy_snapshot=assessment_policy,
    )
    case_input["government_project_source"] = {
        "schema": version.source_input_schema,
        "hash": version.source_input_hash or sha256_json(simple),
    }
    decision = run_government_decision(version.input_snapshot, assessment_policy, case_input)
    participation = deepcopy(
        (decision.get("contract_negotiation") or {}).get("participation_analysis") or {}
    )
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_PROJECT_ASSESSED",
        entity_type="ProjectVersion",
        entity_id=version.id,
        metadata={
            "policy_version_id": project_policy.id,
            "valuation_policy_version_id": valuation_policy.id,
            "input_hash": version.input_hash,
            "output_hash": decision.get("output_hash"),
            "mode": mode,
        },
    )
    return {
        "project_id": project.id,
        "project_version_id": version.id,
        "policy_pack_version_id": project_policy.id,
        "valuation_policy_pack_version_id": valuation_policy.id,
        "project_status": project.status,
        "version_status": version.status,
        "source_input_schema": version.source_input_schema,
        "source_input_hash": version.source_input_hash or sha256_json(simple),
        "derived_summary": _summary_for_version(project, version),
        "decision": decision,
        "participation_analysis": participation,
    }


def create_case_for_government_project(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    policy_version_id: str | None,
    valuation_policy_version_id: str | None,
    case_code: str,
    title: str,
    mode: str,
    offered_share_percent: Any | None,
    upfront_amount: Any | None,
    public_discount_rate_percent: Any | None,
    partnership_method: str | None = None,
):
    version = get_project_version(session, context, version_id)
    project = get_project(session, context, version.project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Landowner project version not found.")
    project_policy = _default_policy(session, context=context, policy_version_id=policy_version_id, expected_type="PROJECT")
    valuation_policy = _default_policy(session, context=context, policy_version_id=valuation_policy_version_id, expected_type="VALUATION")
    assessment_policy = _compose_assessment_policy(project_policy.policy_snapshot, valuation_policy.policy_snapshot)
    simple = deepcopy(version.source_input_snapshot or _landowner_input_from_canonical_snapshot(version.input_snapshot or {}))
    case_input = _case_input_from_landowner(
        simple,
        mode=mode,
        offered_share_percent=offered_share_percent,
        upfront_amount=upfront_amount,
        public_discount_rate_percent=None,
        partnership_method=partnership_method,
        policy_snapshot=assessment_policy,
    )
    case_input["assessment_policy_sources"] = {"project_policy_version_id": project_policy.id, "valuation_policy_version_id": valuation_policy.id}
    case_input["government_project_source"] = {
        "schema": version.source_input_schema,
        "hash": version.source_input_hash or sha256_json(simple),
    }
    return create_government_case(
        session,
        context=context,
        project_version_id=version.id,
        policy_pack_version_id=project_policy.id,
        valuation_policy_pack_version_id=valuation_policy.id,
        scenario_id=None,
        case_code=case_code,
        title=title,
        mode=mode,
        input_snapshot=case_input,
    )
