"""Independent bilingual advisory reports for the LandValue360 Landowner interface.

The report layer never recalculates financial metrics. It presents only the
locked advisory output and discloses unavailable datasets rather than
fabricating them. Reports are not official valuations or government decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from typing import Any, Callable

from .manifest import (
    FORMULA_REGISTRY_VERSION,
    GOVERNMENT_VERSION,
    REPORT_REGISTRY_VERSION,
)
from .registries import SCENARIO_DEFINITIONS, STATUS_LABELS
from .negotiation import CONSTRAINT_LABELS

ZERO = Decimal("0")


@dataclass(frozen=True)
class ReportDefinition:
    title_ar: str
    title_en: str
    purpose_ar: str
    purpose_en: str
    required_sections: tuple[str, ...]


REPORT_CATALOG: dict[str, ReportDefinition] = {
    "executive-decision-memorandum": ReportDefinition(
        "التقرير التنفيذي",
        "Executive Advisory Report",
        "تقرير مختصر مخصص للإدارة العليا يركز على قيمة الأرض والعرض والنطاق التفاوضي وعائد المطور والمخاطر والتوصية.",
        "A concise senior-management report focused on land value, offer position, negotiation range, developer return, key risks and recommendation.",
        ("decision", "range", "resilience", "risks", "conclusion"),
    ),
    "technical-financial-report": ReportDefinition(
        "التقرير الفني والمالي التفصيلي",
        "Detailed Technical and Financial Report",
        "تقرير تفصيلي يضم الافتراضات والتقييم والكلف والتدفقات الشهرية والعقود والقيود والسيناريوهات والمصالحات.",
        "A detailed report covering assumptions, valuation, costs, monthly cash flows, contracts, constraints, scenarios and reconciliations.",
        (),
    ),
}
REPORT_TYPES = {key: (value.title_ar, value.title_en) for key, value in REPORT_CATALOG.items()}
REPORT_PURPOSES = {key: (value.purpose_ar, value.purpose_en) for key, value in REPORT_CATALOG.items()}

LANDSCAPE_REPORT_TYPES: set[str] = {"technical-financial-report"}


def _e(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return escape(str(value))


def _d(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _num(value: Any, places: int = 2) -> str:
    number = _d(value)
    if number is None:
        return "—"
    quantum = Decimal(1).scaleb(-places)
    return f"{number.quantize(quantum):,.{places}f}"


def _compact_value(value: Any, places: int = 4) -> str:
    """Format a scalar without raw Decimal tails or forced trailing zeros."""

    number = _d(value)
    if number is None:
        return "—" if value in (None, "") else str(value)
    quantum = Decimal(1).scaleb(-places)
    rounded = number.quantize(quantum)
    if rounded == rounded.to_integral_value():
        return f"{rounded:,.0f}"
    return f"{rounded:,.{places}f}".rstrip("0").rstrip(".")


def _money(value: Any, currency: str = "USD") -> str:
    number = _d(value)
    if number is None:
        return "—"
    return f"{currency} {number.quantize(Decimal('0.01')):,.2f}"



def _display_reconciliation_value(value: Any, currency: str = "USD", *, ar: bool = False) -> str:
    """Render audit values without leaking raw Decimal precision into reports."""
    if value in (None, ""):
        return "—"
    if isinstance(value, bool):
        return ("نعم" if value else "لا") if ar else ("Yes" if value else "No")
    text = str(value).strip()
    if text.upper() in {"PASS", "FAIL"}:
        return text.upper()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    # Reconciliation checks are monetary unless the value is an exact small integer/percentage-like scalar.
    if number == number.to_integral_value() and abs(number) <= Decimal("100"):
        return f"{number.quantize(Decimal('1')):,}"
    return _money(number, currency)

def _pct(value: Any, *, ratio: bool = True) -> str:
    number = _d(value)
    if number is None:
        return "—"
    if ratio:
        number *= 100
    return f"{number.quantize(Decimal('0.01')):,.2f}%"


def _multiple(value: Any) -> str:
    number = _d(value)
    return "—" if number is None else f"{number.quantize(Decimal('0.01')):,.2f}x"


def _lang(ar: bool, ar_text: str, en_text: str) -> str:
    return ar_text if ar else en_text


def _rows(rows: list[tuple[Any, Any]], *, ar: bool) -> str:
    return "".join(f"<tr><th>{_e(a)}</th><td>{_e(b)}</td></tr>" for a, b in rows)


def _table(headers: list[str], rows: list[list[Any]], *, compact: bool = False) -> str:
    klass = "table compact" if compact else "table"
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(item)}</td>" for item in row) + "</tr>" for row in rows)
    if not rows:
        body = f'<tr><td colspan="{max(len(headers), 1)}" class="empty">—</td></tr>'
    return f'<div class="table-wrap"><table class="{klass}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _cards(items: list[tuple[str, str, str | None]]) -> str:
    return '<div class="metric-grid">' + "".join(
        f'<div class="metric-card"><span>{_e(label)}</span><strong>{_e(value)}</strong>{f"<small>{_e(note)}</small>" if note else ""}</div>'
        for label, value, note in items
    ) + "</div>"


def _section(identifier: str, title: str, body: str, eyebrow: str = "") -> dict[str, str]:
    return {"id": identifier, "title": title, "eyebrow": eyebrow, "html": body}


def _bullet(items: list[str]) -> str:
    clean = [item for item in items if item]
    return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in clean) + "</ul>" if clean else '<p class="empty">—</p>'


def _status_text(status: Any, *, ar: bool) -> str:
    raw = str(status or "UNSPECIFIED")
    labels = STATUS_LABELS.get(raw) or {}
    return str(labels.get("ar" if ar else "en") or raw.replace("_", " ").title())


def _status_badge(status: Any, *, ar: bool = False) -> str:
    raw = str(status or "UNSPECIFIED")
    slug = raw.lower().replace("_", "-")
    return f'<span class="status-badge status-{_e(slug)}">{_e(_status_text(raw, ar=ar))}</span>'


OFFER_POSITION_LABELS = {
    "BELOW_MINIMUM": {"ar": "أقل من الحد الأدنى", "en": "Below minimum"},
    "WITHIN_NEGOTIATION_RANGE": {"ar": "ضمن نطاق التفاوض", "en": "Within negotiation range"},
    "WITHIN_RECOMMENDED_RANGE": {"ar": "ضمن النطاق الموصى به", "en": "Within recommended range"},
    "NEAR_BALANCED_RECOMMENDATION": {"ar": "قريب من التوصية المتوازنة", "en": "Near balanced recommendation"},
    "ABOVE_RISK_ADJUSTED_CEILING": {"ar": "أعلى من السقف المتحفظ وفق السياسة", "en": "Above policy-adjusted ceiling"},
    "ABOVE_TECHNICAL_CEILING": {"ar": "أعلى من السقف الفني", "en": "Above technical ceiling"},
    "AT_OR_BELOW_MINIMUM": {"ar": "عند الحد الأدنى أو دونه", "en": "At or below minimum"},
    "WITHIN_RANGE": {"ar": "ضمن النطاق", "en": "Within range"},
    "ABOVE_RANGE": {"ar": "أعلى من النطاق", "en": "Above range"},
}
PARTY_LABELS = {
    "DEVELOPER": {"ar": "المطور", "en": "Developer"},
    "PUBLIC_AUTHORITY": {"ar": "مالك الأرض", "en": "Landowner"},
    "GOVERNMENT": {"ar": "مالك الأرض", "en": "Landowner"},
    "LANDOWNER": {"ar": "مالك الأرض", "en": "Landowner"},
    "SHARED": {"ar": "مشترك", "en": "Shared"},
    "THIRD_PARTY": {"ar": "طرف ثالث", "en": "Third party"},
}
RISK_LEVEL_LABELS = {
    "LOW": {"ar": "منخفض", "en": "Low"},
    "MEDIUM": {"ar": "متوسط", "en": "Medium"},
    "HIGH": {"ar": "مرتفع", "en": "High"},
    "CRITICAL": {"ar": "حرج", "en": "Critical"},
}
RISK_TEXT_AR = {
    "Construction cost escalation": "تصاعد كلفة الإنشاء",
    "Sales-price and absorption underperformance": "انخفاض أسعار البيع أو بطء الاستيعاب",
    "Financing availability or interest-rate stress": "مخاطر توفر التمويل أو ارتفاع الفائدة",
    "Planning or permitting delay": "تأخر التنظيم أو التراخيص",
    "Unverified market or cost assumptions": "افتراضات سوقية أو كلف غير متحققة",
    "BOQ maturity, procurement strategy and controlled contingencies.": "استكمال جداول الكميات، واستراتيجية الشراء، واحتياطيات منضبطة.",
    "Independent market study, phased release and price monitoring.": "دراسة سوق مستقلة، وطرح مرحلي، ومراقبة الأسعار.",
    "Committed facilities, financing covenants and interest reserve.": "تسهيلات تمويل ملتزمة، وتعهدات تمويلية، واحتياطي للفائدة.",
    "Committed facilities, funding covenant and interest reserve.": "تسهيلات تمويل ملتزمة، وتعهدات تمويلية، واحتياطي للفائدة.",
    "Early authority engagement and milestone-linked delivery.": "تنسيق مبكر مع الجهات وربط التنفيذ بمعالم واضحة.",
    "Evidence room, assumption approval and independent model review.": "غرفة بيانات، واعتماد الافتراضات، ومراجعة مستقلة للنموذج.",
}
CONDITION_TEXT_AR = {
    "Verify title, encumbrances and legal authority before contracting.": "التحقق من الملكية والقيود والصلاحية القانونية قبل التعاقد.",
    "Approve the reference land-value basis and selected policy version.": "اعتماد أساس قيمة الأرض المرجعية وإصدار السياسة المختارة.",
    "Require audited sales, collections and eligible-cost reporting.": "اشتراط تقارير مدققة للمبيعات والتحصيلات والكلف المؤهلة.",
    "Secure performance, payment and completion guarantees proportionate to exposure.": "تأمين ضمانات تنفيذ وسداد وإنجاز تتناسب مع حجم التعرض.",
    "Complete legal review of the selected contractual route.": "استكمال المراجعة القانونية لمسار التعاقد المختار.",
}
ASSUMPTION_TEXT_AR = {
    "Clear title subject to legal verification.": "ملكية سليمة مفترضة، رهناً بالتحقق القانوني.",
    "Not confirmed; legal due diligence required.": "غير مؤكدة؛ يلزم استكمال العناية القانونية الواجبة.",
    "Current project planning inputs; subject to authority confirmation.": "وفق مدخلات المشروع الحالية، رهناً بتأكيد الجهة التنظيمية المختصة.",
    "Only rights evidenced in the project version are included.": "تقتصر الحقوق المحتسبة على ما تؤيده مستندات المشروع.",
    "Existing use not independently verified.": "لم يُتحقق بصورة مستقلة من الاستخدام القائم.",
    "Proposed development scenario.": "سيناريو التطوير المقترح.",
    "Conditionally supported subject to legal, physical and financial feasibility.": "مدعوم بشروط، رهناً بالجدوى القانونية والمادية والمالية.",
    "No unconfirmed planning approval is represented as current market fact.": "لا تُعامل أي موافقة تنظيمية غير مؤكدة كحقيقة سوقية قائمة.",
    "None unless explicitly entered in the case.": "لا يوجد ما لم يُسجل صراحة ضمن الحالة.",
    "Material uncertainty exists where market evidence is limited or dated.": "يوجد عدم يقين جوهري عندما تكون الأدلة السوقية محدودة أو قديمة.",
    "Public authority title is assumed valid and subject to legal verification.": "تُفترض سلامة ملكية صاحب الأرض، رهناً بالتحقق القانوني.",
    "Landowner title is assumed valid and subject to legal verification.": "تُفترض سلامة ملكية صاحب الأرض، رهناً بالتحقق القانوني.",
    "No undisclosed encumbrances assumed; legal verification required.": "يفترض عدم وجود قيود غير مفصح عنها؛ ويلزم التحقق القانوني.",
    "Current planning assumptions supplied by the public authority.": "الافتراضات التنظيمية الحالية مقدمة من مالك الأرض.",
    "Current project planning assumptions supplied by the public authority.": "الافتراضات التنظيمية الحالية للمشروع مقدمة من مالك الأرض.",
    "Only rights evidenced in the project record are reflected.": "تقتصر الحقوق المحتسبة على ما تؤيده مستندات المشروع.",
    "Developer obligations are modeled from the stated cost and responsibility inputs.": "تُحتسب التزامات المطور وفق مدخلات الكلفة والمسؤولية المسجلة.",
    "Verified market evidence or an independent appraisal is required before treating the screening benchmark as market value.": "يلزم دليل سوقي متحقق أو تقييم مستقل قبل معاملة مرجع الفحص كقيمة سوقية.",
    "Additional independent market and title evidence is required to improve confidence.": "يلزم دليل مستقل إضافي للسوق والملكية لرفع درجة الثقة.",
    "Risk mitigation coverage is below 80%.": "تغطية إجراءات معالجة المخاطر أقل من 80%.",
    "The numerical calculation must converge and pass cash reconciliation before any economic recommendation is issued.": "يجب أن يتقارب الحساب العددي وتنجح المصالحة النقدية قبل إصدار أي توصية اقتصادية.",
}
VALUATION_METHOD_LABELS = {
    "COMPARABLE_TRANSACTIONS": {"ar": "المعاملات المقارنة", "en": "Comparable transactions"},
    "RESIDUAL": {"ar": "القيمة المتبقية", "en": "Residual value"},
    "EXISTING_USE_VALUE": {"ar": "قيمة الاستخدام القائم", "en": "Existing-use value"},
    "ALTERNATIVE_USE_VALUE": {"ar": "قيمة الاستخدام البديل", "en": "Alternative-use value"},
    "INDEPENDENT_APPRAISAL": {"ar": "تقييم مستقل", "en": "Independent appraisal"},
    "TENDER_EVIDENCE": {"ar": "أدلة الطرح", "en": "Tender evidence"},
    "SCENARIO_VALUATION": {"ar": "مرجع فحص السيناريو", "en": "Scenario screening benchmark"},
    "EVIDENCE_WEIGHTED_MEDIAN": {"ar": "وسيط مرجح بقوة الأدلة", "en": "Evidence-weighted median"},
}
COST_CATEGORY_LABELS = {
    "BUILDING": {"ar": "أعمال المباني", "en": "Building works"},
    "CONSTRUCTION": {"ar": "أعمال الإنشاء", "en": "Construction"},
    "INTERNAL_INFRASTRUCTURE": {"ar": "الطرق والبنية التحتية الداخلية", "en": "Internal roads and infrastructure"},
    "EXTERNAL_INFRASTRUCTURE": {"ar": "ربط المرافق الخارجية", "en": "External utility connections"},
    "PUBLIC_FACILITIES": {"ar": "المرافق والالتزامات العامة", "en": "Public facilities and obligations"},
    "PERMITS": {"ar": "التراخيص والرسوم النظامية", "en": "Permits and statutory fees"},
    "PROFESSIONAL_FEES": {"ar": "التصميم والدراسات التخصصية", "en": "Design and specialist studies"},
    "MANAGEMENT": {"ar": "إدارة المشروع والإشراف", "en": "Project management and supervision"},
    "MARKETING": {"ar": "التسويق والمبيعات", "en": "Marketing and sales"},
    "OTHER": {"ar": "كلف أخرى", "en": "Other costs"},
    "PRODUCT_CONSTRUCTION": {"ar": "إنشاء المنتجات العقارية", "en": "Product construction"},
    "PROJECT_MANAGEMENT": {"ar": "إدارة المشروع والإشراف", "en": "Project management and supervision"},
    "SALES_MARKETING": {"ar": "التسويق والمبيعات", "en": "Marketing and sales"},
    "AUTHORITIES": {"ar": "التراخيص والرسوم والجهات", "en": "Permits, authorities and statutory fees"},
    "INFRASTRUCTURE": {"ar": "البنية التحتية", "en": "Infrastructure"},
}
CLOSURE_LABELS = {
    "land_uses_100_percent": {"ar": "مجموع استعمالات الأرض", "en": "Land-use allocation"},
    "product_allocation_100_percent": {"ar": "مجموع توزيع المنتجات", "en": "Product allocation"},
    "terminal_debt_zero": {"ar": "عدم وجود دين ختامي", "en": "Zero terminal debt"},
    "deferred_cost_zero": {"ar": "عدم وجود كلف مؤجلة", "en": "Zero deferred cost"},
    "contractual_arrears_zero": {"ar": "عدم وجود متأخرات تعاقدية", "en": "Zero contractual arrears"},
    "mandatory_obligations_settled": {"ar": "تسوية الالتزامات الإلزامية", "en": "Mandatory obligations settled"},
    "unmodeled_scope_zero": {"ar": "عدم وجود نطاق غير محتسب", "en": "Zero unmodelled scope"},
    "ledger_invariants_passed": {"ar": "سلامة دفتر التدفقات", "en": "Ledger integrity"},
    "cash_reconciliation_passed": {"ar": "مصالحة النقد الشهرية", "en": "Monthly cash reconciliation"},
    "numerical_resolution_passed": {"ar": "اكتمال الحل العددي", "en": "Numerical resolution"},
    "economic_feasibility_passed": {"ar": "الجدوى الاقتصادية للعقد", "en": "Contract economic feasibility"},
    "policy_compliance_passed": {"ar": "الامتثال لحدود السياسة", "en": "Policy compliance"},
    "no_double_counting": {"ar": "عدم الازدواج في الحساب", "en": "No double counting"},
    "required_evidence_disclosed": {"ar": "الإفصاح عن الأدلة وعدم اليقين", "en": "Evidence and uncertainty disclosure"},
}
PUBLIC_VALUE_LAYER_LABELS = {
    "contractual_consideration": {"ar": "المقابل التعاقدي", "en": "Contractual consideration"},
    "cash_receipts": {"ar": "المقبوضات النقدية", "en": "Cash receipts"},
    "units_in_kind": {"ar": "وحدات عينية", "en": "Units in kind"},
    "infrastructure_delivered_to_public_authority": {"ar": "بنية تحتية مسلمة لمالك الأرض", "en": "Infrastructure delivered to landowner"},
    "taxes_and_statutory_charges": {"ar": "ضرائب ورسوم نظامية", "en": "Taxes and statutory charges"},
    "wider_economic_benefits": {"ar": "منافع اقتصادية أوسع", "en": "Wider economic benefits"},
    "wider_social_benefits": {"ar": "منافع اجتماعية أوسع", "en": "Wider social benefits"},
    "public_costs": {"ar": "كلف مالك الأرض", "en": "Landowner costs"},
    "public_guarantees": {"ar": "ضمانات مالك الأرض", "en": "Landowner guarantees"},
    "contingent_liabilities": {"ar": "التزامات محتملة", "en": "Contingent liabilities"},
    "administrative_and_audit_costs": {"ar": "كلف الإدارة والتدقيق", "en": "Administration and audit costs"},
    "residual_and_reversionary_value": {"ar": "القيمة المتبقية أو الراجعة", "en": "Residual and reversionary value"},
}

DISTRIBUTION_BLOCK_LABELS = {
    "DEBT_OUTSTANDING": {"ar": "يوجد دين قائم", "en": "Debt outstanding"},
    "PRIOR_OBLIGATIONS_OUTSTANDING": {"ar": "التزامات سابقة غير مسددة", "en": "Prior obligations outstanding"},
    "PROJECT_NOT_COMPLETE": {"ar": "المشروع غير مكتمل وفق السياسة", "en": "Project not complete under policy"},
    "BELOW_MINIMUM_DISTRIBUTION": {"ar": "الفائض أقل من الحد الأدنى للتوزيع", "en": "Below minimum distribution"},
    "FUNDING_GAP": {"ar": "فجوة تمويل قائمة", "en": "Funding gap"},
    "MANDATORY_SHORTFALL": {"ar": "عجز إلزامي قائم", "en": "Mandatory shortfall"},
}

RECONCILIATION_LABELS = {
    "COST_RESPONSIBILITY_RECONCILES": {"ar": "تسوية تحميل الكلف على الأطراف", "en": "Cost responsibility reconciliation"},
    "CONSIDERATION_ACCRUAL_RECONCILES": {"ar": "تسوية استحقاق مقابل مالك الأرض", "en": "Landowner consideration accrual reconciliation"},
    "CLOSING_CASH_RECONCILES": {"ar": "تسوية الرصيد النقدي الختامي", "en": "Closing cash reconciliation"},
    "MONTHLY_CASH_ROWS_RECONCILE": {"ar": "تسوية صفوف التدفق النقدي الشهري", "en": "Monthly cash-flow reconciliation"},
    "TERMINAL_DEBT_ZERO": {"ar": "عدم وجود دين ختامي", "en": "Zero terminal debt"},
    "DEFERRED_COST_ZERO": {"ar": "عدم وجود كلف تطوير مؤجلة", "en": "Zero deferred development cost"},
    "CONTRACTUAL_ARREARS_ZERO": {"ar": "عدم وجود متأخرات تعاقدية", "en": "Zero contractual arrears"},
    "FINANCE_ARREARS_ZERO": {"ar": "عدم وجود متأخرات تمويلية", "en": "Zero finance arrears"},
    "MANDATORY_SHORTFALL_ZERO": {"ar": "عدم وجود عجز إلزامي", "en": "Zero mandatory shortfall"},
    "UNMODELED_SCOPE_ZERO": {"ar": "عدم وجود نطاق غير محتسب", "en": "Zero unmodelled scope"},
}

COST_ITEM_TEXT_AR = {
    "Residential construction": "إنشاء المنتج السكني",
    "Retail construction": "إنشاء المنتج التجاري",
    "Office construction": "إنشاء المكاتب",
    "Hospitality / serviced units construction": "إنشاء الضيافة والوحدات المخدومة",
    "Internal roads and infrastructure": "الطرق والبنية التحتية الداخلية",
    "External utility connections": "ربط المرافق الخارجية",
    "Public-facility site works": "أعمال موقع المرافق العامة",
    "Design, engineering and specialist studies": "التصميم والهندسة والدراسات التخصصية",
    "Permits, authorities and statutory fees": "التراخيص ورسوم الجهات والرسوم النظامية",
    "Project management, supervision and administration": "إدارة المشروع والإشراف والإدارة",
    "Marketing, sales and brokerage": "التسويق والمبيعات والوساطة",
}

ARABIC_REPORT_TEXT = {
    "Model-derived capacity is disclosed for feasibility screening and is not independent market evidence.": "القدرة المستنتجة من النموذج معروضة لفحص الجدوى وليست دليلاً سوقياً مستقلاً.",
    "Retained as a disclosed screening benchmark only until verified market evidence is supplied.": "يُحتفظ به كمرجع فحص معلن فقط إلى حين تقديم دليل سوقي متحقق.",
    "Excluded from market-value reconciliation because it is model-derived or unverified.": "مستبعد من مصالحة القيمة السوقية لأنه مشتق من النموذج أو غير متحقق.",
    "Evidence strength and method applicability.": "قوة الدليل ومدى ملاءمة الطريقة.",
    "Verified market evidence is reconciled by evidence-weighted median; the displayed interval is the interval of the identified anchor method.": "تُصالح الأدلة السوقية المتحققة باستخدام وسيط مرجح بقوة الأدلة، ويعكس النطاق المعروض نطاق الطريقة المرجعية المحددة.",
    "No verified market evidence was supplied. The result is a provisional screening benchmark using disclosed assumptions and must not be represented as an independent market valuation.": "لم يُقدم دليل سوقي متحقق. النتيجة مرجع فحص أولي قائم على الافتراضات المعلنة ولا يجوز عرضها كتقييم سوقي مستقل.",
}
CONFIDENCE_LABELS = {
    "HIGH": {"ar": "مرتفعة", "en": "High"},
    "MODERATE": {"ar": "متوسطة", "en": "Moderate"},
    "MEDIUM": {"ar": "متوسطة", "en": "Medium"},
    "LOW": {"ar": "منخفضة", "en": "Low"},
}
SOLVER_STATUS_LABELS = {
    "VALID_RANGE": {"ar": "نطاق صالح", "en": "Valid range"},
    "NONCONTIGUOUS_FEASIBLE_REGION": {"ar": "مجال جدوى غير متصل", "en": "Non-contiguous feasible region"},
    "NO_FEASIBLE_RANGE": {"ar": "لا يوجد نطاق قابل للتنفيذ", "en": "No feasible range"},
    "NUMERICALLY_UNRESOLVED": {"ar": "الحساب العددي غير محسوم", "en": "Numerically unresolved"},
}
VALUE_CODE_LABELS = {
    "MARKET_VALUE": {"ar": "القيمة السوقية", "en": "Market value"},
    "FAIR_VALUE": {"ar": "القيمة العادلة", "en": "Fair value"},
    "INVESTMENT_VALUE": {"ar": "القيمة الاستثمارية", "en": "Investment value"},
    "SPECIAL_VALUE": {"ar": "القيمة الخاصة", "en": "Special value"},
    "NOMINAL": {"ar": "اسمي", "en": "Nominal"},
    "REAL": {"ar": "حقيقي", "en": "Real"},
    "EXCLUSIVE_OF_TRANSACTION_TAXES": {"ar": "دون ضرائب المعاملات", "en": "Exclusive of transaction taxes"},
}
RISK_CATEGORY_LABELS = {
    "COST": {"ar": "الكلفة", "en": "Cost"},
    "MARKET": {"ar": "السوق", "en": "Market"},
    "FINANCE": {"ar": "التمويل", "en": "Finance"},
    "PLANNING": {"ar": "التنظيم", "en": "Planning"},
    "LEGAL": {"ar": "قانوني", "en": "Legal"},
    "DELIVERY": {"ar": "التنفيذ", "en": "Delivery"},
    "DATA": {"ar": "البيانات", "en": "Data"},
}
SHOCK_LABELS = {
    "PRICE_CHANGE": {"ar": "تغير السعر", "en": "Price change"},
    "COST_CHANGE": {"ar": "تغير الكلفة", "en": "Cost change"},
    "SALES_DELAY_MONTHS": {"ar": "تأخر المبيعات بالأشهر", "en": "Sales delay (months)"},
    "CONSTRUCTION_DELAY_MONTHS": {"ar": "تأخر الإنشاء بالأشهر", "en": "Construction delay (months)"},
    "INTEREST_CHANGE": {"ar": "تغير الفائدة", "en": "Interest-rate change"},
}
SAFEGUARD_LABELS_AR = {
    "sales_definition": "تعريف المبيعات", "collection_definition": "تعريف التحصيل",
    "net_sales_definition": "تعريف صافي المبيعات", "eligible_cost_definition": "تعريف الكلف المؤهلة",
    "related_party_transactions": "معاملات الأطراف المرتبطة", "market_testing": "اختبار السعر السوقي",
    "audit_rights": "حقوق التدقيق", "reporting": "التقارير الدورية", "escrow": "حساب الضمان",
    "guarantees": "الضمانات", "security_over_receivables": "الضمان على الذمم",
    "step_in_rights": "حقوق الحلول", "default": "حالات الإخلال", "cure_period": "مهلة المعالجة",
    "late_payment": "تأخر السداد", "change_in_law": "تغير القانون", "force_majeure": "القوة القاهرة",
    "extension_of_time": "تمديد المدة", "completion_obligations": "التزامات الإنجاز",
    "minimum_development": "الحد الأدنى للتطوير", "termination": "الإنهاء",
    "termination_compensation": "تعويض الإنهاء", "anti_avoidance": "منع التحايل",
    "clawback": "استرداد المنافع", "final_true_up": "التسوية النهائية",
    "dispute_resolution": "تسوية النزاعات",
}

def _normalize_code(value: Any) -> str:
    raw = str(value or "").strip()
    return "_".join(raw.replace("-", " ").replace("/", " ").split()).upper()


def _coded_label(value: Any, mapping: dict[str, dict[str, str]], *, ar: bool) -> str:
    raw = str(value or "—").strip()
    normalized = _normalize_code(raw)
    row = (
        mapping.get(raw)
        or mapping.get(raw.upper())
        or mapping.get(raw.lower())
        or mapping.get(normalized)
        or mapping.get(normalized.lower())
        or {}
    )
    return str(row.get("ar" if ar else "en") or raw.replace("_", " ").title())


def _constraint_label(value: Any, *, ar: bool) -> str:
    raw = str(value or "—").strip()
    normalized = _normalize_code(raw)
    closure = (
        CLOSURE_LABELS.get(raw)
        or CLOSURE_LABELS.get(raw.lower())
        or CLOSURE_LABELS.get(normalized)
        or CLOSURE_LABELS.get(normalized.lower())
    )
    if closure:
        return str(closure.get("ar" if ar else "en") or raw)
    negotiation = CONSTRAINT_LABELS.get(normalized)
    if negotiation:
        return str(negotiation[1] if ar else negotiation[0])
    return raw.replace("_", " ").title()

def _offer_position_text(value: Any, *, ar: bool) -> str:
    return _coded_label(value, OFFER_POSITION_LABELS, ar=ar)


def _contract_uses_net_sales(decision: dict[str, Any]) -> bool:
    native = decision.get("contract_negotiation") or (decision.get("decision_levels") or {}).get("native") or {}
    method = str(native.get("contract_method") or native.get("contract_type") or (decision.get("contract") or {}).get("contract_type") or "").upper()
    if method in {"NET_SALES", "NET_SALES_SHARE"}:
        return True
    if method == "HYBRID":
        components = (decision.get("contract") or {}).get("components") or (decision.get("contract") or {}).get("hybrid_components") or []
        return any("NET" in str(component.get("type") if isinstance(component, dict) else component).upper() for component in components)
    return False


def _constraint_value(value: Any, unit: Any, *, ar: bool = False) -> str:
    if value in (None, ""):
        return "—"
    if str(unit or "").lower() == "currency":
        return _num(value, 2)
    number = _d(value)
    if number is not None:
        return _num(number, 4 if abs(number) < Decimal("1") else 2)
    if isinstance(value, bool):
        return ("نعم" if value else "لا") if ar else ("Yes" if value else "No")
    return str(value)


def _localized_literal(value: Any, *, ar: bool, mapping: dict[str, str]) -> str:
    text = str(value or "—")
    return mapping.get(text, text) if ar else text


def _report_text(value: Any, *, ar: bool) -> str:
    """Localize known engine prose while preserving user-entered Arabic text."""
    text = str(value or "—")
    return ARABIC_REPORT_TEXT.get(text, ASSUMPTION_TEXT_AR.get(text, text)) if ar else text


def _scenario_label(code: Any, *, ar: bool) -> str:
    key = _normalize_code(code or "BASE")
    definition = SCENARIO_DEFINITIONS.get(key) or {}
    return str(definition.get("label_ar" if ar else "label_en") or key.replace("_", " ").title())


def _scenario_description(code: Any, *, ar: bool) -> str:
    key = _normalize_code(code or "BASE")
    definition = SCENARIO_DEFINITIONS.get(key) or {}
    return str(definition.get("description_ar" if ar else "description_en") or "")


def _currency(decision: dict[str, Any]) -> str:
    return str((((decision.get("valuation") or {}).get("basis") or {}).get("currency")) or "USD")


def _native_display(native: dict[str, Any], key: str, *, ar: bool, currency: str) -> str:
    point = native.get(key) or {}
    display = point.get("display_ar" if ar else "display_en")
    if display:
        return str(display)
    value = point.get("value")
    if native.get("measure_type") == "AMOUNT":
        return _money(value, currency)
    if native.get("measure_type") == "HYBRID_PERCENT":
        return f"{_money(native.get('fixed_component'), currency)} + {_pct(value, ratio=False)}"
    return _pct(value, ratio=False)


def _common_context(decision: dict[str, Any], *, ar: bool) -> dict[str, Any]:
    valuation = decision.get("valuation") or {}
    basis = valuation.get("basis") or {}
    native = decision.get("contract_negotiation") or {}
    recommendation = decision.get("recommendation") or {}
    metrics = decision.get("metrics") or {}
    levels = decision.get("decision_levels") or {}
    currency = str(basis.get("currency") or "USD")
    return {
        "valuation": valuation, "basis": basis, "native": native,
        "recommendation": recommendation, "metrics": metrics, "levels": levels,
        "currency": currency, "ar": ar,
    }


def _decision_cards(ctx: dict[str, Any]) -> str:
    ar, native, cur = ctx["ar"], ctx["native"], ctx["currency"]
    return _cards([
        (_lang(ar, "الحد الأدنى", "Minimum defensible"), _native_display(native, "minimum", ar=ar, currency=cur), None),
        (_lang(ar, "التوصية المتوازنة", "Balanced recommendation"), _native_display(native, "balanced", ar=ar, currency=cur), None),
        (_lang(ar, "السقف المتحفظ وفق السياسة", "Policy-adjusted ceiling"), _native_display(native, "risk_adjusted_ceiling", ar=ar, currency=cur), None),
        (_lang(ar, "عرض المستثمر", "Investor offer"), _native_display(native, "offer", ar=ar, currency=cur), _offer_position_text(native.get("offer_position"), ar=ar)),
    ])


def _range_visual(ctx: dict[str, Any]) -> str:
    ar, native, cur = ctx["ar"], ctx["native"], ctx["currency"]
    keys = ["minimum", "balanced", "risk_adjusted_ceiling", "technical_ceiling", "offer"]
    raw_values = [_d((native.get(key) or {}).get("value")) for key in keys]
    values = [value for value in raw_values if value is not None]
    if not values:
        return f'<div class="warning">{_e(_lang(ar,"لم يصدر نطاق صالح بسبب عدم كفاية البيانات أو تعذر الحل.","No valid range was issued because evidence is insufficient or the calculation is unresolved."))}</div>'
    low, high = min(values), max(values)
    span = max(high - low, Decimal("0.0000001"))
    labels = {
        "minimum": _lang(ar, "الحد الأدنى", "Minimum"),
        "balanced": _lang(ar, "التوصية", "Recommendation"),
        "risk_adjusted_ceiling": _lang(ar, "السقف المتحفظ", "Policy-adjusted"),
        "technical_ceiling": _lang(ar, "السقف الفني", "Technical"),
        "offer": _lang(ar, "العرض", "Offer"),
    }
    markers: list[str] = []
    legend: list[str] = []
    for key in keys:
        point = native.get(key) or {}
        value = _d(point.get("value"))
        if value is None:
            continue
        pos = max(Decimal("0"), min(Decimal("100"), (value - low) / span * Decimal("100")))
        markers.append(
            f'<span class="range-marker range-{_e(key)}" style="inset-inline-start:{_e(pos.quantize(Decimal("0.1")))}%"><i></i><b>{_e(labels[key])}</b></span>'
        )
        legend.append(
            f'<div><span class="legend-dot range-{_e(key)}"></span><strong>{_e(labels[key])}</strong><small>{_e(_native_display(native,key,ar=ar,currency=cur))}</small></div>'
        )
    return f'<div class="range-visual"><div class="range-track">{"".join(markers)}</div><div class="range-legend">{"".join(legend)}</div></div>'


def _point_rows(ctx: dict[str, Any]) -> list[list[str]]:
    ar, native, cur = ctx["ar"], ctx["native"], ctx["currency"]
    labels = {
        "minimum": _lang(ar, "الحد الأدنى", "Minimum"),
        "balanced": _lang(ar, "المتوازن", "Balanced"),
        "risk_adjusted_ceiling": _lang(ar, "السقف المتحفظ", "Policy-adjusted ceiling"),
        "technical_ceiling": _lang(ar, "السقف الفني", "Technical ceiling"),
        "offer": _lang(ar, "العرض", "Offer"),
    }
    rows = []
    for key in labels:
        point = native.get(key) or {}
        rows.append([
            labels[key], _native_display(native, key, ar=ar, currency=cur),
            _money(point.get("public_npv"), cur), _pct(point.get("developer_irr")),
            _multiple(point.get("developer_moic")), _money(point.get("funding_gap"), cur),
            _lang(ar, "قابل" if point.get("feasible") else "غير قابل", "Feasible" if point.get("feasible") else "Not feasible"),
            ", ".join(_constraint_label(item, ar=ar) for item in (point.get("failed_constraints") or [])) or "—",
        ])
    return rows


def _executive(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx = _common_context(decision, ar=ar)
    rec, metrics, risk = ctx["recommendation"], ctx["metrics"], decision.get("risk") or {}
    developer = metrics.get("developer") or {}; public = metrics.get("public_authority") or {}
    reason = rec.get("reason_ar" if ar else "reason_en") or rec.get("reason") or _lang(ar,"لا توجد توصية مسجلة.","No recommendation is recorded.")
    risk_items = sorted(risk.get("items") or [], key=lambda x: _d(x.get("residual_score")) or Decimal(0), reverse=True)[:3]
    native = ctx["native"]
    governing = native.get("governing_constraint") or native.get("binding_constraint") or {}
    if isinstance(governing, str):
        binding_label = governing.replace("_", " ").title()
        binding_detail = ""
    else:
        binding_label = governing.get("label_ar" if ar else "label_en") or governing.get("title_ar" if ar else "title_en") or governing.get("id") or _lang(ar,"لم يحدد قيد حاكم منفرد","No single binding constraint identified")
        constraint_id = str(governing.get("id") or governing.get("constraint_id") or "").upper()
        actual = governing.get("actual")
        operator = governing.get("operator") or ""
        threshold = governing.get("threshold")
        operator_display = {">=": "≥", "<=": "≤", "==": "=", ">": ">", "<": "<"}.get(str(operator), str(operator))
        def display_constraint_value(value: Any) -> str:
            if value in (None, ""):
                return "—"
            if any(token in constraint_id for token in ("IRR", "PROFIT_ON_COST", "PROFIT_ON_REVENUE", "MARGIN")):
                return _pct(value)
            if "MULTIPLE" in constraint_id or "MOIC" in constraint_id:
                return _multiple(value)
            if any(token in constraint_id for token in ("NPV", "FUNDING_GAP", "DEBT", "SHORTFALL", "VALUE")):
                return _money(value, ctx["currency"])
            return _compact_value(value, 4)
        if threshold is not None and ar:
            threshold_value = display_constraint_value(threshold)
            requirement_ar = {
                ">=": f"{threshold_value} أو أكثر",
                "<=": f"{threshold_value} أو أقل",
                "==": f"يساوي {threshold_value}",
                ">": f"أكبر من {threshold_value}",
                "<": f"أقل من {threshold_value}",
            }.get(str(operator), f"{operator_display} {threshold_value}".strip())
        else:
            requirement_ar = ""
        binding_detail = " · ".join(filter(None,[
            f"{_lang(ar,'الفعلي','Actual')}: {display_constraint_value(actual)}" if actual is not None else "",
            (f"المطلوب: {requirement_ar}" if ar else f"Required: {operator_display} {display_constraint_value(threshold)}") if threshold is not None else "",
        ]))
    scenarios = decision.get("scenarios") or []
    downside = next((row for row in scenarios if str(row.get("scenario") or "").upper() in {"DOWNSIDE","LOW","SEVERE_DOWNSIDE"}), None)
    if downside is None:
        downside = next((row for row in scenarios if not row.get("feasible")), None) or (scenarios[0] if scenarios else {})
    offer_position = native.get("offer_position") or "—"
    conditions = (rec.get("conditions_precedent_ar") if ar else rec.get("conditions_precedent")) or [_localized_literal(x, ar=ar, mapping=CONDITION_TEXT_AR) for x in (rec.get("conditions_precedent") or [])]
    conditions = [str(x) for x in conditions if x][:4]
    final_cards = [
        (_lang(ar,"العرض الحالي","Current offer"),_native_display(native,"offer",ar=ar,currency=ctx["currency"]),_offer_position_text(offer_position,ar=ar)),
        (_lang(ar,"التوصية المتوازنة","Balanced recommendation"),_native_display(native,"balanced",ar=ar,currency=ctx["currency"]),None),
        (_lang(ar,"السقف المتحفظ وفق السياسة","Policy-adjusted ceiling"),_native_display(native,"risk_adjusted_ceiling",ar=ar,currency=ctx["currency"]),None),
        (_lang(ar,"عائد حقوق ملكية المطور","Developer equity IRR"),_pct(developer.get("equity_irr")),None),
    ]
    conclusion_html = (
        f'<div class="final-opinion"><div class="decision-callout">{_status_badge(rec.get("classification") or rec.get("status"),ar=ar)}<p class="lead"><strong>{_e(_lang(ar,"الرأي النهائي","Final opinion"))}:</strong> {_e(reason)}</p></div>'
        + _cards(final_cards)
        + f'<div class="final-guidance"><article><h3>{_e(_lang(ar,"القيد الحاكم","Binding constraint"))}</h3><p><strong>{_e(binding_label)}</strong></p>{f"<small>{_e(binding_detail)}</small>" if binding_detail else ""}</article><article><h3>{_e(_lang(ar,"الخطوات التالية","Next steps"))}</h3>{_bullet(conditions)}</article></div>'
        + f'<p class="final-note">{_e(_lang(ar,"أُعد هذا التقرير استناداً إلى البيانات والافتراضات المسجلة في النموذج، ويستخدم لأغراض التحليل الاستشاري ودعم القرار.","This report was prepared from the recorded inputs and assumptions for advisory analysis and decision support."))}</p></div>'
    )
    return [
        _section("decision", _lang(ar, "الخلاصة التنفيذية", "Executive conclusion"),
                 f'<div class="decision-callout">{_status_badge(rec.get("classification") or rec.get("status"),ar=ar)}<p class="lead">{_e(reason)}</p></div>' + _cards([
                     (_lang(ar,"موقع العرض","Offer position"),_offer_position_text(offer_position, ar=ar),None),
                     (_lang(ar,"القيمة الحالية لمقابل مالك الأرض","Landowner consideration NPV"),_money(public.get("contractual_consideration_npv"),ctx["currency"]),None),
                     (_lang(ar,"عائد حقوق ملكية المطور","Developer equity IRR"),_pct(developer.get("equity_irr")),None),
                     (_lang(ar,"مضاعف حقوق الملكية","Developer MOIC"),_multiple(developer.get("moic")),None),
                     (_lang(ar,"فجوة التمويل","Funding gap"),_money(developer.get("funding_gap"),ctx["currency"]),None),
                 ])),
        _section("range", _lang(ar,"القيمة والنطاق الاستشاري","Value and advisory range"),
                 _range_visual(ctx)+_decision_cards(ctx)+f'<div class="callout"><strong>{_e(_lang(ar,"القيد الحاكم","Binding constraint"))}:</strong> {_e(binding_label)}{f"<br><small>{_e(binding_detail)}</small>" if binding_detail else ""}</div>'),
        _section("resilience", _lang(ar,"السيناريو المتحفظ ومتانة التمويل","Downside case and funding resilience"),
                 _cards([
                     (_lang(ar,"السيناريو","Scenario"),_scenario_label(downside.get("scenario"),ar=ar),_scenario_description(downside.get("scenario"),ar=ar)),
                     (_lang(ar,"الجدوى","Feasibility"),_lang(ar,"قابل للتنفيذ" if downside.get("feasible") else "غير قابل للتنفيذ","Feasible" if downside.get("feasible") else "Not feasible"),None),
                     (_lang(ar,"عائد حقوق ملكية المطور","Developer equity IRR"),_pct(downside.get("developer_irr")),None),
                     (_lang(ar,"القيمة الحالية لمقابل مالك الأرض","Landowner NPV"),_money(downside.get("government_npv"),ctx["currency"]),None),
                     (_lang(ar,"فجوة التمويل","Funding gap"),_money(downside.get("funding_gap"),ctx["currency"]),None),
                 ])+f'<p class="fine">{_e(_lang(ar,"هذه نتيجة سيناريو حتمي وليست احتمال نجاح إحصائياً.","This is a deterministic scenario result, not a statistical probability of success."))}</p>'),
        _section("risks", _lang(ar,"أهم المخاطر","Material risks"), _table(
            [_lang(ar,"الخطر","Risk"),_lang(ar,"التخصيص","Allocation"),_lang(ar,"المستوى","Level"),_lang(ar,"المعالجة","Mitigation")],
            [[_localized_literal(x.get("title"), ar=ar, mapping=RISK_TEXT_AR),_coded_label(x.get("allocation"), PARTY_LABELS, ar=ar),_coded_label(x.get("residual_level"), RISK_LEVEL_LABELS, ar=ar),_localized_literal(x.get("mitigation"), ar=ar, mapping=RISK_TEXT_AR)] for x in risk_items], compact=True)),
        _section("conclusion", _lang(ar,"الخلاصة والرأي الاستشاري","Conclusion and advisory opinion"), conclusion_html),
    ]

def _land_valuation(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx = _common_context(decision, ar=ar); val=ctx["valuation"]; basis=ctx["basis"]; cur=ctx["currency"]
    methods=val.get("methods") or []; rec=val.get("reconciliation") or {}; concepts=val.get("separated_value_concepts") or {}; units=val.get("unit_values") or {}
    return [
        _section("basis", _lang(ar,"أساس التقييم","Valuation basis"), '<table class="key-value">'+_rows([
            (_lang(ar,"تاريخ التقييم","Valuation date"),basis.get("valuation_date")),
            (_lang(ar,"تاريخ الأساس","Base date"),basis.get("base_date")),
            (_lang(ar,"أساس القيمة","Basis of value"),_coded_label(basis.get("basis_of_value"),VALUE_CODE_LABELS,ar=ar)),
            (_lang(ar,"العملة","Currency"),cur),
            (_lang(ar,"الوضع التنظيمي","Planning status"),_localized_literal(basis.get("planning_and_zoning_status"),ar=ar,mapping=ASSUMPTION_TEXT_AR)),
            (_lang(ar,"حقوق التطوير","Development rights"),_localized_literal(basis.get("development_rights"),ar=ar,mapping=ASSUMPTION_TEXT_AR)),
        ],ar=ar)+'</table>'),
        _section("methods", _lang(ar,"طرق التقييم","Valuation methods"), _table(
            [_lang(ar,"الطريقة","Method"),_lang(ar,"القيمة","Value"),_lang(ar,"النطاق","Range"),_lang(ar,"قوة الدليل","Evidence"),_lang(ar,"الوزن","Weight"),_lang(ar,"المبرر","Rationale")],
            [[_coded_label(m.get("method"),VALUATION_METHOD_LABELS,ar=ar),_money(m.get("value"),cur),f'{_money(m.get("low"),cur)} – {_money(m.get("high"),cur)}',_pct(m.get("evidence_strength")),_pct(m.get("reconciliation_weight")),_report_text(m.get("weight_reason"),ar=ar)] for m in methods])),
        _section("reconciliation", _lang(ar,"مصالحة القيم","Method reconciliation"), _cards([
            (_lang(ar,"القيمة المصالحة","Reconciled value"),_money(rec.get("value"),cur),_coded_label(rec.get("method"),VALUATION_METHOD_LABELS,ar=ar)),
            (_lang(ar,"الحد الأدنى","Low"),_money(rec.get("low"),cur),None),
            (_lang(ar,"الحد الأعلى","High"),_money(rec.get("high"),cur),None),
        ])+f'<div class="callout">{_e(_report_text(rec.get("reason"),ar=ar))}</div>'),
        _section("concepts", _lang(ar,"مفاهيم القيمة المفصولة","Separated value concepts"), _table(
            [_lang(ar,"المفهوم","Value concept"),_lang(ar,"القيمة","Value")],
            [[k.replace("_"," ").title(),_money(v,cur)] for k,v in concepts.items()], compact=True)),
        _section("unit-values", _lang(ar,"قيم الوحدة","Unit values"), _cards([
            (_lang(ar,"لكل م² أرض","Per land m²"),_money(units.get("per_land_sqm"),cur),None),
            (_lang(ar,"لكل م² بنائي","Per buildable m²"),_money(units.get("per_buildable_sqm"),cur),None),
            (_lang(ar,"لكل م² بيعي","Per sellable m²"),_money(units.get("per_sellable_sqm"),cur),None),
        ])),
        _section("uncertainty", _lang(ar,"الثقة وعدم اليقين","Confidence and uncertainty"), _cards([
            (_lang(ar,"حالة مدخلات التحليل","Advisory input status"),_lang(ar,"مكتملة للتحليل الاستشاري","Complete for advisory analysis"),None),
        ])+f'<div class="warning">{_e(_localized_literal(val.get("material_valuation_uncertainty"), ar=ar, mapping=ASSUMPTION_TEXT_AR))}</div>'),
    ]


def _project_economics(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    """Present the one reconciled set of project, area, sales and cost totals."""
    book = decision.get("results_book") or {}
    cur = str(book.get("currency") or _currency(decision))
    land = book.get("land") or {}
    sales = book.get("sales") or {}
    costs = book.get("costs") or {}
    project = book.get("project_metrics") or {}
    reconciliation = book.get("reconciliation") or {}
    category_rows = [
        [
            _coded_label(row.get("category"), COST_CATEGORY_LABELS, ar=ar),
            _money(row.get("gross_total"), cur),
            _money(row.get("developer_total"), cur),
            _money(row.get("government_total"), cur),
            _money(row.get("third_party_total"), cur),
        ]
        for row in costs.get("categories") or []
    ]
    check_rows = [
        [
            _coded_label(row.get("id"), RECONCILIATION_LABELS, ar=ar),
            _lang(ar, "ناجح" if row.get("passed") else "فاشل", "Pass" if row.get("passed") else "Fail"),
            _money(row.get("actual"), cur),
            _money(row.get("required"), cur),
            _money(row.get("variance"), cur),
        ]
        for row in reconciliation.get("checks") or []
    ]
    cost_explanation = _lang(
        ar,
        "إجمالي كلفة المشروع هو مجموع الكلف الإجمالية لكل البنود بعد التصعيد والاحتياطي، ويساوي ما دفعه المطور ومالك الأرض والطرف الثالث معاً. لذلك لا يجوز مقارنته بكلفة المطور وحده.",
        "Total project cost is the sum of every gross cost item after escalation and contingency. It equals the developer, landowner and third-party cash shares combined, so it must not be compared with the developer-borne slice alone.",
    )
    return [
        _section("summary", _lang(ar, "ملخص اقتصاديات المشروع", "Project economics summary"), _cards([
            (_lang(ar, "إجمالي المبيعات", "Gross sales"), _money(sales.get("gross_sales"), cur), None),
            (_lang(ar, "صافي المبيعات", "Net sales"), _money(sales.get("net_sales"), cur), None),
            (_lang(ar, "إجمالي كلفة المشروع", "Total project cost"), _money(costs.get("planned_total_cost"), cur), None),
            (_lang(ar, "ربح المشروع قبل الأرض والتمويل", "Project profit before land and finance"), _money(project.get("profit"), cur), None),
            (_lang(ar, "هامش الربح على الكلفة", "Profit on cost"), _pct(project.get("profit_on_cost")), None),
        ])),
        _section("areas", _lang(ar, "المساحات المحتسبة", "Calculated areas"), _cards([
            (_lang(ar, "مساحة الأرض الإجمالية", "Gross land area"), f"{_num(land.get('gross_land_area_sqm'))} m²", None),
            (_lang(ar, "مساحة الأرض الصافية", "Net land area"), f"{_num(land.get('net_land_area_sqm'))} m²", None),
            (_lang(ar, "إجمالي المساحة الطابقية", "Total GFA"), f"{_num(land.get('total_gfa_sqm'))} m²", None),
            (_lang(ar, "المساحة القابلة للبيع", "Sellable area"), f"{_num(land.get('sellable_area_sqm'))} m²", None),
        ])),
        _section("costs", _lang(ar, "تسوية الكلف وتحميلها", "Cost reconciliation and responsibility"), _cards([
            (_lang(ar, "إجمالي كلفة المشروع", "Total project cost"), _money(costs.get("planned_total_cost"), cur), None),
            (_lang(ar, "حصة المطور النقدية", "Developer cash share"), _money(costs.get("developer_total"), cur), None),
            (_lang(ar, "حصة مالك الأرض النقدية", "Landowner cash share"), _money(costs.get("government_total"), cur), None),
            (_lang(ar, "حصة الطرف الثالث", "Third-party cash share"), _money(costs.get("third_party_total"), cur), None),
        ]) + f'<div class="callout">{_e(cost_explanation)}</div>' + _table(
            [
                _lang(ar, "فئة الكلفة", "Cost category"),
                _lang(ar, "الإجمالي", "Gross"),
                _lang(ar, "المطور", "Developer"),
                _lang(ar, "مالك الأرض", "Landowner"),
                _lang(ar, "طرف ثالث", "Third party"),
            ],
            category_rows,
            compact=True,
        )),
        _section("reconciliation", _lang(ar, "فحوص المصالحة", "Reconciliation checks"), _table(
            [
                _lang(ar, "الفحص", "Check"),
                _lang(ar, "الحالة", "Status"),
                _lang(ar, "الفعلي", "Actual"),
                _lang(ar, "المطلوب", "Required"),
                _lang(ar, "الفرق", "Variance"),
            ],
            check_rows,
            compact=True,
        )),
    ]


def _fair_consideration(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx=_common_context(decision,ar=ar); native=ctx["native"]
    why=native.get("why_this_range") or []
    return [
        _section("contract",_lang(ar,"تعريف المقابل","Contract basis"),'<table class="key-value">'+_rows([
            (_lang(ar,"النموذج","Contract model"),native.get("contract_label_ar" if ar else "contract_label_en")),
            (_lang(ar,"المقياس","Measure"),native.get("measure_label_ar" if ar else "measure_label_en")),
            (_lang(ar,"الأساس","Basis"),native.get("basis_ar" if ar else "basis_en")),
            (_lang(ar,"حالة الحل","Solver status"),_coded_label(native.get("solver_status"), SOLVER_STATUS_LABELS, ar=ar)),
        ],ar=ar)+'</table>'),
        _section("range",_lang(ar,"نطاق التفاوض","Negotiation range"),_decision_cards(ctx)+f'<p class="lead">{_e(native.get("summary_ar" if ar else "summary_en"))}</p>'),
        _section("rationale",_lang(ar,"لماذا هذا النطاق؟","Why this range?"),''.join(
            f'<article class="reason"><h3>{_e(x.get("title_ar" if ar else "title_en"))}</h3><p>{_e(x.get("detail_ar" if ar else "detail_en"))}</p></article>' for x in why)),
        _section("tests",_lang(ar,"اختبار نقاط المقابل","Consideration point tests"),_table(
            [_lang(ar,"النقطة","Point"),_lang(ar,"المقابل","Term"),"Landowner NPV","Developer IRR","MOIC","Funding gap",_lang(ar,"الجدوى","Feasibility"),_lang(ar,"القيود","Failed constraints")],_point_rows(ctx))),
        _section("closure",_lang(ar,"سلامة الإقفال","Closure integrity"),_closure_table(decision,ar)),
    ]


def _offer_assessment(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx=_common_context(decision,ar=ar); native=ctx["native"]; offer=native.get("offer") or {}; rec=ctx["recommendation"]
    findings=(decision.get("closure") or {}).get("assessment_findings") or []
    return [
        _section("offer",_lang(ar,"ملخص العرض","Investor offer"),_cards([
            (_lang(ar,"العرض","Offer"),_native_display(native,"offer",ar=ar,currency=ctx["currency"]),None),
            (_lang(ar,"الموقع","Position"),_offer_position_text(native.get("offer_position"), ar=ar),None),
            (_lang(ar,"النتيجة","Status"),_status_text(rec.get("status"),ar=ar),None),
        ])),
        _section("position",_lang(ar,"موقع العرض داخل النطاق","Offer position in the range"),_decision_cards(ctx)+f'<div class="callout">{_e(offer.get("reason_ar" if ar else "reason_en"))}</div>'),
        _section("economics",_lang(ar,"اقتصاديات العرض","Offer economics"),_cards([
            ("Landowner NPV",_money(offer.get("public_npv"),ctx["currency"]),None),("Developer IRR",_pct(offer.get("developer_irr")),None),
            ("Developer MOIC",_multiple(offer.get("developer_moic")),None),("Funding gap",_money(offer.get("funding_gap"),ctx["currency"]),None),
        ])),
        _section("findings",_lang(ar,"الملاحظات والتعديلات المطلوبة","Findings and required revisions"),_table(
            [_lang(ar,"الرمز","Code"),_lang(ar,"السبب","Reason"),_lang(ar,"القيود","Constraints")],
            [[x.get("code") or x.get("id"),x.get("reason_ar" if ar else "reason_en"),", ".join(_constraint_label(item, ar=ar) for item in (x.get("failed_constraint_ids") or []))] for x in findings],compact=True)),
        _section("conclusion",_lang(ar,"الخلاصة","Conclusion"),f'<div class="decision-callout">{_status_badge(rec.get("classification") or rec.get("status"),ar=ar)}<p>{_e(rec.get("reason_ar" if ar else "reason_en") or rec.get("reason"))}</p></div>'),
    ]


def _options_from_decision(decision: dict[str, Any]) -> list[dict[str, Any]]:
    for path in (
        decision.get("contract_options"),
        (decision.get("decision_levels") or {}).get("contract_options"),
        (decision.get("recommendation") or {}).get("contract_options"),
    ):
        if isinstance(path,list): return path
    return []


def _partnership_options(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx=_common_context(decision,ar=ar); options=_options_from_decision(decision); selected=ctx["native"]
    if not options:
        options=[{"contract_type":selected.get("contract_type"),"label":selected.get("contract_label_ar" if ar else "contract_label_en"),
                  "public_npv":(selected.get("balanced") or {}).get("public_npv"),"developer_irr":(selected.get("balanced") or {}).get("developer_irr"),
                  "developer_moic":(selected.get("balanced") or {}).get("developer_moic"),"funding_gap":(selected.get("balanced") or {}).get("funding_gap"),"status":"SELECTED"}]
    rows=[]
    for x in options:
        rows.append([x.get("label") or x.get("contract_label") or x.get("contract_type"),_money(x.get("public_npv") or x.get("government_npv"),ctx["currency"]),_pct(x.get("developer_irr")),_multiple(x.get("developer_moic")),_money(x.get("funding_gap"),ctx["currency"]),x.get("risk_transfer") or "—",x.get("audit_burden") or "—",x.get("status") or "—"])
    return [
        _section("basis",_lang(ar,"أساس المقارنة","Common comparison basis"),f'<div class="callout">{_e(_lang(ar,"تستخدم جميع البدائل تاريخ أساس وعملة وافتراضات مشروع موحدة. لا تعني النسب الاسمية المتشابهة قيمة حالية متشابهة.","All alternatives use one base date, currency and project assumptions. Similar nominal rates do not imply similar present value."))}</div>'),
        _section("options",_lang(ar,"البدائل المقارنة","Options considered"),_bullet([str(x[0]) for x in rows])),
        _section("comparison",_lang(ar,"المقارنة الاقتصادية","Economic comparison"),_table([_lang(ar,"النموذج","Model"),"Landowner NPV","Developer IRR","MOIC","Funding gap",_lang(ar,"نقل المخاطر","Risk transfer"),_lang(ar,"عبء التدقيق","Audit burden"),_lang(ar,"الحالة","Status")],rows)),
        _section("tradeoffs",_lang(ar,"المفاضلات","Trade-offs"),_bullet([
            _lang(ar,"البيع المباشر يرفع اليقين النقدي لكنه يتخلى عن المشاركة في الأداء المستقبلي.","Outright sale improves cash certainty but gives up participation in future performance."),
            _lang(ar,"حصص المبيعات أبسط تدقيقاً من حصص الربح لكنها تنقل مخاطر هامش الربح إلى المطور.","Sales shares are easier to audit than profit shares but transfer margin risk to the developer."),
            _lang(ar,"حصص الربح تحتاج تعريف كلف وضوابط أطراف مرتبطة وآلية تسوية أكثر صرامة.","Profit shares require stricter cost definitions, related-party controls and true-up mechanics."),
        ])),
        _section("recommendation",_lang(ar,"النموذج الموصى به","Recommended model"),f'<div class="decision-callout"><strong>{_e(selected.get("contract_label_ar" if ar else "contract_label_en"))}</strong><p>{_e(selected.get("summary_ar" if ar else "summary_en"))}</p></div>'),
    ]


def _risk_report(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    risk=decision.get("risk") or {}; items=risk.get("items") or []; regs=(decision.get("registries") or {}).get("risk_treatments") or {}
    rows=[[
        x.get("risk_id"),
        _localized_literal(x.get("title"),ar=ar,mapping=RISK_TEXT_AR),
        _coded_label(x.get("category"),RISK_CATEGORY_LABELS,ar=ar),
        _coded_label(x.get("residual_level"),RISK_LEVEL_LABELS,ar=ar),
        _num(x.get("residual_score")),
        _coded_label(x.get("allocation"),PARTY_LABELS,ar=ar),
        _coded_label(x.get("owner"),PARTY_LABELS,ar=ar),
        _localized_literal(x.get("mitigation"),ar=ar,mapping=RISK_TEXT_AR),
        _lang(ar,"مطلوب" if x.get("contract_clause_required") else "غير مطلوب","Required" if x.get("contract_clause_required") else "Not required"),
    ] for x in items]
    allocation={}
    for x in items: allocation[x.get("allocation")]=allocation.get(x.get("allocation"),0)+1
    return [
        _section("profile",_lang(ar,"ملف المخاطر","Risk profile"),_cards([
            (_lang(ar,"الدرجة المتبقية","Residual score"),_num(risk.get("score")),_coded_label(risk.get("grade"),RISK_LEVEL_LABELS,ar=ar)),
            (_lang(ar,"الدرجة الكامنة","Inherent score"),_num(risk.get("inherent_score")),None),
            (_lang(ar,"تغطية المعالجة","Mitigation coverage"),_pct(risk.get("mitigation_coverage")),None),
            (_lang(ar,"التغطية التعاقدية","Contract coverage"),_pct(risk.get("contract_coverage")),None),
        ])),
        _section("register",_lang(ar,"سجل المخاطر","Risk register"),_table([_lang(ar,"المعرّف","ID"),_lang(ar,"الخطر","Risk"),_lang(ar,"الفئة","Category"),_lang(ar,"المستوى","Level"),_lang(ar,"الدرجة","Score"),_lang(ar,"التخصيص","Allocation"),_lang(ar,"المالك","Owner"),_lang(ar,"المعالجة","Mitigation"),_lang(ar,"بند تعاقدي","Contract clause")],rows)),
        _section("allocation",_lang(ar,"توزيع المخاطر","Risk allocation"),_cards([(str(k or "UNALLOCATED"),str(v),None) for k,v in sorted(allocation.items())])),
        _section("controls",_lang(ar,"المعالجات والضوابط","Treatments and controls"),_table([_lang(ar,"الخطر","Risk"),_lang(ar,"المعالجة الأساسية","Primary treatment"),_lang(ar,"منع الازدواج","Double-counting rule")],[[k,v.get("primary_treatment"),v.get("double_counting_rule")] for k,v in regs.items()],compact=True)),
        _section("priorities",_lang(ar,"أولويات الإغلاق","Priority actions"),_bullet([f"{x.get('risk_id')}: {x.get('mitigation')}" for x in sorted(items,key=lambda y:_d(y.get("residual_score")) or Decimal(0),reverse=True)[:5]])),
    ]


def _sensitivity(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx=_common_context(decision,ar=ar); scenarios=decision.get("scenarios") or []
    base=next((x for x in scenarios if x.get("scenario")=="BASE"),{})
    rows=[]
    for x in scenarios:
        shocks=", ".join(f"{_coded_label(k,SHOCK_LABELS,ar=ar)}={v}" for k,v in (x.get("shocks") or {}).items()) or "—"
        rows.append([_scenario_label(x.get("scenario"),ar=ar),shocks,_lang(ar,"نعم" if x.get("feasible") else "لا","Yes" if x.get("feasible") else "No"),_pct(x.get("developer_irr")),_money(x.get("developer_npv"),ctx["currency"]),_money(x.get("government_npv"),ctx["currency"]),_money(x.get("funding_gap"),ctx["currency"]),", ".join(x.get("failed_constraints") or []) or "—"])
    failed=sum(1 for x in scenarios if not x.get("feasible")); total=len(scenarios)
    return [
        _section("base",_lang(ar,"الحالة الأساسية","Base case"),_cards([("Developer IRR",_pct(base.get("developer_irr")),None),("Developer NPV",_money(base.get("developer_npv"),ctx["currency"]),None),("Landowner NPV",_money(base.get("government_npv"),ctx["currency"]),None),("Funding gap",_money(base.get("funding_gap"),ctx["currency"]),None)])),
        _section("scenarios",_lang(ar,"نتائج السيناريوهات","Scenario results"),_table([_lang(ar,"السيناريو","Scenario"),_lang(ar,"الصدمات","Shocks"),_lang(ar,"قابل للتنفيذ","Feasible"),"Developer IRR","Developer NPV","Landowner NPV","Funding gap",_lang(ar,"القيود","Constraints")],rows)),
        _section("drivers",_lang(ar,"محركات الحساسية","Sensitivity drivers"),_bullet([str(k).replace("_"," ").title() for x in scenarios for k in (x.get("shocks") or {}).keys()])),
        _section("breakpoints",_lang(ar,"نقاط الانكسار","Breakpoints"),_cards([(_lang(ar,"سيناريوهات غير قابلة","Infeasible scenarios"),f"{failed}/{total}",None),(_lang(ar,"نسبة مرور السيناريوهات","Scenario pass ratio"),_pct(Decimal(total-failed)/Decimal(total) if total else None),None)])),
        _section("conclusion",_lang(ar,"الخلاصة","Conclusion"),f'<div class="callout">{_e(_lang(ar,"يجب ربط التوصية بقدرة المشروع على الصمود في السيناريوهات السلبية، وليس بالحالة الأساسية وحدها.","The recommendation must reflect downside survival rather than the base case alone."))}</div>'),
    ]


def _bid_comparison(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    bids=decision.get("bids") or (decision.get("bid_comparison") or {}).get("bids") or []
    ready=len(bids)>=2
    rows=[[x.get("name") or x.get("bidder") or x.get("id"),x.get("contract_type"),x.get("offered_term"),x.get("public_npv"),x.get("developer_irr"),x.get("funding_gap"),x.get("status")] for x in bids]
    notice=_lang(ar,"لم تُدخل عروض متعددة؛ التقرير يوضح فجوة البيانات ولا ينشئ ترتيباً مصطنعاً.","Multiple bids were not supplied; the report discloses the data gap and does not invent a ranking.")
    return [
        _section("readiness",_lang(ar,"جاهزية المقارنة","Comparison readiness"),f'<div class="{"callout" if ready else "warning"}">{_e(_lang(ar,"البيانات كافية للمقارنة." if ready else notice,"Data are sufficient for comparison." if ready else notice))}</div>'),
        _section("bids",_lang(ar,"العروض","Bids"),_table([_lang(ar,"المتقدم","Bidder"),_lang(ar,"العقد","Contract"),_lang(ar,"العرض","Offer"),"Landowner NPV","Developer IRR","Funding gap",_lang(ar,"الحالة","Status")],rows)),
        _section("normalization",_lang(ar,"أساس التطبيع","Normalization basis"),_bullet([_lang(ar,"تاريخ أساس واحد.","One base date."),_lang(ar,"عملة وضرائب وافتراضات سوق موحدة.","Common currency, tax and market assumptions."),_lang(ar,"تقييم صريح للمخاطر والتوقيت.","Explicit risk and timing treatment.")])),
        _section("ranking",_lang(ar,"الترتيب","Ranking"),_table([_lang(ar,"الترتيب","Rank"),_lang(ar,"المتقدم","Bidder"),_lang(ar,"مبرر الترتيب","Rationale")],[[i+1,(x.get("name") or x.get("bidder")),x.get("ranking_reason") or "—"] for i,x in enumerate(bids)] if ready else [])),
        _section("conclusion",_lang(ar,"الخلاصة","Conclusion"),f'<div class="callout">{_e(_lang(ar,"يُعتمد الترتيب فقط بعد اكتمال التطبيع الفني والمالي والقانوني." if ready else notice,"Ranking is relied upon only after technical, financial and legal normalization." if ready else notice))}</div>'),
    ]


def _renegotiation(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    data=decision.get("renegotiation") or {}; original=data.get("original"); proposed=data.get("proposed"); ready=bool(original and proposed); cur=_currency(decision)
    def row(label,key,fmt): return [label,fmt((original or {}).get(key)),fmt((proposed or {}).get(key)),fmt((_d((proposed or {}).get(key)) or Decimal(0))-(_d((original or {}).get(key)) or Decimal(0)))]
    rows=[]
    if ready:
        rows=[row("Landowner NPV","public_npv",lambda v:_money(v,cur)),row("Developer IRR","developer_irr",lambda v:_pct(v)),row("Funding gap","funding_gap",lambda v:_money(v,cur)),row("Risk score","risk_score",lambda v:_num(v))]
    missing=_lang(ar,"يلزم إدخال خط أساس للعقد الأصلي ومقترح التعديل قبل قياس انتقال القيمة.","Original-contract and proposed-amendment baselines are required before value transfer can be measured.")
    return [
        _section("readiness",_lang(ar,"جاهزية التحليل","Analysis readiness"),f'<div class="{"callout" if ready else "warning"}">{_e(_lang(ar,"خطا الأساس متوفران.","Both baselines are available.") if ready else missing)}</div>'),
        _section("baseline",_lang(ar,"العقد الأصلي","Original baseline"),_table([_lang(ar,"البند","Item"),_lang(ar,"القيمة","Value")],[[k,v] for k,v in (original or {}).items()],compact=True)),
        _section("proposal",_lang(ar,"التعديل المقترح","Proposed amendment"),_table([_lang(ar,"البند","Item"),_lang(ar,"القيمة","Value")],[[k,v] for k,v in (proposed or {}).items()],compact=True)),
        _section("transfer",_lang(ar,"انتقال القيمة والمخاطر","Value and risk transfer"),_table([_lang(ar,"المؤشر","Metric"),_lang(ar,"الأصلي","Original"),_lang(ar,"المقترح","Proposed"),_lang(ar,"التغير","Change")],rows)),
        _section("conclusion",_lang(ar,"الخلاصة","Conclusion"),f'<div class="callout">{_e(data.get("conclusion_ar" if ar else "conclusion_en") or missing)}</div>'),
    ]


def _closure_table(decision: dict[str, Any], ar: bool) -> str:
    closure=decision.get("closure") or {}; details=closure.get("details") or []
    if not details:
        checks=closure.get("checks") or {}
        details=[{"id":k,"passed":v,"actual":v,"required":True} for k,v in checks.items()]
    rows=[]
    for item in details:
        identifier=str(item.get("id") or item.get("code") or "")
        label=_constraint_label(identifier,ar=ar)
        actual=item.get("actual") if item.get("actual") is not None else item.get("actual_value")
        required=item.get("required") if item.get("required") is not None else item.get("required_value")
        rows.append([
            label,
            _lang(ar,"ناجح" if item.get("passed") else "فاشل","PASS" if item.get("passed") else "FAIL"),
            _constraint_value(actual,item.get("unit"), ar=ar),
            _constraint_value(required,item.get("unit"), ar=ar),
            item.get("reason_ar" if ar else "reason_en") or item.get("reason"),
            item.get("corrective_action_ar" if ar else "corrective_action_en") or item.get("corrective_action"),
        ])
    return _table([_lang(ar,"القيد","Constraint"),_lang(ar,"الحالة","Status"),_lang(ar,"الفعلي","Actual"),_lang(ar,"المطلوب","Required"),_lang(ar,"السبب","Reason"),_lang(ar,"الإجراء","Corrective action")],rows)


def _policy(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    explanation = decision.get("explanation_tree") or {}
    raw_policy = explanation.get("policy")
    if isinstance(raw_policy, dict):
        policy_rows = [[k, v] for k, v in raw_policy.items()]
    elif raw_policy not in (None, ""):
        policy_rows = [[_lang(ar, "السياسة المضمّنة", "Embedded policy"), raw_policy]]
    else:
        manifest_policy = (((decision.get("manifest") or {}).get("registries") or {}).get("policy"))
        policy_rows = [[_lang(ar, "إصدار السياسة", "Policy version"), manifest_policy or "—"]]
    closure=decision.get("closure") or {}; findings=closure.get("assessment_findings") or []; overrides=decision.get("overrides") or []
    return [
        _section("policy",_lang(ar,"السياسة المطبقة","Applied policy"),_table([_lang(ar,"البند","Item"),_lang(ar,"القيمة","Value")],policy_rows,compact=True)),
        _section("closure",_lang(ar,"قواعد الإقفال","Closure rules"),_closure_table(decision,ar)),
        _section("constraints",_lang(ar,"ملاحظات تقييم العرض","Offer-assessment constraints"),_table([_lang(ar,"الرمز","Code"),_lang(ar,"السبب","Reason"),_lang(ar,"القيود","Constraints")],[[x.get("code") or x.get("id"),x.get("reason_ar" if ar else "reason_en"),", ".join(_constraint_label(item, ar=ar) for item in (x.get("failed_constraint_ids") or []))] for x in findings],compact=True)),
        _section("overrides",_lang(ar,"التجاوزات","Overrides"),_table([_lang(ar,"الحقل","Field"),_lang(ar,"القديم","Old"),_lang(ar,"الجديد","New"),_lang(ar,"السبب","Reason"),_lang(ar,"الموافقة","Approval")],[[x.get("field"),x.get("old_value"),x.get("new_value"),x.get("reason"),x.get("approval")] for x in overrides],compact=True)),
        _section("conclusion",_lang(ar,"خلاصة الامتثال","Compliance conclusion"),f'<div class="decision-callout">{_status_badge((decision.get("recommendation") or {}).get("classification") or (decision.get("recommendation") or {}).get("status"))}<p>{_e(_lang(ar,"لا يعني اجتياز القيود حكماً قانونياً نهائياً؛ بل يثبت الامتثال للسياسة المضمّنة في هذا التشغيل.","Passing constraints is not a final legal opinion; it demonstrates compliance with the policy version embedded in this run."))}</p></div>'),
    ]


def _assumptions(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    val=decision.get("valuation") or {}; basis=val.get("basis") or {}; methods=val.get("methods") or []; rec=decision.get("recommendation") or {}; explanation=decision.get("explanation_tree") or {}; evidence=explanation.get("evidence") or {}
    if isinstance(evidence, dict):
        evidence_gaps = [str(v) for v in evidence.values() if isinstance(v, str) and v]
    elif isinstance(evidence, list):
        evidence_gaps = [str(v) for v in evidence if v not in (None, "")]
    elif evidence not in (None, ""):
        evidence_gaps = [str(evidence)]
    else:
        evidence_gaps = []
    assumption_labels = {
        "title_and_ownership_assumptions": _lang(ar, "الملكية", "Title and ownership"),
        "encumbrances": _lang(ar, "القيود والحقوق", "Encumbrances"),
        "planning_and_zoning_status": _lang(ar, "الوضع التنظيمي", "Planning and zoning"),
        "development_rights": _lang(ar, "حقوق التطوير", "Development rights"),
        "special_assumptions": _lang(ar, "افتراضات خاصة", "Special assumptions"),
        "extraordinary_assumptions": _lang(ar, "افتراضات استثنائية", "Extraordinary assumptions"),
    }
    assumptions=[
        [assumption_labels[key], _localized_literal(basis.get(key), ar=ar, mapping=ASSUMPTION_TEXT_AR)]
        for key in assumption_labels
    ]
    basis_rows = [
        [_lang(ar, "تاريخ التقييم", "Valuation date"), basis.get("valuation_date")],
        [_lang(ar, "تاريخ الأساس", "Base date"), basis.get("base_date")],
        [_lang(ar, "أساس القيمة", "Basis of value"), _coded_label(basis.get("basis_of_value"), VALUE_CODE_LABELS, ar=ar)],
        [_lang(ar, "العملة", "Currency"), basis.get("currency")],
        [_lang(ar, "نوع القيم", "Value convention"), _coded_label(basis.get("nominal_or_real"), VALUE_CODE_LABELS, ar=ar)],
        [_lang(ar, "المعالجة الضريبية", "Tax basis"), _coded_label(basis.get("tax_basis"), VALUE_CODE_LABELS, ar=ar)],
        [_lang(ar, "تاريخ الدليل السوقي", "Market evidence date"), basis.get("market_evidence_date")],
        [_lang(ar, "حالة مدخلات التحليل", "Advisory input status"), _lang(ar, "مكتملة للتحليل الاستشاري", "Complete for advisory analysis")],
    ]
    return [
        _section("basis",_lang(ar,"أساس البيانات","Data basis"),_table([_lang(ar,"البند","Item"),_lang(ar,"القيمة","Value")],basis_rows,compact=True)),
        _section("evidence",_lang(ar,"الأدلة ودرجتها","Evidence and strength"),_table([_lang(ar,"المصدر/الطريقة","Source / method"),_lang(ar,"التصنيف","Classification"),_lang(ar,"قوة الدليل","Strength"),_lang(ar,"تاريخ الدليل","Evidence date")],[[m.get("method"),(m.get("details") or {}).get("classification"),_pct(m.get("evidence_strength")),basis.get("market_evidence_date")] for m in methods])),
        _section("assumptions",_lang(ar,"الافتراضات","Assumptions"),_table([_lang(ar,"الافتراض","Assumption"),_lang(ar,"النص","Statement")],assumptions,compact=True)),
        _section("confidence",_lang(ar,"حالة المدخلات","Input status"),_cards([(_lang(ar,"اكتمال المدخلات","Input completeness"),_lang(ar,"مكتملة للتحليل الاستشاري","Complete for advisory analysis"),None),(_lang(ar,"مصدر النتيجة","Result source"),_lang(ar,"مدخلات المشروع والسياسة المختارة","Project inputs and selected policy"),None)])),
        _section("gaps",_lang(ar,"الفجوات والبيانات الناقصة","Gaps and missing data"),_bullet([_localized_literal(item, ar=ar, mapping=ASSUMPTION_TEXT_AR) for item in list(rec.get("missing_data") or []) + evidence_gaps])),
    ]


def _ledger(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    contract = decision.get("contract") or {}
    flows = contract.get("monthly_flows") or []
    cur = _currency(decision)
    results = decision.get("results_book") or {}
    costs = results.get("costs") or {}
    cash = results.get("cash_flow") or {}
    reconciliation = results.get("reconciliation") or {}
    distribution_ledger = results.get("distribution_ledger") or []

    rows = [
        [
            x.get("date"),
            _money(x.get("public_consideration_accrual"), cur),
            _money(x.get("public_consideration_payment"), cur),
            _money(x.get("public_receivable"), cur),
            _money(x.get("units_in_kind_delivery"), cur),
            _money(x.get("probability_adjustment"), cur),
        ]
        for x in flows
    ]

    economic_rows: list[list[Any]] = []
    cash_rows: list[list[Any]] = []
    for item in costs.get("items") or []:
        gross = _d(item.get("gross_total")) or ZERO
        developer_cash = _d(item.get("developer_total")) or ZERO
        landowner_cash = _d(item.get("government_total")) or ZERO
        third_party = _d(item.get("third_party_total")) or ZERO
        developer_economic = _d(item.get("developer_economic_total"))
        landowner_economic = _d(item.get("public_economic_total"))
        if developer_economic is None:
            developer_economic = developer_cash
        if landowner_economic is None:
            landowner_economic = landowner_cash
        advanced = max(developer_cash - developer_economic, ZERO)
        item_name = _localized_literal(item.get("name") or item.get("cost_id"), ar=ar, mapping=COST_ITEM_TEXT_AR)
        category = _coded_label(item.get("category"), COST_CATEGORY_LABELS, ar=ar)
        economic_rows.append([
            item_name,
            category,
            _money(gross, cur),
            _money(developer_economic, cur),
            _money(landowner_economic, cur),
            _money(third_party, cur),
        ])
        cash_rows.append([
            item_name,
            _money(developer_cash, cur),
            _money(landowner_cash, cur),
            _money(advanced, cur),
        ])

    distribution_rows: list[list[Any]] = []
    for row in distribution_ledger:
        if not row.get("distribution_due"):
            continue
        blocked = row.get("blocked_reason")
        blocked_label = _coded_label(blocked, DISTRIBUTION_BLOCK_LABELS, ar=ar) if blocked else _lang(ar, "تم الاختبار", "Tested")
        distribution_rows.append([
            row.get("date") or row.get("month"),
            _money(row.get("required_reserve"), cur),
            _money(row.get("contractual_settlement"), cur),
            _money(row.get("landowner_cash_receipt"), cur),
            _money(row.get("developer_advance_recovery"), cur),
            _money(row.get("developer_distribution"), cur),
            blocked_label,
        ])

    recon_rows = [
        [
            _coded_label(x.get("id"), RECONCILIATION_LABELS, ar=ar),
            _lang(ar, "ناجح", "Pass") if x.get("passed") else _lang(ar, "فاشل", "Fail"),
            _display_reconciliation_value(x.get("actual"), cur, ar=ar),
            _display_reconciliation_value(x.get("required"), cur, ar=ar),
            _display_reconciliation_value(x.get("variance"), cur, ar=ar),
        ]
        for x in reconciliation.get("checks") or []
    ]
    summary_cards = [
        (_lang(ar, "الاستحقاق الاسمي", "Nominal accrual"), _money(contract.get("nominal_contractual_accrual"), cur), None),
        (_lang(ar, "المقبوضات النقدية لمالك الأرض", "Landowner cash receipts"), _money(contract.get("cash_receipts"), cur), None),
        (_lang(ar, "القيمة الحالية لمقابل مالك الأرض (NPV)", "Landowner consideration NPV"), _money(contract.get("contractual_consideration_npv"), cur), None),
        (_lang(ar, "الذمة الختامية", "Closing receivable"), _money(contract.get("closing_receivable"), cur), None),
        (_lang(ar, "إجمالي كلفة المشروع", "Total project cost"), _money(costs.get("planned_total_cost"), cur), None),
        (_lang(ar, "الكلفة النقدية التي دفعها المطور", "Developer cash-funded cost"), _money(costs.get("developer_total"), cur), None),
        (_lang(ar, "الكلفة الاقتصادية على مالك الأرض", "Landowner economic cost"), _money(costs.get("public_economic_total", costs.get("government_total")), cur), None),
        (_lang(ar, "النقد الختامي", "Closing cash"), _money(cash.get("closing_cash"), cur), None),
    ]
    reconciliation_html = _table(
        [_lang(ar, "الفحص", "Check"), _lang(ar, "الحالة", "Status"), _lang(ar, "الفعلي", "Actual"), _lang(ar, "المطلوب", "Required"), _lang(ar, "الفرق", "Variance")],
        recon_rows,
        compact=True,
    )
    if not recon_rows:
        anti_rows = []
        for key, value in (contract.get("anti_double_counting_checks") or {}).items():
            if isinstance(value, dict):
                passed = bool(value.get("passed"))
                detail = value.get("reason") or value.get("message") or value.get("variance") or value.get("conflicts") or "—"
            else:
                passed = bool(value)
                detail = "—"
            anti_rows.append([key, _lang(ar, "ناجح" if passed else "فاشل", "PASS" if passed else "FAIL"), detail])
        reconciliation_html = _table(
            [_lang(ar, "الفحص", "Check"), _lang(ar, "الحالة", "Status"), _lang(ar, "التفصيل", "Detail")],
            anti_rows,
            compact=True,
        )

    responsibility_explanation = (
        '<div class="callout"><strong>' + _e(_lang(ar, "الفرق بين الدفع النقدي والتحمل الاقتصادي", "Cash funding versus economic responsibility")) + '</strong><p>'
        + _e(_lang(
            ar,
            "الدفع النقدي يوضح من يسدد الكلفة فعلياً أثناء التنفيذ. أما التحمل الاقتصادي فيوضح الطرف الذي تبقى عليه الكلفة بعد التسويات. إذا دفع المطور حصة مالك الأرض مقدماً، تسجل كسلفة قابلة للاسترداد من أول توزيعات مالك الأرض وفق السياسة.",
            "Cash funding identifies who pays during execution. Economic responsibility identifies who ultimately bears the cost after settlement. A landowner share advanced by the developer is recorded as a recoverable and deducted from the first landowner distributions under policy.",
        )) + '</p></div>'
    )

    distribution_html = _table(
        [
            _lang(ar, "تاريخ الاختبار", "Test date"),
            _lang(ar, "الاحتياطي المطلوب", "Required reserve"),
            _lang(ar, "تسوية الاستحقاق", "Contract settlement"),
            _lang(ar, "المقبوض لمالك الأرض", "Landowner receipt"),
            _lang(ar, "استرداد سلفة المطور", "Developer advance recovery"),
            _lang(ar, "توزيع المطور", "Developer distribution"),
            _lang(ar, "الحالة", "Status"),
        ],
        distribution_rows,
        compact=True,
    )

    return [
        _section("summary", _lang(ar, "ملخص الدفتر", "Ledger summary"), _cards(summary_cards)),
        _section(
            "monthly",
            _lang(ar, "التدفقات الشهرية", "Monthly contractual flows"),
            _table(
                [
                    _lang(ar, "التاريخ", "Date"),
                    _lang(ar, "الاستحقاق", "Accrual"),
                    _lang(ar, "السداد", "Payment"),
                    _lang(ar, "الذمة", "Receivable"),
                    _lang(ar, "التسليم العيني", "In-kind delivery"),
                    _lang(ar, "تعديل التحصيل/الخسارة المتوقعة", "Collection / expected-loss adjustment"),
                ],
                rows,
                compact=True,
            ),
        ),
        _section(
            "settlement",
            _lang(ar, "طبقات قيمة مالك الأرض وتحميل الكلف", "Landowner value layers and cost responsibility"),
            _table(
                [_lang(ar, "الطبقة", "Layer"), _lang(ar, "القيمة", "Value")],
                [[_coded_label(k, PUBLIC_VALUE_LAYER_LABELS, ar=ar), _money(v, cur)] for k, v in (contract.get("public_value_layers") or {}).items()],
                compact=True,
            )
            + responsibility_explanation
            + "<h3>" + _e(_lang(ar, "التحمل الاقتصادي النهائي", "Final economic responsibility")) + "</h3>"
            + _table(
                [
                    _lang(ar, "البند", "Item"),
                    _lang(ar, "الفئة", "Category"),
                    _lang(ar, "الإجمالي", "Gross"),
                    _lang(ar, "المطور", "Developer"),
                    _lang(ar, "مالك الأرض", "Landowner"),
                    _lang(ar, "طرف ثالث", "Third party"),
                ],
                economic_rows,
                compact=True,
            )
            + "<h3>" + _e(_lang(ar, "التمويل النقدي والسلف القابلة للاسترداد", "Cash funding and recoverable advances")) + "</h3>"
            + _table(
                [
                    _lang(ar, "البند", "Item"),
                    _lang(ar, "دفع المطور", "Developer cash"),
                    _lang(ar, "دفع مالك الأرض", "Landowner cash"),
                    _lang(ar, "دفعه المطور عن مالك الأرض", "Advanced by developer"),
                ],
                cash_rows,
                compact=True,
            ),
        ),
        _section(
            "periodic-distributions",
            _lang(ar, "التوزيعات الدورية واحتياطيات السيولة", "Periodic distributions and liquidity reserves"),
            '<div class="callout"><p>'
            + _e(_lang(
                ar,
                "يختبر المحرك الفائض القابل للتوزيع وفق دورية سياسة المشروع، بعد تسديد الالتزامات السابقة وتكوين احتياطي الكلف المستقبلية والحد الأدنى للنقد التشغيلي. وتسترد سلف المطور على مالك الأرض قبل تحويل صافي توزيعات مالك الأرض.",
                "The engine tests distributable surplus at the project-policy frequency after settling prior obligations and retaining future-cost and minimum operating-cash reserves. Developer advances on behalf of the landowner are recovered before net landowner distributions are transferred.",
            ))
            + '</p></div>'
            + distribution_html,
        ),
        _section("reconciliation", _lang(ar, "المصالحة", "Reconciliation"), reconciliation_html),
        _section("audit", _lang(ar, "ضوابط الإقفال", "Closure controls"), _closure_table(decision, ar)),
    ]


def _term_sheet(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    contract=decision.get("contract") or {}; native=decision.get("contract_negotiation") or {}; regs=decision.get("registries") or {}; cur=_currency(decision)
    definition=contract.get("contract_definition") or {}; safeguards=regs.get("contract_safeguards") or []
    contract_label = native.get("contract_label_ar" if ar else "contract_label_en") or contract.get("contract_type")
    safeguard_rows = [
        [
            SAFEGUARD_LABELS_AR.get(str(item), str(item).replace("_"," ").title()) if ar else str(item).replace("_"," ").title(),
            _lang(ar, "ضبط تعاقدي يثبت التعريف والمسؤولية وآلية التحقق أو المعالجة.", "Contract control defining responsibility, verification or remedy."),
        ]
        for item in safeguards
    ]
    return [
        _section("commercial",_lang(ar,"الشروط التجارية","Commercial terms"),'<table class="key-value">'+_rows([(_lang(ar,"نوع العقد","Contract type"),contract_label),(_lang(ar,"قاعدة المقابل","Consideration basis"),native.get("basis_ar" if ar else "basis_en")),(_lang(ar,"التوصية","Recommended term"),_native_display(native,"balanced",ar=ar,currency=cur)),(_lang(ar,"العملة","Currency"),cur),(_lang(ar,"تاريخ الأساس","Base date"),contract.get("base_date")),(_lang(ar,"معدل الخصم","Discount rate"),_pct(contract.get("discount_rate")))],ar=ar)+'</table>'),
        _section("definitions",_lang(ar,"التعريفات","Definitions"),_table([_lang(ar,"البند","Term"),_lang(ar,"التعريف","Definition")],[["Measure",definition.get("measure")],["Supported terms",", ".join(definition.get("supported_terms") or [])],["Net-sales deductions",json.dumps(contract.get("deduction_registry") or {},ensure_ascii=False)],["Eligible costs",json.dumps(contract.get("eligible_cost_registry") or {},ensure_ascii=False)]],compact=True)),
        _section("payment",_lang(ar,"آلية السداد والتسوية","Payment and settlement mechanics"),_bullet([_lang(ar,"يُحتسب الاستحقاق والسداد والذمم شهرياً في دفتر موحد.","Accrual, payment and receivables are measured monthly in the unified ledger."),_lang(ar,"يجب تنفيذ تسوية نهائية ومنع احتساب الضمان أو المكونات الهجينة مرتين.","A final true-up and anti-double-counting control are required for guarantees and hybrid components."),f"{_lang(ar,'الذمة الختامية','Closing receivable')}: {_money(contract.get('closing_receivable'),cur)}"])),
        _section("safeguards",_lang(ar,"الضمانات التعاقدية","Contract safeguards"),_table([_lang(ar,"البند","Safeguard"),_lang(ar,"الغرض","Purpose")],safeguard_rows,compact=True)),
        _section("legal",_lang(ar,"الحدود القانونية","Legal boundary"),f'<div class="warning">{_e((regs.get("syrian_jurisdiction") or {}).get("disclaimer"))}</div>'+_table([_lang(ar,"المسار","Route"),_lang(ar,"التصنيف","Classification")],[[k,v] for k,v in (regs.get("syrian_jurisdiction") or {}).items() if k!="disclaimer"],compact=True)),
    ]


def _tender(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx=_common_context(decision,ar=ar); native=ctx["native"]; rec=ctx["recommendation"]
    variable=native.get("measure_label_ar" if ar else "measure_label_en")
    return [
        _section("strategy",_lang(ar,"استراتيجية الطرح","Tender strategy"),f'<div class="decision-callout"><strong>{_e(native.get("contract_label_ar" if ar else "contract_label_en"))}</strong><p>{_e(native.get("basis_ar" if ar else "basis_en"))}</p></div>'),
        _section("competition",_lang(ar,"متغير المنافسة","Competition variable"),_cards([(_lang(ar,"المتغير","Variable"),str(variable or "—"),None),(_lang(ar,"التوصية","Balanced term"),_native_display(native,"balanced",ar=ar,currency=ctx["currency"]),None),(_lang(ar,"الحد الأدنى","Minimum acceptable"),_native_display(native,"minimum",ar=ar,currency=ctx["currency"]),None)])),
        _section("minimum",_lang(ar,"حدود القبول","Acceptance boundaries"),_decision_cards(ctx)),
        _section("qualification",_lang(ar,"التأهيل والضمانات","Qualification and security"),_bullet([_lang(ar,"إثبات القدرة التمويلية ومصدر رأس المال.","Evidence of funding capacity and equity source."),_lang(ar,"خبرة تطويرية قابلة للتحقق وفريق تنفيذ مؤهل.","Verifiable development track record and qualified delivery team."),_lang(ar,"ضمانات تنفيذ وتحصيل وتقارير وتدقيق مناسبة لنموذج العقد.","Performance, collection, reporting and audit security proportionate to the contract model.")])),
        _section("evaluation",_lang(ar,"قواعد التقييم","Evaluation rules"),_bullet([_lang(ar,"تطبيع جميع العروض إلى تاريخ أساس وعملة وافتراضات واحدة.","Normalize all bids to one base date, currency and assumptions."),_lang(ar,"عدم ترتيب العروض على النسبة الاسمية وحدها.","Do not rank bids by nominal percentage alone."),_lang(ar,"اختبار Landowner NPV وعائد المطور وفجوة التمويل والمخاطر وقابلية الإنفاذ.","Test Landowner NPV, developer return, funding gap, risk and enforceability.")])),
        _section("recommendation",_lang(ar,"توصية الطرح","Tender recommendation"),f'<div class="callout"><strong>{_e(rec.get("classification") or rec.get("status"))}</strong><p>{_e(rec.get("reason_ar" if ar else "reason_en") or rec.get("reason"))}</p></div>'),
    ]


def _net_sales_reconciliation_sections(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    reconciliation = decision.get("net_sales_reconciliation") or {}
    cur = _currency(decision)
    category_rows = [
        [row.get("category"), _money(row.get("amount"), cur)]
        for row in reconciliation.get("deductions_by_category") or []
    ]
    monthly_rows = [
        [
            row.get("month"),
            _money(row.get("gross_sales_collections"), cur),
            _money(row.get("cancellations_refunds_and_incentives"), cur),
            _money(row.get("eligible_cost_deductions"), cur),
            _money(row.get("eligible_deductions_used"), cur),
            _money(row.get("eligible_net_sales"), cur),
            _money(row.get("public_consideration"), cur),
            _money(row.get("variance"), cur),
        ]
        for row in reconciliation.get("monthly") or []
        if any(_d(row.get(key)) not in (None, ZERO) for key in ("gross_sales_collections", "eligible_deductions_used", "public_consideration"))
    ]
    status = bool(reconciliation.get("reconciliation_passed"))
    return [
        _section("summary", _lang(ar,"ملخص مصالحة صافي المبيعات","Net-sales reconciliation summary"), _cards([
            (_lang(ar,"إجمالي التحصيلات المؤهلة","Eligible gross collections"),_money(reconciliation.get("gross_sales_collections"),cur),None),
            (_lang(ar,"الإلغاءات والاستردادات والحوافز","Cancellations, refunds and incentives"),_money(reconciliation.get("cancellations_refunds_and_incentives"),cur),None),
            (_lang(ar,"استقطاعات الكلف المؤهلة","Eligible cost deductions"),_money(reconciliation.get("eligible_cost_deductions"),cur),None),
            (_lang(ar,"الاستقطاعات المستخدمة","Deductions used"),_money(reconciliation.get("eligible_deductions_used"),cur),None),
            (_lang(ar,"صافي المبيعات المؤهل","Eligible net sales"),_money(reconciliation.get("eligible_net_sales"),cur),None),
            (_lang(ar,"حصة مالك الأرض","Landowner consideration"),_money(reconciliation.get("public_consideration_from_net_sales"),cur),_pct(reconciliation.get("public_share_rate"))),
        ])),
        _section("deductions", _lang(ar,"الاستقطاعات حسب الفئة","Deductions by category"), _table([_lang(ar,"الفئة","Category"),_lang(ar,"القيمة المؤهلة","Eligible amount")],category_rows,compact=True)),
        _section("monthly", _lang(ar,"المصالحة الشهرية","Monthly reconciliation"), _table([
            _lang(ar,"الشهر","Month"),_lang(ar,"إجمالي التحصيل","Gross collections"),_lang(ar,"إلغاءات/استردادات","Cancellations/refunds"),_lang(ar,"استقطاعات الكلف","Cost deductions"),_lang(ar,"المستخدم","Used"),_lang(ar,"صافي المبيعات","Eligible net sales"),_lang(ar,"حصة مالك الأرض","Landowner share"),_lang(ar,"الفرق","Variance")
        ],monthly_rows,compact=True)),
        _section("controls", _lang(ar,"ضوابط المصالحة","Reconciliation controls"),
                 f'<div class="decision-callout">{_status_badge("PASS" if status else "FAIL",ar=ar)}<p>{_e(_lang(ar,"تتحقق المعادلة: إجمالي التحصيلات - الاستقطاعات المستخدمة - صافي المبيعات المؤهل = صفر.","The control checks: gross collections - deductions used - eligible net sales = zero."))}</p></div>'+_cards([
                     (_lang(ar,"الفرق الإجمالي","Total variance"),_money(reconciliation.get("reconciliation_variance"),cur),None),
                     (_lang(ar,"رصيد الاستقطاعات غير المستخدم","Unused deduction carry-forward"),_money(reconciliation.get("unused_deduction_carryforward"),cur),str(reconciliation.get("treatment") or "—")),
                 ])),
    ]


def _technical_analysis(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    ctx = _common_context(decision, ar=ar)
    basis = ctx["basis"]
    native = ctx["native"]
    scenarios = decision.get("scenarios") or []
    scenario_rows = [
        [
            _scenario_label(row.get("scenario"),ar=ar),
            _scenario_description(row.get("scenario"),ar=ar),
            ", ".join(f"{_coded_label(key,SHOCK_LABELS,ar=ar)}={value}" for key,value in (row.get("shocks") or {}).items()) or _lang(ar,"لا تغيير عن الأساسي","No change from base"),
            _lang(ar,"نعم" if row.get("feasible") else "لا","Yes" if row.get("feasible") else "No"),
            _pct(row.get("developer_irr")),
            _money(row.get("government_npv"),ctx["currency"]),
            _money(row.get("funding_gap"),ctx["currency"]),
        ]
        for row in scenarios
    ]
    sections = [
        _section("basis",_lang(ar,"أساس التحليل والبيانات","Analysis and data basis"),'<table class="key-value">'+_rows([
            (_lang(ar,"تاريخ التقييم","Valuation date"),basis.get("valuation_date")),
            (_lang(ar,"تاريخ الأساس","Base date"),basis.get("base_date")),
            (_lang(ar,"أساس القيمة","Basis of value"),_coded_label(basis.get("basis_of_value"),VALUE_CODE_LABELS,ar=ar)),
            (_lang(ar,"العملة","Currency"),ctx["currency"]),
            (_lang(ar,"حالة مدخلات التحليل","Advisory input status"),_lang(ar,"مكتملة للتحليل الاستشاري","Complete for advisory analysis")),
        ],ar=ar)+'</table>'),
        _section("valuation",_lang(ar,"التقييم والنطاق","Valuation and range"),_range_visual(ctx)+_decision_cards(ctx)),
        _section("contract",_lang(ar,"تعريف العقد والمقابل","Contract and consideration definition"),'<table class="key-value">'+_rows([
            (_lang(ar,"النموذج","Model"),native.get("contract_label_ar" if ar else "contract_label_en")),
            (_lang(ar,"أساس القياس","Measure basis"),native.get("basis_ar" if ar else "basis_en")),
            (_lang(ar,"حالة الحل","Solver status"),_coded_label(native.get("solver_status"),SOLVER_STATUS_LABELS,ar=ar)),
            (_lang(ar,"موقع العرض","Offer position"),_offer_position_text(native.get("offer_position"), ar=ar)),
        ],ar=ar)+'</table>'),
        _section("constraints",_lang(ar,"حدود القبول والسياسات","Acceptance limits and policies"),_closure_table(decision,ar)),
        _section("scenarios",_lang(ar,"تعريف السيناريوهات ونتائجها","Scenario definitions and results"),_table([
            _lang(ar,"السيناريو","Scenario"),_lang(ar,"المعنى","Meaning"),_lang(ar,"التغييرات","Changes"),_lang(ar,"قابل للتنفيذ","Feasible"),_lang(ar,"عائد المطور","Developer IRR"),_lang(ar,"قيمة مالك الأرض الحالية","Landowner NPV"),_lang(ar,"فجوة التمويل","Funding gap")
        ],scenario_rows,compact=True)),
    ]
    if _contract_uses_net_sales(decision):
        net_sections = _net_sales_reconciliation_sections(decision, ar)
        sections.append(_section("net-sales",_lang(ar,"صافي المبيعات والاستقطاعات","Net sales and deductions"),net_sections[0]["html"]+net_sections[1]["html"]+net_sections[3]["html"]))
    return sections


def _audit_annex(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    manifest = decision.get("manifest") or {}
    engine = manifest.get("engine") or {}
    reconciliation = decision.get("net_sales_reconciliation") or {}
    return [
        _section("lineage",_lang(ar,"تسلسل الحساب","Calculation lineage"),_table([_lang(ar,"المكون","Component"),_lang(ar,"القيمة","Value")],[
            ["Calculation Run ID",decision.get("calculation_run_id")],
            [_lang(ar,"إصدار المحرك","Engine version"),engine.get("version")],
            [_lang(ar,"إصدار الحكومة","Government version"),GOVERNMENT_VERSION],
            [_lang(ar,"سجل المعادلات","Formula registry"),FORMULA_REGISTRY_VERSION],
            [_lang(ar,"سجل التقارير","Report registry"),REPORT_REGISTRY_VERSION],
        ],compact=True)),
        _section("hashes",_lang(ar,"بصمات التحقق","Verification hashes"),_table([_lang(ar,"البصمة","Hash"),_lang(ar,"القيمة","Value")],[
            ["Input Hash",decision.get("input_hash")],["Output Hash",decision.get("output_hash")],["Ledger Hash",decision.get("ledger_hash")]
        ],compact=True)),
        _section("constraints",_lang(ar,"تفاصيل القيود","Constraint details"),_closure_table(decision,ar)),
        _section("reconciliation",_lang(ar,"المصالحات","Reconciliations"),_cards([
            (_lang(ar,"مصالحة صافي المبيعات","Net-sales reconciliation"),_lang(ar,"ناجحة" if reconciliation.get("reconciliation_passed") else "فاشلة","Pass" if reconciliation.get("reconciliation_passed") else "Fail"),None),
            (_lang(ar,"فرق المصالحة","Reconciliation variance"),_money(reconciliation.get("reconciliation_variance"),_currency(decision)),None),
        ])),
    ]


def _comprehensive_report(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    """Compose a concise management report without repeating the same facts."""
    chapters: list[tuple[str, str, Callable[[dict[str, Any], bool], list[dict[str, str]]], set[str] | None]] = [
        ("executive", _lang(ar, "الملخص التنفيذي — صفحتان", "Two-page executive summary"), _executive, None),
        ("valuation", _lang(ar, "قيمة الأرض وأساسها", "Land value and basis"), _land_valuation, {"basis", "methods", "reconciliation", "unit-values", "uncertainty"}),
        ("economics", _lang(ar, "اقتصاديات المشروع وتسوية الكلف", "Project economics and cost reconciliation"), _project_economics, {"summary", "areas", "costs", "reconciliation"}),
        ("consideration", _lang(ar, "المقابل العادل ومنطق النطاق", "Fair consideration and range rationale"), _fair_consideration, {"contract", "range", "rationale", "tests"}),
        ("offer", _lang(ar, "تقييم العرض", "Offer assessment"), _offer_assessment, {"offer", "economics", "findings", "conclusion"}),
        ("risk", _lang(ar, "المخاطر والسيناريوهات", "Risks and scenarios"), _risk_report, {"profile", "register", "priorities"}),
        ("sensitivity", _lang(ar, "اختبار المتانة", "Resilience testing"), _sensitivity, {"base", "scenarios", "conclusion"}),
        ("evidence", _lang(ar, "الافتراضات وحدود الاعتماد", "Assumptions and reliance limits"), _assumptions, {"basis", "assumptions", "gaps"}),
        ("terms", _lang(ar, "الشروط التجارية المقترحة", "Proposed commercial terms"), _term_sheet, {"commercial", "payment", "safeguards"}),
    ]

    result: list[dict[str, str]] = []
    for chapter_number, (prefix, chapter_title, builder, keep) in enumerate(chapters, start=1):
        first_section = True
        for section in builder(decision, ar):
            if keep is not None and section["id"] not in keep:
                continue
            item = dict(section)
            item["id"] = f"{prefix}-{section['id']}"
            item["eyebrow"] = f"{chapter_number:02d} · {chapter_title}" if first_section else ""
            item["chapter_start"] = "true" if first_section else "false"
            result.append(item)
            first_section = False
    return result


def _technical_financial_report(decision: dict[str, Any], ar: bool) -> list[dict[str, str]]:
    """Detailed report grouped into page-safe chapters without repeated constraints."""
    groups = [
        ("technical", _technical_analysis(decision, ar), None),
        ("economics", _project_economics(decision, ar), None),
        ("ledger", _ledger(decision, ar), {"summary", "monthly", "settlement", "periodic-distributions", "reconciliation"}),
        ("audit", _audit_annex(decision, ar), {"lineage", "hashes"}),
    ]
    result: list[dict[str, str]] = []
    for prefix, sections, keep in groups:
        first = True
        for section in sections:
            if keep is not None and section["id"] not in keep:
                continue
            item = dict(section)
            item["id"] = f"{prefix}-{section['id']}"
            item["chapter_start"] = "true" if first else "false"
            item["eyebrow"] = item.get("eyebrow") or (
                _lang(ar, "قسم فني", "Technical chapter") if first else ""
            )
            result.append(item)
            first = False
    return result


BUILDERS: dict[str, Callable[[dict[str, Any], bool], list[dict[str, str]]]] = {
    "executive-decision-memorandum": _executive,
    "technical-financial-report": _technical_financial_report,
    "technical-analysis-report": _technical_financial_report,
    "comprehensive-advisory-report": _technical_financial_report,
    "audit-annex": _audit_annex,
    "net-sales-reconciliation-report": _net_sales_reconciliation_sections,
    "land-valuation-report": _land_valuation,
    "fair-consideration-report": _fair_consideration,
    "offer-assessment-report": _offer_assessment,
    "partnership-options-comparison": _partnership_options,
    "risk-allocation-report": _risk_report,
    "sensitivity-report": _sensitivity,
    "bid-comparison-report": _bid_comparison,
    "renegotiation-report": _renegotiation,
    "policy-compliance-report": _policy,
    "assumptions-evidence-report": _assumptions,
    "calculation-ledger": _ledger,
    "contract-term-sheet": _term_sheet,
    "tender-recommendation": _tender,
}


def _document_control(decision: dict[str, Any], *, ar: bool, reviewer: str | None, approver: str | None, project_version: str | None, policy_version: str | None, scenario_version: str | None, report_type: str) -> str:
    manifest=decision.get("manifest") or {}; engine=manifest.get("engine") or {}; basis=((decision.get("valuation") or {}).get("basis") or {})
    rows=[
        (_lang(ar,"نوع التقرير","Report type"),report_type),(_lang(ar,"إصدار المشروع","Project version"),project_version or "unspecified"),
        (_lang(ar,"إصدار السياسة","Policy version"),policy_version or "unspecified"),(_lang(ar,"إصدار السيناريو","Scenario version"),scenario_version or "base"),
        (_lang(ar,"تاريخ التقييم","Valuation date"),basis.get("valuation_date")),(_lang(ar,"تاريخ الأساس","Base date"),basis.get("base_date")),
        (_lang(ar,"إصدار الحكومة","Government version"),GOVERNMENT_VERSION),(_lang(ar,"إصدار المحرك","Engine version"),engine.get("version")),
        (_lang(ar,"سجل المعادلات","Formula registry"),FORMULA_REGISTRY_VERSION),(_lang(ar,"سجل التقارير","Report registry"),REPORT_REGISTRY_VERSION),
        ("Calculation Run ID",decision.get("calculation_run_id")),("Input Hash",decision.get("input_hash")),("Output Hash",decision.get("output_hash")),("Ledger Hash",decision.get("ledger_hash")),
        (_lang(ar,"المراجع","Reviewer"),reviewer or _lang(ar,"غير منطبق - مسار مستخدم واحد","Not applicable - simple one-user workflow")),(_lang(ar,"اعتماد المراجعة","Review sign-off"),approver or "pending"),
        (_lang(ar,"حالة الاستخدام","Use status"),_lang(ar,"تحليل استشاري لدعم القرار","Advisory analysis for decision support")),
    ]
    return '<table class="key-value audit">'+_rows(rows,ar=ar)+'</table>'


def _verification_code(report_type: str, language: str, decision: dict[str, Any], project_version: str | None, policy_version: str | None) -> str:
    raw="|".join([report_type,language,str(decision.get("output_hash") or ""),str(decision.get("calculation_run_id") or ""),str(project_version or ""),str(policy_version or ""),REPORT_REGISTRY_VERSION])
    digest=sha256(raw.encode()).hexdigest().upper()
    return "-".join(digest[i:i+8] for i in range(0,32,8))


def _styles(ar: bool, *, landscape: bool = False) -> str:
    align = "right" if ar else "left"
    page_size = "A4 landscape" if landscape else "A4 portrait"
    document_width = "297mm" if landscape else "210mm"
    cover_height = "210mm" if landscape else "297mm"
    css = r"""
:root{--ink:#112b3b;--navy:#173d59;--teal:#187b69;--gold:#b77a08;--paper:#fff;--muted:#607583;--line:#cfdae0;--soft:#edf4f6;--warn:#fff2cf;--danger:#ffe5e2}
*{box-sizing:border-box}html{background:#edf2f4}body{margin:0;color:var(--ink);font-family:'Noto Sans Arabic','DejaVu Sans',Arial,'Segoe UI',Tahoma,sans-serif;line-height:1.48;text-align:__ALIGN__;font-variant-numeric:tabular-nums lining-nums}.document{width:min(__DOC_WIDTH__,calc(100% - 28px));margin:18px auto;background:var(--paper);box-shadow:0 8px 28px #1233;overflow:hidden}.running-meta{position:absolute;width:1px;height:1px;overflow:hidden;opacity:0}.running-report-title{string-set:reportTitle content()}.running-project-label{string-set:projectLabel content()}.running-report-date{string-set:reportDate content()}.cover{page:cover;min-height:__COVER_HEIGHT__;padding:22mm 18mm 17mm;background:linear-gradient(135deg,#fff 55%,#e1f2ee);border-top:7px solid var(--navy);display:grid;grid-template-rows:auto auto auto auto auto 1fr auto;align-content:start;break-after:page}.brand{display:flex;align-items:center;gap:12px;font-weight:800}.mark{width:38px;height:38px;border-radius:9px;background:var(--navy);color:white;display:grid;place-items:center}.eyebrow{color:var(--gold);letter-spacing:.12em;text-transform:uppercase;font-size:10px;font-weight:800;margin-top:17mm}h1{font-size:32px;line-height:1.15;margin:10px 0 7px}.subtitle{color:var(--teal);font-size:17px;font-weight:700}.purpose{max-width:760px;color:var(--muted);margin:12px 0}.cover-status{margin:10px 0 14px}.cover-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;align-self:start}.cover-summary .metric-card{min-height:76px}.cover-disclaimer{align-self:end;margin-top:12px;padding-top:7px;border-top:1px solid var(--line);color:var(--muted);font-size:8.5px;line-height:1.4}.cover-meta{display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:11px;margin-top:10px}.cover-meta div{border-top:1px solid var(--line);padding-top:6px;overflow-wrap:anywhere}.toc{padding:18mm;border-top:1px solid var(--line);border-bottom:1px solid var(--line);break-after:page}.toc ol{columns:2;column-gap:40px}.toc li{break-inside:avoid;margin:4px 0}.toc a{color:var(--ink);text-decoration:none}main{padding:7mm 16mm 14mm}section{padding:8mm 0;border-bottom:1px solid var(--line)}section:last-child{border:0}h2{font-size:21px;line-height:1.25;margin:3px 0 12px}h3{font-size:15px;line-height:1.3;margin:0 0 7px}.section-eyebrow{font-size:9.5px;color:var(--gold);font-weight:800;letter-spacing:.1em;text-transform:uppercase}.lead{font-size:15px;color:var(--muted)}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px}.metric-card{background:var(--soft);border:1px solid #dce7eb;border-radius:8px;padding:11px;min-height:82px;break-inside:avoid;page-break-inside:avoid}.metric-card span{display:block;color:var(--muted);font-size:10.5px}.metric-card strong{display:block;font-size:17px;margin-top:5px;direction:ltr;unicode-bidi:isolate}.metric-card small{display:block;color:var(--muted);margin-top:3px;font-size:9px}.decision-callout,.callout,.warning{border-radius:8px;padding:12px 14px;margin:9px 0;break-inside:avoid;page-break-inside:avoid}.decision-callout{background:#e5f4f0;border-inline-start:4px solid var(--teal)}.callout{background:var(--soft);border-inline-start:4px solid var(--navy)}.warning{background:var(--warn);border-inline-start:4px solid var(--gold)}.status-badge{display:inline-block;border-radius:999px;padding:4px 10px;background:#dce8ec;font-weight:800;font-size:10.5px}.status-supported,.status-approved,.status-pass,.status-public-favorable-above-recommended-range{background:#d9f1e7;color:#0c5947}.status-requires-revision,.status-not-supported,.status-fail,.status-policy-noncompliant,.status-economically-infeasible,.status-public-favorable-but-economically-infeasible,.status-audit-failure{background:var(--danger);color:#7c241c}.table-wrap{max-width:100%;overflow-x:auto;margin:5px 0 10px}table{width:100%;border-collapse:collapse;font-size:10.5px;table-layout:fixed}th,td{border:1px solid var(--line);padding:5px 6px;vertical-align:top;text-align:__ALIGN__;overflow-wrap:anywhere;word-break:normal}td{font-variant-numeric:tabular-nums lining-nums}thead{display:table-header-group}thead th{background:var(--navy);color:white}tbody tr{break-inside:avoid;page-break-inside:avoid}.key-value th{width:30%;background:var(--soft);color:var(--ink)}.compact th,.compact td{padding:4px 5px;font-size:9.4px}.empty{color:var(--muted);text-align:center}.reason{padding:11px;border:1px solid var(--line);border-radius:8px;margin:8px 0;break-inside:avoid}.fine{font-size:9px;color:var(--muted)}.footer{background:var(--navy);color:white;padding:9px 16mm;font-size:8.5px;word-break:break-all}.final-opinion{display:grid;gap:12px}.final-guidance{display:grid;grid-template-columns:1fr 1.25fr;gap:10px}.final-guidance article{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:13px;break-inside:avoid}.final-guidance article h3{color:var(--navy)}.final-note{margin-top:8px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:9px}.final-opinion .decision-callout{margin-top:0}.final-opinion .metric-grid{grid-template-columns:repeat(4,minmax(0,1fr))}#conclusion{display:flex;flex-direction:column;border-bottom:0;padding:11mm;background:linear-gradient(145deg,#fff 58%,#eef7f4);border:1px solid var(--line);border-radius:10px}#conclusion>.final-opinion{flex:1}.report-section[id="conclusion"]{padding-bottom:0}.signature-grid{display:grid;grid-template-columns:1fr 1fr;gap:30px;margin-top:25px}.signature{border-top:1px solid var(--ink);padding-top:8px;min-height:50px}.numeric,.money,.percentage{direction:ltr;unicode-bidi:isolate;font-variant-numeric:tabular-nums lining-nums}.range-visual{margin:16px 0 20px;break-inside:avoid}.range-track{position:relative;height:17px;border-radius:999px;background:linear-gradient(90deg,#d8e8ee,#dcefe7);margin:45px 14px 10px}.range-marker{position:absolute;top:-34px;transform:translateX(-50%);font-size:8.5px;text-align:center;white-space:nowrap}.range-marker i{display:block;width:3px;height:42px;margin:auto;background:var(--navy);border-radius:3px}.range-marker b{display:block;background:#fff;border:1px solid var(--line);padding:2px 5px;border-radius:5px}.range-offer i,.legend-dot.range-offer{background:var(--gold)}.range-balanced i,.legend-dot.range-balanced{background:var(--teal)}.range-risk_adjusted_ceiling i,.legend-dot.range-risk_adjusted_ceiling{background:#7a5ca5}.range-technical_ceiling i,.legend-dot.range-technical_ceiling{background:#b33a2d}.range-legend{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}.range-legend div{border:1px solid var(--line);padding:6px;border-radius:6px;min-width:0}.range-legend strong,.range-legend small{display:block}.range-legend small{direction:ltr;unicode-bidi:isolate}.legend-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--navy);margin-inline-end:4px}[data-executive="true"] main{padding-top:4mm}[data-executive="true"] section{padding:6mm 0}[data-executive="true"] .section-eyebrow{display:none}.chapter-start{margin-top:7mm;border-top:3px solid var(--navy)}.chapter-start>.section-eyebrow{display:inline-block;background:var(--navy);color:#fff;padding:5px 9px;border-radius:0 0 7px 7px;margin-bottom:5px}.chapter-start>h2{font-size:24px}
@media print{
  @page{size:__PAGE_SIZE__;margin:24mm 13mm 20mm;@top-left{content:string(reportDate);direction:ltr;color:#526671;font-size:7.6pt}@top-center{content:string(reportTitle);color:#526671;font-size:8pt}@bottom-left{content:counter(page) " / " counter(pages);direction:ltr;color:#526671;font-size:8pt}@bottom-right{content:string(projectLabel);color:#526671;font-size:7.6pt;max-width:70mm}}
  @page cover{size:__PAGE_SIZE__;margin:0;@top-left{content:none}@top-center{content:none}@bottom-left{content:none}@bottom-right{content:none}}
  html,body{background:#fff;print-color-adjust:exact;-webkit-print-color-adjust:exact}.document{width:auto;margin:0;max-width:none;box-shadow:none;overflow:visible}.cover{height:__COVER_HEIGHT__;min-height:__COVER_HEIGHT__;padding:18mm 16mm 15mm}.toc{padding:12mm 8mm}main{padding:0}.footer{display:none}.range-marker{top:-12px}.range-marker i{height:30px}.range-marker b{display:none}section{break-inside:auto;page-break-inside:auto;orphans:3;widows:3}h2,h3,.section-eyebrow{break-after:avoid;page-break-after:avoid}.metric-card,.decision-callout,.callout,.warning,.reason,.signature-grid,.range-visual,.final-guidance article{break-inside:avoid;page-break-inside:avoid}thead{display:table-header-group}tfoot{display:table-footer-group}tbody tr{break-inside:avoid;page-break-inside:avoid}.table-wrap{overflow:visible;break-inside:auto}table{font-size:7.7pt;table-layout:fixed}th,td{padding:4.5px 5px;line-height:1.35;vertical-align:middle}.compact th,.compact td{font-size:7pt}.cover,.toc{page-break-after:always}.chapter-start:not(#executive-decision){break-before:page;page-break-before:always;padding-top:0;margin-top:0}#executive-decision{padding-top:0}#resilience{break-before:page;page-break-before:always}#conclusion{break-before:page;page-break-before:always;min-height:160mm;padding:10mm}.final-guidance{grid-template-columns:1fr 1fr}.final-opinion .metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.cover-disclaimer{font-size:7.4pt}.cover-summary{grid-template-columns:repeat(4,minmax(0,1fr))}[data-executive="true"] #risks{break-inside:avoid;page-break-inside:avoid}[data-executive="true"] #conditions{break-inside:auto;page-break-inside:auto;padding-top:2mm;padding-bottom:3mm}[data-executive="true"] #conditions ul{columns:2;column-gap:9mm}[data-executive="true"] #conditions li{break-inside:avoid;margin-bottom:2mm}}
@media print{
  #conclusion{display:block;min-width:0;max-width:100%}
  #conclusion>.final-opinion{width:100%;max-width:100%;min-width:0}
  #conclusion>.final-opinion>*{max-width:100%;min-width:0}
  #conclusion .final-guidance{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  #conclusion .final-guidance ul{margin:.45em 0 0;padding-inline-start:1.35em}
  #conclusion .final-guidance li{font-size:10.5pt;line-height:1.35;margin-bottom:.7mm}
}
"""
    return (css.replace("__ALIGN__", align).replace("__PAGE_SIZE__", page_size).replace("__DOC_WIDTH__", document_width).replace("__COVER_HEIGHT__", cover_height))

def render_report(report_type: str, decision: dict[str, Any], *, language: str="en", reviewer: str|None=None, approver: str|None=None, project_version: str|None=None, policy_version: str|None=None, scenario_version: str|None=None) -> str:
    """Render a self-contained bilingual HTML report.

    Executive output deliberately excludes technical lineage from its body;
    those details remain available in the technical report and audit annex.
    """
    legacy_aliases = {
        "technical-analysis-report": "technical-financial-report",
        "comprehensive-advisory-report": "technical-financial-report",
    }
    report_type = legacy_aliases.get(report_type, report_type)
    if report_type not in REPORT_CATALOG:
        raise ValueError(f"Unknown report type: {report_type}")
    ar = language.lower().startswith("ar")
    definition = REPORT_CATALOG[report_type]
    executive = report_type == "executive-decision-memorandum"
    landscape = report_type in LANDSCAPE_REPORT_TYPES
    title = definition.title_ar if ar else definition.title_en
    purpose = definition.purpose_ar if ar else definition.purpose_en
    sections = BUILDERS[report_type](decision, ar)
    ids = {section["id"] for section in sections}
    missing = set(definition.required_sections) - ids
    if missing:
        raise ValueError(f"Report {report_type} is missing required sections: {sorted(missing)}")

    basis = ((decision.get("valuation") or {}).get("basis") or {})
    recommendation = decision.get("recommendation") or {}
    classification = recommendation.get("classification") or recommendation.get("status") or "UNSPECIFIED"
    # LandValue360's current Landowner workflow is an input-driven advisory
    # financial model.  It no longer applies the legacy market-evidence
    # confidence gate that labelled otherwise complete project inputs as
    # "insufficient data".  Any genuinely missing mandatory project input is
    # handled by validation before report rendering.
    preliminary = False
    body_parts: list[str] = []
    for section in sections:
        chapter_class = " chapter-start" if section.get("chapter_start") == "true" else ""
        eyebrow = (
            f'<div class="section-eyebrow">{_e(section["eyebrow"])}</div>'
            if section.get("eyebrow")
            else ""
        )
        body_parts.append(
            f'<section id="{_e(section["id"])}" class="report-section{chapter_class}">'
            f'{eyebrow}<h2>{_e(section["title"])}</h2>{section["html"]}</section>'
        )
    body = "".join(body_parts)
    if ar:
        # Some metric names originate as stable engine identifiers.  Localize
        # their presentation here so an Arabic download never becomes a mixed
        # Arabic/English management report.
        for source, target in {
            "Developer equity IRR": "العائد الداخلي لحقوق ملكية المطور",
            "Developer IRR": "العائد الداخلي للمطور",
            "Developer NPV": "القيمة الحالية لحقوق المطور (NPV)",
            "Developer MOIC": "مضاعف رأس مال المطور",
            "Landowner NPV": "القيمة الحالية لمقابل مالك الأرض (NPV)",
            "Landowner consideration NPV": "القيمة الحالية لمقابل مالك الأرض (NPV)",
            "Landowner NPV": "القيمة الحالية لمقابل مالك الأرض (NPV)",
            "Funding gap": "فجوة التمويل",
            "MOIC": "مضاعف رأس المال",
        }.items():
            body = body.replace(source, target)
    direction = "rtl" if ar else "ltr"
    lang = "ar" if ar else "en"
    project_label = str(decision.get("project_name") or decision.get("project_code") or _lang(ar, "تحليل مشروع مالك الأرض", "Landowner project analysis"))
    case_ref = str(decision.get("case_reference") or decision.get("case_code") or (decision.get("calculation_run_id") or "")[:18] or "—")
    body_class = "report-landscape" if landscape else "report-portrait"
    document_flags = f'data-executive="{str(executive).lower()}" data-preliminary="{str(preliminary).lower()}"'

    cover_meta_items = [
        (_lang(ar, "تاريخ التقييم", "Valuation date"), basis.get("valuation_date") or "—"),
        (_lang(ar, "مرجع القضية", "Case reference"), case_ref),
    ]
    cover_meta = "".join(f'<div><strong>{_e(label)}</strong><br>{_e(value)}</div>' for label, value in cover_meta_items)
    ctx = _common_context(decision, ar=ar)
    native = ctx.get("native") or {}
    developer = (ctx.get("metrics") or {}).get("developer") or {}
    public = (ctx.get("metrics") or {}).get("public_authority") or {}
    cover_summary = _cards([
        (_lang(ar, "العرض الحالي", "Current offer"), _native_display(native, "offer", ar=ar, currency=ctx["currency"]), None),
        (_lang(ar, "التوصية المتوازنة", "Balanced recommendation"), _native_display(native, "balanced", ar=ar, currency=ctx["currency"]), None),
        (_lang(ar, "القيمة الحالية لمقابل مالك الأرض", "Landowner consideration NPV"), _money(public.get("contractual_consideration_npv"), ctx["currency"]), None),
        (_lang(ar, "عائد حقوق ملكية المطور", "Developer equity IRR"), _pct(developer.get("equity_irr")), None),
    ])

    toc_html = ""
    document_control_html = ""
    footer_html = ""
    if not executive:
        footer_html = f'<footer class="footer">{_e(_lang(ar,"تقرير قرار استشاري لمالك الأرض — للاستخدام المهني الداخلي.","Advisory landowner decision report — for internal professional use."))}</footer>'
    else:
        footer_html = f'<footer class="footer">{_e(_lang(ar,"تقرير قرار استشاري لمالك الأرض — للاستخدام المهني الداخلي.","Advisory landowner decision report — for internal professional use."))}</footer>'

    report_title_short = _lang(ar, "التقرير التنفيذي", "Executive report") if executive else _lang(ar, "التقرير الفني والمالي التفصيلي", "Detailed technical and financial report")
    report_date = str(basis.get("valuation_date") or "")
    return f'''<!doctype html><html lang="{lang}" dir="{direction}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title><style>{_styles(ar, landscape=landscape)}</style></head><body class="{body_class}">
<article class="document" {document_flags}><header class="cover"><div class="brand"><span class="mark">LV</span><div>LandValue360<br><small>{_e(_lang(ar,"واجهة مالك الأرض","Landowner Interface"))}</small></div></div><div class="eyebrow">{_e(_lang(ar,"تقرير قرار","DECISION REPORT"))}</div><h1>{_e(title)}</h1><div class="subtitle">{_e(project_label)}</div><p class="purpose">{_e(purpose)}</p><div class="cover-status">{_status_badge(classification, ar=ar)}</div><div class="cover-summary">{cover_summary.replace('<div class="metric-grid">','').removesuffix('</div>')}</div><p class="cover-disclaimer">{_e(_lang(ar,"النتائج مبنية على مدخلات المشروع والسياسات المختارة وتستخدم للتحليل الاستشاري ودعم القرار؛ وتستلزم المراجعة الفنية والقانونية قبل التعاقد.","Results are based on project inputs and selected policies and are intended for advisory analysis and decision support; technical and legal review is required before contracting."))}</p><div class="cover-meta">{cover_meta}</div></header>
<div class="running-meta"><span class="running-report-title">{_e(report_title_short)}</span><span class="running-project-label">{_e(project_label)}</span><span class="running-report-date">{_e(report_date)}</span></div>
{toc_html}<main data-report-title="{_e(report_title_short)}" data-project-label="{_e(project_label)}" data-report-date="{_e(report_date)}">{body}{document_control_html}</main>{footer_html}</article></body></html>'''


def render_html_to_pdf(html: str) -> bytes:
    """Render report HTML with the bundled pure-Python Pillow renderer.

    This is the sole supported production path.  It does not invoke a browser,
    WeasyPrint, GTK, MSYS2, Cairo, Pango, or external DLL installation.  Any
    rendering error is isolated to the export request and cannot prevent the
    LandValue360 application from starting.
    """

    from landvalue360_server.python_pdf_renderer import render_html_pdf

    return render_html_pdf(html)

def render_pdf(
    report_type: str,
    decision: dict[str, Any],
    *,
    language: str = "en",
    reviewer: str | None = None,
    approver: str | None = None,
    project_version: str | None = None,
    policy_version: str | None = None,
    scenario_version: str | None = None,
) -> bytes:
    """Render a registered advisory report to a downloadable PDF."""

    html = render_report(
        report_type,
        decision,
        language=language,
        reviewer=reviewer,
        approver=approver,
        project_version=project_version,
        policy_version=policy_version,
        scenario_version=scenario_version,
    )
    return render_html_to_pdf(html)


def generate_all_reports(decision: dict[str, Any], **metadata: Any) -> dict[str, dict[str, str]]:
    result={}
    for report_type in REPORT_CATALOG:
        en=render_report(report_type,decision,language="en",**metadata); ar=render_report(report_type,decision,language="ar",**metadata)
        result[report_type]={"en":en,"ar":ar,"en_sha256":sha256(en.encode()).hexdigest(),"ar_sha256":sha256(ar.encode()).hexdigest()}
    return result
