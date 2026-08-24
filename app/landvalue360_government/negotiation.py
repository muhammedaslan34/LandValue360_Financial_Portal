"""Contract-aware negotiation ranges for Landowner interface.

The unified engine solves each contract in its native economic unit.  This
module preserves that native unit (percentage, upfront amount, or hybrid
percentage plus fixed amount) instead of presenting every result as a currency
amount.  Monetary NPVs remain available as audit evidence, but they are not
misrepresented as contract terms.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Callable

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


METHOD_META: dict[str, dict[str, str]] = {
    "GROSS_SALES": {
        "contract_type": "GROSS_SALES_SHARE",
        "measure_type": "PERCENT",
        "label_en": "Gross sales share",
        "label_ar": "نسبة من إجمالي المبيعات",
        "measure_label_en": "Share of eligible gross cash collections",
        "measure_label_ar": "نسبة من إجمالي التحصيلات البيعية المؤهلة",
        "basis_en": "Eligible gross cash collections after cancellations and refunds defined by the contract.",
        "basis_ar": "إجمالي التحصيلات البيعية المؤهلة بعد الإلغاءات والمرتجعات وفق التعريف التعاقدي.",
    },
    "NET_SALES": {
        "contract_type": "NET_SALES_SHARE",
        "measure_type": "PERCENT",
        "label_en": "Net sales share",
        "label_ar": "نسبة من صافي المبيعات",
        "measure_label_en": "Share of eligible net sales",
        "measure_label_ar": "نسبة من صافي المبيعات المؤهلة",
        "basis_en": "Eligible net sales after only the deductions permitted by the deduction registry.",
        "basis_ar": "صافي المبيعات بعد الاستقطاعات المسموحة حصراً في سجل الاستقطاعات.",
    },
    "PROFIT_SHARE": {
        "contract_type": "PROFIT_SHARE",
        "measure_type": "PERCENT",
        "label_en": "Profit share",
        "label_ar": "نسبة من الربح",
        "measure_label_en": "Share of distributable project profit",
        "measure_label_ar": "نسبة من ربح المشروع القابل للتوزيع",
        "basis_en": "Distributable project profit after eligible costs, return of capital and the configured waterfall.",
        "basis_ar": "ربح المشروع القابل للتوزيع بعد الكلف المؤهلة وإعادة رأس المال وآلية التوزيع المعتمدة.",
    },
    "UPFRONT": {
        "contract_type": "OUTRIGHT_SALE",
        "measure_type": "AMOUNT",
        "label_en": "Direct sale",
        "label_ar": "بيع مباشر",
        "measure_label_en": "Upfront purchase price",
        "measure_label_ar": "ثمن البيع المباشر",
        "basis_en": "Purchase price payable under the entered payment timing and discount basis.",
        "basis_ar": "ثمن البيع المستحق وفق توقيت السداد وأساس الخصم المدخلين.",
    },
    "HYBRID": {
        "contract_type": "HYBRID",
        "measure_type": "HYBRID_PERCENT",
        "label_en": "Hybrid upfront plus gross sales share",
        "label_ar": "دفعة مقدمة مع نسبة من إجمالي المبيعات",
        "measure_label_en": "Variable gross-sales share plus fixed upfront amount",
        "measure_label_ar": "نسبة متغيرة من إجمالي المبيعات مع دفعة مقدمة ثابتة",
        "basis_en": "A fixed upfront amount combined with a variable share of eligible gross cash collections.",
        "basis_ar": "دفعة مقدمة ثابتة مضافة إلى نسبة متغيرة من إجمالي التحصيلات البيعية المؤهلة.",
    },
    "MINIMUM_GUARANTEE": {
        "contract_type": "MINIMUM_GUARANTEE",
        "measure_type": "AMOUNT",
        "label_en": "Minimum guarantee with upside participation",
        "label_ar": "حد أدنى مضمون مع مشاركة في الزيادة",
        "measure_label_en": "Cumulative guaranteed consideration floor",
        "measure_label_ar": "الحد الأدنى التراكمي المضمون للمقابل",
        "basis_en": "The guarantee is a cumulative top-up only; underlying participation consumes prior top-ups and is not paid twice.",
        "basis_ar": "الضمان تكملة تراكمية فقط؛ تُستهلك تكملاته السابقة من المشاركة الأساسية ولا يُدفع المقابل مرتين.",
    },
}

CONTRACT_TO_METHOD = {
    "GROSS_SALES_SHARE": "GROSS_SALES",
    "NET_SALES_SHARE": "NET_SALES",
    "PROFIT_SHARE": "PROFIT_SHARE",
    "OUTRIGHT_SALE": "UPFRONT",
    "HYBRID": "HYBRID",
    "MINIMUM_GUARANTEE": "MINIMUM_GUARANTEE",
}

CONSTRAINT_LABELS: dict[str, tuple[str, str]] = {
    "MIN_DEVELOPER_IRR": ("Minimum developer equity IRR", "الحد الأدنى لعائد حقوق ملكية المطور"),
    "MIN_DEVELOPER_NPV": ("Minimum developer NPV", "الحد الأدنى للقيمة الحالية للمطور"),
    "MIN_PROFIT_ON_COST": ("Minimum profit on cost", "الحد الأدنى للربح على الكلفة"),
    "MIN_DEVELOPER_MULTIPLE": ("Minimum developer equity multiple", "الحد الأدنى لمضاعف حقوق ملكية المطور"),
    "MAX_RESIDUAL_FUNDING_GAP": ("Maximum funding gap", "الحد الأقصى لفجوة التمويل"),
    "COMPLETE_SCOPE": ("Complete modeled scope", "اكتمال نطاق المشروع"),
    "MANDATORY_PAYMENT_SHORTFALL": ("Mandatory payment shortfall", "عجز الالتزامات الإلزامية"),
    "TERMINAL_DEBT": ("Terminal debt", "الدين النهائي"),
    "PROFIT_SHARE_CONVERGENCE": ("Profit-share convergence", "تقارب حساب المشاركة في الربح"),
    "MIN_GOVERNMENT_VALUE_NPV": ("Minimum public value NPV", "الحد الأدنى للقيمة الحالية العامة"),
}


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)
    return result if result.is_finite() else Decimal(default)


def fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(+value, "f")


def _number(value: Any, places: int = 2) -> str:
    number = D(value)
    quantum = Decimal(1).scaleb(-places)
    rendered = f"{number.quantize(quantum):,.{places}f}"
    return rendered.rstrip("0").rstrip(".")


def _compact_value(value: Any, places: int = 4) -> str:
    if value in (None, ""):
        return "not available"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)
    return _number(number, places) if number.is_finite() else str(value)


def _term(value: Decimal, method: str) -> str:
    displayed = display_measure(value, method)
    return _number(displayed, 2) if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else f"{_number(displayed, 2)}%"


def _money(value: Any, currency: str) -> str:
    return f"{str(currency or 'USD').upper()} {_number(value, 2)}"


def _ratio_percent(value: Any) -> str:
    return f"{_number(D(value) * HUNDRED, 2)}%"


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def clamp(value: Decimal, low: Decimal = ZERO, high: Decimal = ONE) -> Decimal:
    return max(low, min(high, value))


def _round_to_increment(value: Decimal, increment: Decimal, mode: str) -> Decimal:
    if increment <= ZERO:
        return value
    rounding = {
        "UP": ROUND_CEILING,
        "DOWN": ROUND_FLOOR,
        "NEAREST": ROUND_HALF_UP,
    }.get(mode, ROUND_HALF_UP)
    units = (value / increment).to_integral_value(rounding=rounding)
    return units * increment


def method_from_contract(contract: dict[str, Any]) -> str:
    return CONTRACT_TO_METHOD.get(str(contract.get("type") or "").upper(), "GROSS_SALES")


def metadata_for_method(method: str) -> dict[str, str]:
    return dict(METHOD_META.get(str(method).upper(), METHOD_META["GROSS_SALES"]))


def _hybrid_components(contract: dict[str, Any]) -> tuple[Decimal, Decimal]:
    fixed = ZERO
    rate = ZERO
    for component in contract.get("components") or []:
        kind = str(component.get("type") or "").upper()
        if kind == "OUTRIGHT_SALE":
            fixed += D(component.get("upfront_amount"))
        elif kind in {"GROSS_SALES_SHARE", "NET_SALES_SHARE", "PROFIT_SHARE"}:
            rate += D(component.get("rate"))
    return fixed, rate


def offered_engine_measure(contract: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Return variable/native engine measure and fixed hybrid component.

    Rate contracts return a fraction (0.18 means 18%).  Direct sale returns the
    amount itself.  Hybrid returns its variable rate plus the fixed amount.
    """

    method = method_from_contract(contract)
    if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        return D(contract.get("upfront_amount") if method == "UPFRONT" else contract.get("guarantee_amount")), ZERO
    if method == "HYBRID":
        fixed, rate = _hybrid_components(contract)
        return rate, fixed
    return D(contract.get("rate")), ZERO


def display_measure(engine_measure: Decimal, method: str) -> Decimal:
    return engine_measure if str(method).upper() in {"UPFRONT", "MINIMUM_GUARANTEE"} else engine_measure * HUNDRED


def engine_measure(display_value: Decimal, method: str) -> Decimal:
    return display_value if str(method).upper() in {"UPFRONT", "MINIMUM_GUARANTEE"} else display_value / HUNDRED


def _summary_from_case(case: dict[str, Any] | None) -> dict[str, Any]:
    case = case or {}
    return {
        "public_npv": case.get("government_npv"),
        "public_nominal": case.get("government_value"),
        "developer_irr": _first_present(case.get("developer_equity_irr"), case.get("developer_irr")),
        "developer_moic": case.get("developer_multiple"),
        "developer_npv": case.get("developer_npv"),
        "profit_on_cost": case.get("developer_profit_on_cost"),
        "peak_equity": case.get("peak_equity"),
        "peak_debt": case.get("peak_debt"),
        "funding_gap": _first_present(case.get("peak_funding_gap"), case.get("funding_gap")),
        "terminal_debt": case.get("terminal_debt"),
        "evaluation_status": case.get("evaluation_status"),
        "calculation_valid": bool(_first_present(case.get("calculation_valid"), default=True)),
        "cash_reconciliation_passed": bool(_first_present(case.get("cash_reconciliation_passed"), default=True)),
        "feasible": bool(case.get("feasible", True)),
        "constraints": case.get("constraints") or [],
        "failed_constraints": [
            row.get("constraint_id")
            for row in case.get("constraints") or []
            if row.get("passed") is False
        ],
    }


def _boundary(
    key: str,
    engine_value: Decimal,
    method: str,
    summary: dict[str, Any],
    *,
    reason_en: str,
    reason_ar: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "value": fmt(display_measure(engine_value, method)),
        "engine_measure": fmt(engine_value),
        "public_npv": summary.get("public_npv"),
        "public_nominal": summary.get("public_nominal"),
        "developer_irr": summary.get("developer_irr"),
        "developer_moic": summary.get("developer_moic"),
        "developer_npv": summary.get("developer_npv"),
        "profit_on_cost": summary.get("profit_on_cost"),
        "peak_equity": summary.get("peak_equity"),
        "peak_debt": summary.get("peak_debt"),
        "funding_gap": summary.get("funding_gap"),
        "terminal_debt": summary.get("terminal_debt"),
        "evaluation_status": summary.get("evaluation_status"),
        "calculation_valid": bool(_first_present(summary.get("calculation_valid"), default=True)),
        "cash_reconciliation_passed": bool(_first_present(summary.get("cash_reconciliation_passed"), default=True)),
        "feasible": bool(summary.get("feasible", True)),
        "failed_constraints": list(summary.get("failed_constraints") or []),
        "reason_en": reason_en,
        "reason_ar": reason_ar,
    }


def _constraint_details(comparison: dict[str, Any]) -> dict[str, Any] | None:
    failures = list(comparison.get("failure_reasons") or [])
    constraint_id = comparison.get("governing_constraint_id") or (failures[0].get("constraint_id") if failures else None)
    if not constraint_id:
        return None
    ceiling = comparison.get("ceiling_case") or comparison.get("best_candidate") or {}
    row = next(
        (item for item in ceiling.get("constraints") or [] if item.get("constraint_id") == constraint_id),
        None,
    ) or next((item for item in failures if item.get("constraint_id") == constraint_id), {})
    label_en, label_ar = CONSTRAINT_LABELS.get(str(constraint_id), (str(constraint_id).replace("_", " ").title(), str(constraint_id).replace("_", " ")))
    return {
        "id": constraint_id,
        "label_en": label_en,
        "label_ar": label_ar,
        "actual": row.get("actual"),
        "operator": row.get("operator"),
        "threshold": row.get("threshold"),
        "reason_en": row.get("reason") or "This constraint is the first one expected to fail above the technical ceiling.",
        "reason_ar": "هذا القيد هو أول قيد متوقع أن يفشل عند تجاوز السقف الفني.",
    }


def _offer_position(offer: Decimal, minimum: Decimal, risk: Decimal, technical: Decimal, *, valid: bool) -> str:
    if not valid:
        return "NO_FEASIBLE_RANGE"
    tolerance = Decimal("0.0000001")
    if offer + tolerance < minimum:
        return "BELOW_MINIMUM"
    if offer <= risk + tolerance:
        return "WITHIN_RECOMMENDED_RANGE"
    if offer <= technical + tolerance:
        return "ABOVE_RISK_ADJUSTED_CEILING"
    return "ABOVE_TECHNICAL_CEILING"


def _find_public_value_floor(
    low: Decimal,
    high: Decimal,
    required_npv: Decimal,
    evaluate: Callable[[Decimal], dict[str, Any]],
) -> tuple[Decimal | None, dict[str, Any] | None]:
    """Find the lowest measure that reaches the public NPV floor.

    The public value of the supported contract methods is expected to be
    non-decreasing in the native measure.  The returned value is validated by a
    direct engine run; no display-rounded number is used.
    """

    low_summary = evaluate(low)
    if not low_summary.get("calculation_valid", True):
        return None, low_summary
    if D(low_summary.get("public_npv")) >= required_npv:
        return low, low_summary
    high_summary = evaluate(high)
    if not high_summary.get("calculation_valid", True):
        return None, high_summary
    if D(high_summary.get("public_npv")) < required_npv:
        return None, high_summary
    left, right = low, high
    selected = high_summary
    for _ in range(12):
        midpoint = (left + right) / Decimal("2")
        summary = evaluate(midpoint)
        if not summary.get("calculation_valid", True):
            return None, summary
        if D(summary.get("public_npv")) >= required_npv:
            right = midpoint
            selected = summary
        else:
            left = midpoint
    return right, selected


def build_native_negotiation(
    *,
    comparison: dict[str, Any] | None,
    contract: dict[str, Any],
    market_low: Decimal,
    confidence_grade: str | None,
    data_confidence: Decimal,
    risk_score: Decimal,
    downside_survival: Decimal,
    enforceability: Decimal,
    target_developer_irr: Decimal,
    evaluate_measure: Callable[[Decimal], dict[str, Any]],
    risk_adjustment_policy: dict[str, Any] | None = None,
    valuation_policy: dict[str, Any] | None = None,
    currency: str = "USD",
    policy_minimum_measure: Decimal | None = None,
) -> dict[str, Any]:
    """Build a contract-native range and its auditable explanation."""

    method = method_from_contract(contract)
    meta = metadata_for_method(method)
    offered, fixed_component = offered_engine_measure(contract)
    comparison = comparison or {}
    status = str(comparison.get("status") or "NO_COMPARISON")
    floor_raw = _first_present(comparison.get("fair_floor"), comparison.get("minimum"))
    balanced_raw = _first_present(comparison.get("balanced"), comparison.get("recommended"))
    ceiling_raw = _first_present(comparison.get("technical_ceiling"), comparison.get("maximum"))
    valid = floor_raw is not None and ceiling_raw is not None and status in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"}

    base: dict[str, Any] = {
        "contract_method": method,
        "contract_type": meta["contract_type"],
        "contract_label_en": meta["label_en"],
        "contract_label_ar": meta["label_ar"],
        "measure_type": meta["measure_type"],
        "measure_label_en": meta["measure_label_en"],
        "measure_label_ar": meta["measure_label_ar"],
        "basis_en": meta["basis_en"],
        "basis_ar": meta["basis_ar"],
        "unit": "%" if method not in {"UPFRONT", "MINIMUM_GUARANTEE"} else "CURRENCY",
        "fixed_component": fmt(fixed_component) if fixed_component > ZERO else None,
        "solver_status": status,
        "search_method": comparison.get("search_method"),
        "monotonic": bool(comparison.get("monotonic", True)),
        "feasible_region_count": comparison.get("feasible_region_count"),
    }
    if not valid:
        numerical = status == "NUMERICALLY_UNRESOLVED"
        reason_en = (
            "The numerical calculation did not converge or reconcile, so no economic range was issued."
            if numerical
            else "The engine could not establish a defensible feasible range for the selected contract model."
        )
        reason_ar = (
            "لم يكتمل الحل العددي أو المصالحة، ولذلك لم يصدر النظام نطاقاً اقتصادياً."
            if numerical
            else "لم يتمكن المحرك من تحديد نطاق قابل للدفاع وقابل للتنفيذ لنموذج التعاقد المحدد."
        )
        unresolved_summary = evaluate_measure(offered)
        base.update(
            {
                "status": "NUMERICALLY_UNRESOLVED" if numerical or not unresolved_summary.get("calculation_valid", True) else "NO_FEASIBLE_RANGE",
                "offer_position": "NUMERICALLY_UNRESOLVED" if numerical or not unresolved_summary.get("calculation_valid", True) else "NO_FEASIBLE_RANGE",
                "minimum": None,
                "balanced": None,
                "risk_adjusted_ceiling": None,
                "technical_ceiling": None,
                "offer": _boundary("offer", offered, method, unresolved_summary, reason_en=reason_en, reason_ar=reason_ar),
                "range": {"low": None, "high": None},
                "summary_en": reason_en,
                "summary_ar": reason_ar,
                "why_this_range": [
                    {
                        "code": "NUMERICALLY_UNRESOLVED" if numerical else "NO_FEASIBLE_RANGE",
                        "title_en": "Numerical calculation unresolved" if numerical else "No feasible contractual range",
                        "title_ar": "الحساب العددي غير محسوم" if numerical else "لا يوجد نطاق تعاقدي قابل للتنفيذ",
                        "detail_en": reason_en,
                        "detail_ar": reason_ar,
                    },
                    *[
                        {
                            "code": row.get("constraint_id") or "FAILED_CONSTRAINT",
                            "title_en": row.get("label") or str(row.get("constraint_id") or "Failed constraint").replace("_", " ").title(),
                            "title_ar": str(row.get("label_ar") or row.get("label") or row.get("constraint_id") or "قيد فاشل"),
                            "detail_en": f"Actual {row.get('actual')} {row.get('operator') or ''} required {row.get('threshold')}; {row.get('reason') or ''}".strip(),
                            "detail_ar": f"القيمة الفعلية {row.get('actual')} والمطلوب {row.get('operator') or ''} {row.get('threshold')}. {row.get('reason_ar') or row.get('reason') or ''}".strip(),
                        }
                        for row in (comparison.get("failure_reasons") or [])[:5]
                    ],
                ],
                "failure_reasons": comparison.get("failure_reasons") or [],
                "diagnostics": comparison.get("diagnostics") or {"best_candidate": comparison.get("best_candidate")},
                "governing_constraint": _constraint_details(comparison),
            }
        )
        return base

    floor = D(floor_raw)
    core_balanced = D(balanced_raw, fmt(floor) or "0")
    technical = D(ceiling_raw)
    if technical < floor:
        floor, technical = technical, floor

    core_min_summary = _summary_from_case(comparison.get("minimum_case"))
    core_technical_summary = _summary_from_case(comparison.get("ceiling_case"))
    if not core_min_summary.get("calculation_valid", True) or not core_technical_summary.get("calculation_valid", True):
        reason_en = "A numerical or cash-reconciliation failure occurred at a proposed range boundary; no economic range is valid."
        reason_ar = "حدث فشل عددي أو فشل في مصالحة النقد عند أحد حدود النطاق؛ لذلك لا يوجد نطاق اقتصادي صالح."
        base.update({
            "status": "NUMERICALLY_UNRESOLVED",
            "offer_position": "NUMERICALLY_UNRESOLVED",
            "minimum": None,
            "balanced": None,
            "risk_adjusted_ceiling": None,
            "technical_ceiling": None,
            "offer": _boundary("offer", offered, method, evaluate_measure(offered), reason_en=reason_en, reason_ar=reason_ar),
            "range": {"low": None, "high": None},
            "summary_en": reason_en,
            "summary_ar": reason_ar,
            "why_this_range": [{"code": "NUMERICALLY_UNRESOLVED", "title_en": "Numerical calculation unresolved", "title_ar": "الحساب العددي غير محسوم", "detail_en": reason_en, "detail_ar": reason_ar}],
            "governing_constraint": _constraint_details(comparison),
        })
        return base
    required_public_npv = max(market_low, D(comparison.get("required_public_npv")))
    public_floor_tolerance = max(Decimal("0.01"), abs(required_public_npv) * Decimal("0.0000000001"))
    # The core solver already refines and validates its minimum boundary.  Reuse
    # that result when it satisfies the same or a lower public-value floor.
    # This removes a duplicate 12-step engine bisection from every preview while
    # preserving a fresh search when verified market evidence raises the floor.
    if (
        core_min_summary.get("public_npv") not in (None, "")
        and D(core_min_summary.get("public_npv")) + public_floor_tolerance >= required_public_npv
    ):
        minimum, minimum_summary = floor, core_min_summary
    else:
        minimum, minimum_summary = _find_public_value_floor(
            floor, technical, required_public_npv, evaluate_measure
        )
    if minimum is None:
        reason_en = "The public-value floor is above the maximum consideration that the project can sustain."
        reason_ar = "الحد الأدنى للقيمة العامة أعلى من أقصى مقابل يمكن للمشروع تحمله."
        base.update(
            {
                "status": "PUBLIC_VALUE_FLOOR_EXCEEDS_CEILING",
                "offer_position": "NO_FEASIBLE_RANGE",
                "minimum": None,
                "balanced": None,
                "risk_adjusted_ceiling": None,
                "technical_ceiling": _boundary("technical_ceiling", technical, method, core_technical_summary, reason_en=reason_en, reason_ar=reason_ar),
                "offer": _boundary("offer", offered, method, evaluate_measure(offered), reason_en=reason_en, reason_ar=reason_ar),
                "range": {"low": None, "high": None},
                "summary_en": reason_en,
                "summary_ar": reason_ar,
                "why_this_range": [
                    {
                        "code": "PUBLIC_VALUE_FLOOR_EXCEEDS_CEILING",
                        "title_en": "Public-value floor exceeds capacity",
                        "title_ar": "الحد الأدنى للقيمة العامة يتجاوز قدرة المشروع",
                        "detail_en": reason_en,
                        "detail_ar": reason_ar,
                        "required_public_npv": fmt(required_public_npv),
                        "technical_public_npv": core_technical_summary.get("public_npv"),
                    }
                ],
                "governing_constraint": _constraint_details(comparison),
            }
        )
        return base

    minimum_summary = minimum_summary or evaluate_measure(minimum)
    adjustment_policy = risk_adjustment_policy or {}
    valuation_rules = valuation_policy or {}
    minimum_capacity_factor = clamp(D(adjustment_policy.get("minimum_capacity_factor"), "0.30"))
    institutional_conservatism = clamp(D(adjustment_policy.get("institutional_conservatism"), "0.45"), ZERO, Decimal("0.95"))
    capacity_from_conservatism = max(minimum_capacity_factor, ONE - institutional_conservatism)
    explicit_capacity = adjustment_policy.get("risk_adjusted_capacity_factor")
    configured_capacity_factor = capacity_from_conservatism
    if explicit_capacity not in (None, ""):
        configured_capacity_factor = min(
            capacity_from_conservatism,
            clamp(D(explicit_capacity), minimum_capacity_factor, ONE),
        )
    developer_safety_buffer = clamp(D(adjustment_policy.get("developer_safety_buffer"), "0"), ZERO, Decimal("0.50"))
    capacity_factor = max(minimum_capacity_factor, configured_capacity_factor - developer_safety_buffer)
    risk_adjusted = minimum + (technical - minimum) * capacity_factor
    risk_adjusted = max(minimum, min(technical, risk_adjusted))

    configured_balanced_position = clamp(D(adjustment_policy.get("balanced_position_factor"), "0.59"))
    recommendation_method = str(valuation_rules.get("recommendation_method") or "POLICY_RANGE_POSITION").upper()
    if recommendation_method == "CORE_TARGET_RETURN" and minimum <= core_balanced <= risk_adjusted:
        balanced = core_balanced
        balanced_summary = _summary_from_case(comparison.get("balanced_case"))
        balanced_selection_method = "CORE_SOLVER_TARGET_RETURN"
        decision_position = None
        raw_position = None
    else:
        decision_position = configured_balanced_position
        raw_position = configured_balanced_position
        balanced = minimum + (risk_adjusted - minimum) * decision_position
        balanced_summary = evaluate_measure(balanced)
        balanced_selection_method = "EXPLICIT_POLICY_POSITION"

    rounding_increment = ZERO
    if method not in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        rounding_increment = max(ZERO, D(valuation_rules.get("rounding_increment_percent"), "0"))
    if rounding_increment > ZERO:
        minimum = clamp(_round_to_increment(minimum, rounding_increment, "UP"))
        technical = clamp(_round_to_increment(technical, rounding_increment, "DOWN"))
        if technical < minimum:
            technical = minimum
        risk_adjusted = clamp(_round_to_increment(risk_adjusted, rounding_increment, "DOWN"), minimum, technical)
        balanced = clamp(_round_to_increment(balanced, rounding_increment, "NEAREST"), minimum, risk_adjusted)
        minimum_summary = evaluate_measure(minimum)
        balanced_summary = evaluate_measure(balanced)
        core_technical_summary = evaluate_measure(technical)
    elif balanced_summary.get("public_npv") in (None, ""):
        balanced_summary = evaluate_measure(balanced)
    risk_summary = evaluate_measure(risk_adjusted)
    offer_summary = evaluate_measure(offered)
    if core_technical_summary.get("public_npv") in (None, ""):
        core_technical_summary = evaluate_measure(technical)
    calculated_summaries = [minimum_summary, balanced_summary, risk_summary, core_technical_summary, offer_summary]
    if any(not row.get("calculation_valid", True) for row in calculated_summaries):
        reason_en = "A numerical or cash-reconciliation failure occurred while validating the range; no recommendation was issued."
        reason_ar = "حدث فشل عددي أو فشل في مصالحة النقد أثناء التحقق من النطاق؛ لذلك لم تصدر توصية."
        base.update({
            "status": "NUMERICALLY_UNRESOLVED",
            "offer_position": "NUMERICALLY_UNRESOLVED",
            "minimum": None,
            "balanced": None,
            "risk_adjusted_ceiling": None,
            "technical_ceiling": None,
            "offer": _boundary("offer", offered, method, offer_summary, reason_en=reason_en, reason_ar=reason_ar),
            "range": {"low": None, "high": None},
            "summary_en": reason_en,
            "summary_ar": reason_ar,
            "why_this_range": [{"code": "NUMERICALLY_UNRESOLVED", "title_en": "Numerical calculation unresolved", "title_ar": "الحساب العددي غير محسوم", "detail_en": reason_en, "detail_ar": reason_ar}],
            "governing_constraint": _constraint_details(comparison),
        })
        return base

    governing = _constraint_details(comparison)
    governing_en = governing.get("label_en") if governing else "the first binding feasibility constraint"
    governing_ar = governing.get("label_ar") if governing else "أول قيد جدوى ملزم"
    minimum_term = _term(minimum, method)
    balanced_term = _term(balanced, method)
    risk_term = _term(risk_adjusted, method)
    technical_term = _term(technical, method)
    offer_term = _term(offered, method)
    minimum_public_npv = _money(minimum_summary.get("public_npv"), currency)
    required_public_npv_display = _money(required_public_npv, currency)
    balanced_public_npv = _money(balanced_summary.get("public_npv"), currency)
    balanced_irr = _ratio_percent(balanced_summary.get("developer_irr"))
    target_irr_display = _ratio_percent(target_developer_irr)
    balanced_gap = _money(balanced_summary.get("funding_gap"), currency)
    capacity_display = _ratio_percent(capacity_factor)
    configured_capacity_display = _ratio_percent(configured_capacity_factor)
    conservatism_display = _ratio_percent(institutional_conservatism)
    safety_buffer_display = _ratio_percent(developer_safety_buffer)
    configured_balanced_display = _ratio_percent(configured_balanced_position)
    policy_floor_applied = (
        policy_minimum_measure is not None
        and abs(minimum - policy_minimum_measure) <= Decimal("0.0000001")
    )
    minimum_origin_en = (
        f"It is also the configured policy floor of {_term(policy_minimum_measure, method)}. "
        if policy_floor_applied and policy_minimum_measure is not None
        else ""
    )
    minimum_origin_ar = (
        f"وهو أيضاً الحد الأدنى المضبوط في السياسة والبالغ {_term(policy_minimum_measure, method)}. "
        if policy_floor_applied and policy_minimum_measure is not None
        else ""
    )
    governing_test_en = ""
    governing_test_ar = ""
    if governing:
        actual_display = _compact_value(governing.get("actual"))
        threshold_display = _compact_value(governing.get("threshold"))
        governing_test_en = (
            f" At the boundary, {governing_en} records actual "
            f"{actual_display} {governing.get('operator') or ''} threshold {threshold_display}."
        )
        governing_test_ar = (
            f" وعند الحد يسجل قيد {governing_ar} قيمة فعلية "
            f"{actual_display} {governing.get('operator') or ''} مقابل حد {threshold_display}."
        )
    minimum_reason_en = (
        f"{minimum_term} is the lowest tested {meta['measure_label_en'].lower()} that passes both tests: "
        f"calculated public NPV is {minimum_public_npv}, at least the required {required_public_npv_display}, "
        f"and no mandatory feasibility constraint fails. {minimum_origin_en}"
        "A lower value is not reported as defensible because it would fall below the policy/search floor "
        "or the required public-value test."
    )
    minimum_reason_ar = (
        f"نسبة {minimum_term} هي أدنى {meta['measure_label_ar']} اختبرها المحرك ونجحت في الشرطين معاً: "
        f"صافي القيمة الحالية لمقابل مالك الأرض يساوي {minimum_public_npv} وهو لا يقل عن المطلوب "
        f"{required_public_npv_display}، ولا يفشل أي قيد جدوى إلزامي. {minimum_origin_ar}"
        "ولا تُعرض قيمة أدنى كحد قابل للدفاع لأنها ستقع دون حد السياسة/البحث أو دون اختبار قيمة مالك الأرض المطلوب."
    )
    if balanced_selection_method == "CORE_SOLVER_TARGET_RETURN":
        balanced_reason_en = (
            f"{balanced_term} is the core solver's balanced point and lies inside the permitted interval "
            f"{minimum_term}–{risk_term}. The solver uses the target developer IRR of {target_irr_display} as its "
            f"return reference; the mandatory direct re-test at this exact term reports developer IRR "
            f"{balanced_irr}, funding gap {balanced_gap}, and public NPV {balanced_public_npv}. The displayed actual "
            "therefore remains auditable even when a binding constraint or non-linear cash timing prevents an exact "
            "match to the target."
        )
        balanced_reason_ar = (
            f"نسبة {balanced_term} هي نقطة التوازن الصادرة من المحرك الأساسي وتقع داخل المجال المسموح "
            f"{minimum_term}–{risk_term}. يستخدم المحرك عائد المطور المستهدف {target_irr_display} كمرجع للعائد؛ "
            f"أما إعادة الاختبار الإلزامية عند هذه النسبة نفسها فتُظهر عائداً فعلياً {balanced_irr}، وفجوة تمويل "
            f"{balanced_gap}، وصافي قيمة حالية لمقابل مالك الأرض {balanced_public_npv}. لذلك تبقى القيمة الفعلية ظاهرة وقابلة "
            "للتدقيق حتى إذا منع قيد حاكم أو توقيت نقدي غير خطي التطابق التام مع الهدف."
        )
    else:
        decision_position_display = _ratio_percent(decision_position)
        balanced_reason_en = (
            f"The core target-return point fell outside the permitted policy-adjusted interval, so {balanced_term} is "
            f"calculated transparently as {minimum_term} + ({risk_term} − {minimum_term}) × "
            f"{decision_position_display}. The {decision_position_display} position is the explicit balanced-position "
            f"factor stored in the selected policy. At the selected point developer IRR is {balanced_irr}, funding "
            f"gap is {balanced_gap}, and public NPV is {balanced_public_npv}."
        )
        balanced_reason_ar = (
            f"وقعت نقطة عائد المطور الأساسية خارج المجال المتحفظ وفق السياسة، لذلك حُسبت {balanced_term} بشفافية وفق "
            f"المعادلة {minimum_term} + ({risk_term} − {minimum_term}) × {decision_position_display}. وتمثل نسبة "
            f"{decision_position_display} موضع التوصية المتوازنة المحدد صراحة في السياسة المختارة. وعند النقطة "
            f"المختارة يبلغ عائد المطور {balanced_irr}، وفجوة التمويل {balanced_gap}، وصافي القيمة الحالية لمقابل "
            f"مالك الأرض {balanced_public_npv}."
        )
    risk_reason_en = (
        f"The policy-adjusted ceiling is calculated transparently: {risk_term} = {minimum_term} + "
        f"({technical_term} − {minimum_term}) × {capacity_display}. The policy explicitly sets the retained-capacity "
        f"factor at {configured_capacity_display}; an additional developer safety buffer of {safety_buffer_display} "
        f"is deducted, producing the applied factor {capacity_display}."
    )
    risk_reason_ar = (
        f"السقف المتحفظ وفق السياسة محسوب بشفافية: {risk_term} = {minimum_term} + "
        f"({technical_term} − {minimum_term}) × {capacity_display}. وتحدد السياسة صراحة معامل الاحتفاظ بالقدرة "
        f"عند {configured_capacity_display}، ثم يُخصم هامش أمان للمطور قدره {safety_buffer_display}، لينتج معامل "
        f"التطبيق الفعلي {capacity_display}."
    )
    technical_reason_en = (
        f"{technical_term} is the highest feasible value found by the engine before {governing_en} becomes the "
        f"first failing constraint. It is an economic capacity limit, not a recommended negotiating target."
        f"{governing_test_en}"
    )
    technical_reason_ar = (
        f"{technical_term} هو أعلى مستوى قابل للتنفيذ وجده المحرك قبل أن يصبح قيد {governing_ar} أول قيد فاشل. "
        f"وهذا سقف للقدرة الاقتصادية وليس هدفاً تفاوضياً موصى به.{governing_test_ar}"
    )

    position = _offer_position(offered, minimum, risk_adjusted, technical, valid=True)
    offer_messages = {
        "BELOW_MINIMUM": (
            "The investor offer is below the minimum defensible term.",
            "عرض المستثمر أدنى من الحد الأدنى القابل للدفاع.",
        ),
        "WITHIN_RECOMMENDED_RANGE": (
            "The investor offer lies inside the defensible and policy-adjusted negotiation range.",
            "عرض المستثمر يقع ضمن نطاق التفاوض القابل للدفاع والمتحفظ وفق السياسة.",
        ),
        "ABOVE_RISK_ADJUSTED_CEILING": (
            f"The offer of {offer_term} is above the policy-adjusted ceiling of {risk_term}; this is more favorable "
            f"to the landowner. Because it remains below the technical ceiling of "
            f"{technical_term}, it is not rejected merely for being higher; developer feasibility and contractual "
            "protections should still be verified.",
            f"العرض البالغ {offer_term} أعلى من السقف المتحفظ وفق السياسة {risk_term}، وهذا من صالح أصحاب الأرض. "
            f"وبما أنه ما زال دون السقف الفني {technical_term} فلا يُرفض لمجرد أن نسبته أعلى؛ "
            "مع ضرورة التحقق من جدوى المطور والضمانات التعاقدية.",
        ),
        "ABOVE_TECHNICAL_CEILING": (
            f"The offer of {offer_term} is higher and therefore nominally more favorable to the "
            f"landowner, but it exceeds the technical ceiling of {technical_term}. Above that point "
            f"{governing_en} fails, so the offered term is not economically sustainable without changing project "
            "assumptions, financing or risk allocation.",
            f"العرض البالغ {offer_term} أعلى، وهو اسمياً من صالح أصحاب الأرض، لكنه يتجاوز السقف "
            f"الفني {technical_term}. بعد هذا الحد يفشل قيد {governing_ar}، لذلك لا تكون النسبة قابلة للاستدامة "
            "اقتصادياً ما لم تتغير افتراضات المشروع أو التمويل أو توزيع المخاطر.",
        ),
    }
    offer_reason_en, offer_reason_ar = offer_messages[position]

    minimum_boundary = _boundary("minimum", minimum, method, minimum_summary, reason_en=minimum_reason_en, reason_ar=minimum_reason_ar)
    balanced_boundary = _boundary("balanced", balanced, method, balanced_summary, reason_en=balanced_reason_en, reason_ar=balanced_reason_ar)
    risk_boundary = _boundary("risk_adjusted_ceiling", risk_adjusted, method, risk_summary, reason_en=risk_reason_en, reason_ar=risk_reason_ar)
    technical_boundary = _boundary("technical_ceiling", technical, method, core_technical_summary, reason_en=technical_reason_en, reason_ar=technical_reason_ar)
    offer_boundary = _boundary("offer", offered, method, offer_summary, reason_en=offer_reason_en, reason_ar=offer_reason_ar)

    summary_en = (
        f"The defensible range for {meta['label_en'].lower()} runs from the public-value floor to the policy-adjusted ceiling. "
        "The balanced point protects public value while retaining financeability and developer return headroom."
    )
    summary_ar = (
        f"يمتد النطاق القابل للدفاع لنموذج {meta['label_ar']} من الحد الأدنى لقيمة مالك الأرض حتى السقف المتحفظ وفق السياسة. "
        "وتحمي النقطة المتوازنة قيمة مالك الأرض مع الإبقاء على قابلية التمويل وهامش عائد مناسب للمطور."
    )
    base.update(
        {
            "status": "VALID_RANGE" if status == "VALID_RANGE" else status,
            "offer_position": position,
            "minimum": minimum_boundary,
            "balanced": balanced_boundary,
            "risk_adjusted_ceiling": risk_boundary,
            "technical_ceiling": technical_boundary,
            "offer": offer_boundary,
            "range": {
                "low": minimum_boundary["value"],
                "high": risk_boundary["value"],
                "technical_high": technical_boundary["value"],
            },
            "summary_en": summary_en,
            "summary_ar": summary_ar,
            "why_this_range": [
                {
                    "code": "PUBLIC_VALUE_FLOOR",
                    "title_en": "Why the minimum is set here",
                    "title_ar": "لماذا يبدأ الحد الأدنى من هنا",
                    "detail_en": minimum_reason_en,
                    "detail_ar": minimum_reason_ar,
                    "required_public_npv": fmt(required_public_npv),
                    "actual_public_npv": minimum_boundary.get("public_npv"),
                },
                {
                    "code": "BALANCED_POINT",
                    "title_en": "Why this is the balanced recommendation",
                    "title_ar": "لماذا هذه هي التوصية المتوازنة",
                    "detail_en": balanced_reason_en,
                    "detail_ar": balanced_reason_ar,
                    "target_developer_irr": fmt(target_developer_irr),
                    "actual_developer_irr": balanced_boundary.get("developer_irr"),
                    "funding_gap": balanced_boundary.get("funding_gap"),
                    "selection_method": balanced_selection_method,
                    "raw_position": fmt(raw_position) if raw_position is not None else None,
                    "decision_position": fmt(decision_position) if decision_position is not None else None,
                },
                {
                    "code": "RISK_ADJUSTMENT",
                    "title_en": "Why the policy-adjusted ceiling is lower than the technical ceiling",
                    "title_ar": "لماذا السقف المتحفظ وفق السياسة أقل من السقف الفني",
                    "detail_en": risk_reason_en,
                    "detail_ar": risk_reason_ar,
                    "institutional_conservatism": fmt(institutional_conservatism),
                    "capacity_from_conservatism": fmt(capacity_from_conservatism),
                    "configured_capacity_factor": fmt(configured_capacity_factor),
                    "developer_safety_buffer": fmt(developer_safety_buffer),
                    "capacity_factor": fmt(capacity_factor),
                    "balanced_position_factor": fmt(configured_balanced_position),
                    "rounding_increment": fmt(rounding_increment),
                    "recommendation_method": recommendation_method,
                    "policy_source": "fair_consideration_policy",
                },
                {
                    "code": "TECHNICAL_CEILING",
                    "title_en": "What limits the technical ceiling",
                    "title_ar": "ما الذي يحدد السقف الفني",
                    "detail_en": technical_reason_en,
                    "detail_ar": technical_reason_ar,
                    "governing_constraint": governing,
                },
                {
                    "code": "OFFER_POSITION",
                    "title_en": "How the investor offer compares",
                    "title_ar": "كيف يقارن عرض المستثمر بالنطاق",
                    "detail_en": offer_reason_en,
                    "detail_ar": offer_reason_ar,
                    "position": position,
                },
            ],
            "governing_constraint": governing,
            "risk_adjustment": {
                "institutional_conservatism": fmt(institutional_conservatism),
                "capacity_from_conservatism": fmt(capacity_from_conservatism),
                "configured_capacity_factor": fmt(configured_capacity_factor),
                "developer_safety_buffer": fmt(developer_safety_buffer),
                "applied_capacity_factor": fmt(capacity_factor),
                "balanced_position_factor": fmt(configured_balanced_position),
                "rounding_increment": fmt(rounding_increment),
                "recommendation_method": recommendation_method,
                "classification": "EXPLICIT_POLICY_ADJUSTMENT_NOT_PROBABILISTIC_RISK",
                "policy_parameters": {key: str(value) for key, value in sorted(adjustment_policy.items())},
            },
        }
    )
    return base


def _summary_from_unified_result(result: dict[str, Any]) -> dict[str, Any]:
    truth = result.get("financial_truth") or {}
    invariants = result.get("engine_invariants") or {}
    constraints = truth.get("constraints") or []
    failed = list(truth.get("failed_constraints") or [])
    failed.extend(invariants.get("failed_invariant_ids") or [])
    return {
        "public_npv": truth.get("government_npv"),
        "public_nominal": truth.get("government_consideration"),
        "government_npv": truth.get("government_npv"),
        "government_value": truth.get("government_consideration"),
        "developer_irr": _first_present(truth.get("developer_equity_irr"), truth.get("developer_irr")),
        "developer_equity_irr": _first_present(truth.get("developer_equity_irr"), truth.get("developer_irr")),
        "developer_moic": _first_present(truth.get("developer_equity_multiple"), truth.get("developer_multiple")),
        "developer_multiple": _first_present(truth.get("developer_equity_multiple"), truth.get("developer_multiple")),
        "developer_npv": _first_present(truth.get("developer_equity_npv"), truth.get("developer_npv")),
        "developer_nominal_distributions": _first_present(
            truth.get("developer_equity_distributions"),
            truth.get("total_developer_distributions"),
        ),
        "developer_nominal_net_profit_after_equity": _first_present(
            truth.get("developer_equity_nominal_profit"),
            truth.get("developer_profit"),
        ),
        "developer_equity_contributed": truth.get("developer_equity_contributed"),
        "developer_net_margin": truth.get("developer_net_margin"),
        "profit_on_cost": truth.get("developer_profit_on_cost"),
        "peak_equity": truth.get("peak_equity"),
        "peak_debt": truth.get("peak_debt"),
        "funding_gap": _first_present(truth.get("funding_gap"), truth.get("peak_funding_gap")),
        "peak_funding_gap": _first_present(truth.get("funding_gap"), truth.get("peak_funding_gap")),
        "terminal_debt": truth.get("terminal_debt"),
        "evaluation_status": truth.get("evaluation_status"),
        "calculation_valid": bool(_first_present(truth.get("calculation_valid"), default=True)),
        "cash_reconciliation_passed": bool(_first_present(truth.get("cash_reconciliation_passed"), default=True)),
        "feasible": bool(truth.get("feasible")) and bool(invariants.get("passed")),
        "constraints": constraints,
        "failed_constraints": list(dict.fromkeys(str(item) for item in failed if item)),
    }


def _apply_measure_to_project(
    project: dict[str, Any],
    *,
    method: str,
    measure: Decimal,
    fixed_component: Decimal,
) -> dict[str, Any]:
    candidate = deepcopy(project)
    partnership = candidate.setdefault("partnership", {})
    studio = candidate.setdefault("landowner_studio", {})
    partnership["method"] = method
    partnership["approved_selection"] = "MANUAL"
    methods = [str(item).upper() for item in studio.get("contract_methods") or []]
    if method not in methods:
        methods.append(method)
    studio["contract_methods"] = methods
    base_date = str(candidate.get("valuation_date") or candidate.get("base_date") or "2026-01-01")[:10]
    if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        amount = fmt(max(ZERO, measure)) or "0"
        partnership["manual_amount"] = amount
        if method == "UPFRONT":
            partnership["upfront_payments"] = [{"date": base_date, "amount": amount}]
            studio["upfront_amount"] = amount
        else:
            partnership["upfront_payments"] = []
            studio["minimum_guarantee_amount"] = amount
    else:
        rate = fmt(clamp(measure)) or "0"
        partnership["manual_share"] = rate
        partnership["share_rate"] = rate
        if method == "HYBRID":
            amount = fmt(max(ZERO, fixed_component)) or "0"
            partnership["upfront_payments"] = ([{"date": base_date, "amount": amount}] if fixed_component > ZERO else [])
            studio["hybrid_upfront_amount"] = amount
        else:
            partnership["upfront_payments"] = []
    return candidate


def _display_boundary(
    boundary: dict[str, Any] | None,
    *,
    method: str,
    fixed_component: Decimal,
    currency: str,
) -> dict[str, Any] | None:
    if not boundary:
        return None
    result = dict(boundary)
    value = D(boundary.get("value"))
    if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        result["display_en"] = f"{currency} {value:,.2f}"
        result["display_ar"] = f"{value:,.2f} {currency}"
    elif method == "HYBRID":
        result["display_en"] = f"{currency} {fixed_component:,.2f} + {value:.2f}%"
        result["display_ar"] = f"{fixed_component:,.2f} {currency} + {value:.2f}%"
    else:
        result["display_en"] = f"{value:.2f}%"
        result["display_ar"] = f"{value:.2f}%"
    return result


def _grid_engine_measures(
    negotiation: dict[str, Any],
    *,
    method: str,
    offered: Decimal,
) -> list[Decimal]:
    boundaries = [
        D((negotiation.get(name) or {}).get("engine_measure"))
        for name in ("minimum", "balanced", "risk_adjusted_ceiling", "technical_ceiling")
        if negotiation.get(name)
    ]
    if not boundaries:
        # Even when the solver cannot establish a defensible recommendation
        # range, decision-makers still need a diagnostic test table.  Generate
        # a deterministic local grid around the entered offer instead of
        # collapsing the analysis to one point.  Rows remain diagnostics only;
        # they do not fabricate minimum, balanced or ceiling boundaries.
        if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
            if offered <= ZERO:
                return [ZERO]
            low = ZERO
            high = offered * Decimal("1.50")
        else:
            half_width = max(Decimal("0.15"), abs(offered) * Decimal("0.50"))
            low = max(ZERO, offered - half_width)
            high = min(ONE, offered + half_width)
            if high <= low:
                high = min(ONE, low + Decimal("0.25"))
        steps = 8
        points = {low + (high - low) * Decimal(index) / Decimal(steps) for index in range(steps + 1)}
        points.add(max(ZERO, offered) if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else clamp(offered))
        return sorted(points)
    low = min(boundaries)
    high = max(boundaries)
    if method not in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        low = max(ZERO, low)
        high = min(ONE, max(low, high))
    if high <= low:
        points = {low, offered}
    else:
        steps = 8
        points = {low + (high - low) * Decimal(index) / Decimal(steps) for index in range(steps + 1)}
        points.add(offered)
        points.update(boundaries)
    return sorted(points)


def build_contract_negotiation(
    project: dict[str, Any],
    policy: dict[str, Any],
    *,
    contract: dict[str, Any],
    case_input: dict[str, Any] | None = None,
    monetary_levels: dict[str, Any] | None = None,
    currency: str = "USD",
    initial_unified: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an engine-tested negotiation range in the selected contract's native unit.

    Percentage contracts are returned as percentages, outright sale as a
    currency amount, and hybrid contracts as a fixed upfront amount plus a
    variable percentage.  Each boundary is re-evaluated through the unified
    monthly engine and carries its public NPV, developer return and funding
    evidence.
    """

    from landvalue360_server.unified_engine import run_unified_financial_engine

    case_input = case_input or {}
    monetary_levels = monetary_levels or {}
    method = method_from_contract(contract)
    offered, fixed_component = offered_engine_measure(contract)
    full = initial_unified or run_unified_financial_engine(
        _apply_measure_to_project(
            project, method=method, measure=offered, fixed_component=fixed_component
        ),
        deepcopy(policy),
        selected_only=False,
    )
    comparison = next(
        (row for row in full.get("contract_comparison") or [] if str(row.get("method") or "").upper() == method),
        None,
    )
    cache: dict[str, dict[str, Any]] = {}

    def evaluate_measure(measure: Decimal) -> dict[str, Any]:
        normalized = max(ZERO, measure) if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else clamp(measure)
        key = format(normalized, "f")
        if key not in cache:
            candidate = _apply_measure_to_project(
                project,
                method=method,
                measure=normalized,
                fixed_component=fixed_component,
            )
            result = run_unified_financial_engine(candidate, deepcopy(policy), selected_only=True)
            cache[key] = _summary_from_unified_result(result)
        return cache[key]

    financial = policy.get("financial_constraints") or {}
    valuation_basis = case_input.get("valuation_basis") or {}
    market_low = D(
        case_input.get("market_low"),
        str(monetary_levels.get("minimum_defensible_consideration") or project.get("land_value_baseline") or "0"),
    )
    negotiation = build_native_negotiation(
        comparison=comparison,
        contract=contract,
        market_low=market_low,
        confidence_grade=str(case_input.get("confidence_grade") or "") or None,
        data_confidence=clamp(D(valuation_basis.get("data_confidence"), "0.60")),
        risk_score=max(ZERO, D(case_input.get("risk_score"), "0")),
        downside_survival=clamp(D(case_input.get("downside_survival"), "0.70")),
        enforceability=clamp(D(case_input.get("contract_enforceability_score"), "0.65")),
        target_developer_irr=D(financial.get("target_developer_irr"), "0.22"),
        evaluate_measure=evaluate_measure,
        risk_adjustment_policy=policy.get("fair_consideration_policy") or {},
        valuation_policy=policy.get("valuation_policy") or {},
        currency=currency,
        policy_minimum_measure=(
            None
            if method in {"UPFRONT", "MINIMUM_GUARANTEE"}
            else D((policy.get("share_policy") or {}).get("policy_minimum_share"), "0")
        ),
    )
    return decorate_native_negotiation(
        negotiation,
        contract=contract,
        currency=currency,
        evaluate_measure=evaluate_measure,
    )


def decorate_native_negotiation(
    negotiation: dict[str, Any],
    *,
    contract: dict[str, Any],
    currency: str,
    evaluate_measure: Callable[[Decimal], dict[str, Any]],
) -> dict[str, Any]:
    """Add display text and an auditable native-measure test table."""

    result = dict(negotiation)
    method = method_from_contract(contract)
    offered, fixed_component = offered_engine_measure(contract)
    result["currency"] = str(currency or "USD").upper()
    result["fixed_component"] = fmt(fixed_component) if fixed_component > ZERO else None
    for name in ("minimum", "balanced", "risk_adjusted_ceiling", "technical_ceiling", "offer"):
        result[name] = _display_boundary(
            result.get(name),
            method=method,
            fixed_component=fixed_component,
            currency=result["currency"],
        )
    result["range_supported"] = result.get("status") in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"}
    result["levels"] = {
        name: result.get(name)
        for name in ("minimum", "balanced", "risk_adjusted_ceiling", "technical_ceiling", "offer")
    }
    boundary_lookup: dict[str, list[str]] = {}
    quantizer = Decimal("0.01") if method in {"UPFRONT", "MINIMUM_GUARANTEE"} else Decimal("0.0000001")
    for boundary_name in ("minimum", "balanced", "risk_adjusted_ceiling", "technical_ceiling", "offer"):
        boundary = result.get(boundary_name) or {}
        raw_measure = boundary.get("engine_measure")
        if raw_measure in (None, ""):
            continue
        lookup_key = format(D(raw_measure).quantize(quantizer), "f")
        boundary_lookup.setdefault(lookup_key, []).append(boundary_name)

    rows: list[dict[str, Any]] = []
    for measure in _grid_engine_measures(result, method=method, offered=offered):
        summary = evaluate_measure(measure)
        displayed = display_measure(measure, method)
        lookup_key = format(measure.quantize(quantizer), "f")
        if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
            display_en = f"{result['currency']} {displayed:,.2f}"
            display_ar = f"{displayed:,.2f} {result['currency']}"
        elif method == "HYBRID":
            display_en = f"{result['currency']} {fixed_component:,.2f} + {displayed:.2f}%"
            display_ar = f"{fixed_component:,.2f} {result['currency']} + {displayed:.2f}%"
        else:
            display_en = display_ar = f"{displayed:.2f}%"
        rows.append(
            {
                "measure": fmt(displayed),
                "engine_measure": fmt(measure),
                "display_en": display_en,
                "display_ar": display_ar,
                "government_npv": summary.get("public_npv"),
                "government_value": summary.get("public_nominal"),
                "government_nominal": summary.get("public_nominal"),
                "developer_nominal_distributions": summary.get("developer_nominal_distributions"),
                "developer_nominal_net_profit_after_equity": summary.get("developer_nominal_net_profit_after_equity"),
                "developer_equity_contributed": summary.get("developer_equity_contributed"),
                "developer_net_margin": summary.get("developer_net_margin"),
                "developer_irr": summary.get("developer_irr"),
                "developer_moic": summary.get("developer_moic"),
                "developer_npv": summary.get("developer_npv"),
                "profit_on_cost": summary.get("profit_on_cost"),
                "peak_equity": summary.get("peak_equity"),
                "peak_debt": summary.get("peak_debt"),
                "funding_gap": summary.get("funding_gap"),
                "terminal_debt": summary.get("terminal_debt"),
                "feasible": bool(summary.get("feasible")),
                "failed_constraints": summary.get("failed_constraints") or [],
                "boundary_keys": boundary_lookup.get(lookup_key, []),
            }
        )
    feasible_rows = [row for row in rows if row.get("feasible")]
    result["participation_analysis"] = {
        "method": method,
        "contract_type": result.get("contract_type"),
        "contract_label_en": result.get("contract_label_en"),
        "contract_label_ar": result.get("contract_label_ar"),
        "measure_type": result.get("measure_type"),
        "measure_label_en": result.get("measure_label_en"),
        "measure_label_ar": result.get("measure_label_ar"),
        "unit": result.get("unit"),
        "currency": result.get("currency"),
        "fixed_upfront_amount": fmt(fixed_component) if fixed_component > ZERO else None,
        "offered_measure": fmt(display_measure(offered, method)),
        "rows": rows,
        "first_feasible_measure": feasible_rows[0]["measure"] if feasible_rows else None,
        "last_feasible_measure": feasible_rows[-1]["measure"] if feasible_rows else None,
    }
    return result
