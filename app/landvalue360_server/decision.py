"""Explainable institutional decision output for LandValue360 Platform 2.1.1 Stabilized.

The calculation kernel remains the numerical authority.  This module only
translates its validation and constraint records into a structured bilingual
explanation.  No financial metric is recalculated here.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from landvalue360_common.versions import DECISION_ENGINE_VERSION
from .constraint_registry import constraint_metadata

_RATE_IDS = {
    "PROJECT_IRR", "DEVELOPER_IRR", "TARGET_DEVELOPER_IRR", "EQUITY_IRR",
    "PROFIT_ON_COST", "MAXIMUM_LTC", "MAXIMUM_LTV",
}
_MULTIPLE_IDS = {"DEVELOPER_MULTIPLE", "MINIMUM_DSCR"}
_MONEY_IDS = {
    "PROJECT_NPV", "DEVELOPER_NPV", "GOVERNMENT_VALUE_NPV", "FUNDING_GAP",
    "STRUCTURED_FUNDING_GAP", "DEFERRED_DEVELOPMENT_COST", "DEFERRED_CONTRACTUAL_PAYMENT",
    "TERMINAL_DEBT", "HYBRID_MINIMUM_EXECUTION", "TERMINAL_DEBT_ZERO",
    "DEFERRED_COST_ZERO", "CONTRACTUAL_ARREARS_ZERO", "MANDATORY_SHORTFALL_ZERO",
    "UNMODELED_SCOPE_ZERO",
}
_CRITICAL_IDS = {
    "FUNDING_GAP", "STRUCTURED_FUNDING_GAP", "DEFERRED_DEVELOPMENT_COST",
    "DEFERRED_CONTRACTUAL_PAYMENT", "TERMINAL_DEBT", "HYBRID_MINIMUM_EXECUTION",
    "MONTHLY_LEDGER_BALANCED", "TERMINAL_DEBT_ZERO", "DEFERRED_COST_ZERO",
    "CONTRACTUAL_ARREARS_ZERO", "MANDATORY_SHORTFALL_ZERO", "UNMODELED_SCOPE_ZERO",
    "SELECTED_CONTRACT_CONSTRAINTS_PASS",
}

_LABELS: dict[str, tuple[str, str]] = {
    "PROJECT_NPV": ("صافي القيمة الحالية للمشروع", "Project NPV"),
    "PROJECT_IRR": ("معدل عائد المشروع", "Project IRR"),
    "DEVELOPER_IRR": ("معدل عائد المطور", "Developer IRR"),
    "TARGET_DEVELOPER_IRR": ("معدل العائد المستهدف للمطور", "Target developer IRR"),
    "PROFIT_ON_COST": ("الربح على الكلفة", "Profit on cost"),
    "DEVELOPER_MULTIPLE": ("مضاعف المطور", "Developer multiple"),
    "DEVELOPER_NPV": ("صافي القيمة الحالية للمطور", "Developer NPV"),
    "FUNDING_GAP": ("فجوة التمويل غير المهيكلة", "Unstructured funding gap"),
    "EQUITY_IRR": ("عائد حقوق الملكية", "Equity IRR"),
    "MINIMUM_DSCR": ("تغطية خدمة الدين", "Debt-service coverage"),
    "MAXIMUM_LTC": ("القرض إلى الكلفة", "Loan to cost"),
    "MAXIMUM_LTV": ("القرض إلى القيمة", "Loan to value"),
    "STRUCTURED_FUNDING_GAP": ("فجوة التمويل المهيكلة", "Structured funding gap"),
    "DEFERRED_DEVELOPMENT_COST": ("كلفة تطوير مؤجلة عند نهاية المشروع", "Deferred development cost at completion"),
    "DEFERRED_CONTRACTUAL_PAYMENT": ("دفعات تعاقدية مؤجلة عند نهاية المشروع", "Deferred contractual payments at completion"),
    "HYBRID_MINIMUM_EXECUTION": ("عجز الحد الأدنى للتنفيذ", "Hybrid minimum-execution shortfall"),
    "TERMINAL_DEBT": ("رصيد دين متبقٍ عند النهاية", "Terminal debt balance"),
    "MONTHLY_LEDGER_BALANCED": ("توازن دفتر التدفقات الشهري", "Monthly ledger reconciliation"),
    "TERMINAL_DEBT_ZERO": ("إقفال الدين النهائي", "Terminal debt closure"),
    "DEFERRED_COST_ZERO": ("إقفال الكلف المؤجلة", "Deferred-cost closure"),
    "CONTRACTUAL_ARREARS_ZERO": ("إقفال المتأخرات التعاقدية", "Contractual arrears closure"),
    "MANDATORY_SHORTFALL_ZERO": ("إقفال العجز الإلزامي", "Mandatory shortfall closure"),
    "UNMODELED_SCOPE_ZERO": ("اكتمال نطاق التدفقات", "Modelled-scope completeness"),
    "SELECTED_CONTRACT_CONSTRAINTS_PASS": ("اجتياز قيود العقد المختار", "Selected-contract constraints"),
}

_REASONS: dict[str, tuple[str, str, str, str]] = {
    "FUNDING_GAP": (
        "التمويل المعترف به لا يغطي ذروة العجز في الجدول النقدي الأساسي.",
        "Recognized funding does not cover the peak deficit in the base cash-flow schedule.",
        "لا يمكن تنفيذ البرنامج الزمني المدخل دون تمويل إضافي أو إعادة توقيت الصرف.",
        "The entered schedule cannot be delivered without more funding or expenditure rephasing.",
    ),
    "STRUCTURED_FUNDING_GAP": (
        "بقي احتياج نقدي غير مغطى بعد تطبيق التزامات الدين وحقوق الملكية الفعلية.",
        "A cash requirement remains uncovered after applying committed debt and equity.",
        "هيكل التمويل غير مغلق مالياً، حتى لو كانت مؤشرات الربحية موجبة.",
        "The financing structure is not financially closed even if profitability is positive.",
    ),
    "DEFERRED_DEVELOPMENT_COST": (
        "سياسة الصرف المرتبطة بالنقد أجّلت جزءاً من كلف التطوير لعدم توفر سيولة كافية.",
        "The cash-linked spend policy deferred development cost because sufficient liquidity was unavailable.",
        "لم يكتمل نطاق المشروع المدخل. عدم ظهور فجوة تمويل لا يعني اكتمال التنفيذ.",
        "The entered project scope is incomplete. Absence of a funding gap does not mean full delivery.",
    ),
    "DEFERRED_CONTRACTUAL_PAYMENT": (
        "أجّلت سياسة ضبط السيولة دفعات تعاقدية مستحقة للجهة العامة أو لطرف تعاقدي آخر.",
        "The liquidity-control policy deferred contractual payments due to the public entity or another contractual party.",
        "لا يكتمل الإغلاق المالي أو التعاقدي ما دامت هذه الدفعات غير مسددة.",
        "Financial and contractual closure is incomplete while these payments remain unpaid.",
    ),
    "HYBRID_MINIMUM_EXECUTION": (
        "السيولة المتاحة لم تكفِ لتنفيذ الحد الأدنى الإلزامي من الصرف المجدول.",
        "Available liquidity was insufficient to execute the mandatory minimum share of scheduled spend.",
        "برنامج التنفيذ الهجين يفشل شرط استمرارية التنفيذ المتفق عليه.",
        "The hybrid programme fails its agreed minimum-execution covenant.",
    ),
    "TERMINAL_DEBT": (
        "بقي رصيد من التسهيل التمويلي غير مسدد عند تاريخ نهاية النموذج.",
        "A senior-facility balance remains unpaid at the model completion date.",
        "المشروع لا يغلق الدين ضمن المدة أو التدفقات الحالية.",
        "The project does not retire debt within the current duration and cash flows.",
    ),
    "EQUITY_IRR": (
        "العائد الفعلي على مساهمات حقوق الملكية أدنى من الحد المؤسسي.",
        "The return on actual equity contributions is below the institutional threshold.",
        "هيكل الدين والرسوم والتوقيت لا يحقق عائد المستثمر المطلوب.",
        "Debt, fees and timing do not deliver the required equity return.",
    ),
    "MINIMUM_DSCR": (
        "النقد المتاح لخدمة الدين غير كافٍ مقارنة بخدمة الدين المطلوبة.",
        "Cash available for debt service is insufficient relative to required debt service.",
        "يرتفع خطر التعثر أو الحاجة إلى احتياطي وفترة سماح وإعادة هيكلة.",
        "Default risk rises or a reserve, grace period or restructuring is required.",
    ),
}


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _gap(actual: Decimal | None, operator: str, threshold: Decimal | None) -> Decimal | None:
    if actual is None or threshold is None:
        return None
    if operator in {">=", ">"}:
        return max(threshold - actual, Decimal("0"))
    if operator in {"<=", "<"}:
        return max(actual - threshold, Decimal("0"))
    if operator == "==":
        return abs(actual - threshold)
    return None


def _unit(identifier: str, currency: str) -> str:
    if identifier in _RATE_IDS:
        return "% p.a." if "IRR" in identifier else "%"
    if identifier in _MULTIPLE_IDS:
        return "x"
    if identifier in _MONEY_IDS:
        return currency
    if "PAYBACK" in identifier:
        return "years"
    return ""


def _severity(identifier: str, gap: Decimal | None, threshold: Decimal | None) -> str:
    if identifier in _CRITICAL_IDS:
        return "CRITICAL"
    if gap is None:
        return "HIGH"
    denominator = abs(threshold or Decimal("0"))
    ratio = gap / denominator if denominator > 0 else gap
    if ratio >= Decimal("0.25"):
        return "HIGH"
    return "MEDIUM"


def _generic_reason(identifier: str, operator: str) -> tuple[str, str, str, str]:
    label_ar, label_en = _LABELS.get(identifier, (identifier.replace("_", " "), identifier.replace("_", " ").title()))
    return (
        f"القيمة الفعلية لمؤشر {label_ar} لا تحقق الشرط المؤسسي {operator}.",
        f"The actual {label_en} does not satisfy the institutional {operator} requirement.",
        "يؤثر القيد مباشرة في قرار الجدوى أو قابلية التمويل أو جاهزية الطرح.",
        "The constraint directly affects feasibility, bankability or tender readiness.",
    )


def _remediation(identifier: str) -> tuple[str, str]:
    if identifier in {"FUNDING_GAP", "STRUCTURED_FUNDING_GAP"}:
        return (
            "زيادة التمويل الملتزم، رفع حقوق الملكية، إعادة توقيت الصرف، أو اعتماد Cash Driven/Hybrid مع إظهار أي كلف مؤجلة.",
            "Increase committed funding/equity, rephase spend, or use Cash Driven/Hybrid while disclosing deferred cost.",
        )
    if identifier == "DEFERRED_DEVELOPMENT_COST":
        return (
            "زيادة التحصيل أو التمويل، تخفيض/تجزئة النطاق، أو تمديد البرنامج حتى يصبح الرصيد المؤجل صفراً.",
            "Increase collections or funding, reduce/phase scope, or extend the programme until deferred cost is zero.",
        )
    if identifier == "DEFERRED_CONTRACTUAL_PAYMENT":
        return (
            "تمديد البرنامج أو زيادة السيولة وجدولة الدفعات تعاقدياً حتى يصبح رصيد الدفعات المؤجلة صفراً قبل الإغلاق.",
            "Extend the programme or increase liquidity and contractually schedule payments until the deferred-payment balance is zero before closure.",
        )
    if identifier == "HYBRID_MINIMUM_EXECUTION":
        return (
            "رفع السيولة المتاحة أو تخفيض الحد الأدنى الهجين ضمن سياسة معتمدة ومبررة.",
            "Increase available liquidity or reduce the hybrid minimum under an approved, justified policy.",
        )
    if identifier == "TERMINAL_DEBT":
        return (
            "تمديد الاستحقاق، زيادة السداد من المبيعات، تخفيض الدين، أو إضافة دفعة نهائية موثقة.",
            "Extend maturity, increase sales sweep, reduce debt, or add a documented terminal repayment source.",
        )
    if identifier in {"PROJECT_IRR", "DEVELOPER_IRR", "TARGET_DEVELOPER_IRR", "EQUITY_IRR", "PROJECT_NPV", "DEVELOPER_NPV", "PROFIT_ON_COST", "DEVELOPER_MULTIPLE"}:
        return (
            "اختبار رفع أسعار البيع، خفض الكلف، تحسين سرعة التحصيل، أو خفض حصة مالك الأرض ضمن الحدود المؤسسية.",
            "Test higher sales prices, lower cost, faster collections, or a lower landowner share within policy bounds.",
        )
    if identifier in {"MINIMUM_DSCR", "MAXIMUM_LTC", "MAXIMUM_LTV"}:
        return (
            "إعادة هيكلة الدين أو زيادة حقوق الملكية وتحسين توقيت السحب والسداد.",
            "Restructure debt or increase equity and improve draw/repayment timing.",
        )
    return (
        "تعديل المدخلات المؤثرة أو الحصول على موافقة سياسة موثقة قبل اعتماد القرار.",
        "Adjust the governing inputs or obtain a documented policy approval before decision approval.",
    )


def _normalise_constraint(raw: dict[str, Any], domain: str, currency: str) -> dict[str, Any]:
    identifier = str(raw.get("constraint_id") or raw.get("invariant_id") or raw.get("code") or "UNKNOWN_CONSTRAINT")
    if identifier == "MAX_RESIDUAL_FUNDING_GAP":
        identifier = "STRUCTURED_FUNDING_GAP"
    actual = _decimal(raw.get("actual"))
    threshold = _decimal(raw.get("threshold"))
    operator = str(raw.get("operator") or "")
    gap = _gap(actual, operator, threshold)
    gap_percent = None
    if gap is not None and threshold not in (None, Decimal("0")):
        gap_percent = gap / abs(threshold)
    reason_ar, reason_en, impact_ar, impact_en = _REASONS.get(identifier, _generic_reason(identifier, operator))
    remediation_ar, remediation_en = _remediation(identifier)
    registry = constraint_metadata(identifier)
    if identifier not in _REASONS:
        remediation_ar = registry["corrective_action_ar"]
        remediation_en = registry["corrective_action_en"]
    label_ar, label_en = _LABELS.get(
        identifier,
        (str(raw.get("label") or identifier.replace("_", " ")), str(raw.get("label") or identifier.replace("_", " "))),
    )
    return {
        "constraint_id": identifier,
        "domain": domain,
        "label_ar": label_ar,
        "label_en": label_en,
        "actual": raw.get("actual"),
        "operator": operator,
        "threshold": raw.get("threshold"),
        "unit": _unit(identifier, currency),
        "status": str(raw.get("status") or ("PASS" if raw.get("passed") else "FAIL")),
        "severity": _severity(identifier, gap, threshold),
        "gap": str(gap) if gap is not None else None,
        "gap_percent": str(gap_percent) if gap_percent is not None else None,
        "reason_ar": reason_ar,
        "reason_en": reason_en,
        "impact_ar": impact_ar,
        "impact_en": impact_en,
        "remediation_ar": remediation_ar,
        "remediation_en": remediation_en,
        "target_page": registry["target_page"],
        "first_failed_month": raw.get("first_failed_month") or raw.get("month"),
        "reason_codes": raw.get("reason_codes") or [],
        "source_explanation": raw.get("explanation"),
    }


def build_decision_explanation(output: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic explanation from calculation and finance output."""

    currency = str(output.get("reporting_currency") or output.get("finance_analysis", {}).get("currency") or "")
    causes: list[dict[str, Any]] = []
    finance = output.get("finance_analysis") or {}
    truth = output.get("financial_truth") or ((output.get("unified_financial_result") or {}).get("financial_truth") or {})
    unified = output.get("unified_financial_result") or {}
    terminal_diagnostic = (
        unified.get("terminal_funding_diagnostic")
        or output.get("terminal_funding_diagnostic")
        or {}
    )
    if truth:
        # the unified monthly engine is the sole decision authority for governed runs.  The
        # historical feasibility/finance outputs remain audit comparators and
        # must not create contradictory headline failures.
        for source_raw in truth.get("constraints") or []:
            raw = dict(source_raw)
            normalized_id = str(raw.get("constraint_id") or "").upper()
            if normalized_id in {
                "MAX_RESIDUAL_FUNDING_GAP", "MANDATORY_PAYMENT_SHORTFALL",
                "COMPLETE_SCOPE", "TERMINAL_DEBT",
            }:
                raw.setdefault("first_failed_month", terminal_diagnostic.get("first_failed_month"))
                raw.setdefault("reason_codes", terminal_diagnostic.get("reason_codes") or [])
            if raw.get("mandatory", True) and not bool(raw.get("passed")):
                causes.append(_normalise_constraint(raw, "UNIFIED_ENGINE", currency))
    else:
        approved = output.get("approved_case") or {}
        for raw in approved.get("constraints") or []:
            if raw.get("mandatory", True) and not bool(raw.get("passed")):
                causes.append(_normalise_constraint(raw, "FEASIBILITY", currency))
        for raw in finance.get("constraints") or []:
            if raw.get("mandatory", True) and raw.get("status") != "NOT_CONFIGURED" and not bool(raw.get("passed")):
                causes.append(_normalise_constraint(raw, "FINANCE", currency))
    engine_invariants = output.get("engine_invariants") or ((output.get("unified_financial_result") or {}).get("engine_invariants") or {})
    for raw in engine_invariants.get("checks") or []:
        if raw.get("mandatory", True) and not bool(raw.get("passed")):
            causes.append(_normalise_constraint(raw, "ENGINE", currency))

    validation_failures = []
    if output.get("status") == "FAILED":
        for item in output.get("validation_messages") or []:
            validation_failures.append({
                "code": str(item.get("code") or "CALCULATION_FAILED"),
                "severity": str(item.get("severity") or "ERROR"),
                "message": str(item.get("message") or "Calculation failed."),
                "path": item.get("path"),
            })

    # ``SELECTED_CONTRACT_CONSTRAINTS_PASS`` is an aggregate invariant.  When
    # the underlying failed contract constraints are already present, showing
    # the aggregate as an additional cause duplicates the same economic issue
    # and confuses users.
    specific_failures = [
        item for item in causes
        if str(item.get("constraint_id") or "").upper() != "SELECTED_CONTRACT_CONSTRAINTS_PASS"
    ]
    if specific_failures:
        causes = specific_failures
    # De-duplicate constraints that arrive through both the selected contract
    # and the invariant adapter. Keep the most severe first occurrence.
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    causes.sort(key=lambda item: (rank.get(str(item["severity"]), 9), str(item["constraint_id"])))
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in causes:
        key = str(item.get("constraint_id") or "")
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)
    causes = deduplicated
    if validation_failures or causes:
        status = "FAIL"
        top = causes[0] if causes else None
        headline_ar = "الحساب مكتمل، لكن بعض شروط الاستثمار الحالية تحتاج معالجة"
        headline_en = "The calculation is complete, but some current investment requirements need action"
        if top:
            summary_ar = f"السبب الحاكم: {top['label_ar']}. يوجد {len(causes)} قيد إلزامي فاشل."
            summary_en = f"Governing cause: {top['label_en']}. {len(causes)} mandatory constraint(s) fail."
        else:
            summary_ar = "تعذر إكمال الحساب بسبب أخطاء في المدخلات أو بنية النموذج."
            summary_en = "Calculation could not complete because of input or model-structure errors."
    elif output.get("status") == "SUCCESS_WITH_WARNINGS" or finance.get("status") in {"NOT_ENABLED", "NOT_CALCULABLE"}:
        status = "CONDITIONAL"
        headline_ar = "النتيجة قابلة للاستمرار مع تحفظات"
        headline_en = "The result may proceed subject to reservations"
        summary_ar = "لا توجد قيود إلزامية فاشلة، لكن توجد تحذيرات أو تحليلات غير مكتملة يجب إغلاقها قبل الاعتماد النهائي."
        summary_en = "No mandatory constraint fails, but warnings or incomplete analyses must be closed before final approval."
    else:
        status = "PASS"
        headline_ar = "المشروع يحقق القيود المؤسسية المحسوبة"
        headline_en = "The project satisfies the calculated institutional constraints"
        summary_ar = "النجاح محصور بالمدخلات والسياسة والأدلة الحالية ولا يغني عن المراجعة القانونية والفنية والتمويلية."
        summary_en = "The pass is limited to current inputs, policy and evidence and does not replace legal, technical or financing review."

    return {
        "decision_engine_version": DECISION_ENGINE_VERSION,
        "status": status,
        "headline_ar": headline_ar,
        "headline_en": headline_en,
        "summary_ar": summary_ar,
        "summary_en": summary_en,
        "failure_count": len(causes) + len(validation_failures),
        "warning_count": sum(1 for item in output.get("validation_messages") or [] if str(item.get("severity")) == "WARNING"),
        "causes": causes,
        "validation_failures": validation_failures,
        "trace": {
            "calculation_model_version": output.get("calculation_model_version"),
            "finance_model_version": finance.get("finance_model_version") or output.get("finance_model_version"),
            "application_input_hash": output.get("application_input_hash"),
            "engine_version": (output.get("engine_manifest") or {}).get("engine_version"),
            "source": "the unified monthly engine invariant, contractual and finance constraints; no metric is recomputed by the explanation engine.",
        },
    }
