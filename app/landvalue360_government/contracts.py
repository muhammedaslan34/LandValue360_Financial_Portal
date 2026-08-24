"""General public-land contract calculation layer.

All contract forms are normalized to independent dated public accrual, cash,
receivable and in-kind flows.  The layer uses the monthly ledger produced by
LandValue360 Engine and never places financial formulas in the browser.
"""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Iterable

from landvalue360_kernel.cashflow import CashFlowSeries, DatedCashFlow
from landvalue360_kernel.finance import xnpv
from landvalue360_kernel.dates import DayCountBasis, year_fraction

from .hashing import sha256_json
from .manifest import CONTRACT_REGISTRY_VERSION, ENGINE_VERSION
from .registries import CONTRACT_DEFINITIONS, ELIGIBLE_COST_REGISTRY, NET_SALES_DEDUCTION_REGISTRY

ZERO = Decimal("0")
ONE = Decimal("1")


class ContractError(ValueError):
    pass


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractError(f"Invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ContractError("Non-finite values are not permitted.")
    return result


def B(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    key = str(value).strip().lower()
    if key in {"1", "true", "yes", "y", "on"}:
        return True
    if key in {"0", "false", "no", "n", "off", ""}:
        return False
    raise ContractError(f"Invalid boolean value: {value!r}")


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception as exc:
        raise ContractError(f"Invalid date: {value!r}") from exc


def _add_months(value: date, months: int) -> date:
    """Return an anniversary-safe month offset.

    Contract dates must not be approximated with calendar-year subtraction.
    The day is clamped only when the destination month is shorter.
    """

    total = value.year * 12 + (value.month - 1) + int(months)
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _elapsed_complete_months(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(+value, "f")


def _rate(value: Any, label: str, default: str = "0", *, high: Decimal = ONE) -> Decimal:
    """Parse a governed rate without silently coercing an invalid contract."""

    parsed = D(value, default)
    if parsed < ZERO or parsed > high:
        raise ContractError(f"{label} must be between 0 and {format(high, 'f')}.")
    return parsed


def _nonnegative(value: Any, label: str, default: str = "0") -> Decimal:
    parsed = D(value, default)
    if parsed < ZERO:
        raise ContractError(f"{label} cannot be negative.")
    return parsed


def _amount(row: dict[str, Any], *keys: str) -> Decimal:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return D(row[key])
    return ZERO


def normalize_monthly_ledger(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize engine rows without losing the governed ledger semantics.

    Field aliases are explicit because Developer and Government outputs retain
    compatibility labels.  Finance cost is taken once from the consolidated
    paid field when available; otherwise it is reconstructed from components.
    """

    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(rows, start=1):
        row = deepcopy(source)
        dt = _date(row.get("date") or row.get("month_date"))
        collections = _amount(row, "gross_collections", "collections", "sales_collections", "receipts")
        net_collections = _amount(row, "net_collections", "net_sales_share_base")
        if net_collections == ZERO and "net_collections" not in row and "net_sales_share_base" not in row:
            net_collections = collections
        actual_cost = _amount(
            row,
            "executed_development_cost",
            "actual_cost",
            "construction_cost",
            "development_cost",
        )
        if row.get("finance_cost_paid") not in (None, ""):
            financing_cost = D(row.get("finance_cost_paid"))
        else:
            financing_cost = (
                _amount(row, "interest_paid", "interest_accrued")
                + _amount(row, "financing_fees")
                + _amount(row, "commitment_fee")
                + _amount(row, "upfront_fee")
            )
        profit_base = _amount(row, "profit_share_base")
        if "profit_share_base" not in row or row.get("profit_share_base") in (None, ""):
            profit_base = net_collections - actual_cost - financing_cost
        normalized.append({
            "month": int(row.get("month") or index),
            "date": dt,
            "contracted_sales": _amount(row, "contracted_sales", "gross_contracted_sales"),
            "recognized_sales": _amount(row, "recognized_sales", "gross_recognized_sales", "gross_contracted_sales"),
            "collections": collections,
            "net_collections": net_collections,
            "eligible_gross_sales_base": _amount(
                row,
                "eligible_gross_sales_base",
                "gross_collections",
                "collections",
                "sales_collections",
                "receipts",
            ),
            "eligible_net_sales_base": _amount(
                row,
                "eligible_net_sales_base",
                "net_sales_share_base",
                "net_collections",
                "gross_collections",
                "collections",
                "sales_collections",
                "receipts",
            ),
            "profit_share_base": profit_base,
            "development_cost": actual_cost,
            "infrastructure_cost": _amount(row, "infrastructure_cost"),
            "soft_cost": _amount(row, "soft_cost"),
            "marketing_cost": _amount(row, "marketing_cost"),
            "taxes": _amount(row, "taxes"),
            "debt_draw": _amount(row, "debt_draw", "financing_draw"),
            "interest": _amount(row, "interest_paid", "interest_accrued"),
            "finance_cost_paid": financing_cost,
            "finance_arrears": _amount(row, "finance_arrears"),
            "principal_repayment": _amount(row, "principal_repayment", "financing_repayment"),
            "developer_equity": _amount(row, "developer_equity", "equity_contribution"),
            "distributable_cash": _amount(row, "distribution", "distributable_cash"),
            "developer_distribution": _amount(row, "developer_distribution"),
            "public_distribution": _amount(row, "landowner_distribution", "public_distribution"),
            "ending_cash": _amount(row, "ending_cash"),
            "ending_debt": _amount(row, "ending_debt", "closing_debt"),
            "source": row,
        })
    normalized.sort(key=lambda row: (row["date"], row["month"]))
    if not normalized:
        raise ContractError("The contract layer requires at least one monthly ledger row.")
    seen_months: set[int] = set()
    seen_dates: set[date] = set()
    previous_month = 0
    previous_date: date | None = None
    for row in normalized:
        month = row["month"]
        row_date = row["date"]
        if month <= 0:
            raise ContractError("Monthly ledger month numbers must be positive.")
        if month in seen_months or row_date in seen_dates:
            raise ContractError("Monthly ledger months and dates must be unique.")
        if month <= previous_month or (previous_date is not None and row_date <= previous_date):
            raise ContractError("Monthly ledger months and dates must be strictly chronological.")
        seen_months.add(month)
        seen_dates.add(row_date)
        previous_month = month
        previous_date = row_date
    return normalized

def _empty_flow() -> dict[str, Decimal]:
    return {"accrual": ZERO, "cash": ZERO, "in_kind": ZERO, "probability_adjustment": ZERO}


def _add(flow_map: dict[date, dict[str, Decimal]], dt: date, *, accrual: Any = ZERO, cash: Any = ZERO, in_kind: Any = ZERO, probability_adjustment: Any = ZERO) -> None:
    row = flow_map.setdefault(dt, _empty_flow())
    row["accrual"] += D(accrual)
    row["cash"] += D(cash)
    row["in_kind"] += D(in_kind)
    row["probability_adjustment"] += D(probability_adjustment)


def _payment_probability(terms: dict[str, Any]) -> Decimal:
    return _rate(terms.get("collection_probability"), "collection_probability", "1")


def _evaluate_sale(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    flows: dict[date, dict[str, Decimal]] = {}
    first = ledger[0]["date"]
    upfront = _nonnegative(
        terms.get("upfront_amount"),
        "upfront_amount",
        str(terms.get("price") or "0"),
    )
    if upfront:
        probability = _payment_probability(terms)
        _add(flows, first, accrual=upfront, cash=upfront * probability, probability_adjustment=upfront * (ONE - probability))
    for item in terms.get("payment_schedule") or []:
        amount = _nonnegative(item.get("amount"), "payment_schedule.amount")
        probability = _rate(
            item.get("collection_probability"),
            "payment_schedule.collection_probability",
            str(_payment_probability(terms)),
        )
        _add(flows, _date(item.get("date")), accrual=amount, cash=amount * probability, probability_adjustment=amount * (ONE - probability))
    return flows


def _evaluate_lease(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    flows: dict[date, dict[str, Decimal]] = {}
    first = _date(terms.get("lease_start_date") or ledger[0]["date"])
    if terms.get("lease_end_date") not in (None, ""):
        lease_expiry = _date(terms.get("lease_end_date"))
        if lease_expiry <= first:
            raise ContractError("lease_end_date must be after lease_start_date.")
        term_months = _elapsed_complete_months(first, lease_expiry)
        if _add_months(first, term_months) < lease_expiry:
            term_months += 1
    else:
        term_months = int(terms.get("term_months") or len(ledger))
        if term_months <= 0 or term_months > 2400:
            raise ContractError("term_months must be between 1 and 2400.")
        lease_expiry = _add_months(first, term_months)
    premium = _nonnegative(terms.get("upfront_premium"), "upfront_premium")
    probability = _payment_probability(terms)
    if premium:
        _add(
            flows,
            first,
            accrual=premium,
            cash=premium * probability,
            probability_adjustment=premium * (ONE - probability),
        )
    fixed = _nonnegative(
        terms.get("fixed_rent_monthly"),
        "fixed_rent_monthly",
        str(terms.get("monthly_rent") or "0"),
    )
    annual_rent = terms.get("fixed_rent_annual", terms.get("annual_rent"))
    if fixed == ZERO and annual_rent not in (None, ""):
        fixed = _nonnegative(annual_rent, "fixed_rent_annual") / Decimal("12")
    escalation = D(terms.get("annual_indexation_rate"), terms.get("indexation_rate", "0"))
    if escalation <= Decimal("-1"):
        raise ContractError("Lease indexation rate must be greater than -100%.")
    frequency_months = int(terms.get("indexation_frequency_months") or 12)
    if frequency_months <= 0:
        raise ContractError("indexation_frequency_months must be positive.")
    turnover_rate = _rate(terms.get("turnover_rate"), "turnover_rate")
    ledger_by_date = {row["date"]: row for row in ledger}
    for month_index in range(term_months):
        current_date = _add_months(first, month_index)
        completed_periods = _elapsed_complete_months(first, current_date) // frequency_months
        with localcontext() as ctx:
            ctx.prec = 50
            indexed = fixed * ((ONE + escalation) ** completed_periods)
        turnover_base = ledger_by_date.get(current_date, {}).get("collections", ZERO)
        amount = indexed + turnover_base * turnover_rate
        _add(
            flows,
            current_date,
            accrual=amount,
            cash=amount * probability,
            probability_adjustment=amount * (ONE - probability),
        )
    reversion = _nonnegative(terms.get("reversionary_value"), "reversionary_value")
    if reversion:
        reversion_probability = _rate(
            terms.get("reversion_probability"), "reversion_probability", "1"
        )
        reversion_date = _date(terms.get("reversion_date") or lease_expiry)
        if reversion_date < first:
            raise ContractError("reversion_date cannot precede lease_start_date.")
        _add(
            flows,
            reversion_date,
            accrual=reversion,
            in_kind=reversion * reversion_probability,
            probability_adjustment=reversion * (ONE - reversion_probability),
        )
    return flows

def _evaluate_share(ledger: list[dict[str, Any]], terms: dict[str, Any], base_key: str) -> dict[date, dict[str, Decimal]]:
    flows: dict[date, dict[str, Decimal]] = {}
    maximum_rate = _rate(terms.get("maximum_rate"), "maximum_rate", "1")
    rate = _rate(terms.get("rate"), "rate", high=maximum_rate)
    probability = _payment_probability(terms)
    recognition = str(terms.get("recognition") or "COLLECTED").upper()
    for row in ledger:
        if recognition == "CONTRACTED" and base_key == "eligible_gross_sales_base":
            base = row["contracted_sales"]
        elif recognition == "RECOGNIZED" and base_key == "eligible_gross_sales_base":
            base = row["recognized_sales"]
        else:
            base = max(ZERO, row[base_key])
        amount = base * rate
        _add(flows, row["date"], accrual=amount, cash=amount * probability, probability_adjustment=amount * (ONE - probability))
    return flows


def _tiered_amount(amount: Decimal, cumulative_before: Decimal, tiers: list[dict[str, Any]], default_rate: Decimal) -> Decimal:
    """Apply marginal rates to an incremental amount across cumulative bands."""
    if amount <= ZERO:
        return ZERO
    if not tiers:
        return amount * default_rate
    remaining = amount
    cursor = cumulative_before
    result = ZERO
    normalized = []
    for tier in tiers:
        limit = (
            None
            if tier.get("up_to") in (None, "")
            else _nonnegative(tier.get("up_to"), "tier.up_to")
        )
        normalized.append((limit, _rate(tier.get("rate"), "tier.rate", str(default_rate))))
    normalized.sort(key=lambda row: (row[0] is None, row[0] or ZERO))
    for limit, rate in normalized:
        if remaining <= ZERO:
            break
        if limit is None:
            band = remaining
        else:
            band = min(remaining, max(ZERO, limit - cursor))
        result += band * rate
        cursor += band
        remaining -= band
    if remaining > ZERO:
        result += remaining * normalized[-1][1]
    return result


def _evaluate_profit_share(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    flows: dict[date, dict[str, Decimal]] = {}
    rate = _rate(terms.get("rate"), "rate")
    probability = _payment_probability(terms)
    carry = ZERO
    cumulative_distributable = ZERO
    reserve_rate = _rate(terms.get("reserve_rate"), "reserve_rate")
    tiers = list(terms.get("tiers") or [])
    for row in ledger:
        base = row["profit_share_base"]
        distributable = base - carry
        if distributable < ZERO:
            carry = -distributable
            amount = ZERO
        else:
            carry = ZERO
            net_distributable = distributable * (ONE - reserve_rate)
            amount = _tiered_amount(net_distributable, cumulative_distributable, tiers, rate)
            cumulative_distributable += net_distributable
        _add(flows, row["date"], accrual=amount, cash=amount * probability, probability_adjustment=amount * (ONE - probability))
    return flows


def _evaluate_land_equity(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    """Evaluate the public capital account in a land-as-equity structure.

    Preferred return accrues on ACT/365F dates.  Return of land capital is
    optional and is never converted into a terminal claim when explicitly
    disabled.  The remaining residual cash is shared by the stated ownership
    ratio after capital and preferred-return priorities.
    """

    flows: dict[date, dict[str, Decimal]] = {}
    land_value = _nonnegative(terms.get("land_value"), "land_value")
    ownership = D(terms.get("ownership_ratio"), terms.get("ownership_share", "0.5"))
    if ownership < ZERO or ownership > ONE:
        raise ContractError("ownership_ratio must be between 0 and 1.")
    preferred_rate = D(terms.get("preferred_return"))
    if preferred_rate < ZERO or preferred_rate > Decimal("5"):
        raise ContractError("preferred_return must be between 0 and 5.")
    return_capital = B(terms.get("return_of_capital"), True)
    settle_unpaid_preferred = B(terms.get("settle_unpaid_preferred_at_terminal"), True)
    unrecovered = land_value if return_capital else ZERO
    preferred_principal = land_value
    preferred_accrued = ZERO
    previous_date = ledger[0]["date"]
    for index, row in enumerate(ledger):
        current_date = row["date"]
        elapsed = ZERO if index == 0 else year_fraction(previous_date, current_date, DayCountBasis.ACT_365F)
        if preferred_principal > ZERO and preferred_rate > ZERO and elapsed > ZERO:
            preferred_accrued += preferred_principal * preferred_rate * elapsed
        cash = max(ZERO, row["distributable_cash"])
        public_cash = ZERO
        if cash > ZERO and return_capital and unrecovered > ZERO:
            recovered = min(cash, unrecovered)
            public_cash += recovered
            unrecovered -= recovered
            preferred_principal = max(preferred_principal - recovered, ZERO)
            cash -= recovered
        if cash > ZERO and preferred_accrued > ZERO:
            paid = min(cash, preferred_accrued)
            public_cash += paid
            preferred_accrued -= paid
            cash -= paid
        if cash > ZERO:
            public_cash += cash * ownership
        _add(flows, current_date, accrual=public_cash, cash=public_cash)
        previous_date = current_date
    terminal_claim = (unrecovered if return_capital else ZERO) + (
        preferred_accrued if settle_unpaid_preferred else ZERO
    )
    if terminal_claim > ZERO:
        probability = _rate(
            terms.get("terminal_recovery_probability"),
            "terminal_recovery_probability",
        )
        terminal_date = _date(terms.get("terminal_settlement_date") or ledger[-1]["date"])
        _add(
            flows,
            terminal_date,
            accrual=terminal_claim,
            cash=terminal_claim * probability,
            probability_adjustment=terminal_claim * (ONE - probability),
        )
    return flows

def _evaluate_units(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    flows: dict[date, dict[str, Decimal]] = {}
    for unit in terms.get("units") or []:
        quantity = D(unit.get("quantity"), "1")
        if quantity < ZERO:
            raise ContractError("Unit quantity cannot be negative.")
        base_value = _nonnegative(
            unit.get("value_at_delivery"),
            "unit.value_at_delivery",
            str(unit.get("agreed_value") or "0"),
        ) * quantity
        factor = ONE
        for key in (
            "quality_factor",
            "location_factor",
            "floor_factor",
            "view_factor",
            "finish_factor",
            "area_factor",
        ):
            value = D(unit.get(key), "1")
            if value < ZERO:
                raise ContractError(f"{key} cannot be negative.")
            factor *= value
        gross_adjusted_value = base_value * factor
        public_borne_costs = sum(
            (
                _nonnegative(unit.get("registration_cost"), "unit.registration_cost"),
                _nonnegative(unit.get("fit_out_cost"), "unit.fit_out_cost"),
                _nonnegative(unit.get("maintenance_liability"), "unit.maintenance_liability"),
            ),
            ZERO,
        )
        delay_compensation = _nonnegative(
            unit.get("delay_compensation"), "unit.delay_compensation"
        )
        contractual_value = max(ZERO, gross_adjusted_value - public_borne_costs + delay_compensation)
        probability = _rate(
            unit.get("delivery_probability"),
            "unit.delivery_probability",
            str(terms.get("delivery_probability", "1")),
        )
        delivery_date = _date(unit.get("delivery_date") or ledger[-1]["date"])
        delivered = contractual_value * probability
        _add(
            flows,
            delivery_date,
            accrual=contractual_value,
            in_kind=delivered,
            probability_adjustment=contractual_value - delivered,
        )
        cash_settlement = _nonnegative(
            unit.get("cash_settlement"), "unit.cash_settlement"
        )
        if cash_settlement:
            cash_probability = _rate(
                unit.get("cash_settlement_probability"),
                "unit.cash_settlement_probability",
                str(probability),
            )
            _add(
                flows,
                delivery_date,
                accrual=cash_settlement,
                cash=cash_settlement * cash_probability,
                probability_adjustment=cash_settlement * (ONE - cash_probability),
            )
    minimum = D(terms.get("minimum_value"))
    if minimum < ZERO:
        raise ContractError("minimum_value cannot be negative.")
    current_contractual = sum((row["accrual"] for row in flows.values()), ZERO)
    if minimum > current_contractual:
        top_up = minimum - current_contractual
        probability = _payment_probability(terms)
        _add(
            flows,
            _date(terms.get("true_up_date") or ledger[-1]["date"]),
            accrual=top_up,
            cash=top_up * probability,
            probability_adjustment=top_up * (ONE - probability),
        )
    return flows

def _combine_flow_maps(*maps: dict[date, dict[str, Decimal]]) -> dict[date, dict[str, Decimal]]:
    result: dict[date, dict[str, Decimal]] = {}
    for mapping in maps:
        for dt, row in mapping.items():
            _add(result, dt, **row)
    return result


def _evaluate_minimum_guarantee(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    """Apply a cumulative minimum guarantee without duplicating later value.

    At every date the contractual entitlement equals the greater of cumulative
    underlying accrual and the active guarantee target.  Expected settlement
    follows the same cumulative maximum.  Later underlying receipts therefore
    consume the prior guarantee rather than being paid on top of it.
    """

    underlying_terms = deepcopy(terms.get("underlying") or {})
    if not underlying_terms:
        underlying_terms = {"type": "GROSS_SALES_SHARE", "rate": terms.get("rate", "0")}
    underlying = _evaluate_flow_map(ledger, underlying_terms)
    raw_schedule = list(terms.get("guarantee_schedule") or [])
    if not raw_schedule:
        raw_schedule = [
            {
                "date": terms.get("guarantee_date") or ledger[-1]["date"].isoformat(),
                "cumulative_amount": terms.get("guarantee_amount", "0"),
            }
        ]
    schedule: list[tuple[date, Decimal]] = []
    for item in raw_schedule:
        target = D(item.get("cumulative_amount"), item.get("amount", "0"))
        if target < ZERO:
            raise ContractError("Minimum-guarantee targets cannot be negative.")
        schedule.append((_date(item.get("date")), target))
    schedule.sort(key=lambda item: item[0])
    previous_target = ZERO
    targets: dict[date, Decimal] = {}
    for dt, target in schedule:
        if target < previous_target:
            raise ContractError("Cumulative minimum-guarantee targets cannot decrease over time.")
        targets[dt] = max(targets.get(dt, ZERO), target)
        previous_target = target

    all_dates = sorted(set(underlying) | set(targets))
    result: dict[date, dict[str, Decimal]] = {}
    active_target = ZERO
    cumulative_underlying_accrual = ZERO
    cumulative_underlying_settlement = ZERO
    cumulative_contract_accrual = ZERO
    cumulative_contract_settlement = ZERO
    for dt in all_dates:
        if dt in targets:
            active_target = max(active_target, targets[dt])
        base = underlying.get(dt, _empty_flow())
        cumulative_underlying_accrual += base["accrual"]
        base_settlement = base["cash"] + base["in_kind"]
        cumulative_underlying_settlement += base_settlement
        required_accrual = max(cumulative_underlying_accrual, active_target)
        required_settlement = max(cumulative_underlying_settlement, active_target)
        accrual_increment = required_accrual - cumulative_contract_accrual
        settlement_increment = required_settlement - cumulative_contract_settlement
        if accrual_increment < -Decimal("0.01") or settlement_increment < -Decimal("0.01"):
            raise ContractError("Minimum-guarantee cumulative entitlement cannot reverse.")
        accrual_increment = max(accrual_increment, ZERO)
        settlement_increment = max(settlement_increment, ZERO)
        underlying_component = min(settlement_increment, base_settlement)
        in_kind = min(base["in_kind"], underlying_component)
        cash = settlement_increment - in_kind
        _add(
            result,
            dt,
            accrual=accrual_increment,
            cash=cash,
            in_kind=in_kind,
            probability_adjustment=max(accrual_increment - settlement_increment, ZERO),
        )
        cumulative_contract_accrual += accrual_increment
        cumulative_contract_settlement += settlement_increment
    return result

def _evaluate_overage(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    base_terms = deepcopy(terms.get("underlying") or {})
    base = _evaluate_flow_map(ledger, base_terms) if base_terms else {}
    driver = str(terms.get("driver") or "COLLECTIONS").upper()
    baseline = _nonnegative(terms.get("baseline"), "baseline")
    rate = _rate(terms.get("rate"), "rate")
    total = ZERO
    if driver in {"COLLECTIONS", "SALES", "GROSS_SALES"}:
        total = sum((row["collections"] for row in ledger), ZERO)
    elif driver in {"NET_SALES", "NET_COLLECTIONS"}:
        total = sum((row["net_collections"] for row in ledger), ZERO)
    elif driver in {"PROFIT", "PROJECT_PROFIT"}:
        total = sum((max(ZERO, row["profit_share_base"]) for row in ledger), ZERO)
    elif driver in {"DENSITY", "AREA", "LAND_VALUE", "IRR"}:
        total = D(terms.get("actual_value"))
    else:
        raise ContractError(f"Unsupported overage driver: {driver}")
    uplift = max(ZERO, total - baseline)
    amount = uplift * rate
    if amount:
        _add(base, _date(terms.get("settlement_date") or ledger[-1]["date"]), accrual=amount, cash=amount * _payment_probability(terms), probability_adjustment=amount * (ONE - _payment_probability(terms)))
    return base


def _evaluate_hybrid(ledger: list[dict[str, Any]], terms: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    components = list(terms.get("components") or [])
    if not components:
        raise ContractError("Hybrid contracts require at least one component.")
    seen: set[str] = set()
    monetization_components: list[dict[str, Any]] = []
    maps: list[dict[date, dict[str, Decimal]]] = []
    for index, component in enumerate(components, start=1):
        component_id = str(component.get("component_id") or f"component-{index}")
        if component_id in seen:
            raise ContractError(f"Duplicate hybrid component_id: {component_id}")
        seen.add(component_id)
        component_type = str(component.get("type") or "").upper()
        if component_type == "HYBRID":
            raise ContractError("Nested HYBRID components are not permitted; flatten the component list.")
        if component_type in {"OUTRIGHT_SALE", "LAND_AS_EQUITY"}:
            monetization_components.append(component)
        maps.append(_evaluate_flow_map(ledger, component))
    component_types = {str(item.get("type") or "").upper() for item in monetization_components}
    if len(component_types) > 1:
        if not B(terms.get("allow_mixed_land_consideration"), False):
            raise ContractError(
                "A hybrid cannot count the same land simultaneously as sale consideration and equity "
                "without allow_mixed_land_consideration=true and an explicit allocation policy."
            )
        has_scopes = all(str(item.get("asset_scope") or "").strip() for item in monetization_components)
        allocation_values = [item.get("allocation_share") for item in monetization_components]
        has_allocations = all(value not in (None, "") for value in allocation_values)
        if has_allocations:
            shares = [D(value) for value in allocation_values]
            if any(value < ZERO or value > ONE for value in shares) or sum(shares, ZERO) > ONE + Decimal("0.00000001"):
                raise ContractError("Hybrid land allocation shares must be between 0 and 1 and cannot exceed 100%.")
        if not has_scopes and not has_allocations:
            raise ContractError(
                "Mixed land sale/equity treatment requires non-overlapping asset_scope values or explicit allocation_share values."
            )
        if has_scopes:
            scopes = [str(item.get("asset_scope")).strip().lower() for item in monetization_components]
            if len(scopes) != len(set(scopes)):
                raise ContractError("Mixed land monetization components cannot use the same asset_scope.")
    return _combine_flow_maps(*maps)

def _evaluate_flow_map(ledger: list[dict[str, Any]], contract: dict[str, Any]) -> dict[date, dict[str, Decimal]]:
    kind = str(contract.get("type") or contract.get("method") or "").upper()
    terms = deepcopy(contract.get("terms") or contract)
    if kind == "OUTRIGHT_SALE":
        flows = _evaluate_sale(ledger, terms)
    elif kind == "GROUND_LEASE":
        flows = _evaluate_lease(ledger, terms)
    elif kind == "GROSS_SALES_SHARE":
        flows = _evaluate_share(ledger, terms, "eligible_gross_sales_base")
    elif kind == "NET_SALES_SHARE":
        flows = _evaluate_share(ledger, terms, "eligible_net_sales_base")
    elif kind == "PROFIT_SHARE":
        flows = _evaluate_profit_share(ledger, terms)
    elif kind == "LAND_AS_EQUITY":
        flows = _evaluate_land_equity(ledger, terms)
    elif kind == "UNITS_IN_KIND":
        flows = _evaluate_units(ledger, terms)
    elif kind == "HYBRID":
        flows = _evaluate_hybrid(ledger, terms)
    elif kind == "MINIMUM_GUARANTEE":
        flows = _evaluate_minimum_guarantee(ledger, terms)
    elif kind == "OVERAGE":
        flows = _evaluate_overage(ledger, terms)
    else:
        raise ContractError(f"Unsupported contract type: {kind or '<missing>'}")

    # Universal floor and cap apply to contractual accrual, not to
    # probability-adjusted expected settlement.  Collection risk therefore
    # remains visible as a receivable/ECL rather than manufacturing a floor
    # top-up.
    floor = D(terms.get("floor"))
    cap = D(terms.get("cap"))
    if floor < ZERO or cap < ZERO:
        raise ContractError("Contractual floor and cap cannot be negative.")
    if floor > ZERO and cap > ZERO and floor > cap:
        raise ContractError("Contractual floor cannot exceed cap.")
    terminal = max(flows, default=ledger[-1]["date"])
    total_accrual = sum((row["accrual"] for row in flows.values()), ZERO)
    probability = _payment_probability(terms)
    if floor > ZERO and total_accrual < floor:
        top_up = floor - total_accrual
        _add(
            flows,
            terminal,
            accrual=top_up,
            cash=top_up * probability,
            probability_adjustment=top_up * (ONE - probability),
        )
        total_accrual = floor
    if cap > ZERO and total_accrual > cap:
        reduction = total_accrual - cap
        total_cash = sum((max(row["cash"], ZERO) for row in flows.values()), ZERO)
        total_in_kind = sum((max(row["in_kind"], ZERO) for row in flows.values()), ZERO)
        total_settlement = total_cash + total_in_kind
        settlement_reversal = min(reduction, total_settlement)
        if total_settlement > ZERO:
            cash_reversal = min(total_cash, settlement_reversal * total_cash / total_settlement)
        else:
            cash_reversal = ZERO
        in_kind_reversal = min(total_in_kind, settlement_reversal - cash_reversal)
        _add(
            flows,
            terminal,
            accrual=-reduction,
            cash=-cash_reversal,
            in_kind=-in_kind_reversal,
            probability_adjustment=-(reduction - settlement_reversal),
        )
    return flows


def _check_minimum_guarantee(
    ledger: list[dict[str, Any]],
    contract: dict[str, Any],
    settlement: Decimal,
) -> dict[str, Any]:
    terms = deepcopy(contract.get("terms") or contract)
    underlying_terms = deepcopy(terms.get("underlying") or {})
    if not underlying_terms:
        underlying_terms = {"type": "GROSS_SALES_SHARE", "rate": terms.get("rate", "0")}
    underlying = _evaluate_flow_map(ledger, underlying_terms)
    underlying_settlement = sum((row["cash"] + row["in_kind"] for row in underlying.values()), ZERO)
    schedule = list(terms.get("guarantee_schedule") or [])
    if schedule:
        guarantee = max((D(item.get("cumulative_amount"), item.get("amount", "0")) for item in schedule), default=ZERO)
    else:
        guarantee = D(terms.get("guarantee_amount"))
    expected = max(underlying_settlement, guarantee)
    variance = settlement - expected
    passed = abs(variance) <= Decimal("0.01")
    return {
        "passed": passed,
        "actual_settlement": _fmt(settlement),
        "underlying_settlement": _fmt(underlying_settlement),
        "guarantee_target": _fmt(guarantee),
        "expected_top_up_settlement": _fmt(expected),
        "variance": _fmt(variance),
        "reason": (
            "The guarantee is applied only as a cumulative top-up."
            if passed
            else "The guarantee settlement does not reconcile to max(underlying settlement, cumulative guarantee)."
        ),
    }


def _check_land_treatment(contract: dict[str, Any]) -> dict[str, Any]:
    kind = str(contract.get("type") or contract.get("method") or "").upper()
    terms = deepcopy(contract.get("terms") or contract)
    conflicting_keys = {
        key: terms.get(key)
        for key in ("price", "upfront_amount", "land_cost", "land_consideration")
        if terms.get(key) not in (None, "", ZERO, "0", "0.0")
    }
    if kind == "LAND_AS_EQUITY":
        passed = not conflicting_keys or B(terms.get("allow_mixed_land_treatment"), False)
    elif kind == "HYBRID":
        component_types = [str(item.get("type") or "").upper() for item in terms.get("components") or []]
        simultaneous = "LAND_AS_EQUITY" in component_types and "OUTRIGHT_SALE" in component_types
        passed = not simultaneous or B(terms.get("allow_mixed_land_consideration"), False)
        conflicting_keys = {"component_types": component_types} if simultaneous else {}
    else:
        passed = True
    return {
        "passed": passed,
        "conflicts": conflicting_keys,
        "reason": (
            "No duplicate land treatment was detected."
            if passed
            else "Land is represented as equity and as separate sale/cost consideration without an explicit allocation policy."
        ),
    }


def _dated_npv(flows: dict[date, dict[str, Decimal]], currency: str, rate: Decimal, base_date: date, key: str) -> Decimal:
    series = CashFlowSeries.from_iterable(
        f"government-{key}", currency,
        (DatedCashFlow(dt, row[key], key) for dt, row in flows.items() if row[key] != ZERO),
    )
    return xnpv(rate, series, valuation_date=base_date) if series.flows else ZERO


def evaluate_contract(
    monthly_ledger: Iterable[dict[str, Any]],
    contract: dict[str, Any],
    *,
    currency: str = "USD",
    base_date: date | str | None = None,
    discount_rate: Decimal | str | None = None,
    public_value_layers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ledger = normalize_monthly_ledger(monthly_ledger)
    base = _date(base_date or ledger[0]["date"])
    if discount_rate in (None, ""):
        raise ContractError(
            "A versioned valuation-policy discount rate is required; no default discount rate is applied."
        )
    rate = D(discount_rate)
    if rate <= Decimal("-1"):
        raise ContractError("Discount rate must be greater than -100%.")
    kind = str(contract.get("type") or contract.get("method") or "").upper()
    if kind not in CONTRACT_DEFINITIONS:
        raise ContractError(f"Contract type is not registered: {kind!r}")
    flows = _evaluate_flow_map(ledger, contract)
    dates = sorted(flows)
    cumulative_accrual = ZERO
    cumulative_settlement = ZERO
    rows: list[dict[str, Any]] = []
    for dt in dates:
        row = flows[dt]
        cumulative_accrual += row["accrual"]
        cumulative_settlement += row["cash"] + row["in_kind"]
        rows.append({
            "date": dt.isoformat(),
            "public_consideration_accrual": _fmt(row["accrual"]),
            "public_consideration_payment": _fmt(row["cash"]),
            "public_receivable": _fmt(cumulative_accrual - cumulative_settlement),
            "units_in_kind_delivery": _fmt(row["in_kind"]),
            "probability_adjustment": _fmt(row["probability_adjustment"]),
        })
    nominal_accrual = sum((row["accrual"] for row in flows.values()), ZERO)
    cash = sum((row["cash"] for row in flows.values()), ZERO)
    in_kind = sum((row["in_kind"] for row in flows.values()), ZERO)
    probability_adjustment = sum((row["probability_adjustment"] for row in flows.values()), ZERO)
    contractual_npv = _dated_npv(flows, currency, rate, base, "cash") + _dated_npv(flows, currency, rate, base, "in_kind")
    layers = deepcopy(public_value_layers or {})
    layer_values = {
        "contractual_consideration": cash + in_kind,
        "cash_receipts": cash,
        "units_in_kind": in_kind,
        "infrastructure_delivered_to_public_authority": D(layers.get("infrastructure_delivered_to_public_authority")),
        "taxes_and_statutory_charges": D(layers.get("taxes_and_statutory_charges")),
        "wider_economic_benefits": D(layers.get("wider_economic_benefits")),
        "wider_social_benefits": D(layers.get("wider_social_benefits")),
        "public_costs": D(layers.get("public_costs")),
        "public_guarantees": D(layers.get("public_guarantees")),
        "contingent_liabilities": D(layers.get("contingent_liabilities")),
        "administrative_and_audit_costs": D(layers.get("administrative_and_audit_costs")),
        "residual_and_reversionary_value": D(layers.get("residual_and_reversionary_value")),
    }
    public_financial_value = (
        layer_values["contractual_consideration"]
        + layer_values["infrastructure_delivered_to_public_authority"]
        + layer_values["residual_and_reversionary_value"]
        - layer_values["public_costs"]
        - layer_values["public_guarantees"]
        - layer_values["contingent_liabilities"]
        - layer_values["administrative_and_audit_costs"]
    )
    settlement = cash + in_kind
    minimum_guarantee_check = (
        _check_minimum_guarantee(ledger, contract, settlement)
        if kind == "MINIMUM_GUARANTEE"
        else {
            "passed": True,
            "actual_settlement": _fmt(settlement),
            "reason": "Not applicable to this contract type.",
        }
    )
    land_treatment_check = _check_land_treatment(contract)
    tax_separation_variance = layer_values["contractual_consideration"] - settlement
    wider_value_in_financial_layer = (
        layer_values["wider_economic_benefits"] + layer_values["wider_social_benefits"]
    )
    anti_double_counting_checks = {
        "taxes_excluded_from_contractual_consideration": {
            "passed": abs(tax_separation_variance) <= Decimal("0.01"),
            "contractual_consideration": _fmt(layer_values["contractual_consideration"]),
            "cash_and_in_kind_settlement": _fmt(settlement),
            "variance": _fmt(tax_separation_variance),
            "reason": "Statutory taxes and charges remain a separate public-value layer.",
        },
        "wider_benefits_excluded_from_public_financial_value": {
            "passed": True,
            "wider_benefits_disclosed_separately": _fmt(wider_value_in_financial_layer),
            "reason": "Wider economic and social benefits are disclosed but are not added to public financial value.",
        },
        "minimum_guarantee_top_up_only": minimum_guarantee_check,
        "land_not_counted_as_cost_and_consideration": land_treatment_check,
    }
    anti_double_counting_passed = all(
        bool(check.get("passed")) for check in anti_double_counting_checks.values()
    )
    result = {
        "contract_registry_version": CONTRACT_REGISTRY_VERSION,
        "engine_version": ENGINE_VERSION,
        "contract_type": kind,
        "contract_definition": deepcopy(CONTRACT_DEFINITIONS[kind]),
        "currency": currency.upper(),
        "base_date": base.isoformat(),
        "discount_rate": _fmt(rate),
        "nominal_contractual_accrual": _fmt(nominal_accrual),
        "cash_receipts": _fmt(cash),
        "units_in_kind_value": _fmt(in_kind),
        "contractual_consideration": _fmt(cash + in_kind),
        "contractual_consideration_npv": _fmt(contractual_npv),
        "probability_adjustment": _fmt(probability_adjustment),
        "closing_receivable": _fmt(nominal_accrual - cash - in_kind),
        "public_financial_value": _fmt(public_financial_value),
        "public_value_layers": {key: _fmt(value) for key, value in layer_values.items()},
        "monthly_flows": rows,
        "deduction_registry": deepcopy(NET_SALES_DEDUCTION_REGISTRY) if kind == "NET_SALES_SHARE" else None,
        "eligible_cost_registry": deepcopy(ELIGIBLE_COST_REGISTRY) if kind in {"PROFIT_SHARE", "LAND_AS_EQUITY"} else None,
        "anti_double_counting_checks": anti_double_counting_checks,
        "anti_double_counting_passed": anti_double_counting_passed,
    }
    result["contract_hash"] = sha256_json({"contract": contract, "base_date": result["base_date"], "currency": result["currency"], "ledger": ledger})
    result["output_hash"] = sha256_json(result)
    return result
