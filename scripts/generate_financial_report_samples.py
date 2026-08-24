#!/usr/bin/env python3
"""Generate deterministic portal-native financial PDF/XLSX samples through HTTP APIs."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("release_artifacts"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".sample-runtime"))
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    runtime_dir = args.runtime_dir.resolve()
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["LV360_PORTAL_DATABASE_URL"] = f"sqlite+pysqlite:///{runtime_dir / 'sample.db'}"
    os.environ["LV360_PORTAL_LOCAL_STORAGE_PATH"] = str(runtime_dir / "private")
    os.environ["LV360_PORTAL_AUTO_VERIFY_EMAIL"] = "true"
    os.environ["LV360_PORTAL_SECRET_KEY"] = "sample-secret-key-long-enough-for-controlled-generation"
    os.environ["LV360_PORTAL_TRUSTED_HOSTS"] = "testserver,127.0.0.1,localhost"

    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from landvalue360_portal.database import Base, engine, session_scope
    from landvalue360_portal.main import app
    from landvalue360_portal.models import Organization

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        register = client.post("/api/auth/register", json={
            "email": "sample-owner@example.com",
            "password": "StrongPass123!",
            "full_name": "Financial Sample Owner",
            "organization_name": "Financial Sample Organization",
            "country": "SY",
            "phone": "",
            "accepted_terms": True,
        })
        register.raise_for_status()
        csrf = register.json()["csrf_token"]
        with session_scope() as db:
            organization = db.scalar(select(Organization).where(Organization.slug == "financial-sample-organization"))
            if organization is None:
                raise RuntimeError("Sample organization was not created")

        create = client.post("/api/projects", json={
            "organization_id": organization.id,
            "name": "LandValue360 Standalone Financial Portal Sample",
            "currency": "USD",
        }, headers={"X-CSRF-Token": csrf})
        create.raise_for_status()
        project_id = create.json()["id"]
        project = {
            "name": "LandValue360 Standalone Financial Portal Sample",
            "description": "Deterministic monthly feasibility and land negotiation sample",
            "currency": "USD",
            "gross_land_area_sqm": "10000",
            "excluded_land_area_sqm": "0",
            "title_reference": "LV360-FP-SAMPLE-001",
            "location": "Damascus",
            "current_land_value": "2500000",
            "far": "2",
            "bcr": "0.4",
            "planning_status": "Concept",
            "project_duration_months": 36,
            "sales_duration_months": 30,
            "land_uses": [{"code": "INVESTMENT", "name": "Investment", "percentage": "100"}],
            "products": [{
                "code": "RES", "name": "Residential", "allocation_percentage": "100",
                "sellable_efficiency_percentage": "80", "unit_selling_price": "1000",
                "currency": "USD", "price_source": "Controlled sample", "evidence_confidence": "HIGH",
            }],
            "costs": [{
                "name": "Construction", "category": "CONSTRUCTION", "amount": "8000000",
                "currency": "USD", "quantity_basis": None, "quantity": None, "unit_cost": None,
                "developer_share_percentage": "100", "net_sales_deductible": True,
                "notes": None, "source": "Controlled sample", "evidence_confidence": "HIGH",
            }],
        }
        update = client.put(f"/api/projects/{project_id}", json=project, headers={"X-CSRF-Token": csrf})
        update.raise_for_status()
        state_response = client.get(f"/api/projects/{project_id}/financial")
        state_response.raise_for_status()
        state = state_response.json()
        model = state["financial_model"]
        model["valuation_date"] = "2026-01-01"
        model["sales"]["duration_months"] = 30
        # Standard-user sample: advanced sales/cost curves, collections and
        # financing are policy-managed and hidden. Default financing is OFF.
        model["funding"].update({
            "opening_cash": "3000000",
            "total_developer_equity": "10000000",
        })
        model["contract"].update({"method": "GROSS_SALES", "share_rate": "0.08"})
        save = client.put(f"/api/projects/{project_id}/financial", json=model, headers={"X-CSRF-Token": csrf})
        save.raise_for_status()
        run_response = client.post(
            f"/api/projects/{project_id}/financial/runs",
            json={"project_version_id": state["project_version"]["id"]},
            headers={"X-CSRF-Token": csrf},
        )
        run_response.raise_for_status()
        run = run_response.json()
        run_id = run["id"]
        pdf = client.get(f"/api/projects/{project_id}/financial/runs/{run_id}/report.pdf")
        pdf.raise_for_status()
        xlsx = client.get(f"/api/projects/{project_id}/financial/runs/{run_id}/report.xlsx")
        xlsx.raise_for_status()

    pdf_path = output_dir / "LandValue360_Financial_Portal_Sample_Report_AR.pdf"
    xlsx_path = output_dir / "LandValue360_Financial_Portal_Sample_Cashflow.xlsx"
    json_path = output_dir / "financial-sample-run.json"
    pdf_path.write_bytes(pdf.content)
    xlsx_path.write_bytes(xlsx.content)
    concise = {
        "status": "PASS",
        "project_id": project_id,
        "calculation_run_id": run_id,
        "input_hash": run["input_hash"],
        "result_hash": run["result_hash"],
        "engine_version": run["engine_version_label"],
        "policy_version": run["financial_policy_version_number"],
        "calculation_status": run["calculation_status"],
        "policy_compliant": run["policy_compliant"],
        "reconciliation_passed": run["reconciliation_passed"],
        "financial_audit": run.get("financial_audit"),
        "recommendation_validation": run.get("recommendation_validation"),
        "monthly_rows": len(run["monthly_cashflow"]),
        "annual_rows": len(run["annual_cashflow"]),
        "negotiation_methods": [row["method"] for row in run["negotiation_results"]],
        "summary": run["summary"],
        "artifacts": {"pdf": pdf_path.name, "xlsx": xlsx_path.name},
    }
    json_path.write_text(json.dumps(concise, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(concise, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
