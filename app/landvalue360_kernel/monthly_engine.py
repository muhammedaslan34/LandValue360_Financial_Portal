"""LandValue360 Engine 2.1.1 unified deterministic monthly funding and execution kernel.

This module is the single source of truth for cash-controlled project execution
used by the main finance analysis and the landowner fair-share studio.  It
models monthly receipts, development-cost execution, contractual public-land
payments, senior debt, committed developer equity, interest, reserves,
distributions, arrears, and schedule extension.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from landvalue360_kernel.dates import DayCountBasis, year_fraction
from landvalue360_kernel.decimal_utils import ONE, ZERO

MONTHLY_ENGINE_VERSION = "2.1.1"
EPS = Decimal("0.00000001")
CASH_RECONCILIATION_TOLERANCE = Decimal("0.01")


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite numeric value is not permitted: {value!r}")
    return result


def B(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def rate(value: Any, default: str = "0", *, name: str = "rate", upper: Decimal = ONE) -> Decimal:
    result = D(value, default)
    if result < ZERO or result > upper:
        raise ValueError(f"{name} must be between 0 and {upper}.")
    return result


def _series_value(values: list[Decimal], index: int) -> Decimal:
    return values[index] if index < len(values) else ZERO


def _zero_small(value: Decimal) -> Decimal:
    """Remove arithmetic dust without concealing a material cash amount."""

    return ZERO if abs(value) <= EPS else value


def _future_total(values: list[Decimal], start: int) -> Decimal:
    return sum(values[start:], ZERO) if start < len(values) else ZERO


def _allocate_funding(
    need: Decimal,
    *,
    available_debt: Decimal,
    available_equity: Decimal,
    order: str,
) -> tuple[Decimal, Decimal, Decimal]:
    if need <= ZERO:
        return ZERO, ZERO, ZERO
    debt = ZERO
    equity = ZERO
    key = str(order or "DEBT_FIRST").upper()
    if key == "EQUITY_FIRST":
        equity = min(need, available_equity)
        debt = min(need - equity, available_debt)
    elif key == "PRO_RATA" and available_debt + available_equity > ZERO:
        capacity = available_debt + available_equity
        debt = min(need * available_debt / capacity, available_debt)
        equity = min(need - debt, available_equity)
        residual = need - debt - equity
        if residual > ZERO:
            extra_debt = min(residual, available_debt - debt)
            debt += extra_debt
            residual -= extra_debt
        if residual > ZERO:
            extra_equity = min(residual, available_equity - equity)
            equity += extra_equity
    else:
        debt = min(need, available_debt)
        equity = min(need - debt, available_equity)
    return debt, equity, max(need - debt - equity, ZERO)


def _distribution_timing(raw: dict[str, Any]) -> tuple[str, int, bool]:
    """Normalize governed distribution timing without a hidden annual default.

    ``frequency_months`` remains supported for migrated projects. New policy
    versions should use ``frequency_code`` so the intended cadence is explicit.
    The boolean return value marks project-end-only policies.
    """

    aliases = {
        "MONTHLY": (1, False),
        "QUARTERLY": (3, False),
        "SEMIANNUAL": (6, False),
        "SEMI_ANNUAL": (6, False),
        "ANNUAL": (12, False),
        "YEARLY": (12, False),
        "PROJECT_END": (1, True),
        "END_OF_PROJECT": (1, True),
        # A conditional policy is tested monthly after its first eligible
        # month. The same liquidity, debt, reserve and obligation controls
        # below decide whether a distribution is actually permitted.
        "CONDITIONAL": (1, False),
    }
    requested = raw.get("frequency_code", raw.get("frequency"))
    if requested not in (None, ""):
        code = str(requested).strip().upper().replace("-", "_")
        if code not in aliases:
            raise ValueError(
                "distribution frequency_code must be MONTHLY, QUARTERLY, "
                "SEMIANNUAL, ANNUAL, PROJECT_END, or CONDITIONAL."
            )
        months, project_end_only = aliases[code]
        return code, months, project_end_only
    if raw.get("frequency_months") in (None, ""):
        # Distribution is disabled unless explicitly enabled. When enabled,
        # timing must be explicit rather than silently assuming annual.
        if B(raw.get("enabled"), False):
            raise ValueError("Enabled distribution policy requires frequency_code or frequency_months.")
        return "DISABLED", 1, False
    months = int(raw["frequency_months"])
    if months <= 0:
        raise ValueError("distribution frequency_months must be positive.")
    return "CUSTOM_MONTHS", months, False


@dataclass(slots=True)
class KernelConfig:
    finance_enabled: bool
    spend_policy: str
    allow_negative_cash: bool
    defer_contractual_payments: bool
    annual_interest_rate: Decimal
    upfront_fee_rate: Decimal
    commitment_fee_rate: Decimal
    cash_sweep_share: Decimal
    minimum_cash_balance: Decimal
    funding_draw_order: str
    hybrid_minimum_execution_share: Decimal
    future_cost_reserve_share: Decimal
    maximum_monthly_execution_share: Decimal
    maximum_monthly_execution_amount: Decimal
    maximum_extension_months: int

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "KernelConfig":
        raw = raw or {}
        enabled = B(raw.get("enabled"), True)
        policy = str(raw.get("spend_policy") or "SCHEDULE_DRIVEN").upper()
        if policy not in {"SCHEDULE_DRIVEN", "CASH_DRIVEN", "HYBRID"}:
            raise ValueError("spend_policy must be SCHEDULE_DRIVEN, CASH_DRIVEN, or HYBRID.")
        allow_negative = B(raw.get("allow_negative_cash"), policy == "SCHEDULE_DRIVEN")
        if not enabled:
            policy = "CASH_DRIVEN"
            allow_negative = False
        elif not allow_negative and policy == "SCHEDULE_DRIVEN":
            policy = "CASH_DRIVEN"
        maximum_extension = int(raw.get("maximum_extension_months") or 120)
        if maximum_extension < 0 or maximum_extension > 600:
            raise ValueError("maximum_extension_months must be between 0 and 600.")
        max_share = rate(
            raw.get("maximum_monthly_execution_share"),
            "0.15",
            name="maximum_monthly_execution_share",
        )
        max_amount = D(raw.get("maximum_monthly_execution_amount"), "0")
        if max_amount < ZERO:
            raise ValueError("maximum_monthly_execution_amount cannot be negative.")
        funding_order = str(raw.get("funding_draw_order") or "DEBT_FIRST").upper()
        if funding_order not in {"DEBT_FIRST", "EQUITY_FIRST", "PRO_RATA"}:
            raise ValueError("funding_draw_order must be DEBT_FIRST, EQUITY_FIRST, or PRO_RATA.")
        return cls(
            finance_enabled=enabled,
            spend_policy=policy,
            allow_negative_cash=allow_negative,
            defer_contractual_payments=B(raw.get("defer_contractual_payments"), False),
            annual_interest_rate=rate(raw.get("annual_interest_rate"), "0.08", name="annual_interest_rate", upper=Decimal("5")),
            upfront_fee_rate=rate(raw.get("upfront_fee_rate"), "0.01", name="upfront_fee_rate"),
            commitment_fee_rate=rate(raw.get("commitment_fee_rate"), "0", name="commitment_fee_rate"),
            cash_sweep_share=rate(raw.get("cash_sweep_share"), "1", name="cash_sweep_share"),
            minimum_cash_balance=D(raw.get("minimum_cash_balance"), "0"),
            funding_draw_order=funding_order,
            hybrid_minimum_execution_share=rate(
                raw.get("hybrid_minimum_execution_share"), "0.35", name="hybrid_minimum_execution_share"
            ),
            future_cost_reserve_share=rate(
                raw.get("future_cost_reserve_share"), "0", name="future_cost_reserve_share"
            ),
            maximum_monthly_execution_share=max_share,
            maximum_monthly_execution_amount=max_amount,
            maximum_extension_months=maximum_extension,
        )


def run_monthly_kernel(
    *,
    dates: list[date],
    receipts: list[Decimal],
    cost_items: list[dict[str, Any]],
    contractual_payments: list[Decimal],
    committed_equity: Decimal,
    committed_debt: Decimal,
    finance_model: dict[str, Any] | None,
    distribution_policy: dict[str, Any] | None = None,
    original_completion_index: int | None = None,
    initial_cash: Decimal = ZERO,
) -> dict[str, Any]:
    """Execute one governed monthly project cash flow.

    ``dates`` must already include the maximum permitted extension period.
    Planned receipts/costs/contractual payments may be shorter; missing months
    are treated as zero.  A PASS-compatible completion requires no deferred
    cost, contractual arrears, unsupported funding gap, or terminal debt.
    """

    if not dates:
        raise ValueError("At least one monthly date is required.")
    if len(set(dates)) != len(dates) or any(later <= earlier for earlier, later in zip(dates, dates[1:])):
        raise ValueError("Monthly dates must be unique and strictly increasing.")
    if any(D(value) < ZERO for value in receipts):
        raise ValueError("Monthly receipts cannot be negative.")
    if any(D(value) < ZERO for value in contractual_payments):
        raise ValueError("Contractual payments cannot be negative.")
    cost_ids = [str(item.get("cost_id") or "").strip() for item in cost_items]
    if any(not value for value in cost_ids) or len(cost_ids) != len(set(cost_ids)):
        raise ValueError("Every cost item requires a unique non-empty cost_id.")
    if any(D(value) < ZERO for item in cost_items for value in item.get("schedule") or []):
        raise ValueError("Cost schedules cannot contain negative amounts.")
    config = KernelConfig.from_dict(finance_model)
    if config.minimum_cash_balance < ZERO:
        raise ValueError("minimum_cash_balance cannot be negative.")
    if committed_equity < ZERO or committed_debt < ZERO:
        raise ValueError("Funding commitments cannot be negative.")
    if initial_cash < ZERO:
        raise ValueError("initial_cash cannot be negative.")
    if not config.finance_enabled:
        committed_debt = ZERO

    planned_end = original_completion_index
    if planned_end is None:
        planned_end = max(
            len(receipts),
            len(contractual_payments),
            max((len(item.get("schedule") or []) for item in cost_items), default=0),
        ) - 1
    planned_end = max(0, min(planned_end, len(dates) - 1))

    total_scheduled_cost = sum(
        (sum((D(v) for v in item.get("schedule") or []), ZERO) for item in cost_items), ZERO
    )
    monthly_share_capacity = total_scheduled_cost * config.maximum_monthly_execution_share
    if monthly_share_capacity <= ZERO:
        monthly_share_capacity = total_scheduled_cost

    backlogs = {str(item.get("cost_id")): ZERO for item in cost_items}
    paid_by_item = {str(item.get("cost_id")): [ZERO for _ in dates] for item in cost_items}
    contractual_arrears = ZERO
    finance_arrears = ZERO
    cash = initial_cash
    debt = ZERO
    cumulative_equity = initial_cash
    unsupported_gap = ZERO
    mandatory_shortfall = ZERO
    total_executed_cost = ZERO
    total_contractual_paid = ZERO
    total_interest = ZERO
    total_fees = ZERO
    total_finance_paid = ZERO
    total_hybrid_minimum_shortfall = ZERO
    peak_debt = ZERO
    peak_equity = initial_cash
    peak_negative_cash = ZERO
    maximum_cash_balance_variance = ZERO
    rows: list[dict[str, Any]] = []
    diagnostic_ledger: list[dict[str, Any]] = []
    distribution_ledger: list[dict[str, Any]] = []
    equity_cashflows: list[Decimal] = [ZERO for _ in dates]
    equity_contribution_series: list[Decimal] = [ZERO for _ in dates]
    if initial_cash > ZERO:
        # Opening cash is a base-date developer equity contribution by default.
        # It is captured in the equity-return series, while the monthly funding
        # source series contains only cash newly drawn during that month.
        equity_cashflows[0] = -initial_cash
    debt_draw_series: list[Decimal] = [ZERO for _ in dates]
    debt_repayment_series: list[Decimal] = [ZERO for _ in dates]
    interest_series: list[Decimal] = [ZERO for _ in dates]
    executed_cost_series: list[Decimal] = [ZERO for _ in dates]
    contractual_paid_series: list[Decimal] = [ZERO for _ in dates]
    developer_distribution_series: list[Decimal] = [ZERO for _ in dates]
    developer_recovery_series: list[Decimal] = [ZERO for _ in dates]
    developer_recoverable_accrual_series: list[Decimal] = [ZERO for _ in dates]
    landowner_distribution_series: list[Decimal] = [ZERO for _ in dates]
    landowner_cash_receipt_series: list[Decimal] = [ZERO for _ in dates]
    contractual_accrual_series: list[Decimal] = [ZERO for _ in dates]

    dist = distribution_policy or {}
    distribution_enabled = B(dist.get("enabled"), False)
    distribution_frequency_code, distribution_frequency, project_end_only_distribution = _distribution_timing(dist)
    first_distribution_month = max(1, int(dist.get("first_distribution_month") or distribution_frequency))
    reserve_months = int(dist.get("reserve_months") or 12)
    if reserve_months < 0:
        raise ValueError("distribution reserve_months cannot be negative.")
    future_cost_reserve_share = rate(
        dist.get("future_cost_reserve_share", dist.get("remaining_cost_reserve_share")),
        "0.25",
        name="future_cost_reserve_share",
    )
    reserve_basis = str(dist.get("reserve_basis") or "ALL_REMAINING_COSTS").upper()
    if reserve_basis not in {"ALL_REMAINING_COSTS", "NEXT_N_MONTHS", "HIGHER_OF_BOTH"}:
        raise ValueError("Unsupported distribution reserve_basis.")
    distribution_share = rate(dist.get("distribution_share"), "1", name="distribution_share")
    landowner_share = rate(dist.get("landowner_share"), "0", name="landowner_share")
    allocation_method = str(dist.get("allocation_method") or "CONTRACTUAL_ACCRUAL_FIRST").upper()
    if allocation_method not in {"CONTRACTUAL_ACCRUAL_FIRST", "CONTRACT_RATE_SPLIT"}:
        raise ValueError("Unsupported distribution allocation_method.")
    contractual_payment_timing = str(dist.get("contractual_payment_timing") or "AS_ACCRUED").upper()
    if contractual_payment_timing not in {"AS_ACCRUED", "DISTRIBUTION_DATES"}:
        raise ValueError("Unsupported contractual_payment_timing.")
    recover_advances = B(dist.get("recover_developer_advances_before_landowner_cash"), True)
    settle_prior_obligations = B(dist.get("settle_prior_obligations_before_distribution"), True)
    minimum_distribution_amount = max(ZERO, D(dist.get("minimum_distribution_amount"), "0"))
    policy_minimum_cash = max(config.minimum_cash_balance, D(dist.get("minimum_operating_cash"), "0"))
    prohibit_with_debt = B(dist.get("prohibit_while_debt_outstanding"), True)
    prohibit_before_completion = B(dist.get("prohibit_before_completion"), False)
    return_capital_first = B(dist.get("return_capital_first"), True)
    preferred_return_rate = rate(
        dist.get("preferred_return_rate"), "0", name="preferred_return_rate", upper=Decimal("5")
    )
    preferred_accrual = ZERO
    unrecovered_equity = initial_cash
    developer_recoverable_balance = ZERO
    total_developer_recoverable_accrued = ZERO
    total_developer_recovered = ZERO

    ordered_items = sorted(
        cost_items,
        key=lambda item: (int(item.get("priority") or 70), str(item.get("cost_id") or "")),
    )
    prepared_items: list[dict[str, Any]] = []
    scheduled_cost_by_month = [ZERO for _ in dates]
    for source_item in ordered_items:
        item = dict(source_item)
        raw_schedule = [D(value) for value in source_item.get("schedule") or []]
        schedule = raw_schedule[: len(dates)] + [ZERO for _ in range(max(0, len(dates) - len(raw_schedule)))]
        schedule = schedule[: len(dates)]
        suffix = [ZERO for _ in range(len(dates) + 1)]
        running = ZERO
        for index in range(len(dates) - 1, -1, -1):
            running += schedule[index]
            suffix[index] = running
            scheduled_cost_by_month[index] += schedule[index]
        raw_recoverable = [D(value) for value in source_item.get("developer_recoverable_schedule") or []]
        recoverable_schedule = raw_recoverable[: len(dates)] + [ZERO for _ in range(max(0, len(dates) - len(raw_recoverable)))]
        recoverable_schedule = recoverable_schedule[: len(dates)]
        scheduled_total = sum(schedule, ZERO)
        recoverable_total = min(sum(recoverable_schedule, ZERO), scheduled_total)
        item["_schedule"] = schedule
        item["_suffix"] = suffix
        item["_recoverable_schedule"] = recoverable_schedule
        item["_recoverable_fraction"] = (recoverable_total / scheduled_total) if scheduled_total > ZERO else ZERO
        prepared_items.append(item)
    ordered_items = prepared_items
    receipt_suffix = [ZERO for _ in range(len(dates) + 1)]
    contractual_suffix = [ZERO for _ in range(len(dates) + 1)]
    running_receipts = ZERO
    running_contractual = ZERO
    for index in range(len(dates) - 1, -1, -1):
        running_receipts += _series_value(receipts, index)
        running_contractual += _series_value(contractual_payments, index)
        receipt_suffix[index] = running_receipts
        contractual_suffix[index] = running_contractual
    previous_date = dates[0]
    upfront_fee = committed_debt * config.upfront_fee_rate if config.finance_enabled else ZERO
    completion_index = len(dates) - 1

    for month, current_date in enumerate(dates):
        elapsed = ZERO if month == 0 else year_fraction(previous_date, current_date, DayCountBasis.ACT_365F)
        opening_cash = cash
        opening_debt = debt
        opening_equity = cumulative_equity
        opening_contractual_arrears = contractual_arrears
        opening_finance_arrears = finance_arrears
        opening_backlog = sum(backlogs.values(), ZERO)
        current_receipts = _series_value(receipts, month)
        if current_receipts < ZERO:
            raise ValueError("Receipts cannot be negative.")
        cash += current_receipts

        interest = debt * config.annual_interest_rate * elapsed if config.finance_enabled else ZERO
        commitment_fee = (
            max(committed_debt - debt, ZERO) * config.commitment_fee_rate * elapsed
            if config.finance_enabled
            else ZERO
        )
        current_upfront_fee = upfront_fee if month == 0 else ZERO
        total_interest += interest
        total_fees += commitment_fee + current_upfront_fee
        interest_series[month] = interest

        scheduled_contractual = _series_value(contractual_payments, month)
        if scheduled_contractual < ZERO:
            raise ValueError("Contractual payments cannot be negative.")
        contractual_accrual_series[month] = scheduled_contractual
        contractual_due = contractual_arrears + scheduled_contractual
        pay_contract_as_accrued = contractual_payment_timing == "AS_ACCRUED"
        scheduled_cost_total = scheduled_cost_by_month[month]
        due_cost_total = opening_backlog + scheduled_cost_total

        # Accrue preferred return on unrecovered equity using actual dates.
        if month > 0 and preferred_return_rate > ZERO and unrecovered_equity > ZERO:
            preferred_accrual += unrecovered_equity * preferred_return_rate * elapsed

        # Only the minimum operating cash balance restricts due construction
        # execution and contractual payments.  The future-cost reserve is a
        # distribution safeguard, not a reason to postpone work that is already
        # scheduled and funded.  Applying the full future-cost reserve here used
        # to delay month-1 construction until enough sales cash had accumulated.
        reserve_requirement = config.minimum_cash_balance
        if month >= planned_end and due_cost_total == ZERO and contractual_due == ZERO:
            reserve_requirement = ZERO

        current_finance_accrual = interest + commitment_fee + current_upfront_fee
        finance_due = opening_finance_arrears + current_finance_accrual
        immediate_need = max(
            finance_due
            + (ZERO if config.defer_contractual_payments or not pay_contract_as_accrued else contractual_due)
            - max(cash - reserve_requirement, ZERO),
            ZERO,
        )
        available_debt = max(committed_debt - debt, ZERO)
        available_equity = max(committed_equity - cumulative_equity, ZERO)
        draw, equity, residual = _allocate_funding(
            immediate_need,
            available_debt=available_debt,
            available_equity=available_equity,
            order=config.funding_draw_order,
        )
        debt += draw
        cumulative_equity += equity
        cash += draw + equity
        debt_draw_series[month] += draw
        equity_cashflows[month] -= equity
        equity_contribution_series[month] += equity
        unrecovered_equity += equity
        peak_equity = max(peak_equity, cumulative_equity)

        # Finance charges are senior cash obligations. Schedule-driven mode may
        # expose a negative cash balance; cash-controlled modes pay only the
        # funded amount and carry the unpaid balance as finance arrears. This
        # preserves a true sources-and-uses reconciliation instead of silently
        # zeroing a negative balance.
        if config.spend_policy == "SCHEDULE_DRIVEN" and config.allow_negative_cash:
            finance_paid = finance_due
        else:
            finance_paid = min(finance_due, max(cash, ZERO))
        cash -= finance_paid
        finance_arrears = finance_due - finance_paid
        total_finance_paid += finance_paid

        # Public-land consideration accrues according to the contract.  The
        # selected distribution policy controls whether it is paid monthly or
        # accumulated and settled on governed distribution dates.
        contractual_paid = ZERO
        accrued_payment_recovery = ZERO
        accrued_landowner_cash = ZERO
        if pay_contract_as_accrued:
            if config.spend_policy == "SCHEDULE_DRIVEN" and config.allow_negative_cash:
                contractual_paid = contractual_due
            else:
                if not config.defer_contractual_payments and cash < contractual_due + reserve_requirement:
                    need = contractual_due + reserve_requirement - cash
                    draw3, equity3, residual3 = _allocate_funding(
                        need,
                        available_debt=max(committed_debt - debt, ZERO),
                        available_equity=max(committed_equity - cumulative_equity, ZERO),
                        order=config.funding_draw_order,
                    )
                    debt += draw3
                    cumulative_equity += equity3
                    cash += draw3 + equity3
                    debt_draw_series[month] += draw3
                    equity_cashflows[month] -= equity3
                    equity_contribution_series[month] += equity3
                    unrecovered_equity += equity3
                    residual += residual3
                    peak_equity = max(peak_equity, cumulative_equity)
                contractual_paid = min(contractual_due, max(cash - reserve_requirement, ZERO))
            cash -= contractual_paid
        contractual_arrears = contractual_due - contractual_paid
        contractual_paid_series[month] = contractual_paid
        total_contractual_paid += contractual_paid
        if contractual_paid > ZERO:
            # A cash payment of contractual consideration must be visible to
            # the landowner cash series even when it is paid as accrued rather
            # than on a periodic distribution date.  Prior developer advances
            # are recovered from that same payment first when policy requires;
            # the gross contractual use remains recorded exactly once.
            accrued_payment_recovery = (
                min(contractual_paid, developer_recoverable_balance)
                if recover_advances else ZERO
            )
            accrued_landowner_cash = contractual_paid - accrued_payment_recovery
            developer_recoverable_balance -= accrued_payment_recovery
            total_developer_recovered += accrued_payment_recovery
            developer_recovery_series[month] += accrued_payment_recovery
            landowner_cash_receipt_series[month] += accrued_landowner_cash
            equity_cashflows[month] += accrued_payment_recovery
        # Determine executable cost capacity.
        max_monthly = monthly_share_capacity
        if config.maximum_monthly_execution_amount > ZERO:
            max_monthly = min(max_monthly, config.maximum_monthly_execution_amount)
        if max_monthly <= ZERO:
            max_monthly = due_cost_total

        available_for_cost = max(cash - reserve_requirement, ZERO)
        if config.spend_policy == "SCHEDULE_DRIVEN" and config.allow_negative_cash:
            # Schedule-driven execution still consumes recognized committed
            # debt and equity before reporting a residual negative-cash gap.
            # Otherwise an available facility would be ignored and the solver
            # could not close a funding gap by increasing real commitments.
            need_for_cost = max(due_cost_total - available_for_cost, ZERO)
            draw4, equity4, residual4 = _allocate_funding(
                need_for_cost,
                available_debt=max(committed_debt - debt, ZERO),
                available_equity=max(committed_equity - cumulative_equity, ZERO),
                order=config.funding_draw_order,
            )
            debt += draw4
            cumulative_equity += equity4
            cash += draw4 + equity4
            debt_draw_series[month] += draw4
            equity_cashflows[month] -= equity4
            equity_contribution_series[month] += equity4
            unrecovered_equity += equity4
            residual += residual4
            peak_equity = max(peak_equity, cumulative_equity)
            executable_limit = due_cost_total
        else:
            # Funding for cost is drawn only up to committed resources. This
            # prevents a hidden gap while allowing cash/equity/debt funded work.
            need_for_cost = max(min(due_cost_total, max_monthly) - available_for_cost, ZERO)
            draw4, equity4, residual4 = _allocate_funding(
                need_for_cost,
                available_debt=max(committed_debt - debt, ZERO),
                available_equity=max(committed_equity - cumulative_equity, ZERO),
                order=config.funding_draw_order,
            )
            debt += draw4
            cumulative_equity += equity4
            cash += draw4 + equity4
            debt_draw_series[month] += draw4
            equity_cashflows[month] -= equity4
            equity_contribution_series[month] += equity4
            unrecovered_equity += equity4
            # Any unfunded deferrable cost remains in backlog; it is not an
            # unsupported financing gap unless still unpaid at the terminal date.
            peak_equity = max(peak_equity, cumulative_equity)
            available_for_cost = max(cash - reserve_requirement, ZERO)
            executable_limit = min(due_cost_total, available_for_cost, max_monthly)

        remaining_capacity = executable_limit
        executed_cost = ZERO
        developer_recoverable_accrual = ZERO
        minimum_execution_shortfall = ZERO
        for item in ordered_items:
            item_id = str(item.get("cost_id"))
            scheduled = item["_schedule"][month]
            due = backlogs[item_id] + scheduled
            if due <= ZERO:
                continue
            if config.spend_policy == "SCHEDULE_DRIVEN" and config.allow_negative_cash:
                payable = due
            else:
                payable = min(due, remaining_capacity)
                # Non-deferrable unpaid scope is reported once at terminal,
                # avoiding repeated counting of the same arrear every month.
                if config.spend_policy == "HYBRID":
                    minimum_current = scheduled * config.hybrid_minimum_execution_share
                    if payable + EPS < minimum_current:
                        minimum_execution_shortfall += minimum_current - payable
            backlogs[item_id] = due - payable
            paid_by_item[item_id][month] = payable
            executed_cost += payable
            developer_recoverable_accrual += payable * D(item.get("_recoverable_fraction"), "0")
            remaining_capacity -= payable
        cash -= executed_cost
        developer_recoverable_accrual_series[month] = developer_recoverable_accrual
        developer_recoverable_balance += developer_recoverable_accrual
        total_developer_recoverable_accrued += developer_recoverable_accrual
        executed_cost_series[month] = executed_cost
        total_executed_cost += executed_cost
        if minimum_execution_shortfall > ZERO:
            total_hybrid_minimum_shortfall += minimum_execution_shortfall
        # ``residual`` is an allocation diagnostic only. It may describe the
        # same unpaid obligation at more than one funding stage in a month, so
        # it must never be accumulated into the authoritative funding gap.
        allocation_residual = max(residual, ZERO)
        monthly_uncovered_gap = ZERO
        if cash < ZERO:
            peak_negative_cash = max(peak_negative_cash, -cash)
            monthly_uncovered_gap = -cash
            if config.spend_policy == "SCHEDULE_DRIVEN" and config.allow_negative_cash:
                unsupported_gap = max(unsupported_gap, monthly_uncovered_gap)
            else:
                unsupported_gap = max(unsupported_gap, monthly_uncovered_gap)
                cash = ZERO

        # Debt cash sweep precedes discretionary distributions.
        repayment = ZERO
        if debt > ZERO and cash > config.minimum_cash_balance:
            repayment = min(debt, max(cash - config.minimum_cash_balance, ZERO) * config.cash_sweep_share)
            cash -= repayment
            debt -= repayment
            debt_repayment_series[month] = repayment

        developer_distribution = ZERO
        developer_recovery = accrued_payment_recovery
        developer_recovery_cash_use = ZERO
        landowner_distribution = ZERO
        landowner_contract_cash = accrued_landowner_cash
        required_distribution_reserve = ZERO
        distributable = ZERO
        available_distribution = ZERO
        capital_return = ZERO
        preferred_paid = ZERO
        distribution_block_reason = None

        ending_backlog = sum(backlogs.values(), ZERO)
        future_receipts = receipt_suffix[month + 1]
        future_costs = sum((item["_suffix"][month + 1] for item in ordered_items), ZERO)
        future_contractual = contractual_suffix[month + 1]
        terminal_candidate = (
            month >= planned_end
            and future_receipts == ZERO
            and future_costs == ZERO
            and future_contractual == ZERO
            and ending_backlog <= EPS
        )
        regular_distribution_date = (
            not project_end_only_distribution
            and
            month + 1 >= first_distribution_month
            and ((month + 1 - first_distribution_month) % distribution_frequency == 0)
        )
        distribution_due = distribution_enabled and (regular_distribution_date or terminal_candidate)

        if distribution_due:
            next_end = min(len(dates), month + 1 + reserve_months)
            next_period_cost = ending_backlog + sum(
                (sum(item["_schedule"][month + 1:next_end], ZERO) for item in ordered_items), ZERO
            )
            all_remaining_cost = ending_backlog + future_costs
            if reserve_basis == "NEXT_N_MONTHS":
                eligible_future_cost = next_period_cost
            elif reserve_basis == "HIGHER_OF_BOTH":
                eligible_future_cost = max(next_period_cost, all_remaining_cost)
            else:
                eligible_future_cost = all_remaining_cost
            future_cost_reserve = eligible_future_cost * future_cost_reserve_share
            required_distribution_reserve = policy_minimum_cash + future_cost_reserve
            available_distribution = max(cash - required_distribution_reserve, ZERO)
            if prohibit_with_debt and debt > EPS:
                available_distribution = ZERO
                distribution_block_reason = "DEBT_OUTSTANDING"
            if settle_prior_obligations and (finance_arrears > EPS or ending_backlog > EPS):
                available_distribution = ZERO
                distribution_block_reason = "PRIOR_OBLIGATIONS_OUTSTANDING"
            if prohibit_before_completion and (all_remaining_cost > EPS or future_contractual > EPS):
                available_distribution = ZERO
                distribution_block_reason = "PROJECT_NOT_COMPLETE"
            distributable = available_distribution * distribution_share
            if distributable < minimum_distribution_amount:
                if distributable > ZERO:
                    distribution_block_reason = "BELOW_MINIMUM_DISTRIBUTION"
                distributable = ZERO

            # When consideration is governed by distribution dates, accrued
            # landowner entitlement is settled from the distributable pool.
            # A contract-rate split is optional; the default settles contractual
            # arrears first and releases only the residual to developer equity.
            if distributable > ZERO and contractual_payment_timing == "DISTRIBUTION_DATES":
                if allocation_method == "CONTRACT_RATE_SPLIT" and not terminal_candidate:
                    contractual_budget = distributable * landowner_share
                else:
                    # On the terminal distribution date all accrued landowner
                    # entitlement must be caught up before residual developer
                    # cash is released.  Periodic contract-rate splitting must
                    # never strand contractual arrears at project close.
                    contractual_budget = distributable
                contractual_settlement = min(contractual_arrears, contractual_budget)
                if contractual_settlement > ZERO:
                    developer_recovery = (
                        min(contractual_settlement, developer_recoverable_balance)
                        if recover_advances else ZERO
                    )
                    landowner_contract_cash = contractual_settlement - developer_recovery
                    cash -= contractual_settlement
                    distributable -= contractual_settlement
                    contractual_arrears -= contractual_settlement
                    contractual_paid += contractual_settlement
                    contractual_paid_series[month] += contractual_settlement
                    total_contractual_paid += contractual_settlement
                    developer_recoverable_balance -= developer_recovery
                    total_developer_recovered += developer_recovery
                    developer_recovery_series[month] += developer_recovery
                    landowner_cash_receipt_series[month] += landowner_contract_cash
                    equity_cashflows[month] += developer_recovery

            if distributable > ZERO:
                capital_return = min(distributable, unrecovered_equity) if return_capital_first else ZERO
                unrecovered_equity -= capital_return
                remaining = distributable - capital_return
                preferred_paid = min(remaining, preferred_accrual)
                preferred_accrual -= preferred_paid
                remaining -= preferred_paid
                # Sales-share and fixed-consideration contracts already create
                # a contractual landowner accrual.  Residual project cash must
                # therefore default to the developer; otherwise the landowner
                # share is counted twice.  JV/profit-sharing policies may opt in
                # to a separate residual share explicitly.
                residual_landowner_share = (
                    rate(dist.get("residual_landowner_share"), "0", name="residual_landowner_share")
                    if allocation_method == "CONTRACTUAL_ACCRUAL_FIRST"
                    else ZERO
                )
                landowner_distribution = remaining * residual_landowner_share
                developer_distribution = capital_return + preferred_paid + remaining - landowner_distribution
                if recover_advances and landowner_distribution > ZERO and developer_recoverable_balance > ZERO:
                    recovery_from_distribution = min(landowner_distribution, developer_recoverable_balance)
                    landowner_distribution -= recovery_from_distribution
                    developer_recovery += recovery_from_distribution
                    developer_recovery_cash_use += recovery_from_distribution
                    developer_recoverable_balance -= recovery_from_distribution
                    total_developer_recovered += recovery_from_distribution
                    developer_recovery_series[month] += recovery_from_distribution
                    equity_cashflows[month] += recovery_from_distribution
                cash -= distributable
                developer_distribution_series[month] = developer_distribution
                landowner_distribution_series[month] = landowner_distribution
                landowner_cash_receipt_series[month] += landowner_distribution
                equity_cashflows[month] += developer_distribution

            if regular_distribution_date or terminal_candidate:
                distribution_ledger.append(
                    {
                        "month": month + 1,
                        "date": current_date.isoformat(),
                        "distribution_due": True,
                        "terminal_candidate": terminal_candidate,
                        "reserve_basis": reserve_basis,
                        "eligible_future_cost": eligible_future_cost,
                        "future_cost_reserve_share": future_cost_reserve_share,
                        "required_reserve": required_distribution_reserve,
                        "available_before_policy_share": available_distribution,
                        "distributable_cash": contractual_paid_series[month] + developer_distribution + landowner_distribution + developer_recovery_cash_use,
                        "contractual_accrual": scheduled_contractual,
                        "contractual_settlement": contractual_paid_series[month],
                        "landowner_contract_cash": landowner_contract_cash,
                        "developer_advance_recovery": developer_recovery,
                        "developer_advance_recovery_from_residual_distribution": developer_recovery_cash_use,
                        "return_of_capital": capital_return,
                        "preferred_return_paid": preferred_paid,
                        "developer_distribution": developer_distribution,
                        "landowner_distribution": landowner_distribution,
                        "landowner_cash_receipt": landowner_contract_cash + landowner_distribution,
                        "developer_recoverable_balance_after": developer_recoverable_balance,
                        "unrecovered_equity_after": unrecovered_equity,
                        "preferred_return_accrual_after": preferred_accrual,
                        "blocked_reason": distribution_block_reason,
                    }
                )

        peak_debt = max(peak_debt, debt)
        # Initial cash is an opening balance and therefore must not be counted
        # again as a same-month source.  Only funding drawn during this row is
        # included in the monthly sources-and-uses reconciliation.
        equity_contribution = equity_contribution_series[month]
        cash_sources = opening_cash + current_receipts + debt_draw_series[month] + equity_contribution
        cash_uses_before_ending_cash = (
            finance_paid
            + contractual_paid
            + executed_cost
            + repayment
            + developer_distribution
            + landowner_distribution
            + developer_recovery_cash_use
        )
        cash_balance_variance = cash_sources - cash_uses_before_ending_cash - cash
        maximum_cash_balance_variance = max(maximum_cash_balance_variance, abs(cash_balance_variance))
        current_nondeferrable_backlog = sum(
            (backlogs[str(item.get("cost_id"))] for item in ordered_items if not B(item.get("deferrable"), True)),
            ZERO,
        )
        overdue_contractual = (
            contractual_arrears
            if pay_contract_as_accrued and not config.defer_contractual_payments
            else ZERO
        )
        current_mandatory_shortfall = finance_arrears + overdue_contractual + current_nondeferrable_backlog
        diagnostic_reasons: list[str] = []
        if monthly_uncovered_gap > EPS:
            diagnostic_reasons.append("NEGATIVE_CASH")
        if finance_arrears > EPS:
            diagnostic_reasons.append("FINANCE_ARREARS")
        if overdue_contractual > EPS:
            diagnostic_reasons.append("CONTRACTUAL_ARREARS")
        if current_nondeferrable_backlog > EPS:
            diagnostic_reasons.append("NONDEFERRABLE_COST_BACKLOG")
        if ending_backlog > current_nondeferrable_backlog + EPS:
            diagnostic_reasons.append("DEFERRABLE_COST_BACKLOG")
        diagnostic_entry = {
            "month": month + 1,
            "date": current_date.isoformat(),
            "status": "FAIL" if diagnostic_reasons else "PASS",
            "reason_codes": diagnostic_reasons,
            "opening_cash": opening_cash,
            "cash_receipts": current_receipts,
            "debt_capacity_available_before_draw": available_debt,
            "equity_capacity_available_before_draw": available_equity,
            "debt_draw": debt_draw_series[month],
            "equity_contribution": equity_contribution,
            "development_expenses": executed_cost,
            "finance_expenses": finance_paid,
            "contractual_payments": contractual_paid,
            "principal_repayment": repayment,
            "required_reserves": required_distribution_reserve,
            "developer_advance_recovery": developer_recovery,
            "developer_distribution": developer_distribution,
            "landowner_distribution": landowner_distribution,
            "total_cash_outflows": cash_uses_before_ending_cash,
            "allocation_residual_diagnostic_only": allocation_residual,
            "monthly_uncovered_gap": monthly_uncovered_gap,
            "current_mandatory_shortfall": current_mandatory_shortfall,
            "deferred_cost_backlog": ending_backlog,
            "contractual_arrears": contractual_arrears,
            "finance_arrears": finance_arrears,
            "ending_cash": cash,
            "corrective_action": (
                "Increase documented equity/debt capacity, revise payment timing, or reduce/reschedule eligible uses."
                if diagnostic_reasons else None
            ),
            "target_page": "developer/funding" if diagnostic_reasons else None,
        }
        diagnostic_ledger.append(diagnostic_entry)
        rows.append(
            {
                "month": month + 1,
                "date": current_date.isoformat(),
                "opening_cash": opening_cash,
                "receipts": current_receipts,
                "scheduled_development_cost": scheduled_cost_total,
                "executed_development_cost": executed_cost,
                "deferred_development_cost": ending_backlog,
                "scheduled_contractual_payment": scheduled_contractual,
                "contractual_accrual": scheduled_contractual,
                "contractual_payment": contractual_paid,
                "contractual_arrears": contractual_arrears,
                "landowner_contract_cash": landowner_contract_cash,
                "landowner_cash_receipt": landowner_contract_cash + landowner_distribution,
                "developer_recoverable_accrual": developer_recoverable_accrual,
                "developer_advance_recovery": developer_recovery,
                "developer_recoverable_balance": developer_recoverable_balance,
                "interest_accrued": interest,
                "commitment_fee": commitment_fee,
                "upfront_fee": current_upfront_fee,
                "finance_cost_accrued": current_finance_accrual,
                "finance_cost_paid": finance_paid,
                "finance_arrears": finance_arrears,
                "debt_draw": debt_draw_series[month],
                "equity_contribution": equity_contribution,
                "principal_repayment": repayment,
                "developer_distribution": developer_distribution,
                "landowner_distribution": landowner_distribution,
                "required_distribution_reserve": required_distribution_reserve,
                "distribution_due": distribution_due,
                "distribution_block_reason": distribution_block_reason,
                "opening_debt": opening_debt,
                "closing_debt": debt,
                "opening_committed_equity_drawn": opening_equity,
                "closing_committed_equity_drawn": cumulative_equity,
                "monthly_uncovered_gap": monthly_uncovered_gap,
                "unsupported_funding_gap": unsupported_gap,
                "mandatory_shortfall": current_mandatory_shortfall,
                "funding_diagnostic": diagnostic_entry,
                "ending_cash": cash,
                "cash_sources_total": cash_sources,
                "cash_uses_before_ending_cash": cash_uses_before_ending_cash,
                "cash_balance_variance": cash_balance_variance,
            }
        )

        all_planned_elapsed = month >= planned_end and future_receipts == ZERO and future_costs == ZERO and future_contractual == ZERO
        complete = (
            ending_backlog <= EPS
            and contractual_arrears <= EPS
            and finance_arrears <= EPS
            and developer_recoverable_balance <= EPS
            and debt <= EPS
        )
        if all_planned_elapsed and complete:
            # Release terminal surplus to developer equity after every debt and
            # contractual obligation has been closed.
            terminal_distribution = max(cash, ZERO)
            if terminal_distribution > ZERO:
                cash = ZERO
                developer_distribution_series[month] += terminal_distribution
                equity_cashflows[month] += terminal_distribution
                rows[-1]["developer_distribution"] += terminal_distribution
                rows[-1]["ending_cash"] = ZERO
                rows[-1]["cash_uses_before_ending_cash"] += terminal_distribution
                rows[-1]["cash_balance_variance"] = (
                    rows[-1]["cash_sources_total"]
                    - rows[-1]["cash_uses_before_ending_cash"]
                    - rows[-1]["ending_cash"]
                )
                maximum_cash_balance_variance = max(
                    maximum_cash_balance_variance,
                    abs(rows[-1]["cash_balance_variance"]),
                )
                distribution_ledger.append(
                    {
                        "month": month + 1,
                        "date": current_date.isoformat(),
                        "required_reserve": ZERO,
                        "distributable_cash": terminal_distribution,
                        "return_of_capital": min(terminal_distribution, unrecovered_equity),
                        "preferred_return_paid": ZERO,
                        "developer_distribution": terminal_distribution,
                        "landowner_distribution": ZERO,
                        "unrecovered_equity_after": max(unrecovered_equity - terminal_distribution, ZERO),
                        "preferred_return_accrual_after": preferred_accrual,
                        "terminal": True,
                    }
                )
            completion_index = month
            break
        previous_date = current_date

    # Trim all series to actual modelled completion / extension limit.
    used = completion_index + 1
    rows = rows[:used]
    for item_id in paid_by_item:
        paid_by_item[item_id] = paid_by_item[item_id][:used]
    equity_cashflows = equity_cashflows[:used]
    equity_contribution_series = equity_contribution_series[:used]
    debt_draw_series = debt_draw_series[:used]
    debt_repayment_series = debt_repayment_series[:used]
    interest_series = interest_series[:used]
    executed_cost_series = executed_cost_series[:used]
    contractual_paid_series = contractual_paid_series[:used]
    developer_distribution_series = developer_distribution_series[:used]
    developer_recovery_series = developer_recovery_series[:used]
    developer_recoverable_accrual_series = developer_recoverable_accrual_series[:used]
    landowner_distribution_series = landowner_distribution_series[:used]
    landowner_cash_receipt_series = landowner_cash_receipt_series[:used]
    contractual_accrual_series = contractual_accrual_series[:used]

    terminal_backlog = _zero_small(sum(backlogs.values(), ZERO))
    nondeferrable_backlog = _zero_small(sum(
        (backlogs[str(item.get("cost_id"))] for item in ordered_items if not B(item.get("deferrable"), True)),
        ZERO,
    ))
    contractual_arrears = _zero_small(contractual_arrears)
    finance_arrears = _zero_small(finance_arrears)
    developer_recoverable_balance = _zero_small(developer_recoverable_balance)
    debt = _zero_small(debt)
    cash = _zero_small(cash)
    deferrable_backlog = _zero_small(max(terminal_backlog - nondeferrable_backlog, ZERO))
    recoverable_shortfall = developer_recoverable_balance if recover_advances else ZERO
    mandatory_shortfall = _zero_small(
        contractual_arrears + finance_arrears + nondeferrable_backlog + recoverable_shortfall
    )
    # Funding gap and mandatory shortfall are deliberately non-overlapping.
    # Negative cash represents executed uses with no source; deferrable backlog
    # represents unexecuted scope. Contractual/finance/nondeferrable arrears are
    # classified only as mandatory shortfall.
    unsupported_gap = _zero_small(peak_negative_cash + deferrable_backlog)
    terminal_reason_codes: list[str] = []
    if peak_negative_cash > EPS:
        terminal_reason_codes.append("NEGATIVE_CASH")
    if deferrable_backlog > EPS:
        terminal_reason_codes.append("DEFERRABLE_COST_BACKLOG")
    if contractual_arrears > EPS:
        terminal_reason_codes.append("CONTRACTUAL_ARREARS")
    if finance_arrears > EPS:
        terminal_reason_codes.append("FINANCE_ARREARS")
    if nondeferrable_backlog > EPS:
        terminal_reason_codes.append("NONDEFERRABLE_COST_BACKLOG")
    if recoverable_shortfall > EPS:
        terminal_reason_codes.append("DEVELOPER_RECOVERABLE_UNSETTLED")
    terminal_diagnostic = {
        "status": "FAIL" if terminal_reason_codes else "PASS",
        "first_failed_month": (
            next(
                (
                    row["month"]
                    for row in diagnostic_ledger
                    if set(row.get("reason_codes") or []).intersection(terminal_reason_codes)
                ),
                None,
            )
            if terminal_reason_codes
            else None
        ),
        "reason_codes": terminal_reason_codes,
        "unsupported_funding_gap": unsupported_gap,
        "negative_cash_gap": peak_negative_cash,
        "deferrable_cost_gap": deferrable_backlog,
        "mandatory_shortfall": mandatory_shortfall,
        "contractual_arrears": contractual_arrears,
        "finance_arrears": finance_arrears,
        "nondeferrable_cost_backlog": nondeferrable_backlog,
        "developer_recoverable_shortfall": recoverable_shortfall,
        "corrective_action": (
            "Close each listed obligation with documented funding, payment rescheduling, or an approved scope/timing revision."
            if terminal_reason_codes else None
        ),
        "target_page": "developer/funding" if terminal_reason_codes else None,
    }
    terminal_debt = debt
    completion_status = (
        "COMPLETE"
        if (
            terminal_backlog <= EPS
            and contractual_arrears <= EPS
            and finance_arrears <= EPS
            and terminal_debt <= EPS
            and developer_recoverable_balance <= EPS
            and unsupported_gap <= EPS
            and maximum_cash_balance_variance <= CASH_RECONCILIATION_TOLERANCE
        )
        else "INCOMPLETE"
    )
    adjusted_index = used - 1
    extension_months = max(0, adjusted_index - planned_end)
    return {
        "monthly_engine_version": MONTHLY_ENGINE_VERSION,
        "config": {
            "finance_enabled": config.finance_enabled,
            "finance_mode": "STRUCTURED_FINANCE" if config.finance_enabled else "SELF_FUNDED_FROM_COLLECTIONS",
            "spend_policy": config.spend_policy,
            "allow_negative_cash": config.allow_negative_cash,
            "defer_contractual_payments": config.defer_contractual_payments,
            "maximum_extension_months": config.maximum_extension_months,
            "initial_cash": initial_cash,
            "initial_cash_source": "DEVELOPER_EQUITY_AT_BASE_DATE",
            "committed_equity": committed_equity,
            "committed_debt": committed_debt,
            "minimum_cash_balance": config.minimum_cash_balance,
            "cash_reconciliation_tolerance": CASH_RECONCILIATION_TOLERANCE,
            "distribution_policy": {
                "enabled": distribution_enabled,
                "frequency_code": distribution_frequency_code,
                "frequency_months": distribution_frequency,
                "first_distribution_month": first_distribution_month,
                "reserve_basis": reserve_basis,
                "future_cost_reserve_share": future_cost_reserve_share,
                "minimum_operating_cash": policy_minimum_cash,
                "allocation_method": allocation_method,
                "contractual_payment_timing": contractual_payment_timing,
                "recover_developer_advances_before_landowner_cash": recover_advances,
            },
        },
        "rows": rows,
        "diagnostic_ledger": diagnostic_ledger,
        "terminal_diagnostic": terminal_diagnostic,
        "paid_by_item": paid_by_item,
        "equity_cashflows": equity_cashflows,
        "base_date_equity_contribution": initial_cash,
        "equity_contributions": equity_contribution_series,
        "debt_draws": debt_draw_series,
        "debt_repayments": debt_repayment_series,
        "interest": interest_series,
        "executed_costs": executed_cost_series,
        "contractual_accruals": contractual_accrual_series,
        "contractual_paid": contractual_paid_series,
        "developer_distributions": developer_distribution_series,
        "developer_advance_recoveries": developer_recovery_series,
        "developer_recoverable_accruals": developer_recoverable_accrual_series,
        "landowner_distributions": landowner_distribution_series,
        "landowner_cash_receipts": landowner_cash_receipt_series,
        "distribution_ledger": distribution_ledger,
        "terminal_backlog": terminal_backlog,
        "contractual_arrears": contractual_arrears,
        "finance_arrears": finance_arrears,
        "mandatory_shortfall": mandatory_shortfall,
        "mandatory_shortfall_components": {
            "contractual_arrears": contractual_arrears,
            "finance_arrears": finance_arrears,
            "nondeferrable_cost_backlog": nondeferrable_backlog,
            "developer_recoverable_shortfall": recoverable_shortfall,
        },
        "hybrid_minimum_execution_shortfall": total_hybrid_minimum_shortfall,
        "unsupported_funding_gap": unsupported_gap,
        "unsupported_funding_gap_components": {
            "negative_cash_gap": peak_negative_cash,
            "deferrable_cost_gap": deferrable_backlog,
        },
        "peak_negative_cash": peak_negative_cash,
        "peak_debt": peak_debt,
        "peak_equity": peak_equity,
        "available_equity_capacity": max(committed_equity - cumulative_equity, ZERO),
        "available_debt_capacity": max(committed_debt - terminal_debt, ZERO),
        "ending_debt": terminal_debt,
        "ending_cash": cash,
        "maximum_cash_balance_variance": maximum_cash_balance_variance,
        "cash_reconciliation_passed": maximum_cash_balance_variance <= CASH_RECONCILIATION_TOLERANCE,
        "total_interest": total_interest,
        "total_fees": total_fees,
        "total_finance_paid": total_finance_paid,
        "total_debt_drawn": sum(debt_draw_series, ZERO),
        "total_debt_repaid": sum(debt_repayment_series, ZERO),
        # Gross equity contributions and gross developer distributions must
        # be read from their dedicated series.  Net equity cash flow is valid
        # for IRR/NPV, but can conceal a contribution and distribution that
        # occur in the same month.
        "total_equity_contributed": initial_cash + sum(equity_contribution_series, ZERO),
        "total_developer_distributions": sum(developer_distribution_series, ZERO),
        "total_developer_advance_recoveries": sum(developer_recovery_series, ZERO),
        "total_developer_equity_receipts": sum(developer_distribution_series, ZERO) + sum(developer_recovery_series, ZERO),
        "total_developer_recoverable_accrued": total_developer_recoverable_accrued,
        "ending_developer_recoverable_balance": developer_recoverable_balance,
        "total_landowner_cash_receipts": sum(landowner_cash_receipt_series, ZERO),
        "total_executed_cost": total_executed_cost,
        "total_contractual_paid": total_contractual_paid,
        "completion_status": completion_status,
        "original_completion_index": planned_end,
        "adjusted_completion_index": adjusted_index,
        "schedule_extension_months": extension_months,
    }
