"""Financial mathematics for dated cash flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import Enum
from math import exp as float_exp, isfinite

from .cashflow import CashFlowSeries
from .dates import DayCountBasis, year_fraction
from .decimal_utils import DECIMAL_PRECISION, ONE, ZERO, as_json_number, decimal, decimal_exp, decimal_ln, decimal_power
from .exceptions import CalculationError


class MetricStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_CALCULABLE = "NOT_CALCULABLE"
    ERROR = "ERROR"


class XirrStatus(str, Enum):
    VALID = "VALID"
    NOT_CALCULABLE = "NOT_CALCULABLE"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class XirrResult:
    status: XirrStatus
    rate: Decimal | None
    roots: tuple[Decimal, ...]
    iterations: int
    message: str
    day_count_basis: DayCountBasis = DayCountBasis.ACT_365F

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "rate": as_json_number(self.rate),
            "roots": [as_json_number(root) for root in self.roots],
            "iterations": self.iterations,
            "message": self.message,
            "day_count_basis": self.day_count_basis.value,
        }


def _validate_rate(rate: Decimal) -> Decimal:
    rate = decimal(rate)
    if rate <= Decimal("-1"):
        raise CalculationError("Discount rate must be greater than -100%.")
    return rate


def xnpv(
    rate: Decimal,
    series: CashFlowSeries,
    *,
    valuation_date: date | None = None,
    basis: DayCountBasis = DayCountBasis.ACT_365F,
) -> Decimal:
    """Calculate XNPV using explicit dates and Decimal arithmetic."""

    rate = _validate_rate(rate)
    if not series.flows:
        return ZERO
    valuation_date = valuation_date or series.flows[0].date
    base = ONE + rate
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        total = ZERO
        for flow in series.flows:
            exponent = year_fraction(valuation_date, flow.date, basis)
            factor = decimal_power(base, exponent)
            total += flow.amount / factor
        return +total


def _xnpv_log_rate(
    log_rate: Decimal,
    series: CashFlowSeries,
    valuation_date: date,
    basis: DayCountBasis,
) -> Decimal:
    """Calculate NPV as a function of ``log(1 + r)``.

    This transformation maps the entire valid IRR domain ``r > -1`` to the
    unbounded real line and makes bracketing more reliable.
    """

    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        total = ZERO
        for flow in series.flows:
            years = year_fraction(valuation_date, flow.date, basis)
            total += flow.amount * decimal_exp(-log_rate * years)
        return +total


def _sign(value: Decimal, tolerance: Decimal) -> int:
    if abs(value) <= tolerance:
        return 0
    return 1 if value > ZERO else -1


def _bisect_log_root(
    series: CashFlowSeries,
    valuation_date: date,
    basis: DayCountBasis,
    left: Decimal,
    right: Decimal,
    *,
    value_tolerance: Decimal,
    interval_tolerance: Decimal,
    max_iterations: int,
) -> tuple[Decimal, int]:
    f_left = _xnpv_log_rate(left, series, valuation_date, basis)
    f_right = _xnpv_log_rate(right, series, valuation_date, basis)
    left_sign = _sign(f_left, value_tolerance)
    right_sign = _sign(f_right, value_tolerance)
    if left_sign == 0:
        return left, 0
    if right_sign == 0:
        return right, 0
    if left_sign == right_sign:
        raise CalculationError("Root interval does not bracket a sign change.")

    for iteration in range(1, max_iterations + 1):
        midpoint = (left + right) / Decimal("2")
        f_mid = _xnpv_log_rate(midpoint, series, valuation_date, basis)
        mid_sign = _sign(f_mid, value_tolerance)
        if mid_sign == 0 or abs(right - left) <= interval_tolerance:
            return midpoint, iteration
        if mid_sign == left_sign:
            left = midpoint
            f_left = f_mid
            left_sign = mid_sign
        else:
            right = midpoint
            f_right = f_mid
            right_sign = mid_sign
    return (left + right) / Decimal("2"), max_iterations


def _cash_flow_sign_changes(series: CashFlowSeries) -> int:
    signs = [1 if flow.amount > ZERO else -1 for flow in series.flows if flow.amount != ZERO]
    return sum(1 for left, right in zip(signs, signs[1:]) if left != right)


def _xnpv_and_derivative_log_rate(
    log_rate: Decimal,
    series: CashFlowSeries,
    valuation_date: date,
    basis: DayCountBasis,
) -> tuple[Decimal, Decimal]:
    """Return NPV and first derivative with respect to ``log(1+r)``."""

    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        value = ZERO
        derivative = ZERO
        for flow in series.flows:
            years = year_fraction(valuation_date, flow.date, basis)
            factor = decimal_exp(-log_rate * years)
            discounted = flow.amount * factor
            value += discounted
            derivative -= years * discounted
        return +value, +derivative


def _solve_unique_xirr_fast(
    series: CashFlowSeries,
    valuation_date: date,
    basis: DayCountBasis,
    *,
    scan_log_min: Decimal,
    scan_log_max: Decimal,
    rate_tolerance: Decimal,
    max_iterations: int,
) -> tuple[Decimal, int] | None:
    """Fast path for conventional cash flows with exactly one sign change."""

    scale = max(abs(flow.amount) for flow in series.flows)
    value_tolerance = max(Decimal("1e-24"), scale * Decimal("1e-28"))
    log_rate = decimal_ln(Decimal("1.10"))

    # Newton in log-rate space preserves r > -100%. Damping avoids large jumps.
    for iteration in range(1, min(max_iterations, 80) + 1):
        value, derivative = _xnpv_and_derivative_log_rate(log_rate, series, valuation_date, basis)
        if abs(value) <= value_tolerance:
            return log_rate, iteration
        if derivative == ZERO:
            break
        step = value / derivative
        if abs(step) > Decimal("2"):
            step = Decimal("2") if step > ZERO else Decimal("-2")
        candidate = log_rate - step
        if candidate < scan_log_min or candidate > scan_log_max:
            break
        if abs(candidate - log_rate) <= rate_tolerance:
            return candidate, iteration
        log_rate = candidate

    # Guaranteed fallback when the broad endpoints bracket the unique root.
    left_value = _xnpv_log_rate(scan_log_min, series, valuation_date, basis)
    right_value = _xnpv_log_rate(scan_log_max, series, valuation_date, basis)
    if _sign(left_value, value_tolerance) * _sign(right_value, value_tolerance) <= 0:
        root, iterations = _bisect_log_root(
            series,
            valuation_date,
            basis,
            scan_log_min,
            scan_log_max,
            value_tolerance=value_tolerance,
            interval_tolerance=rate_tolerance,
            max_iterations=max_iterations,
        )
        return root, iterations
    return None




def _float_scan_brackets(
    series: CashFlowSeries,
    valuation_date: date,
    basis: DayCountBasis,
    scan_log_min: Decimal,
    scan_log_max: Decimal,
    scan_step: Decimal,
) -> tuple[list[tuple[Decimal, Decimal]], list[Decimal]]:
    """Locate candidate log-rate roots quickly using bounded float arithmetic.

    Decimal bisection still performs the final root refinement.  The float pass
    is used only to identify sign-changing intervals and avoids hundreds of
    expensive high-precision exponential evaluations for non-conventional cash
    flows.
    """

    points: list[tuple[float, float]] = []
    amounts_years = [
        (float(flow.amount), float(year_fraction(valuation_date, flow.date, basis)))
        for flow in series.flows
    ]
    scale = max(abs(amount) for amount, _ in amounts_years) or 1.0
    tolerance = max(1e-12, scale * 1e-12)

    start = float(scan_log_min)
    stop = float(scan_log_max)
    step = float(scan_step)
    count = max(1, int(round((stop - start) / step)))
    for index in range(count + 1):
        point = stop if index == count else start + index * step
        total = 0.0
        finite = True
        for amount, years in amounts_years:
            exponent = -point * years
            # exp() overflows beyond about 709 in IEEE-754; use signed infinity
            # solely for scan direction, then refine any bracket with Decimal.
            if exponent > 700:
                term = float("inf") if amount >= 0 else float("-inf")
            elif exponent < -745:
                term = 0.0
            else:
                term = amount * float_exp(exponent)
            total += term
            if not isfinite(total):
                finite = False
                break
        if not finite:
            total = float("inf") if total > 0 else float("-inf")
        if abs(total) <= tolerance:
            total = 0.0
        points.append((point, total))

    brackets: list[tuple[Decimal, Decimal]] = []
    exact: list[Decimal] = []
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if left_y == 0.0:
            exact.append(Decimal(str(left_x)))
        if right_y == 0.0:
            exact.append(Decimal(str(right_x)))
        if left_y == 0.0 or right_y == 0.0:
            continue
        if (left_y < 0 < right_y) or (right_y < 0 < left_y):
            brackets.append((Decimal(str(left_x)), Decimal(str(right_x))))
    return brackets, exact


def xirr(
    series: CashFlowSeries,
    *,
    basis: DayCountBasis = DayCountBasis.ACT_365F,
    scan_log_min: Decimal = Decimal("-20"),
    scan_log_max: Decimal = Decimal("20"),
    scan_step: Decimal = Decimal("0.05"),
    rate_tolerance: Decimal = Decimal("1e-18"),
    max_iterations: int = 300,
    stop_after_ambiguity: bool = False,
) -> XirrResult:
    """Return every sign-changing XIRR root in the configured search domain.

    If more than one root exists, the result is ``AMBIGUOUS`` and no single
    rate is selected. This is intentionally safer than silently returning the
    root nearest an arbitrary guess.
    """

    if not series.flows:
        return XirrResult(XirrStatus.NOT_CALCULABLE, None, (), 0, "Cash-flow series is empty.", basis)

    amounts = [flow.amount for flow in series.flows]
    if not any(amount < ZERO for amount in amounts) or not any(amount > ZERO for amount in amounts):
        return XirrResult(
            XirrStatus.NOT_CALCULABLE,
            None,
            (),
            0,
            "XIRR requires at least one negative and one positive cash flow.",
            basis,
        )

    valuation_date = series.flows[0].date
    scale = max(abs(amount) for amount in amounts)
    value_tolerance = max(Decimal("1e-24"), scale * Decimal("1e-28"))
    interval_tolerance = rate_tolerance

    if _cash_flow_sign_changes(series) == 1:
        fast = _solve_unique_xirr_fast(
            series,
            valuation_date,
            basis,
            scan_log_min=scan_log_min,
            scan_log_max=scan_log_max,
            rate_tolerance=rate_tolerance,
            max_iterations=max_iterations,
        )
        if fast is not None:
            root_log, iterations = fast
            root = decimal_exp(root_log) - ONE
            return XirrResult(
                XirrStatus.VALID,
                root,
                (root,),
                iterations,
                "A unique XIRR root was found using the conventional-cash-flow solver.",
                basis,
            )

    roots_log: list[Decimal] = []
    iterations_total = 0

    brackets, exact_candidates = _float_scan_brackets(
        series, valuation_date, basis, scan_log_min, scan_log_max, scan_step
    )
    for candidate in exact_candidates:
        candidate_value = _xnpv_log_rate(candidate, series, valuation_date, basis)
        if _sign(candidate_value, value_tolerance) == 0:
            roots_log.append(candidate)
    for left, right in brackets:
        try:
            root_log, iterations = _bisect_log_root(
                series,
                valuation_date,
                basis,
                left,
                right,
                value_tolerance=value_tolerance,
                interval_tolerance=interval_tolerance,
                max_iterations=max_iterations,
            )
        except CalculationError:
            # A float bracket can occasionally be a numerical false positive.
            # It is safe to ignore because accepted roots are always rechecked
            # with Decimal arithmetic.
            continue
        iterations_total += iterations
        roots_log.append(root_log)
        if stop_after_ambiguity and len(roots_log) >= 2:
            break

    roots: list[Decimal] = []
    for root_log in roots_log:
        rate = decimal_exp(root_log) - ONE
        if all(abs(rate - existing) > Decimal("1e-10") for existing in roots):
            roots.append(rate)
        if stop_after_ambiguity and len(roots) >= 2:
            break

    roots.sort()
    if not roots:
        return XirrResult(
            XirrStatus.NOT_CALCULABLE,
            None,
            (),
            iterations_total,
            "No sign-changing XIRR root was found in the configured search domain.",
            basis,
        )
    if len(roots) > 1:
        return XirrResult(
            XirrStatus.AMBIGUOUS,
            None,
            tuple(roots),
            iterations_total,
            "Multiple economically possible XIRR roots were detected. Review the cash-flow pattern.",
            basis,
        )
    return XirrResult(
        XirrStatus.VALID,
        roots[0],
        tuple(roots),
        iterations_total,
        "A unique sign-changing XIRR root was found.",
        basis,
    )


def nominal_profit(series: CashFlowSeries) -> Decimal:
    return series.total


def profit_on_cost(profit: Decimal, eligible_cost: Decimal) -> Decimal | None:
    profit = decimal(profit)
    eligible_cost = decimal(eligible_cost)
    if eligible_cost == ZERO:
        return None
    return profit / eligible_cost


def profit_on_revenue(profit: Decimal, recognized_revenue: Decimal) -> Decimal | None:
    profit = decimal(profit)
    recognized_revenue = decimal(recognized_revenue)
    if recognized_revenue == ZERO:
        return None
    return profit / recognized_revenue


def capital_multiple(series: CashFlowSeries) -> Decimal | None:
    contributions = sum((-flow.amount for flow in series.flows if flow.amount < ZERO), ZERO)
    distributions = sum((flow.amount for flow in series.flows if flow.amount > ZERO), ZERO)
    if contributions == ZERO:
        return None
    return distributions / contributions


def payback_date(series: CashFlowSeries, *, permanent_crossing: bool = True) -> date | None:
    points = series.cumulative_points()
    if not points:
        return None
    experienced_deficit = False
    for index, (point_date, cumulative) in enumerate(points):
        if cumulative < ZERO:
            experienced_deficit = True
            continue
        if not experienced_deficit:
            # A series that never requires capital has no meaningful payback period.
            continue
        if not permanent_crossing:
            return point_date
        if all(later_cumulative >= ZERO for _, later_cumulative in points[index:]):
            return point_date
    return None


def payback_years(series: CashFlowSeries, *, permanent_crossing: bool = True) -> Decimal | None:
    if not series.flows:
        return None
    paid_back = payback_date(series, permanent_crossing=permanent_crossing)
    if paid_back is None:
        return None
    return year_fraction(series.flows[0].date, paid_back, DayCountBasis.ACT_365F)


def peak_funding(series: CashFlowSeries) -> Decimal:
    minimum = ZERO
    running = ZERO
    for flow in series.flows:
        running += flow.amount
        minimum = min(minimum, running)
    return abs(minimum)


def minimum_cash_balance(series: CashFlowSeries) -> Decimal:
    minimum = ZERO
    running = ZERO
    for flow in series.flows:
        running += flow.amount
        minimum = min(minimum, running)
    return minimum


def funding_gap(required_peak_funding: Decimal, committed_funding: Decimal) -> Decimal:
    return max(ZERO, decimal(required_peak_funding) - decimal(committed_funding))
