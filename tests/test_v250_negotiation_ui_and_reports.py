from pathlib import Path

from landvalue360_portal.database import session_scope
from landvalue360_portal.report_renderer import render_html_pdf
from landvalue360_portal.services import create_staff_user

ROOT = Path(__file__).resolve().parents[1]


def test_negotiation_axis_is_floor_anchored_and_explained():
    script = (ROOT / "app/landvalue360_portal/static/financial.js").read_text(encoding="utf-8")
    template = (ROOT / "app/landvalue360_portal/templates/financial.html").read_text(encoding="utf-8")
    styles = (ROOT / "app/landvalue360_portal/static/styles.css").read_text(encoding="utf-8")
    assert "const axisStart=floor!==null?floor" in script
    assert "(num(value)-axisStart)/(axisEnd-axisStart)" in script
    assert "assignNegotiationLanes" in script
    assert "renderNegotiationExplanations" in script
    assert 'id="negotiationExplanation"' in template
    assert "الحد الأدنى المقبول" in template
    assert "الحد العادل" not in template
    assert ".neg-explanation-grid" in styles
    assert ".neg-marker.lane-3" in styles


def test_simple_portal_hides_legacy_analyst_and_status_workflow():
    base = (ROOT / "app/landvalue360_portal/templates/base.html").read_text(encoding="utf-8")
    dashboard = (ROOT / "app/landvalue360_portal/templates/dashboard.html").read_text(encoding="utf-8")
    common = (ROOT / "app/landvalue360_portal/static/common.js").read_text(encoding="utf-8")
    assert "operationsNav" not in base
    assert "/portal/projects/{{ project.id }}/status" not in dashboard
    assert "getElementById('operationsNav')" not in common


def test_sales_curve_intensity_accepts_neutral_value_one():
    admin = (ROOT / "app/landvalue360_portal/templates/admin.html").read_text(encoding="utf-8")
    assert 'name="advanced_sales_curve_intensity" min="0.000001" max="100" step="any"' in admin
    admin_js = (ROOT / "app/landvalue360_portal/static/admin.js").read_text(encoding="utf-8")
    assert "cleanPolicyNumber" in admin_js


def test_legacy_operations_pages_redirect_to_current_portal(client):
    with session_scope() as db:
        create_staff_user(
            db,
            email="v250-admin@example.com",
            password="StrongPass123!",
            full_name="V250 Admin",
            role_code="PLATFORM_ADMIN",
        )
    login = client.post("/api/auth/login", json={"email": "v250-admin@example.com", "password": "StrongPass123!"})
    assert login.status_code == 200
    response = client.get("/operations", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_pdf_renderer_supports_dynamic_negotiation_axis_and_explanation_cards():
    html = '''<!doctype html><html lang="ar" dir="rtl"><head><title>V250 Report</title></head><body>
    <section class="page"><h2>المجال التفاوضي</h2>
      <div class="negotiation-band" data-title="نسبة من إجمالي المبيعات" data-floor="0.10" data-balanced="0.123" data-policy="0.141" data-residual="0.1658" data-ceiling="0.199" data-offer="0.18" data-floor-display="10%" data-balanced-display="12.3%" data-policy-display="14.1%" data-residual-display="16.58%" data-ceiling-display="19.9%" data-offer-display="18%" data-floor-label="الحد الأدنى المقبول" data-balanced-label="النقطة المتوازنة" data-policy-label="السقف المتحفظ" data-residual-label="القيمة المتبقية" data-ceiling-label="السقف الفني" data-offer-label="العرض الحالي"></div>
      <h3>كيف تم تحديد هذا النطاق؟</h3>
      <div class="explanation-grid">
        <div data-tone="minimum" data-title="الحد الأدنى المقبول" data-value="10%" data-body="أقل مقابل يحقق قيمة الأرض المطلوبة." data-evidence="NPV صاحب الأرض أكبر من الحد المطلوب."></div>
        <div data-tone="balanced" data-title="النقطة المتوازنة" data-value="12.3%" data-body="توازن بين الطرفين وفق السياسة." data-evidence="IRR المطور ناجح."></div>
      </div>
    </section></body></html>'''
    pdf = render_html_pdf(html)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000
