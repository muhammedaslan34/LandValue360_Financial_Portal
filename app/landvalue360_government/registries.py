"""Versioned Government methodology registries.

The registries are executable metadata rather than marketing labels.  They are
included in calculation output and reports so a reviewer can trace each result
to its definition, cash-flow basis and policy source.
"""
from __future__ import annotations

from copy import deepcopy

from .manifest import CONTRACT_REGISTRY_VERSION, FORMULA_REGISTRY_VERSION, METRIC_DICTIONARY_VERSION

CONTRACT_DEFINITIONS = {
    "OUTRIGHT_SALE": {
        "label": "Outright Sale",
        "measure": "amount",
        "supported_terms": ["upfront_amount", "payment_schedule", "indexation", "overage", "clawback"],
    },
    "GROUND_LEASE": {
        "label": "Ground Lease / Usufruct",
        "measure": "rent and premium",
        "supported_terms": ["upfront_premium", "fixed_rent", "indexation_rate", "turnover_rate", "reversionary_value"],
    },
    "GROSS_SALES_SHARE": {
        "label": "Gross Sales Share",
        "measure": "rate",
        "supported_terms": ["rate", "basis", "collection_probability", "floor", "cap"],
    },
    "NET_SALES_SHARE": {
        "label": "Net Sales Share",
        "measure": "rate",
        "supported_terms": ["rate", "deduction_registry", "related_party_rules", "floor", "cap"],
    },
    "PROFIT_SHARE": {
        "label": "Profit Share",
        "measure": "rate",
        "supported_terms": ["rate", "profit_definition", "eligible_cost_registry", "loss_carryforward", "waterfall"],
    },
    "LAND_AS_EQUITY": {
        "label": "Land as Equity / Joint Venture",
        "measure": "land contribution and waterfall",
        "supported_terms": ["land_value", "preferred_return", "ownership_ratio", "return_of_capital", "catch_up", "tiers"],
    },
    "UNITS_IN_KIND": {
        "label": "Units in Kind",
        "measure": "scheduled unit value",
        "supported_terms": ["units", "delivery_probability", "minimum_value", "cash_settlement"],
    },
    "HYBRID": {
        "label": "Hybrid",
        "measure": "independent components",
        "supported_terms": ["components", "floor", "cap", "minimum_guarantee", "overage"],
    },
    "MINIMUM_GUARANTEE": {
        "label": "Minimum Guarantee",
        "measure": "top-up only",
        "supported_terms": ["underlying", "guarantee_amount", "guarantee_schedule", "escrow", "bank_guarantee"],
    },
    "OVERAGE": {
        "label": "Overage / Uplift Sharing",
        "measure": "share of uplift",
        "supported_terms": ["underlying", "driver", "baseline", "rate", "floor", "cap"],
    },
}

NET_SALES_DEDUCTION_REGISTRY = {
    "commercial_discounts": {"allowed": True, "cap": "policy", "basis": "accrual", "document_required": True, "related_party": "market_test"},
    "refunds": {"allowed": True, "cap": None, "basis": "cash", "document_required": True, "related_party": "prohibited_without_approval"},
    "sales_taxes": {"allowed": True, "cap": "statutory", "basis": "accrual", "document_required": True, "related_party": "not_applicable"},
    "service_charges": {"allowed": False, "cap": "0", "basis": "accrual", "document_required": True, "related_party": "not_applicable"},
    "marketing_costs": {"allowed": False, "cap": "0", "basis": "cash", "document_required": True, "related_party": "market_test"},
    "financing_costs": {"allowed": False, "cap": "0", "basis": "cash", "document_required": True, "related_party": "market_test"},
}

ELIGIBLE_COST_REGISTRY = {
    "construction": {"eligible": True, "cash_only": False, "approval": "budget", "related_party": "market_test"},
    "infrastructure": {"eligible": True, "cash_only": False, "approval": "scope_and_budget", "related_party": "market_test"},
    "soft_cost": {"eligible": True, "cap": "policy", "approval": "budget", "related_party": "market_test"},
    "marketing": {"eligible": True, "cap": "policy", "approval": "budget", "related_party": "market_test"},
    "financing": {"eligible": "policy", "cap": "policy", "approval": "financing_plan", "related_party": "market_test"},
    "developer_overhead": {"eligible": "policy", "cap": "policy", "approval": "explicit", "related_party": "market_test"},
    "land_value": {"eligible": False, "reason": "Cannot be cost, equity contribution and payable consideration simultaneously."},
    "fines_and_penalties": {"eligible": False, "reason": "Not a project value-creating cost."},
}

RISK_TREATMENT_REGISTER = {
    key: {"primary_treatment": treatment, "double_counting_rule": "A second treatment requires an explicit rationale."}
    for key, treatment in {
        "title": "contract_term", "eviction": "scenario", "planning": "scenario", "licensing": "scenario",
        "infrastructure": "base_cash_flow", "design": "contingency", "construction": "contingency",
        "cost_overrun": "scenario", "delay": "scenario", "demand": "scenario", "sales": "scenario",
        "collection": "probability_weighting", "financing": "solver_constraint", "interest": "scenario",
        "inflation": "base_cash_flow", "fx": "scenario", "tax": "base_cash_flow", "change_in_law": "contract_term",
        "environment": "contingency", "force_majeure": "contract_term", "quality": "guarantee",
        "delivery": "guarantee", "operation": "contract_term", "dispute": "contract_term", "termination": "contract_term",
    }.items()
}

FORMULA_REGISTRY = {
    "XNPV": {"formula": "sum(CF_t / (1+r)^ACT365F(base,t))", "rounding": "unrounded Decimal internally"},
    "CONTRACT_RECEIVABLE": {"formula": "cumulative contractual accrual - cumulative cash/in-kind settlement", "floor": "0 unless a contractual refund is explicitly modelled"},
    "CONTRACT_FLOOR_CAP": {"formula": "floor and cap constrain contractual accrual; collection probability affects settlement and receivables, not entitlement"},
    "MINIMUM_GUARANTEE": {"formula": "cumulative entitlement = max(cumulative underlying entitlement, active cumulative guarantee); guarantee is top-up only"},
    "LAND_AS_EQUITY_PREFERRED_RETURN": {"formula": "outstanding eligible capital × annual preferred rate × ACT/365F elapsed days", "capital_return": "separate from preferred return"},
    "PROFIT_SHARE_BASE": {"formula": "eligible contract revenue - eligible project costs - eligible financing costs, each recognized once under the contract registry"},
    "TERMINAL_UNPAID_OBLIGATIONS": {"formula": "terminal debt + deferred development cost + contractual arrears + finance arrears + unmodelled scope + project-cost scope shortfall", "excludes": "mandatory_shortfall aggregate to prevent double counting"},
    "RISK_ADJUSTED_NPV": {"formula": "sum(probability-adjusted contractual cash flows discounted on the common base date)"},
    "WEIGHTED_MEDIAN_RECONCILIATION": {"formula": "evidence-weighted median; no automatic arithmetic mean"},
    "TECHNICAL_CEILING": {"formula": "maximum numerically resolved feasible consideration satisfying all binding policy constraints", "invalid_when": "solver is unresolved, discontinuous without a resolved feasible region, or evidence gate fails"},
    "ELIGIBLE_NET_SALES": {
        "formula": "eligible gross sales collections - eligible cancellations/refunds/incentives - contractually excluded taxes - eligible contractual cost deductions",
        "rule": "Only deductions explicitly enabled by the contract registry are included. Unused deductions follow the selected carry-forward policy.",
    },
    "NET_SALES_PUBLIC_SHARE": {
        "formula": "public share rate × eligible net sales base",
        "reconciliation": "gross collections - deductions used - eligible net sales = 0",
    },
}

METRIC_DICTIONARY = {
    "project_unlevered_irr": {"flow": "project operating cash flow before financing", "invalid_when": "no sign change or multiple roots"},
    "developer_equity_irr": {"flow": "developer equity contributions and distributions", "invalid_when": "no sign change or multiple roots"},
    "developer_moic": {"flow": "developer distributions / developer paid-in equity", "invalid_when": "paid-in equity is zero"},
    "public_authority_npv": {"flow": "contractual public cash flows only", "excludes": ["taxes", "wider benefits"]},
    "public_financial_npv": {"flow": "contractual receipts plus public assets less public costs and exposure"},
    "funding_gap": {"flow": "maximum unsupported cumulative project cash deficit"},
    "peak_debt": {"flow": "maximum closing debt balance"},
    "profit_on_cost": {"flow": "developer profit / eligible development cost"},
    "profit_on_revenue": {"flow": "developer profit / recognized revenue"},
    "finance_arrears": {"flow": "accrued finance charges and fees not settled by the terminal date", "closure_requirement": "zero"},
    "terminal_unpaid_obligations": {"flow": "non-overlapping terminal debt, deferred cost, contractual arrears, finance arrears, unmodelled scope and cost-scope shortfall"},
}

CONTRACT_SAFEGUARDS = [
    "sales_definition", "collection_definition", "net_sales_definition", "eligible_cost_definition",
    "related_party_transactions", "market_testing", "audit_rights", "reporting", "escrow", "guarantees",
    "security_over_receivables", "step_in_rights", "default", "cure_period", "late_payment",
    "change_in_law", "force_majeure", "extension_of_time", "completion_obligations", "minimum_development",
    "termination", "termination_compensation", "anti_avoidance", "clawback", "final_true_up", "dispute_resolution",
]

SYRIAN_JURISDICTION_PROFILE = {
    "disclaimer": "Decision-support classification only; not a final Syrian legal opinion.",
    "public_domain_sale": "POTENTIALLY_PROHIBITED",
    "state_private_property_sale": "REQUIRES_LEGAL_REVIEW",
    "local_authority_property_sale": "REQUIRES_SPECIAL_APPROVAL",
    "usufruct": "AVAILABLE_WITH_CONDITIONS",
    "long_term_lease": "AVAILABLE_WITH_CONDITIONS",
    "investment_contract": "AVAILABLE_WITH_CONDITIONS",
    "joint_development": "NOT_CONFIRMED",
    "land_as_equity": "NOT_CONFIRMED",
    "competitive_tender": "AVAILABLE_WITH_CONDITIONS",
    "direct_award": "REQUIRES_SPECIAL_APPROVAL",
}


ANALYSIS_PURPOSES = {
    "STRUCTURING": {
        "label_ar": "هيكلة شراكة جديدة",
        "label_en": "Structuring a New Partnership",
        "when_ar": "قبل استلام عرض ملزم، لتحديد نموذج المقابل وشروط الطرح والنطاق التفاوضي.",
        "when_en": "Before receiving a binding offer, to structure consideration, tender terms and a negotiation range.",
        "inputs_ar": "بيانات الأرض، البرنامج التطويري، السوق، الكلف، التمويل والسياسات.",
        "inputs_en": "Land, development programme, market, cost, funding and policy inputs.",
        "outputs_ar": "النموذج المقترح، الحد الأدنى، التوصية، السقف وشروط الطرح.",
        "outputs_en": "Recommended model, minimum, balanced recommendation, ceiling and tender conditions.",
        "example_ar": "اختيار ما إذا كان الطرح الأنسب بيعاً أو نسبة مبيعات أو نموذجاً هجيناً.",
        "example_en": "Selecting whether the tender should use a sale, revenue share or hybrid consideration.",
        "estimated_minutes": 20,
    },
    "OFFER_ASSESSMENT": {
        "label_ar": "تقييم عرض مقدم",
        "label_en": "Assessing an Existing Offer",
        "when_ar": "عند وجود عرض محدد من مطور وضرورة اختباره مقابل القيمة والجدوى.",
        "when_en": "When a specific developer offer must be tested against value and feasibility.",
        "inputs_ar": "بيانات المشروع والعرض وشروط الدفع والاستقطاعات والضمانات.",
        "inputs_en": "Project, offer, payment, deduction and guarantee terms.",
        "outputs_ar": "موقع العرض، عدالته الاستشارية، التعديلات المطلوبة ومخاطر التنفيذ.",
        "outputs_en": "Offer position, advisory support status, required revisions and execution risks.",
        "example_ar": "اختبار عرض حصة 28% من صافي المبيعات.",
        "example_en": "Testing an offer of 28% of eligible net sales.",
        "estimated_minutes": 15,
    },
    "BID_COMPARISON": {
        "label_ar": "مقارنة عروض",
        "label_en": "Comparing Bids",
        "when_ar": "عند وجود عرضين أو أكثر بآليات مقابل مختلفة.",
        "when_en": "When two or more bids use different consideration mechanisms.",
        "inputs_ar": "الشروط المالية والتعاقدية لكل عرض على افتراضات موحدة.",
        "inputs_en": "Financial and contractual terms for each bid on a common assumption set.",
        "outputs_ar": "قيمة حالية موحدة، ترتيب، مخاطر، وقابلية التحصيل والتنفيذ.",
        "outputs_en": "Normalized NPV, ranking, risk, collectability and deliverability.",
        "example_ar": "مقارنة بيع مقدم مع نسبة من المبيعات ونموذج هجين.",
        "example_en": "Comparing an upfront sale, revenue share and hybrid bid.",
        "estimated_minutes": 25,
    },
    "RENEGOTIATION": {
        "label_ar": "تقييم تعديل عقد",
        "label_en": "Assessing a Contract Renegotiation",
        "when_ar": "عند اقتراح تعديل نسبة أو مدة أو كلفة أو ضمان في عقد قائم.",
        "when_en": "When changing a share, duration, cost allocation or guarantee in an existing contract.",
        "inputs_ar": "خط الأساس التعاقدي والتعديل المقترح وأسباب التغيير.",
        "inputs_en": "Original contractual baseline, proposed amendment and rationale.",
        "outputs_ar": "انتقال القيمة والمخاطر وأثر التعديل على الطرفين.",
        "outputs_en": "Value/risk transfer and the effect on both parties.",
        "example_ar": "قياس أثر خفض النسبة مقابل تمديد مدة التنفيذ.",
        "example_en": "Measuring a lower share in exchange for a delivery extension.",
        "estimated_minutes": 25,
    },
}


SCENARIO_DEFINITIONS = {
    "UPSIDE": {
        "label_ar": "متفائل",
        "label_en": "Upside",
        "description_ar": "يفترض تحسن المتغيرات التجارية الرئيسية مقارنة بالأساسي، مثل ارتفاع السعر أو سرعة البيع والتحصيل.",
        "description_en": "Assumes favourable commercial movement from base, such as stronger prices, sales velocity or collections.",
        "purpose_ar": "قياس الاستفادة المحتملة دون اعتمادها كأساس وحيد للتوصية.",
        "purpose_en": "Tests upside participation without using it as the sole recommendation basis.",
    },
    "BASE": {
        "label_ar": "أساسي",
        "label_en": "Base",
        "description_ar": "الافتراضات المركزية المدخلة أو المعتمدة في المشروع دون صدمة إضافية.",
        "description_en": "The central entered or approved project assumptions without an additional shock.",
        "purpose_ar": "مرجع المقارنة لجميع السيناريوهات الأخرى.",
        "purpose_en": "Reference case for every other scenario.",
    },
    "DOWNSIDE": {
        "label_ar": "متحفظ",
        "label_en": "Downside",
        "description_ar": "يخفض الإيرادات أو سرعة التحصيل ويرفع الكلف أو التأخير بصورة متحفظة.",
        "description_en": "Applies conservative revenue/collection reductions and cost or delay increases.",
        "purpose_ar": "اختبار بقاء الجدوى وهامش الأمان تحت ضغط معقول.",
        "purpose_en": "Tests feasibility and headroom under a plausible adverse case.",
    },
    "SEVERE_DOWNSIDE": {
        "label_ar": "ضغط شديد",
        "label_en": "Severe Downside",
        "description_ar": "يجمع صدمات أشد في الأسعار والكلف والسرعة والتأخير والتمويل.",
        "description_en": "Combines more severe price, cost, velocity, delay and funding shocks.",
        "purpose_ar": "إظهار نقطة الانكسار وليس توقعاً إحصائياً مؤكداً.",
        "purpose_en": "Shows a stress breakpoint; it is not a calibrated probability forecast.",
    },
    "PRICE_DOWN": {"label_ar": "انخفاض سعر البيع", "label_en": "Selling Price Reduction", "description_ar": "يخفض أسعار البيع فقط مع تثبيت بقية المتغيرات.", "description_en": "Reduces selling prices while holding other assumptions constant.", "purpose_ar": "حساسية السعر.", "purpose_en": "Price sensitivity."},
    "COST_UP": {"label_ar": "تجاوز الكلفة", "label_en": "Cost Overrun", "description_ar": "يرفع كلف التطوير والبنية التحتية وفق الصدمة المحددة.", "description_en": "Increases development and infrastructure costs by the stated shock.", "purpose_ar": "حساسية الكلفة.", "purpose_en": "Cost sensitivity."},
    "SLOW_SALES": {"label_ar": "بطء المبيعات", "label_en": "Slow Sales", "description_ar": "يمدد فترة البيع ويؤخر التدفقات الداخلة.", "description_en": "Extends the sales period and delays cash inflows.", "purpose_ar": "اختبار السيولة والتمويل.", "purpose_en": "Liquidity and funding stress."},
    "COLLECTION_DELAY": {"label_ar": "تأخر التحصيل", "label_en": "Collection Delay", "description_ar": "يؤخر دفعات المشترين دون تغيير السعر الاسمي.", "description_en": "Delays buyer instalments without changing nominal price.", "purpose_ar": "اختبار الذمم وفجوة التمويل.", "purpose_en": "Receivable and funding-gap stress."},
    "CONSTRUCTION_DELAY": {"label_ar": "تأخر الإنشاء", "label_en": "Construction Delay", "description_ar": "يؤخر الإنفاق والتسليم وقد يرفع التصعيد والتمويل.", "description_en": "Delays construction and delivery and may increase escalation and finance costs.", "purpose_ar": "اختبار الجدول والتصعيد.", "purpose_en": "Schedule and escalation stress."},
    # Compatibility aliases retained for legacy projects. The interface presents
    # these with unambiguous business labels rather than raw LOW/HIGH codes.
    "MILD_DOWNSIDE": {"label_ar": "متحفظ معتدل", "label_en": "Mild downside", "description_ar": "انخفاض محدود في الأسعار مع زيادة محدودة في الكلف مقارنة بالسيناريو الأساسي.", "description_en": "A limited price reduction combined with a limited cost increase relative to base.", "purpose_ar": "اختبار متانة أولي قبل السيناريو المتحفظ الكامل.", "purpose_en": "Provides an initial resilience test before the full downside case."},
    "LOW": {"label_ar": "متحفظ", "label_en": "Downside", "description_ar": "اسم متوافق مع المشاريع السابقة للسيناريو المتحفظ؛ يعرض جميع الصدمات الفعلية مقارنة بالأساسي.", "description_en": "Legacy-compatible name for the downside scenario; all actual shocks are disclosed against base.", "purpose_ar": "اختبار المتانة تحت افتراضات أقل ملاءمة.", "purpose_en": "Tests resilience under less favourable assumptions."},
    "HIGH": {"label_ar": "متفائل", "label_en": "Upside", "description_ar": "اسم متوافق مع المشاريع السابقة للسيناريو المتفائل؛ يعرض جميع الصدمات الفعلية مقارنة بالأساسي.", "description_en": "Legacy-compatible name for the upside scenario; all actual shocks are disclosed against base.", "purpose_ar": "اختبار المشاركة في التحسن المحتمل.", "purpose_en": "Tests participation in potential upside."},
    # Named compatibility scenarios emitted by prior editions and sample projects.
    "COST_OVERRUN": {"label_ar": "تجاوز الكلفة", "label_en": "Cost Overrun", "description_ar": "يرفع كلف التطوير والبنية التحتية وفق الصدمة المحددة.", "description_en": "Increases development and infrastructure costs by the stated shock.", "purpose_ar": "حساسية الكلفة.", "purpose_en": "Cost sensitivity."},
    "PRICE_REDUCTION": {"label_ar": "انخفاض سعر البيع", "label_en": "Selling Price Reduction", "description_ar": "يخفض أسعار البيع فقط مع تثبيت بقية المتغيرات.", "description_en": "Reduces selling prices while holding other assumptions constant.", "purpose_ar": "حساسية السعر.", "purpose_en": "Price sensitivity."},
    "DELIVERY_DELAY": {"label_ar": "تأخر التسليم", "label_en": "Delivery Delay", "description_ar": "يؤخر الإنشاء والتسليم والتحصيل وفق المدة المحددة.", "description_en": "Delays construction, delivery and collections by the stated period.", "purpose_ar": "اختبار أثر التأخير على السيولة والقيمة الحالية.", "purpose_en": "Tests the effect of delay on liquidity and present value."},
    "FX_SHOCK": {"label_ar": "صدمة سعر الصرف", "label_en": "FX Shock", "description_ar": "يطبق صدمة متزامنة على الأسعار والكلف لتمثيل تغيرات العملة.", "description_en": "Applies a combined price and cost shock to represent currency movement.", "purpose_ar": "اختبار التعرض للعملة.", "purpose_en": "Currency-exposure stress."},
    "INFLATION_SHOCK": {"label_ar": "صدمة التضخم", "label_en": "Inflation Shock", "description_ar": "يرفع الكلف وفق صدمة تضخم محددة مع تثبيت بقية الافتراضات.", "description_en": "Increases costs by the stated inflation shock while holding other assumptions constant.", "purpose_ar": "اختبار حساسية التضخم.", "purpose_en": "Inflation sensitivity."},
    "INTEREST_RATE_SHOCK": {"label_ar": "صدمة سعر الفائدة", "label_en": "Interest Rate Shock", "description_ar": "يرفع سعر الفائدة على التمويل المستخدم في المشروع.", "description_en": "Increases the interest rate applied to project financing.", "purpose_ar": "اختبار حساسية التمويل.", "purpose_en": "Financing sensitivity."},
    "REGULATORY_DELAY": {"label_ar": "تأخر تنظيمي", "label_en": "Regulatory Delay", "description_ar": "يؤخر بدء التنفيذ والتسليم والتحصيل بسبب تأخر الموافقات التنظيمية.", "description_en": "Delays construction, delivery and collections because of regulatory approvals.", "purpose_ar": "اختبار مخاطر التنظيم والمدة.", "purpose_en": "Planning and schedule stress."},
    "INFRASTRUCTURE_DELAY": {"label_ar": "تأخر البنية التحتية", "label_en": "Infrastructure Delay", "description_ar": "يؤخر التنفيذ والتحصيل ويرفع بعض الكلف نتيجة تأخر البنية التحتية.", "description_en": "Delays delivery and collections and increases selected costs due to infrastructure delay.", "purpose_ar": "اختبار اعتماد المشروع على البنية التحتية.", "purpose_en": "Infrastructure-dependency stress."},
}


STATUS_LABELS = {
    "SUPPORTED": {"ar": "مدعوم استشارياً", "en": "Advisory supported"},
    "CONDITIONALLY_SUPPORTED": {"ar": "مدعوم بشروط", "en": "Conditionally supported"},
    "PUBLIC_FAVORABLE_ABOVE_RECOMMENDED_RANGE": {
        "ar": "أعلى من النطاق الموصى به — من صالح أصحاب الأرض",
        "en": "Above recommended range — favorable to landowners",
    },
    "PUBLIC_FAVORABLE_BUT_ECONOMICALLY_INFEASIBLE": {
        "ar": "من صالح أصحاب الأرض اسمياً — لكنه يتجاوز الجدوى الفنية",
        "en": "Nominally favorable to landowners — but above technical feasibility",
    },
    "POLICY_NONCOMPLIANT": {"ar": "لا يحقق حدود القبول", "en": "Policy limits not met"},
    "PUBLIC_VALUE_POLICY_NONCOMPLIANT": {"ar": "لا يحقق الحد الأدنى لقيمة مالك الأرض", "en": "Landowner-value threshold not met"},
    "NOT_SUPPORTED": {"ar": "غير مدعوم استشارياً", "en": "Not supported"},
    "REQUIRES_REVISION": {"ar": "يحتاج إلى تعديل", "en": "Requires revision"},
    "ECONOMICALLY_INFEASIBLE": {"ar": "غير قابل للتنفيذ اقتصادياً", "en": "Economically infeasible"},
    "NUMERICALLY_UNRESOLVED": {"ar": "تعذر حسم الحساب العددي", "en": "Numerically unresolved"},
    "DATA_INSUFFICIENT": {"ar": "البيانات غير كافية", "en": "Insufficient data"},
    "LEGALLY_UNCONFIRMED": {"ar": "القابلية القانونية غير مؤكدة", "en": "Legal feasibility unconfirmed"},
    "CONTRACT_DEFINITION_INCOMPLETE": {"ar": "تعريف العقد غير مكتمل", "en": "Contract definition incomplete"},
    "AUDIT_FAILURE": {"ar": "فشل تدقيق الحساب", "en": "Calculation audit failure"},
    "PASS": {"ar": "ناجح", "en": "Pass"},
    "FAIL": {"ar": "غير ناجح", "en": "Fail"},
}


GLOSSARY_REGISTRY = {
    "cash_payer": {"label_ar": "الدافع النقدي", "label_en": "Cash payer", "short_ar": "الطرف الذي يسدد الكلفة نقدياً عند استحقاقها.", "short_en": "The party that pays the cost in cash when due.", "detail_ar": "لا يحدد وحده من يتحمل الكلفة اقتصادياً أو قابليتها للخصم من صافي المبيعات.", "detail_en": "This does not by itself determine the economic bearer or net-sales deductibility.", "classification": "input", "unit": "party"},
    "economic_bearer": {"label_ar": "المتحمل الاقتصادي", "label_en": "Economic bearer", "short_ar": "الطرف الذي تبقى عليه الكلفة اقتصادياً بعد أي استرداد أو تسوية.", "short_en": "The party that ultimately bears the cost after reimbursement or settlement.", "detail_ar": "قد يختلف عن الدافع النقدي عند وجود استرداد أو مقاصة تعاقدية.", "detail_en": "May differ from the cash payer when reimbursement or contractual set-off applies.", "classification": "input", "unit": "party"},
    "net_sales_deduction_treatment": {"label_ar": "معاملة الخصم من صافي المبيعات", "label_en": "Net-sales deduction treatment", "short_ar": "هل يسمح العقد بطرح هذا البند قبل تطبيق نسبة مالك الأرض.", "short_en": "Whether the contract allows this item to reduce the base before applying the landowner share.", "detail_ar": "لا تخصم أي كلفة تلقائياً؛ يجب تحديد القاعدة والنسبة والسقف والموافقة والدليل عند اللزوم.", "detail_en": "No cost is deducted automatically; the rule, percentage, cap, approval and evidence must be defined where required.", "formula": "eligible deduction = eligible cost × approved fraction, subject to cap", "classification": "input", "unit": "treatment"},
    "cost_sharing_simple": {"label_ar": "توزيع مسؤولية الكلفة", "label_en": "Cost allocation", "short_ar": "أدخل نسبة تحميل المطور؛ يحسب النظام نسبة مالك الأرض تلقائياً.", "short_en": "Enter the developer share; the landowner share is calculated automatically.", "detail_ar": "نسبة تحميل المطور تحدد ما يدفعه ويتحمله المطور من هذا البند. نسبة مالك الأرض تساوي 100% ناقص نسبة المطور. عند عقد صافي المبيعات يمكن تفعيل الخصم ليطرح كامل البند المؤهل من إجمالي المبيعات قبل تطبيق نسبة مالك الأرض. هذا الخيار هو تعريف تعاقدي صريح ويظهر في سجل المصالحة.", "detail_en": "The developer share determines the developer cash and economic burden for the item. The landowner share equals 100% less the developer share. Under a net-sales contract, the checkbox deducts the eligible item from gross sales before applying the landowner share. The selection is retained as an explicit contract rule in the reconciliation trail.", "formula": "landowner share = 100% - developer share; eligible net sales = gross sales - checked eligible costs", "classification": "input", "unit": "% / checkbox"},
    "opening_cash": {"label_ar": "الرصيد النقدي الافتتاحي", "label_en": "Opening cash balance", "short_ar": "النقد الموجود فعلياً في تاريخ الأساس.", "short_en": "Cash physically available on the base date.", "detail_ar": "هذا رصيد افتتاحي داخل المشروع وليس مساهمة شهرية جديدة. يظهر مرة واحدة في عمود الرصيد الافتتاحي ولا يعاد إضافته كمصدر نقدي في الشهر نفسه.", "detail_en": "This is an opening project balance, not a new monthly contribution. It appears once as opening cash and is not added again as a same-month source.", "formula": "monthly closing cash = opening cash + current receipts + current equity draw + current debt draw - current uses", "classification": "input", "unit": "currency"},
    "additional_equity_commitment": {"label_ar": "التزام حقوق ملكية إضافي غير مسحوب", "label_en": "Additional undrawn equity commitment", "short_ar": "سقف تمويل إضافي يستطيع المطور ضخه عند الحاجة.", "short_en": "Additional equity capacity available to be drawn when required.", "detail_ar": "لا يشمل الرصيد النقدي الافتتاحي. يسحب المحرك من هذا السقف فقط عندما لا تكفي التحصيلات والرصيد المتاح لتنفيذ الالتزامات وفق سياسة التمويل. يظهر كل سحب فعلي في صف الشهر كمساهمة حقوق ملكية.", "detail_en": "It excludes opening cash. The engine draws this capacity only when collections and available cash are insufficient under the funding policy. Each actual draw appears in the relevant monthly row as an equity contribution.", "formula": "remaining equity capacity = committed additional equity - cumulative monthly equity draws", "classification": "input", "unit": "currency"},
    "eligible_net_sales": {"label_ar": "صافي المبيعات المؤهل", "label_en": "Eligible net sales", "short_ar": "وعاء المبيعات بعد الاستقطاعات المسموحة تعاقدياً فقط.", "short_en": "Sales base after only contractually eligible deductions.", "detail_ar": "يُصالح شهرياً وإجمالياً مع إجمالي التحصيلات والاستقطاعات المستخدمة.", "detail_en": "Reconciled monthly and in total to gross collections and deductions used.", "formula": "gross eligible collections - eligible deductions used", "classification": "output", "unit": "currency"},
    "minimum_defensible": {"label_ar": "الحد الأدنى القابل للدفاع عنه", "label_en": "Minimum defensible consideration", "short_ar": "أدنى مقابل يمكن تبريره وفق قيمة الأرض والسياسة والأدلة المتاحة.", "short_en": "Lowest consideration supportable by land value, policy and available evidence.", "detail_ar": "ليس بالضرورة أقل رقم تفاوضي قانوناً؛ هو حد تحليلي استشاري مشروط بجودة البيانات.", "detail_en": "Not necessarily a legal tender floor; it is an advisory analytical threshold conditional on evidence quality.", "classification": "output", "unit": "contract-native"},
    "technical_ceiling": {"label_ar": "السقف الفني", "label_en": "Technical ceiling", "short_ar": "أعلى مقابل يحافظ على جميع قيود الجدوى الأساسية في السيناريو المحدد.", "short_en": "Highest consideration satisfying all core feasibility constraints in the selected scenario.", "detail_ar": "لا يصدر إذا كان الحل العددي غير محسوم، ويجب إظهار القيد الحاكم والنقطة التالية الفاشلة.", "detail_en": "Not issued when numerical resolution fails; the binding constraint and next failing point must be shown.", "classification": "output", "unit": "contract-native"},
    "risk_adjusted_ceiling": {"label_ar": "السقف المتحفظ وفق السياسة", "label_en": "Policy-adjusted ceiling", "short_ar": "سقف أقل أو مساوٍ للسقف الفني بعد تطبيق معاملات التحفظ وهامش حماية المطور المعتمدة في سياسة التقييم.", "short_en": "A ceiling at or below the technical ceiling after applying the valuation policy's conservatism and developer-protection settings.", "detail_ar": "هذا تعديل مؤسسي معلن وفق السياسة، وليس احتمالاً إحصائياً لمخاطر المشروع.", "detail_en": "This is an explicit institutional policy adjustment, not a statistical project-risk probability.", "classification": "output", "unit": "contract-native"},
    "balanced_recommendation": {"label_ar": "التوصية المتوازنة", "label_en": "Balanced recommendation", "short_ar": "مقابل داخل المجال القابل للتنفيذ يوازن قيمة مالك الأرض وهامش أمان المطور والتمويل.", "short_en": "A feasible consideration balancing landowner value, developer headroom and funding resilience.", "detail_ar": "تعرض معها الأوزان والسياسة والقيد الحاكم ولا تقدم كرقم يقيني عند ضعف الأدلة.", "detail_en": "Its policy weights and binding constraints are disclosed; it is not presented as certain when evidence is weak.", "classification": "output", "unit": "contract-native"},
    "developer_equity_irr": {"label_ar": "عائد حقوق ملكية المطور", "label_en": "Developer equity IRR", "short_ar": "العائد السنوي على مساهمات وتوزيعات حقوق ملكية المطور.", "short_en": "Annual return on developer equity contributions and distributions.", "detail_ar": "يختلف عن عائد المشروع غير الممول ولا يعرض إذا لم يكن معرفاً رياضياً.", "detail_en": "Different from unlevered project IRR and not shown when mathematically undefined.", "classification": "output", "unit": "percentage"},
    "funding_gap": {"label_ar": "فجوة التمويل", "label_en": "Funding gap", "short_ar": "أكبر عجز نقدي غير مغطى بتمويل أو حقوق ملكية معترف بها.", "short_en": "Maximum cash deficit not covered by recognized equity or committed financing.", "detail_ar": "يجب أن تكون صفراً في حالة الإقفال الناجح ما لم تسمح السياسة صراحة بخلاف ذلك.", "detail_en": "Must be zero at successful closure unless policy explicitly permits otherwise.", "classification": "output", "unit": "currency"},
    "analysis_purpose": {"label_ar": "غرض التحليل", "label_en": "Analysis purpose", "short_ar": "المسار الذي يحدد المدخلات والمخرجات المناسبة للحالة.", "short_en": "Workflow that determines the relevant inputs and outputs.", "detail_ar": "هيكلة شراكة، تقييم عرض، مقارنة عروض، أو تقييم تعديل عقد.", "detail_en": "Partnership structuring, offer assessment, bid comparison or renegotiation assessment.", "classification": "input", "unit": "mode"},
    "scenario": {"label_ar": "السيناريو", "label_en": "Scenario", "short_ar": "مجموعة معلنة من التغييرات مقارنة بالحالة الأساسية.", "short_en": "A disclosed set of changes relative to the base case.", "detail_ar": "لا تعني نسبة مرور السيناريوهات احتمال نجاح إحصائياً ما لم توجد توزيعات معايرة.", "detail_en": "Scenario pass ratio is not a statistical success probability without calibrated distributions.", "classification": "input/output", "unit": "scenario"},
}


GLOSSARY_REGISTRY.update({
    "project_name": {"label_ar": "اسم المشروع", "label_en": "Project name", "short_ar": "الاسم التعريفي الذي يظهر في التحليلات والتقارير.", "short_en": "The identifying name shown in analyses and reports.", "detail_ar": "استخدم اسماً واضحاً يميز الأرض أو المشروع دون تضمين معلومات سرية غير لازمة.", "detail_en": "Use a clear name that distinguishes the land or project without unnecessary confidential information.", "classification": "input", "unit": "text"},
    "valuation_date": {"label_ar": "تاريخ التقييم", "label_en": "Valuation date", "short_ar": "التاريخ الذي تنسب إليه القيمة والظروف السوقية.", "short_en": "The date to which value and market conditions relate.", "detail_ar": "لا تقارن قيماً من تواريخ مختلفة دون تعديل زمني موثق.", "detail_en": "Values from different dates should not be compared without an evidenced time adjustment.", "classification": "input", "unit": "date"},
    "base_date": {"label_ar": "تاريخ الأساس", "label_en": "Base date", "short_ar": "تاريخ خصم التدفقات وبدء القياس المالي.", "short_en": "The cash-flow discounting and financial measurement date.", "detail_ar": "يستخدم في XNPV وXIRR وتوقيت مساهمات رأس المال.", "detail_en": "Used for XNPV, XIRR and the timing of capital contributions.", "classification": "input", "unit": "date"},
    "land_value_baseline": {"label_ar": "مرجع قيمة الأرض", "label_en": "Land-value benchmark", "short_ar": "مرجع إدخالي للمقارنة وليس تقييماً مستقلاً ما لم تدعمه أدلة.", "short_en": "An entered comparison benchmark, not an independent valuation unless evidenced.", "detail_ar": "يجب بيان مصدره وتاريخه وأساس القيمة ودرجة الثقة.", "detail_en": "Its source, date, basis of value and confidence should be disclosed.", "classification": "input", "unit": "currency"},
    "existing_use_value": {"label_ar": "قيمة الاستخدام القائم", "label_en": "Existing-use value", "short_ar": "قيمة الأصل وفق الاستخدام القائم والحقوق الحالية.", "short_en": "Value under the existing use and current rights.", "detail_ar": "لا تتضمن افتراض تغيير تنظيمي مستقبلي إلا إذا صرح بذلك.", "detail_en": "Does not include a future planning-change assumption unless explicitly stated.", "classification": "input/output", "unit": "currency"},
    "alternative_use_value": {"label_ar": "قيمة الاستخدام البديل", "label_en": "Alternative-use value", "short_ar": "قيمة استخدام بديل قابل للتحقق قانونياً وفنياً.", "short_en": "Value of an alternative legally and physically achievable use.", "detail_ar": "يجب فصلها عن قيمة الاستخدام القائم وعن الزيادة التنظيمية الافتراضية.", "detail_en": "Should be separated from existing-use value and hypothetical planning uplift.", "classification": "input/output", "unit": "currency"},
    "far": {"label_ar": "معامل البناء FAR", "label_en": "Floor Area Ratio (FAR)", "short_ar": "نسبة المساحة الطابقية الإجمالية إلى أساس مساحة الأرض المحدد.", "short_en": "Total floor area divided by the explicitly selected land-area basis.", "detail_ar": "يجب تحديد هل يطبق على الأرض الإجمالية أو الصافية أو الاستثمارية.", "detail_en": "The model must state whether it applies to gross, net or investment land.", "formula": "GFA = FAR × selected land-area basis", "classification": "input", "unit": "ratio"},
    "bcr": {"label_ar": "نسبة إشغال الأرض BCR", "label_en": "Building Coverage Ratio (BCR)", "short_ar": "أقصى بصمة مبنية نسبة إلى أساس مساحة الأرض المحدد.", "short_en": "Maximum building footprint relative to the selected land-area basis.", "detail_ar": "لا تثبت وحدها قابلية البرنامج العمراني؛ يلزم فحص الارتفاع والارتدادات والمواقف.", "detail_en": "Does not alone prove planning feasibility; height, setbacks and parking also require review.", "formula": "maximum footprint = BCR × selected land-area basis", "classification": "input", "unit": "percentage"},
    "data_confidence": {"label_ar": "الثقة بالبيانات", "label_en": "Data confidence", "short_ar": "تقدير لجودة واكتمال وحداثة الأدلة المدخلة.", "short_en": "Assessment of the quality, completeness and recency of input evidence.", "detail_ar": "لا يحول افتراضاً غير موثق إلى حقيقة، ويجب تفسير سبب النسبة المستخدمة.", "detail_en": "Does not convert an unsupported assumption into evidence; the selected level requires rationale.", "classification": "input", "unit": "percentage"},
    "gross_sales_share": {"label_ar": "حصة من إجمالي المبيعات", "label_en": "Gross-sales share", "short_ar": "نسبة تطبق على أساس مبيعات إجمالي محدد تعاقدياً قبل استقطاعات الكلف.", "short_en": "A percentage applied to a contractually defined gross-sales base before cost deductions.", "detail_ar": "يجب تعريف الإلغاءات والخصومات والمقايضات والضرائب والوحدات المجانية.", "detail_en": "Cancellations, discounts, barter, taxes and free units require explicit treatment.", "classification": "input/output", "unit": "percentage"},
    "net_sales_share": {"label_ar": "حصة من صافي المبيعات", "label_en": "Net-sales share", "short_ar": "نسبة من المبيعات بعد الاستقطاعات المسموحة تعاقدياً فقط.", "short_en": "A share of sales after only contractually eligible deductions.", "detail_ar": "تحتاج سجل استقطاعات مغلقاً ومصالحة شهرية وإجمالية.", "detail_en": "Requires a closed deduction registry and monthly/total reconciliation.", "classification": "input/output", "unit": "percentage"},
    "profit_share": {"label_ar": "حصة من الربح", "label_en": "Profit share", "short_ar": "نسبة من ربح قابل للتوزيع وفق تعريف الكلف والتمويل والاحتياطيات.", "short_en": "A percentage of distributable profit under defined cost, financing and reserve rules.", "detail_ar": "لا يكفي مصطلح الربح وحده؛ يجب تحديد إعادة رأس المال والعائد المفضل والـTrue-up.", "detail_en": "The term profit alone is insufficient; return of capital, preferred return and true-up must be defined.", "classification": "input/output", "unit": "percentage"},
    "upfront_consideration": {"label_ar": "المقابل المقدم", "label_en": "Upfront consideration", "short_ar": "دفعة ثابتة تستحق في تاريخ محدد مقابل حق الأرض أو الشراكة.", "short_en": "A fixed payment due on a specified date for the land or partnership right.", "detail_ar": "يجب فصل القيمة الاسمية عن القيمة الحالية ومخاطر التحصيل ونقل الملكية.", "detail_en": "Nominal amount, present value, collection risk and transfer timing should be separated.", "classification": "input/output", "unit": "currency"},
    "public_discount_rate": {"label_ar": "معدل خصم مالك الأرض", "label_en": "Landowner discount rate", "short_ar": "المعدل المستخدم لتحويل التدفقات المستقبلية لمالك الأرض إلى قيمة حالية.", "short_en": "Rate used to convert future landowner cash flows to present value.", "detail_ar": "يجب أن يكون متسقاً مع أساس اسمي أو حقيقي ومعالجة المخاطر والتضخم.", "detail_en": "Must be consistent with nominal/real basis and the treatment of risk and inflation.", "classification": "input", "unit": "percentage"},
    "public_npv": {"label_ar": "القيمة الحالية لمقابل مالك الأرض", "label_en": "Landowner consideration NPV", "short_ar": "القيمة الحالية للتدفقات التعاقدية التي تخص مالك الأرض.", "short_en": "Present value of contractual cash flows attributable to the landowner.", "detail_ar": "تفصل عن الضرائب والمنافع الاجتماعية والالتزامات المحتملة لمنع الازدواج.", "detail_en": "Separated from taxes, wider benefits and contingent liabilities to prevent double counting.", "classification": "output", "unit": "currency"},
    "developer_moic": {"label_ar": "مضاعف رأس مال المطور MOIC", "label_en": "Developer MOIC", "short_ar": "إجمالي توزيعات المطور مقسوماً على مساهماته الرأسمالية.", "short_en": "Total developer distributions divided by developer equity contributions.", "detail_ar": "لا يعكس التوقيت بمفرده؛ لذلك يقرأ مع IRR ومدة الاسترداد.", "detail_en": "Does not capture timing by itself; read it with IRR and payback.", "formula": "developer distributions / developer equity contributions", "classification": "output", "unit": "multiple"},
    "constraint": {"label_ar": "حد القبول أو السياسة", "label_en": "Acceptance or policy constraint", "short_ar": "شرط يجب أن يحققه المشروع أو العقد حتى يعد قابلاً للقبول.", "short_en": "A condition the project or contract must satisfy to be acceptable.", "detail_ar": "يعرض الحد المطلوب والقيمة الفعلية والمصدر وما إذا كان القيد حاكماً للتوصية.", "detail_en": "Shows threshold, actual value, source and whether it binds the recommendation.", "classification": "input/output", "unit": "varies"},
    "offer_position": {"label_ar": "موقع العرض", "label_en": "Offer position", "short_ar": "موضع العرض الحالي مقارنة بالحد الأدنى والتوصية والسقوف.", "short_en": "Where the current offer sits relative to the minimum, recommendation and ceilings.", "detail_ar": "لا يعتمد على النسبة الاسمية وحدها بل على المقياس التعاقدي والقيمة الحالية والجدوى.", "detail_en": "Based on the contract-native measure, present value and feasibility, not nominal percentage alone.", "classification": "output", "unit": "classification"},
})


def registry_snapshot() -> dict:
    # Imported lazily to avoid making the calculation registry depend on the
    # presentation package during kernel startup.
    from landvalue360_server.constraint_registry import CONSTRAINT_REGISTRY

    return deepcopy({
        "versions": {
            "formula_registry": FORMULA_REGISTRY_VERSION,
            "contract_registry": CONTRACT_REGISTRY_VERSION,
            "metric_dictionary": METRIC_DICTIONARY_VERSION,
        },
        "contracts": CONTRACT_DEFINITIONS,
        "net_sales_deductions": NET_SALES_DEDUCTION_REGISTRY,
        "eligible_costs": ELIGIBLE_COST_REGISTRY,
        "risk_treatments": RISK_TREATMENT_REGISTER,
        "formulas": FORMULA_REGISTRY,
        "metrics": METRIC_DICTIONARY,
        "contract_safeguards": CONTRACT_SAFEGUARDS,
        "syrian_jurisdiction": SYRIAN_JURISDICTION_PROFILE,
        "analysis_purposes": ANALYSIS_PURPOSES,
        "scenario_definitions": SCENARIO_DEFINITIONS,
        "status_labels": STATUS_LABELS,
        "constraint_definitions": CONSTRAINT_REGISTRY,
        "glossary": GLOSSARY_REGISTRY,
    })
