#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from weasyprint import HTML

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "FINAL_TEST_REPORT_AR.pdf"
ART = ROOT / "release_artifacts" / "LandValue360_Financial_Portal_v2.5.0_FINAL_TEST_REPORT_AR.pdf"

html = r'''<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><style>
@page { size: A4; margin: 17mm 16mm 18mm; @bottom-left { content: "LandValue360 v2.5.0"; color:#60727c; font-size:8pt } @bottom-right { content: "صفحة " counter(page) " من " counter(pages); color:#60727c; font-size:8pt } }
*{box-sizing:border-box} body{font-family:"DejaVu Sans",sans-serif;color:#142839;font-size:10.1pt;line-height:1.72;margin:0;background:#fff}
h1,h2,h3{color:#0c4650;margin:0 0 9px} h1{font-size:25pt;border-bottom:3px solid #0c5963;padding-bottom:12px} h2{font-size:16pt;border-bottom:1.4px solid #8daeb5;padding-bottom:6px;margin-top:22px} h3{font-size:12.5pt;margin-top:15px}
.cover{min-height:245mm;padding-top:28mm}.kicker{color:#b48430;font-weight:700;letter-spacing:.6px}.subtitle{font-size:14pt;color:#516b78;margin-top:10px}.decision{margin-top:28px;border:2px solid #14705f;background:#edf8f4;border-radius:10px;padding:18px;font-size:14pt;font-weight:700;color:#115e50}.meta{margin-top:22px;display:grid;grid-template-columns:1fr 1fr;gap:10px}.box{background:#f2f7f9;border:1px solid #c7d8de;border-radius:8px;padding:12px}.box b{display:block;color:#5c7280;font-size:8.7pt;margin-bottom:5px}.box strong{font-size:12pt;color:#132c3e}.note{border-right:5px solid #0c5963;background:#eef6f7;padding:12px 14px;border-radius:8px;margin:12px 0}.warn{border-right-color:#b48430;background:#fff8e9}
table{width:100%;border-collapse:collapse;margin:10px 0 16px;font-size:8.7pt;page-break-inside:auto} th{background:#174f66;color:white;padding:7px 6px;text-align:right} td{border-bottom:1px solid #d4e0e4;padding:7px 6px;vertical-align:top} tr:nth-child(even) td{background:#f7fafb}.num{direction:ltr;text-align:left;white-space:nowrap}.pass{color:#14705f;font-weight:700}.cond{color:#a86d15;font-weight:700}.fail{color:#a53b3b;font-weight:700}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}.metric{border:1px solid #cad9df;border-radius:8px;padding:10px;background:#f7fafb}.metric .v{font-size:15pt;font-weight:700;color:#102b3e}.metric .l{color:#627b87;font-size:8.7pt}.page-break{break-before:page}.small{font-size:8.7pt;color:#5a6f7b}.checks{columns:2;column-gap:24px}.checks li{break-inside:avoid;margin-bottom:6px}.footer-note{margin-top:18px;font-size:8.4pt;color:#5f737d;border-top:1px solid #ccd9de;padding-top:8px}
</style></head><body>
<section class="cover">
<div class="kicker">LANDVALUE360 RELEASE ASSURANCE</div>
<h1>تقرير الاختبارات والتدقيق النهائي</h1>
<div class="subtitle">LandValue360 Financial Portal v2.5.0</div>
<div class="decision">القرار: جاهز لبيئة Staging والنشر المنضبط على الدومين</div>
<div class="meta">
<div class="box"><b>المحرك الشهري</b><strong>2.1.1</strong></div>
<div class="box"><b>Contract Semantics</b><strong>3.1.0</strong></div>
<div class="box"><b>Portal Adapter</b><strong>2.5.0</strong></div>
<div class="box"><b>Migration Head</b><strong>0007</strong></div>
</div>
<div class="note">يشمل القبول المجال التفاوضي الديناميكي، تفسير الحدود، السياسات متعددة النسخ، إزالة Workflow المحلل غير الضروري، التقرير الرسمي، وصحة المحرك والتقارير والحزمة. لا يشمل أسرار Production أو النشر الفعلي على الدومين.</div>
</section>

<section class="page-break"><h2>1. ملخص التحقق</h2>
<div class="grid">
<div class="metric"><div class="l">اختبارات Python</div><div class="v">56 PASS</div></div>
<div class="metric"><div class="l">Golden Cases</div><div class="v">14 / 14</div></div>
<div class="metric"><div class="l">سيناريوهات السياسات</div><div class="v">49 / 49</div></div>
<div class="metric"><div class="l">فحوص العقود</div><div class="v">513 / 513</div></div>
<div class="metric"><div class="l">Platform Core</div><div class="v">104 / 104</div></div>
<div class="metric"><div class="l">الأمان الساكن</div><div class="v">0 High / 0 Medium</div></div>
</div>
<table><thead><tr><th>مجموعة التحقق</th><th>النتيجة</th><th>ملاحظات</th></tr></thead><tbody>
<tr><td>JavaScript syntax</td><td class="pass">6/6 PASS</td><td>جميع ملفات الواجهة</td></tr>
<tr><td>Contract candidate matrix</td><td class="pass">144 نقطة</td><td>8 مشاريع و3 آليات نسبية</td></tr>
<tr><td>SQLite migration</td><td class="pass">PASS</td><td>47 جدولاً عند الرأس 0007</td></tr>
<tr><td>PostgreSQL offline SQL</td><td class="pass">PASS</td><td>91,941 bytes</td></tr>
<tr><td>Live Uvicorn HTTP</td><td class="pass">PASS</td><td>Root/Register/Live/Ready/OpenAPI = 200</td></tr>
<tr><td>PDF</td><td class="pass">PASS</td><td>11 صفحة A4، فحص بصري</td></tr>
<tr><td>Excel</td><td class="pass">PASS</td><td>5 أوراق، دون أخطاء أو روابط خارجية</td></tr>
<tr><td>Browser E2E</td><td class="cond">SKIPPED</td><td>يتطلب Chromium غير مقيد في Staging</td></tr>
</tbody></table>
<div class="note">لم تتغير معادلات المحرك عن v2.4.0. Contract Engine 3.1.0 والخدمة الشهرية متطابقان، والتغيير المالي البرمجي الوحيد هو وسم Adapter 2.5.0.</div>
</section>

<section class="page-break"><h2>2. مصفوفة المشاريع الفعلية</h2>
<p>نُفذت عشرة سيناريوهات عبر نفس API الذي تستخدمه الواجهة، على قاعدة بيانات مؤقتة وسياسات منشورة قابلة للاختيار.</p>
<table><thead><tr><th>السيناريو</th><th>المبيعات</th><th>الكلفة</th><th>ربح المطور</th><th>الحالة</th></tr></thead><tbody>
<tr><td>مرجع البلاتفورم القديم</td><td class="num">877.5M</td><td class="num">556.5M</td><td class="num">163.0M</td><td class="pass">مطابق</td></tr>
<tr><td>المرجع - سياسة محافظة</td><td class="num">877.5M</td><td class="num">556.5M</td><td class="num">163.0M</td><td class="pass">مطابق</td></tr>
<tr><td>المرجع - سياسة نمو</td><td class="num">877.5M</td><td class="num">556.5M</td><td class="num">163.0M</td><td class="pass">مطابق</td></tr>
<tr><td>منخفض الكلفة وعالي الربحية</td><td class="num">15.3M</td><td class="num">4.0M</td><td class="num">9.77M</td><td class="pass">VALIDATED</td></tr>
<tr><td>مرتفع القيمة وعالي الربحية</td><td class="num">675.0M</td><td class="num">250.0M</td><td class="num">344.0M</td><td class="pass">VALIDATED</td></tr>
<tr><td>منخفض الهامش</td><td class="num">100.0M</td><td class="num">82.0M</td><td class="num">10.0M</td><td class="cond">CONDITIONAL</td></tr>
<tr><td>خاسر</td><td class="num">100.0M</td><td class="num">110.0M</td><td class="num">(15.0M)</td><td class="cond">CONDITIONAL</td></tr>
<tr><td>صافي مبيعات مع حسومات</td><td class="num">100.0M</td><td class="num">50.0M</td><td class="num">22.0M</td><td class="pass">VALIDATED</td></tr>
<tr><td>مشاركة ربح</td><td class="num">100.0M</td><td class="num">60.0M</td><td class="num">30.0M</td><td class="pass">VALIDATED</td></tr>
<tr><td>تمويل تديره السياسة</td><td class="num">50.0M</td><td class="num">35.0M</td><td class="num">12.5M</td><td class="pass">VALIDATED</td></tr>
</tbody></table>
<h3>مرجع البلاتفورم القديم</h3>
<table><thead><tr><th>المؤشر</th><th>القديم</th><th>v2.5.0</th><th>النتيجة</th></tr></thead><tbody>
<tr><td>Gross Sales</td><td class="num">877,500,000</td><td class="num">877,500,000</td><td class="pass">مطابق</td></tr>
<tr><td>Development Cost</td><td class="num">556,522,674</td><td class="num">556,522,674</td><td class="pass">مطابق</td></tr>
<tr><td>18% Landowner Nominal</td><td class="num">157,950,000</td><td class="num">157,950,000</td><td class="pass">مطابق</td></tr>
<tr><td>Developer Profit</td><td class="num">163,027,326</td><td class="num">163,027,326</td><td class="pass">مطابق</td></tr>
<tr><td>Minimum Acceptable</td><td class="num">9.5%</td><td class="num">9.6%</td><td class="pass">ضمن التقريب</td></tr>
<tr><td>Balanced</td><td class="num">12.3%</td><td class="num">12.0%</td><td class="pass">ضمن النطاق</td></tr>
<tr><td>Policy Ceiling</td><td class="num">14.1%</td><td class="num">13.8%</td><td class="pass">ضمن النطاق</td></tr>
<tr><td>Technical Ceiling</td><td class="num">19.9%</td><td class="num">19.9%</td><td class="pass">مطابق</td></tr>
<tr><td>Residual Equivalent</td><td>—</td><td class="num">16.58%</td><td class="pass">مرجع مستقل</td></tr>
</tbody></table>
</section>

<section class="page-break"><h2>3. تدقيق السياسات والواجهة</h2>
<h3>السياسات متعددة النسخ</h3>
<ul class="checks">
<li>استنساخ نسخة سابقة وتعديلها.</li><li>أسماء وأوصاف عربية وإنكليزية.</li><li>نشر نسخة قابلة للاختيار.</li><li>تعيين سياسة افتراضية.</li><li>أرشفة وإعادة نشر النسخ.</li><li>تثبيت Policy Snapshot داخل كل Run.</li><li>تغيير Balanced عند تغيير السياسة.</li><li>ثبات Technical Ceiling مع ثبات المشروع.</li><li>إدارة التمويل والمنحنيات والتحصيل والكلف.</li><li>إخفاء التفاصيل المتقدمة عن المستخدم العادي.</li></ul>
<h3>واجهة v2.5.0</h3>
<table><thead><tr><th>الفحص</th><th>النتيجة</th></tr></thead><tbody>
<tr><td>محور المجال يبدأ من الحد الأدنى المقبول</td><td class="pass">PASS</td></tr>
<tr><td>Dynamic scaling حتى أعلى نقطة مؤثرة</td><td class="pass">PASS</td></tr>
<tr><td>Collision avoidance للعلامات</td><td class="pass">PASS</td></tr>
<tr><td>عرض العرض خارج المجال بوضوح</td><td class="pass">PASS</td></tr>
<tr><td>شرح النقاط الست بالأرقام والقيد الحاكم</td><td class="pass">PASS</td></tr>
<tr><td>إزالة مساحة المحلل وإدارة الحالة</td><td class="pass">PASS</td></tr>
<tr><td>إعادة توجيه المسارات القديمة</td><td class="pass">PASS</td></tr>
<tr><td>Sales Curve Intensity = 1</td><td class="pass">PASS</td></tr>
<tr><td>تنظيف آثار Floating Point في السياسة</td><td class="pass">PASS</td></tr>
</tbody></table>
<div class="note warn">قواعد سلامة الحساب لا تزال مقفلة: لا كاش سالب غير مغطى، Terminal Debt = 0، Deferred Cost = 0، Contractual Arrears = 0، ومصالحة شهرية إلزامية.</div>
</section>

<section class="page-break"><h2>4. التقارير والتشغيل والنشر</h2>
<h3>PDF</h3><p>تم إنشاء تقرير عربي من 11 صفحة A4. يعرض الملخص التنفيذي، اقتصاديات المشروع والمطور وصاحب الأرض، القدرة التطويرية للأرض، الرسم التفاوضي الديناميكي، شرح الحدود، مقارنة العقود، التدفقات، الفحوص الفنية ودليل المصطلحات. لم يظهر قص أو تداخل أو حروف مكسورة في الصفحات المفحوصة.</p>
<h3>Excel</h3><p>تم إنشاء خمس أوراق: Executive Summary، Negotiation Range، Annual Cash Flow، Monthly Cash Flow، Inputs and Provenance. لا توجد صيغ خاطئة أو روابط خارجية.</p>
<h3>التشغيل</h3><p>نجحت ترقية قاعدة SQLite جديدة إلى الرأس 0007، وتوليد SQL الخاص بـPostgreSQL، وتشغيل Uvicorn فعلياً، واستجابة مسارات الصحة والواجهة وOpenAPI برمز HTTP 200.</p>
<h3>حدود القبول</h3><p>لم يجر النشر على الدومين أو اختبار PostgreSQL الإنتاجية والتخزين الخاص وSMTP وTLS الفعلي. يلزم تطبيق النسخ الاحتياطي وStaging وضبط الأسرار قبل Production.</p>
<div class="decision" style="margin-top:24px">النتيجة النهائية: READY FOR STAGING AND CONTROLLED DOMAIN DEPLOYMENT</div>
<div class="footer-note">هذا التقرير يوثق الاختبارات المنفذة ضمن بيئة البناء. لا يحل محل اختبار القبول التشغيلي على بيانات الإنتاج بعد استعادتها في Staging.</div>
</section>
</body></html>'''

HTML(string=html, base_url=str(ROOT)).write_pdf(str(OUT))
ART.write_bytes(OUT.read_bytes())
print(OUT)
print(ART)
