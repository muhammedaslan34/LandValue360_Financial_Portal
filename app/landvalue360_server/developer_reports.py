"""Developer-facing institutional reports sourced from one calculation run.

The renderer is deliberately presentation-only.  Financial values come from the
canonical ``financial_truth`` and ``developer_advisory`` objects stored on the
calculation run.  No cash-flow schedule, IRR, NPV or fair-share result is
recalculated inside the report layer.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from landvalue360_government.reports import render_html_to_pdf

from landvalue360_common.versions import DEVELOPER_REPORT_VERSION


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        if value in (None, ""):
            return Decimal(default)
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _money(value: Any, currency: str) -> str:
    return f"{currency} {_d(value):,.2f}"


def _pct(value: Any) -> str:
    return f"{_d(value) * Decimal(100):,.2f}%"


def _multiple(value: Any) -> str:
    return f"{_d(value):,.2f}x"


def _label(language: str, ar: str, en: str) -> str:
    return ar if language == "ar" else en


def _offer(value: Any, measure_type: str, currency: str) -> str:
    if value in (None, ""):
        return "-"
    return _money(value, currency) if str(measure_type).upper() == "AMOUNT" else _pct(value)


def _metric(label: str, value: str, note: str = "", *, tone: str = "") -> str:
    tone_class = f" metric--{tone}" if tone else ""
    note_html = f"<small>{escape(note)}</small>" if note else ""
    return (
        f'<article class="metric{tone_class}"><span>{escape(label)}</span>'
        f'<strong>{escape(value)}</strong>{note_html}</article>'
    )


def _status(output: dict[str, Any], language: str) -> tuple[str, str]:
    truth = output.get("financial_truth") or (output.get("unified_financial_result") or {}).get("financial_truth") or {}
    scope = ((output.get("cost_calculation") or {}).get("scope_coverage") or {})
    if not bool(truth.get("result_usable")):
        return "danger", _label(language, "تعذر الاعتماد على عملية الحساب", "Calculation is not usable")
    if str(scope.get("status") or "").upper() == "INCOMPLETE":
        return "warning", _label(language, "نتيجة أولية - نطاق الكلف غير مكتمل", "Preliminary - incomplete cost scope")
    if not bool(truth.get("closure_passed")):
        return "danger", _label(language, "التدفقات لا تقفل بصورة سليمة", "Cash flows do not close correctly")
    if not bool(truth.get("economic_feasible")):
        return "danger", _label(language, "يحتاج المشروع إلى إعادة هيكلة", "Project restructuring is required")
    if not bool(truth.get("policy_compliant")):
        return "warning", _label(language, "المشروع مجدٍ لكن الشروط تحتاج تعديلاً", "Project is viable but terms need revision")
    return "success", _label(language, "قابل للاستثمار ضمن الشروط الحالية", "Investable under current terms")


_CONSTRAINT_LABELS = {
    "MIN_PROFIT_ON_COST": ("الحد الأدنى لربح المطور على الكلفة", "Minimum developer profit on cost"),
    "MIN_DEVELOPER_IRR": ("الحد الأدنى لعائد حقوق ملكية المطور", "Minimum developer equity IRR"),
    "MIN_DEVELOPER_EQUITY_IRR": ("الحد الأدنى لعائد حقوق ملكية المطور", "Minimum developer equity IRR"),
    "MIN_DEVELOPER_NPV": ("الحد الأدنى للقيمة الحالية لحقوق المطور", "Minimum developer equity NPV"),
    "MIN_DEVELOPER_MULTIPLE": ("الحد الأدنى لمضاعف حقوق ملكية المطور", "Minimum developer equity multiple"),
    "MAX_FUNDING_GAP": ("الحد الأقصى لفجوة التمويل", "Maximum funding gap"),
    "MAX_RESIDUAL_FUNDING_GAP": ("الحد الأقصى لفجوة التمويل المتبقية", "Maximum residual funding gap"),
    "COST_SCOPE_COMPLETE": ("اكتمال نطاق الكلف", "Cost-scope completeness"),
    "COMPLETE_SCOPE": ("اكتمال نطاق المشروع وتنفيذه", "Complete modeled and executed scope"),
    "MANDATORY_PAYMENT_SHORTFALL": ("عجز الدفعات الإلزامية", "Mandatory payment shortfall"),
    "TERMINAL_DEBT": ("رصيد الدين النهائي", "Terminal debt balance"),
    "PROFIT_SHARE_CONVERGENCE": ("تقارب حساب حصة الأرباح", "Profit-share calculation convergence"),
    "MONTHLY_CASH_RECONCILIATION": ("مصالحة النقد الشهرية", "Monthly cash reconciliation"),
}


def _constraint_label(row: dict[str, Any], language: str) -> str:
    key = str(row.get("constraint_id") or "").upper()
    pair = _CONSTRAINT_LABELS.get(key)
    if pair:
        return pair[0 if language == "ar" else 1]
    explicit = row.get("label_ar" if language == "ar" else "label_en") or row.get("label")
    return str(explicit or key.replace("_", " ").title() or "-")


def _constraint_value(row: dict[str, Any], field: str, currency: str) -> str:
    value = row.get(field)
    if value in (None, ""):
        return "-"
    key = str(row.get("constraint_id") or "").upper()
    if any(token in key for token in ("IRR", "PROFIT_ON_COST", "MARGIN", "SHARE")):
        return _pct(value)
    if any(token in key for token in ("MULTIPLE", "MOIC")):
        return _multiple(value)
    if any(token in key for token in ("NPV", "FUNDING", "PAYMENT", "DEBT", "CASH", "SCOPE")):
        return _money(value, currency)
    return str(value)


def _constraint_reason(row: dict[str, Any], language: str) -> str:
    key = str(row.get("constraint_id") or "").upper()
    explicit = row.get("reason_ar" if language == "ar" else "reason_en")
    if explicit:
        return str(explicit)
    if language == "ar":
        known = {
            "MIN_PROFIT_ON_COST": "يجب ألا يقل ربح المطور على الكلفة عن الحد المعتمد في السياسة.",
            "MIN_DEVELOPER_IRR": "يجب ألا يقل العائد الداخلي على حقوق المطور عن الحد المعتمد في السياسة.",
            "MIN_DEVELOPER_EQUITY_IRR": "يجب ألا يقل العائد الداخلي على حقوق المطور عن الحد المعتمد في السياسة.",
            "MIN_DEVELOPER_NPV": "يجب ألا تكون القيمة الحالية لحقوق المطور أدنى من الحد المعتمد.",
            "MIN_DEVELOPER_MULTIPLE": "يجب ألا يقل مضاعف حقوق ملكية المطور عن الحد المعتمد.",
            "MAX_FUNDING_GAP": "يجب ألا تتجاوز فجوة التمويل الحد المسموح به.",
            "MAX_RESIDUAL_FUNDING_GAP": "يجب إقفال فجوة التمويل المتبقية ضمن الحد المسموح.",
            "COST_SCOPE_COMPLETE": "يجب تمثيل جميع الكلف المطلوبة قبل الاعتماد على النتيجة.",
            "COMPLETE_SCOPE": "يجب تنفيذ كامل النطاق المخطط وإقفال الكلف أو الالتزامات المؤجلة.",
            "MANDATORY_PAYMENT_SHORTFALL": "لا يجوز بقاء عجز في الدفعات النظامية أو التمويلية أو التعاقدية الإلزامية.",
            "TERMINAL_DEBT": "يجب سداد جميع أرصدة الدين المعترف بها قبل الإقفال المالي.",
            "PROFIT_SHARE_CONVERGENCE": "يجب أن يتقارب حساب حصة الأرباح ضمن حد الدقة العددية المعتمد.",
            "MONTHLY_CASH_RECONCILIATION": "يجب أن تتصالح مصادر واستخدامات النقد والرصيد الختامي في كل شهر.",
        }
        if key in known:
            return known[key]
    explicit = row.get("explanation") or row.get("reason")
    if explicit:
        return str(explicit)
    return _label(language, "لم يسجل تفسير تفصيلي لهذا القيد.", "No detailed explanation was recorded for this constraint.")


def _cost_category_label(value: Any, language: str) -> str:
    key = str(value or "").upper()
    labels = {
        "PRODUCT_CONSTRUCTION": ("إنشاء المنتجات", "Product construction"),
        "DIRECT_CONSTRUCTION": ("الإنشاء المباشر", "Direct construction"),
        "INFRASTRUCTURE": ("البنية التحتية", "Infrastructure"),
        "PUBLIC_FACILITIES": ("المرافق العامة", "Public facilities"),
        "PROFESSIONAL_FEES": ("التصميم والاستشارات", "Design and professional fees"),
        "AUTHORITIES": ("التراخيص والرسوم", "Permits and statutory fees"),
        "PROJECT_MANAGEMENT": ("إدارة المشروع", "Project management"),
        "SALES_MARKETING": ("التسويق والمبيعات", "Sales and marketing"),
    }
    pair = labels.get(key)
    return pair[0 if language == "ar" else 1] if pair else (key.replace("_", " ").title() or "-")


def _cost_item_label(row: dict[str, Any], language: str) -> str:
    cost_id = str(row.get("cost_id") or "").upper()
    labels = {
        "INTERNAL_INFRASTRUCTURE": ("الطرق والبنية الداخلية", "Internal roads and infrastructure"),
        "EXTERNAL_CONNECTIONS": ("الربط الخارجي للمرافق", "External utility connections"),
        "PUBLIC_FACILITIES": ("المرافق والمباني العامة", "Public-service buildings and facilities"),
        "DESIGN_STUDIES": ("التصميم والدراسات", "Design, engineering and studies"),
        "PERMITS_FEES": ("التراخيص والرسوم", "Permits and statutory fees"),
        "PROJECT_MANAGEMENT": ("إدارة المشروع والإشراف", "Project management and supervision"),
        "MARKETING_SALES": ("التسويق والمبيعات", "Marketing and sales"),
    }
    pair = labels.get(cost_id)
    if pair:
        return pair[0 if language == "ar" else 1]
    return str(row.get("name") or cost_id or "-")


def _basis_label(value: Any, language: str) -> str:
    raw = str(value or "")
    key = raw.upper()
    labels = {
        "FIXED_AMOUNT": ("مبلغ مقطوع", "Fixed amount"),
        "LAND_USE_AREA_SQM:ROADS": ("مساحة أرض الطرق والحركة", "Roads and circulation land area"),
        "LAND_USE_AREA_SQM:PUBLIC": ("مساحة أرض المرافق العامة", "Public-facilities land area"),
        "HARD_COST:ALL:BASE_COST": ("إجمالي الكلف المباشرة", "Total base hard costs"),
    }
    pair = labels.get(key)
    if pair:
        return pair[0 if language == "ar" else 1]
    if key.startswith("PRODUCT_GFA_SQM:"):
        return _label(language, "المساحة المبنية للمنتج", "Product GFA")
    if key.startswith("PRODUCT_SELLABLE_AREA_SQM:"):
        return _label(language, "المساحة البيعية للمنتج", "Product sellable area")
    return raw or "-"


def _table_row(cells: list[str]) -> str:
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _empty_row(message: str, columns: int) -> str:
    return f'<tr><td colspan="{columns}">{escape(message)}</td></tr>'


def render_developer_report_html(run: Any, *, language: str = "en", report_type: str = "executive") -> str:
    language = "ar" if str(language).lower() == "ar" else "en"
    report_type = "technical" if str(report_type).lower() == "technical" else "executive"
    rtl = language == "ar"
    output = run.output_snapshot or {}
    truth = output.get("financial_truth") or (output.get("unified_financial_result") or {}).get("financial_truth") or {}
    advisory = output.get("developer_advisory") or {}
    strategy = advisory.get("negotiation_strategy") or {}
    capital_policy = advisory.get("capital_policy") or {}
    procurement_policy = advisory.get("procurement_policy") or {}
    context = output.get("run_context") or {}
    scope = ((output.get("cost_calculation") or {}).get("scope_coverage") or {})
    currency = str(output.get("reporting_currency") or "USD")
    project_name = str(output.get("project_name") or output.get("case_name") or run.case_id or "LandValue360 Project")
    tone, status = _status(output, language)
    # Use the canonical status headline so reports remain stable even when advisory copy varies.
    headline = status
    title = _label(language, "التقرير التنفيذي للمطور", "Developer Executive Investment Report") if report_type == "executive" else _label(language, "التقرير الفني والمالي للمطور", "Developer Technical & Financial Report")

    # Canonical metrics: no report-side cash-flow recalculation.
    project_metrics = "".join([
        _metric(_label(language, "إجمالي المبيعات المحتملة", "Gross potential sales"), _money(truth.get("gross_potential_revenue"), currency)),
        _metric(_label(language, "صافي المبيعات", "Net sales"), _money(truth.get("net_sales"), currency)),
        _metric(_label(language, "إجمالي كلفة التطوير", "Total development cost"), _money(truth.get("planned_total_cost"), currency)),
        _metric(_label(language, "ربح المشروع قبل مقابل الأرض والتمويل", "Project profit before land and financing"), _money(truth.get("project_profit"), currency)),
        _metric(_label(language, "ربح المشروع على الكلفة", "Project profit on cost"), _pct(truth.get("project_profit_on_cost"))),
        _metric(_label(language, "القيمة الحالية للمشروع", "Project NPV"), _money(truth.get("project_npv"), currency)),
    ])
    developer_metrics = "".join([
        _metric(_label(language, "إجمالي حقوق الملكية المضخوخة", "Total equity contributed"), _money(truth.get("developer_equity_contributions"), currency)),
        _metric(_label(language, "إجمالي توزيعات المطور", "Developer nominal distributions"), _money(truth.get("developer_equity_distributions"), currency)),
        _metric(_label(language, "صافي ربح المطور بعد استرداد رأس المال", "Developer nominal profit after return of equity"), _money(truth.get("developer_equity_nominal_profit"), currency)),
        _metric(_label(language, "القيمة الحالية لحقوق المطور", "Developer equity NPV"), _money(truth.get("developer_equity_npv"), currency)),
        _metric(_label(language, "العائد الداخلي على حقوق المطور", "Developer equity IRR"), _pct(truth.get("developer_equity_irr"))),
        _metric(_label(language, "مضاعف حقوق الملكية", "Equity multiple"), _multiple(truth.get("developer_equity_multiple"))),
        _metric(_label(language, "هامش صافي ربح المطور", "Developer net margin"), _pct(truth.get("developer_net_margin"))),
        _metric(_label(language, "فجوة التمويل", "Funding gap"), _money(truth.get("funding_gap"), currency)),
    ])
    capital_metrics = "".join([
        _metric(_label(language, "ذروة حقوق الملكية", "Peak equity"), _money(truth.get("peak_equity"), currency)),
        _metric(_label(language, "ذروة الدين", "Peak debt"), _money(truth.get("peak_debt"), currency)),
        _metric(_label(language, "الفوائد الإجمالية", "Total interest"), _money(truth.get("interest_total"), currency)),
        _metric(_label(language, "رسوم التمويل", "Financing fees"), _money(truth.get("financing_fees_total"), currency)),
        _metric(_label(language, "الحد الأدنى للنقد", "Minimum cash balance"), _money(truth.get("minimum_cash_balance"), currency)),
        _metric(_label(language, "الدين الختامي", "Terminal debt"), _money(truth.get("terminal_debt"), currency)),
    ])
    method = advisory.get("current_contract") or {}
    method_label = method.get("label") or {}
    method_label = method_label.get("ar" if rtl else "en") or method.get("method") or "-"
    offer_metrics = "".join([
        _metric(_label(language, "طريقة المشاركة الحالية", "Current partnership method"), str(method_label)),
        _metric(_label(language, "العرض الحالي", "Current offer"), _offer(method.get("measure"), strategy.get("measure_type") or "RATE", currency)),
        _metric(_label(language, "العرض الافتتاحي المقترح", "Suggested opening offer"), _offer(strategy.get("opening_offer"), strategy.get("measure_type") or "RATE", currency)),
        _metric(_label(language, "العرض التنافسي", "Competitive offer"), _offer(strategy.get("competitive_offer"), strategy.get("measure_type") or "RATE", currency)),
        _metric(_label(language, "الحد الأقصى الذي لا ينصح بتجاوزه", "Maximum recommended ceiling"), _offer(strategy.get("maximum_tolerable"), strategy.get("measure_type") or "RATE", currency)),
        _metric(_label(language, "مقابل مالك الأرض - القيمة الحالية", "Land consideration NPV"), _money(truth.get("government_consideration_npv"), currency)),
    ])

    constraints = list(truth.get("constraints") or [])
    failed = [row for row in constraints if not bool(row.get("passed"))]
    constraint_rows = "".join(
        _table_row([
            escape(_constraint_label(row, language)),
            escape(_label(language, "ناجح", "Pass") if row.get("passed") else _label(language, "فاشل", "Fail")),
            escape(_constraint_value(row, "actual", currency)),
            escape(_constraint_value(row, "threshold", currency) if row.get("threshold") not in (None, "") else _constraint_value(row, "required", currency)),
            escape(_constraint_reason(row, language)),
        ]) for row in constraints
    ) or _empty_row(_label(language, "لا توجد قيود مسجلة.", "No constraints recorded."), 5)
    failed_rows = "".join(
        _table_row([
            escape(_constraint_label(row, language)),
            escape(_constraint_value(row, "actual", currency)),
            escape(_constraint_value(row, "threshold", currency) if row.get("threshold") not in (None, "") else _constraint_value(row, "required", currency)),
            escape(_constraint_reason(row, language)),
        ]) for row in failed
    )

    cost_items = list((output.get("cost_calculation") or {}).get("items") or [])
    cost_rows = "".join(
        _table_row([
            escape(_cost_item_label(row, language)),
            escape(_cost_category_label(row.get("category"), language)),
            escape(_basis_label(row.get("basis_label"), language)),
            escape(_money(row.get("resolved_base_plus_contingency") or row.get("resolved_amount"), currency)),
        ]) for row in cost_items
    ) or _empty_row(_label(language, "لا توجد بنود كلفة قابلة للعرض.", "No cost items available."), 4)

    opening_discount = _d(procurement_policy.get("opening_discount_rate"), "0.08")
    target_discount = _d(procurement_policy.get("target_discount_rate"), "0.04")
    retained_contingency = _d(procurement_policy.get("minimum_retained_contingency_rate"), "0.03")
    procurement_rows = ""
    for row in sorted(cost_items, key=lambda item: _d(item.get("resolved_base_plus_contingency") or item.get("resolved_amount")), reverse=True)[:30]:
        budget = _d(row.get("resolved_base_plus_contingency") or row.get("resolved_amount"))
        if budget <= 0:
            continue
        procurement_rows += _table_row([
            escape(_cost_item_label(row, language)),
            escape(_money(budget, currency)),
            escape(_money(budget * (Decimal("1") - opening_discount), currency)),
            escape(_money(budget * (Decimal("1") - target_discount), currency)),
            escape(_money(budget * retained_contingency, currency)),
        ])
    if not procurement_rows:
        procurement_rows = _empty_row(_label(language, "لا توجد حزم كلفة قابلة للعرض.", "No cost packages available."), 5)

    monthly = list((output.get("unified_financial_result") or {}).get("monthly_cashflow") or [])
    cash_rows = "".join(
        _table_row([
            escape(str(row.get("date") or "")),
            escape(_money(row.get("sales_collections"), currency)),
            escape(_money(row.get("equity_contribution"), currency)),
            escape(_money(row.get("debt_draw"), currency)),
            escape(_money(row.get("actual_cost"), currency)),
            escape(_money(row.get("government_payment"), currency)),
            escape(_money(row.get("developer_distribution"), currency)),
            escape(_money(row.get("ending_cash"), currency)),
        ]) for row in monthly
    )

    scope_note = ""
    if str(scope.get("status") or "").upper() == "INCOMPLETE":
        missing = ", ".join(scope.get("missing_required_scope_ids") or [])
        scope_note = (
            f'<div class="callout callout--warning"><strong>{escape(_label(language, "تحذير نطاق الكلف", "Cost scope warning"))}</strong>'
            f'<p>{escape(missing or _label(language, "بنود كلفة أساسية مفقودة.", "Required cost categories are missing."))}</p></div>'
        )
    strategy_note = str(strategy.get("explanation_ar" if rtl else "explanation_en") or _label(language, "لم يتوفر نطاق تفاوضي متحقق.", "No verified negotiation range was available."))

    recommendations = advisory.get("recommended_actions") or []
    recommendation_rows = "".join(
        f"<li>{escape(str(item.get('ar' if rtl else 'en') if isinstance(item, dict) else item))}</li>"
        for item in recommendations
    )
    if not recommendation_rows:
        recommendation_rows = f"<li>{escape(_label(language, 'راجع العرض الحالي مقابل العرض التنافسي والحد الأقصى، ثم اختبر السيناريو المتحفظ قبل الالتزام.', 'Compare the current offer with the competitive and maximum levels, then test the downside scenario before commitment.'))}</li>"

    executive_actions = (
        f'<section class="page"><h2>{escape(_label(language, "الرأي والخطوات التالية", "Conclusion and next steps"))}</h2>'
        f'<div class="decision-box decision-box--{tone}"><strong>{escape(headline)}</strong><p>{escape(strategy_note)}</p></div>'
        f'<h3>{escape(_label(language, "الإجراءات الموصى بها", "Recommended actions"))}</h3><ol class="actions">{recommendation_rows}</ol>'
        + (
            f'<h3>{escape(_label(language, "القيود المطلوب معالجتها", "Constraints requiring action"))}</h3>'
            f'<table><thead><tr><th>{escape(_label(language,"القيد","Constraint"))}</th><th>{escape(_label(language,"الفعلي","Actual"))}</th><th>{escape(_label(language,"المطلوب","Required"))}</th><th>{escape(_label(language,"السبب","Reason"))}</th></tr></thead><tbody>{failed_rows}</tbody></table>'
            if failed else
            f'<div class="callout callout--success">{escape(_label(language, "لم تسجل قيود فاشلة في تشغيل الحساب المحدد.", "No failed constraints were recorded for the selected run."))}</div>'
        )
        + f'<p class="disclaimer">{escape(_label(language, "هذه أداة تحليل استشارية لدعم القرار وليست تقييماً معتمداً أو رأياً قانونياً. يجب التحقق المستقل من المدخلات قبل الالتزام.", "This is an advisory decision-support tool, not a certified valuation or legal opinion. Inputs require independent verification before commitment."))}</p></section>'
    )

    technical_sections = ""
    if report_type == "technical":
        technical_sections = f'''
        <section class="page"><h2>{escape(_label(language,"استراتيجية التعاقد والشراء","Procurement and contracting strategy"))}</h2>
          <div class="callout"><p>{escape(_label(language,"القيم التالية أهداف تفاوضية من سياسة المشروع. لا تغير الموازنة المعتمدة حتى تسجيل عرض أو عقد فعلي.","The following are policy-based negotiation targets. They do not change the approved budget until an actual quote or contract is recorded."))}</p></div>
          <table><thead><tr><th>{escape(_label(language,"حزمة الكلفة","Cost package"))}</th><th>{escape(_label(language,"الموازنة","Budget"))}</th><th>{escape(_label(language,"هدف افتتاحي","Opening target"))}</th><th>{escape(_label(language,"سقف ترسية","Award ceiling"))}</th><th>{escape(_label(language,"احتياطي محتفظ به","Retained contingency"))}</th></tr></thead><tbody>{procurement_rows}</tbody></table>
        </section>
        <section class="page"><h2>{escape(_label(language,"مصالحة الكلف","Cost reconciliation"))}</h2>{scope_note}
          <table><thead><tr><th>{escape(_label(language,"البند","Item"))}</th><th>{escape(_label(language,"الفئة","Category"))}</th><th>{escape(_label(language,"أساس الكمية","Quantity basis"))}</th><th>{escape(_label(language,"القيمة قبل التصعيد","Pre-escalation amount"))}</th></tr></thead><tbody>{cost_rows}</tbody></table>
        </section>
        <section class="page"><h2>{escape(_label(language,"القيود والسياسات","Constraints and policies"))}</h2>
          <table><thead><tr><th>{escape(_label(language,"القيد","Constraint"))}</th><th>{escape(_label(language,"الحالة","Status"))}</th><th>{escape(_label(language,"الفعلي","Actual"))}</th><th>{escape(_label(language,"المطلوب","Required"))}</th><th>{escape(_label(language,"السبب","Reason"))}</th></tr></thead><tbody>{constraint_rows}</tbody></table>
        </section>
        <section class="page page--wide"><h2>{escape(_label(language,"التدفق النقدي الشهري","Monthly cash flow"))}</h2>
          <table class="compact-table"><thead><tr><th>{escape(_label(language,"التاريخ","Date"))}</th><th>{escape(_label(language,"التحصيل","Collections"))}</th><th>{escape(_label(language,"الإيكويتي","Equity"))}</th><th>{escape(_label(language,"سحب الدين","Debt draw"))}</th><th>{escape(_label(language,"الكلف","Costs"))}</th><th>{escape(_label(language,"مقابل الأرض","Land consideration"))}</th><th>{escape(_label(language,"توزيعات المطور","Developer distributions"))}</th><th>{escape(_label(language,"النقد الختامي","Closing cash"))}</th></tr></thead><tbody>{cash_rows}</tbody></table>
        </section>
        <section class="page"><h2>{escape(_label(language,"مرجع التشغيل والخلاصة","Run reference and conclusion"))}</h2>
          <dl class="trace"><div><dt>Run ID</dt><dd>{escape(str(run.id))}</dd></div><div><dt>Input hash</dt><dd>{escape(str(context.get('project_version_input_hash') or run.input_hash or '-'))}</dd></div><div><dt>Output hash</dt><dd>{escape(str(run.output_hash or '-'))}</dd></div><div><dt>{escape(_label(language,'إصدار التطبيق','Application version'))}</dt><dd>{escape(str(run.application_version or '-'))}</dd></div><div><dt>{escape(_label(language,'إصدار التقرير','Report version'))}</dt><dd>{DEVELOPER_REPORT_VERSION}</dd></div></dl>
          <div class="decision-box decision-box--{tone}"><strong>{escape(headline)}</strong><p>{escape(strategy_note)}</p></div>
          <p class="disclaimer">{escape(_label(language,"هذه أداة تحليل استشارية لدعم القرار وليست تقييماً معتمداً أو رأياً قانونياً. يجب التحقق المستقل من المدخلات قبل الالتزام.","This is an advisory decision-support tool, not a certified valuation or legal opinion. Inputs require independent verification before commitment."))}</p>
        </section>
        '''

    page_size = "A4 landscape" if report_type == "technical" else "A4"
    metrics_columns = 3 if report_type == "technical" else 2
    html = f'''<!doctype html>
<html lang="{language}" dir="{'rtl' if rtl else 'ltr'}"><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
@page{{size:{page_size};margin:18mm 15mm 18mm;@bottom-center{{content:counter(page) " / " counter(pages);direction:ltr;unicode-bidi:isolate;font-size:8pt;color:#647a77}}}}
*{{box-sizing:border-box}}html,body{{margin:0;padding:0}}body{{font-family:"Noto Sans Arabic","DejaVu Sans",Arial,sans-serif;color:#112d34;font-size:10pt;line-height:1.55}}
.cover{{min-height:245mm;display:flex;flex-direction:column;justify-content:center;border-top:8px solid #174f49;padding:0 8mm}}
.cover .eyebrow{{color:#a97700;font-weight:800;letter-spacing:.12em}}h1{{font-size:28pt;margin:2mm 0}}h2{{font-size:18pt;margin:0 0 8mm;padding-bottom:3mm;border-bottom:2px solid #174f49}}h3{{font-size:12.5pt;margin:8mm 0 3mm}}
.subtitle{{font-size:13pt;color:#5d7471;max-width:170mm}}.status{{display:inline-block;margin-top:6mm;padding:2.5mm 5mm;border-radius:99px;font-weight:800}}.status--success{{background:#dff3e7;color:#17603b}}.status--warning{{background:#fff0c5;color:#765000}}.status--danger{{background:#ffe0dc;color:#942a24}}
.page{{break-before:page;padding-top:2mm}}.page--wide{{font-size:8.5pt}}.metrics{{display:grid;grid-template-columns:repeat({metrics_columns},minmax(0,1fr));gap:6mm;margin:6mm 0 10mm}}.metric{{border:1px solid #cbdad7;border-radius:9px;background:#f5f9f8;padding:5mm;min-height:34mm;break-inside:avoid}}.metric span,.metric small{{display:block;color:#617673}}.metric strong{{display:block;font-size:16pt;margin:2mm 0;color:#102e35}}.metric--warning{{background:#fff8e5}}.metric--danger{{background:#fff0ed}}
.callout,.decision-box{{margin:6mm 0;padding:5mm;border-inline-start:5px solid #174f49;background:#eef6f4;border-radius:7px;break-inside:avoid}}.callout--warning{{border-color:#b07b00;background:#fff7df}}.callout--success{{border-color:#2c8b61;background:#edf8f2}}.decision-box--warning{{border-color:#b07b00;background:#fff7df}}.decision-box--danger{{border-color:#a13a2f;background:#fff0ed}}.decision-box--success{{border-color:#2c8b61;background:#edf8f2}}.decision-box strong{{font-size:15pt}}
table{{width:100%;border-collapse:collapse;table-layout:fixed;margin:5mm 0 8mm;font-size:8.6pt}}thead{{display:table-header-group}}th{{background:#174f49;color:#fff;text-align:start;font-weight:700}}th,td{{border:1px solid #c9d7d5;padding:2.5mm;vertical-align:middle;overflow-wrap:anywhere}}tr{{break-inside:avoid}}.compact-table{{font-size:7.4pt}}.compact-table th,.compact-table td{{padding:1.8mm}}.actions{{padding-inline-start:7mm}}.actions li{{margin:2.5mm 0}}.trace{{display:grid;gap:0}}.trace div{{display:grid;grid-template-columns:28% 1fr;border-bottom:1px solid #d4dfdd;padding:2.5mm 0}}.trace dt{{font-weight:700}}.trace dd{{margin:0;overflow-wrap:anywhere}}.disclaimer{{font-size:8.5pt;color:#647a77;margin-top:10mm;border-top:1px solid #d4dfdd;padding-top:4mm}}
@media print{{a{{color:inherit;text-decoration:none}}}}
</style></head><body>
<section class="cover"><p class="eyebrow">LANDVALUE360 DEVELOPER ADVISORY</p><h1>{escape(project_name)}</h1><h2>{escape(title)}</h2><p><span class="status status--{tone}">{escape(status)}</span></p><p>{escape(_label(language,"جميع المؤشرات مأخوذة من عملية حساب شهرية واحدة ومصدر مالي موحد.","All indicators are sourced from one monthly calculation run and one financial truth."))}</p></section>
<section class="page"><h2>{escape(_label(language,"اقتصاديات المشروع","Project economics"))}</h2>{scope_note}<div class="metrics">{project_metrics}</div></section>
<section class="page"><h2>{escape(_label(language,"عوائد المطور وحقوق الملكية","Developer returns and equity"))}</h2><div class="metrics">{developer_metrics}</div></section>
<section class="page"><h2>{escape(_label(language,"هيكل رأس المال والرافعة المالية","Capital structure and leverage"))}</h2><div class="metrics">{capital_metrics}</div><div class="callout"><p>{escape(_label(language,"تعرض مؤشرات الرافعة من التدفقات الفعلية. لا يعرض النظام DOL أو DFL عندما لا تكون الكلف مصنفة بصورة موثوقة إلى ثابتة ومتغيرة أو لا يتوفر سجل خدمة دين كافٍ.","Leverage indicators are sourced from actual cash flows. DOL/DFL are not shown without reliable fixed-variable cost classification and sufficient debt-service history."))}</p></div></section>
<section class="page"><h2>{escape(_label(language,"استراتيجية العرض والمشاركة","Offer and partnership strategy"))}</h2><div class="metrics">{offer_metrics}</div><div class="callout"><p>{escape(strategy_note)}</p></div></section>
{technical_sections if report_type == 'technical' else executive_actions}
</body></html>'''
    return html


def render_developer_report_pdf(run: Any, *, language: str = "en", report_type: str = "executive") -> bytes:
    return render_html_to_pdf(render_developer_report_html(run, language=language, report_type=report_type))
