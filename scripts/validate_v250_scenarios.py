#!/usr/bin/env python3
"""Audit the v2.5.0 multi-project contract scenario matrix.

The matrix is generated from real disposable portal projects and calculation
runs. This validator independently checks contract-base arithmetic, ordering,
monotonicity, feasibility disclosure, policy-version effects and the two
regression cases that exposed the v2.3 negotiation defect.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

D = lambda value: Decimal(str(value or 0))
TOL = Decimal("0.02")
RATES = [Decimal("0"), Decimal("0.10"), Decimal("0.18"), Decimal("0.25"), Decimal("0.35"), Decimal("0.50")]
METHODS = ("GROSS_SALES", "NET_SALES", "PROFIT_SHARE")


def close(actual: Any, expected: Any, tolerance: Decimal = TOL) -> bool:
    return abs(D(actual) - D(expected)) <= tolerance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("release_artifacts/contract-scenario-tests-v2.5.0.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("release_artifacts/contract-scenario-audit-v2.5.0.json"),
    )
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    failures: list[dict[str, Any]] = []
    checks = 0

    def check(code: str, condition: bool, message: str, context: Any = None) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            failures.append({"code": code, "message": message, "context": context})

    check("STATUS", source.get("status") == "PASS", "Source matrix must be marked PASS.", source.get("status"))
    check("PORTAL_VERSION", source.get("portal_version") == "2.5.0", "Unexpected portal version.", source.get("portal_version"))
    check("ADAPTER_VERSION", source.get("portal_adapter_version") == "2.5.0", "Unexpected adapter version.", source.get("portal_adapter_version"))
    check("CONTRACT_ENGINE_VERSION", source.get("contract_engine_version") == "3.1.0", "Unexpected contract engine version.", source.get("contract_engine_version"))
    results = source.get("results") or []
    check("SCENARIO_COUNT", len(results) == 8 == int(source.get("scenario_count") or 0), "Eight scenario projects are required.", len(results))

    scenario_by_code = {str(row.get("code")): row for row in results}
    required_codes = {
        "CHEAP_HIGH_PROFIT", "EXPENSIVE_HIGH_PROFIT", "ULTRA_PROFIT_POLICY_CAP",
        "LOW_MARGIN_FEASIBLE", "EXPENSIVE_LOW_MARGIN", "LOSS_MAKING",
        "DOUMA_REGRESSION", "OLD_PLATFORM_REFERENCE",
    }
    check("SCENARIO_SET", set(scenario_by_code) == required_codes, "Scenario set is incomplete or unexpected.", sorted(scenario_by_code))

    candidate_count = 0
    for scenario in results:
        code = str(scenario.get("code"))
        matrix = scenario.get("candidate_matrix") or {}
        gross_base = D(scenario.get("gross_sales"))
        net_base = D(scenario.get("net_sales"))
        selected_offer = D(scenario.get("selected_offer"))
        selected_nominal = D(scenario.get("selected_landowner_nominal"))
        expected_weak = code in {"LOW_MARGIN_FEASIBLE", "EXPENSIVE_LOW_MARGIN", "LOSS_MAKING", "DOUMA_REGRESSION"}
        audit_status = scenario.get("financial_audit")
        recommendation_status = scenario.get("recommendation_validation")
        check(
            f"{code}_AUDIT",
            audit_status in ({"VALIDATED", "CONDITIONAL"} if expected_weak else {"VALIDATED"}),
            "Scenario audit status is inconsistent with the project economics.",
            audit_status,
        )
        check(
            f"{code}_RECOMMENDATION",
            recommendation_status in ({"SUPPORTED", "CONDITIONAL"} if expected_weak else {"SUPPORTED"}),
            "Scenario recommendation status is inconsistent with the project economics.",
            recommendation_status,
        )
        check(f"{code}_RESIDUAL", D((scenario.get("gross_sales_negotiation") or {}).get("residual_land_value")) != 0, "Residual land value comparison must be present.")

        if code not in {"LOSS_MAKING"}:
            check(
                f"{code}_SELECTED_OFFER_BASIS",
                close(selected_nominal, gross_base * selected_offer),
                "Selected gross-sales offer must apply to gross sales.",
                {"base": str(gross_base), "rate": str(selected_offer), "actual": str(selected_nominal)},
            )

        for method in METHODS:
            rows = matrix.get(method) or []
            candidate_count += len(rows)
            check(f"{code}_{method}_COUNT", len(rows) == len(RATES), "Each method must contain six candidate rates.", len(rows))
            actual_rates = [D(row.get("rate")) for row in rows]
            check(f"{code}_{method}_RATES", actual_rates == RATES, "Candidate rates differ from the approved matrix.", [str(x) for x in actual_rates])
            public_values = [D(row.get("public_nominal")) for row in rows]
            developer_npvs = [D(row.get("developer_npv")) for row in rows]
            check(
                f"{code}_{method}_LANDOWNER_MONOTONIC",
                all(b + TOL >= a for a, b in zip(public_values, public_values[1:])),
                "Landowner nominal value must not fall when the contractual rate rises.",
                [str(x) for x in public_values],
            )
            check(
                f"{code}_{method}_DEVELOPER_NPV_MONOTONIC",
                all(b <= a + TOL for a, b in zip(developer_npvs, developer_npvs[1:])),
                "Developer NPV must not rise when the landowner rate rises.",
                [str(x) for x in developer_npvs],
            )
            base = gross_base if method == "GROSS_SALES" else net_base if method == "NET_SALES" else None
            for row in rows:
                rate = D(row.get("rate"))
                failed = set(row.get("failed_constraints") or [])
                check(f"{code}_{method}_{rate}_CALC", bool(row.get("calculation_valid")), "Candidate calculation must be valid.", row)
                check(f"{code}_{method}_{rate}_RECON", bool(row.get("cash_reconciliation_passed")), "Candidate monthly cash must reconcile.", row)
                if base is not None:
                    expected = base * rate
                    actual = D(row.get("public_nominal"))
                    if bool(row.get("feasible")):
                        check(
                            f"{code}_{method}_{rate}_BASIS",
                            close(actual, expected),
                            "Feasible rate contract must apply exactly to its disclosed sales base.",
                            {"base": str(base), "rate": str(rate), "expected": str(expected), "actual": str(actual)},
                        )
                    elif actual + TOL < expected:
                        check(
                            f"{code}_{method}_{rate}_SHORTFALL_DISCLOSED",
                            bool(failed & {"MANDATORY_PAYMENT_SHORTFALL", "SELECTED_CONTRACT_CONSTRAINTS_PASS", "COMPLETE_SCOPE"}),
                            "An infeasible underpaid contractual entitlement must disclose a failed shortfall/contract constraint.",
                            {"expected": str(expected), "actual": str(actual), "failed_constraints": sorted(failed)},
                        )

        for label in ("gross_sales_negotiation", "net_sales_negotiation"):
            row = scenario.get(label) or {}
            if row.get("status") in {"VALID_RANGE", "NONCONTIGUOUS_FEASIBLE_REGION"}:
                points = [D(row.get(name)) for name in ("fair_floor", "balanced", "policy_adjusted_ceiling", "technical_ceiling")]
                check(
                    f"{code}_{label}_ORDER",
                    points[0] <= points[1] <= points[2] <= points[3],
                    "Negotiation points must be ordered Fair Floor <= Balanced <= Policy Ceiling <= Technical Ceiling.",
                    [str(x) for x in points],
                )
                check(f"{code}_{label}_RESIDUAL_MARKER", row.get("residual_equivalent_measure") not in (None, ""), "Residual equivalent marker is required.")

    check("CANDIDATE_COUNT", candidate_count == 144 == int(source.get("candidate_point_count") or 0), "The matrix must contain 144 candidate points.", candidate_count)

    douma = scenario_by_code.get("DOUMA_REGRESSION") or {}
    douma_50 = next((r for r in (douma.get("candidate_matrix") or {}).get("NET_SALES", []) if D(r.get("rate")) == Decimal("0.50")), None)
    check("DOUMA_50_EXISTS", douma_50 is not None, "Douma 50% net-sales regression point is missing.")
    if douma_50:
        check("DOUMA_50_REJECTED", not bool(douma_50.get("feasible")), "Douma 50% net-sales share must be rejected as infeasible.", douma_50)
        check("DOUMA_50_ARITHMETIC", close(D(douma_50.get("public_nominal")), D(douma.get("net_sales")) * Decimal("0.50")) or bool(douma_50.get("failed_constraints")), "Douma entitlement or shortfall disclosure is inconsistent.", douma_50)

    old = scenario_by_code.get("OLD_PLATFORM_REFERENCE") or {}
    check("OLD_GROSS_SALES", close(old.get("gross_sales"), "877500000"), "Old-platform reference gross sales mismatch.", old.get("gross_sales"))
    check("OLD_COST", close(old.get("development_cost"), "556522674"), "Old-platform reference cost mismatch.", old.get("development_cost"))
    check("OLD_18_PERCENT", close(old.get("selected_landowner_nominal"), "157950000"), "18% gross-sales consideration mismatch.", old.get("selected_landowner_nominal"))
    old_neg = old.get("gross_sales_negotiation") or {}
    check("OLD_BALANCED", Decimal("0.120") <= D(old_neg.get("balanced")) <= Decimal("0.126"), "Balanced point is outside the old-platform reference band.", old_neg.get("balanced"))
    check("OLD_POLICY_CEILING", Decimal("0.138") <= D(old_neg.get("policy_adjusted_ceiling")) <= Decimal("0.144"), "Policy-adjusted ceiling is outside the reference band.", old_neg.get("policy_adjusted_ceiling"))
    check("OLD_TECHNICAL", Decimal("0.195") <= D(old_neg.get("technical_ceiling")) <= Decimal("0.202"), "Technical ceiling is outside the reference band.", old_neg.get("technical_ceiling"))
    check("OLD_RESIDUAL", Decimal("0.160") <= D(old_neg.get("residual_equivalent_measure")) <= Decimal("0.170"), "Residual equivalent is outside the reference band.", old_neg.get("residual_equivalent_measure"))

    policy = source.get("policy_version_selection_test") or {}
    check("POLICY_TEST_PRESENT", bool(policy), "Policy-version selection test is missing.")
    if policy:
        check("POLICY_BALANCED_CHANGES", D(policy.get("alternate_balanced")) < D(policy.get("base_balanced")), "Alternate policy must change the Balanced recommendation.", policy)
        check("POLICY_CEILING_CHANGES", D(policy.get("alternate_policy_ceiling")) < D(policy.get("base_policy_ceiling")), "Alternate policy must change the policy-adjusted ceiling.", policy)
        check("POLICY_TECHNICAL_STABLE", close(policy.get("alternate_technical_ceiling"), policy.get("base_technical_ceiling"), Decimal("0.00002")), "Policy choice must not change the underlying technical ceiling.", policy)
        check("POLICY_RUN_HASHED", bool(policy.get("alternate_run_input_hash")) and bool(policy.get("alternate_run_result_hash")), "Policy-selected run hashes are required.", policy)

    result = {
        "status": "PASS" if not failures else "FAIL",
        "source": str(args.input),
        "portal_version": source.get("portal_version"),
        "contract_engine_version": source.get("contract_engine_version"),
        "scenario_count": len(results),
        "candidate_point_count": candidate_count,
        "checks_executed": checks,
        "failure_count": len(failures),
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
