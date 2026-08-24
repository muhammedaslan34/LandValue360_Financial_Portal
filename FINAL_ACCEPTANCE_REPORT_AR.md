# تقرير القبول النهائي - LandValue360 Financial Portal v2.5.0

## القرار

**READY FOR STAGING AND CONTROLLED DOMAIN DEPLOYMENT**

تم قبول الإصدار بعد مراجعة الواجهة، المجال التفاوضي، السياسات المالية، التقارير، المحرك المالي، الترحيلات، الأمان، التشغيل الفعلي والحزمة المستخرجة.

## نطاق القبول

- الاحتفاظ بجميع وظائف v2.4.0 المالية والإدارية والأمنية.
- بدء المجال التفاوضي من الحد الأدنى المقبول بدلاً من الصفر.
- منع تداخل العلامات مع توزيعها على مستويات متعددة.
- استبدال «الحد العادل» بـ«الحد الأدنى المقبول» في العربية.
- تفسير كل حد بالأرقام وطريقة الحساب والقيد الحاكم.
- مقارنة Residual Land Value كمبلغ ونسبة مكافئة على وعاء العقد نفسه.
- إزالة مساحة المحلل وإدارة الحالة من رحلة البورتال المبسطة.
- استمرار إدارة جميع الافتراضات من حساب الأدمن ضمن نسخ سياسات ثابتة وقابلة للاختيار.
- إصلاح إدخال Sales Curve Intensity = 1 وتنظيف آثار Floating Point في حقول السياسات.
- تقرير PDF رسمي من 11 صفحة وExcel من خمس أوراق.
- عدم تغيير معادلات المحرك أو Contract Engine 3.1.0.

## الإصدارات

- Portal `2.5.0`
- Platform Monthly Engine `2.1.1`
- Portal Adapter `2.5.0`
- Contract Semantics `3.1.0`
- Policy Schema `financial-policy-controls-2.4.0`
- Alembic Head `0007_admin_governance_and_security`

## نتيجة التحقق

- Release Gate: `38/38 PASS`

- Python non-browser tests: `56 PASS`
- Browser E2E: `1 SKIPPED` افتراضياً ويتطلب Chromium غير مقيد
- JavaScript syntax: `6/6 PASS`
- Policy/Negotiation current-code scenarios: `49/49 PASS` عبر `10` حالات
- Contract regression matrix: `513/513 PASS`، `144` نقطة، `8` مشاريع
- Golden Cases: `14/14 PASS`
- Platform Core: `104/104 MATCHED`
- Fresh SQLite Migration: `PASS` - 47 جدولاً
- PostgreSQL Offline Migration: `PASS` - 91,941 bytes
- Static Security: `0 High / 0 Medium`
- Live Uvicorn HTTP Smoke: `PASS`
- PDF structural and visual QA: `PASS` - 11 صفحات A4
- Excel structural QA: `PASS` - 5 أوراق، 0 أخطاء صيغ، 0 روابط خارجية

## حدود القبول

لم يتم نشر الحزمة على الدومين أو اختبارها على قاعدة PostgreSQL الإنتاجية الفعلية لعدم وجود أسرار الإنتاج وبيانات الوصول. القبول يعني جاهزية الحزمة لـStaging والنشر المنضبط بعد النسخ الاحتياطي وضبط PostgreSQL والتخزين الخاص وSMTP وDNS وTLS.
