"""Developer-perspective advisory output built from the canonical financial truth.

This module never recalculates cash flow. It translates the selected contract,
verified fair-range comparison and constraint-solver output into a constructive
investment and negotiation view for the developer edition.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

DEVELOPER_ADVISORY_VERSION = "2.1.1"

_METHOD_LABELS = {
    "GROSS_SALES": ("نسبة من إجمالي المبيعات", "Gross-sales share"),
    "NET_SALES": ("نسبة من صافي المبيعات", "Net-sales share"),
    "PROFIT_SHARE": ("حصة من الأرباح", "Profit share"),
    "UPFRONT": ("مقابل ثابت أو شراء مباشر", "Upfront / direct consideration"),
    "HYBRID": ("صيغة هجينة", "Hybrid consideration"),
}
_COMPLEXITY = {"UPFRONT": 1, "GROSS_SALES": 1, "NET_SALES": 2, "HYBRID": 3, "PROFIT_SHARE": 4}


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        if value in (None, ""):
            return Decimal(default)
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _s(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _method_label(method: str) -> dict[str, str]:
    ar, en = _METHOD_LABELS.get(method, (method.replace("_", " "), method.replace("_", " ").title()))
    return {"ar": ar, "en": en}


def _comparison_rows(unified: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in unified.get("contract_comparison") or []:
        method = str(raw.get("method") or "").upper()
        if not method:
            continue
        status = str(raw.get("status") or "")
        rows.append({
            "method": method,
            "label": _method_label(method),
            "status": status,
            "feasible": status in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"},
            "minimum": _s(raw.get("fair_floor", raw.get("minimum"))),
            "balanced": _s(raw.get("balanced", raw.get("recommended"))),
            "technical_ceiling": _s(raw.get("technical_ceiling", raw.get("maximum"))),
            "developer_profit": _s(raw.get("developer_profit")),
            "developer_profit_on_cost": _s(raw.get("developer_profit_on_cost")),
            "developer_npv": _s(raw.get("developer_npv")),
            "developer_irr": _s(raw.get("developer_irr")),
            "developer_equity_irr": _s(raw.get("developer_equity_irr")),
            "developer_multiple": _s(raw.get("developer_multiple")),
            "peak_equity": _s(raw.get("peak_equity")),
            "funding_gap": _s(raw.get("peak_funding_gap")),
            "government_npv": _s(raw.get("government_npv")),
            "governing_constraint_id": raw.get("governing_constraint_id"),
            "complexity_rank": _COMPLEXITY.get(method, 5),
            "failure_reasons": raw.get("failure_reasons") or [],
        })
    return rows


def _solver_share(output: dict[str, Any]) -> dict[str, Any] | None:
    for suggestion in (output.get("constraint_solver") or {}).get("suggestions") or []:
        if str(suggestion.get("lever") or "").upper() == "LANDOWNER_SHARE" and suggestion.get("solves_all_constraints"):
            return suggestion
    return None


def _strengths(truth: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if _d(truth.get("project_npv")) > 0:
        rows.append({"ar": "المشروع يخلق قيمة حالية موجبة قبل هيكلة المقابل والتمويل.", "en": "The project creates positive present value before financing and consideration structuring."})
    if _d(truth.get("developer_npv")) > 0:
        rows.append({"ar": "القيمة الحالية للمطور موجبة وفق الافتراضات الحالية.", "en": "Developer NPV is positive under the current assumptions."})
    if abs(_d(truth.get("funding_gap"))) <= Decimal("0.01"):
        rows.append({"ar": "لا توجد فجوة تمويل غير مغطاة في الجدول المحسوب.", "en": "No unsupported funding gap remains in the calculated schedule."})
    if abs(_d(truth.get("terminal_unpaid_obligations"))) <= Decimal("0.01"):
        rows.append({"ar": "الدين والكلف والدفعات التعاقدية مقفلة في نهاية النموذج.", "en": "Debt, costs and contractual payments close by the end of the model."})
    if _d(truth.get("developer_equity_irr")) > Decimal("0"):
        rows.append({"ar": "عائد حقوق الملكية موجب ويمكن اختباره مقابل حدود السياسة.", "en": "Equity return is positive and can be tested against policy thresholds."})
    return rows[:5]


def _issues(truth: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in truth.get("constraints") or []:
        if bool(row.get("passed")):
            continue
        result.append({
            "constraint_id": row.get("constraint_id"),
            "actual": row.get("actual"),
            "operator": row.get("operator"),
            "threshold": row.get("threshold"),
            "severity": row.get("severity") or "HIGH",
            "label": row.get("label") or row.get("constraint_id"),
            "explanation": row.get("explanation") or row.get("reason"),
        })
    return result


def build_developer_advisory(
    output: dict[str, Any],
    *,
    project_snapshot: dict[str, Any] | None = None,
    policy_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a developer-oriented advisory interpretation of one run."""

    truth = output.get("financial_truth") or {}
    unified = output.get("unified_financial_result") or {}
    project = project_snapshot or {}
    policy = policy_snapshot or {}
    partnership = project.get("partnership") or {}
    valuation_policy_context = project.get("developer_valuation_policy") or {}
    valuation_policy_summary = valuation_policy_context.get("summary") if isinstance(valuation_policy_context, dict) else {}
    valuation_policy_summary = valuation_policy_summary if isinstance(valuation_policy_summary, dict) else {}
    competitive_position = min(Decimal("1"), max(Decimal("0"), _d(valuation_policy_summary.get("developer_competitive_position_factor"), "0.40")))
    current_method = str(truth.get("method") or partnership.get("method") or "GROSS_SALES").upper()
    current_measure = truth.get("approved_share")
    comparison = _comparison_rows(unified)
    current_comparison = next((row for row in comparison if row["method"] == current_method), None)
    feasible_rows = [row for row in comparison if row["feasible"]]

    preferred = None
    if feasible_rows:
        # Prefer the structure producing the highest developer NPV at its
        # balanced point; use lower audit complexity as a deterministic tie-break.
        preferred = max(
            feasible_rows,
            key=lambda row: (
                _d(row.get("developer_npv"), "-1E99")
                / (Decimal("1") + Decimal("0.05") * Decimal(int(row.get("complexity_rank") or 9))),
                -int(row.get("complexity_rank") or 9),
            ),
        )

    strategy_source = "NOT_CALCULATED"
    opening = competitive = maximum = None
    selected_only = bool((unified.get("provenance") or {}).get("selected_only_evaluation"))
    if current_comparison and current_comparison.get("feasible") and not selected_only:
        opening_d = _d(current_comparison.get("minimum"))
        balanced_d = _d(current_comparison.get("balanced"))
        maximum_d = _d(current_comparison.get("technical_ceiling"))
        # The landowner balanced point is an institutional reference.  The
        # developer competitive offer is intentionally positioned inside the
        # verified range, preserving negotiation headroom rather than treating
        # the landowner-balanced point as the developer's opening recommendation.
        competitive_d = opening_d + (balanced_d - opening_d) * competitive_position
        opening = str(opening_d)
        competitive = str(min(max(competitive_d, opening_d), maximum_d))
        maximum = str(maximum_d)
        strategy_source = "VERIFIED_FAIR_RANGE"
    else:
        share_remedy = _solver_share(output)
        if share_remedy:
            maximum_d = max(Decimal("0"), _d(share_remedy.get("required_value")))
            policy_minimum = max(Decimal("0"), _d((policy.get("share_policy") or {}).get("policy_minimum_share")))
            opening_d = max(policy_minimum, maximum_d * Decimal("0.85"))
            competitive_d = max(policy_minimum, maximum_d * Decimal("0.95"))
            opening = str(min(opening_d, maximum_d))
            competitive = str(min(competitive_d, maximum_d))
            maximum = str(maximum_d)
            strategy_source = "VERIFIED_SINGLE_LEVER_REMEDIATION"

    failed = _issues(truth)
    scope_coverage = ((output.get("cost_calculation") or {}).get("scope_coverage") or {})
    scope_incomplete = str(scope_coverage.get("status") or "").upper() == "INCOMPLETE"
    if scope_incomplete:
        failed.append({
            "constraint_id": "COST_SCOPE_COMPLETE",
            "severity": "CRITICAL",
            "actual": scope_coverage.get("missing_required_scope_ids") or [],
            "required": "COMPLETE",
            "reason_ar": "نطاق الكلف غير مكتمل؛ توجد فئات لازمة لم تدخل في كلفة المشروع.",
            "reason_en": "The cost scope is incomplete; required cost categories are missing from project cost.",
            "remediation_ar": "أكمل بنود الكلف المفقودة ثم أعد الحساب قبل الاعتماد على العوائد أو التوصية التفاوضية.",
            "remediation_en": "Complete the missing cost categories and recalculate before relying on returns or negotiation guidance.",
        })
    result_usable = bool(truth.get("result_usable"))
    policy_compliant = bool(truth.get("policy_compliant"))
    economic_feasible = bool(truth.get("economic_feasible"))
    closure_passed = bool(truth.get("closure_passed"))
    if scope_incomplete:
        # A negotiation range derived from a model with missing required cost
        # categories would overstate developer headroom. Keep the economics
        # visible, but withhold the offer recommendation until the scope is
        # completed and recalculated.
        opening = None
        competitive = None
        maximum = None
        preferred = None
        strategy_source = "WITHHELD_COST_SCOPE_INCOMPLETE"

    if not result_usable:
        status = "CALCULATION_NOT_USABLE"
        headline_ar = "يجب تصحيح الحساب قبل تقديم توصية استثمارية"
        headline_en = "The calculation must be corrected before an investment recommendation is issued"
    elif scope_incomplete:
        status = "CONDITIONAL_COST_SCOPE_INCOMPLETE"
        headline_ar = "الاقتصاديات الأولية إيجابية، لكن نطاق الكلف غير مكتمل"
        headline_en = "Preliminary economics are positive, but the cost scope is incomplete"
    elif economic_feasible and policy_compliant and closure_passed:
        status = "INVESTABLE_WITHIN_CURRENT_TERMS"
        headline_ar = "المشروع قابل للاستثمار ضمن الشروط الحالية"
        headline_en = "The project is investable under the current terms"
    elif economic_feasible and closure_passed:
        status = "VIABLE_BUT_TERMS_NEED_REVISION"
        headline_ar = "اقتصاديات المشروع إيجابية، لكن شروط المشاركة تحتاج تعديلاً"
        headline_en = "Project economics are positive, but the partnership terms need revision"
    else:
        status = "STRUCTURAL_OR_FUNDING_REVISION_REQUIRED"
        headline_ar = "المشروع يحتاج إعادة هيكلة للنطاق أو التمويل أو الجدول"
        headline_en = "The project requires scope, funding or schedule restructuring"

    actions = []
    for suggestion in (output.get("constraint_solver") or {}).get("suggestions") or []:
        actions.append({
            "lever": suggestion.get("lever"),
            "title_ar": suggestion.get("title_ar"),
            "title_en": suggestion.get("title_en"),
            "current_value": suggestion.get("current_value"),
            "required_value": suggestion.get("required_value"),
            "unit": suggestion.get("unit"),
            "solves_all_constraints": bool(suggestion.get("solves_all_constraints")),
            "resulting_metrics": suggestion.get("resulting_metrics") or {},
        })

    min_profit = (policy.get("financial_constraints") or {}).get("minimum_profit_on_cost")
    min_irr = (policy.get("financial_constraints") or {}).get("minimum_developer_irr")
    target_irr = (policy.get("financial_constraints") or {}).get("target_developer_irr")

    return {
        "developer_advisory_version": DEVELOPER_ADVISORY_VERSION,
        "status": status,
        "headline_ar": headline_ar,
        "headline_en": headline_en,
        "message_ar": (
            "تعرض هذه الصفحة اقتصاديات المشروع أولاً، ثم توضح كيف يمكن تحسين شروط الأرض والمشاركة دون إخفاء القيود أو المخاطر."
        ),
        "message_en": (
            "This view presents project economics first, then shows how land and partnership terms can be improved without hiding constraints or risks."
        ),
        "current_contract": {
            "method": current_method,
            "label": _method_label(current_method),
            "measure": _s(current_measure),
        },
        "negotiation_strategy": {
            "source": strategy_source,
            "opening_offer": opening,
            "competitive_offer": competitive,
            "maximum_tolerable": maximum,
            "measure_type": "AMOUNT" if current_method == "UPFRONT" else "RATE",
            "explanation_ar": (
                "أُوقفت التوصية التفاوضية لأن نطاق الكلف المطلوب غير مكتمل. أكمل كلف البنية التحتية والمرافق أو وثّق أنها صفر ثم أعد الحساب."
                if strategy_source == "WITHHELD_COST_SCOPE_INCOMPLETE"
                else "العرض الافتتاحي هو الحد الأدنى القابل للدفاع عنه، والعرض التنافسي يحافظ على هامش تفاوض للمطور داخل النطاق المتحقق، والحد الأقصى هو آخر نقطة اجتازت القيود في إعادة الحساب."
                if strategy_source == "VERIFIED_FAIR_RANGE"
                else "النطاق الإرشادي مشتق من تعديل واحد تم التحقق منه بإعادة الحساب؛ يجب تشغيل التحليل الكامل لإصدار نطاق متعدد الطرق."
                if strategy_source == "VERIFIED_SINGLE_LEVER_REMEDIATION"
                else "لم يتوفر نطاق تفاوضي موثوق في هذا التشغيل."
            ),
            "explanation_en": (
                "The negotiation recommendation is withheld because required cost scope is incomplete. Complete infrastructure and public-facility costs, or document them as zero, then recalculate."
                if strategy_source == "WITHHELD_COST_SCOPE_INCOMPLETE"
                else "The opening offer is the defensible floor, the competitive offer preserves developer negotiation headroom inside the verified range, and the maximum is the last point that passed all constraints under recalculation."
                if strategy_source == "VERIFIED_FAIR_RANGE"
                else "The planning range is derived from one verified single-lever recalculation; run full analysis for a cross-method range."
                if strategy_source == "VERIFIED_SINGLE_LEVER_REMEDIATION"
                else "No reliable negotiation range was available in this run."
            ),
        },
        "preferred_contract": preferred,
        "contract_options": comparison,
        "valuation_policy": {
            "version_id": valuation_policy_context.get("version_id") if isinstance(valuation_policy_context, dict) else None,
            "pack_id": valuation_policy_context.get("pack_id") if isinstance(valuation_policy_context, dict) else None,
            "pack_name": valuation_policy_context.get("pack_name") if isinstance(valuation_policy_context, dict) else None,
            "version_label": valuation_policy_context.get("version_label") if isinstance(valuation_policy_context, dict) else None,
            "policy_hash": valuation_policy_context.get("policy_hash") if isinstance(valuation_policy_context, dict) else None,
            "developer_competitive_position_factor": str(competitive_position),
        },
        "strengths": _strengths(truth),
        "issues": failed,
        "recommended_actions": actions,
        "policy_thresholds": {
            "minimum_developer_irr": _s(min_irr),
            "target_developer_irr": _s(target_irr),
            "minimum_profit_on_cost": _s(min_profit),
        },
        "capital_policy": {
            "minimum_dscr": _s((policy.get("finance_constraints") or {}).get("minimum_dscr")),
            "maximum_ltc": _s((policy.get("finance_constraints") or {}).get("maximum_ltc")),
            "maximum_ltv": _s((policy.get("finance_constraints") or {}).get("maximum_ltv")),
            "equity_commitment_mode": (policy.get("funding_policy") or {}).get("equity_commitment_mode"),
            "fixed_equity_direct_cost_share": _s((policy.get("funding_policy") or {}).get("fixed_equity_direct_cost_share")),
        },
        "procurement_policy": {
            "opening_discount_rate": _s((policy.get("procurement_policy") or {}).get("opening_discount_rate")),
            "target_discount_rate": _s((policy.get("procurement_policy") or {}).get("target_discount_rate")),
            "minimum_retained_contingency_rate": _s((policy.get("procurement_policy") or {}).get("minimum_retained_contingency_rate")),
        },
        "core_metrics": {
            key: truth.get(key)
            for key in (
                "gross_potential_revenue", "gross_sales", "net_sales", "planned_total_cost",
                "project_profit", "project_profit_on_cost", "project_npv", "project_irr",
                "developer_profit", "developer_profit_on_cost", "developer_npv", "developer_irr",
                "developer_equity_contributions", "developer_equity_irr", "developer_equity_multiple",
                "peak_equity", "funding_gap", "terminal_unpaid_obligations",
                "government_consideration", "government_npv",
            )
        },
        "methodology_note": "Advisory interpretation only. All values are sourced from the canonical monthly financial truth and verified solver outputs; no cash-flow metric is recomputed here.",
    }
