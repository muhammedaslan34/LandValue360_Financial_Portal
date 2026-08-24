"""Seed demo project directly in DB (production container / no HTTP login)."""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import Project, User
from landvalue360_portal.security import apply_rls_context
from landvalue360_portal.services import create_project, current_version, persist_snapshot, user_org_ids

PROJECT_NAME = "مشروع اختبار SIA — دمشق"

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


def build_snapshot(payload: dict) -> dict:
    return {
        "identity": {"name": payload["name"], "description": payload["description"], "currency": payload["currency"]},
        "land": {
            "gross_land_area_sqm": payload["gross_land_area_sqm"],
            "excluded_land_area_sqm": payload["excluded_land_area_sqm"],
            "title_reference": payload["title_reference"],
            "location": payload["location"],
            "current_land_value": payload["current_land_value"],
            "currency": payload["currency"],
        },
        "planning": {
            "far": payload["far"],
            "bcr": payload["bcr"],
            "planning_status": payload["planning_status"],
            "project_duration_months": payload["project_duration_months"],
            "sales_duration_months": payload["sales_duration_months"],
        },
        "land_uses": payload["land_uses"],
        "products": payload["products"],
        "costs": payload["costs"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="muhammadaslan201999@gmail.com")
    args = parser.parse_args()
    email = args.email.lower().strip()

    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
        if not user:
            print(f"USER_NOT_FOUND {email}", file=sys.stderr)
            return 1
        apply_rls_context(db, user)
        org_ids = user_org_ids(db, user.id)
        if not org_ids:
            print("NO_ORG", file=sys.stderr)
            return 1
        org_id = next(iter(org_ids))

        project = db.scalar(
            select(Project).where(
                Project.organization_id == org_id,
                Project.name == PROJECT_NAME,
                Project.deleted_at.is_(None),
            )
        )
        if not project:
            project = create_project(
                db,
                user=user,
                organization_id=org_id,
                name=PROJECT_NAME,
                description=SAMPLE["description"],
                currency="USD",
            )
            print("CREATED", project.reference, project.id)
        else:
            print("EXISTING", project.reference, project.id)

        version = current_version(db, project)
        project.name = PROJECT_NAME
        project.description = SAMPLE["description"]
        project.updated_by = user.id
        snapshot = build_snapshot(SAMPLE)
        result = persist_snapshot(db, version, snapshot, user=user)
        db.commit()
        print("POPULATED completeness=", version.completeness_percent)
        print("GFA", result.get("total_gfa_sqm"))
        print("PROJECT_ID", project.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
