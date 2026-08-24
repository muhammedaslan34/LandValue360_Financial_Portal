# دليل المحلل المالي

## تجهيز المشروع

راجع المساحات والمنتجات والأسعار والكلف والمصادر. افتح قسم Financial Portal وحدد:

- تاريخ التقييم.
- بداية ومدة ومنحنى البيع.
- خطة التحصيل الشهرية.
- جدول التنفيذ والكلف والاحتياطي والتصاعد.
- النقد الافتتاحي وEquity والتمويل الملتزم.
- الفائدة والرسوم وسياسة السحب والإنفاق.
- الآلية التعاقدية الأساسية ومدخلاتها.

النسخة المرسلة Immutable. أي تعديل جوهري عليها يحتاج Revision جديدة.

## تشغيل التحليل المالي

التشغيل ينفذ نموذجاً شهرياً واحداً ويختبر الآليات التعاقدية داخلياً. راجع قبل اعتماد النتيجة:

- Calculation Status وPolicy Compliance.
- Monthly Cash Reconciliation.
- Peak Funding Gap وPeak Equity وPeak Debt.
- Terminal Debt وDeferred Cost وContractual Arrears.
- Project Profit وDeveloper Profit.
- Developer Equity IRR/NPV/MOIC.
- مدة المشروع المعدلة وتاريخ الإكمال.

## Residual Land Value

القيمة تحسب من حالة قبل مقابل الأرض:

`GDV / (1 + Target Developer Profit on Cost) - Development Costs - Finance Costs`

وLand Capacity DCF هي NPV للتدفق قبل الأرض. كلاهما Development Residual Indication وليس Market Valuation.

## المجال التفاوضي

- Fair Floor: الحد الأدنى الذي يحقق قيمة صاحب الأرض المطلوبة.
- Balanced: النقطة التي تحقق العائد المستهدف للمطور.
- Technical Ceiling: أعلى مقابل قبل فشل أحد القيود.
- Governing Constraint: القيد الذي يحدد السقف.

## التقارير

نزّل PDF وExcel من سجل التشغيل المحدد. التقرير مرتبط بـ Project Version وPolicy Version وEngine Version والـ hashes نفسها. لا تستخدم Screenshot أو أرقام من واجهة تشغيل أخرى بوصفها تقريراً معتمداً.
