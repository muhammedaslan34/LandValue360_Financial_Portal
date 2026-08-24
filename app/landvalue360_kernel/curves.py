"""Weight curves and dated phasing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from .cashflow import CashFlowSeries, DatedCashFlow
from .dates import parse_date
from .decimal_utils import ONE, ZERO, decimal
from .exceptions import InputValidationError


@dataclass(frozen=True, slots=True)
class CurvePoint:
    date: date
    weight: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "date", parse_date(self.date, path="curve.date"))
        weight = decimal(self.weight, path="curve.weight")
        if weight < ZERO:
            raise InputValidationError("Curve weights cannot be negative.", path="curve.weight")
        object.__setattr__(self, "weight", weight)


def parse_curve(raw: Iterable[dict[str, object]], *, path: str, tolerance: Decimal = Decimal("0.00000001")) -> tuple[CurvePoint, ...]:
    points = tuple(
        CurvePoint(parse_date(item["date"], path=f"{path}[{index}].date"), decimal(item["weight"], path=f"{path}[{index}].weight"))
        for index, item in enumerate(raw)
    )
    if not points:
        raise InputValidationError("A phasing curve must contain at least one point.", path=path)
    total = sum((point.weight for point in points), ZERO)
    if abs(total - ONE) > tolerance:
        raise InputValidationError(
            f"Curve weights must sum to 1. Current total: {total}.",
            path=path,
            code="CURVE_NOT_RECONCILED",
        )
    return tuple(sorted(points, key=lambda point: point.date))


def phase_total(
    total: Decimal,
    curve: Iterable[CurvePoint],
    *,
    series_id: str,
    currency: str,
    label: str = "",
    description: str = "",
) -> CashFlowSeries:
    total = decimal(total)
    return CashFlowSeries.from_iterable(
        series_id,
        currency,
        (DatedCashFlow(point.date, total * point.weight, label) for point in curve),
        description,
    )


def lag_date(source_date: date, lag_days: int) -> date:
    return source_date + timedelta(days=lag_days)


def apply_collection_rules(
    source: CashFlowSeries,
    rules: Iterable[tuple[int, Decimal]],
    *,
    series_id: str,
    description: str = "",
) -> CashFlowSeries:
    parsed_rules = tuple((int(lag), decimal(weight)) for lag, weight in rules)
    if not parsed_rules:
        parsed_rules = ((0, ONE),)
    if any(weight < ZERO for _, weight in parsed_rules):
        raise InputValidationError("Collection-rule weights cannot be negative.")
    total_weight = sum((weight for _, weight in parsed_rules), ZERO)
    if abs(total_weight - ONE) > Decimal("0.00000001"):
        raise InputValidationError(
            f"Collection-rule weights must sum to 1. Current total: {total_weight}.",
            code="COLLECTION_RULE_NOT_RECONCILED",
        )
    flows: list[DatedCashFlow] = []
    for source_flow in source.flows:
        for lag_days, weight in parsed_rules:
            if lag_days < 0:
                raise InputValidationError("Collection lag cannot be negative.")
            flows.append(
                DatedCashFlow(
                    lag_date(source_flow.date, lag_days),
                    source_flow.amount * weight,
                    source_flow.label,
                )
            )
    return CashFlowSeries.from_iterable(series_id, source.currency, flows, description)
