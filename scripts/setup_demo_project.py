"""Create and populate the SIA demo project (local or production)."""
from __future__ import annotations

import json
import os
import re
import sys
from urllib import error, request

BASE = os.environ.get("LV360_BASE_URL", "http://127.0.0.1:8090")
EMAIL = os.environ.get("LV360_TEST_EMAIL", "muhammadaslan201999@gmail.com")
PASSWORD = os.environ["LV360_TEST_PASSWORD"]
PROJECT_NAME = os.environ.get("LV360_TEST_PROJECT_NAME", "مشروع اختبار SIA — دمشق")

SAMPLE = {
    "name": PROJECT_NAME,
    "description": "مشروع سكني-تجاري تجريبي في دمشق لاختبار النموذج المالي والمجال التفاوضي.",
    "currency": "USD",
    "gross_land_area_sqm": "15000",
    "excluded_land_area_sqm": "1500",
    "title_reference": "سند ملكية رقم 45821/2024 — محافظة دمشق",
    "location": "دمشق — المزة — قرب طريق المطار",
    "current_land_value": "4800000",
    "far": "3.5",
    "bcr": "0.35",
    "planning_status": "تنظيم سكني متوسط الكثافة مع طابق تجاري أرضي",
    "project_duration_months": 36,
    "sales_duration_months": 24,
    "land_uses": [
        {"code": "INVESTMENT", "name": "أرض استثمارية", "percentage": "60"},
        {"code": "ROADS", "name": "طرق وحركة", "percentage": "20"},
        {"code": "GREEN", "name": "مساحات خضراء", "percentage": "10"},
        {"code": "PUBLIC", "name": "مرافق عامة", "percentage": "10"},
    ],
    "products": [
        {
            "code": "RESIDENTIAL",
            "name": "شقق سكنية",
            "allocation_percentage": "70",
            "sellable_efficiency_percentage": "82",
            "unit_selling_price": "1250",
            "currency": "USD",
        },
        {
            "code": "COMMERCIAL",
            "name": "محلات تجارية",
            "allocation_percentage": "30",
            "sellable_efficiency_percentage": "88",
            "unit_selling_price": "2800",
            "currency": "USD",
        },
    ],
    "costs": [
        {"name": "كلفة الإنشاء الرئيسية", "category": "CONSTRUCTION", "amount": "28500000", "currency": "USD", "developer_share_percentage": "100", "net_sales_deductible": False},
        {"name": "دراسات وتصاميم", "category": "PROFESSIONAL_FEES", "amount": "1650000", "currency": "USD", "developer_share_percentage": "100", "net_sales_deductible": False},
        {"name": "تراخيص ورسوم بلدية", "category": "PERMITS", "amount": "420000", "currency": "USD", "developer_share_percentage": "100", "net_sales_deductible": False},
        {"name": "تسويق ومبيعات", "category": "MARKETING", "amount": "950000", "currency": "USD", "developer_share_percentage": "100", "net_sales_deductible": False},
        {"name": "بنية تحتية وشبكات", "category": "INFRASTRUCTURE", "amount": "2100000", "currency": "USD", "developer_share_percentage": "100", "net_sales_deductible": False},
    ],
}


def http(method, path, body=None, headers=None, cookie=None, accept="application/json"):
    hdrs = {
        "Accept": accept,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LandValue360-Setup/2.5.0",
    }
    if headers:
        hdrs.update(headers)
    if cookie:
        hdrs["Cookie"] = cookie
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, raw, resp.headers.get("Set-Cookie")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, raw, None


def login():
    status, raw, set_cookie = http("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    if status != 200:
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        raise SystemExit(f"LOGIN_FAILED {status} {detail}")
    cookie = (set_cookie or "").split(";")[0]
    data = json.loads(raw)
    csrf = data["csrf_token"]
    return cookie, csrf


def org_id_from_new_project_page(cookie):
    status, html, _ = http("GET", "/portal/projects/new", cookie=cookie, accept="text/html")
    if status != 200:
        raise SystemExit(f"Cannot load new project page: {status}")
    match = re.search(r'<select name="organization_id"[^>]*>(.*?)</select>', html, re.S)
    if not match:
        raise SystemExit("organization_id select not found")
    option = re.search(r'<option value="([^"]+)"', match.group(1))
    if not option:
        raise SystemExit("No organization available for this user")
    return option.group(1)


def find_project_by_name(cookie, name):
    status, html, _ = http("GET", "/portal", cookie=cookie, accept="text/html")
    if status != 200:
        return None
    # project cards link to /portal/projects/{uuid}
    for pid in re.findall(r'/portal/projects/([0-9a-f-]{36})', html):
        st, raw, _ = http("GET", f"/api/projects/{pid}", cookie=cookie)
        if st == 200:
            data = json.loads(raw)
            if data.get("name") == name:
                return pid, data.get("reference")
    return None


def create_project(cookie, csrf, org_id):
    status, raw, _ = http(
        "POST",
        "/api/projects",
        {"organization_id": org_id, "name": PROJECT_NAME, "description": SAMPLE["description"], "currency": "USD"},
        headers={"X-CSRF-Token": csrf},
        cookie=cookie,
    )
    if status != 201:
        raise SystemExit(f"CREATE_FAILED {status} {raw}")
    data = json.loads(raw)
    return data["id"], data.get("reference")


def populate_project(cookie, csrf, project_id):
    status, raw, _ = http(
        "PUT",
        f"/api/projects/{project_id}",
        SAMPLE,
        headers={"X-CSRF-Token": csrf},
        cookie=cookie,
    )
    if status != 200:
        raise SystemExit(f"POPULATE_FAILED {status} {raw}")
    return json.loads(raw)


def main() -> int:
    cookie, csrf = login()
    print("LOGIN_OK", BASE)

    found = find_project_by_name(cookie, PROJECT_NAME)
    if found:
        project_id, ref = found
        print("EXISTING_PROJECT", ref, project_id)
    else:
        org_id = org_id_from_new_project_page(cookie)
        project_id, ref = create_project(cookie, csrf, org_id)
        print("PROJECT_CREATED", ref, project_id)

    result = populate_project(cookie, csrf, project_id)
    project = result.get("project") or result
    calc = result.get("calculations") or {}
    print("POPULATED completeness=", project.get("completeness_percent", "?"))
    print("GFA", calc.get("total_gfa_sqm"))
    print("OPEN", f"{BASE}/portal/projects/{project_id}")
    print("FINANCIAL", f"{BASE}/portal/projects/{project_id}/financial")
    return 0


if __name__ == "__main__":
    if not os.environ.get("LV360_TEST_PASSWORD"):
        print("Set LV360_TEST_PASSWORD", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
