#!/usr/bin/env python3
"""Validate the vendored Platform 2.1.1 core against its official Golden Cases."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from landvalue360_government.contracts import evaluate_contract
from landvalue360_kernel.monthly_engine import run_monthly_kernel
from landvalue360_kernel.manifest import ENGINE_VERSION

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "validation" / "golden_cases"
DEFAULT_OUTPUT = ROOT / "release_artifacts" / "golden-cases-2.1.1.json"
TOLERANCE = Decimal("0.000001")
PROJECT_TOLERANCE = Decimal("0.000000000000000000000001")


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def monthly_dates() -> list[date]:
    return [date(2026 + ((month - 1) // 12), ((month - 1) % 12) + 1, 1) for month in range(1, 14)]


def project_actual_metrics(case: dict[str, Any]) -> dict[str, Decimal]:
    dates = monthly_dates()
    cost = d(case["development_cost"])
    sales = d(case["gross_sales"])
    share = d(case["landowner_share"])
    discount = d(case["discount_rate"])
    receipts = [Decimal("0") for _ in dates]
    receipts[-1] = sales
    contractual = [Decimal("0") for _ in dates]
    contractual[-1] = sales * share
    schedule = [Decimal("0") for _ in dates]
    schedule[0] = cost
    kernel = run_monthly_kernel(
        dates=dates,
        receipts=receipts,
        cost_items=[{"cost_id": "GOLDEN-COST", "schedule": schedule, "priority": 10, "deferrable": False}],
        contractual_payments=contractual,
        committed_equity=d(case["committed_equity"]),
        committed_debt=Decimal("0"),
        finance_model={
            "enabled": False,
            "spend_policy": "CASH_DRIVEN",
            "annual_interest_rate": "0",
            "upfront_fee_rate": "0",
            "commitment_fee_rate": "0",
            "minimum_cash_balance": "0",
            "maximum_extension_months": 0,
        },
        distribution_policy={
            "enabled": True,
            "frequency_code": "PROJECT_END",
            "first_distribution_month": 13,
            "future_cost_reserve_share": "0.25",
            "minimum_operating_cash": "0",
            "allocation_method": "CONTRACTUAL_ACCRUAL_FIRST",
            "contractual_payment_timing": "AS_ACCRUED",
            "recover_developer_advances_before_landowner_cash": True,
        },
        original_completion_index=12,
    )
    landowner = kernel["total_landowner_cash_receipts"]
    equity = kernel["total_equity_contributed"]
    developer_receipts = kernel["total_developer_equity_receipts"]
    return {
        "gross_sales": sales,
        "development_cost": kernel["total_executed_cost"],
        "project_operating_profit": sales - kernel["total_executed_cost"],
        "landowner_nominal_consideration": landowner,
        "landowner_consideration_npv": landowner / (Decimal("1") + discount),
        "developer_net_profit": sales - kernel["total_executed_cost"] - landowner,
        "equity_contributed": equity,
        "project_irr": sales / cost - Decimal("1"),
        "project_npv": -cost + sales / (Decimal("1") + discount),
        "developer_equity_irr": developer_receipts / equity - Decimal("1"),
        "developer_equity_npv": -equity + developer_receipts / (Decimal("1") + discount),
        "developer_moic": developer_receipts / equity,
        "funding_gap": kernel["unsupported_funding_gap"],
        "developer_distributions": kernel["total_developer_distributions"],
        "landowner_distributions": kernel["total_landowner_cash_receipts"],
        "ending_cash": kernel["ending_cash"],
        "terminal_debt": kernel["ending_debt"],
        "deferred_cost": kernel["terminal_backlog"],
        "contractual_arrears": kernel["contractual_arrears"],
        "finance_arrears": kernel["finance_arrears"],
        "mandatory_shortfall": kernel["mandatory_shortfall"],
    }


def validate_contract_cases() -> list[dict[str, Any]]:
    cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        result = evaluate_contract(
            case["ledger"],
            case["contract"],
            base_date=case["ledger"][0]["date"],
            discount_rate=case["discount_rate"],
        )
        mismatches = []
        for key, expected in case["expected"].items():
            actual = result.get(key)
            if actual is None or abs(d(actual) - d(expected)) > TOLERANCE:
                mismatches.append({"field": key, "expected": expected, "actual": None if actual is None else str(actual)})
        results.append({"id": case["id"], "passed": not mismatches, "mismatches": mismatches})
    return results


def validate_project_cases() -> list[dict[str, Any]]:
    cases = json.loads((FIXTURES / "project_cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        actual = project_actual_metrics(case)
        mismatches = [
            {"field": key, "expected": expected, "actual": str(actual.get(key))}
            for key, expected in case["expected"].items()
            if key not in actual or abs(actual[key] - d(expected)) > PROJECT_TOLERANCE
        ]
        results.append({"id": case["id"], "passed": not mismatches, "mismatches": mismatches})
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    contract_results = validate_contract_cases()
    project_results = validate_project_cases()
    all_results = contract_results + project_results
    payload = {
        "engine_version": ENGINE_VERSION,
        "fixture_source": "LandValue360 Platform 2.1.1 supplied release",
        "independent_expected_values": True,
        "fixture_hashes": {
            "cases.json": sha256_file(FIXTURES / "cases.json"),
            "project_cases.json": sha256_file(FIXTURES / "project_cases.json"),
        },
        "contract_cases": {"count": len(contract_results), "passed": sum(row["passed"] for row in contract_results), "results": contract_results},
        "project_cases": {"count": len(project_results), "passed": sum(row["passed"] for row in project_results), "results": project_results},
        "total_cases": len(all_results),
        "total_passed": sum(row["passed"] for row in all_results),
        "status": "PASS" if all(row["passed"] for row in all_results) else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
