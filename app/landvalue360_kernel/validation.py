"""Validation message primitives used by every calculation service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    ADVISORY = "ADVISORY"


@dataclass(frozen=True, slots=True)
class ValidationMessage:
    severity: Severity
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }


def strict_boolean(value: object, *, path: str) -> bool:
    """Accept only a JSON boolean; never coerce non-empty strings to ``True``."""

    from .exceptions import InputValidationError

    if not isinstance(value, bool):
        raise InputValidationError(
            "Value must be a JSON boolean (true or false).",
            path=path,
            code="BOOLEAN_REQUIRED",
        )
    return value


def strict_integer(value: object, *, path: str, minimum: int | None = None) -> int:
    """Parse an integer without silently truncating a decimal value."""

    from decimal import Decimal, InvalidOperation

    from .exceptions import InputValidationError

    if isinstance(value, bool):
        raise InputValidationError("Boolean is not a valid integer.", path=path, code="INTEGER_REQUIRED")
    try:
        numeric = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise InputValidationError("Value must be an integer.", path=path, code="INTEGER_REQUIRED") from exc
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise InputValidationError("Value must be an integer.", path=path, code="INTEGER_REQUIRED")
    parsed = int(numeric)
    if minimum is not None and parsed < minimum:
        raise InputValidationError(
            f"Integer value must be at least {minimum}.",
            path=path,
            code="INTEGER_BELOW_MINIMUM",
        )
    return parsed
