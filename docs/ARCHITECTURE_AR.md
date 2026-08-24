# البنية المعمارية - LandValue360 Financial Portal v2.5.0

## الطبقات

```text
Browser / RTL-LTR UI
       |
FastAPI + RBAC + CSRF + Audit
       |
Project Versioning + Financial Policy Versioning
       |
Portal Adapter 2.4.0 + Contract Semantics 3.1.0
       |
Vendored Platform Monthly Calculation Core 2.1.1
       |
PostgreSQL + Private S3/MinIO
```

## مصدر الحقيقة الحسابي

- `ProjectVersion.input_snapshot`: مدخلات المشروع المحفوظة.
- `FinancialPolicyVersion.policy_snapshot`: نسخة ثابتة من جميع الافتراضات والسياسات المعتمدة وقت التشغيل.
- `EngineVersion`: إصدار المحرك والـAdapter والـSource Hash القابل للتنفيذ.
- `CalculationRun.input_snapshot`: نسخة مجمدة من كل مدخل فعلي، بما فيه السياسة المختارة والافتراضات المولدة وتاريخ التقييم.
- `CalculationRunResult` و`MonthlyCashflowSnapshot`: النتيجة المعتمدة والتدفق الشهري.
- `NegotiationResult`: نتائج كل آلية، ووعاء الحساب، وFair Floor وBalanced والسقف المتحفظ والسقف الفني وResidual Equivalent.

لا تعتمد أي نتيجة مالية معتمدة على JavaScript. الواجهة تعرض Preview وتستدعي API؛ الحساب والحفظ والتدقيق تتم على الخادم.

## دورة التشغيل

1. اختيار Project Version ثابتة أو المسودة الحالية.
2. اختيار Financial Policy Version منشورة ومسموح بها للمستخدم.
3. تطبيق نسخة السياسة المختارة على المدخلات، بما يشمل الافتراضات الخفية للمستخدم العادي.
4. Materialize الافتراضات المالية الناقصة وتجميدها داخل Input Snapshot.
5. التحقق من Engine Version وSource Hash.
6. تشغيل نموذج مالي شهري واحد.
7. حساب Pre-Land Case مستقلة للـResidual Land Value.
8. إعادة تشغيل Candidate Runs داخلية لكل آلية تعاقدية على وعائها المعلن.
9. اختبار القيود وإيجاد Fair Floor وTechnical Ceiling الحقيقيين.
10. تطبيق عوامل السياسة لحساب Balanced وPolicy-Adjusted Ceiling بصورة مستقلة عن السقف الفني.
11. تحويل Residual Land Value إلى Residual Equivalent للمقارنة، دون اعتباره تقييماً سوقياً مستقلاً.
12. حفظ النتائج والبصمات والتدفقات والتفاوض ونسخة السياسة.
13. توليد PDF/Excel من Calculation Run المخزنة، لا من المدخلات الحالية.

## حوكمة السياسات

- كل تعديل ينشئ Policy Version جديدة؛ لا تعدل النسخ المنشورة أو التاريخية بأثر رجعي.
- الأدمن يستطيع الاستنساخ، التعديل، النشر، جعل النسخة افتراضية، الأرشفة وإعادة النشر.
- المستخدم العادي يرى فقط النسخ المنشورة والمعلّمة `user_selectable`.
- كل تشغيل يحتفظ باسم ووصف ورقم وحالة وبصمة نسخة السياسة المختارة.
- القواعد الإلزامية لسلامة المحرك لا يمكن تعطيلها من السياسة: منع الكاش السالب غير المغطى، صفر Terminal Debt، صفر Deferred Costs، صفر Contractual Arrears، والمصالحة الشهرية.

## جداول الإصدار المالي

- `financial_policies`
- `financial_policy_versions`
- `engine_versions`
- `calculation_runs`
- `calculation_run_results`
- `monthly_cashflow_snapshots`
- `negotiation_results`

## العلاقة مع البلاتفورم

النواة الحسابية المضمّنة من LandValue360 Platform 2.1.1 مثبتة داخل الحزمة مع Provenance وSource Hash. البلاتفورم يبقى مرجعاً للمحرك المتقدم. تصدير `.lv360` الداخلي ينقل المدخلات الزمنية والتمويلية والتعاقدية الفعلية إلى البلاتفورم لإعادة الحساب والوظائف المتقدمة.
