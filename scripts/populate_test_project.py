"""Populate the SIA test project with realistic sample data."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib import error, request

BASE = os.environ.get("LV360_BASE_URL", "http://127.0.0.1:8090")
EMAIL = os.environ.get("LV360_TEST_EMAIL", "muhammadaslan201999@gmail.com")
PASSWORD = os.environ["LV360_TEST_PASSWORD"]
PROJECT_REF = os.environ.get("LV360_TEST_PROJECT_REF", "LV-20260824-0002")

SAMPLE = {
    "name": "مشروع اختبار SIA — دمشق",
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
        {
            "name": "كلفة الإنشاء الرئيسية",
            "category": "CONSTRUCTION",
            "amount": "28500000",
            "currency": "USD",
            "developer_share_percentage": "100",
            "net_sales_deductible": False,
        },
        {
            "name": "دراسات وتصاميم",
            "category": "PROFESSIONAL_FEES",
            "amount": "1650000",
            "currency": "USD",
            "developer_share_percentage": "100",
            "net_sales_deductible": False,
        },
        {
            "name": "تراخيص ورسوم بلدية",
            "category": "PERMITS",
            "amount": "420000",
            "currency": "USD",
            "developer_share_percentage": "100",
            "net_sales_deductible": False,
        },
        {
            "name": "تسويق ومبيعات",
            "category": "MARKETING",
            "amount": "950000",
            "currency": "USD",
            "developer_share_percentage": "100",
            "net_sales_deductible": False,
        },
        {
            "name": "بنية تحتية وشبكات",
            "category": "INFRASTRUCTURE",
            "amount": "2100000",
            "currency": "USD",
            "developer_share_percentage": "100",
            "net_sales_deductible": False,
        },
    ],
}


def http(method, path, body=None, headers=None, cookie=None):
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if cookie:
        hdrs["Cookie"] = cookie
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}, resp.headers.get("Set-Cookie")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return exc.code, payload, None


def project_id_for_ref(ref: str) -> str:
    db = Path(__file__).resolve().parents[1] / "data" / "portal.db"
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT id FROM projects WHERE reference=?", (ref,)).fetchone()
    conn.close()
    if not row:
        raise SystemExit(f"Project not found: {ref}")
    return row[0]


def main() -> int:
    status, login, set_cookie = http("POST", "/api/auth/login", {"email": EMAIL, "password": PASSWORD})
    if status != 200:
        print("LOGIN_FAILED", status, login)
        return 1
    cookie = (set_cookie or "").split(";")[0]
    csrf = login["csrf_token"]
    project_id = project_id_for_ref(PROJECT_REF)

    status, result, _ = http(
        "PUT",
        f"/api/projects/{project_id}",
        SAMPLE,
        headers={"X-CSRF-Token": csrf},
        cookie=cookie,
    )
    if status != 200:
        print("UPDATE_FAILED", status, result)
        return 1

    project = result.get("project") or result
    calc = result.get("calculations") or {}
    print("UPDATED", PROJECT_REF, project_id)
    print("COMPLETENESS", project.get("completeness_percent", "?"))
    print("GFA", calc.get("total_gfa_sqm"))
    print("TOTAL_SALES", calc.get("total_gross_sales"))
    print("OPEN", f"{BASE}/portal/projects/{project_id}")
    return 0


if __name__ == "__main__":
    if not os.environ.get("LV360_TEST_PASSWORD"):
        print("Set LV360_TEST_PASSWORD", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
