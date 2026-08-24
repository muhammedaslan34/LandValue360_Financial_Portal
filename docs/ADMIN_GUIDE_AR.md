# دليل مدير المنصة

## الصلاحية

إدارة Financial Policy محصورة بحساب `PLATFORM_ADMIN`. بقية وظائف المستخدمين والمؤسسات والعضويات والإعدادات تتبع RBAC الحالي.

## Financial Policy Management

كل عملية نشر تنشئ `FinancialPolicyVersion` جديدة وثابتة. لا تعدّل نسخة موجودة ولا تعيد احتساب التشغيلات القديمة.

الحقول المتاحة:

- Discount Rate وLandowner Discount Rate.
- Minimum/Target Developer IRR.
- Minimum Developer NPV وMinimum Project NPV.
- Minimum/Target Profit on Cost.
- Minimum Developer MOIC.
- Maximum Funding Gap.
- Minimum Landowner NPV وMinimum Value Recovery.
- الحد الأدنى والأقصى لحصة صاحب الأرض وSearch Tolerance.
- آليات التعاقد المسموحة.
- كلف Net Sales القابلة للخصم وكلف Profit Share.
- السماح بالتمويل وتأجيل الكلف غير المغطاة.
- منحنيات البيع والكلفة وخطة التحصيل الافتراضية.
- Funding Draw Order وSpend Policy وتأجيل الالتزامات التعاقدية افتراضياً.
- طريقة اختيار المقترح: Balanced أو Maximum Landowner Value.

## قيود لا يمكن تعطيلها

- لا كاش سالب غير مغطى.
- Terminal Debt = 0.
- Deferred Cost = 0.
- Contractual Arrears = 0.
- Monthly Cash Reconciliation.

## قواعد الإدخال

API يستخدم النسب العشرية: `0.25` تعني 25% و`2` تعني 200%. الواجهة تحول النسبة المئوية المعروضة إلى قيمة عشرية قبل الإرسال. يرفض الخادم الحدود المتعارضة والأوزان السالبة والقيم خارج النطاق.

## Engine Versions

لا ينفذ البورتال أي Engine Version ما لم تتطابق Engine Version وAdapter Version وSource Hash مع الكود المثبت. أي تغيير في النواة يولد سجلاً جديداً ولا يعيد تفسير النتائج السابقة.
