"""Calendar and day-count conventions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from .decimal_utils import decimal
from .exceptions import InputValidationError


class DayCountBasis(str, Enum):
    ACT_365F = "ACT_365F"
    ACT_360 = "ACT_360"


def parse_date(value: date | datetime | str, *, path: str | None = None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise InputValidationError(f"Invalid ISO date: {value}", path=path) from exc
    raise InputValidationError(f"Unsupported date type: {type(value).__name__}", path=path)


def year_fraction(start: date, end: date, basis: DayCountBasis = DayCountBasis.ACT_365F) -> Decimal:
    days = Decimal((end - start).days)
    if basis == DayCountBasis.ACT_365F:
        return days / decimal("365")
    if basis == DayCountBasis.ACT_360:
        return days / decimal("360")
    raise InputValidationError(f"Unsupported day-count basis: {basis}")
