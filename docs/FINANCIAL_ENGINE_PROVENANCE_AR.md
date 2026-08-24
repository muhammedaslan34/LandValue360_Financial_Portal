# Provenance المحرك المالي

## المصدر

النواة الحسابية من الأرشيف المرفق `LandValue360_Platform_v2.1.1_PORTAL_INTEGRATION_STABLE(2).zip`. الحزمة تحتفظ بـ `CORE_PROVENANCE.json` الذي يسجل SHA-256 لأرشيف البلاتفورم وأرشيف البورتال الأساسي، وإصدار المحرك والـ Adapter.

## التطابق

`verify_platform_core_parity.py` يقارن الملفات المضمّنة بالمصدر. ملفات Migration الخاصة بالبلاتفورم مستثناة عمداً؛ بقية ملفات النواة المضمّنة يجب أن تتطابق بايتاً ببايت.

## Source Hash

`engine_source_hash()` يحسب Hash من Adapter البورتال وبنية التحويل وجميع حزم النواة المضمّنة. لا يسمح بالتنفيذ إذا كان سجل Engine Version لا يطابق هذا الـ Hash.

## Golden Cases

Fixtures الأصلية محفوظة داخل `validation/golden_cases`. `scripts/validate_golden_cases.py` يعيد تشغيلها ويقارن النتائج المرجعية.

## ثبات التشغيل

كل Calculation Run تحفظ المدخلات الفعلية بعد تطبيق الافتراضات، Policy Snapshot، Engine Manifest، Input Hash وResult Hash. التقارير تُبنى من التشغيل المخزن، لا من حالة المشروع الحالية.
