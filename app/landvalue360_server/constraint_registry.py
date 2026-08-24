"""Central user-facing registry for financial and closure constraints.

The financial engines retain stable machine codes.  Presentation layers consume
this registry so Arabic and English interfaces never expose raw codes and every
failure can point the user to the governing page and the first failed month.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any


def _entry(
    title_ar: str,
    title_en: str,
    explanation_ar: str,
    explanation_en: str,
    target_page: str,
    corrective_action_ar: str,
    corrective_action_en: str,
    *,
    aggregate: bool = False,
) -> dict[str, Any]:
    return {
        "title_ar": title_ar,
        "title_en": title_en,
        "criterion_ar": title_ar,
        "criterion_en": title_en,
        "success_explanation_ar": f"تم التحقق من القيد: {title_ar}.",
        "success_explanation_en": f"Constraint satisfied: {title_en}.",
        "failure_explanation_ar": explanation_ar,
        "failure_explanation_en": explanation_en,
        # Compatibility aliases are retained for old snapshots only.
        "explanation_ar": explanation_ar,
        "explanation_en": explanation_en,
        "target_page": target_page,
        "corrective_action_ar": corrective_action_ar,
        "corrective_action_en": corrective_action_en,
        "aggregate": aggregate,
    }


FUNDING_ACTION_AR = "راجع التزامات حقوق الملكية والتمويل وتوقيت التحصيل والصرف في أول شهر فاشل."
FUNDING_ACTION_EN = "Review equity/funding commitments and collection/expenditure timing in the first failed month."
COST_ACTION_AR = "راجع نطاق الكلف ومسؤولية السداد والجدول التنفيذي وتأكد من عدم تكرار البند."
COST_ACTION_EN = "Review cost scope, payment responsibility, execution timing, and duplicate inclusion."
FINANCE_ACTION_AR = "راجع هيكل الدين وشروط السداد والاحتياطي والسيولة المتاحة."
FINANCE_ACTION_EN = "Review debt structure, repayment terms, reserves, and available liquidity."
RETURN_ACTION_AR = "راجع الإيرادات والكلف والتوقيت ومقابل مالك الأرض ثم أعد تشغيل التحليل."
RETURN_ACTION_EN = "Review revenue, cost, timing, and landowner consideration, then rerun the analysis."
CONTRACT_ACTION_AR = "راجع طريقة المقابل ونسبته وتوقيته وتعريف الاستقطاعات والتزامات الأطراف."
CONTRACT_ACTION_EN = "Review the consideration method, rate/amount, timing, deductions, and party obligations."
POLICY_ACTION_AR = "راجع السياسة المنشورة وحدودها وفترة سريانها دون تعديل النتيجة يدوياً."
POLICY_ACTION_EN = "Review the published policy, limits, and effective period without manually overriding the result."
EVIDENCE_ACTION_AR = "أكمل المدخلات والأدلة والافتراضات المطلوبة أو صنف النتيجة كتحليل أولي."
EVIDENCE_ACTION_EN = "Complete required inputs, evidence, and assumptions or classify the result as preliminary."
CASH_ACTION_AR = "راجع دفتر التدفقات الشهري ومصادر واستخدامات النقد في أول شهر فاشل."
CASH_ACTION_EN = "Review the monthly ledger and sources/uses in the first failed month."


CONSTRAINT_REGISTRY: dict[str, dict[str, Any]] = {
    "LAND_USES_100_PERCENT": _entry(
        "يجب أن يساوي مجموع استعمالات الأرض 100%", "Land uses must total 100%",
        "مجموع نسب استعمالات الأرض لا يساوي 100%.", "Land-use percentages do not total 100%.",
        "landowner/project", "عدّل نسب استعمالات الأرض حتى يصبح مجموعها 100%.", "Adjust land-use percentages so they total 100%.",
    ),
    "PRODUCT_ALLOCATION_100_PERCENT": _entry(
        "يجب أن يساوي توزيع المنتجات 100%", "Product allocation must total 100%",
        "مجموع نسب المنتجات الفعالة لا يساوي 100%.", "Active product percentages do not total 100%.",
        "landowner/products", "عدّل نسب المنتجات حتى يصبح مجموعها 100%.", "Adjust product percentages so they total 100%.",
    ),
    "ZERO_DEBT_TERMINAL": _entry(
        "يجب أن يكون الدين الختامي صفراً", "Terminal debt must be zero",
        "يوجد دين غير مسدد عند نهاية أفق المشروع.", "Debt remains unpaid at the end of the project horizon.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "ZERO_COST_DEFERRED": _entry(
        "يجب إقفال الكلف المؤجلة", "Deferred cost must close at zero",
        "توجد كلف تطوير لم تُنفذ أو تُدفع ضمن الأفق.", "Development costs remain unexecuted or unpaid within the horizon.",
        "developer/costs", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "CALCULATION_RESOLVED": _entry(
        "لم يكتمل الحل الحسابي بصورة موثوقة", "Calculation did not resolve reliably",
        "لم يصل المحرك إلى نتيجة عددية مستقرة وقابلة للتفسير.", "The engine did not reach a stable, interpretable numerical result.",
        "landowner/assessment", "راجع تعريف العقد والمدخلات ثم أعد التحليل؛ لا تصدر توصية قبل اكتمال الحل.", "Review the contract definition and inputs, then rerun; do not issue a recommendation before resolution.",
    ),
    "NUMERICAL_RESOLUTION": _entry(
        "مشكلة في التقارب العددي", "Numerical resolution issue",
        "تعذر إثبات النتيجة ضمن حدود التقارب والسماح المعتمدة.", "The result could not be established within the approved convergence and tolerance limits.",
        "landowner/assessment", CONTRACT_ACTION_AR, CONTRACT_ACTION_EN,
    ),
    "MIN_GOVERNMENT_VALUE_NPV": _entry(
        "القيمة الحالية لمقابل مالك الأرض أقل من الحد", "Landowner consideration NPV is below the minimum",
        "القيمة الحالية للمقابل لا تحقق الحد الأدنى المعتمد في سياسة التقييم.", "The present value of consideration is below the valuation-policy minimum.",
        "landowner/assessment", POLICY_ACTION_AR, POLICY_ACTION_EN,
    ),
    "MAX_PUBLIC_NPV": _entry(
        "القيمة الحالية العامة تتجاوز الحد المحدد", "Public NPV exceeds the configured limit",
        "القيمة الحالية العامة تقع خارج الحد المستخدم في مسار التحسين.", "Public NPV lies outside the limit used by the optimization path.",
        "landowner/assessment", POLICY_ACTION_AR, POLICY_ACTION_EN,
    ),
    "MIN_FUNDING_GAP": _entry(
        "فجوة التمويل هي القيد الحاكم", "Funding gap is the governing constraint",
        "يحدد الاحتياج التمويلي المتبقي الحد الممكن للعقد أو التوصية.", "The remaining funding requirement governs the feasible contract or recommendation boundary.",
        "developer/funding", FUNDING_ACTION_AR, FUNDING_ACTION_EN,
    ),
    "MAX_RESIDUAL_FUNDING_GAP": _entry(
        "فجوة التمويل المتبقية تتجاوز الحد المسموح",
        "Residual funding gap exceeds the permitted limit",
        "بقي عجز نقدي بعد استخدام رأس المال والتمويل المعترف بهما.",
        "A cash deficit remains after recognised equity and committed funding are used.",
        "developer/funding", FUNDING_ACTION_AR, FUNDING_ACTION_EN,
    ),
    "FUNDING_GAP": _entry(
        "فجوة تمويل غير مغطاة", "Unfunded financing gap",
        "لا تكفي مصادر التمويل المعترف بها لتغطية استخدامات المشروع.",
        "Recognised funding sources do not cover project uses.",
        "developer/funding", FUNDING_ACTION_AR, FUNDING_ACTION_EN,
    ),
    "STRUCTURED_FUNDING_GAP": _entry(
        "فجوة في هيكل التمويل", "Structured funding gap",
        "هيكل حقوق الملكية والدين لا يغطي الاحتياج النقدي الموقّت.",
        "The equity and debt structure does not cover the temporary cash requirement.",
        "developer/funding", FUNDING_ACTION_AR, FUNDING_ACTION_EN,
    ),
    "MANDATORY_PAYMENT_SHORTFALL": _entry(
        "عجز في سداد الالتزامات الإلزامية", "Mandatory payment shortfall",
        "لم تكفِ السيولة لسداد كلفة أو التزام تمويلي أو تعاقدي إلزامي في موعده.",
        "Available liquidity did not cover a mandatory cost, financing, or contractual obligation when due.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "MANDATORY_SHORTFALL_ZERO": _entry(
        "لم يتحقق شرط انعدام العجز الإلزامي", "Mandatory shortfall must be zero",
        "يجب أن تنتهي جميع الالتزامات الإلزامية دون رصيد غير مسدد.",
        "All mandatory obligations must close with no unpaid balance.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "MANDATORY_OBLIGATIONS_SETTLED": _entry(
        "الالتزامات الإلزامية غير مسددة بالكامل", "Mandatory obligations are not fully settled",
        "توجد التزامات حالية أو متأخرة لم تُغطَّ قبل الإقفال.",
        "Current or overdue mandatory obligations remain unsettled at close.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "DEFERRED_DEVELOPMENT_COST": _entry(
        "كلف تطوير مؤجلة", "Deferred development cost",
        "بقيت كلف مخططة لم تُنفذ أو تُدفع ضمن الأفق والسيولة المتاحة.",
        "Planned development cost remains unexecuted or unpaid within the model horizon.",
        "developer/costs", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "DEFERRED_COST_ZERO": _entry(
        "يجب إقفال الكلف المؤجلة", "Deferred cost must close at zero",
        "لا يجوز إنهاء المشروع مع كلفة تطوير مؤجلة.",
        "The project cannot close with deferred development cost.",
        "developer/costs", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "COMPLETE_SCOPE": _entry(
        "نطاق المشروع غير مكتمل", "Project scope is incomplete",
        "يوجد جزء من الكلف أو الالتزامات أو التدفقات لم يدخل بالكامل في النموذج.",
        "A portion of costs, obligations, or cash flows is not fully represented in the model.",
        "developer/costs", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "UNMODELED_SCOPE_ZERO": _entry(
        "يوجد نطاق غير ممثل في النموذج", "Unmodelled scope remains",
        "يجب أن يشمل أفق المشروع جميع التدفقات والكلف المطلوبة.",
        "The project horizon must include all required flows and costs.",
        "developer/costs", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "PROJECT_COST_SCOPE_RECONCILED": _entry(
        "نطاق كلف المشروع غير متصالح", "Project cost scope does not reconcile",
        "إجمالي الكلف المخططة لا يساوي الكلف الممثلة والمنفذة في الدفتر.",
        "Planned total cost does not equal the cost represented and executed in the ledger.",
        "developer/costs", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "COST_RESPONSIBILITY_RECONCILES": _entry(
        "تحميل الكلف على الأطراف غير متصالح", "Party cost responsibility does not reconcile",
        "مجموع ما يتحمله المطور ومالك الأرض والطرف الثالث لا يساوي كلفة المشروع.",
        "Developer, landowner, and third-party burdens do not sum to total project cost.",
        "landowner/project", COST_ACTION_AR, COST_ACTION_EN,
    ),
    "TERMINAL_DEBT": _entry(
        "دين متبقٍ عند إقفال المشروع", "Debt remains at project close",
        "لم يُسدد أصل الدين بالكامل قبل نهاية الأفق.",
        "Debt principal is not fully repaid before the end of the horizon.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "TERMINAL_DEBT_ZERO": _entry(
        "يجب أن يكون الدين الختامي صفراً", "Terminal debt must be zero",
        "لا يجوز إصدار نتيجة مقفلة مع دين نهائي غير مسدد.",
        "A closed result cannot contain unpaid terminal debt.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "FINANCE_ARREARS_ZERO": _entry(
        "متأخرات تمويلية غير مسددة", "Financing arrears remain",
        "توجد فوائد أو رسوم أو دفعات أصل دين مستحقة وغير مسددة.",
        "Interest, fees, or principal instalments remain due and unpaid.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "MINIMUM_DSCR": _entry(
        "نسبة تغطية خدمة الدين أقل من الحد", "Debt-service coverage is below the minimum",
        "التدفقات المتاحة لا تحقق الحد الأدنى لتغطية خدمة الدين.",
        "Cash available for debt service does not meet the minimum coverage threshold.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "MAXIMUM_LTC": _entry(
        "نسبة الدين إلى الكلفة أعلى من الحد", "Loan-to-cost exceeds the limit",
        "هيكل التمويل يتجاوز الحد المسموح للدين نسبةً إلى الكلفة.",
        "Debt exceeds the approved proportion of project cost.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "MAXIMUM_LTV": _entry(
        "نسبة الدين إلى القيمة أعلى من الحد", "Loan-to-value exceeds the limit",
        "هيكل التمويل يتجاوز الحد المسموح للدين نسبةً إلى قيمة المشروع.",
        "Debt exceeds the approved proportion of project value.",
        "developer/financing", FINANCE_ACTION_AR, FINANCE_ACTION_EN,
    ),
    "PROJECT_PROFIT_NONNEGATIVE": _entry(
        "ربح المشروع التشغيلي سالب", "Project operating profit is negative",
        "إجمالي صافي المبيعات أقل من جميع كلف التطوير قبل الأرض والتمويل.",
        "Total net sales are below all development costs before land and financing.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "DEVELOPER_PROFIT_NONNEGATIVE": _entry(
        "صافي ربح المطور سالب", "Developer net profit is negative",
        "لا يحتفظ المطور بفائض اسمي بعد الكلف ومقابل مالك الأرض.",
        "The developer retains no nominal surplus after costs and landowner consideration.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "PROJECT_NPV": _entry(
        "القيمة الحالية للمشروع دون الحد", "Project NPV is below the threshold",
        "القيمة الحالية للتدفقات غير الممولة أقل من الحد المعتمد.",
        "Unlevered project cash-flow NPV is below the approved threshold.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "PROJECT_IRR": _entry(
        "عائد المشروع أقل من الحد", "Project IRR is below the threshold",
        "عائد المشروع غير الممول لا يحقق الحد المعتمد.",
        "Unlevered project return does not meet the approved threshold.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "DEVELOPER_IRR": _entry(
        "عائد المطور أقل من الحد", "Developer IRR is below the threshold",
        "عائد المطور بعد مقابل الأرض لا يحقق الحد المطلوب.",
        "Developer return after landowner consideration is below the required threshold.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "TARGET_DEVELOPER_IRR": _entry(
        "عائد المطور المستهدف غير متحقق", "Target developer IRR is not achieved",
        "النقطة المختبرة لا تحقق العائد المستهدف المستخدم في تحديد التوصية المتوازنة.",
        "The tested point does not achieve the target return used for the balanced recommendation.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "DEVELOPER_NPV": _entry(
        "القيمة الحالية لحقوق المطور دون الحد", "Developer equity NPV is below the threshold",
        "القيمة الحالية لتدفقات حقوق المطور لا تحقق الحد المطلوب.",
        "Developer equity cash-flow NPV does not meet the required threshold.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "MIN_DEVELOPER_IRR": _entry(
        "عائد حقوق ملكية المطور أقل من الحد", "Developer equity IRR is below the minimum",
        "عائد حقوق الملكية الفعلي أقل من الحد المعتمد في السياسة.",
        "Actual developer equity IRR is below the policy minimum.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "MIN_DEVELOPER_NPV": _entry(
        "القيمة الحالية لحقوق المطور أقل من الحد", "Developer equity NPV is below the minimum",
        "القيمة الحالية لحقوق المطور أقل من الحد المعتمد.",
        "Developer equity NPV is below the approved minimum.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "MIN_PROFIT_ON_COST": _entry(
        "ربح المطور على الكلفة أقل من الحد", "Developer profit on cost is below the minimum",
        "الهامش الاسمي للمطور لا يحقق الحد الأدنى المعتمد.",
        "The developer's nominal margin does not meet the approved minimum.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "MIN_DEVELOPER_MULTIPLE": _entry(
        "مضاعف حقوق ملكية المطور أقل من الحد", "Developer equity multiple is below the minimum",
        "إجمالي المقبوضات إلى رأس المال المستثمر لا يحقق الحد المطلوب.",
        "Total equity receipts relative to invested equity do not meet the required threshold.",
        "developer/results", RETURN_ACTION_AR, RETURN_ACTION_EN,
    ),
    "PROFIT_SHARE_CONVERGENCE": _entry(
        "لم يتقارب حساب حصة الأرباح", "Profit-share calculation did not converge",
        "الدورة بين قاعدة الربح والمدفوعات لم تصل إلى حل عددي ضمن السماح المعتمد.",
        "The circular profit-share base and payments did not converge within the approved tolerance.",
        "landowner/assessment", CONTRACT_ACTION_AR, CONTRACT_ACTION_EN,
    ),
    "CONTRACTUAL_ARREARS_ZERO": _entry(
        "متأخرات تعاقدية غير مسددة", "Contractual arrears remain",
        "توجد دفعات مستحقة لمالك الأرض لم تُسدّد عند الإقفال.",
        "Landowner consideration remains due and unpaid at close.",
        "landowner/assessment", CONTRACT_ACTION_AR, CONTRACT_ACTION_EN,
    ),
    "DEFERRED_CONTRACTUAL_PAYMENT": _entry(
        "دفعات تعاقدية مؤجلة", "Deferred contractual payment",
        "أُجّل جزء من المقابل التعاقدي خارج توقيته المطلوب.",
        "A portion of contractual consideration was deferred beyond its required timing.",
        "landowner/assessment", CONTRACT_ACTION_AR, CONTRACT_ACTION_EN,
    ),
    "NO_DOUBLE_COUNTING": _entry(
        "تداخل أو ازدواج في مكونات المقابل", "Potential double counting in consideration",
        "يظهر بند واحد في أكثر من مسار اقتصادي أو تعاقدي.",
        "A single item appears in more than one economic or contractual path.",
        "landowner/assessment", CONTRACT_ACTION_AR, CONTRACT_ACTION_EN,
    ),
    "MONTHLY_CASH_RECONCILIATION": _entry(
        "فشل مصالحة النقد الشهرية", "Monthly cash reconciliation failed",
        "لا يساوي النقد الافتتاحي والمصادر مطروحاً منها الاستخدامات النقد الختامي في شهر أو أكثر.",
        "Opening cash plus sources minus uses does not equal closing cash in one or more months.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "MONTHLY_LEDGER_BALANCED": _entry(
        "دفتر التدفقات الشهري غير متوازن", "Monthly ledger is not balanced",
        "يوجد فرق بين مصادر واستخدامات النقد في الدفتر الشهري.",
        "Monthly ledger sources and uses do not balance.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "LEDGER_INVARIANTS_PASSED": _entry(
        "فشل اختبار سلامة دفتر التدفقات", "Ledger integrity test failed",
        "فشل واحد أو أكثر من اختبارات الدفتر والإقفال الإلزامية.",
        "One or more mandatory ledger and closure checks failed.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "CASH_RECONCILIATION_PASSED": _entry(
        "مصالحة النقد غير ناجحة", "Cash reconciliation did not pass",
        "توجد فروق نقدية تتجاوز السماح المعتمد.",
        "Cash variances exceed the approved tolerance.",
        "developer/cashflow", CASH_ACTION_AR, CASH_ACTION_EN,
    ),
    "POLICY_COMPLIANCE_PASSED": _entry(
        "شروط السياسة غير مستوفاة", "Policy constraints are not satisfied",
        "نجح الحساب عددياً لكن قيداً أو أكثر من حدود السياسة لم يتحقق.",
        "The calculation resolved, but one or more policy thresholds were not met.",
        "admin/policies", POLICY_ACTION_AR, POLICY_ACTION_EN,
    ),
    "REQUIRED_EVIDENCE_DISCLOSED": _entry(
        "الإفصاح عن الافتراضات أو الأدلة غير مكتمل", "Required assumptions or evidence are not disclosed",
        "لا تتوافر إفصاحات كافية عن مصدر بعض المدخلات الجوهرية.",
        "Sufficient disclosure is missing for one or more material inputs.",
        "landowner/project", EVIDENCE_ACTION_AR, EVIDENCE_ACTION_EN,
    ),
    "SELECTED_CONTRACT_CONSTRAINTS_PASS": _entry(
        "نتيجة تجميعية لقيود العقد", "Aggregate selected-contract constraint",
        "هذا قيد تجميعي؛ يجب عرض القيود التفصيلية الفاشلة بدلاً منه.",
        "This is an aggregate result; display the underlying failed constraints instead.",
        "landowner/assessment", CONTRACT_ACTION_AR, CONTRACT_ACTION_EN,
        aggregate=True,
    ),
}


def constraint_metadata(code: str | None) -> dict[str, Any]:
    key = str(code or "").strip().upper()
    payload = deepcopy(CONSTRAINT_REGISTRY.get(key) or {
        "title_ar": key.replace("_", " ") or "قيد غير مسمى",
        "title_en": key.replace("_", " ").title() or "Unnamed constraint",
        "criterion_ar": key.replace("_", " ") or "قيد غير مسمى",
        "criterion_en": key.replace("_", " ").title() or "Unnamed constraint",
        "success_explanation_ar": "تم التحقق من القيد وفق القيمة الفعلية والحد المطلوب.",
        "success_explanation_en": "The constraint was satisfied using the actual and required values.",
        "failure_explanation_ar": "راجع المدخلات والسياسة المرتبطة بهذا القيد ثم أعد الحساب.",
        "failure_explanation_en": "Review the inputs and policy governing this constraint, then recalculate.",
        "explanation_ar": "راجع المدخلات والسياسة المرتبطة بهذا القيد ثم أعد الحساب.",
        "explanation_en": "Review the inputs and policy governing this constraint, then recalculate.",
        "target_page": "developer/analysis",
        "corrective_action_ar": "راجع المدخلات والسياسة المرتبطة بهذا القيد ثم أعد الحساب.",
        "corrective_action_en": "Review the inputs and policy governing this constraint, then recalculate.",
        "aggregate": False,
    })
    payload["code"] = key
    return payload


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _diagnostic_failure(row: dict[str, Any]) -> bool:
    if str(row.get("status") or "").upper() == "FAIL":
        return True
    for key in (
        "monthly_uncovered_gap",
        "current_mandatory_shortfall",
        "deferred_cost_backlog",
        "contractual_arrears",
        "finance_arrears",
    ):
        value = _decimal(row.get(key))
        if value is not None and abs(value) > Decimal("0.01"):
            return True
    return False


def enrich_constraint_rows(
    rows: list[dict[str, Any]] | None,
    diagnostic_ledger: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach bilingual metadata and first-failure diagnostics to constraints.

    Aggregate wrapper constraints are kept in the calculation output for audit
    compatibility but marked as aggregate so user interfaces can suppress them
    when the underlying failures are available.
    """

    diagnostics = list(diagnostic_ledger or [])
    first_failed = next((row for row in diagnostics if _diagnostic_failure(row)), None)
    enriched: list[dict[str, Any]] = []
    for source in rows or []:
        item = deepcopy(source)
        code = str(item.get("constraint_id") or item.get("id") or "").upper()
        meta = constraint_metadata(code)
        item.setdefault("constraint_id", code)
        item["code"] = code
        item["title_ar"] = meta["title_ar"]
        item["title_en"] = meta["title_en"]
        item["criterion_ar"] = meta.get("criterion_ar") or meta["title_ar"]
        item["criterion_en"] = meta.get("criterion_en") or meta["title_en"]
        passed = bool(item.get("passed"))
        item["reason_ar"] = meta["success_explanation_ar"] if passed else meta["failure_explanation_ar"]
        item["reason_en"] = meta["success_explanation_en"] if passed else meta["failure_explanation_en"]
        item["success_explanation_ar"] = meta["success_explanation_ar"]
        item["success_explanation_en"] = meta["success_explanation_en"]
        item["failure_explanation_ar"] = meta["failure_explanation_ar"]
        item["failure_explanation_en"] = meta["failure_explanation_en"]
        item["corrective_action_ar"] = meta["corrective_action_ar"]
        item["corrective_action_en"] = meta["corrective_action_en"]
        item["target_page"] = meta["target_page"]
        item["aggregate"] = bool(meta.get("aggregate"))
        if not bool(item.get("passed")) and first_failed:
            item.setdefault("first_failed_month", first_failed.get("month"))
            item.setdefault("first_failed_date", first_failed.get("date"))
            item.setdefault("failure_amount", first_failed.get("monthly_uncovered_gap") or first_failed.get("current_mandatory_shortfall"))
            item.setdefault("failure_components", {
                "uncovered_gap": first_failed.get("monthly_uncovered_gap"),
                "mandatory_shortfall": first_failed.get("current_mandatory_shortfall"),
                "deferred_cost": first_failed.get("deferred_cost_backlog"),
                "contractual_arrears": first_failed.get("contractual_arrears"),
                "finance_arrears": first_failed.get("finance_arrears"),
            })
        enriched.append(item)
    return enriched


def visible_failed_constraints(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    failed = [row for row in rows or [] if not bool(row.get("passed"))]
    detailed = [row for row in failed if not bool(row.get("aggregate"))]
    return detailed or failed
