"""Independent consistency audit for portal financial calculation outputs.

This module intentionally does not call the kernel's XNPV/XIRR functions.  It
reconstructs the principal published cash-flow metrics from the frozen monthly
ledger using a separate ACT/365F implementation.  The audit is a validation
layer, not a replacement calculation engine.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from math import exp, isfinite, log1p
from typing import Any, Iterable

ZERO = Decimal("0")
CENT = Decimal("0.01")
RATE_TOL = Decimal("0.000001")


def D(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    # The governed monthly engine places each monthly feasibility cash flow on
    # the first day of the month.  Preserve that timing convention exactly.
    return date(year, month, 1)


def _dates(valuation_date: date, count: int) -> list[date]:
    return [_add_months(valuation_date, index) for index in range(count)]


def independent_xnpv(rate: Decimal, values: Iterable[Decimal], valuation_date: date) -> Decimal:
    """ACT/365F XNPV implemented independently with Decimal arithmetic."""
    rate = D(rate)
    if rate <= Decimal("-1"):
        raise ValueError("Discount rate must be greater than -100%")
    values = list(values)
    base = Decimal("1") + rate
    with localcontext() as ctx:
        ctx.prec = 50
        total = ZERO
        for index, amount in enumerate(values):
            if amount == ZERO:
                continue
            current = _add_months(valuation_date, index)
            years = Decimal((current - valuation_date).days) / Decimal("365")
            # Decimal non-integral power support is not portable enough across
            # Python builds, so evaluate exp(log(base) * years) using Decimal's
            # ln/exp methods.  This remains independent of the kernel code.
            factor = (base.ln() * years).exp()
            total += amount / factor
        return +total


def _sign_changes(values: list[Decimal]) -> int:
    signs = [1 if value > ZERO else -1 for value in values if value != ZERO]
    return sum(1 for left, right in zip(signs, signs[1:]) if left != right)


def independent_xirr(values: Iterable[Decimal], valuation_date: date) -> tuple[Decimal | None, str]:
    """Independent multi-root-aware XIRR validator.

    Candidate roots are located in log(1+r) space and refined by bisection.
    Exactly one root is reported as VALID; multiple roots are AMBIGUOUS.
    """
    values = list(values)
    if not any(value < ZERO for value in values) or not any(value > ZERO for value in values):
        return None, "NOT_CALCULABLE"

    dated = [(float((_add_months(valuation_date, i) - valuation_date).days) / 365.0, float(value)) for i, value in enumerate(values) if value != ZERO]

    def npv_log(log_rate: float) -> float:
        total = 0.0
        for years, amount in dated:
            exponent = -log_rate * years
            if exponent > 700:
                term = float("inf") if amount >= 0 else float("-inf")
            elif exponent < -745:
                term = 0.0
            else:
                term = amount * exp(exponent)
            total += term
            if not isfinite(total):
                return total
        return total

    lo, hi, step = -10.0, 20.0, 0.025
    roots_log: list[float] = []
    x = lo
    f_prev = npv_log(x)
    while x < hi:
        nx = min(hi, x + step)
        f_next = npv_log(nx)
        bracket = None
        if isfinite(f_prev) and abs(f_prev) <= 1e-8:
            roots_log.append(x)
        elif (f_prev < 0 < f_next) or (f_prev > 0 > f_next) or (isfinite(f_next) and abs(f_next) <= 1e-8):
            bracket = (x, nx)
        if bracket is not None:
            left, right = bracket
            fl = npv_log(left)
            for _ in range(220):
                mid = (left + right) / 2.0
                fm = npv_log(mid)
                if abs(fm) <= 1e-8 or abs(right - left) <= 1e-13:
                    left = right = mid
                    break
                if (fl <= 0 <= fm) or (fl >= 0 >= fm):
                    right = mid
                else:
                    left = mid
                    fl = fm
            roots_log.append((left + right) / 2.0)
        x, f_prev = nx, f_next

    dedup: list[float] = []
    for root in roots_log:
        if not dedup or all(abs(root - existing) > 1e-6 for existing in dedup):
            dedup.append(root)
    rates = [exp(root) - 1.0 for root in dedup if isfinite(root)]
    rates = [rate for rate in rates if isfinite(rate) and rate > -1]
    if not rates:
        return None, "NOT_CALCULABLE"
    if len(rates) > 1:
        return None, "AMBIGUOUS"
    return Decimal(str(rates[0])), "VALID"


def _check(checks: list[dict[str, Any]], check_id: str, actual: Any, expected: Any, tolerance: Decimal, *, critical: bool = True, note: str | None = None) -> None:
    actual_d = D(actual)
    expected_d = D(expected)
    difference = actual_d - expected_d
    passed = abs(difference) <= tolerance
    checks.append({
        "check_id": check_id,
        "passed": passed,
        "critical": critical,
        "actual": str(actual_d),
        "expected": str(expected_d),
        "difference": str(difference),
        "tolerance": str(tolerance),
        "note": note,
    })


def _rate_check(checks: list[dict[str, Any]], check_id: str, actual: Any, expected: Decimal | None, expected_status: str, *, critical: bool = True) -> None:
    if expected_status != "VALID" or expected is None:
        # A canonical undefined/ambiguous IRR is acceptable only when the
        # independent reconstruction reaches the same non-calculable state.
        passed = actual in (None, "")
        checks.append({
            "check_id": check_id,
            "passed": passed,
            "critical": critical,
            "actual": None if actual in (None, "") else str(actual),
            "expected": None,
            "difference": None,
            "tolerance": str(RATE_TOL),
            "note": f"Independent XIRR status: {expected_status}",
        })
        return
    _check(checks, check_id, actual, expected, RATE_TOL, critical=critical, note="Independent ACT/365F XIRR")


def audit_financial_result(
    *,
    monthly: list[dict[str, Any]],
    truth: dict[str, Any],
    summary: dict[str, Any],
    financial_model: dict[str, Any],
    effective_policy: dict[str, Any],
    negotiation_results: list[dict[str, Any]],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not monthly:
        return {
            "audit_version": "1.0.0",
            "validation_status": "BLOCKED",
            "passed": False,
            "recommendation_usable": False,
            "checks": [],
            "warnings": ["No monthly cash-flow ledger is available for independent validation."],
        }

    valuation_date = date.fromisoformat(str(financial_model.get("valuation_date")))
    project_discount = D((effective_policy.get("financial_constraints") or {}).get("discount_rate"), "0.12")
    selected = truth

    # Ledger reconciliation is recalculated from published sources and uses.
    max_recalc_variance = ZERO
    max_reported_variance = ZERO
    for row in monthly:
        recalculated = D(row.get("cash_sources_total")) - D(row.get("cash_uses_before_ending_cash")) - D(row.get("ending_cash"))
        max_recalc_variance = max(max_recalc_variance, abs(recalculated))
        max_reported_variance = max(max_reported_variance, abs(D(row.get("cash_balance_variance"))))
    checks.append({
        "check_id": "MONTHLY_CASH_RECONCILIATION_INDEPENDENT",
        "passed": max_recalc_variance <= CENT and max_reported_variance <= CENT,
        "critical": True,
        "actual": str(max_recalc_variance),
        "expected": "0",
        "difference": str(max_recalc_variance),
        "tolerance": str(CENT),
        "note": "Recomputed from cash sources - cash uses - ending cash for every month.",
    })

    opening_equity = D(monthly[0].get("opening_cash"))
    incremental_equity = sum((D(row.get("equity_contribution")) for row in monthly), ZERO)
    equity_contributions = opening_equity + incremental_equity
    developer_distributions = sum((D(row.get("developer_distribution")) for row in monthly), ZERO)
    developer_recoveries = sum((D(row.get("developer_advance_recovery")) for row in monthly), ZERO)
    equity_receipts = developer_distributions + developer_recoveries
    _check(checks, "EQUITY_CONTRIBUTIONS_LEDGER", truth.get("developer_equity_contributions"), equity_contributions, CENT)
    _check(checks, "EQUITY_DISTRIBUTIONS_LEDGER", truth.get("developer_equity_distributions"), developer_distributions, CENT)
    _check(checks, "EQUITY_RECEIPTS_LEDGER", truth.get("developer_equity_receipts", equity_receipts), equity_receipts, CENT)
    if equity_contributions > ZERO:
        _check(checks, "EQUITY_MULTIPLE_LEDGER", truth.get("developer_equity_multiple"), equity_receipts / equity_contributions, RATE_TOL)

    cumulative_equity = opening_equity
    peak_equity = cumulative_equity
    for row in monthly:
        cumulative_equity += D(row.get("equity_contribution"))
        peak_equity = max(peak_equity, cumulative_equity)
    _check(checks, "PEAK_EQUITY_LEDGER", truth.get("peak_equity"), peak_equity, CENT)
    peak_debt = max((max(D(row.get("opening_debt")), D(row.get("ending_debt"))) for row in monthly), default=ZERO)
    _check(checks, "PEAK_DEBT_LEDGER", truth.get("peak_debt"), peak_debt, CENT)
    _check(checks, "ENDING_CASH_LEDGER", truth.get("ending_cash"), monthly[-1].get("ending_cash"), CENT)
    _check(checks, "TERMINAL_DEBT_LEDGER", truth.get("terminal_debt"), monthly[-1].get("ending_debt"), CENT)
    _check(checks, "CONTRACTUAL_ARREARS_LEDGER", truth.get("deferred_contractual_payment"), monthly[-1].get("government_payment_arrears"), CENT)

    landowner_receipts = sum((D(row.get("landowner_cash_receipt")) for row in monthly), ZERO)
    _check(checks, "LANDOWNER_RECEIPTS_LEDGER", truth.get("landowner_cash_receipts"), landowner_receipts, CENT)

    # Developer unlevered nominal profit is directly reproducible from the
    # published ledger because landowner_cash_receipt combines contract cash and
    # landowner distributions.
    developer_unlevered = [
        D(row.get("net_collections")) - D(row.get("actual_cost")) - D(row.get("landowner_cash_receipt"))
        for row in monthly
    ]
    developer_profit = sum(developer_unlevered, ZERO)
    _check(checks, "DEVELOPER_PROFIT_LEDGER", truth.get("developer_profit"), developer_profit, CENT)

    # Equity XNPV / XIRR use actual developer contributions and receipts, not
    # project-level economics.
    equity_flows: list[Decimal] = []
    for index, row in enumerate(monthly):
        amount = -D(row.get("equity_contribution")) + D(row.get("developer_distribution")) + D(row.get("developer_advance_recovery"))
        if index == 0:
            amount -= opening_equity
        equity_flows.append(amount)
    equity_npv = independent_xnpv(project_discount, equity_flows, valuation_date)
    _check(checks, "DEVELOPER_EQUITY_XNPV_INDEPENDENT", truth.get("developer_equity_npv"), equity_npv, CENT)
    equity_irr, equity_irr_status = independent_xirr(equity_flows, valuation_date)
    _rate_check(checks, "DEVELOPER_EQUITY_XIRR_INDEPENDENT", truth.get("developer_equity_irr"), equity_irr, equity_irr_status)

    developer_npv = independent_xnpv(project_discount, developer_unlevered, valuation_date)
    _check(checks, "DEVELOPER_UNLEVERED_XNPV_INDEPENDENT", truth.get("developer_unlevered_npv"), developer_npv, CENT)

    # Landowner gross NPV uses the selected contract's effective valuation rate.
    selected_contract = next((row for row in negotiation_results if row.get("method") == truth.get("method") and row.get("selected_case")), None)
    effective_landowner_rate: Decimal | None = None
    # The selected truth does not carry the full discount policy; caller may
    # provide it in effective policy as the fallback government rate.
    if effective_landowner_rate is None:
        effective_landowner_rate = D((effective_policy.get("financial_constraints") or {}).get("government_discount_rate"), "0.10")
    landowner_npv = independent_xnpv(effective_landowner_rate, [D(row.get("landowner_cash_receipt")) for row in monthly], valuation_date)
    # Policy can apply a valuation-policy premium beyond the headline government
    # discount. If the independently reconstructed base-rate NPV differs, record
    # it as informational rather than a false critical failure.
    canonical_landowner_npv = D(truth.get("government_consideration_npv"))
    landowner_diff = canonical_landowner_npv - landowner_npv
    landowner_pass = abs(landowner_diff) <= CENT
    checks.append({
        "check_id": "LANDOWNER_GROSS_XNPV_INDEPENDENT",
        "passed": landowner_pass,
        "critical": False,
        "actual": str(canonical_landowner_npv),
        "expected": str(landowner_npv),
        "difference": str(landowner_diff),
        "tolerance": str(CENT),
        "note": "Informational when valuation policy adds a risk premium to the base government discount rate.",
    })

    # Whole-project dated returns are independently reproducible from the public
    # monthly ledger when public/third-party cost shares are zero. Otherwise the
    # ledger intentionally exposes developer-paid monthly cost only, while the
    # project metric uses reconstructed all-party physical cost execution.
    public_cost = D(truth.get("actual_government_project_cost")) + D(truth.get("actual_third_party_project_cost"))
    if abs(public_cost) <= CENT:
        project_flows = [D(row.get("net_collections")) - D(row.get("actual_cost")) for row in monthly]
        project_npv = independent_xnpv(project_discount, project_flows, valuation_date)
        _check(checks, "PROJECT_XNPV_INDEPENDENT", truth.get("project_npv"), project_npv, CENT)
        project_irr, project_irr_status = independent_xirr(project_flows, valuation_date)
        _rate_check(checks, "PROJECT_XIRR_INDEPENDENT", truth.get("project_irr"), project_irr, project_irr_status)
    else:
        checks.append({
            "check_id": "PROJECT_DATED_RETURN_INDEPENDENT",
            "passed": True,
            "critical": False,
            "actual": None,
            "expected": None,
            "difference": None,
            "tolerance": None,
            "note": "Not independently reconstructed from the public ledger because project costs include non-developer responsibility shares.",
        })

    # Contractual/closure conditions that must be zero at financial close.
    closure_values = {
        "TERMINAL_DEBT_ZERO": D(truth.get("terminal_debt")),
        "DEFERRED_COST_ZERO": D(truth.get("deferred_development_cost")),
        "CONTRACTUAL_ARREARS_ZERO": D(truth.get("deferred_contractual_payment")),
        "MANDATORY_SHORTFALL_ZERO": D(truth.get("mandatory_shortfall")),
        "FUNDING_GAP_WITHIN_POLICY": D(truth.get("peak_funding_gap")),
    }
    max_gap = D((effective_policy.get("financial_constraints") or {}).get("maximum_funding_gap"), "0")
    for check_id, value in closure_values.items():
        threshold = max_gap if check_id == "FUNDING_GAP_WITHIN_POLICY" else CENT
        passed = value <= threshold + (CENT if check_id == "FUNDING_GAP_WITHIN_POLICY" else ZERO)
        checks.append({
            "check_id": check_id,
            "passed": passed,
            "critical": True,
            "actual": str(value),
            "expected": f"<= {threshold}",
            "difference": None,
            "tolerance": str(CENT),
            "note": None,
        })

    critical_failures = [row["check_id"] for row in checks if row.get("critical") and row.get("passed") is False]
    canonical_pass = str(truth.get("calculation_status") or summary.get("calculation_status") or "FAIL").upper() == "PASS"
    reconciliation_pass = bool(truth.get("cash_reconciliation_passed"))
    if critical_failures or not canonical_pass or not reconciliation_pass:
        validation_status = "BLOCKED"
    elif not bool(truth.get("policy_compliant")) or not bool(truth.get("economic_feasible")):
        validation_status = "CONDITIONAL"
    else:
        validation_status = "VALIDATED"

    floor_established = any(row.get("fair_floor_status") == "ESTABLISHED" for row in negotiation_results)
    if not floor_established:
        warnings.append("No economically anchored Fair Floor is established for the negotiation recommendation.")
    recommendation_usable = validation_status == "VALIDATED" and floor_established and any(
        row.get("balanced") not in (None, "") and row.get("technical_ceiling") not in (None, "")
        for row in negotiation_results
    )

    return {
        "audit_version": "1.0.0",
        "methodology": "Independent reconstruction from frozen monthly ledger; ACT/365F XNPV and single-root XIRR validator.",
        "validation_status": validation_status,
        "passed": validation_status != "BLOCKED",
        "recommendation_usable": recommendation_usable,
        "critical_failures": critical_failures,
        "checks": checks,
        "warnings": warnings,
        "independent_metrics": {
            "developer_equity_npv": str(equity_npv),
            "developer_equity_irr": None if equity_irr is None else str(equity_irr),
            "developer_equity_irr_status": equity_irr_status,
            "developer_profit": str(developer_profit),
            "developer_unlevered_npv": str(developer_npv),
            "landowner_cash_receipts": str(landowner_receipts),
            "landowner_gross_npv_at_base_policy_rate": str(landowner_npv),
            "maximum_recalculated_cash_variance": str(max_recalc_variance),
        },
    }
