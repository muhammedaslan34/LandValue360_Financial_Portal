"""Land valuation, evidence classification and auditable reconciliation.

Market evidence, model-derived residual capacity and unverified screening
assumptions are deliberately kept separate.  The module can still return a
provisional screening result when verified evidence is unavailable, but it
labels that result and prevents it from being mistaken for an independently
supported market valuation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .hashing import sha256_json

ZERO = Decimal("0")
ONE = Decimal("1")


class ValuationError(ValueError):
    pass


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValuationError(f"Invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValuationError("Non-finite valuation inputs are not permitted.")
    return result


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(+value, "f")


def _clamp(value: Decimal, low: Decimal = ZERO, high: Decimal = ONE) -> Decimal:
    return max(low, min(high, value))


REQUIRED_BASIS_FIELDS = (
    "valuation_date", "base_date", "basis_of_value", "currency", "nominal_or_real",
    "tax_basis", "title_and_ownership_assumptions", "encumbrances", "planning_and_zoning_status",
    "development_rights", "permitted_density", "infrastructure_obligations", "existing_use",
    "alternative_use", "highest_and_best_use", "special_assumptions", "extraordinary_assumptions",
    "market_evidence_date", "data_confidence", "material_valuation_uncertainty",
)

VERIFIED_MARKET_CLASSIFICATIONS = {
    "MARKET_EVIDENCE",
    "VERIFIED_MARKET_EVIDENCE",
    "INDEPENDENT_APPRAISAL",
    "VERIFIED_AUTHORITY_APPRAISAL",
    "TENDER_EVIDENCE",
    "AUDITED_TRANSACTION_EVIDENCE",
}
MODEL_DERIVED_CLASSIFICATIONS = {
    "MODEL_DERIVED_CAPACITY",
    "MONTHLY_RESIDUAL_DCF",
    "RESIDUAL_SCREENING",
}
PROVISIONAL_CLASSIFICATIONS = {
    "TECHNICAL_ASSUMPTION",
    "TECHNICAL_PROXY",
    "USER_INPUT",
    "USER_INPUT_BASELINE",
    "DEMO_ASSUMPTION",
    "TECHNICAL_SCREENING",
    "TECHNICAL_SCREENING_BASELINE",
    "POLICY_DEFAULT",
}


def validate_valuation_basis(basis: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_BASIS_FIELDS if basis.get(field) in (None, "")]
    if missing:
        raise ValuationError("Valuation basis is incomplete: " + ", ".join(missing))
    try:
        valuation_date = date.fromisoformat(str(basis["valuation_date"])[:10])
        base_date = date.fromisoformat(str(basis["base_date"])[:10])
        evidence_date = date.fromisoformat(str(basis["market_evidence_date"])[:10])
    except Exception as exc:
        raise ValuationError("Valuation, base and evidence dates must be ISO dates.") from exc
    confidence = D(basis["data_confidence"])
    if confidence < ZERO or confidence > ONE:
        raise ValuationError("data_confidence must be between 0 and 1.")
    result = deepcopy(basis)
    result["valuation_date"] = valuation_date.isoformat()
    result["base_date"] = base_date.isoformat()
    result["market_evidence_date"] = evidence_date.isoformat()
    result["currency"] = str(basis["currency"]).upper()
    result["data_confidence"] = _fmt(confidence)
    result["evidence_age_days"] = (valuation_date - evidence_date).days
    return result


def _classification(config: dict[str, Any], default: str) -> str:
    details = config.get("details") or {}
    return str(config.get("classification") or details.get("classification") or default).upper()


def _is_verified_market(classification: str) -> bool:
    return classification in VERIFIED_MARKET_CLASSIFICATIONS


def _adjusted_comparable(row: dict[str, Any]) -> dict[str, Any]:
    price = D(row.get("price_per_land_sqm"))
    if price < ZERO:
        raise ValuationError("Comparable unit price cannot be negative.")
    adjustments = row.get("adjustments") or {}
    factor = ONE
    applied: dict[str, str] = {}
    for key in (
        "location", "time", "size", "zoning", "density", "use", "shape", "infrastructure",
        "payment_terms", "development_rights", "vacancy", "legal_obligations", "transaction_conditions", "related_parties",
    ):
        adjustment = D(adjustments.get(key))
        if adjustment <= Decimal("-1"):
            raise ValuationError(f"Comparable adjustment {key} cannot reduce value by 100% or more.")
        factor *= ONE + adjustment
        applied[key] = _fmt(adjustment) or "0"
    adjusted = max(ZERO, price * factor)
    quality = _clamp(D(row.get("evidence_quality"), "0.5"), Decimal("0.01"), ONE)
    arms_length = bool(row.get("arms_length", True))
    verified = bool(row.get("verified", arms_length))
    return {
        "reference": str(row.get("reference") or "unreferenced"),
        "transaction_date": str(row.get("transaction_date") or ""),
        "unadjusted_price_per_land_sqm": _fmt(price),
        "adjustments": applied,
        "compound_adjustment_factor": _fmt(factor),
        "adjusted_price_per_land_sqm": _fmt(adjusted),
        "evidence_quality": _fmt(quality),
        "arms_length": arms_length,
        "verified": verified,
        "eligible": arms_length and verified,
    }


def _weighted_median(items: list[tuple[Decimal, Decimal]]) -> Decimal:
    valid = sorted(((value, max(ZERO, weight)) for value, weight in items if weight > ZERO), key=lambda item: item[0])
    if not valid:
        raise ValuationError("No positive reconciliation weights are available.")
    total = sum((weight for _, weight in valid), ZERO)
    running = ZERO
    for value, weight in valid:
        running += weight
        if running >= total / Decimal("2"):
            return value
    return valid[-1][0]


def _range(value: Decimal, uncertainty: Decimal) -> tuple[Decimal, Decimal]:
    uncertainty = _clamp(uncertainty, ZERO, Decimal("0.75"))
    return max(ZERO, value * (ONE - uncertainty)), max(ZERO, value * (ONE + uncertainty))


def _append_method(
    results: list[dict[str, Any]],
    *,
    method: str,
    value: Decimal,
    low: Decimal,
    high: Decimal,
    strength: Decimal,
    classification: str,
    details: Any,
    eligible_for_market_reconciliation: bool,
) -> None:
    if high < low:
        raise ValuationError(f"{method} high value cannot be below low value.")
    results.append(
        {
            "method": method,
            "value": _fmt(value),
            "low": _fmt(low),
            "high": _fmt(high),
            "evidence_strength": _fmt(_clamp(strength, Decimal("0.01"), ONE)),
            "classification": classification,
            "eligible_for_market_reconciliation": bool(eligible_for_market_reconciliation),
            "details": deepcopy(details),
        }
    )


def evaluate_valuation(
    basis: dict[str, Any],
    methods: dict[str, Any],
    *,
    land_area_sqm: Any,
    buildable_area_sqm: Any = ZERO,
    sellable_area_sqm: Any = ZERO,
) -> dict[str, Any]:
    normalized_basis = validate_valuation_basis(basis)
    land_area = D(land_area_sqm)
    if land_area <= ZERO:
        raise ValuationError("Land area must be positive.")
    buildable = D(buildable_area_sqm)
    sellable = D(sellable_area_sqm)
    results: list[dict[str, Any]] = []

    comparable_config = methods.get("comparables") or {}
    comparables = list(comparable_config.get("transactions") or [])
    if comparables:
        adjusted = [_adjusted_comparable(row) for row in comparables]
        points = [
            (D(row["adjusted_price_per_land_sqm"]), D(row["evidence_quality"]))
            for row in adjusted
            if row["eligible"]
        ]
        if points:
            unit_value = _weighted_median(points)
            value = unit_value * land_area
            quality = sum((weight for _, weight in points), ZERO) / Decimal(len(points))
            low, high = _range(value, Decimal("0.20") * (ONE - quality))
            _append_method(
                results,
                method="COMPARABLE_TRANSACTIONS",
                value=value,
                low=low,
                high=high,
                strength=quality,
                classification="MARKET_EVIDENCE",
                details=adjusted,
                eligible_for_market_reconciliation=True,
            )
        else:
            # Keep rejected rows visible in the evidence register without using
            # them to manufacture a market benchmark.
            results.append(
                {
                    "method": "COMPARABLE_TRANSACTIONS",
                    "value": None,
                    "low": None,
                    "high": None,
                    "evidence_strength": "0",
                    "classification": "UNVERIFIED_MARKET_EVIDENCE",
                    "eligible_for_market_reconciliation": False,
                    "details": adjusted,
                    "exclusion_reason": "No verified arm's-length comparable was supplied.",
                }
            )

    residual = methods.get("residual") or {}
    if residual:
        gdv = D(residual.get("gross_development_value"))
        costs = (
            D(residual.get("development_costs"))
            + D(residual.get("finance_costs"))
            + D(residual.get("taxes"))
            + D(residual.get("contingency"))
        )
        target_profit = D(residual.get("target_profit_amount"))
        if target_profit == ZERO:
            target_profit = gdv * D(residual.get("target_profit_on_revenue"))
        value = gdv - costs - target_profit
        uncertainty = D(residual.get("uncertainty"), "0.20")
        low, high = _range(max(ZERO, value), uncertainty)
        strength = _clamp(D(residual.get("evidence_strength"), "0.55"), Decimal("0.05"), ONE)
        classification = _classification(residual, "RESIDUAL_SCREENING")
        eligible = bool(residual.get("include_in_market_reconciliation", False)) and classification in {
            "MONTHLY_RESIDUAL_DCF",
            "INDEPENDENT_APPRAISAL",
            "VERIFIED_AUTHORITY_APPRAISAL",
        }
        _append_method(
            results,
            method="RESIDUAL",
            value=value,
            low=low,
            high=high,
            strength=strength,
            classification=classification,
            details={
                "gdv": _fmt(gdv),
                "costs": _fmt(costs),
                "target_profit": _fmt(target_profit),
                "calculation_basis": str(residual.get("calculation_basis") or "SCREENING_RESIDUAL"),
                "warning": None if classification == "MONTHLY_RESIDUAL_DCF" else "Model-derived capacity is not independent market evidence.",
            },
            eligible_for_market_reconciliation=eligible,
        )

    for key, label, default_classification in (
        ("existing_use_value", "EXISTING_USE_VALUE", "TECHNICAL_ASSUMPTION"),
        ("alternative_use_value", "ALTERNATIVE_USE_VALUE", "TECHNICAL_ASSUMPTION"),
        ("independent_appraisal", "INDEPENDENT_APPRAISAL", "INDEPENDENT_APPRAISAL"),
        ("tender_evidence", "TENDER_EVIDENCE", "TENDER_EVIDENCE"),
        ("scenario_valuation", "SCENARIO_VALUATION", "TECHNICAL_SCREENING"),
    ):
        config = methods.get(key) or {}
        if config and config.get("value") not in (None, ""):
            value = D(config.get("value"))
            strength = _clamp(D(config.get("evidence_strength"), "0.5"), Decimal("0.01"), ONE)
            low = D(config.get("low"), _fmt(value * Decimal("0.9")) or "0")
            high = D(config.get("high"), _fmt(value * Decimal("1.1")) or "0")
            classification = _classification(config, default_classification)
            verified = bool(config.get("verified", _is_verified_market(classification)))
            eligible = verified and _is_verified_market(classification)
            _append_method(
                results,
                method=label,
                value=value,
                low=low,
                high=high,
                strength=strength,
                classification=classification,
                details=config.get("details") or {},
                eligible_for_market_reconciliation=eligible,
            )

    usable_results = [row for row in results if row.get("value") not in (None, "")]
    if not usable_results:
        raise ValuationError("At least one valuation method must provide a result.")

    weights_config = methods.get("reconciliation_weights") or {}
    verified_results = [row for row in usable_results if row.get("eligible_for_market_reconciliation")]
    reconciliation_pool = verified_results or usable_results
    provisional = not bool(verified_results)
    weighted: list[tuple[Decimal, Decimal]] = []
    for result in usable_results:
        configured = weights_config.get(result["method"])
        method_weight = D(configured, result["evidence_strength"])
        if result not in reconciliation_pool:
            method_weight = ZERO
        result["reconciliation_weight"] = _fmt(method_weight)
        result["weight_reason"] = str(
            (methods.get("weight_reasons") or {}).get(result["method"])
            or ("Excluded from market-value reconciliation because it is model-derived or unverified." if method_weight == ZERO else "Evidence strength and method applicability.")
        )
        if method_weight > ZERO:
            weighted.append((D(result["value"]), method_weight))

    reconciled = _weighted_median(weighted)
    anchor = min(
        (row for row in reconciliation_pool if D(row.get("reconciliation_weight")) > ZERO),
        key=lambda row: (abs(D(row["value"]) - reconciled), -D(row["reconciliation_weight"]), row["method"]),
    )
    # The interval comes from one identified anchor method.  This avoids the
    # incoherent practice of taking independent medians of unrelated low and
    # high endpoints.
    low = D(anchor["low"])
    high = D(anchor["high"])

    existing = next((D(row["value"]) for row in usable_results if row["method"] == "EXISTING_USE_VALUE"), None)
    alternative = next((D(row["value"]) for row in usable_results if row["method"] == "ALTERNATIVE_USE_VALUE"), None)
    planning_reference = alternative if alternative is not None else reconciled
    planning_uplift = max(ZERO, planning_reference - existing) if existing is not None else None
    residual_value = next((row["value"] for row in usable_results if row["method"] == "RESIDUAL"), None)

    basis_confidence = D(normalized_basis["data_confidence"])
    verified_strength = max((D(row["evidence_strength"]) for row in verified_results), default=ZERO)
    effective_confidence = min(basis_confidence, verified_strength) if verified_results else min(basis_confidence, Decimal("0.49"))
    confidence_grade = "HIGH" if effective_confidence >= Decimal("0.8") else "MODERATE" if effective_confidence >= Decimal("0.55") else "LOW"
    evidence_status = "SUPPORTED_BY_VERIFIED_MARKET_EVIDENCE" if verified_results else "PROVISIONAL_SCREENING_ONLY"

    output = {
        "basis": normalized_basis,
        "methods": results,
        "evidence_readiness": {
            "status": evidence_status,
            "verified_method_count": len(verified_results),
            "usable_method_count": len(usable_results),
            "provisional": provisional,
            "eligible_methods": [row["method"] for row in verified_results],
            "excluded_methods": [
                {"method": row["method"], "classification": row.get("classification")}
                for row in usable_results
                if row not in verified_results
            ],
            "minimum_action": None if verified_results else "Obtain verified market evidence or an independent appraisal before relying on the result as market value.",
        },
        "reconciliation": {
            "method": "EVIDENCE_WEIGHTED_MEDIAN",
            "value": _fmt(reconciled),
            "low": _fmt(low),
            "high": _fmt(high),
            "anchor_method": anchor["method"],
            "interval_method": "ANCHOR_METHOD_INTERVAL",
            "provisional": provisional,
            "reason": (
                "Verified market evidence is reconciled by evidence-weighted median; the displayed interval is the interval of the identified anchor method."
                if not provisional
                else "No verified market evidence was supplied. The result is a provisional screening benchmark using disclosed assumptions and must not be represented as an independent market valuation."
            ),
        },
        "separated_value_concepts": {
            "market_value_of_land": None if provisional else _fmt(reconciled),
            "provisional_screening_benchmark": _fmt(reconciled) if provisional else None,
            "existing_use_value": _fmt(existing),
            "alternative_use_value": _fmt(alternative),
            "residual_land_value": residual_value,
            "residual_capacity": residual_value,
            "development_rights_value": _fmt(planning_uplift),
            "planning_uplift": _fmt(planning_uplift),
            "publicly_created_uplift": None,
            "developer_created_value": None,
        },
        "unit_values": {
            "per_land_sqm": _fmt(reconciled / land_area),
            "per_buildable_sqm": _fmt(reconciled / buildable) if buildable > ZERO else None,
            "per_sellable_sqm": _fmt(reconciled / sellable) if sellable > ZERO else None,
        },
        "confidence_grade": confidence_grade,
        "effective_confidence": _fmt(effective_confidence),
        "material_valuation_uncertainty": normalized_basis["material_valuation_uncertainty"],
    }
    output["valuation_hash"] = sha256_json(output)
    return output
