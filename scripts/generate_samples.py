from __future__ import annotations
import os
import shutil
import tempfile
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    os.environ["LV360_PORTAL_DATABASE_URL"] = f"sqlite+pysqlite:///{Path(tmp)/'sample.db'}"
    os.environ["LV360_PORTAL_LOCAL_STORAGE_PATH"] = str(Path(tmp)/"private")
    os.environ["LV360_PORTAL_SECRET_KEY"] = secrets.token_urlsafe(48)
    from landvalue360_portal.database import Base, engine, session_scope
    from landvalue360_portal.models import Organization
    from landvalue360_portal.services import create_personal_landowner, create_project, persist_snapshot, current_version, seed_defaults
    from landvalue360_portal.packages import export_portal_package, export_internal_package, export_excel
    Base.metadata.create_all(engine)
    snapshot = {
        "identity": {"name": "مشروع نموذجي", "description": "بيانات تدريبية غير حقيقية", "currency": "USD"},
        "land": {"gross_land_area_sqm": "10000", "excluded_land_area_sqm": "0", "title_reference": "SAMPLE", "location": "Sample", "current_land_value": "2500000", "currency": "USD"},
        "planning": {"far": "2", "bcr": "0.4", "planning_status": "concept", "project_duration_months": 36, "sales_duration_months": 36},
        "land_uses": [{"code":"INVESTMENT","name":"أرض استثمارية","percentage":"60"},{"code":"ROADS","name":"طرق","percentage":"20"},{"code":"GREEN","name":"مساحات خضراء","percentage":"10"},{"code":"PUBLIC","name":"مرافق عامة","percentage":"10"}],
        "products": [{"code":"RES","name":"سكني","allocation_percentage":"100","sellable_efficiency_percentage":"80","unit_selling_price":"1000","currency":"USD","price_source":"sample","evidence_confidence":"LOW"}],
        "costs": [{"name":"إنشاء","category":"CONSTRUCTION","amount":"8000000","currency":"USD","quantity_basis":None,"quantity":None,"unit_cost":None,"developer_share_percentage":"100","net_sales_deductible":False,"notes":"Sample","source":"sample","evidence_confidence":"LOW"}],
        "financial_model": {
            "schema_version": "standalone-financial-input-2.1.0",
            "advanced_overrides_enabled": True,
            "valuation_date": "2026-01-01",
            "sales": {
                "start_month": 1, "duration_months": 30, "curve_type": "S_CURVE",
                "curve_intensity": "1", "commercial_discount_rate": "0",
                "buyer_incentive_rate": "0", "refund_rate": "0",
                "collection_rules": [
                    {"lag_months": 0, "weight": "0.25", "label": "Contract"},
                    {"lag_months": 12, "weight": "0.35", "label": "Construction"},
                    {"lag_months": 24, "weight": "0.40", "label": "Handover"}
                ]
            },
            "delivery": {
                "construction_start_month": 1, "construction_duration_months": 36,
                "construction_curve_type": "BELL", "other_cost_start_month": 1,
                "other_cost_duration_months": 36, "other_cost_curve_type": "BELL",
                "cost_escalation_rate": "0", "cost_contingency_rate": "0",
                "maximum_extension_months": 120, "maximum_monthly_execution_share": "0.15",
                "maximum_monthly_execution_amount": "0"
            },
            "funding": {
                "opening_cash": "500000", "total_developer_equity": "500000",
                "committed_additional_equity": "0", "committed_financing": "7500000"
            },
            "finance": {
                "enabled": True, "annual_interest_rate": "0.08",
                "upfront_fee_rate": "0.01", "commitment_fee_rate": "0.005",
                "cash_sweep_share": "1", "capitalize_interest": True,
                "force_terminal_repayment": True, "minimum_cash_balance": "0",
                "funding_draw_order": "DEBT_FIRST", "spend_policy": "CASH_DRIVEN",
                "hybrid_minimum_execution_share": "0.35", "future_cost_reserve_share": "0",
                "allow_negative_cash": False, "defer_contractual_payments": True
            },
            "contract": {
                "method": "GROSS_SALES", "share_rate": "0.08", "upfront_amount": "0",
                "upfront_payment_month": 1, "hybrid_upfront_amount": "0",
                "hybrid_share_rate": "0.04", "minimum_guarantee_amount": "0",
                "minimum_guarantee_share_rate": "0.04"
            }
        },
    }
    with session_scope() as db:
        seed_defaults(db)
        user, org = create_personal_landowner(db, email="sample@example.invalid", password=secrets.token_urlsafe(24), full_name="Sample User", organization_name="Sample Organization", country="SY", phone=None)
        project = create_project(db, user=user, organization_id=org.id, name="مشروع نموذجي", description="Training data", currency="USD")
        version = current_version(db, project)
        persist_snapshot(db, version, snapshot, user=user)
        out = root / "release_artifacts"
        out.mkdir(exist_ok=True)
        (out / "sample-portal-submission.lv360").write_bytes(export_portal_package(db, project, version, user))
        (out / "sample-internal-import.lv360").write_bytes(export_internal_package(db, project, version))
        (out / "sample-project.xlsx").write_bytes(export_excel(project, version))
print("Sample artifacts generated.")
