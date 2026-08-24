# دليل تسليم ونشر LandValue360 Financial Portal v2.5.0

## ملاحظات خاصة بالإصدار 2.5.0

- لا توجد Migration جديدة؛ يبقى Alembic head عند `0007_admin_governance_and_security`.
- التحديث يغيّر الواجهة والتقرير ومسار Workflow فقط، ولا يغيّر معادلات المحرك المالي أو Contract Engine 3.1.0.
- أعد تشغيل Calculation Run للمشروع إذا أردت التقرير الجديد والمجال التفاوضي الديناميكي؛ التشغيلات التاريخية تبقى ثابتة.
- مساحة المحلل/إدارة حالة المشروع لم تعد جزءاً من رحلة البورتال المبسطة.
- تحقق بعد النشر من اختيار سياسات منشورة متعددة ومن أن المستخدم يستطيع اختيارها قبل التحليل.

## 1. حالة الإصدار

الحزمة جاهزة للرفع إلى بيئة Staging ثم Production بعد ضبط البنية التحتية والأسرار. لم يتم نشرها على الدومين أو قاعدة البيانات الإنتاجية ضمن بيئة البناء.

- Portal Version: `2.5.0`
- Financial Engine: `2.1.1`
- Portal Adapter: `2.5.0`
- Contract Engine Semantics: `3.1.0`
- Database Migration Head: `0007_admin_governance_and_security`
- Financial Policy Schema: `financial-policy-controls-2.4.0`

## 2. ما يجب نسخه احتياطياً قبل الترقية

نفّذ النسخ الاحتياطي من الخادم الحي، وليس من الحزمة الجديدة:

```bash
./scripts/pre_deploy_backup.sh
```

أو على Windows/PowerShell:

```powershell
.\scripts\pre_deploy_backup.ps1
```

يجب حفظ الآتي خارج الخادم:

1. نسخة كاملة من Source Code الحي.
2. PostgreSQL dump قابل للاستعادة.
3. نسخة من Private Object Storage.
4. ملفات `.env` والأسرار وإعدادات Caddy/Reverse Proxy.
5. سجل إصدار التطبيق ورأس Alembic الحالي.

## 3. النشر في مسار جديد

لا تستبدل مجلد الإنتاج مباشرة. فك الحزمة في مسار إصدار مستقل، مثلاً:

```text
/opt/landvalue360/releases/2.5.0
```

ثم تحقق من سلامة الملفات:

```bash
cd /opt/landvalue360/releases/2.5.0
python scripts/verify_release.py
```

## 4. متغيرات الإنتاج

استخدم `.env.production.example` كأساس، واضبط خصوصاً:

- `LV360_PORTAL_ENV=production`
- `LV360_PORTAL_SECRET_KEY`
- PostgreSQL connection URL
- Private S3/MinIO credentials and bucket
- SMTP settings
- Trusted hosts
- Secure cookie settings
- Public base URL/domain

لا تستخدم أسرار التطوير المضمنة في الاختبارات.

## 5. قاعدة البيانات

جرّب الترحيل أولاً على نسخة Staging مستعادة من Production:

```bash
alembic current
alembic upgrade head
alembic current
```

رأس الإصدار النهائي:

```text
0007_admin_governance_and_security
```

لا يحتاج v2.5.0 جدولاً جديداً؛ ترقية السياسة تتم تطبيقياً. عند أول تشغيل، إذا كانت السياسة الحالية من إصدار أقدم، ينشئ النظام Policy Version صريحة وفق Schema 2.4.0، يجعلها الافتراضية، ويحفظ النسخة القديمة مؤرشفة للتدقيق. Calculation Runs التاريخية لا تتغير.

## 6. فحوص ما قبل التشغيل

```bash
python -m compileall -q app scripts
python scripts/validate_golden_cases.py
python scripts/validate_v250_scenarios.py
python scripts/validate_policy_negotiation_v250.py
python scripts/security_scan.py
python scripts/validate_report_artifacts.py
```

## 7. تشغيل الخدمة

عبر Docker Compose:

```bash
docker compose build --pull
docker compose up -d db minio minio-init
docker compose run --rm app alembic upgrade head
docker compose up -d app notification-worker caddy
```

تحقق من الصحة:

```text
/api/health/live
/api/health/ready
```

يجب أن يعيدا HTTP 200.

## 8. فحص وظيفي إلزامي في Staging

1. تسجيل الدخول بحساب Platform Admin.
2. فتح مكتبة السياسات المالية.
3. استنساخ سياسة، تعديل اسمها وافتراض واحد، ونشرها كنسخة User Selectable.
4. تسجيل الدخول بحساب مستخدم عادي والتأكد من ظهور النسختين في Policy Selector.
5. تشغيل المشروع المرجعي تحت كل سياسة.
6. التأكد أن Balanced/Policy Ceiling يتغيران، بينما Technical Ceiling يبقى ناتج الجدوى الفنية ذاتها.
7. تنزيل PDF وExcel.
8. التحقق من ظهور Policy Version وProject Version وEngine Version في المراجع الفنية.
9. اختبار مشروع المستخدم من حساب الأدمن وتنزيل تقريره.
10. اختبار Reset Password وإبطال الجلسات.

## 9. نقاط القبول المالي

للمشروع المرجعي القديم يجب أن تكون المؤشرات الاسمية:

```text
Gross Sales                 877,500,000 USD
Development Cost            556,522,674 USD
18% Gross Sales Share       157,950,000 USD
Developer Profit            163,027,326 USD
```

ونطاق السياسة القياسية يقارب:

```text
Fair Floor                  9.5%-10.0%
Balanced                    12.3%
Policy-Adjusted Ceiling     14.1%
Residual Equivalent         16.58%
Current Offer               18.0%
Technical Ceiling           19.9%
```

فروقات IRR/NPV قد تتغير فقط عند اختلاف التوقيت الشهري أو منحنيات التحصيل والكلف أو Discount Rate بين السياسات.

## 10. Rollback

عند فشل النشر:

1. أوقف إصدار 2.5.0.
2. أعد توجيه Reverse Proxy إلى الإصدار السابق.
3. استعد قاعدة البيانات فقط إذا طُبقت تغييرات بيانات لا يمكن التعايش معها؛ لا تنفذ Downgrade عشوائياً.
4. استعد Object Storage عند الحاجة.
5. وثق سبب Rollback ووقت التنفيذ.

إنشاء Policy Version جديدة عند أول تشغيل لا يغير التشغيلات التاريخية، ويمكن إبقاء النسخة الجديدة مؤرشفة إذا تقرر الرجوع وظيفياً.
