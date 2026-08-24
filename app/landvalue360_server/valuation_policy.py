"""Explicit valuation-policy discount-rate resolution.

The monthly engine and the contract report both consume the same effective
annual rate.  This module rejects missing or basis-incompatible metadata rather
than substituting a scalar default.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from landvalue360_kernel.decimal_utils import decimal_exp


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Valuation policy field {field} must be numeric.") from exc
    if not result.is_finite():
        raise ValueError(f"Valuation policy field {field} must be finite.")
    return result


def resolve_valuation_discount(
    policy_snapshot: dict[str, Any],
    *,
    project_currency: str,
    cashflow_basis: str = "NOMINAL",
) -> dict[str, Any]:
    financial = policy_snapshot.get("financial_constraints") or {}
    raw_rate = financial.get("government_discount_rate")
    rate_type = financial.get("discount_rate_type", financial.get("government_discount_rate_type"))
    currency = financial.get("discount_currency", financial.get("government_discount_currency"))
    compounding = financial.get("discount_compounding", financial.get("government_discount_compounding"))
    missing = [
        name
        for name, value in (
            ("government_discount_rate", raw_rate),
            ("discount_rate_type", rate_type),
            ("discount_currency", currency),
            ("discount_compounding", compounding),
        )
        if value in (None, "")
    ]
    if missing:
        raise ValueError(
            "Valuation policy must explicitly define financial_constraints."
            + ", financial_constraints.".join(missing)
            + "."
        )

    rate = _decimal(raw_rate, field="financial_constraints.government_discount_rate")
    rate_type_code = str(rate_type).upper()
    cashflow_basis_code = str(cashflow_basis or "NOMINAL").upper()
    if rate_type_code not in {"NOMINAL", "REAL"}:
        raise ValueError("Valuation policy discount_rate_type must be NOMINAL or REAL.")
    if cashflow_basis_code not in {"NOMINAL", "REAL"}:
        raise ValueError("Project cash-flow basis must be NOMINAL or REAL.")
    if rate_type_code != cashflow_basis_code:
        raise ValueError(
            f"Valuation discount basis {rate_type_code} does not match project cash-flow basis {cashflow_basis_code}."
        )

    project_currency_code = str(project_currency or "").upper()
    currency_code = str(currency).upper()
    if currency_code != "PROJECT_CURRENCY" and currency_code != project_currency_code:
        raise ValueError(
            f"Valuation discount currency {currency_code} does not match project currency {project_currency_code}."
        )

    compounding_code = str(compounding).upper()
    if compounding_code == "ANNUAL":
        effective_annual_rate = rate
    elif compounding_code == "MONTHLY":
        monthly_rate = rate / Decimal("12")
        if monthly_rate <= Decimal("-1"):
            raise ValueError("Monthly-compounded discount rate must remain greater than -100% per month.")
        effective_annual_rate = (Decimal("1") + monthly_rate) ** 12 - Decimal("1")
    elif compounding_code == "CONTINUOUS":
        effective_annual_rate = decimal_exp(rate) - Decimal("1")
    else:
        raise ValueError("Valuation policy discount_compounding must be ANNUAL, MONTHLY, or CONTINUOUS.")
    if effective_annual_rate <= Decimal("-1"):
        raise ValueError("Effective annual valuation discount rate must be greater than -100%.")

    provenance = policy_snapshot.get("valuation_policy_context") or {}
    sources = policy_snapshot.get("policy_sources") or policy_snapshot.get("assessment_policy_sources") or {}
    return {
        "policy_rate": rate,
        "effective_annual_rate": effective_annual_rate,
        "rate_type": rate_type_code,
        "currency": currency_code,
        "project_currency": project_currency_code,
        "compounding": compounding_code,
        "cashflow_basis": cashflow_basis_code,
        "policy_id": provenance.get("policy_id") or sources.get("valuation_policy_id"),
        "policy_version": provenance.get("policy_version") or sources.get("valuation_policy_version"),
        "effective_date": provenance.get("effective_date") or sources.get("valuation_policy_effective_date"),
    }
