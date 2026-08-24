from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from landvalue360_common.versions import UNIFIED_ENGINE_ADAPTER_VERSION
from landvalue360_kernel.manifest import ENGINE_VERSION, engine_manifest
from landvalue360_server.unified_engine import run_unified_financial_engine
from landvalue360_government.negotiation import (
    _apply_measure_to_project,
    _summary_from_unified_result,
    build_native_negotiation,
)
from landvalue360_server.web_defaults import (
    default_policy_snapshot,
    default_project_snapshot,
    default_valuation_policy_snapshot,
)

from .models import Project, ProjectVersion
from .financial_audit import audit_financial_result
from .packages import internal_input_snapshot

PORTAL_FINANCIAL_ADAPTER_VERSION = "2.5.0"
LANDOWNER_CONTRACT_ENGINE_VERSION = "3.1.0"
PORTAL_POLICY_CODE = "LV360-STANDALONE-FINANCIAL-BASE"
CONTRACT_METHODS = (
    "GROSS_SALES",
    "NET_SALES",
    "PROFIT_SHARE",
    "UPFRONT",
    "HYBRID",
    "MINIMUM_GUARANTEE",
)
CURVE_TYPES = ("LINEAR", "FRONT_LOADED", "BACK_LOADED", "BELL", "S_CURVE", "ACCELERATED_S_CURVE", "DELAYED_RAMP")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(json_ready(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def deep_merge(base: dict[str, Any], overlay: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite numeric value: {value!r}")
    return result


def _bounded_fraction(
    value: Any,
    default: str = "0",
    *,
    minimum: str = "0",
    maximum: str = "1",
    label: str = "Rate",
) -> str:
    """Validate a canonical decimal rate.

    The portal API and persisted snapshots use decimal fractions: 0.25 means
    25%, while 2 means 200%.  No percentage guessing is performed here because
    doing so makes values above 100% ambiguous and non-deterministic.
    """
    result = D(value, default)
    lower = D(minimum)
    upper = D(maximum)
    if result < lower or result > upper:
        raise ValueError(f"{label} must be between {lower} and {upper}")
    return str(result)


def _bounded_decimal(
    value: Any,
    default: str = "0",
    *,
    minimum: str | None = None,
    maximum: str | None = None,
    label: str = "Value",
) -> str:
    result = D(value, default)
    if minimum is not None and result < D(minimum):
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and result > D(maximum):
        raise ValueError(f"{label} must not exceed {maximum}")
    return str(result)


def _boolean(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, Decimal)):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"Invalid boolean value: {value!r}")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _category_list(value: Any, *, label: str) -> list[str]:
    if value in (None, ""):
        return []
    rows = value if isinstance(value, (list, tuple, set)) else str(value).replace("\n", ",").split(",")
    cleaned: list[str] = []
    for raw in rows:
        item = str(raw or "").strip().upper()
        if not item:
            continue
        if len(item) > 100:
            raise ValueError(f"{label} contains an overlong category")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def _normalize_collection_rules(value: Any, *, label: str = "Collection plan") -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must contain at least one row")
    rows: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError(f"{label} rows must be objects")
        try:
            lag = int(raw.get("lag_months") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} lag must be an integer") from exc
        if lag < 0 or lag > 600:
            raise ValueError(f"{label} lag must be between 0 and 600 months")
        weight = D(raw.get("weight"), "0")
        if weight < 0:
            raise ValueError(f"{label} weights cannot be negative")
        rows.append({
            "lag_months": lag,
            "weight": weight,
            "label": str(raw.get("label") or "").strip()[:240],
        })
    total_weight = sum((row["weight"] for row in rows), Decimal("0"))
    if total_weight <= 0:
        raise ValueError(f"{label} must contain positive weights")
    return [
        {
            "lag_months": row["lag_months"],
            "weight": str(row["weight"] / total_weight),
            **({"label": row["label"]} if row["label"] else {}),
        }
        for row in rows
    ]


def _nonnegative(value: Any, default: str = "0") -> str:
    result = D(value, default)
    if result < 0:
        raise ValueError("Financial inputs cannot be negative")
    return str(result)


def _positive_int(value: Any, default: int, maximum: int = 600) -> int:
    try:
        result = int(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value: {value!r}") from exc
    if result < 1 or result > maximum:
        raise ValueError(f"Value must be between 1 and {maximum}")
    return result


def _curve_type(value: Any, default: str) -> str:
    result = str(value or default).upper()
    if result not in CURVE_TYPES:
        raise ValueError(f"Unsupported curve type: {result}")
    return result


def _iso_date(value: Any, fallback: date | None = None) -> str:
    fallback = fallback or date.today()
    if value in (None, ""):
        return fallback.isoformat()
    parsed = date.fromisoformat(str(value))
    return parsed.isoformat()


def default_financial_model(*, planning: dict[str, Any] | None = None, controls: dict[str, Any] | None = None) -> dict[str, Any]:
    planning = planning or {}
    controls = controls or {}
    advanced = deepcopy(controls.get("advanced_defaults") or {})
    user_defaults = deepcopy(controls.get("user_input_defaults") or controls.get("advanced_defaults") or {})
    legacy_timing = controls.get("default_timing") or {}
    project_months = _positive_int(planning.get("project_duration_months"), 36)
    sales_months = _positive_int(planning.get("sales_duration_months"), project_months)
    collection_rules = deepcopy(
        advanced.get("collection_rules")
        or legacy_timing.get("collection_rules")
        or [
            {"lag_months": 0, "weight": "0.20", "label": "Contract / booking"},
            {"lag_months": 12, "weight": "0.30", "label": "Construction collections"},
            {"lag_months": 24, "weight": "0.50", "label": "Handover / final collections"},
        ]
    )
    return {
        "schema_version": "standalone-financial-input-2.1.0",
        # Basic users consume policy-managed advanced assumptions. Analysts may
        # explicitly switch to project-specific overrides.
        "advanced_overrides_enabled": False,
        "valuation_date": date.today().isoformat(),
        "sales": {
            "start_month": 1,
            "duration_months": sales_months,
            "curve_type": str(advanced.get("sales_curve_type") or legacy_timing.get("sales_curve_type") or "S_CURVE").upper(),
            "curve_intensity": str(advanced.get("sales_curve_intensity") or "1"),
            "commercial_discount_rate": str(user_defaults.get("commercial_discount_rate") or "0"),
            "buyer_incentive_rate": str(user_defaults.get("buyer_incentive_rate") or "0"),
            "refund_rate": str(user_defaults.get("refund_rate") or "0"),
            "collection_rules": collection_rules,
        },
        "delivery": {
            "construction_start_month": 1,
            "construction_duration_months": project_months,
            "construction_curve_type": str(advanced.get("construction_curve_type") or legacy_timing.get("cost_curve_type") or "BELL").upper(),
            "other_cost_start_month": 1,
            "other_cost_duration_months": project_months,
            "other_cost_curve_type": str(advanced.get("other_cost_curve_type") or advanced.get("construction_curve_type") or legacy_timing.get("cost_curve_type") or "BELL").upper(),
            "cost_escalation_rate": str(user_defaults.get("cost_escalation_rate") or "0"),
            "cost_contingency_rate": str(user_defaults.get("cost_contingency_rate") or "0"),
            "maximum_extension_months": int(advanced.get("maximum_extension_months") or 120),
            "maximum_monthly_execution_share": str(advanced.get("maximum_monthly_execution_share") or "0.15"),
            "maximum_monthly_execution_amount": str(advanced.get("maximum_monthly_execution_amount") or "0"),
        },
        "funding": {
            "opening_cash": "0",
            # v2.1 public semantics: this is the TOTAL developer equity
            # commitment, inclusive of opening cash. The engine still receives
            # additional draw capacity after deducting opening cash.
            "total_developer_equity": "0",
            "committed_additional_equity": "0",
            "committed_financing": str(advanced.get("committed_financing") or "0"),
        },
        "finance": {
            "enabled": bool(advanced.get("finance_enabled", False)),
            "annual_interest_rate": str(advanced.get("annual_interest_rate") or "0.08"),
            "upfront_fee_rate": str(advanced.get("upfront_fee_rate") or "0.01"),
            "commitment_fee_rate": str(advanced.get("commitment_fee_rate") or "0.005"),
            "cash_sweep_share": str(advanced.get("cash_sweep_share") or "1"),
            "capitalize_interest": bool(advanced.get("capitalize_interest", True)),
            "force_terminal_repayment": bool(advanced.get("force_terminal_repayment", True)),
            "minimum_cash_balance": str(advanced.get("minimum_cash_balance") or "0"),
            "funding_draw_order": str(advanced.get("funding_draw_order") or legacy_timing.get("funding_draw_order") or "EQUITY_FIRST").upper(),
            "spend_policy": str(advanced.get("spend_policy") or legacy_timing.get("spend_policy") or "CASH_DRIVEN").upper(),
            "hybrid_minimum_execution_share": str(advanced.get("hybrid_minimum_execution_share") or "0.35"),
            "future_cost_reserve_share": str(advanced.get("future_cost_reserve_share") or "0"),
            "allow_negative_cash": False,
            "defer_contractual_payments": bool(advanced.get("defer_contractual_payments", legacy_timing.get("defer_contractual_payments", True))),
        },
        "contract": {
            "method": "GROSS_SALES",
            "share_rate": "0.10",
            "upfront_amount": "0",
            "upfront_payment_month": 1,
            "hybrid_upfront_amount": "0",
            "hybrid_upfront_payment_month": 1,
            "hybrid_variable_basis": "GROSS_SALES",
            "minimum_guarantee_amount": "0",
            "minimum_guarantee_payment_month": project_months,
            "minimum_guarantee_underlying_method": "GROSS_SALES",
            "minimum_guarantee_underlying_share": "0.05",
            "net_deduction_treatment": "CUMULATIVE_CARRY_FORWARD",
        },
    }
def default_portal_policy_controls() -> dict[str, Any]:
    collection_rules = [
        {"lag_months": 0, "weight": "0.20", "label": "Contract / booking"},
        {"lag_months": 12, "weight": "0.30", "label": "Construction collections"},
        {"lag_months": 24, "weight": "0.50", "label": "Handover / final collections"},
    ]
    return {
        "schema_version": "financial-policy-controls-2.4.0",
        "display_name_ar": "السياسة المالية القياسية",
        "display_name_en": "Standard Financial Policy",
        "description_ar": "سياسة افتراضية متوازنة لتحليل الجدوى وقدرة الأرض والمجال التفاوضي.",
        "description_en": "Balanced default policy for feasibility, land capacity and negotiation analysis.",
        "user_selectable": True,
        "discount_rate": "0.12",
        "government_discount_rate": "0.10",
        "minimum_project_npv": "0",
        "minimum_developer_equity_irr": "0.18",
        "target_developer_irr": "0.22",
        "minimum_developer_npv": "0",
        "minimum_profit_on_cost": "0.20",
        "target_developer_profit_on_cost": "0.25",
        "minimum_developer_multiple": "1.20",
        "maximum_funding_gap": "0",
        "minimum_landowner_npv": "0",
        "minimum_landowner_value_recovery": "1",
        "minimum_landowner_share": "0",
        "maximum_landowner_share": "0.50",
        "search_tolerance": "0.00001",
        "negotiation_recommendation_method": "POLICY_RANGE_POSITION",
        "institutional_conservatism": "0.58",
        "risk_adjusted_capacity_factor": "0.42",
        "minimum_capacity_factor": "0.30",
        "developer_safety_buffer": "0",
        "balanced_position_factor": "0.56",
        "balanced_position_minimum": "0",
        "balanced_position_maximum": "1",
        "developer_competitive_position_factor": "0.40",
        "rounding_increment_percent": "0.001",
        "allowed_contract_methods": list(CONTRACT_METHODS),
        # Net Sales is a revenue basis in Contract Engine 3.x. Development
        # costs never reduce it. The empty registry is preserved for schema
        # compatibility and future revenue-only deductions.
        "net_sales_deductible_categories": [],
        "profit_share_cost_categories": [
            "CONSTRUCTION", "INFRASTRUCTURE", "PUBLIC_FACILITIES", "PERMITS",
            "PROFESSIONAL_FEES", "MANAGEMENT", "MARKETING",
        ],
        "finance_policy": {
            "allow_financing": True,
            "allow_negative_cash": False,
            "defer_unfunded_costs": True,
            "require_terminal_debt_zero": True,
            "require_deferred_cost_zero": True,
            "require_contractual_arrears_zero": True,
            "require_monthly_cash_reconciliation": True,
        },
        "advanced_defaults": {
            "finance_enabled": False,
            "committed_financing": "0",
            "annual_interest_rate": "0.08",
            "upfront_fee_rate": "0.01",
            "commitment_fee_rate": "0.005",
            "cash_sweep_share": "1",
            "capitalize_interest": True,
            "force_terminal_repayment": True,
            "minimum_cash_balance": "0",
            "funding_draw_order": "EQUITY_FIRST",
            "spend_policy": "CASH_DRIVEN",
            "hybrid_minimum_execution_share": "0.35",
            "future_cost_reserve_share": "0",
            "defer_contractual_payments": True,
            "sales_curve_type": "S_CURVE",
            "sales_curve_intensity": "1",
            "construction_curve_type": "BELL",
            "other_cost_curve_type": "BELL",
            "maximum_extension_months": 120,
            "maximum_monthly_execution_share": "0.15",
            "maximum_monthly_execution_amount": "0",
            "commercial_discount_rate": "0",
            "buyer_incentive_rate": "0",
            "refund_rate": "0",
            "cost_escalation_rate": "0",
            "cost_contingency_rate": "0",
            "horizon_buffer_months": 12,
            "solver_grid_intervals": 12,
            "distribution_frequency_code": "ANNUAL",
            "first_distribution_month": 12,
            "distribution_share": "1",
            "distribution_reserve_months": 12,
            "remaining_cost_reserve_share": "0.20",
            "prohibit_distributions_while_debt_outstanding": True,
            "recover_developer_advances_before_landowner_cash": True,
            "settle_prior_obligations_before_distribution": True,
            "prohibit_before_completion": False,
            "upfront_search_land_value_multiple": "4",
            "upfront_search_cost_multiple": "2",
            "collection_rules": collection_rules,
        },
        "default_timing": {
            "sales_curve_type": "S_CURVE",
            "cost_curve_type": "BELL",
            "funding_draw_order": "EQUITY_FIRST",
            "spend_policy": "CASH_DRIVEN",
            "defer_contractual_payments": True,
            "collection_rules": collection_rules,
        },
        "proposal_selection_method": "BALANCED",
    }


def default_financial_policy_snapshot() -> dict[str, Any]:
    merged = deep_merge(default_policy_snapshot(), default_valuation_policy_snapshot())
    controls = default_portal_policy_controls()
    merged["policy_id"] = PORTAL_POLICY_CODE
    merged["version"] = PORTAL_FINANCIAL_ADAPTER_VERSION
    merged["portal_policy"] = controls
    return apply_policy_controls(merged, controls)


def apply_policy_controls(policy: dict[str, Any], controls: dict[str, Any] | None = None) -> dict[str, Any]:
    result = deepcopy(policy)
    raw_controls = deep_merge(default_portal_policy_controls(), controls or result.get("portal_policy") or {})

    methods: list[str] = []
    for raw_method in raw_controls.get("allowed_contract_methods") or CONTRACT_METHODS:
        method = str(raw_method).strip().upper()
        if method in CONTRACT_METHODS and method not in methods:
            methods.append(method)
    if not methods:
        raise ValueError("At least one contract method must be enabled")

    proposal = str(raw_controls.get("proposal_selection_method") or "BALANCED").strip().upper()
    if proposal == "MAX_PUBLIC_NPV":
        proposal = "MAXIMUM_LANDOWNER_VALUE"
    if proposal not in {"BALANCED", "MAXIMUM_LANDOWNER_VALUE"}:
        raise ValueError("Unsupported proposal selection method")

    discount_rate = _bounded_fraction(raw_controls.get("discount_rate"), "0.12", maximum="10", label="Discount rate")
    government_discount_rate = _bounded_fraction(raw_controls.get("government_discount_rate"), "0.10", maximum="10", label="Landowner discount rate")
    minimum_developer_irr = _bounded_fraction(raw_controls.get("minimum_developer_equity_irr"), "0.18", maximum="10", label="Minimum developer equity IRR")
    target_developer_irr = _bounded_fraction(raw_controls.get("target_developer_irr"), "0.22", maximum="10", label="Target developer IRR")
    if D(target_developer_irr) < D(minimum_developer_irr):
        raise ValueError("Target developer IRR cannot be below the minimum developer equity IRR")
    minimum_profit_on_cost = _bounded_fraction(raw_controls.get("minimum_profit_on_cost"), "0.20", maximum="10", label="Minimum profit on cost")
    target_profit_on_cost = _bounded_fraction(raw_controls.get("target_developer_profit_on_cost"), "0.25", maximum="10", label="Target developer profit on cost")
    if D(target_profit_on_cost) < D(minimum_profit_on_cost):
        raise ValueError("Target developer profit on cost cannot be below the minimum profit on cost")
    minimum_share = _bounded_fraction(raw_controls.get("minimum_landowner_share"), "0", maximum="1", label="Minimum landowner share")
    maximum_share = _bounded_fraction(raw_controls.get("maximum_landowner_share"), "0.50", maximum="1", label="Maximum landowner share")
    if D(minimum_share) > D(maximum_share):
        raise ValueError("Minimum landowner share cannot exceed maximum landowner share")
    search_tolerance = _bounded_decimal(raw_controls.get("search_tolerance"), "0.00001", minimum="0.000000001", maximum="1", label="Search tolerance")
    minimum_landowner_recovery = _bounded_fraction(raw_controls.get("minimum_landowner_value_recovery"), "1", maximum="10", label="Minimum landowner value recovery")

    negotiation_method = str(raw_controls.get("negotiation_recommendation_method") or "POLICY_RANGE_POSITION").strip().upper()
    if negotiation_method not in {"POLICY_RANGE_POSITION", "CORE_TARGET_RETURN"}:
        raise ValueError("Unsupported negotiation recommendation method")
    institutional_conservatism = _bounded_fraction(raw_controls.get("institutional_conservatism"), "0.58", maximum="0.95", label="Institutional conservatism")
    minimum_capacity_factor = _bounded_fraction(raw_controls.get("minimum_capacity_factor"), "0.30", maximum="1", label="Minimum capacity factor")
    risk_adjusted_capacity_factor = _bounded_fraction(raw_controls.get("risk_adjusted_capacity_factor"), "0.42", maximum="1", label="Risk-adjusted capacity factor")
    if D(risk_adjusted_capacity_factor) < D(minimum_capacity_factor):
        raise ValueError("Risk-adjusted capacity factor cannot be below the minimum capacity factor")
    developer_safety_buffer = _bounded_fraction(raw_controls.get("developer_safety_buffer"), "0", maximum="0.50", label="Developer safety buffer")
    balanced_position_factor = _bounded_fraction(raw_controls.get("balanced_position_factor"), "0.56", maximum="1", label="Balanced position factor")
    balanced_position_minimum = _bounded_fraction(raw_controls.get("balanced_position_minimum"), "0", maximum="1", label="Balanced position minimum")
    balanced_position_maximum = _bounded_fraction(raw_controls.get("balanced_position_maximum"), "1", maximum="1", label="Balanced position maximum")
    if D(balanced_position_minimum) > D(balanced_position_maximum):
        raise ValueError("Balanced position minimum cannot exceed its maximum")
    if not (D(balanced_position_minimum) <= D(balanced_position_factor) <= D(balanced_position_maximum)):
        raise ValueError("Balanced position factor must lie between its configured minimum and maximum")
    developer_competitive_position_factor = _bounded_fraction(raw_controls.get("developer_competitive_position_factor"), "0.40", maximum="1", label="Developer competitive position factor")
    rounding_increment_percent = _bounded_fraction(raw_controls.get("rounding_increment_percent"), "0.001", maximum="1", label="Negotiation rounding increment")

    finance_source = raw_controls.get("finance_policy") or {}
    finance_rules = {
        "allow_financing": _boolean(finance_source.get("allow_financing"), True),
        "allow_negative_cash": False,
        "defer_unfunded_costs": _boolean(finance_source.get("defer_unfunded_costs"), True),
        "require_terminal_debt_zero": True,
        "require_deferred_cost_zero": True,
        "require_contractual_arrears_zero": True,
        "require_monthly_cash_reconciliation": True,
    }

    timing_source = raw_controls.get("default_timing") or {}
    advanced_source = raw_controls.get("advanced_defaults") or {}
    funding_draw_order = str(advanced_source.get("funding_draw_order") or timing_source.get("funding_draw_order") or "EQUITY_FIRST").strip().upper()
    if funding_draw_order not in {"DEBT_FIRST", "EQUITY_FIRST", "PRO_RATA"}:
        raise ValueError("Unsupported default funding draw order")
    spend_policy = str(advanced_source.get("spend_policy") or timing_source.get("spend_policy") or "CASH_DRIVEN").strip().upper()
    if spend_policy not in {"CASH_DRIVEN", "HYBRID"}:
        raise ValueError("Unsupported default spend policy")
    distribution_frequency_code = str(advanced_source.get("distribution_frequency_code") or "ANNUAL").strip().upper()
    if distribution_frequency_code not in {"MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL"}:
        raise ValueError("Unsupported default distribution frequency")

    def _int_setting(name: str, default: int, minimum: int, maximum: int, label: str) -> int:
        try:
            value = int(advanced_source.get(name) if advanced_source.get(name) not in (None, "") else default)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if value < minimum or value > maximum:
            raise ValueError(f"{label} must be between {minimum} and {maximum}")
        return value

    advanced_defaults = {
        "finance_enabled": _boolean(advanced_source.get("finance_enabled"), False),
        "committed_financing": _bounded_decimal(advanced_source.get("committed_financing"), "0", minimum="0", label="Default committed financing"),
        "annual_interest_rate": _bounded_fraction(advanced_source.get("annual_interest_rate"), "0.08", maximum="10", label="Default annual interest rate"),
        "upfront_fee_rate": _bounded_fraction(advanced_source.get("upfront_fee_rate"), "0.01", maximum="1", label="Default upfront fee rate"),
        "commitment_fee_rate": _bounded_fraction(advanced_source.get("commitment_fee_rate"), "0.005", maximum="1", label="Default commitment fee rate"),
        "cash_sweep_share": _bounded_fraction(advanced_source.get("cash_sweep_share"), "1", maximum="1", label="Default cash sweep share"),
        "capitalize_interest": _boolean(advanced_source.get("capitalize_interest"), True),
        "force_terminal_repayment": _boolean(advanced_source.get("force_terminal_repayment"), True),
        "minimum_cash_balance": _bounded_decimal(advanced_source.get("minimum_cash_balance"), "0", minimum="0", label="Default minimum cash balance"),
        "funding_draw_order": funding_draw_order,
        "spend_policy": spend_policy,
        "hybrid_minimum_execution_share": _bounded_fraction(advanced_source.get("hybrid_minimum_execution_share"), "0.35", maximum="1", label="Default hybrid execution share"),
        "future_cost_reserve_share": _bounded_fraction(advanced_source.get("future_cost_reserve_share"), "0", maximum="1", label="Default future cost reserve share"),
        "defer_contractual_payments": _boolean(advanced_source.get("defer_contractual_payments", timing_source.get("defer_contractual_payments")), True),
        "sales_curve_type": _curve_type(advanced_source.get("sales_curve_type", timing_source.get("sales_curve_type")), "S_CURVE"),
        "sales_curve_intensity": _bounded_decimal(advanced_source.get("sales_curve_intensity"), "1", minimum="0.000001", maximum="100", label="Default sales curve intensity"),
        "construction_curve_type": _curve_type(advanced_source.get("construction_curve_type", timing_source.get("cost_curve_type")), "BELL"),
        "other_cost_curve_type": _curve_type(advanced_source.get("other_cost_curve_type", timing_source.get("cost_curve_type")), "BELL"),
        "maximum_extension_months": _int_setting("maximum_extension_months", 120, 0, 600, "Default maximum extension"),
        "maximum_monthly_execution_share": _bounded_fraction(advanced_source.get("maximum_monthly_execution_share"), "0.15", maximum="1", label="Default maximum monthly execution share"),
        "maximum_monthly_execution_amount": _bounded_decimal(advanced_source.get("maximum_monthly_execution_amount"), "0", minimum="0", label="Default maximum monthly execution amount"),
        "commercial_discount_rate": _bounded_fraction(advanced_source.get("commercial_discount_rate"), "0", maximum="1", label="Default commercial discount rate"),
        "buyer_incentive_rate": _bounded_fraction(advanced_source.get("buyer_incentive_rate"), "0", maximum="1", label="Default buyer incentive rate"),
        "refund_rate": _bounded_fraction(advanced_source.get("refund_rate"), "0", maximum="1", label="Default refund rate"),
        "cost_escalation_rate": _bounded_decimal(advanced_source.get("cost_escalation_rate"), "0", minimum="-1", maximum="10", label="Default cost escalation rate"),
        "cost_contingency_rate": _bounded_fraction(advanced_source.get("cost_contingency_rate"), "0", maximum="10", label="Default cost contingency rate"),
        "horizon_buffer_months": _int_setting("horizon_buffer_months", 12, 0, 240, "Default horizon buffer"),
        "solver_grid_intervals": _int_setting("solver_grid_intervals", 12, 4, 100, "Solver grid intervals"),
        "distribution_frequency_code": distribution_frequency_code,
        "first_distribution_month": _int_setting("first_distribution_month", 12, 1, 600, "First distribution month"),
        "distribution_share": _bounded_fraction(advanced_source.get("distribution_share"), "1", maximum="1", label="Default distribution share"),
        "distribution_reserve_months": _int_setting("distribution_reserve_months", 12, 0, 240, "Default distribution reserve"),
        "remaining_cost_reserve_share": _bounded_fraction(advanced_source.get("remaining_cost_reserve_share"), "0.20", maximum="1", label="Default remaining-cost reserve share"),
        "prohibit_distributions_while_debt_outstanding": _boolean(advanced_source.get("prohibit_distributions_while_debt_outstanding"), True),
        "recover_developer_advances_before_landowner_cash": _boolean(advanced_source.get("recover_developer_advances_before_landowner_cash"), True),
        "settle_prior_obligations_before_distribution": _boolean(advanced_source.get("settle_prior_obligations_before_distribution"), True),
        "prohibit_before_completion": _boolean(advanced_source.get("prohibit_before_completion"), False),
        "upfront_search_land_value_multiple": _bounded_decimal(advanced_source.get("upfront_search_land_value_multiple"), "4", minimum="0", maximum="100", label="Upfront search land-value multiple"),
        "upfront_search_cost_multiple": _bounded_decimal(advanced_source.get("upfront_search_cost_multiple"), "2", minimum="0", maximum="100", label="Upfront search cost multiple"),
        "collection_rules": _normalize_collection_rules(
            advanced_source.get("collection_rules") or timing_source.get("collection_rules") or default_portal_policy_controls()["advanced_defaults"]["collection_rules"],
            label="Default collection plan",
        ),
    }
    default_timing = {
        "sales_curve_type": advanced_defaults["sales_curve_type"],
        "cost_curve_type": advanced_defaults["construction_curve_type"],
        "funding_draw_order": advanced_defaults["funding_draw_order"],
        "spend_policy": advanced_defaults["spend_policy"],
        "defer_contractual_payments": advanced_defaults["defer_contractual_payments"],
        "collection_rules": deepcopy(advanced_defaults["collection_rules"]),
    }

    display_name_ar = str(raw_controls.get("display_name_ar") or "السياسة المالية القياسية").strip()[:240]
    display_name_en = str(raw_controls.get("display_name_en") or "Standard Financial Policy").strip()[:240]
    if not display_name_ar or not display_name_en:
        raise ValueError("Policy names in Arabic and English are required")

    normalized_controls = {
        "schema_version": "financial-policy-controls-2.4.0",
        "display_name_ar": display_name_ar,
        "display_name_en": display_name_en,
        "description_ar": str(raw_controls.get("description_ar") or "").strip()[:4000],
        "description_en": str(raw_controls.get("description_en") or "").strip()[:4000],
        "user_selectable": _boolean(raw_controls.get("user_selectable"), True),
        "discount_rate": discount_rate,
        "government_discount_rate": government_discount_rate,
        "minimum_project_npv": _bounded_decimal(raw_controls.get("minimum_project_npv"), "0", minimum="0", label="Minimum project NPV"),
        "minimum_developer_equity_irr": minimum_developer_irr,
        "target_developer_irr": target_developer_irr,
        "minimum_developer_npv": _bounded_decimal(raw_controls.get("minimum_developer_npv"), "0", minimum="0", label="Minimum developer NPV"),
        "minimum_profit_on_cost": minimum_profit_on_cost,
        "target_developer_profit_on_cost": target_profit_on_cost,
        "minimum_developer_multiple": _bounded_decimal(raw_controls.get("minimum_developer_multiple"), "1.20", minimum="0", label="Minimum developer multiple"),
        "maximum_funding_gap": _bounded_decimal(raw_controls.get("maximum_funding_gap"), "0", minimum="0", label="Maximum funding gap"),
        "minimum_landowner_npv": _bounded_decimal(raw_controls.get("minimum_landowner_npv"), "0", minimum="0", label="Minimum landowner NPV"),
        "minimum_landowner_value_recovery": minimum_landowner_recovery,
        "minimum_landowner_share": minimum_share,
        "maximum_landowner_share": maximum_share,
        "search_tolerance": search_tolerance,
        "negotiation_recommendation_method": negotiation_method,
        "institutional_conservatism": institutional_conservatism,
        "risk_adjusted_capacity_factor": risk_adjusted_capacity_factor,
        "minimum_capacity_factor": minimum_capacity_factor,
        "developer_safety_buffer": developer_safety_buffer,
        "balanced_position_factor": balanced_position_factor,
        "balanced_position_minimum": balanced_position_minimum,
        "balanced_position_maximum": balanced_position_maximum,
        "developer_competitive_position_factor": developer_competitive_position_factor,
        "rounding_increment_percent": rounding_increment_percent,
        "allowed_contract_methods": methods,
        "net_sales_deductible_categories": _category_list(raw_controls.get("net_sales_deductible_categories"), label="Net sales deductible categories"),
        "profit_share_cost_categories": _category_list(raw_controls.get("profit_share_cost_categories"), label="Profit share cost categories"),
        "finance_policy": finance_rules,
        "advanced_defaults": advanced_defaults,
        "default_timing": default_timing,
        "proposal_selection_method": proposal,
    }

    result["portal_policy"] = normalized_controls
    financial = result.setdefault("financial_constraints", {})
    financial.update({
        "discount_rate": discount_rate,
        "government_discount_rate": government_discount_rate,
        "minimum_project_npv": normalized_controls["minimum_project_npv"],
        "minimum_developer_irr": minimum_developer_irr,
        "target_developer_irr": target_developer_irr,
        "minimum_profit_on_cost": minimum_profit_on_cost,
        "minimum_developer_multiple": normalized_controls["minimum_developer_multiple"],
        "minimum_developer_npv": normalized_controls["minimum_developer_npv"],
        "maximum_funding_gap": normalized_controls["maximum_funding_gap"],
    })
    result.setdefault("finance_constraints", {})["minimum_equity_irr"] = minimum_developer_irr
    result.setdefault("share_policy", {}).update({
        "policy_minimum_share": minimum_share,
        "policy_maximum_share": maximum_share,
        "search_tolerance": search_tolerance,
        "minimum_government_value_npv": normalized_controls["minimum_landowner_npv"],
    })
    result["fair_consideration_policy"] = {
        "institutional_conservatism": institutional_conservatism,
        "risk_adjusted_capacity_factor": risk_adjusted_capacity_factor,
        "minimum_capacity_factor": minimum_capacity_factor,
        "developer_safety_buffer": developer_safety_buffer,
        "balanced_position_factor": balanced_position_factor,
        "balanced_position_minimum": balanced_position_minimum,
        "balanced_position_maximum": balanced_position_maximum,
        "developer_competitive_position_factor": developer_competitive_position_factor,
    }
    result["valuation_policy"] = deep_merge(result.get("valuation_policy") or {}, {
        "recommendation_method": negotiation_method,
        "rounding_increment_percent": rounding_increment_percent,
        "proposal_selection_method": proposal,
    })
    result["solver_defaults"] = {
        "solver_grid_intervals": advanced_defaults["solver_grid_intervals"],
        "horizon_buffer_months": advanced_defaults["horizon_buffer_months"],
    }
    result["distribution_policy"] = deep_merge(result.get("distribution_policy") or {}, {
        "enabled": True,
        "frequency_code": advanced_defaults["distribution_frequency_code"],
        "first_distribution_month": advanced_defaults["first_distribution_month"],
        "distribution_share": advanced_defaults["distribution_share"],
        "reserve_months": advanced_defaults["distribution_reserve_months"],
        "remaining_cost_reserve_share": advanced_defaults["remaining_cost_reserve_share"],
        "prohibit_while_debt_outstanding": advanced_defaults["prohibit_distributions_while_debt_outstanding"],
        "recover_developer_advances_before_landowner_cash": advanced_defaults["recover_developer_advances_before_landowner_cash"],
        "settle_prior_obligations_before_distribution": advanced_defaults["settle_prior_obligations_before_distribution"],
        "prohibit_before_completion": advanced_defaults["prohibit_before_completion"],
    })
    return json_ready(result)


def policy_controls(policy_snapshot: dict[str, Any]) -> dict[str, Any]:
    return deep_merge(default_portal_policy_controls(), policy_snapshot.get("portal_policy") or {})


def normalize_financial_model(
    raw: dict[str, Any] | None,
    *,
    planning: dict[str, Any] | None = None,
    controls: dict[str, Any] | None = None,
    force_policy_advanced: bool = False,
) -> dict[str, Any]:
    planning = planning or {}
    controls = controls or {}
    base = default_financial_model(planning=planning, controls=controls)
    raw_copy = deepcopy(raw or {})

    # Upgrade v2.0 funding semantics deterministically. Older snapshots stored
    # committed_additional_equity as additional cash beyond opening cash. v2.1
    # exposes a total commitment inclusive of opening cash.
    raw_funding = raw_copy.get("funding") or {}
    if raw_funding.get("total_developer_equity") in (None, ""):
        opening_legacy = D(raw_funding.get("opening_cash"), "0")
        declared_legacy = D(raw_funding.get("committed_additional_equity"), "0")
        # The v2.0 portal LABEL described this field as committed equity even
        # though the adapter passed it to the kernel as additional equity. User
        # intent therefore treated the displayed amount as TOTAL commitment.
        # Correct that semantic defect when upgrading v2.0 portal snapshots.
        if str(raw_copy.get("schema_version") or "").startswith("standalone-financial-input-2.0"):
            total_legacy = max(opening_legacy, declared_legacy)
        else:
            total_legacy = opening_legacy + declared_legacy
        raw_funding["total_developer_equity"] = str(total_legacy)
        raw_copy["funding"] = raw_funding

    result = deep_merge(base, raw_copy)
    result["schema_version"] = "standalone-financial-input-2.1.0"
    result["advanced_overrides_enabled"] = _boolean(result.get("advanced_overrides_enabled"), False)

    # Policy-managed advanced assumptions are reapplied for standard projects on
    # every authoritative calculation. This makes the active Policy Version the
    # immutable source of these defaults. Analysts can opt into project-specific
    # advanced overrides, which are then frozen in the Project Version.
    if force_policy_advanced or not result["advanced_overrides_enabled"]:
        policy_defaults = default_financial_model(planning=planning, controls=controls)
        for path in (
            ("sales", "curve_type"), ("sales", "curve_intensity"), ("sales", "collection_rules"),
            ("delivery", "construction_curve_type"), ("delivery", "other_cost_curve_type"),
            ("delivery", "maximum_extension_months"), ("delivery", "maximum_monthly_execution_share"),
            ("delivery", "maximum_monthly_execution_amount"), ("funding", "committed_financing"),
        ):
            result[path[0]][path[1]] = deepcopy(policy_defaults[path[0]][path[1]])
        result["finance"] = deepcopy(policy_defaults["finance"])
        # Standard-user projects keep other-cost timing aligned with the main
        # construction programme. Analysts can decouple the schedules only by
        # enabling advanced project overrides.
        result["delivery"]["other_cost_start_month"] = result["delivery"].get("construction_start_month", 1)
        result["delivery"]["other_cost_duration_months"] = result["delivery"].get("construction_duration_months", 36)

    result["valuation_date"] = _iso_date(result.get("valuation_date"))

    sales = result["sales"]
    sales["start_month"] = _positive_int(sales.get("start_month"), 1)
    sales["duration_months"] = _positive_int(sales.get("duration_months"), 36)
    sales["curve_type"] = _curve_type(sales.get("curve_type"), "S_CURVE")
    sales["curve_intensity"] = _bounded_decimal(sales.get("curve_intensity"), "1", minimum="0.000001", maximum="100", label="Sales curve intensity")
    for key, label in (
        ("commercial_discount_rate", "Commercial discount rate"),
        ("buyer_incentive_rate", "Buyer incentive rate"),
        ("refund_rate", "Refund rate"),
    ):
        sales[key] = _bounded_fraction(sales.get(key), "0", maximum="1", label=label)
    sales["collection_rules"] = _normalize_collection_rules(sales.get("collection_rules"), label="Collection plan")

    delivery = result["delivery"]
    for key, default in (
        ("construction_start_month", 1),
        ("construction_duration_months", 36),
        ("other_cost_start_month", 1),
        ("other_cost_duration_months", 36),
    ):
        delivery[key] = _positive_int(delivery.get(key), default)
    delivery["construction_curve_type"] = _curve_type(delivery.get("construction_curve_type"), "BELL")
    delivery["other_cost_curve_type"] = _curve_type(delivery.get("other_cost_curve_type"), "BELL")
    delivery["cost_escalation_rate"] = _bounded_decimal(delivery.get("cost_escalation_rate"), "0", minimum="-1", maximum="10", label="Cost escalation rate")
    delivery["cost_contingency_rate"] = _bounded_fraction(delivery.get("cost_contingency_rate"), "0", maximum="10", label="Cost contingency rate")
    try:
        delivery["maximum_extension_months"] = int(delivery.get("maximum_extension_months") if delivery.get("maximum_extension_months") not in (None, "") else 120)
    except (TypeError, ValueError) as exc:
        raise ValueError("Maximum extension must be an integer") from exc
    if delivery["maximum_extension_months"] < 0 or delivery["maximum_extension_months"] > 600:
        raise ValueError("Maximum extension must be between 0 and 600 months")
    delivery["maximum_monthly_execution_share"] = _bounded_fraction(delivery.get("maximum_monthly_execution_share"), "0.15", maximum="1", label="Maximum monthly execution share")
    delivery["maximum_monthly_execution_amount"] = _nonnegative(delivery.get("maximum_monthly_execution_amount"), "0")

    funding = result["funding"]
    opening_cash = D(_nonnegative(funding.get("opening_cash"), "0"))
    total_equity = D(_nonnegative(funding.get("total_developer_equity"), str(opening_cash)))
    if total_equity < opening_cash:
        raise ValueError("Total developer equity commitment cannot be below the initial equity contribution / opening cash")
    funding["opening_cash"] = str(opening_cash)
    funding["total_developer_equity"] = str(total_equity)
    funding["committed_additional_equity"] = str(total_equity - opening_cash)
    funding["committed_financing"] = _nonnegative(funding.get("committed_financing"), "0")

    finance = result["finance"]
    finance["enabled"] = _boolean(finance.get("enabled"), False)
    finance["allow_negative_cash"] = False
    finance["annual_interest_rate"] = _bounded_fraction(finance.get("annual_interest_rate"), "0.08", maximum="10", label="Annual interest rate")
    for key, default, label in (
        ("upfront_fee_rate", "0.01", "Upfront fee rate"),
        ("commitment_fee_rate", "0.005", "Commitment fee rate"),
        ("cash_sweep_share", "1", "Cash sweep share"),
        ("hybrid_minimum_execution_share", "0.35", "Hybrid minimum execution share"),
        ("future_cost_reserve_share", "0", "Future cost reserve share"),
    ):
        finance[key] = _bounded_fraction(finance.get(key), default, maximum="1", label=label)
    finance["minimum_cash_balance"] = _nonnegative(finance.get("minimum_cash_balance"), "0")
    finance["capitalize_interest"] = _boolean(finance.get("capitalize_interest"), True)
    finance["force_terminal_repayment"] = _boolean(finance.get("force_terminal_repayment"), True)
    finance["defer_contractual_payments"] = _boolean(finance.get("defer_contractual_payments"), True)
    finance["funding_draw_order"] = str(finance.get("funding_draw_order") or "EQUITY_FIRST").strip().upper()
    if finance["funding_draw_order"] not in {"DEBT_FIRST", "EQUITY_FIRST", "PRO_RATA"}:
        raise ValueError("Invalid funding draw order")
    finance["spend_policy"] = str(finance.get("spend_policy") or "CASH_DRIVEN").strip().upper()
    if finance["spend_policy"] not in {"CASH_DRIVEN", "HYBRID"}:
        raise ValueError("Invalid spend policy")

    contract = result["contract"]
    contract["method"] = str(contract.get("method") or "GROSS_SALES").strip().upper()
    if contract["method"] not in CONTRACT_METHODS:
        raise ValueError("Unsupported contract method")
    contract["share_rate"] = _bounded_fraction(contract.get("share_rate"), "0", maximum="1", label="Contract share rate")
    contract["upfront_amount"] = _nonnegative(contract.get("upfront_amount"), "0")
    contract["upfront_payment_month"] = _positive_int(contract.get("upfront_payment_month"), 1)
    contract["hybrid_upfront_amount"] = _nonnegative(contract.get("hybrid_upfront_amount"), "0")
    contract["hybrid_upfront_payment_month"] = _positive_int(contract.get("hybrid_upfront_payment_month"), 1)
    contract["hybrid_variable_basis"] = str(contract.get("hybrid_variable_basis") or "GROSS_SALES").strip().upper()
    if contract["hybrid_variable_basis"] not in {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE"}:
        raise ValueError("Invalid hybrid variable basis")
    contract["minimum_guarantee_amount"] = _nonnegative(contract.get("minimum_guarantee_amount"), "0")
    contract["minimum_guarantee_payment_month"] = _positive_int(contract.get("minimum_guarantee_payment_month"), delivery["construction_duration_months"])
    contract["minimum_guarantee_underlying_method"] = str(contract.get("minimum_guarantee_underlying_method") or "GROSS_SALES").strip().upper()
    if contract["minimum_guarantee_underlying_method"] not in {"GROSS_SALES", "NET_SALES", "PROFIT_SHARE"}:
        raise ValueError("Invalid minimum guarantee underlying method")
    contract["minimum_guarantee_underlying_share"] = _bounded_fraction(contract.get("minimum_guarantee_underlying_share"), "0", maximum="1", label="Minimum guarantee underlying share")
    contract["net_deduction_treatment"] = str(contract.get("net_deduction_treatment") or "CUMULATIVE_CARRY_FORWARD").strip().upper()
    if contract["net_deduction_treatment"] not in {"CUMULATIVE_CARRY_FORWARD", "PERIOD_BY_PERIOD"}:
        raise ValueError("Invalid net sales deduction treatment")
    return json_ready(result)
def _cost_amount(row: dict[str, Any]) -> Decimal:
    if row.get("amount") not in (None, ""):
        return D(row.get("amount"))
    return D(row.get("quantity")) * D(row.get("unit_cost"))


def effective_project_input_snapshot(version: ProjectVersion, policy_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Freeze every input actually consumed by the financial adapter.

    Legacy project versions can pre-date the financial portal and therefore may
    not contain a ``financial_model`` block.  Defaults are policy-dependent and
    include a valuation date, so they must be materialized before hashing and
    persisted with the Calculation Run rather than generated invisibly inside
    the engine.
    """
    source = deepcopy(version.input_snapshot or {})
    planning = source.get("planning") or {}
    controls = policy_controls(policy_snapshot)
    raw_model = source.get("financial_model")
    if raw_model is None:
        raw_model = default_financial_model(planning=planning, controls=controls)
    source["financial_model"] = normalize_financial_model(raw_model, planning=planning, controls=controls)
    return json_ready(source)


def build_engine_project_snapshot(
    project: Project,
    version: ProjectVersion,
    financial_model: dict[str, Any],
    policy_snapshot: dict[str, Any],
    *,
    source_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = deepcopy(source_snapshot if source_snapshot is not None else (version.input_snapshot or {}))
    planning = source.get("planning") or {}
    controls = policy_controls(policy_snapshot)
    advanced = controls.get("advanced_defaults") or {}
    model = normalize_financial_model(financial_model, planning=planning, controls=controls)
    native = internal_input_snapshot(project, version, source_snapshot=source)
    native["valuation_date"] = model["valuation_date"]
    native["reporting_currency"] = (source.get("identity") or {}).get("currency") or native.get("reporting_currency") or "USD"
    current_land_value = D((source.get("land") or {}).get("current_land_value"), "0")
    native["land_value_baseline"] = str(current_land_value)
    native["reference_land_value_total"] = str(current_land_value)
    sales = model["sales"]
    delivery = model["delivery"]
    for product in native.get("products") or []:
        product.update({
            "sales_start_month": sales["start_month"],
            "sales_duration_months": sales["duration_months"],
            "sales_curve_type": sales["curve_type"],
            "sales_curve_intensity": sales["curve_intensity"],
            "commercial_discount_rate": sales["commercial_discount_rate"],
            "buyer_incentive_rate": sales["buyer_incentive_rate"],
            "refund_rate": sales["refund_rate"],
            "collection_rules": deepcopy(sales["collection_rules"]),
            "construction_start_month": delivery["construction_start_month"],
            "construction_duration_months": delivery["construction_duration_months"],
            "construction_curve_type": delivery["construction_curve_type"],
            "construction_cost_base_date": model["valuation_date"],
            "construction_escalation_rate": delivery["cost_escalation_rate"],
            "construction_contingency_rate": delivery["cost_contingency_rate"],
        })
    # v2.3.0 contract semantics: Net Sales is a REVENUE basis only.
    # Development/construction/marketing costs must never reduce the Net Sales
    # contract base. They remain project/developer costs and are tested by the
    # profitability, IRR, NPV and liquidity constraints. Legacy policy/category
    # flags are retained in stored snapshots for backward audit compatibility
    # but are deliberately ignored for Net Sales Share calculations.
    net_categories: set[str] = set()
    profit_categories = {str(item).upper() for item in controls.get("profit_share_cost_categories") or []}
    finance_rules = controls.get("finance_policy") or {}
    allow_financing = bool(finance_rules.get("allow_financing", True))
    defer_unfunded_costs = bool(finance_rules.get("defer_unfunded_costs", True))
    source_costs = source.get("costs") or []
    for index, cost in enumerate(native.get("costs") or []):
        portal_cost = source_costs[index] if index < len(source_costs) else {}
        category = str(cost.get("category") or portal_cost.get("category") or "CUSTOM").upper()
        explicit_net = portal_cost.get("net_sales_deductible")
        cost.update({
            "base_date": model["valuation_date"],
            "escalation_rate": delivery["cost_escalation_rate"],
            "contingency_rate": delivery["cost_contingency_rate"],
            "monthly_start_month": delivery["other_cost_start_month"],
            "monthly_duration_months": delivery["other_cost_duration_months"],
            "monthly_curve_type": delivery["other_cost_curve_type"],
            # Costs are NOT Net Sales deductions in Contract Engine 3.1.0.
            "eligible_net_sales_deduction_fraction": "0",
            "eligible_profit_share_cost_fraction": "1" if category in profit_categories else "0",
            "net_sales_deduction_treatment": "SALES_ADJUSTMENTS_ONLY",
            "legacy_net_sales_cost_deduction_ignored": bool(explicit_net) or category in net_categories,
            "deferrable": defer_unfunded_costs,
        })
    funding = model["funding"]
    native["funding"] = {
        "opening_cash": funding["opening_cash"],
        "committed_additional_equity": funding["committed_additional_equity"],
        "committed_equity": funding["committed_additional_equity"],
        "committed_equity_is_additional": True,
        "committed_financing": funding["committed_financing"] if model["finance"]["enabled"] and allow_financing else "0",
    }
    finance = model["finance"]
    native["finance_model"] = {
        "enabled": bool(finance["enabled"] and allow_financing),
        "annual_interest_rate": finance["annual_interest_rate"],
        "upfront_fee_rate": finance["upfront_fee_rate"],
        "commitment_fee_rate": finance["commitment_fee_rate"],
        "cash_sweep_share": finance["cash_sweep_share"],
        "capitalize_interest": finance["capitalize_interest"],
        "force_terminal_repayment": finance["force_terminal_repayment"],
        "minimum_cash_balance": finance["minimum_cash_balance"],
        "funding_draw_order": finance["funding_draw_order"],
        "spend_policy": finance["spend_policy"],
        "hybrid_minimum_execution_share": finance["hybrid_minimum_execution_share"],
        "future_cost_reserve_share": finance["future_cost_reserve_share"],
        "allow_negative_cash": False,
        "defer_contractual_payments": bool(finance["defer_contractual_payments"] and defer_unfunded_costs),
        "maximum_extension_months": delivery["maximum_extension_months"],
        "maximum_monthly_execution_share": delivery["maximum_monthly_execution_share"],
        "maximum_monthly_execution_amount": delivery["maximum_monthly_execution_amount"],
    }
    contract = model["contract"]
    maximum_search_amount = max(
        current_land_value * D(advanced.get("upfront_search_land_value_multiple"), "4"),
        sum((_cost_amount(row) for row in source_costs), Decimal("0")) * D(advanced.get("upfront_search_cost_multiple"), "2"),
        Decimal("1"),
    )
    native["landowner_studio"] = {
        "horizon_months": max(
            delivery["construction_start_month"] + delivery["construction_duration_months"],
            sales["start_month"] + sales["duration_months"] + max(int(row["lag_months"]) for row in sales["collection_rules"]),
        ) + int(advanced.get("horizon_buffer_months") or 12),
        "auto_extend_horizon": True,
        "allow_negative_cash": False,
        "use_committed_financing": bool(finance["enabled"] and allow_financing),
        "initial_cash": funding["opening_cash"],
        "other_cost_curve_type": delivery["other_cost_curve_type"],
        "other_cost_start_month": delivery["other_cost_start_month"],
        "other_cost_duration_months": delivery["other_cost_duration_months"],
        "land_value_recovery_share": _bounded_fraction(controls.get("minimum_landowner_value_recovery"), "1", maximum="10", label="Minimum landowner value recovery"),
        "upfront_amount": contract["upfront_amount"],
        "upfront_payment_month": contract["upfront_payment_month"],
        "upfront_search_cap": str(max(maximum_search_amount, D(contract["upfront_amount"]))),
        "hybrid_upfront_amount": contract["hybrid_upfront_amount"],
        "hybrid_upfront_payment_month": contract["hybrid_upfront_payment_month"],
        "hybrid_variable_basis": contract["hybrid_variable_basis"],
        "minimum_guarantee_amount": contract["minimum_guarantee_amount"],
        "minimum_guarantee_payment_month": contract["minimum_guarantee_payment_month"],
        "minimum_guarantee_underlying_method": contract["minimum_guarantee_underlying_method"],
        "minimum_guarantee_underlying_share": contract["minimum_guarantee_underlying_share"],
        "minimum_guarantee_search_cap": str(max(maximum_search_amount, D(contract["minimum_guarantee_amount"]))),
        "solver_grid_intervals": int(advanced.get("solver_grid_intervals") or 12),
        "distribution_reserve_months": int(advanced.get("distribution_reserve_months") or 12),
        "remaining_cost_reserve_share": str(advanced.get("remaining_cost_reserve_share") or "0.20"),
        "distribution_policy": {
            "enabled": True,
            "frequency_code": str(advanced.get("distribution_frequency_code") or "ANNUAL"),
            "first_distribution_month": int(advanced.get("first_distribution_month") or 12),
            "distribution_share": str(advanced.get("distribution_share") or "1"),
            "landowner_share": "0",
            "reserve_months": int(advanced.get("distribution_reserve_months") or 12),
            "remaining_cost_reserve_share": str(advanced.get("remaining_cost_reserve_share") or "0.20"),
            "prohibit_while_debt_outstanding": bool(advanced.get("prohibit_distributions_while_debt_outstanding", True)),
            "recover_developer_advances_before_landowner_cash": bool(advanced.get("recover_developer_advances_before_landowner_cash", True)),
            "settle_prior_obligations_before_distribution": bool(advanced.get("settle_prior_obligations_before_distribution", True)),
            "prohibit_before_completion": bool(advanced.get("prohibit_before_completion", False)),
        },
        "contract_methods": list(controls.get("allowed_contract_methods") or CONTRACT_METHODS),
        "recommendation_objective": (
            "MAX_PUBLIC_NPV" if str(controls.get("proposal_selection_method") or "BALANCED").upper() == "MAXIMUM_LANDOWNER_VALUE" else "BALANCED"
        ),
    }
    native["partnership"] = {
        "method": contract["method"],
        "share_rate": contract["share_rate"],
        "approved_selection": "MANUAL",
        "manual_share": contract["share_rate"],
        "net_deduction_treatment": contract["net_deduction_treatment"],
        "hybrid_variable_basis": contract["hybrid_variable_basis"],
        "upfront_payments": ([{"month": contract["upfront_payment_month"], "amount": contract["upfront_amount"]}] if D(contract["upfront_amount"]) > 0 else []),
    }
    native["portal_financial_model"] = deepcopy(model)
    native["source_input_snapshot"] = deepcopy(source)
    return json_ready(native)


def effective_engine_policy(policy_snapshot: dict[str, Any], project_snapshot: dict[str, Any]) -> dict[str, Any]:
    result = apply_policy_controls(policy_snapshot)
    controls = policy_controls(result)
    reference_land_value = D(project_snapshot.get("reference_land_value_total"), "0")
    recovery = D(_bounded_fraction(controls.get("minimum_landowner_value_recovery"), "1", maximum="10", label="Minimum landowner value recovery"))
    fixed_minimum = D(controls.get("minimum_landowner_npv"), "0")
    result.setdefault("share_policy", {})["minimum_government_value_npv"] = str(max(fixed_minimum, reference_land_value * recovery))
    result["financial_constraints"]["maximum_funding_gap"] = str(D(controls.get("maximum_funding_gap"), "0"))
    return json_ready(result)


def zero_landowner_consideration(project_snapshot: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(project_snapshot)
    result["partnership"] = {
        "method": "GROSS_SALES",
        "share_rate": "0",
        "approved_selection": "MANUAL",
        "manual_share": "0",
        "net_deduction_treatment": "CUMULATIVE_CARRY_FORWARD",
        "hybrid_variable_basis": "GROSS_SALES",
        "upfront_payments": [],
    }
    studio = result.setdefault("landowner_studio", {})
    studio.update({
        "contract_methods": ["GROSS_SALES"],
        "land_value_recovery_share": "0",
        "upfront_amount": "0",
        "hybrid_upfront_amount": "0",
        "minimum_guarantee_amount": "0",
        "minimum_guarantee_underlying_share": "0",
    })
    return result


def _annual_cashflow(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    numeric_fields = (
        "gross_contracted_sales", "net_contracted_sales", "gross_collections", "net_collections",
        "planned_cost", "actual_cost", "deferred_cost", "equity_contribution", "financing_draw",
        "interest_paid", "financing_fees", "financing_repayment", "government_payment",
        "landowner_cash_receipt", "developer_distribution", "unsupported_funding_gap", "mandatory_shortfall",
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        row_date = date.fromisoformat(str(row.get("date")))
        target = grouped.setdefault(row_date.year, {"year": row_date.year, **{field: Decimal("0") for field in numeric_fields}})
        for field in numeric_fields:
            target[field] += D(row.get(field), "0")
        target["ending_cash"] = D(row.get("ending_cash"), "0")
        target["ending_debt"] = D(row.get("ending_debt"), "0")
        target["contractual_arrears"] = D(row.get("government_payment_arrears"), "0")
    return [json_ready(grouped[year]) for year in sorted(grouped)]


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    truth = result.get("financial_truth") or {}
    source = result.get("summary") or {}
    keys = (
        "gross_potential_revenue", "gross_sales", "net_sales", "development_cost", "planned_total_cost",
        "project_profit", "developer_profit", "project_profit_on_cost", "project_profit_on_revenue",
        "developer_profit_on_cost", "developer_profit_on_revenue", "project_irr", "project_npv",
        "developer_equity_irr", "developer_equity_npv", "developer_npv", "developer_equity_multiple",
        "developer_equity_contributions", "developer_equity_distributions", "peak_equity", "peak_funding_gap",
        "peak_debt", "interest_total", "financing_fees_total", "ending_cash", "terminal_debt",
        "deferred_development_cost", "deferred_contractual_payment", "terminal_unpaid_obligations",
        "project_duration_months", "original_completion_date", "adjusted_completion_date",
        "schedule_extension_months", "calculation_status", "policy_compliant", "cash_reconciliation_passed",
        "economic_feasible", "method", "approved_share", "government_consideration", "government_consideration_npv",
    )
    summary = {key: truth.get(key, source.get(key)) for key in keys}
    summary.update({
        "development_cost": truth.get("development_cost", truth.get("planned_total_cost", source.get("planned_total_cost"))),
        "developer_equity_npv": truth.get("developer_equity_npv", truth.get("developer_npv", source.get("developer_npv"))),
        "developer_equity_multiple": truth.get("developer_equity_multiple", truth.get("developer_multiple", source.get("developer_multiple"))),
        "deferred_costs": truth.get("deferred_development_cost", source.get("terminal_deferred_cost")),
        "contractual_arrears": truth.get("deferred_contractual_payment", source.get("terminal_contractual_arrears")),
        "original_project_duration_months": source.get("planned_project_duration_months"),
        "adjusted_project_duration_months": truth.get("project_duration_months", source.get("project_duration_months")),
    })
    return json_ready(summary)


def calculate_residual(pre_land_result: dict[str, Any], policy_snapshot: dict[str, Any]) -> dict[str, Any]:
    controls = policy_controls(policy_snapshot)
    truth = pre_land_result.get("financial_truth") or {}
    summary = pre_land_result.get("summary") or {}
    gdv = D(truth.get("gross_potential_revenue", truth.get("gross_sales")), "0")
    development_costs = D(truth.get("development_cost", truth.get("planned_total_cost")), "0")
    finance_costs = D(truth.get("interest_total", summary.get("interest_total")), "0") + D(truth.get("financing_fees_total", summary.get("financing_fees_total")), "0")
    target_poc = D(_bounded_fraction(controls.get("target_developer_profit_on_cost"), "0.25", maximum="10", label="Target developer profit on cost"))
    residual = gdv / (Decimal("1") + target_poc) - development_costs - finance_costs
    land_capacity_dcf = D(truth.get("project_npv", summary.get("project_npv")), "0")
    return json_ready({
        "indication_type": "DEVELOPMENT_RESIDUAL_INDICATION",
        "label_ar": "مؤشر القيمة المتبقية التطويرية - وليس تقييماً سوقياً مستقلاً",
        "label_en": "Development Residual Indication - not an independent market valuation",
        "gross_development_value": gdv,
        "target_developer_profit_on_cost": target_poc,
        "development_costs": development_costs,
        "finance_costs": finance_costs,
        "residual_land_value": residual,
        "land_capacity_dcf": land_capacity_dcf,
        "calculated_before_landowner_consideration": True,
        "formula": "GDV / (1 + target developer profit on cost) - development costs - finance costs",
        "dcf_basis": "XNPV of the pre-land monthly project cash flow using the policy discount rate and ACT/365F dates",
    })



def _contract_definition_for_method(method: str, financial_model: dict[str, Any]) -> dict[str, Any]:
    contract = financial_model.get("contract") or {}
    rate = str(contract.get("share_rate") or "0")
    if method == "GROSS_SALES":
        return {"type": "GROSS_SALES_SHARE", "rate": rate}
    if method == "NET_SALES":
        return {"type": "NET_SALES_SHARE", "rate": rate}
    if method == "PROFIT_SHARE":
        return {"type": "PROFIT_SHARE", "rate": rate}
    if method == "UPFRONT":
        return {"type": "OUTRIGHT_SALE", "upfront_amount": str(contract.get("upfront_amount") or "0")}
    if method == "HYBRID":
        variable_basis = str(contract.get("hybrid_variable_basis") or "GROSS_SALES").upper()
        component_type = {
            "NET_SALES": "NET_SALES_SHARE",
            "PROFIT_SHARE": "PROFIT_SHARE",
        }.get(variable_basis, "GROSS_SALES_SHARE")
        return {
            "type": "HYBRID",
            "components": [
                {"type": "OUTRIGHT_SALE", "upfront_amount": str(contract.get("hybrid_upfront_amount") or "0")},
                {"type": component_type, "rate": rate},
            ],
        }
    return {"type": "MINIMUM_GUARANTEE", "guarantee_amount": str(contract.get("minimum_guarantee_amount") or "0")}


def _boundary_case(boundary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not boundary:
        return None
    return json_ready({
        "measure": boundary.get("engine_measure"),
        "government_value": boundary.get("public_nominal"),
        "government_gross_npv": boundary.get("public_npv"),
        "government_npv": boundary.get("public_npv"),
        "developer_irr": boundary.get("developer_irr"),
        "developer_equity_irr": boundary.get("developer_irr"),
        "developer_npv": boundary.get("developer_npv"),
        "developer_multiple": boundary.get("developer_moic"),
        "developer_profit_on_cost": boundary.get("profit_on_cost"),
        "peak_equity": boundary.get("peak_equity"),
        "peak_debt": boundary.get("peak_debt"),
        "peak_funding_gap": boundary.get("funding_gap"),
        "terminal_debt": boundary.get("terminal_debt"),
        "feasible": boundary.get("feasible"),
        "evaluation_status": boundary.get("evaluation_status"),
        "calculation_valid": boundary.get("calculation_valid"),
        "cash_reconciliation_passed": boundary.get("cash_reconciliation_passed"),
        "failed_constraints": boundary.get("failed_constraints") or [],
    })


def _apply_policy_adjusted_negotiation(
    *,
    raw_comparison: dict[str, Any],
    engine_project: dict[str, Any],
    engine_policy: dict[str, Any],
    financial_model: dict[str, Any],
    reference_land_value: Decimal,
    currency: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    method = str(raw_comparison.get("method") or "").upper()
    contract = _contract_definition_for_method(method, financial_model)
    fixed_component = Decimal("0")
    if method == "HYBRID":
        fixed_component = D(financial_model.get("contract", {}).get("hybrid_upfront_amount"), "0")
    cache: dict[str, dict[str, Any]] = {}

    def evaluate_measure(measure: Decimal) -> dict[str, Any]:
        normalized = max(Decimal("0"), measure) if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else max(Decimal("0"), min(Decimal("1"), measure))
        key = format(normalized, "f")
        if key not in cache:
            candidate = _apply_measure_to_project(
                engine_project,
                method=method,
                measure=normalized,
                fixed_component=fixed_component,
            )
            evaluated = run_unified_financial_engine(candidate, deepcopy(engine_policy), selected_only=True)
            cache[key] = _summary_from_unified_result(evaluated)
        return cache[key]

    controls = policy_controls(engine_policy)
    policy_minimum_measure = D(controls.get("minimum_landowner_share"), "0") if str(raw_comparison.get("measure_type") or "RATE").upper() == "RATE" else None
    native = build_native_negotiation(
        comparison=raw_comparison,
        contract=contract,
        market_low=reference_land_value,
        confidence_grade=None,
        data_confidence=Decimal("0.60"),
        risk_score=Decimal("0"),
        downside_survival=Decimal("0.70"),
        enforceability=Decimal("0.65"),
        target_developer_irr=D((engine_policy.get("financial_constraints") or {}).get("target_developer_irr"), "0.22"),
        evaluate_measure=evaluate_measure,
        risk_adjustment_policy=engine_policy.get("fair_consideration_policy") or {},
        valuation_policy=engine_policy.get("valuation_policy") or {},
        currency=currency,
        policy_minimum_measure=policy_minimum_measure,
    )
    clean = json_ready(raw_comparison)
    minimum = native.get("minimum") or {}
    balanced = native.get("balanced") or {}
    risk_ceiling = native.get("risk_adjusted_ceiling") or {}
    technical = native.get("technical_ceiling") or {}
    offer = native.get("offer") or {}
    if native.get("status") in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"}:
        clean.update({
            "fair_floor": minimum.get("engine_measure"),
            "minimum": minimum.get("engine_measure"),
            "balanced": balanced.get("engine_measure"),
            "recommended": balanced.get("engine_measure"),
            "policy_adjusted_ceiling": risk_ceiling.get("engine_measure"),
            "risk_adjusted_ceiling": risk_ceiling.get("engine_measure"),
            "technical_ceiling": technical.get("engine_measure"),
            "maximum": technical.get("engine_measure"),
            "negotiation_minimum": minimum.get("engine_measure"),
            "negotiation_maximum": risk_ceiling.get("engine_measure"),
            "minimum_case": _boundary_case(minimum),
            "balanced_case": _boundary_case(balanced),
            "recommended_case": _boundary_case(balanced),
            "policy_adjusted_ceiling_case": _boundary_case(risk_ceiling),
            "risk_adjusted_case": _boundary_case(risk_ceiling),
            "ceiling_case": _boundary_case(technical) or clean.get("ceiling_case"),
            "offer_case": _boundary_case(offer),
            "offer_position": native.get("offer_position"),
            "balanced_selection_method": next((x.get("selection_method") for x in native.get("why_this_range") or [] if x.get("code") == "BALANCED_POINT"), None),
            "negotiation_summary_ar": native.get("summary_ar"),
            "negotiation_summary_en": native.get("summary_en"),
            "negotiation_explanations": native.get("why_this_range") or [],
            "risk_adjustment": native.get("risk_adjustment") or {},
            "native_negotiation": native,
        })
    else:
        clean.update({
            "status": native.get("status") or clean.get("status"),
            "fair_floor": None,
            "balanced": None,
            "recommended": None,
            "policy_adjusted_ceiling": None,
            "risk_adjusted_ceiling": None,
            "negotiation_minimum": None,
            "negotiation_maximum": None,
            "offer_position": native.get("offer_position"),
            "negotiation_summary_ar": native.get("summary_ar"),
            "negotiation_summary_en": native.get("summary_en"),
            "negotiation_explanations": native.get("why_this_range") or [],
            "risk_adjustment": native.get("risk_adjustment") or {},
            "native_negotiation": native,
        })
    return clean, native


def run_financial_model(
    project: Project,
    version: ProjectVersion,
    policy_snapshot: dict[str, Any],
    *,
    source_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = deepcopy(source_snapshot if source_snapshot is not None else effective_project_input_snapshot(version, policy_snapshot))
    controls = policy_controls(policy_snapshot)
    financial_model = normalize_financial_model(source.get("financial_model"), planning=source.get("planning") or {}, controls=controls)
    source["financial_model"] = financial_model
    engine_project = build_engine_project_snapshot(
        project, version, financial_model, policy_snapshot, source_snapshot=source
    )
    engine_policy = effective_engine_policy(policy_snapshot, engine_project)
    result = run_unified_financial_engine(engine_project, engine_policy, selected_only=False)
    pre_land_project = zero_landowner_consideration(engine_project)
    pre_land_policy = deepcopy(engine_policy)
    pre_land_policy.setdefault("share_policy", {})["minimum_government_value_npv"] = "0"
    pre_land_result = run_unified_financial_engine(pre_land_project, pre_land_policy, selected_only=True)
    residual = calculate_residual(pre_land_result, engine_policy)
    monthly = json_ready(result.get("monthly_cashflow") or [])
    comparisons = []
    rank_map = {method: index + 1 for index, method in enumerate((result.get("recommendation_summary") or {}).get("ranked_methods") or [])}
    required_landowner_npv = D((engine_policy.get("share_policy") or {}).get("minimum_government_value_npv"), "0")
    reference_land_value = D(engine_project.get("reference_land_value_total", engine_project.get("land_value_baseline")), "0")
    currency = str((source.get("identity") or {}).get("currency") or "USD").upper()
    for row in result.get("contract_comparison") or []:
        clean, native_negotiation = _apply_policy_adjusted_negotiation(
            raw_comparison=row,
            engine_project=engine_project,
            engine_policy=engine_policy,
            financial_model=financial_model,
            reference_land_value=reference_land_value,
            currency=currency,
        )
        # A binary solver can return a tiny positive number when the policy's
        # required landowner NPV is exactly zero.  That value is only numerical
        # search tolerance; it is not an economically meaningful Fair Floor.
        if required_landowner_npv <= Decimal("0.01"):
            clean["fair_floor"] = None
            clean["negotiation_minimum"] = None
            clean["fair_floor_status"] = "NOT_ESTABLISHED"
            clean["fair_floor_reason_ar"] = "لم يتم تحديد حد أدنى اقتصادي لصاحب الأرض لأن السياسة لا تحتوي قيمة أرض مرجعية أو حد NPV أدنى موجب."
            clean["fair_floor_reason_en"] = "No economic Fair Floor is established because the policy has no positive reference land value or minimum landowner NPV."
        elif clean.get("fair_floor") not in (None, ""):
            clean["fair_floor_status"] = "ESTABLISHED"
            clean["negotiation_minimum"] = clean.get("fair_floor")
            clean["fair_floor_reason_ar"] = "الحد الأدنى الذي يحقق قيمة صاحب الأرض المطلوبة وفق السياسة المالية."
            clean["fair_floor_reason_en"] = "Minimum consideration that satisfies the policy-required landowner value."
        else:
            clean["fair_floor_status"] = "UNAVAILABLE"
            clean["negotiation_minimum"] = None
            clean["fair_floor_reason_ar"] = "تعذر إيجاد حد أدنى يحقق قيمة صاحب الأرض ضمن السقف الفني المتاح."
            clean["fair_floor_reason_en"] = "No Fair Floor could be found within the technical ceiling while satisfying the required landowner value."
        raw_ceiling = clean.get("technical_ceiling")
        if clean.get("negotiation_maximum") in (None, ""):
            clean["negotiation_maximum"] = clean.get("policy_adjusted_ceiling") or raw_ceiling
        clean["recommendation_rank"] = rank_map.get(clean.get("method"))

        # Distinguish a true economic/technical ceiling from merely reaching
        # the administrator's search cap. A feasible result exactly at the cap
        # is a lower bound on the true ceiling, not proof of the ceiling itself.
        method = str(clean.get("method") or "").upper()
        measure_type = str(clean.get("measure_type") or "RATE").upper()
        policy_max_share = D((engine_policy.get("share_policy") or {}).get("policy_maximum_share"), "0.50")
        ceiling_d = D(raw_ceiling, "0") if raw_ceiling not in (None, "") else None
        if measure_type == "RATE" and ceiling_d is not None and abs(ceiling_d - policy_max_share) <= Decimal("0.0000001"):
            clean["ceiling_kind"] = "POLICY_CAP_REACHED"
            clean["policy_cap_reached"] = str(policy_max_share)
            clean["technical_ceiling_established"] = False
            clean["technical_ceiling_lower_bound"] = str(policy_max_share)
            clean["governing_constraint_id"] = "MAX_LANDOWNER_SHARE_POLICY_CAP"
        else:
            clean["ceiling_kind"] = "TECHNICAL_CEILING" if raw_ceiling not in (None, "") else "NOT_ESTABLISHED"
            clean["policy_cap_reached"] = None
            clean["technical_ceiling_established"] = raw_ceiling not in (None, "")

        # Residual land value is a comparison reference, not a contractual
        # entitlement. Express it both as money and, where meaningful, as an
        # equivalent contract measure on the SAME disclosed calculation base.
        residual_amount = D(residual.get("residual_land_value"), "0")
        eligible_base = D(clean.get("eligible_base_total"), "0")
        if eligible_base <= 0:
            result_truth = result.get("financial_truth") or result.get("summary") or {}
            if method == "GROSS_SALES":
                eligible_base = D(result_truth.get("gross_sales", result_truth.get("gross_potential_revenue")), "0")
            elif method == "NET_SALES":
                eligible_base = D(result_truth.get("net_sales"), "0")
            elif method == "HYBRID":
                hybrid_basis = str(financial_model.get("contract", {}).get("hybrid_variable_basis") or "GROSS_SALES").upper()
                eligible_base = D(result_truth.get("net_sales" if hybrid_basis == "NET_SALES" else "gross_sales"), "0") if hybrid_basis != "PROFIT_SHARE" else eligible_base
            if eligible_base > 0:
                clean["eligible_base_total"] = str(eligible_base)
        fixed_component = Decimal("0")
        if method == "HYBRID":
            fixed_component = D(financial_model.get("contract", {}).get("hybrid_upfront_amount"), "0")
        if measure_type == "RATE" and eligible_base > 0:
            residual_measure = max(Decimal("0"), residual_amount - fixed_component) / eligible_base
        elif measure_type == "AMOUNT":
            residual_measure = residual_amount
        else:
            residual_measure = None
        clean["residual_land_value"] = str(residual_amount)
        clean["residual_equivalent_measure"] = None if residual_measure is None else str(residual_measure)
        clean["residual_comparison_basis"] = clean.get("basis_label")
        balanced_case = clean.get("balanced_case") or clean.get("recommended_case") or {}
        balanced_land = D(balanced_case.get("government_value", clean.get("government_value")), "0")
        clean["balanced_landowner_value"] = str(balanced_land)
        clean["balanced_vs_residual_amount"] = str(balanced_land - residual_amount)
        if balanced_land < residual_amount - Decimal("0.01"):
            clean["balanced_vs_residual_status"] = "BELOW_RESIDUAL"
        elif balanced_land > residual_amount + Decimal("0.01"):
            clean["balanced_vs_residual_status"] = "ABOVE_RESIDUAL"
        else:
            clean["balanced_vs_residual_status"] = "AT_RESIDUAL"
        comparisons.append(clean)
    valid_comparisons = [row for row in comparisons if row.get("status") in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"} and row.get("balanced_case")]
    proposal_method = str(controls.get("proposal_selection_method") or "BALANCED").upper()
    if valid_comparisons:
        if proposal_method == "MAXIMUM_LANDOWNER_VALUE":
            recommended_row = max(valid_comparisons, key=lambda item: D((item.get("policy_adjusted_ceiling_case") or {}).get("government_npv"), "0"))
            recommended_case = recommended_row.get("policy_adjusted_ceiling_case") or recommended_row.get("balanced_case") or {}
            recommended_measure = recommended_row.get("policy_adjusted_ceiling") or recommended_row.get("balanced")
            recommendation_basis = "MAXIMUM_LANDOWNER_VALUE_WITHIN_POLICY_RANGE"
        else:
            recommended_row = min(valid_comparisons, key=lambda item: item.get("recommendation_rank") or 9999)
            recommended_case = recommended_row.get("balanced_case") or {}
            recommended_measure = recommended_row.get("balanced")
            recommendation_basis = "POLICY_ADJUSTED_BALANCED_POINT"
        recommended_contract = json_ready({
            "method": recommended_row.get("method"),
            "measure": recommended_measure,
            "measure_type": recommended_row.get("measure_type"),
            "fair_floor": recommended_row.get("fair_floor"),
            "balanced": recommended_row.get("balanced"),
            "policy_adjusted_ceiling": recommended_row.get("policy_adjusted_ceiling"),
            "technical_ceiling": recommended_row.get("technical_ceiling"),
            "recommendation_basis": recommendation_basis,
            **recommended_case,
        })
        recommendation_summary = deepcopy(result.get("recommendation_summary") or {})
        recommendation_summary.update({
            "recommended_method": recommended_row.get("method"),
            "recommended_measure": recommended_measure,
            "recommendation_basis": recommendation_basis,
            "policy_adjusted": True,
        })
    else:
        recommended_contract = json_ready(result.get("recommended_contract") or {})
        recommendation_summary = json_ready(result.get("recommendation_summary") or {})
    summary = _summary(result)
    truth = json_ready(result.get("financial_truth") or {})
    opening_equity = D(financial_model.get("funding", {}).get("opening_cash"), "0")
    total_equity_commitment = D(financial_model.get("funding", {}).get("total_developer_equity"), str(opening_equity))
    summary.update(json_ready({
        "total_developer_equity_commitment": total_equity_commitment,
        "initial_equity_contribution": opening_equity,
        "additional_equity_capacity": max(total_equity_commitment - opening_equity, Decimal("0")),
        "actual_equity_contributions": truth.get("developer_equity_contributions"),
        "landowner_cash_receipts": truth.get("landowner_cash_receipts"),
    }))
    combined = {
        "schema_version": "standalone-financial-result-2.2.0",
        "project_snapshot": engine_project,
        "effective_policy_snapshot": engine_policy,
        "financial_model": financial_model,
        "summary": summary,
        "financial_truth": truth,
        "residual_valuation": residual,
        "annual_cashflow": _annual_cashflow(monthly),
        "monthly_cashflow": monthly,
        "negotiation_results": comparisons,
        "selected_contract": json_ready(result.get("selected_contract") or {}),
        "recommended_contract": recommended_contract,
        "recommendation_summary": recommendation_summary,
        "constraints": json_ready((result.get("selected_contract") or {}).get("constraints") or []),
        "engine_manifest": json_ready(result.get("engine_manifest") or engine_manifest()),
        "engine_version": result.get("engine_version") or ENGINE_VERSION,
        "unified_engine_adapter_version": result.get("unified_engine_adapter_version") or UNIFIED_ENGINE_ADAPTER_VERSION,
        "portal_adapter_version": PORTAL_FINANCIAL_ADAPTER_VERSION,
        "contract_engine_version": LANDOWNER_CONTRACT_ENGINE_VERSION,
        "calculation_hash": result.get("calculation_hash"),
        "pre_land_calculation_hash": pre_land_result.get("calculation_hash"),
        "raw_engine_result": json_ready(result),
    }
    audit_result = audit_financial_result(
        monthly=monthly,
        truth=truth,
        summary=summary,
        financial_model=financial_model,
        effective_policy=engine_policy,
        negotiation_results=comparisons,
    )
    combined["financial_audit"] = json_ready(audit_result)
    if audit_result.get("validation_status") == "BLOCKED":
        recommendation_status = "BLOCKED"
        reason_ar = "تم حجب التوصية لأن التدقيق المالي المستقل كشف فشلاً جوهرياً في المصالحة أو الإغلاق أو مطابقة النتائج."
        reason_en = "Recommendation withheld because the independent financial audit found a material reconciliation, closure, or result-consistency failure."
    elif not audit_result.get("recommendation_usable"):
        recommendation_status = "CONDITIONAL"
        reason_ar = "الحسابات قابلة للاستخدام، لكن التوصية التفاوضية مشروطة إلى أن يتم تثبيت حد أدنى اقتصادي لصاحب الأرض ونطاق تفاوضي صالح."
        reason_en = "Calculations are usable, but the negotiation recommendation is conditional until an economic landowner floor and valid range are established."
    else:
        recommendation_status = "SUPPORTED"
        reason_ar = "التوصية مدعومة بالتدفقات الشهرية، فحوص السياسة، والتدقيق المالي المستقل."
        reason_en = "Recommendation is supported by monthly cash flows, policy constraints, and the independent financial audit."
    combined["recommendation_validation"] = {
        "status": recommendation_status,
        "usable": recommendation_status == "SUPPORTED",
        "reason_ar": reason_ar,
        "reason_en": reason_en,
    }
    combined["result_hash"] = sha256_json({key: value for key, value in combined.items() if key != "result_hash"})
    return combined


def engine_source_hash() -> str:
    package_root = Path(__file__).resolve().parents[1]
    roots = [
        Path(__file__).resolve(),
        package_root / "landvalue360_portal" / "packages.py",
        package_root / "landvalue360_kernel",
        package_root / "landvalue360_common",
        package_root / "landvalue360_government",
        package_root / "landvalue360_valuation",
        package_root / "landvalue360_finance",
        package_root / "landvalue360_risk",
        package_root / "landvalue360_server",
    ]
    digest = hashlib.sha256()
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts))
        elif root.exists():
            files.append(root)
    for path in sorted(set(files), key=lambda item: str(item)):
        digest.update(str(path.relative_to(package_root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def engine_registration_manifest() -> dict[str, Any]:
    return {
        **engine_manifest(),
        "unified_engine_adapter_version": UNIFIED_ENGINE_ADAPTER_VERSION,
        "portal_financial_adapter_version": PORTAL_FINANCIAL_ADAPTER_VERSION,
        "source_hash": engine_source_hash(),
        "source": "Vendored and version-pinned from LandValue360 Platform 2.1.1",
    }
