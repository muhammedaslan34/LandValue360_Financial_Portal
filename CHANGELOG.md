# LandValue360 Financial Portal v2.5.0

## هدف الإصدار

يثبت الإصدار 2.5.0 واجهة الإطلاق النهائية للبورتال المبسط، مع إبقاء التحليل المالي كاملاً، وإزالة عناصر Workflow غير اللازمة، وتحويل المجال التفاوضي والتقرير المطبوع إلى أدوات قرار واضحة وقابلة للدفاع.

## تحسين المجال التفاوضي

- يبدأ المحور بصرياً من **الحد الأدنى المقبول** بدلاً من الصفر.
- يتوسع المحور ديناميكياً حتى أعلى قيمة مؤثرة: السقف الفني أو العرض الحالي أو القيمة المتبقية المكافئة.
- أضيف توزيع تلقائي للعلامات على عدة مستويات لمنع تداخل التسميات.
- يظهر العرض الواقع تحت الحد الأدنى أو فوق السقف الفني خارج النطاق الموصى به بصورة صريحة.
- استبدلت التسمية العربية «الحد العادل» بـ **«الحد الأدنى المقبول»** في الواجهة والتقرير.
- أضيفت منطقة موصى بها من الحد الأدنى المقبول إلى السقف المتحفظ، ومنطقة قدرة فنية حتى السقف الفني، ومنطقة تجاوز تحذيرية.

## تفسير الحدود والتوصية

أصبح أسفل الرسم قسم «كيف تم تحديد هذا النطاق؟» ويشرح بالأرقام الفعلية:

- الحد الأدنى المقبول وLandowner NPV مقابل الحد المطلوب.
- النقطة المتوازنة وDeveloper IRR وLandowner NPV وفجوة التمويل عندها.
- القيمة المتبقية المكافئة ومعادلة تحويلها إلى نسبة على وعاء العقد المختار.
- السقف المتحفظ وفق السياسة وعامل القدرة المطبق.
- السقف الفني والقيد الحاكم وقيمته وحده المطلوب.
- العرض الحالي ومقابل صاحب الأرض وNPV وDeveloper IRR وموقعه من النطاق.

التفاصيل الفنية الإضافية بقيت قابلة للفتح، بينما القراءة الأساسية تبقى بسيطة للمستخدم غير المالي.

## تبسيط البورتال الحالي

- أزيلت «مساحة المحلل» وإدارة حالة المشروع من تنقلات البورتال الحالي.
- روابط Workflow القديمة تعيد التوجيه إلى الإدارة أو التحليل المالي بدلاً من عرض مساحة تشغيل مستقلة.
- بقيت صلاحيات المحلل المالية المتقدمة متاحة عند الحاجة من دون إظهار Workflow لا يخدم نموذج البورتال الحالي.

## السياسات والافتراضات

- احتفظ الإصدار بمكتبة Financial Policy Versions غير القابلة للتعديل بأثر رجعي.
- يستطيع الأدمن استنساخ أي سياسة، تعديل افتراضاتها، نشرها، تفعيلها، أرشفتها وإعادة نشرها.
- يختار المستخدم من السياسات المنشورة والمسموح بها قبل التحليل.
- كل تشغيل يحتفظ نهائياً بنسخة المشروع والسياسة والمحرك وInput Hash وResult Hash.
- أصلح عرض الأرقام العشرية في لوحة السياسات، مثل `56.00000000000001` لتظهر `56`.
- أصلح حقل شدة منحنى المبيعات ليقبل القيمة المحايدة `1` وأي قيمة عشرية صحيحة وفق تحقق الخادم.

## التقرير المطبوع

- أعيد تصميم تقرير PDF ليعكس أسلوب البورتال الرسمي والبسيط.
- أضيف الرسم الديناميكي نفسه إلى التقرير.
- أضيف شرح كل حد وحسابه ومؤشراته والقيد الحاكم.
- أصبح التقرير 11 صفحة A4 تشمل الملخص التنفيذي، اقتصاديات المشروع والمطور وصاحب الأرض، تقييم الأرض، التفاوض، المقارنة بين العقود، التدفقات والمراجعة الفنية ودليل المؤشرات.
- بقي Excel ملحقاً تحليلياً من خمس أوراق.

## التوافق المالي

- Portal: `2.5.0`
- Platform Monthly Engine: `2.1.1`
- Portal Financial Adapter: `2.5.0`
- Contract Semantics: `3.1.0`
- Policy Schema: `financial-policy-controls-2.4.0`
- Alembic Head: `0007_admin_governance_and_security`

لا توجد Migration جديدة. لم تتغير معادلات المحرك أو تعريفات العقود؛ التغييرات تخص الواجهة والتقرير وWorkflow وعرض السياسات. التشغيلات القديمة لا تتغير بأثر رجعي.
# LandValue360 Financial Portal v2.4.0

## Financial corrections

- Separated **Fair Floor**, **Balanced**, **Policy-Adjusted Ceiling**, **Residual Equivalent**, **Current Offer**, and **Technical Ceiling** as distinct negotiation references.
- Recalibrated the Balanced recommendation against the legacy-platform reference project: 9.5%-10.0% Fair Floor, 12.3% Balanced, 14.1% policy-adjusted ceiling, 16.58% residual equivalent, 18% current offer, and 19.9% technical ceiling.
- Preserved Contract Engine 3.x semantics: Gross Sales Share applies only to gross collections; Net Sales Share applies only to eligible net collections after sales-side deductions; Profit Share applies only to distributable cash profit.
- Added explicit distinction between a true Technical Ceiling and an administrator-defined policy search cap.
- Added Residual Land Value as a monetary and equivalent-rate comparison marker without treating it as an independent market valuation or contractual entitlement.
- Re-runs the monthly financial model for each negotiation candidate and rejects candidates that fail profitability, return, liquidity, payment, close-out, or reconciliation constraints.

## Versioned financial policy library

- Administrators can clone, edit, publish, activate, archive, and republish immutable financial policy versions.
- Published versions may be marked user-selectable; standard users choose a policy before running the analysis.
- Every Calculation Run permanently freezes the selected Project Version, Policy Version, Engine Version, Input Hash, and Result Hash.
- Historical runs remain linked to their original policy even if the policy is later archived.
- v2.3 policies are materialized into explicit v2.4 policy snapshots on upgrade; the historical source version remains preserved.
- All policy-governed assumptions are editable by the administrator: discount and return thresholds, landowner recovery rules, allowed contracts, negotiation positioning, financing, liquidity, sales/cost curves, collection rules, spending limits, distributions, and solver controls.
- Core integrity invariants remain locked: no uncovered negative cash, zero terminal debt, zero deferred development cost, zero contractual arrears, and mandatory monthly cash reconciliation.

## User experience and reporting

- Standard users retain simple project inputs while receiving the complete financial feasibility analysis.
- Analysts and administrators retain controlled access to advanced financial inputs.
- Financial policy selection is shown before analysis and in every result/report.
- Negotiation charts and tables include policy ceiling and residual comparison markers.
- Arabic and English labels were completed for new negotiation and policy controls.
- PDF and Excel reports identify the selected immutable policy version and the contract calculation basis.

## Validation

- 51 Python tests passed; 1 optional browser test skipped by environment flag.
- Release Gate: 34/34 checks passed.
- Platform golden cases: 14/14 passed.
- Platform core parity: 104/104 files matched; 0 changed, missing, or extra.
- Contract scenario matrix: 8 projects, 144 candidate points, 513 independent audit checks, all passed.
- Policy/negotiation end-to-end matrix: 49/49 assertions across 10 portal scenarios, all passed.
- SQLite migration, PostgreSQL offline migration, wheel installation, live HTTP smoke, PDF/Excel validation, and static security scan passed.

---

# LandValue360 Financial Portal 2.4.0

## هدف الإصدار

الإصدار 2.4.0 يثبت البورتال كنسخة تشغيلية ذات سياسات مالية متعددة وقابلة للاختيار، ويصحح منطق النقطة المتوازنة بحيث تبقى مستقلة عن السقف الفني. يحافظ الإصدار على المحرك الشهري المثبت من Platform 2.1.1 وعلى جميع خصائص الإدارة والأمان والتقارير والإرشاد السياقي الموجودة في 2.3.0.

## أضيف

- إدارة كاملة لافتراضات التحليل من حساب الأدمن ضمن `Financial Policy Versions` مستقلة وثابتة.
- أسماء وأوصاف عربية وإنكليزية لكل سياسة.
- نشر سياسة للمستخدمين أو إبقاؤها داخلية للأدمن/المحلل.
- اختيار المستخدم لنسخة السياسة المنشورة قبل الحفظ أو تشغيل التحليل.
- تفعيل أي نسخة منشورة كسياسة افتراضية للمنصة.
- أرشفة وإعادة نشر النسخ مع منع أرشفة السياسة الافتراضية الفعالة.
- ربط كل Calculation Run نهائياً بنسخة السياسة المختارة، مع حفظ الاسم والحالة والوصف والـsnapshot hash.
- افتراضات قابلة للإدارة تشمل حدود IRR/NPV/MOIC والربحية، الخصم، قيمة صاحب الأرض، مجال البحث، منهج التفاوض، التمويل، منحنيات البيع والكلف، التحصيل، السيولة، التوزيعات، الاحتياطيات، التصاعد والـcontingency وإعدادات الحل.
- مقارنة القيمة المتبقية للأرض مع Fair Floor وBalanced والسقف المتحفظ والسقف الفني والعرض الحالي.
- اختبارات Regression لحالة البلاتفورم القديم ومصفوفة مشاريع منخفضة ومرتفعة القيمة والربحية والخسارة والتمويل وآليات Gross/Net/Profit Share.

## صُحح

- لم تعد النقطة المتوازنة تساوي السقف الفني أو تُرفع إليه لمجرد بقاء المشروع قابلاً للتنفيذ.
- أصبح السقف الفني حداً اقتصادياً بحتاً تحدده أول مخالفة فعلية للقيود.
- أصبح السقف المتحفظ وفق السياسة نقطة مستقلة بين Fair Floor والسقف الفني.
- أصبحت Balanced موضعاً معلناً داخل المجال من Fair Floor إلى السقف المتحفظ، وفق معامل محفوظ في السياسة المختارة.
- بقي Policy Cap منفصلاً عن Technical Ceiling ولا يعرض كأنه سقف اقتصادي مكتشف.
- بقيت Gross Sales Share وNet Sales Share وProfit Share مرتبطة فقط بأوعيتها الاقتصادية المعلنة.

## التوافق المالي

- Application: `2.4.0`
- Platform monthly engine: `2.1.1`
- Portal financial adapter: `2.4.0`
- Contract semantics: `3.1.0`
- Alembic head: `0007_admin_governance_and_security`
- لا توجد Migration جديدة؛ افتراضات السياسات تحفظ داخل snapshots مؤرخة في الجداول الموجودة.
- التشغيلات القديمة لا تتغير. يلزم تشغيل Calculation Run جديد لاستخدام سياسة أو منطق توصية جديد.
