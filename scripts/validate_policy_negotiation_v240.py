#!/usr/bin/env python3
"""Validate v2.4.0 policy versioning and negotiation economics across project types.

This is an end-to-end API validation. It uses a disposable database, creates
published policy versions through the administrator API, lets a standard user
select them, and executes immutable financial runs through the same endpoints
used by the portal UI.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any


VALIDATION_CREDENTIAL = "StrongPass123!"


def D(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def close(actual: Any, expected: Any, tolerance: str = "0.02") -> bool:
    return abs(D(actual) - D(expected)) <= D(tolerance)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("release_artifacts/policy-negotiation-scenarios-v2.4.0.json"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".policy-scenario-runtime"))
    args = parser.parse_args()
    output = args.output.resolve()
    runtime = args.runtime_dir.resolve()
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    os.environ["LV360_PORTAL_DATABASE_URL"] = f"sqlite+pysqlite:///{runtime / 'scenario.db'}"
    os.environ["LV360_PORTAL_LOCAL_STORAGE_PATH"] = str(runtime / "private")
    os.environ["LV360_PORTAL_AUTO_VERIFY_EMAIL"] = "true"
    os.environ["LV360_PORTAL_SECRET_KEY"] = "policy-scenario-secret-key-long-enough-for-validation"
    os.environ["LV360_PORTAL_TRUSTED_HOSTS"] = "testserver,127.0.0.1,localhost"

    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from landvalue360_portal.database import Base, engine, session_scope
    from landvalue360_portal.main import app
    from landvalue360_portal.models import Organization
    from landvalue360_portal.services import create_staff_user

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    assertions: list[dict[str, Any]] = []
    scenarios: dict[str, dict[str, Any]] = {}

    def check(name: str, condition: bool, actual: Any = None, expected: Any = None) -> None:
        assertions.append({"name": name, "passed": bool(condition), "actual": actual, "expected": expected})
        if not condition:
            raise AssertionError(f"{name}: actual={actual!r}, expected={expected!r}")

    with TestClient(app) as client:
        register = client.post("/api/auth/register", json={
            "email": "scenario-owner-v240@example.com",
            "password": VALIDATION_CREDENTIAL,
            "full_name": "Scenario Owner",
            "organization_name": "Scenario Validation Organization",
            "country": "SY", "phone": "", "accepted_terms": True,
        })
        register.raise_for_status()
        user_csrf = register.json()["csrf_token"]
        with session_scope() as db:
            organization = db.scalar(select(Organization).where(Organization.slug == "scenario-validation-organization"))
            if organization is None:
                raise RuntimeError("Scenario organization not created")
            create_staff_user(
                db, email="scenario-admin-v240@example.com", password=VALIDATION_CREDENTIAL,
                full_name="Scenario Policy Admin", role_code="PLATFORM_ADMIN",
            )

        client.post("/api/auth/logout", headers={"X-CSRF-Token": user_csrf})
        admin_login = client.post("/api/auth/login", json={"email": "scenario-admin-v240@example.com", "password": VALIDATION_CREDENTIAL})
        admin_login.raise_for_status()
        admin_csrf = admin_login.json()["csrf_token"]
        current = client.get("/api/admin/financial-policy").json()["current"]

        def publish_policy(name_ar: str, name_en: str, changes: dict[str, Any]) -> dict[str, Any]:
            controls = deepcopy(current["controls"])
            controls.update(changes)
            controls["display_name_ar"] = name_ar
            controls["display_name_en"] = name_en
            controls["description_ar"] = f"نسخة اختبار {name_ar} قابلة لاختيار المستخدم."
            controls["description_en"] = f"Selectable validation policy: {name_en}."
            controls["user_selectable"] = True
            response = client.post("/api/admin/financial-policy/versions", json={
                "controls": controls,
                "change_reason": f"v2.4.0 scenario validation - {name_en}",
                "source_version_id": current["id"],
                "activate": False,
            }, headers={"X-CSRF-Token": admin_csrf})
            response.raise_for_status()
            return response.json()

        gross_standard = publish_policy("سياسة إجمالي المبيعات القياسية", "Standard Gross-Sales Policy", {
            "allowed_contract_methods": ["GROSS_SALES"],
        })
        conservative = publish_policy("سياسة محافظة", "Conservative Policy", {
            "allowed_contract_methods": ["GROSS_SALES"],
            "risk_adjusted_capacity_factor": "0.35",
            "institutional_conservatism": "0.55",
            "balanced_position_factor": "0.40",
        })
        growth = publish_policy("سياسة نمو", "Growth Policy", {
            "allowed_contract_methods": ["GROSS_SALES"],
            "risk_adjusted_capacity_factor": "0.65",
            "institutional_conservatism": "0.20",
            "balanced_position_factor": "0.75",
        })
        net_policy = publish_policy("سياسة صافي المبيعات", "Net-Sales Validation Policy", {
            "allowed_contract_methods": ["NET_SALES"],
        })
        profit_policy = publish_policy("سياسة مشاركة الربح", "Profit-Share Validation Policy", {
            "allowed_contract_methods": ["PROFIT_SHARE"],
        })
        financed_controls = deepcopy(current["controls"])
        financed_controls.update({
            "display_name_ar": "سياسة تمويل اختبارية",
            "display_name_en": "Financed Validation Policy",
            "description_ar": "سياسة اختبار تمويل تدار بالكامل من الأدمن.",
            "description_en": "Administrator-managed financing validation policy.",
            "user_selectable": True,
            "allowed_contract_methods": ["GROSS_SALES"],
        })
        financed_controls["finance_policy"] = {
            **financed_controls["finance_policy"],
            "allow_financing": True,
            "defer_unfunded_costs": True,
        }
        financed_controls["advanced_defaults"] = {
            **financed_controls["advanced_defaults"],
            "finance_enabled": True,
            "committed_financing": "40000000",
            "annual_interest_rate": "0.08",
            "upfront_fee_rate": "0.01",
            "commitment_fee_rate": "0.0025",
            "funding_draw_order": "DEBT_FIRST",
            "sales_curve_type": "BACK_LOADED",
            "construction_curve_type": "FRONT_LOADED",
            "other_cost_curve_type": "FRONT_LOADED",
            "force_terminal_repayment": True,
        }
        financed = client.post("/api/admin/financial-policy/versions", json={
            "controls": financed_controls,
            "change_reason": "v2.4.0 financed scenario validation",
            "source_version_id": current["id"],
            "activate": False,
        }, headers={"X-CSRF-Token": admin_csrf})
        financed.raise_for_status()
        financed = financed.json()

        client.post("/api/auth/logout", headers={"X-CSRF-Token": admin_csrf})
        user_login = client.post("/api/auth/login", json={"email": "scenario-owner-v240@example.com", "password": VALIDATION_CREDENTIAL})
        user_login.raise_for_status()
        user_csrf = user_login.json()["csrf_token"]

        def create_and_run(
            *, key: str, name: str, land_area: str, far: str, efficiency: str,
            unit_price: str, cost: str, land_value: str, equity: str,
            method: str, share: str, policy_id: str | None = None,
            duration: int = 60, sales_duration: int = 36, sales_start: int = 1,
            construction_start: int = 1, construction_duration: int | None = None,
            commercial_discount: str = "0", buyer_incentive: str = "0", refund: str = "0",
        ) -> dict[str, Any]:
            create = client.post("/api/projects", json={
                "organization_id": organization.id, "name": name, "currency": "USD",
            }, headers={"X-CSRF-Token": user_csrf})
            create.raise_for_status()
            project_id = create.json()["id"]
            project_payload = {
                "name": name, "currency": "USD", "gross_land_area_sqm": land_area,
                "excluded_land_area_sqm": "0", "current_land_value": land_value,
                "far": far, "bcr": "0.40", "project_duration_months": duration,
                "sales_duration_months": sales_duration,
                "land_uses": [{"code": "INVESTMENT", "name": "Investment", "percentage": "100"}],
                "products": [{
                    "code": "MIX", "name": "Mixed", "allocation_percentage": "100",
                    "sellable_efficiency_percentage": efficiency,
                    "unit_selling_price": unit_price, "currency": "USD",
                }],
                "costs": [{
                    "name": "Total development cost", "category": "CONSTRUCTION",
                    "amount": cost, "currency": "USD", "developer_share_percentage": "100",
                    "net_sales_deductible": False,
                }],
            }
            update = client.put(f"/api/projects/{project_id}", json=project_payload, headers={"X-CSRF-Token": user_csrf})
            update.raise_for_status()
            query = f"?policy_version_id={policy_id}" if policy_id else ""
            state_response = client.get(f"/api/projects/{project_id}/financial{query}")
            state_response.raise_for_status()
            state = state_response.json()
            model = state["financial_model"]
            model["valuation_date"] = "2026-01-01"
            model["sales"]["start_month"] = sales_start
            model["sales"]["duration_months"] = sales_duration
            model["sales"]["commercial_discount_rate"] = commercial_discount
            model["sales"]["buyer_incentive_rate"] = buyer_incentive
            model["sales"]["refund_rate"] = refund
            model["delivery"]["construction_start_month"] = construction_start
            model["delivery"]["construction_duration_months"] = construction_duration or duration
            model["funding"]["opening_cash"] = equity
            model["funding"]["total_developer_equity"] = equity
            model["contract"]["method"] = method
            model["contract"]["share_rate"] = share
            policy_query = f"?policy_version_id={policy_id or state['policy']['id']}"
            save = client.put(f"/api/projects/{project_id}/financial{policy_query}", json=model, headers={"X-CSRF-Token": user_csrf})
            save.raise_for_status()
            run_response = client.post(f"/api/projects/{project_id}/financial/runs", json={
                "project_version_id": state["project_version"]["id"],
                "policy_version_id": policy_id or state["policy"]["id"],
            }, headers={"X-CSRF-Token": user_csrf})
            run_response.raise_for_status()
            run = run_response.json()
            gross_row = next((row for row in run.get("negotiation_results", []) if row.get("method") == method), {})
            scenarios[key] = {
                "name": name,
                "project_id": project_id,
                "run_id": run["id"],
                "policy_version_id": run["financial_policy_version_id"],
                "policy_name": run.get("financial_policy_display_name_en"),
                "calculation_status": run.get("calculation_status"),
                "policy_compliant": run.get("policy_compliant"),
                "reconciliation_passed": run.get("reconciliation_passed"),
                "summary": run.get("summary"),
                "residual_valuation": run.get("residual_valuation"),
                "selected_mechanism_negotiation": gross_row,
                "financial_audit": run.get("financial_audit"),
                "recommendation_validation": run.get("recommendation_validation"),
                "input_hash": run.get("input_hash"),
                "result_hash": run.get("result_hash"),
            }
            check(f"{key}: run completed", run["status"] == "COMPLETED", run["status"], "COMPLETED")
            check(f"{key}: monthly reconciliation", bool(run.get("reconciliation_passed")), run.get("reconciliation_passed"), True)
            return run

        reference = create_and_run(
            key="old_platform_reference", name="Old Platform Golden Reference",
            land_area="600000", far="2.25", efficiency="100", unit_price="650",
            cost="556522674", land_value="60000000", equity="7000000",
            method="GROSS_SALES", share="0.18", policy_id=gross_standard["id"],
        )
        reference_project_id = scenarios["old_platform_reference"]["project_id"]
        ref_summary = reference["summary"]
        check("reference gross sales", close(ref_summary["gross_sales"], "877500000"), ref_summary["gross_sales"], "877500000")
        check("reference development cost", close(ref_summary["development_cost"], "556522674"), ref_summary["development_cost"], "556522674")
        check("reference landowner nominal at 18%", close(ref_summary["government_consideration"], "157950000"), ref_summary["government_consideration"], "157950000")
        check("reference developer profit", close(ref_summary["developer_profit"], "163027326"), ref_summary["developer_profit"], "163027326")
        ref_neg = next(row for row in reference["negotiation_results"] if row["method"] == "GROSS_SALES")
        floor = D(ref_neg["fair_floor"]); balanced = D(ref_neg["balanced"]); policy_ceiling = D(ref_neg["policy_adjusted_ceiling"]); technical = D(ref_neg["technical_ceiling"]); residual_eq = D(ref_neg["residual_equivalent_measure"])
        check("reference ordered negotiation points", floor < balanced < policy_ceiling < technical, [str(floor), str(balanced), str(policy_ceiling), str(technical)], "floor < balanced < policy ceiling < technical")
        check("reference balanced range", D("0.115") <= balanced <= D("0.135"), str(balanced), "11.5%-13.5%")
        check("reference policy ceiling range", D("0.135") <= policy_ceiling <= D("0.155"), str(policy_ceiling), "13.5%-15.5%")
        check("reference technical ceiling range", D("0.185") <= technical <= D("0.210"), str(technical), "18.5%-21.0%")
        check("reference residual equivalent range", D("0.15") <= residual_eq <= D("0.18"), str(residual_eq), "15.0%-18.0%")

        # Re-run the exact frozen project under two selectable policy versions.
        def rerun_reference(policy: dict[str, Any], key: str) -> dict[str, Any]:
            state = client.get(f"/api/projects/{reference_project_id}/financial?policy_version_id={policy['id']}")
            state.raise_for_status()
            response = client.post(f"/api/projects/{reference_project_id}/financial/runs", json={
                "project_version_id": state.json()["project_version"]["id"],
                "policy_version_id": policy["id"],
            }, headers={"X-CSRF-Token": user_csrf})
            response.raise_for_status()
            run = response.json()
            row = next(item for item in run["negotiation_results"] if item["method"] == "GROSS_SALES")
            scenarios[key] = {
                "name": key,
                "project_id": reference_project_id,
                "run_id": run["id"],
                "policy_version_id": policy["id"],
                "policy_name": run.get("financial_policy_display_name_en"),
                "summary": run["summary"], "selected_mechanism_negotiation": row,
                "input_hash": run["input_hash"], "result_hash": run["result_hash"],
                "policy_compliant": run["policy_compliant"], "reconciliation_passed": run["reconciliation_passed"],
            }
            return run

        conservative_run = rerun_reference(conservative, "reference_conservative_policy")
        growth_run = rerun_reference(growth, "reference_growth_policy")
        conservative_row = next(row for row in conservative_run["negotiation_results"] if row["method"] == "GROSS_SALES")
        growth_row = next(row for row in growth_run["negotiation_results"] if row["method"] == "GROSS_SALES")
        conservative_bal = D(conservative_row["balanced"]); growth_bal = D(growth_row["balanced"])
        check("policy choice changes balanced recommendation", conservative_bal < balanced < growth_bal, [str(conservative_bal), str(balanced), str(growth_bal)], "conservative < standard < growth")
        check("policy choice preserves technical economics", abs(D(conservative_row["technical_ceiling"]) - technical) <= D("0.00002") and abs(D(growth_row["technical_ceiling"]) - technical) <= D("0.00002"), [conservative_row["technical_ceiling"], ref_neg["technical_ceiling"], growth_row["technical_ceiling"]], "same technical ceiling")
        check("policy runs are immutable and distinct", len({reference["input_hash"], conservative_run["input_hash"], growth_run["input_hash"]}) == 3, [reference["input_hash"], conservative_run["input_hash"], growth_run["input_hash"]], "three distinct input hashes")

        cheap = create_and_run(
            key="cheap_high_profit", name="Low-Cost High-Profit Project",
            land_area="10000", far="2", efficiency="85", unit_price="900",
            cost="4000000", land_value="800000", equity="5000000",
            method="GROSS_SALES", share="0.10", policy_id=gross_standard["id"],
        )
        check("cheap project profitable", D(cheap["summary"]["project_profit"]) > 0, cheap["summary"]["project_profit"], "> 0")
        check("cheap project positive developer return", D(cheap["summary"]["developer_profit"]) > 0, cheap["summary"]["developer_profit"], "> 0")

        premium = create_and_run(
            key="premium_high_profit", name="Premium High-Value Project",
            land_area="100000", far="3", efficiency="90", unit_price="2500",
            cost="250000000", land_value="40000000", equity="150000000",
            method="GROSS_SALES", share="0.12", policy_id=gross_standard["id"],
        )
        check("premium project gross sales", close(premium["summary"]["gross_sales"], "675000000"), premium["summary"]["gross_sales"], "675000000")
        check("premium project profitable", D(premium["summary"]["developer_profit"]) > D("100000000"), premium["summary"]["developer_profit"], "> 100M")

        low_margin = create_and_run(
            key="low_margin", name="Low-Margin Project",
            land_area="50000", far="2", efficiency="100", unit_price="1000",
            cost="82000000", land_value="8000000", equity="90000000",
            method="GROSS_SALES", share="0.08", policy_id=gross_standard["id"],
        )
        check("low-margin nominal profit positive", D(low_margin["summary"]["developer_profit"]) > 0, low_margin["summary"]["developer_profit"], "> 0")
        check("low-margin policy flags weak return", not bool(low_margin["policy_compliant"]), low_margin["policy_compliant"], False)

        loss = create_and_run(
            key="loss_making", name="Loss-Making Project",
            land_area="50000", far="2", efficiency="100", unit_price="1000",
            cost="110000000", land_value="5000000", equity="120000000",
            method="GROSS_SALES", share="0.05", policy_id=gross_standard["id"],
        )
        check("loss scenario project profit negative", D(loss["summary"]["project_profit"]) < 0, loss["summary"]["project_profit"], "< 0")
        check("loss scenario not policy compliant", not bool(loss["policy_compliant"]), loss["policy_compliant"], False)

        net = create_and_run(
            key="net_sales_discount", name="Net Sales Discount Project",
            land_area="50000", far="2", efficiency="100", unit_price="1000",
            cost="50000000", land_value="10000000", equity="60000000",
            method="NET_SALES", share="0.20", policy_id=net_policy["id"], buyer_incentive="0.10",
        )
        check("net scenario gross sales", close(net["summary"]["gross_sales"], "100000000"), net["summary"]["gross_sales"], "100000000")
        check("net scenario net sales", close(net["summary"]["net_sales"], "90000000"), net["summary"]["net_sales"], "90000000")
        check("20% net sales gives 18M landowner", close(net["summary"]["government_consideration"], "18000000"), net["summary"]["government_consideration"], "18000000")

        profit = create_and_run(
            key="profit_share", name="Profit Share Project",
            land_area="50000", far="2", efficiency="100", unit_price="1000",
            cost="60000000", land_value="10000000", equity="70000000",
            method="PROFIT_SHARE", share="0.25", policy_id=profit_policy["id"],
        )
        check("profit-share consideration nonnegative", D(profit["summary"]["government_consideration"]) >= 0, profit["summary"]["government_consideration"], ">= 0")
        check("profit-share does not use sales basis", D(profit["summary"]["government_consideration"]) < D("25000000"), profit["summary"]["government_consideration"], "< 25% of gross sales")

        financed_run = create_and_run(
            key="financed_policy", name="Policy-Managed Financed Project",
            land_area="25000", far="2", efficiency="100", unit_price="1000",
            cost="35000000", land_value="3000000", equity="1000000",
            method="GROSS_SALES", share="0.05", policy_id=financed["id"],
            sales_start=12, sales_duration=30, construction_duration=36,
        )
        check("financed policy uses debt", D(financed_run["summary"]["peak_debt"]) > 0, financed_run["summary"]["peak_debt"], "> 0")
        check("financed policy incurs interest", D(financed_run["summary"]["interest_total"]) > 0, financed_run["summary"]["interest_total"], "> 0")
        check("financed policy clears terminal debt", abs(D(financed_run["summary"]["terminal_debt"])) <= D("0.01"), financed_run["summary"]["terminal_debt"], "0")

        # Confirm standard users can see all selectable published versions.
        policy_state = client.get(f"/api/projects/{reference_project_id}/financial")
        policy_state.raise_for_status()
        selectable_ids = {row["id"] for row in policy_state.json()["policy_versions"]}
        check("standard user sees standard policy", current["id"] in selectable_ids, current["id"], "visible")
        check("standard user sees gross validation policy", gross_standard["id"] in selectable_ids, gross_standard["id"], "visible")
        check("standard user sees conservative policy", conservative["id"] in selectable_ids, conservative["id"], "visible")
        check("standard user sees growth policy", growth["id"] in selectable_ids, growth["id"], "visible")
        check("standard user sees financed policy", financed["id"] in selectable_ids, financed["id"], "visible")

    old_reference = {
        "source": "User-provided old-platform screenshots",
        "gross_sales": "877500000",
        "development_cost": "556522674",
        "landowner_share": "0.18",
        "landowner_nominal": "157950000",
        "landowner_npv": "111383187",
        "developer_profit": "163027326",
        "developer_equity": "7000000",
        "developer_irr": "1.1707",
        "developer_npv": "97051343",
        "developer_moic": "24.29",
        "fair_floor": "0.095",
        "balanced": "0.123",
        "policy_ceiling": "0.141",
        "technical_ceiling": "0.199",
    }
    portal_reference = scenarios["old_platform_reference"]
    portal_summary = portal_reference["summary"]
    portal_neg = portal_reference["selected_mechanism_negotiation"]
    comparison = {
        "nominal_economics": {
            "gross_sales_delta": str(D(portal_summary["gross_sales"]) - D(old_reference["gross_sales"])),
            "development_cost_delta": str(D(portal_summary["development_cost"]) - D(old_reference["development_cost"])),
            "landowner_nominal_delta": str(D(portal_summary["government_consideration"]) - D(old_reference["landowner_nominal"])),
            "developer_profit_delta": str(D(portal_summary["developer_profit"]) - D(old_reference["developer_profit"])),
        },
        "timing_sensitive_metrics": {
            "portal_developer_irr": portal_summary.get("developer_equity_irr"),
            "old_developer_irr": old_reference["developer_irr"],
            "portal_developer_npv": portal_summary.get("developer_equity_npv"),
            "old_developer_npv": old_reference["developer_npv"],
            "portal_landowner_npv": portal_summary.get("government_consideration_npv"),
            "old_landowner_npv": old_reference["landowner_npv"],
            "explanation": "NPV/IRR differences are timing-sensitive because the screenshot set does not expose the old platform's complete monthly curves and collection rules.",
        },
        "negotiation": {
            "portal_fair_floor": portal_neg.get("fair_floor"),
            "old_fair_floor": old_reference["fair_floor"],
            "portal_balanced": portal_neg.get("balanced"),
            "old_balanced": old_reference["balanced"],
            "portal_policy_ceiling": portal_neg.get("policy_adjusted_ceiling"),
            "old_policy_ceiling": old_reference["policy_ceiling"],
            "portal_technical_ceiling": portal_neg.get("technical_ceiling"),
            "old_technical_ceiling": old_reference["technical_ceiling"],
            "portal_residual_equivalent": portal_neg.get("residual_equivalent_measure"),
        },
    }

    result = {
        "release": "LandValue360 Financial Portal v2.4.0",
        "validation_type": "end-to-end policy, negotiation and scenario matrix",
        "status": "PASS",
        "assertions_passed": sum(1 for row in assertions if row["passed"]),
        "assertions_total": len(assertions),
        "old_platform_reference": old_reference,
        "old_platform_comparison": comparison,
        "policies": {
            "standard": {"id": current["id"], "version_number": current["version_number"]},
            "gross_standard": {"id": gross_standard["id"], "version_number": gross_standard["version_number"]},
            "conservative": {"id": conservative["id"], "version_number": conservative["version_number"]},
            "growth": {"id": growth["id"], "version_number": growth["version_number"]},
            "net_sales": {"id": net_policy["id"], "version_number": net_policy["version_number"]},
            "profit_share": {"id": profit_policy["id"], "version_number": profit_policy["version_number"]},
            "financed": {"id": financed["id"], "version_number": financed["version_number"]},
        },
        "scenarios": scenarios,
        "assertions": assertions,
    }
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "assertions": f"{result['assertions_passed']}/{result['assertions_total']}",
        "scenario_count": len(scenarios),
        "output": str(output),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
