"""High-precision decimal helpers.

All contractual and financial amounts enter the kernel as :class:`Decimal`.
Binary floating point is never used for money, percentages, areas, or reported
financial metrics. Transcendental functions required by XNPV/XIRR are also
performed with Decimal's ``ln`` and ``exp`` operations under a controlled
context.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Any

from .exceptions import InputValidationError

DECIMAL_PRECISION = 50
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


def decimal(value: Any, *, path: str | None = None) -> Decimal:
    """Convert input to a finite :class:`Decimal` without float contamination."""

    if isinstance(value, bool):
        raise InputValidationError("Boolean values are not valid numeric inputs.", path=path)
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, float):
        # ``str`` preserves the user-facing decimal representation and avoids
        # importing the binary expansion of the float.
        result = Decimal(str(value))
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise InputValidationError(f"Invalid decimal value: {value!r}", path=path) from exc
    else:
        raise InputValidationError(f"Unsupported numeric type: {type(value).__name__}", path=path)

    if not result.is_finite():
        raise InputValidationError("Numeric inputs must be finite.", path=path)
    return result


def require_between(
    value: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    *,
    path: str,
    inclusive: bool = True,
) -> Decimal:
    valid = minimum <= value <= maximum if inclusive else minimum < value < maximum
    if not valid:
        boundary = "inclusive" if inclusive else "exclusive"
        raise InputValidationError(
            f"Value {value} must be between {minimum} and {maximum} ({boundary}).",
            path=path,
        )
    return value


def quantize(value: Decimal, places: int) -> Decimal:
    """Display-oriented bankers rounding. Never use inside core calculations."""

    exponent = Decimal(1).scaleb(-places)
    return value.quantize(exponent, rounding=ROUND_HALF_EVEN)


def decimal_power(base: Decimal, exponent: Decimal) -> Decimal:
    """Return ``base ** exponent`` using Decimal logarithms.

    The base must be strictly positive. This is required for discount factors.
    """

    if base <= ZERO:
        raise ValueError("Decimal power requires a positive base.")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return (base.ln() * exponent).exp()


def decimal_exp(value: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return value.exp()


def decimal_ln(value: Decimal) -> Decimal:
    if value <= ZERO:
        raise ValueError("Natural logarithm requires a positive value.")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        return value.ln()


def as_json_number(value: Decimal | None) -> str | None:
    """Serialize Decimal exactly as a canonical string for JSON output."""

    if value is None:
        return None
    normalized = value.normalize()
    # Decimal('0E-50') should be represented as "0".
    if normalized == ZERO:
        return "0"
    return format(normalized, "f")
