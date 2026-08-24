# تكامل API المالي

## المسارات الأساسية

- `GET /api/projects/{project_id}/financial`: المدخلات والسياسة والمحرك وآخر تشغيل.
- `PUT /api/projects/{project_id}/financial`: حفظ النموذج المالي على Project Version قابلة للتعديل.
- `POST /api/projects/{project_id}/financial/runs`: إنشاء Financial Analysis Calculation Run.
- `GET /api/projects/{project_id}/financial/runs`: سجل التشغيلات.
- `GET /api/projects/{project_id}/financial/runs/{run_id}`: نتيجة تشغيل مع التدفق الشهري.
- `GET .../report.pdf` و`GET .../report.xlsx`: تقارير التشغيل المؤرّخ.
- `GET /api/admin/financial-policy`: السياسة والإصدارات والمحركات.
- `POST /api/admin/financial-policy/versions`: نشر Policy Version جديدة.

## عقد النسب

جميع نسب API قيم عشرية Canonical. `0.10` = 10%، `1` = 100%، `2` = 200%. لا يطبّق الخادم تخميناً تلقائياً لتحويل الأرقام الأكبر من 1 إلى نسب مئوية.

## الثبات

أي Run تربط Project Version وPolicy Version وEngine Version وتحفظ Input Snapshot كاملة. تعديل المسودة بعد التشغيل لا يغيّر Source Project Snapshot Hash أو Effective Project Input Hash لذلك التشغيل.
