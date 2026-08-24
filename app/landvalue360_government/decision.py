"""Government decision orchestration over the unified LandValue360 Engine."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from landvalue360_risk import apply_project_shocks, assess_risk_register
from landvalue360_server.constraint_registry import constraint_metadata
from landvalue360_server.unified_engine import run_unified_financial_engine
from landvalue360_server.valuation_policy import resolve_valuation_discount

from .contracts import evaluate_contract
from .hashing import sha256_json
from .manifest import platform_manifest
from .metrics import build_metric_snapshot
from .negotiation import (
    build_native_negotiation,
    decorate_native_negotiation,
    method_from_contract,
    offered_engine_measure,
)
from .registries import registry_snapshot
from .results import build_results_book
from .valuation import evaluate_valuation

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
    for value in values:
        if value not in (None, ""):
            return value
    return default


def _default_valuation_basis(project: dict[str, Any]) -> dict[str, Any]:
    valuation_date = str(project.get("valuation_date") or date.today().isoformat())[:10]
    context = project.get("valuation_context") or {}
    return {
        "valuation_date": valuation_date,
        "base_date": valuation_date,
        "basis_of_value": context.get("basis_of_value") or "MARKET_VALUE",
        "currency": project.get("reporting_currency") or "USD",
        "nominal_or_real": "NOMINAL",
        "tax_basis": "EXCLUSIVE_OF_TRANSACTION_TAXES",
        "title_and_ownership_assumptions": "Clear title subject to legal verification.",
        "encumbrances": "Not confirmed; legal due diligence required.",
        "planning_and_zoning_status": "Current project planning inputs; subject to authority confirmation.",
        "development_rights": "Only rights evidenced in the project version are included.",
        "permitted_density": str((project.get("planning") or {}).get("far") or "0"),
        "infrastructure_obligations": "Project obligations as entered in the cost and scope registers.",
        "existing_use": "Existing use not independently verified.",
        "alternative_use": "Proposed development scenario.",
        "highest_and_best_use": "Conditionally supported subject to legal, physical and financial feasibility.",
        "special_assumptions": "No unconfirmed planning approval is represented as current market fact.",
        "extraordinary_assumptions": "None unless explicitly entered in the case.",
        "market_evidence_date": valuation_date,
        "data_confidence": "0.60",
        "material_valuation_uncertainty": "Material uncertainty exists where market evidence is limited or dated.",
    }


def _default_valuation_methods(
    project: dict[str, Any],
    unified: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Return disclosed screening methods when no evidence package is supplied.

    A user baseline is not relabelled as an independent appraisal.  The residual
    calculation is explicitly model-derived capacity and is excluded from market
    reconciliation unless a reviewer supplies and verifies a full valuation pack.
    """

    baseline = max(ZERO, D(project.get("land_value_baseline")))
    truth = unified.get("financial_truth") or {}
    summary = unified.get("summary") or {}
    financial = policy.get("financial_constraints") or {}
    gdv = D(truth.get("gross_sales"))
    cost = D(truth.get("development_cost"))
    finance = D(_first_present(truth.get("interest_total"), summary.get("interest_total"))) + D(
        _first_present(truth.get("financing_fees_total"), summary.get("financing_fees_total"))
    )
    target_poc = max(ZERO, D(financial.get("minimum_profit_on_cost"), "0.20"))
    target_profit = max(ZERO, cost * target_poc)
    methods: dict[str, Any] = {
        "residual": {
            "gross_development_value": _fmt(gdv),
            "development_costs": _fmt(cost),
            "finance_costs": _fmt(finance),
            "taxes": "0",
            "contingency": "0",
            "target_profit_amount": _fmt(target_profit),
            "uncertainty": "0.25",
            "evidence_strength": "0.50",
            "classification": "RESIDUAL_SCREENING",
            "calculation_basis": "SCREENING_RESIDUAL_FROM_UNIFIED_MONTHLY_OUTPUT",
            "include_in_market_reconciliation": False,
        },
        "reconciliation_weights": {"RESIDUAL": "0.50"},
        "weight_reasons": {
            "RESIDUAL": "Model-derived capacity is disclosed for feasibility screening and is not independent market evidence."
        },
    }
    if baseline > ZERO:
        methods["scenario_valuation"] = {
            "value": _fmt(baseline),
            "low": _fmt(baseline * Decimal("0.85")),
            "high": _fmt(baseline * Decimal("1.15")),
            "evidence_strength": "0.35",
            "classification": str(
                (project.get("valuation_context") or {}).get("land_value_baseline_classification")
                or "TECHNICAL_SCREENING_BASELINE"
            ),
            "verified": False,
            "details": {
                "source_field": "land_value_baseline",
                "warning": "User or demo baseline; not an independent appraisal or verified market transaction.",
            },
        }
        methods["reconciliation_weights"]["SCENARIO_VALUATION"] = "0.35"
        methods["weight_reasons"]["SCENARIO_VALUATION"] = (
            "Retained as a disclosed screening benchmark only until verified market evidence is supplied."
        )
    return methods


def _contract_from_project(project: dict[str, Any]) -> dict[str, Any]:
    partnership = project.get("partnership") or {}
    studio = project.get("landowner_studio") or {}
    method = str(partnership.get("method") or "GROSS_SALES").upper()
    share = _first_present(partnership.get("manual_share"), partnership.get("share_rate"), default="0")
    hybrid_basis = str(
        studio.get("hybrid_variable_basis")
        or partnership.get("hybrid_variable_basis")
        or "GROSS_SALES"
    ).upper()
    hybrid_component_type = {
        "NET_SALES": "NET_SALES_SHARE",
        "PROFIT_SHARE": "PROFIT_SHARE",
        "GROSS_SALES": "GROSS_SALES_SHARE",
    }.get(hybrid_basis, "GROSS_SALES_SHARE")
    mapping = {
        "GROSS_SALES": {"type": "GROSS_SALES_SHARE", "rate": share, "basis": "COLLECTED"},
        "NET_SALES": {"type": "NET_SALES_SHARE", "rate": share, "basis": "COLLECTED"},
        "PROFIT_SHARE": {"type": "PROFIT_SHARE", "rate": share, "loss_carryforward": True},
        "UPFRONT": {"type": "OUTRIGHT_SALE", "upfront_amount": _first_present(studio.get("upfront_amount"), partnership.get("manual_amount"), default="0")},
        "HYBRID": {
            "type": "HYBRID",
            "components": [
                {"component_id": "upfront", "type": "OUTRIGHT_SALE", "upfront_amount": _first_present(studio.get("hybrid_upfront_amount"), default="0")},
                {"component_id": "variable", "type": hybrid_component_type, "rate": share},
            ],
        },
        "MINIMUM_GUARANTEE": {
            "type": "MINIMUM_GUARANTEE",
            "guarantee_amount": _first_present(studio.get("minimum_guarantee_amount"), partnership.get("manual_amount"), default="0"),
            "guarantee_date": str(project.get("valuation_date")),
            "underlying": {
                "type": {
                    "NET_SALES": "NET_SALES_SHARE",
                    "PROFIT_SHARE": "PROFIT_SHARE",
                    "GROSS_SALES": "GROSS_SALES_SHARE",
                }.get(str(studio.get("minimum_guarantee_underlying_method") or "GROSS_SALES").upper(), "GROSS_SALES_SHARE"),
                "rate": _first_present(studio.get("minimum_guarantee_underlying_share"), default="0"),
                "basis": "COLLECTED",
                "loss_carryforward": True,
            },
        },
    }
    return mapping.get(method, mapping["GROSS_SALES"])



def _hybrid_variable_basis(contract: dict[str, Any]) -> str:
    for component in contract.get("components") or []:
        kind = str(component.get("type") or "").upper()
        if kind == "NET_SALES_SHARE":
            return "NET_SALES"
        if kind == "PROFIT_SHARE":
            return "PROFIT_SHARE"
        if kind == "GROSS_SALES_SHARE":
            return "GROSS_SALES"
    return "GROSS_SALES"


def _project_for_contract(project: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Materialize the assessed contract into the project snapshot."""

    snapshot = deepcopy(project)
    method = method_from_contract(contract)
    measure, fixed = offered_engine_measure(contract)
    partnership = snapshot.setdefault("partnership", {})
    studio = snapshot.setdefault("landowner_studio", {})
    partnership["method"] = method
    partnership["approved_selection"] = "MANUAL"
    # Government assessment is contract-specific. Solving all five methods on
    # every preview multiplied run time and created room for stale cross-method
    # results. The option-comparison workflow invokes each contract explicitly.
    studio["contract_methods"] = [method]
    if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        partnership["manual_amount"] = _fmt(measure)
        if method == "UPFRONT":
            studio["upfront_amount"] = _fmt(measure)
        else:
            studio["minimum_guarantee_amount"] = _fmt(measure)
    else:
        partnership["manual_share"] = _fmt(measure)
        partnership["share_rate"] = _fmt(measure)
        if method == "HYBRID":
            studio["hybrid_upfront_amount"] = _fmt(fixed)
            studio["hybrid_variable_basis"] = _hybrid_variable_basis(contract)
    return snapshot


def prepare_government_project(
    project: dict[str, Any],
    case_input: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the case-specific engine snapshot and explicit contract.

    Government previews, approvals and calculation-run provenance must all use
    the same materialized contract.  Keeping this adapter public avoids each
    service reimplementing the mapping from a Government case to the canonical
    partnership fields consumed by the monthly engine.
    """

    payload = deepcopy(case_input or {})
    contract = deepcopy(payload.get("contract") or _contract_from_project(project))
    return _project_for_contract(project, contract), contract


def _project_at_measure(project: dict[str, Any], contract: dict[str, Any], measure: Decimal) -> dict[str, Any]:
    candidate_contract = deepcopy(contract)
    method = method_from_contract(candidate_contract)
    if method in {"UPFRONT", "MINIMUM_GUARANTEE"}:
        candidate_contract["upfront_amount" if method == "UPFRONT" else "guarantee_amount"] = _fmt(measure)
    elif method == "HYBRID":
        variable_set = False
        for component in candidate_contract.get("components") or []:
            if str(component.get("type") or "").upper() in {"GROSS_SALES_SHARE", "NET_SALES_SHARE", "PROFIT_SHARE"}:
                component["rate"] = _fmt(measure)
                variable_set = True
                break
        if not variable_set:
            candidate_contract.setdefault("components", []).append(
                {"component_id": "variable", "type": "GROSS_SALES_SHARE", "rate": _fmt(measure)}
            )
    else:
        candidate_contract["rate"] = _fmt(measure)
    return _project_for_contract(project, candidate_contract)


def _comparison(unified: dict[str, Any], method: str) -> dict[str, Any] | None:
    return next(
        (row for row in unified.get("contract_comparison") or [] if str(row.get("method") or "").upper() == method),
        None,
    )


def _selected_summary(result: dict[str, Any], method: str) -> dict[str, Any]:
    truth = result.get("financial_truth") or {}
    comparison = _comparison(result, method) or {}
    invariants = result.get("engine_invariants") or {}
    constraints = comparison.get("constraints") or truth.get("constraints") or []
    failed_constraints = [row.get("constraint_id") for row in constraints if row.get("passed") is False]
    failed_constraints.extend(invariants.get("failed_invariant_ids") or [])
    return {
        "public_npv": _first_present(truth.get("government_npv"), comparison.get("government_npv")),
        "public_nominal": _first_present(truth.get("government_consideration"), comparison.get("government_value")),
        "developer_irr": _first_present(truth.get("developer_equity_irr"), truth.get("developer_irr"), comparison.get("developer_equity_irr"), comparison.get("developer_irr")),
        "developer_moic": _first_present(truth.get("developer_equity_multiple"), truth.get("developer_multiple"), comparison.get("developer_multiple")),
        "developer_npv": _first_present(truth.get("developer_equity_npv"), truth.get("developer_npv"), comparison.get("developer_npv")),
        "developer_nominal_distributions": _first_present(
            truth.get("developer_equity_distributions"),
            truth.get("total_developer_distributions"),
            comparison.get("developer_equity_distributions"),
        ),
        "developer_nominal_net_profit_after_equity": _first_present(
            truth.get("developer_equity_nominal_profit"),
            truth.get("developer_profit"),
            comparison.get("developer_equity_nominal_profit"),
        ),
        "developer_equity_contributed": _first_present(
            truth.get("developer_equity_contributions"),
            comparison.get("developer_equity_contributions"),
        ),
        "developer_net_margin": _first_present(
            truth.get("developer_net_margin"),
            comparison.get("developer_net_margin"),
        ),
        "profit_on_cost": _first_present(truth.get("developer_profit_on_cost"), comparison.get("developer_profit_on_cost")),
        "peak_equity": _first_present(truth.get("peak_equity"), comparison.get("peak_equity")),
        "peak_debt": _first_present(truth.get("peak_debt"), comparison.get("peak_debt")),
        "funding_gap": _first_present(truth.get("funding_gap"), truth.get("peak_funding_gap"), comparison.get("peak_funding_gap")),
        "terminal_debt": _first_present(truth.get("terminal_debt"), comparison.get("terminal_debt")),
        "evaluation_status": _first_present(truth.get("evaluation_status"), comparison.get("evaluation_status")),
        "calculation_valid": bool(_first_present(truth.get("calculation_valid"), comparison.get("calculation_valid"), default=True)),
        "cash_reconciliation_passed": bool(_first_present(truth.get("cash_reconciliation_passed"), comparison.get("cash_reconciliation_passed"), default=True)),
        "feasible": bool(truth.get("feasible")) and bool(invariants.get("passed")),
        "constraints": constraints,
        "failed_constraints": list(dict.fromkeys(value for value in failed_constraints if value)),
    }


def _summary_seed_from_case(case: dict[str, Any] | None) -> dict[str, Any] | None:
    if not case or case.get("measure") in (None, ""):
        return None
    constraints = list(case.get("constraints") or [])
    failed = [row.get("constraint_id") for row in constraints if row.get("passed") is False]
    calculation_valid = bool(case.get("calculation_valid", True))
    feasible = bool(case.get("feasible")) and calculation_valid
    return {
        "measure": D(case.get("measure")),
        "summary": {
            "public_npv": case.get("government_npv"),
            "public_nominal": case.get("government_value"),
            "developer_irr": _first_present(case.get("developer_equity_irr"), case.get("developer_irr")),
            "developer_moic": case.get("developer_multiple"),
            "developer_npv": case.get("developer_npv"),
            "developer_nominal_distributions": _first_present(
                case.get("developer_equity_distributions"), case.get("developer_distributions")
            ),
            "developer_nominal_net_profit_after_equity": _first_present(
                case.get("developer_equity_nominal_profit"), case.get("developer_profit")
            ),
            "developer_equity_contributed": case.get("developer_equity_contributions"),
            "developer_net_margin": case.get("developer_net_margin"),
            "profit_on_cost": case.get("developer_profit_on_cost"),
            "peak_equity": case.get("peak_equity"),
            "peak_debt": case.get("peak_debt"),
            "funding_gap": case.get("peak_funding_gap"),
            "terminal_debt": case.get("terminal_debt"),
            "evaluation_status": case.get("evaluation_status"),
            "calculation_valid": calculation_valid,
            "cash_reconciliation_passed": bool(case.get("cash_reconciliation_passed", True)),
            "feasible": feasible,
            "constraints": constraints,
            "failed_constraints": list(dict.fromkeys(value for value in failed if value)),
        },
    }


def _measure_evaluator(
    project: dict[str, Any],
    policy: dict[str, Any],
    contract: dict[str, Any],
    *,
    seed: dict[str, dict[str, Any]] | None = None,
) -> Callable[[Decimal], dict[str, Any]]:
    method = method_from_contract(contract)
    cache: dict[str, dict[str, Any]] = deepcopy(seed or {})

    def evaluate(measure: Decimal) -> dict[str, Any]:
        key = format(+measure, "f")
        cached = cache.get(key)
        # Legacy comparison seeds do not contain the complete party cash-flow
        # metrics required by the participation table.  Re-run the unified
        # engine whenever a seed is incomplete rather than displaying dashes.
        if cached is None or cached.get("developer_nominal_distributions") in (None, ""):
            candidate = _project_at_measure(project, contract, measure)
            result = run_unified_financial_engine(candidate, deepcopy(policy), selected_only=True)
            cache[key] = _selected_summary(result, method)
        return deepcopy(cache[key])

    return evaluate

def _land_areas(project: dict[str, Any], unified: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal]:
    planning = project.get("planning") or {}
    land = D(planning.get("gross_land_area_sqm"))
    buildable = D((unified.get("summary") or {}).get("total_gfa_sqm"))
    if buildable == ZERO:
        buildable = land * D(planning.get("far"))
    sellable = sum((D(row.get("sellable_sqm")) for row in unified.get("products") or []), ZERO)
    return land, buildable, sellable


SCENARIOS = [
    ("MILD_DOWNSIDE", {"price_change": "-0.05", "cost_change": "0.05"}),
    ("BASE", {}),
    ("UPSIDE", {"price_change": "0.10", "cost_change": "-0.05"}),
    ("DOWNSIDE", {"price_change": "-0.10", "cost_change": "0.10"}),
    ("SEVERE_DOWNSIDE", {"price_change": "-0.20", "cost_change": "0.20", "sales_delay_months": 12}),
    ("COST_OVERRUN", {"cost_change": "0.15"}),
    ("PRICE_REDUCTION", {"price_change": "-0.15"}),
    ("SLOW_SALES", {"sales_delay_months": 6}),
    ("COLLECTION_DELAY", {"sales_delay_months": 9}),
    ("DELIVERY_DELAY", {"construction_delay_months": 6, "sales_delay_months": 6}),
    ("FX_SHOCK", {"price_change": "0.05", "cost_change": "0.15"}),
    ("INFLATION_SHOCK", {"cost_change": "0.10"}),
    ("INTEREST_RATE_SHOCK", {"interest_change": "0.03"}),
    ("REGULATORY_DELAY", {"construction_delay_months": 12, "sales_delay_months": 12}),
    ("INFRASTRUCTURE_DELAY", {"construction_delay_months": 9, "sales_delay_months": 9, "cost_change": "0.05"}),
]


def _scenario_envelope(project: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, shocks in SCENARIOS:
        shocked = apply_project_shocks(project, shocks)
        result = run_unified_financial_engine(shocked, policy, selected_only=True)
        truth = result.get("financial_truth") or {}
        rows.append({
            "scenario": name,
            "shocks": shocks,
            "feasible": bool(truth.get("feasible")) and bool((result.get("engine_invariants") or {}).get("passed")),
            "developer_irr": _first_present(truth.get("developer_equity_irr"), truth.get("developer_irr")),
            "developer_npv": truth.get("developer_npv"),
            "government_npv": truth.get("government_npv"),
            "funding_gap": truth.get("funding_gap"),
            "peak_debt": truth.get("peak_debt"),
            "evaluation_status": truth.get("evaluation_status"),
            "calculation_valid": truth.get("calculation_valid"),
            "cash_reconciliation_passed": truth.get("cash_reconciliation_passed"),
            "failed_constraints": list(truth.get("failed_constraints") or []) + list((result.get("engine_invariants") or {}).get("failed_invariant_ids") or []),
        })
    return rows


def _core_range(unified: dict[str, Any], method: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    core_method = {
        "GROSS_SALES_SHARE": "GROSS_SALES", "NET_SALES_SHARE": "NET_SALES", "PROFIT_SHARE": "PROFIT_SHARE",
        "OUTRIGHT_SALE": "UPFRONT", "HYBRID": "HYBRID", "MINIMUM_GUARANTEE": "MINIMUM_GUARANTEE",
    }.get(method)
    comparison = next((row for row in unified.get("contract_comparison") or [] if row.get("method") == core_method), None)
    if not comparison:
        return None, None, None
    minimum = D((comparison.get("minimum_case") or {}).get("government_npv"))
    balanced = D((comparison.get("balanced_case") or {}).get("government_npv"))
    ceiling = D((comparison.get("ceiling_case") or {}).get("government_npv"))
    return minimum, balanced, ceiling



_CLOSURE_TEXT: dict[str, tuple[str, str, str, str]] = {
    "land_uses_100_percent": (
        "يجب أن يساوي مجموع استعمالات الأرض 100%.",
        "Land-use allocation must total 100%.",
        "عدّل نسب استعمالات الأرض ليصبح مجموعها 100%.",
        "Adjust land-use shares so they total 100%.",
    ),
    "product_allocation_100_percent": (
        "يجب أن يساوي مجموع توزيع المنتجات 100%.",
        "Product allocation must total 100%.",
        "عدّل نسب المنتجات الفعالة ليصبح مجموعها 100%.",
        "Adjust active-product allocations so they total 100%.",
    ),
    "terminal_debt_zero": (
        "يوجد دين متبقٍ في نهاية أفق المشروع.",
        "Debt remains at the end of the project horizon.",
        "مدد التحصيل أو زد رأس المال أو عدّل جدول سداد الدين.",
        "Extend collections, add equity, or revise debt repayment.",
    ),
    "deferred_cost_zero": (
        "توجد كلف تطوير لم تُنفذ ضمن المدة والسيولة المتاحة.",
        "Development costs remain deferred beyond the modeled horizon.",
        "مدد البرنامج أو زد التمويل أو أعد توقيت الكلف.",
        "Extend the programme, add funding, or rephase costs.",
    ),
    "contractual_arrears_zero": (
        "توجد دفعات تعاقدية مستحقة لم تُسدد عند الإقفال.",
        "Contractual payments remain unpaid at close.",
        "عدّل توقيت المقابل أو السيولة أو شروط السداد.",
        "Revise consideration timing, liquidity, or payment terms.",
    ),
    "mandatory_obligations_settled": (
        "توجد التزامات إلزامية غير ممولة بالكامل.",
        "Mandatory obligations are not fully funded.",
        "زد التمويل أو عدّل التوقيت والنطاق حتى تزول الفجوة.",
        "Add funding or revise timing and scope until the shortfall is removed.",
    ),
    "unmodeled_scope_zero": (
        "لا يشمل أفق النموذج جميع التدفقات المطلوبة.",
        "The model horizon does not include all required flows.",
        "مدد أفق المشروع أو صحح المنحنيات والمدد المتجاوزة للأفق.",
        "Extend the horizon or correct curves and durations outside it.",
    ),
    "ledger_invariants_passed": (
        "فشل اختبار أو أكثر من اختبارات سلامة دفتر التدفقات.",
        "One or more monthly-ledger integrity checks failed.",
        "راجع الاختبارات الفرعية وحدد أول شهر أو رصيد نهائي فاشل.",
        "Review the failed sub-checks and correct the first failing month or terminal balance.",
    ),
    "no_double_counting": (
        "اكتُشف تداخل أو ازدواج محتمل في مكونات المقابل.",
        "A potential overlap or double count exists in consideration components.",
        "افصل مكونات العقد عن الضرائب والكلف والمنافع الأوسع.",
        "Separate contract components from taxes, costs, and wider benefits.",
    ),
    "cash_reconciliation_passed": (
        "فشلت مصالحة مصادر واستخدامات النقد في شهر واحد أو أكثر.",
        "Monthly cash sources and uses do not reconcile in one or more periods.",
        "راجع أول شهر فاشل وصحح الرصيد الافتتاحي أو مصدر التمويل أو الاستخدام المكرر.",
        "Review the first failing month and correct the opening balance, funding source, or duplicated use.",
    ),
    "numerical_resolution_passed": (
        "لم يكتمل الحل العددي بصورة موثوقة.",
        "The numerical calculation did not resolve reliably.",
        "صحح تعريف العقد أو إعدادات الحل ولا تصدر توصية اقتصادية قبل التقارب.",
        "Correct the contract definition or solver settings and do not issue an economic recommendation before convergence.",
    ),
    "economic_feasibility_passed": (
        "العقد أو النسبة المدخلة لا تحقق قيود الجدوى الاقتصادية الإلزامية.",
        "The entered contract or term does not satisfy mandatory economic-feasibility constraints.",
        "راجع القيود الفاشلة وحدد أول قيد حاكم ثم عدّل النسبة أو الكلف أو التمويل أو الجدول.",
        "Review the failed constraints and first binding constraint, then revise the term, costs, funding, or schedule.",
    ),
    "policy_compliance_passed": (
        "النتيجة لا تحقق جميع حدود السياسة المختارة.",
        "The result does not satisfy every limit in the selected policy.",
        "استخدم سياسة منشورة مناسبة أو عدّل مدخلات المشروع أو العرض دون تجاوز صلاحيات الاعتماد.",
        "Use an appropriate published policy or revise the project inputs or offer within approval authority.",
    ),
    "required_evidence_disclosed": (
        "لم يُسجل إفصاح كافٍ عن عدم اليقين في التقييم.",
        "Material valuation uncertainty has not been disclosed.",
        "سجل الافتراضات الجوهرية والأدلة الناقصة وعدم اليقين.",
        "Record material assumptions, missing evidence, and uncertainty.",
    ),
}


def _closure_diagnostics(
    project_snapshot: dict[str, Any],
    truth: dict[str, Any],
    invariants: dict[str, Any],
    contract_result: dict[str, Any],
    basis: dict[str, Any],
) -> dict[str, Any]:
    land_total = sum(
        (D(row.get("share")) for row in (project_snapshot.get("planning") or {}).get("land_uses") or []),
        ZERO,
    )
    product_total = sum(
        (D(row.get("gfa_allocation_share")) for row in project_snapshot.get("planning_products") or []),
        ZERO,
    )
    # The unified engine intentionally records the feasibility of the
    # *selected investor term* as an invariant so every calculation retains a
    # complete audit trail.  That finding is not, however, a structural ledger
    # failure.  An offer-assessment case must be capable of reaching a
    # locked advisory conclusion precisely when the entered offer is unaffordable or
    # otherwise outside policy: the advisory output is then a documented
    # NOT_SUPPORTED / REQUIRES_REVISION decision.  Only cash/debt roll-forward
    # and terminal-balance invariants block closure here.
    all_invariant_failures = [
        row
        for row in invariants.get("checks") or []
        if bool(row.get("mandatory", True)) and row.get("passed") is False
    ]
    selected_contract_findings = [
        row
        for row in all_invariant_failures
        if str(row.get("invariant_id") or row.get("id") or "").upper()
        == "SELECTED_CONTRACT_CONSTRAINTS_PASS"
    ]
    blocking_invariant_failures = [
        row
        for row in all_invariant_failures
        if str(row.get("invariant_id") or row.get("id") or "").upper()
        != "SELECTED_CONTRACT_CONSTRAINTS_PASS"
    ]
    structural_invariants_passed = not blocking_invariant_failures

    checks = {
        "land_uses_100_percent": abs(land_total - ONE) <= Decimal("0.000001"),
        "product_allocation_100_percent": abs(product_total - ONE) <= Decimal("0.000001"),
        "terminal_debt_zero": abs(D(truth.get("terminal_debt"))) <= Decimal("0.01"),
        "deferred_cost_zero": abs(D(truth.get("deferred_development_cost"))) <= Decimal("0.01"),
        "contractual_arrears_zero": abs(D(truth.get("deferred_contractual_payment"))) <= Decimal("0.01"),
        "mandatory_obligations_settled": abs(D(truth.get("mandatory_shortfall"))) <= Decimal("0.01"),
        "unmodeled_scope_zero": abs(D(truth.get("unmodeled_scope"))) <= Decimal("0.01"),
        "ledger_invariants_passed": structural_invariants_passed,
        "cash_reconciliation_passed": bool(truth.get("cash_reconciliation_passed")),
        "numerical_resolution_passed": bool(truth.get("numerical_resolution_passed", truth.get("calculation_valid"))) and str(truth.get("evaluation_status") or "") != "NUMERICALLY_UNRESOLVED",
        "economic_feasibility_passed": bool(truth.get("economic_feasible", truth.get("feasible"))),
        "policy_compliance_passed": bool(truth.get("policy_compliant")),
        "no_double_counting": bool(contract_result.get("anti_double_counting_passed")),
        "required_evidence_disclosed": bool(basis.get("material_valuation_uncertainty")),
    }
    values: dict[str, tuple[Any, Any, str]] = {
        "land_uses_100_percent": (_fmt(land_total * Decimal("100")), "100", "%"),
        "product_allocation_100_percent": (_fmt(product_total * Decimal("100")), "100", "%"),
        "terminal_debt_zero": (truth.get("terminal_debt"), "<= 0.01", "currency"),
        "deferred_cost_zero": (truth.get("deferred_development_cost"), "<= 0.01", "currency"),
        "contractual_arrears_zero": (truth.get("deferred_contractual_payment"), "<= 0.01", "currency"),
        "mandatory_obligations_settled": (truth.get("mandatory_shortfall"), "<= 0.01", "currency"),
        "unmodeled_scope_zero": (truth.get("unmodeled_scope"), "<= 0.01", "currency"),
        "ledger_invariants_passed": (
            "PASS" if structural_invariants_passed else "FAIL",
            "PASS",
            "status",
        ),
        "cash_reconciliation_passed": (
            truth.get("maximum_cash_balance_variance"),
            "<= 0.01",
            "currency",
        ),
        "numerical_resolution_passed": (truth.get("evaluation_status"), "RESOLVED", "status"),
        "economic_feasibility_passed": (truth.get("economic_feasibility", truth.get("evaluation_status")), "FEASIBLE", "status"),
        "policy_compliance_passed": (truth.get("policy_compliant"), True, "boolean"),
        "no_double_counting": (bool(contract_result.get("anti_double_counting_passed")), True, "boolean"),
        "required_evidence_disclosed": (bool(basis.get("material_valuation_uncertainty")), True, "boolean"),
    }
    details: list[dict[str, Any]] = []
    for identifier, passed in checks.items():
        actual, required, unit = values[identifier]
        reason_ar, reason_en, action_ar, action_en = _CLOSURE_TEXT[identifier]
        row: dict[str, Any] = {
            "id": identifier,
            "code": identifier.upper(),
            "passed": bool(passed),
            "status": "PASS" if passed else "FAIL",
            "actual": actual,
            "required": required,
            "unit": unit,
            "reason_ar": reason_ar,
            "reason_en": reason_en,
            "corrective_action_ar": action_ar,
            "corrective_action_en": action_en,
            "corrective_action": action_en,
        }
        registry = constraint_metadata(identifier)
        row["target_page"] = registry["target_page"]
        if identifier == "no_double_counting":
            row["subchecks"] = deepcopy(contract_result.get("anti_double_counting_checks") or {})
        if identifier == "ledger_invariants_passed":
            row["failed_subchecks"] = [
                {
                    "id": item.get("invariant_id") or item.get("id"),
                    "label": item.get("label"),
                    "actual": item.get("actual"),
                    "operator": item.get("operator"),
                    "threshold": item.get("threshold"),
                    "month": item.get("month") or item.get("first_failed_month"),
                    "failed_constraint_ids": item.get("failed_constraint_ids") or [],
                }
                for item in blocking_invariant_failures
            ]
            row["first_failed_month"] = next(
                (
                    item.get("month") or item.get("first_failed_month")
                    for item in blocking_invariant_failures
                    if item.get("month") or item.get("first_failed_month")
                ),
                None,
            )
        details.append(row)
    # Economic feasibility and policy compliance are decision findings, not
    # ledger-integrity defects.  A valid advisory run must be closable even when
    # the correct conclusion is that the offered term is unaffordable or outside
    # policy.  Only structural/cash/audit failures block closure.
    decision_check_ids = {"economic_feasibility_passed", "policy_compliance_passed"}
    blocking_failed = [row for row in details if not row["passed"] and row["id"] not in decision_check_ids]
    decision_failed = [row for row in details if not row["passed"] and row["id"] in decision_check_ids]
    assessment_findings = [
        {
            "id": item.get("invariant_id") or item.get("id"),
            "code": item.get("invariant_id") or item.get("id"),
            "blocking": False,
            "status": "FINDING",
            "label": item.get("label"),
            "actual": item.get("actual"),
            "operator": item.get("operator"),
            "threshold": item.get("threshold"),
            "failed_constraint_ids": item.get("failed_constraint_ids") or [],
            "reason_ar": (
                "العرض أو الشرط التعاقدي المدخل لا يحقق جميع القيود المؤسسية. "
                "هذه نتيجة قرار وليست خللاً في سلامة دفتر التدفقات، ولذلك يمكن اعتماد "
                "إقفال التحليل الاستشاري مع توصية بالرفض أو التعديل."
            ),
            "reason_en": (
                "The entered offer or contract term does not satisfy every institutional "
                "constraint. This is a decision finding, not a ledger-integrity defect, so "
                "the assessment may be finalised with a reject-or-revise recommendation."
            ),
        }
        for item in selected_contract_findings
    ]
    assessment_findings.extend(
        {
            **deepcopy(row),
            "blocking": False,
            "status": "FINDING",
        }
        for row in decision_failed
    )
    return {
        "passed": not blocking_failed,
        "integrity_passed": not blocking_failed,
        "decision_constraints_passed": not decision_failed and not selected_contract_findings,
        "checks": checks,
        "details": details,
        "failed": blocking_failed,
        "decision_findings": decision_failed,
        "assessment_findings": assessment_findings,
        "selected_contract_constraints_passed": not selected_contract_findings,
        "engine_invariants_status": invariants.get("status"),
    }



def _monetary_value(boundary: dict[str, Any] | None, fallback: Decimal = ZERO) -> Decimal:
    return D((boundary or {}).get("public_npv"), _fmt(fallback) or "0")

def _contract_recommendation_reason(negotiation: dict[str, Any], *, language: str) -> str:
    levels = negotiation.get("levels") or {}
    minimum = (levels.get("minimum") or {}).get(f"display_{language}") or "—"
    balanced = (levels.get("balanced") or {}).get(f"display_{language}") or "—"
    ceiling = (levels.get("risk_adjusted_ceiling") or {}).get(f"display_{language}") or "—"
    if language == "ar":
        if negotiation.get("status") == "NUMERICALLY_UNRESOLVED":
            return "لم يكتمل الحل العددي بصورة موثوقة، ولذلك لم يصدر النظام سقفاً أو توصية اقتصادية."
        if not negotiation.get("range_supported"):
            failures = negotiation.get("failure_reasons") or []
            if failures:
                parts = []
                for row in failures[:3]:
                    label = row.get("label_ar") or row.get("label") or row.get("constraint_id") or "قيد غير محدد"
                    parts.append(f"{label}: الفعلي {row.get('actual')}، والمطلوب {row.get('operator') or ''} {row.get('threshold')}")
                return "لم يجد المحرك نطاقاً قابلاً للتنفيذ. القيود التي منعت الحل: " + "؛ ".join(parts) + "."
            return "لم يجد المحرك نطاقاً تعاقدياً يحقق الحد الأدنى للقيمة العامة وقيود التمويل والإقفال معاً."
        return f"النطاق القابل للدفاع يبدأ من {minimum}، والتوصية المتوازنة {balanced}، والسقف المتحفظ وفق السياسة {ceiling}."
    if negotiation.get("status") == "NUMERICALLY_UNRESOLVED":
        return "The numerical calculation did not resolve reliably, so no economic ceiling or recommendation was issued."
    if not negotiation.get("range_supported"):
        failures = negotiation.get("failure_reasons") or []
        if failures:
            parts = []
            for row in failures[:3]:
                label = row.get("label_en") or row.get("label") or row.get("constraint_id") or "Unnamed constraint"
                parts.append(f"{label}: actual {row.get('actual')}; required {row.get('operator') or ''} {row.get('threshold')}")
            return "The engine found no feasible contractual range. The blocking constraints were: " + "; ".join(parts) + "."
        return "The engine did not find a contractual range that simultaneously meets the public-value, funding, and closure constraints."
    return f"The defensible range starts at {minimum}; the balanced recommendation is {balanced}; and the policy-adjusted ceiling is {ceiling}."

def run_government_decision(
    project: dict[str, Any],
    policy: dict[str, Any],
    case_input: dict[str, Any] | None = None,
    *,
    calculation_run_id: str | None = None,
) -> dict[str, Any]:
    """Run a contract-aware government decision from one authoritative ledger."""

    case_input = deepcopy(case_input or {})
    raw_project = deepcopy(project)
    policy_snapshot = deepcopy(policy)
    raw_public_discount_rate = _first_present(
        case_input.get("public_discount_rate"),
        (policy_snapshot.get("financial_constraints") or {}).get("government_discount_rate"),
    )
    if raw_public_discount_rate in (None, ""):
        raise ValueError(
            "The selected valuation policy must explicitly define "
            "financial_constraints.government_discount_rate."
        )
    public_discount_rate = D(raw_public_discount_rate)
    if public_discount_rate <= Decimal("-1"):
        raise ValueError("The public discount rate must be greater than -100%.")
    # One rate must drive negotiation, scenarios, and the displayed public NPV.
    policy_snapshot.setdefault("financial_constraints", {})["government_discount_rate"] = _fmt(public_discount_rate)
    basis_hint = case_input.get("valuation_basis") or {}
    discount_context = resolve_valuation_discount(
        policy_snapshot,
        project_currency=str(
            basis_hint.get("currency")
            or raw_project.get("reporting_currency")
            or raw_project.get("currency")
            or "USD"
        ),
        cashflow_basis=str(basis_hint.get("nominal_or_real") or "NOMINAL"),
    )
    # The assessment must run under the contract selected by the government
    # user, not under a stale method stored in an earlier project revision.
    project_snapshot, contract = prepare_government_project(raw_project, case_input)
    # Run the governed range analysis once, but verify that its selected result
    # still represents the offered contract.  Older solver paths could replace
    # an infeasible offer with a boundary-search candidate.  When the selected
    # measure or method does not match, recompute the offered contract in an
    # isolated selected-only run while retaining the range output for negotiation.
    range_unified = run_unified_financial_engine(project_snapshot, policy_snapshot, selected_only=False)
    expected_method = method_from_contract(contract)
    expected_measure, _expected_fixed = offered_engine_measure(contract)
    selected_payload = range_unified.get("selected_contract") or {}
    selected_method = str(selected_payload.get("method") or "").upper()
    selected_measure = D(selected_payload.get("measure"), "-1")
    selected_matches_offer = (
        selected_method == expected_method
        and abs(selected_measure - expected_measure) <= Decimal("0.00000001")
    )
    unified = (
        range_unified
        if selected_matches_offer
        else run_unified_financial_engine(project_snapshot, policy_snapshot, selected_only=True)
    )
    land, buildable, sellable = _land_areas(project_snapshot, unified)
    basis = deepcopy(case_input.get("valuation_basis") or _default_valuation_basis(project_snapshot))
    methods = deepcopy(case_input.get("valuation_methods") or _default_valuation_methods(project_snapshot, unified, policy_snapshot))
    valuation_land_area = land
    valuation = evaluate_valuation(
        basis,
        methods,
        land_area_sqm=valuation_land_area,
        buildable_area_sqm=buildable,
        sellable_area_sqm=sellable,
    )
    # Landowner Edition uses the reference land value supplied in the project as
    # an advisory negotiation benchmark.  Market-evidence scoring remains an
    # audit disclosure, but it must not suppress a valid financial analysis or
    # manufacture a LOW/DATA_INSUFFICIENT decision state.
    legacy_evidence_readiness = deepcopy(valuation.get("evidence_readiness") or {})
    valuation["legacy_evidence_readiness"] = legacy_evidence_readiness
    valuation["evidence_readiness"] = {
        "status": "ADVISORY_INPUTS_COMPLETE",
        "provisional": False,
        "verified_method_count": legacy_evidence_readiness.get("verified_method_count", 0),
        "eligible_method_count": legacy_evidence_readiness.get("eligible_method_count", 0),
        "note": "Reference value supplied by the user for advisory financial analysis; not an independent formal valuation.",
    }
    valuation["confidence_grade"] = "NOT_APPLICABLE"
    valuation["reference_value_status"] = "USER_SUPPLIED_ADVISORY_BENCHMARK"
    discount_rate = _fmt(discount_context["effective_annual_rate"])
    contract_result = evaluate_contract(
        unified.get("monthly_cashflow") or [],
        contract,
        currency=str(project_snapshot.get("reporting_currency") or "USD"),
        base_date=basis["base_date"],
        discount_rate=discount_rate,
        public_value_layers=case_input.get("public_value_layers") or {},
    )
    contract_result["contract_input"] = deepcopy(contract)
    truth = unified.get("financial_truth") or {}
    scheduled_npv = contract_result.get("contractual_consideration_npv")
    scheduled_nominal = contract_result.get("contractual_consideration")
    actual_npv = _first_present(truth.get("government_consideration_npv"), scheduled_npv)
    actual_nominal = _first_present(truth.get("government_consideration"), scheduled_nominal)
    actual_cash = sum(
        (
            D(row.get("government_payment")) + D(row.get("landowner_distribution"))
            for row in unified.get("monthly_cashflow") or []
        ),
        ZERO,
    )
    formatted_actual_npv = _fmt(D(actual_npv))
    formatted_scheduled_npv = _fmt(D(scheduled_npv))
    formatted_timing_variance = _fmt(D(formatted_actual_npv) - D(formatted_scheduled_npv))
    # Publish an arithmetically reconciled actual value under the active
    # Decimal context.  The adjustment, if any, is below display precision and
    # prevents audit consumers from seeing a false residual caused solely by
    # formatting three separately rounded high-precision values.
    formatted_actual_npv = _fmt(D(formatted_scheduled_npv) + D(formatted_timing_variance))
    contract_result.update({
        "scheduled_contractual_consideration": scheduled_nominal,
        "scheduled_contractual_consideration_npv": formatted_scheduled_npv,
        "contractual_consideration": _fmt(D(actual_nominal)),
        "contractual_consideration_npv": formatted_actual_npv,
        "cash_receipts": _fmt(actual_cash),
        "actual_modeled_payment_timing": True,
        "payment_timing_npv_variance": formatted_timing_variance,
        "public_cost_contribution_npv": truth.get("government_cost_contribution_npv"),
        "public_net_npv_after_costs": truth.get("government_net_npv_after_costs"),
    })
    contract_result["output_hash"] = sha256_json(contract_result)

    risk = assess_risk_register(
        case_input.get("risk_items")
        or (project_snapshot.get("risk_register") or {}).get("items")
        or []
    )
    metrics = build_metric_snapshot(unified, contract_result, valuation, risk_score=risk.get("score"), policy=policy_snapshot)
    scenarios = _scenario_envelope(project_snapshot, policy_snapshot)
    scenario_pass_ratio = Decimal(sum(1 for row in scenarios if row["feasible"])) / Decimal(len(scenarios))

    market_value = D((valuation.get("reconciliation") or {}).get("value"))
    market_low = D((valuation.get("reconciliation") or {}).get("low"))
    policy_public_floor = D((policy_snapshot.get("share_policy") or {}).get("minimum_government_value_npv"), "0")
    # In the advisory Landowner workflow the entered reference value is an
    # explicit user benchmark.  It can therefore act as the public-value floor
    # without claiming to be an independently certified market valuation.
    market_floor_for_solver = max(policy_public_floor, market_low)
    enforceability = max(ZERO, min(ONE, D(case_input.get("contract_enforceability_score"), "0.65")))
    method = method_from_contract(contract)
    comparison = _comparison(range_unified, method) or _comparison(unified, method)
    evaluator_seed: dict[str, dict[str, Any]] = {
        format(+expected_measure, "f"): _selected_summary(unified, method)
    }
    for case_name in ("minimum_case", "balanced_case", "ceiling_case"):
        seeded = _summary_seed_from_case((comparison or {}).get(case_name))
        if seeded is not None:
            evaluator_seed[format(+seeded["measure"], "f")] = seeded["summary"]
    evaluate_measure = _measure_evaluator(
        project_snapshot, policy_snapshot, contract, seed=evaluator_seed
    )
    native = build_native_negotiation(
        comparison=comparison,
        contract=contract,
        market_low=market_floor_for_solver,
        confidence_grade="NOT_APPLICABLE",
        data_confidence=ONE,
        risk_score=max(ZERO, D(risk.get("score"))),
        downside_survival=scenario_pass_ratio,
        enforceability=enforceability,
        target_developer_irr=D(
            (policy_snapshot.get("financial_constraints") or {}).get("target_developer_irr"),
            "0.22",
        ),
        evaluate_measure=evaluate_measure,
        risk_adjustment_policy=policy_snapshot.get("fair_consideration_policy") or {},
        valuation_policy=policy_snapshot.get("valuation_policy") or {},
        currency=str(basis.get("currency") or project_snapshot.get("reporting_currency") or "USD"),
        policy_minimum_measure=(
            None
            if method in {"UPFRONT", "MINIMUM_GUARANTEE"}
            else D((policy_snapshot.get("share_policy") or {}).get("policy_minimum_share"), "0")
        ),
    )
    negotiation = decorate_native_negotiation(
        native,
        contract=contract,
        currency=str(basis.get("currency") or project_snapshot.get("reporting_currency") or "USD"),
        evaluate_measure=evaluate_measure,
    )

    invariants = unified.get("engine_invariants") or {}
    closure = _closure_diagnostics(project_snapshot, truth, invariants, contract_result, basis)

    numerical_unresolved = (
        str(truth.get("evaluation_status") or "") == "NUMERICALLY_UNRESOLVED"
        or str(negotiation.get("status") or "") == "NUMERICALLY_UNRESOLVED"
        or str(negotiation.get("solver_status") or "") == "NUMERICALLY_UNRESOLVED"
    )
    range_supported = bool(negotiation.get("range_supported")) and not numerical_unresolved
    minimum_money = _monetary_value(negotiation.get("minimum"), market_low) if range_supported else None
    balanced_money = _monetary_value(negotiation.get("balanced"), minimum_money or ZERO) if range_supported else None
    risk_money = _monetary_value(negotiation.get("risk_adjusted_ceiling"), balanced_money or ZERO) if range_supported else None
    technical_money = _monetary_value(negotiation.get("technical_ceiling"), risk_money or ZERO) if range_supported else None
    offer_money = _monetary_value(
        negotiation.get("offer"),
        D(contract_result.get("contractual_consideration_npv")),
    )
    consideration_npv = contract_result.get("contractual_consideration_npv")
    simple_sale = str(contract_result.get("contract_type") or "").upper() == "OUTRIGHT_SALE"
    valuation_readiness = valuation.get("evidence_readiness") or {}
    evidence_provisional = False
    levels: dict[str, Any] = {
        "market_value_benchmark": _fmt(market_value),
        "provisional_screening_benchmark": None,
        "reconciled_land_value": _fmt(market_value),
        "reference_value_status": "USER_SUPPLIED_ADVISORY_BENCHMARK",
        "residual_capacity": _fmt(technical_money),
        "minimum_defensible_consideration": _fmt(minimum_money),
        "technical_ceiling": _fmt(technical_money),
        "risk_adjusted_ceiling": _fmt(risk_money),
        "balanced_recommendation": _fmt(balanced_money),
        "negotiation_range": {"low": _fmt(minimum_money), "high": _fmt(risk_money)},
        "contract_negotiation_range": negotiation,
        "offered_consideration": _fmt(offer_money),
        "offer_position": negotiation.get("offer_position"),
        "equivalent_upfront_consideration": consideration_npv,
        "equivalent_upfront_consideration_definition": "NPV of contractual public consideration at the approved public discount rate",
        "implied_land_value": consideration_npv if simple_sale else None,
        "implied_land_value_status": (
            "DIRECT_SALE_EQUIVALENT" if simple_sale else "NOT_EQUATED_TO_REFERENCE_VALUE"
        ),
        "developer_return_headroom_npv": (metrics.get("developer") or {}).get("return_headroom_npv"),
        "developer_return_headroom_irr": (metrics.get("developer") or {}).get("return_headroom_irr"),
        "funding_headroom": (metrics.get("developer") or {}).get("funding_headroom"),
        "scenario_pass_ratio": _fmt(scenario_pass_ratio),
        "downside_survival": _fmt(scenario_pass_ratio),
        "downside_survival_label": "SCENARIO_PASS_RATIO_NOT_PROBABILITY",
        "downside_break_point": next((row["scenario"] for row in scenarios if not row["feasible"]), None),
    }

    offer_position = str(negotiation.get("offer_position") or "")
    if numerical_unresolved:
        recommendation_status = "NUMERICALLY_UNRESOLVED"
        decision_classification = "NUMERICALLY_UNRESOLVED"
    elif not closure.get("passed"):
        recommendation_status = "REQUIRES_REVISION"
        decision_classification = "AUDIT_FAILURE"
    elif offer_position == "WITHIN_RECOMMENDED_RANGE":
        recommendation_status = "SUPPORTED"
        decision_classification = "SUPPORTED"
    elif offer_position == "BELOW_MINIMUM":
        recommendation_status = "NOT_SUPPORTED"
        decision_classification = "PUBLIC_VALUE_POLICY_NONCOMPLIANT"
    elif offer_position == "ABOVE_RISK_ADJUSTED_CEILING":
        recommendation_status = "CONDITIONALLY_SUPPORTED"
        decision_classification = "PUBLIC_FAVORABLE_ABOVE_RECOMMENDED_RANGE"
    elif offer_position == "ABOVE_TECHNICAL_CEILING":
        recommendation_status = "NOT_SUPPORTED"
        decision_classification = "PUBLIC_FAVORABLE_BUT_ECONOMICALLY_INFEASIBLE"
    else:
        recommendation_status = "NOT_SUPPORTED"
        decision_classification = "NO_FEASIBLE_RANGE"

    missing_data: list[str] = []
    if risk.get("mitigation_coverage") not in (None, "") and D(risk.get("mitigation_coverage")) < Decimal("0.8"):
        missing_data.append("Risk mitigation coverage is below 80%.")
    if numerical_unresolved:
        missing_data.append("The numerical calculation must converge and pass cash reconciliation before any economic recommendation is issued.")
    conditions = [
        "Verify title, encumbrances and legal authority before contracting.",
        "Approve the reference land-value basis and selected policy version.",
        "Require audited sales, collections and eligible-cost reporting.",
        "Secure performance, payment and completion guarantees proportionate to exposure.",
        "Complete legal review of the selected contractual route.",
    ]
    conditions_ar = [
        "التحقق من الملكية والقيود والصلاحية القانونية قبل التعاقد.",
        "اعتماد أساس قيمة الأرض المرجعية وإصدار السياسة المختارة.",
        "اشتراط تقارير مدققة للمبيعات والتحصيلات والكلف المؤهلة.",
        "تأمين ضمانات تنفيذ وسداد وإنجاز تتناسب مع حجم التعرض.",
        "استكمال المراجعة القانونية لمسار التعاقد المختار.",
    ]
    contract_reason_en = _contract_recommendation_reason(negotiation, language="en")
    contract_reason_ar = _contract_recommendation_reason(negotiation, language="ar")
    if decision_classification == "NUMERICALLY_UNRESOLVED":
        contract_reason_en = "No economic recommendation was issued because the calculation did not converge or the monthly cash ledger did not reconcile within tolerance."
        contract_reason_ar = "لم تصدر توصية اقتصادية لأن الحساب لم يتقارب أو لأن دفتر الكاش الشهري لم يتصالح ضمن هامش السماح."
    elif decision_classification == "AUDIT_FAILURE":
        contract_reason_en = "No final recommendation was issued because one or more mandatory closure or audit controls failed."
        contract_reason_ar = "لم تصدر توصية نهائية بسبب فشل قيد إلزامي واحد أو أكثر من قيود الإقفال أو التدقيق."
    elif decision_classification in {
        "PUBLIC_FAVORABLE_ABOVE_RECOMMENDED_RANGE",
        "PUBLIC_FAVORABLE_BUT_ECONOMICALLY_INFEASIBLE",
    }:
        offer_boundary = negotiation.get("offer") or {}
        contract_reason_en = str(offer_boundary.get("reason_en") or contract_reason_en)
        contract_reason_ar = str(offer_boundary.get("reason_ar") or contract_reason_ar)

    output = {
        "manifest": platform_manifest(),
        "status": "SUCCESS" if truth.get("result_usable") else "FAILED",
        "display_authority": "FINANCIAL_TRUTH",
        "financial_truth": deepcopy(truth),
        "financial_reconciliation": {
            "status": "RECONCILED" if truth.get("cash_reconciliation_passed") and truth.get("ledger_invariants_passed") else "OUT_OF_BALANCE",
            "source": truth.get("single_source_financial_kernel"),
            "calculation_hash": truth.get("calculation_hash"),
            "ledger_hash": (unified.get("event_ledger") or {}).get("ledger_hash"),
            "message": "All landowner decision and report metrics use the canonical Financial Truth.",
        },
        "case_mode": str(case_input.get("mode") or "STRUCTURING").upper(),
        "calculation_run_id": calculation_run_id,
        "calculation_basis": {
            "public_discount_rate": _fmt(public_discount_rate),
            "public_discount_rate_effective_annual": discount_rate,
            "public_discount_rate_type": discount_context["rate_type"],
            "public_discount_currency": discount_context["currency"],
            "public_discount_compounding": discount_context["compounding"],
            "valuation_policy_id": discount_context["policy_id"],
            "valuation_policy_version": discount_context["policy_version"],
            "valuation_policy_effective_date": discount_context["effective_date"],
            "public_discount_rate_source": "SELECTED_POLICY",
            "public_consideration_timing": "ACTUAL_MODELED_PAYMENT_TIMING",
        },
        "valuation": valuation,
        "contract": contract_result,
        "metrics": metrics,
        "risk": risk,
        "scenarios": scenarios,
        "scenario_analysis": {
            "scenario_pass_ratio": _fmt(scenario_pass_ratio),
            "is_probability": False,
            "warning": "The pass ratio counts deterministic scenarios and is not a probability of viability.",
        },
        "decision_levels": levels,
        "contract_negotiation": negotiation,
        "recommendation": {
            "status": recommendation_status,
            "classification": decision_classification,
            "calculation_status": truth.get("evaluation_status"),
            "evidence_status": (valuation.get("evidence_readiness") or {}).get("status"),
            "reason": contract_reason_en,
            "reason_en": contract_reason_en,
            "reason_ar": contract_reason_ar,
            "model": contract_result["contract_type"],
            "confidence_grade": "NOT_APPLICABLE",
            "balanced_consideration": _fmt(balanced_money),
            "balanced_contract_term": (negotiation.get("balanced") or {}).get("value"),
            "balanced_contract_display_en": (negotiation.get("balanced") or {}).get("display_en"),
            "balanced_contract_display_ar": (negotiation.get("balanced") or {}).get("display_ar"),
            "conditions_precedent": conditions,
            "conditions_precedent_ar": conditions_ar,
            "missing_data": missing_data,
        },
        "closure": closure,
        "net_sales_reconciliation": deepcopy(unified.get("net_sales_reconciliation") or {}),
        "results_book": build_results_book(
            project_snapshot, unified, valuation, contract_result, metrics, levels
        ),
        "explanation_tree": {
            "result": "contract_negotiation.balanced",
            "policy": "published policy thresholds and public discount rate",
            "formula": "contract-native feasible range, public-value floor and risk adjustment",
            "contract_measure": negotiation,
            "cash_flow": "LandValue360 Engine monthly ledger",
            "inputs": ["project version", "selected policy", "contract terms", "reference land value", "cost and sales assumptions"],
            "evidence": ["disclosed project inputs", "policy parameters", "monthly engine ledger"],
        },
        "registries": registry_snapshot(),
        "limitations": [
            "This is decision support, not a final legal opinion or formal valuation certification.",
            "Unverified planning, title and market assumptions remain conditional.",
            "Taxes and wider economic or social benefits are not treated as land consideration.",
        ],
    }
    output["input_hash"] = sha256_json(
        {"project": project_snapshot, "policy": policy_snapshot, "case": case_input}
    )
    output["ledger_hash"] = (unified.get("event_ledger") or {}).get("ledger_hash")
    output["output_hash"] = sha256_json(output)
    return output
