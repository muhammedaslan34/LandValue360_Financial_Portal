"""Dated cash-flow primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import accumulate
from typing import Iterable, Iterator

from .dates import parse_date
from .decimal_utils import ZERO, as_json_number, decimal


@dataclass(frozen=True, slots=True, order=True)
class DatedCashFlow:
    date: date
    amount: Decimal
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "date", parse_date(self.date, path="cash_flow.date"))
        object.__setattr__(self, "amount", decimal(self.amount, path="cash_flow.amount"))

    def to_dict(self) -> dict[str, str]:
        return {
            "date": self.date.isoformat(),
            "amount": as_json_number(self.amount) or "0",
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class CashFlowSeries:
    series_id: str
    currency: str
    flows: tuple[DatedCashFlow, ...]
    description: str = ""

    def __post_init__(self) -> None:
        currency = self.currency.upper().strip()
        object.__setattr__(self, "currency", currency)
        aggregated: dict[date, Decimal] = {}
        labels: dict[date, list[str]] = {}
        for flow in self.flows:
            aggregated[flow.date] = aggregated.get(flow.date, ZERO) + flow.amount
            if flow.label:
                labels.setdefault(flow.date, []).append(flow.label)
        canonical = tuple(
            DatedCashFlow(dt, amount, "; ".join(labels.get(dt, [])))
            for dt, amount in sorted(aggregated.items())
            if amount != ZERO or labels.get(dt)
        )
        object.__setattr__(self, "flows", canonical)

    @classmethod
    def from_iterable(
        cls,
        series_id: str,
        currency: str,
        flows: Iterable[DatedCashFlow],
        description: str = "",
    ) -> "CashFlowSeries":
        return cls(series_id, currency, tuple(flows), description)

    def __iter__(self) -> Iterator[DatedCashFlow]:
        return iter(self.flows)

    def __len__(self) -> int:
        return len(self.flows)

    @property
    def total(self) -> Decimal:
        return sum((flow.amount for flow in self.flows), ZERO)

    @property
    def first_date(self) -> date | None:
        return self.flows[0].date if self.flows else None

    @property
    def last_date(self) -> date | None:
        return self.flows[-1].date if self.flows else None

    def amount_on(self, target_date: date) -> Decimal:
        for flow in self.flows:
            if flow.date == target_date:
                return flow.amount
        return ZERO

    def cumulative_points(self) -> tuple[tuple[date, Decimal], ...]:
        running = ZERO
        points: list[tuple[date, Decimal]] = []
        for flow in self.flows:
            running += flow.amount
            points.append((flow.date, running))
        return tuple(points)

    def scale(self, factor: Decimal, *, series_id: str | None = None) -> "CashFlowSeries":
        factor = decimal(factor)
        return CashFlowSeries.from_iterable(
            series_id or self.series_id,
            self.currency,
            (DatedCashFlow(flow.date, flow.amount * factor, flow.label) for flow in self.flows),
            self.description,
        )

    def negate(self, *, series_id: str | None = None) -> "CashFlowSeries":
        return self.scale(decimal("-1"), series_id=series_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "series_id": self.series_id,
            "currency": self.currency,
            "description": self.description,
            "points": [flow.to_dict() for flow in self.flows],
        }


def combine_series(
    series_id: str,
    currency: str,
    *series: CashFlowSeries,
    description: str = "",
) -> CashFlowSeries:
    flows: list[DatedCashFlow] = []
    for item in series:
        if item.currency != currency:
            raise ValueError(f"Currency mismatch in combined series: {item.currency} vs {currency}")
        flows.extend(item.flows)
    return CashFlowSeries.from_iterable(series_id, currency, flows, description)


def subtract_series(
    series_id: str,
    left: CashFlowSeries,
    right: CashFlowSeries,
    *,
    description: str = "",
) -> CashFlowSeries:
    if left.currency != right.currency:
        raise ValueError("Cannot subtract cash-flow series with different currencies.")
    return combine_series(series_id, left.currency, left, right.negate(), description=description)


def series_from_date_amounts(
    series_id: str,
    currency: str,
    values: Iterable[tuple[date | str, Decimal | str | int | float]],
    *,
    label: str = "",
    description: str = "",
) -> CashFlowSeries:
    return CashFlowSeries.from_iterable(
        series_id,
        currency,
        (DatedCashFlow(parse_date(dt), decimal(amount), label) for dt, amount in values),
        description,
    )
