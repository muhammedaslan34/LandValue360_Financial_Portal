# LandValue360 Financial Portal v2.5.0

بوابة مالية مستقلة (FastAPI) لمشاريع التطوير العقاري — نموذج مالي شهري، جدوى المشروع/المطور/صاحب الأرض، والمجال التفاوضي.

---

## المتطلبات

- **Windows 10/11** (64-bit)
- **Python 3.12+** مع تفعيل `Add Python to PATH`
- اتصال إنترنت (أول تشغيل فقط — لتثبيت الحزم)

---

## التشغيل المحلي — خطوة بخطوة

### 1) فك الحزمة

```text
D:\Projects\paython\LandValue360_Financial_Portal_v2.5.0_READY
```

### 2) التشغيل السريع (موصى به)

```powershell
cd "D:\Projects\paython\LandValue360_Financial_Portal_v2.5.0_READY"
.\START_PORTAL.bat
```

يقوم الملف تلقائياً بـ:

1. إنشاء بيئة `.venv` إن لم تكن موجودة  
2. تثبيت الاعتماديات من `requirements-runtime-lock.txt`  
3. تثبيت الحزمة من `dist\`  
4. تشغيل ترحيلات قاعدة البيانات (`alembic upgrade head`)  
5. إنشاء حساب مدير محلي (أول مرة فقط)  
6. فتح المتصفح على **http://127.0.0.1:8090**

### 3) التشغيل اليدوي (بديل)

```powershell
cd "D:\Projects\paython\LandValue360_Financial_Portal_v2.5.0_READY"

# بيئة افتراضية
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-runtime-lock.txt
.\.venv\Scripts\python.exe -m pip install -e .

# إعداد محلي
copy .env.example .env
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\first_run_bootstrap.py --non-interactive `
  --email admin@local.test --password "LocalAdmin123!" --name "Local Admin"

# تشغيل الخادم
.\.venv\Scripts\python.exe -m uvicorn landvalue360_portal.main:app --host 127.0.0.1 --port 8090
```

### 4) فتح البوابة

| الصفحة | الرابط |
|--------|--------|
| الرئيسية | http://127.0.0.1:8090 |
| تسجيل الدخول | http://127.0.0.1:8090/login |
| مشروعاتي | http://127.0.0.1:8090/portal |
| مشروع جديد | http://127.0.0.1:8090/portal/projects/new |

**حساب المدير المحلي (افتراضي):**

| البريد | كلمة المرور |
|--------|-------------|
| `admin@local.test` | `LocalAdmin123!` |

> محلياً: قاعدة SQLite في `./data/portal.db` — البريد يُرسل إلى الطرفية (console) وليس SMTP حقيقي.

### 5) إيقاف الخادم

في نافذة التشغيل اضغط **`Ctrl + C`**.

### 6) إذا لم تظهر التعديلات (CSS / القوائم)

Hard refresh في المتصفح:

```text
Ctrl + Shift + R
```

لا حاجة لإعادة تشغيل الخادم بعد تغيير CSS/JS/HTML — فقط حدّث الصفحة.

---

## إنشاء مشروع تجريبي بالبيانات الكاملة

سكربتات مساعدة (كلمة المرور عبر متغير بيئة — **لا تُحفظ في Git**):

```powershell
cd "D:\Projects\paython\LandValue360_Financial_Portal_v2.5.0_READY"
$env:LV360_TEST_PASSWORD = "كلمة-المرور-هنا"

# إنشاء مشروع فارغ
$env:LV360_BASE_URL = "http://127.0.0.1:8090"
.\.venv\Scripts\python.exe scripts\test_login_create_project.py

# تعبئة بيانات تجريبية (أرض، منتجات، كلف — اكتمال 100%)
$env:LV360_TEST_PROJECT_REF = "LV-20260824-0002"   # أو مرجع مشروعك
.\.venv\Scripts\python.exe scripts\populate_test_project.py

# أو: إنشاء + تعبئة دفعة واحدة
.\.venv\Scripts\python.exe scripts\setup_demo_project.py
```

**بيانات المشروع التجريبي (مشروع اختبار SIA — دمشق):**

- مساحة أرض: 15,000 م² | مستبعدة: 1,500 م² | FAR: 3.5 | BCR: 35%  
- قيمة أرض: 4,800,000 USD | الموقع: دمشق — المزة  
- استخدامات أرض 100% | منتجات سكني + تجاري | 5 بنود كلف  

---

## الإنتاج (Production)

| البند | القيمة |
|-------|--------|
| **الرابط** | https://sia-ai.net |
| **المنصة** | Coolify + Docker Compose |
| **قاعدة البيانات** | PostgreSQL 16 (داخل Compose) |
| **التخزين** | iDrive E2 (S3) |
| **البريد** | Brevo SMTP — notifications@sia-ai.net |

### النشر على Coolify

1. ادفع التغييرات إلى GitHub (`main`)  
2. Coolify يسحب تلقائياً أو نفّذ Redeploy من لوحة Coolify  
3. تحقق من الصحة: https://sia-ai.net/api/health/ready  

```powershell
# بعد الدفع — إنشاء مشروع تجريبي على الإنتاج
$env:LV360_BASE_URL = "https://sia-ai.net"
$env:LV360_TEST_EMAIL = "your@email.com"
$env:LV360_TEST_PASSWORD = "your-password"
.\.venv\Scripts\python.exe scripts\setup_demo_project.py
```

---

## استكشاف الأخطاء

| المشكلة | الحل |
|---------|------|
| `Startup failed` | شغّل `REPAIR_INSTALLATION.bat` |
| المنفذ 8090 مشغول | أوقف العملية القديمة أو غيّر المنفذ |
| Python غير موجود | ثبّت Python 3.12+ 64-bit |
| Wheel مفقود | تأكد من وجود ملف في `dist\` |
| تسجيل الدخول فاشل محلياً | أنشئ مستخدم عبر `first_run_bootstrap.py` أو `/register` |

---

## التحقق قبل النشر

```powershell
.\.venv\Scripts\python.exe scripts\verify_release.py
.\.venv\Scripts\python.exe scripts\runtime_preflight.py
.\.venv\Scripts\python.exe -m alembic upgrade head
```

---

## English summary

Standalone FastAPI financial portal (LandValue360 monthly engine v2.1.1). Run locally with `START_PORTAL.bat` → http://127.0.0.1:8090. Production: https://sia-ai.net on Coolify.

See also: [README_AR.md](README_AR.md) for extended Arabic product documentation.
