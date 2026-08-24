# دليل النشر - LandValue360 Financial Portal v2.5.0

هذا الدليل مختصر. المرجع التنفيذي الكامل هو `DEPLOYMENT_HANDOFF_AR.md` في جذر الحزمة.

## مكونات الإصدار

- FastAPI مع PostgreSQL للإنتاج وSQLite للاختبار المحلي.
- Platform Monthly Engine `2.1.1`.
- Portal Adapter `2.4.0`.
- Contract Semantics `3.1.0`.
- Policy Schema `financial-policy-controls-2.4.0`.
- Alembic head `0007_admin_governance_and_security`.
- نسخ سياسات مالية ثابتة ينشرها الأدمن ويختار المستخدم منها.
- تقارير PDF/Excel، تدقيق مالي، وصول إداري شامل، إدارة حسابات وجلسات، ومساعدة سياقية ثنائية اللغة.

## قبل الترقية

نفذ نسخة احتياطية كاملة للكود وPostgreSQL والتخزين الخاص ومتغيرات البيئة. لا تطور مباشرة على المسار الحي.

Linux:

```bash
bash scripts/pre_deploy_backup.sh /secure/off-server/backups
```

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\pre_deploy_backup.ps1 -OutputRoot D:\SecureBackups
```

## فحص الحزمة

```bash
sha256sum -c LandValue360_Financial_Portal_v2.5.0_READY.zip.sha256
unzip -t LandValue360_Financial_Portal_v2.5.0_READY.zip
unzip LandValue360_Financial_Portal_v2.5.0_READY.zip
cd LandValue360_Financial_Portal_v2.5.0_READY
python scripts/verify_release.py
python scripts/validate_policy_negotiation_v250.py
alembic upgrade head
```

## تشغيل محلي على Windows

```powershell
Expand-Archive .\LandValue360_Financial_Portal_v2.5.0_READY.zip -DestinationPath .\lv360-v2.5.0
cd .\lv360-v2.5.0\LandValue360_Financial_Portal_v2.5.0_READY
.\START_PORTAL.bat
```

## نشر Docker

```bash
cp .env.production.example .env.production
# اضبط الأسرار والدومين وPostgreSQL وS3/MinIO وSMTP
docker compose --env-file .env.production build --pull
docker compose --env-file .env.production up -d db minio minio-init
docker compose --env-file .env.production run --rm app alembic upgrade head
docker compose --env-file .env.production up -d app notification-worker caddy
curl -f https://YOUR_DOMAIN/api/health/live
curl -f https://YOUR_DOMAIN/api/health/ready
```

## تحقق Staging الإلزامي

- إنشاء Policy Version جديدة من حساب الأدمن، تعديل افتراضات تمثيلية، نشرها واختيارها من حساب مستخدم.
- تشغيل نفس المشروع على سياستين والتحقق من تغير Balanced والسقف المتحفظ مع ثبات الاقتصاديات الاسمية والسقف الفني عند عدم تغير القيود الأساسية.
- التحقق من بقاء التشغيلات القديمة مرتبطة بنسخ السياسات الأصلية.
- تشغيل مرجع البلاتفورم القديم ومراجعة النطاق التقريبي: 9.6% / 12.0% / 13.8% / 19.9% تحت السياسة القياسية.
- تنزيل PDF وExcel والتحقق من اسم ورقم السياسة والتدفقات واللغة.

## Rollback

أعد نشر نسخة التطبيق السابقة من مسار مستقل، ولا تحذف نسخ السياسات أو التشغيلات الجديدة يدوياً. لا تنفذ Alembic downgrade على الإنتاج دون خطة استعادة مختبرة.
