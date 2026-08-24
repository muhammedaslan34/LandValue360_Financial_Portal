"""Deterministic valuation reconciliation for LandValue360 Enterprise.

The module deliberately does not claim to replace a signed professional
valuation.  It produces transparent indications from the project's frozen
calculation output, market evidence and explicit method assumptions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from math import sqrt
from typing import Any


VALUATION_MODEL_VERSION = "0.2.0"
ZERO = Decimal("0")
ONE = Decimal("1")


class ValuationError(ValueError):
    """Raised when a valuation request cannot be calculated transparently."""


def D(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None:
        if default is None:
            raise ValuationError("A required decimal value is missing.")
        return default
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - defensive
        raise ValuationError(f"Invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValuationError("Decimal values must be finite.")
    return result


def J(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: J(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [J(item) for item in value]
    return value


def _hash(value: Any) -> str:
    payload = json.dumps(J(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


EVIDENCE_REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {"type": "TITLE", "weight": Decimal("0.15"), "critical": True, "gate": "LEGAL"},
    {"type": "PLANNING", "weight": Decimal("0.15"), "critical": True, "gate": "PLANNING"},
    {"type": "MARKET_STUDY", "weight": Decimal("0.15"), "critical": True, "gate": "MARKET"},
    {"type": "COST_ESTIMATE", "weight": Decimal("0.15"), "critical": True, "gate": "COST"},
    {"type": "LEGAL_OPINION", "weight": Decimal("0.10"), "critical": False, "gate": "LEGAL"},
    {"type": "INFRASTRUCTURE", "weight": Decimal("0.10"), "critical": False, "gate": "PLANNING"},
    {"type": "FINANCE", "weight": Decimal("0.10"), "critical": False, "gate": "FINANCE"},
    {"type": "ENVIRONMENT_SOCIAL", "weight": Decimal("0.05"), "critical": False, "gate": "E_AND_S"},
    {"type": "MEASUREMENT", "weight": Decimal("0.05"), "critical": False, "gate": "PLANNING"},
)

EVIDENCE_STATUS_SCORE = {
    "VERIFIED": Decimal("1"),
    "UPLOADED": Decimal("0.60"),
    "UNDER_REVIEW": Decimal("0.70"),
    "REJECTED": ZERO,
    "ARCHIVED": ZERO,
}
ASSUMPTION_APPROVAL_SCORE = {
    "APPROVED": Decimal("1"),
    "REVIEWED": Decimal("0.85"),
    "DRAFT": Decimal("0.50"),
    "REJECTED": ZERO,
}
ASSUMPTION_EVIDENCE_SCORE = {
    "VERIFIED": Decimal("1"),
    "PARTIAL": Decimal("0.70"),
    "UNVERIFIED": Decimal("0.40"),
    "MISSING": ZERO,
    "NOT_APPLICABLE": Decimal("1"),
}
CRITICALITY_WEIGHT = {
    "CRITICAL": Decimal("4"),
    "HIGH": Decimal("3"),
    "MEDIUM": Decimal("2"),
    "LOW": Decimal("1"),
}

COST_ESTIMATE_CLASS_SCORE = {
    "CLASS_5": Decimal("20"),
    "CLASS_4": Decimal("40"),
    "CLASS_3": Decimal("70"),
    "CLASS_2": Decimal("90"),
    "CLASS_1": Decimal("100"),
}
DESIGN_MATURITY_SCORE = {
    "STRATEGIC": Decimal("20"),
    "CONCEPT": Decimal("40"),
    "SPATIAL_COORDINATION": Decimal("65"),
    "TECHNICAL_DESIGN": Decimal("85"),
    "CONSTRUCTION": Decimal("100"),
}
MEASUREMENT_BASIS_SCORE = {
    "IPMS": Decimal("100"),
    "LOCAL": Decimal("75"),
    "CUSTOM": Decimal("60"),
}


def calculate_study_maturity(project_context: dict[str, Any] | None) -> dict[str, Any]:
    context = project_context or {}
    cost_class = str(context.get("cost_estimate_class") or "UNKNOWN").upper()
    design_maturity = str(context.get("design_maturity") or "UNKNOWN").upper()
    measurement_basis = str(context.get("measurement_basis") or "UNKNOWN").upper()
    cost_score = COST_ESTIMATE_CLASS_SCORE.get(cost_class, Decimal("25"))
    design_score = DESIGN_MATURITY_SCORE.get(design_maturity, Decimal("25"))
    measurement_score = MEASUREMENT_BASIS_SCORE.get(measurement_basis, Decimal("40"))
    score = cost_score * Decimal("0.45") + design_score * Decimal("0.45") + measurement_score * Decimal("0.10")
    if score >= Decimal("85"):
        grade = "ADVANCED_DESIGN"
    elif score >= Decimal("70"):
        grade = "FEASIBILITY_READY"
    elif score >= Decimal("45"):
        grade = "CONCEPT_LEVEL"
    else:
        grade = "EARLY_SCREENING"
    return {
        "score": score,
        "grade": grade,
        "cost_estimate_class": cost_class,
        "cost_estimate_class_score": cost_score,
        "design_maturity": design_maturity,
        "design_maturity_score": design_score,
        "measurement_basis": measurement_basis,
        "measurement_basis_score": measurement_score,
        "institutional_gate_passed": cost_score >= Decimal("70") and design_score >= Decimal("65"),
    }


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def calculate_data_quality(
    evidence: list[dict[str, Any]],
    assumptions: list[dict[str, Any]],
    *,
    valuation_date: str,
) -> dict[str, Any]:
    as_of = _parse_iso_date(valuation_date) or date.today()
    evidence_rows: list[dict[str, Any]] = []
    critical_missing: list[str] = []
    critical_not_verified: list[str] = []
    gate_numerators: dict[str, Decimal] = {}
    gate_denominators: dict[str, Decimal] = {}
    evidence_total = ZERO

    for requirement in EVIDENCE_REQUIREMENTS:
        evidence_type = requirement["type"]
        candidates = [item for item in evidence if str(item.get("evidence_type")) == evidence_type]
        best_score = ZERO
        best_status = "MISSING"
        best_id = None
        expired = False
        for item in candidates:
            status = str(item.get("status") or "UPLOADED")
            score = EVIDENCE_STATUS_SCORE.get(status, ZERO)
            expiry = _parse_iso_date(item.get("expiry_date"))
            item_expired = bool(expiry and expiry < as_of)
            if item_expired:
                score = min(score, Decimal("0.25"))
            if score > best_score or (score == best_score and best_id is None):
                best_score = score
                best_status = status
                best_id = item.get("id")
                expired = item_expired
        weighted = best_score * requirement["weight"]
        evidence_total += weighted
        gate = requirement["gate"]
        gate_numerators[gate] = gate_numerators.get(gate, ZERO) + weighted
        gate_denominators[gate] = gate_denominators.get(gate, ZERO) + requirement["weight"]
        if requirement["critical"] and best_score == ZERO:
            critical_missing.append(evidence_type)
        if requirement["critical"] and best_score < ONE:
            critical_not_verified.append(evidence_type)
        evidence_rows.append(
            {
                "evidence_type": evidence_type,
                "weight": requirement["weight"],
                "critical": requirement["critical"],
                "status": best_status,
                "score": best_score,
                "weighted_score": weighted,
                "document_id": best_id,
                "expired": expired,
                "gate": gate,
            }
        )

    assumption_numerator = ZERO
    assumption_denominator = ZERO
    assumption_rows: list[dict[str, Any]] = []
    for item in assumptions:
        criticality = str(item.get("criticality") or "MEDIUM")
        weight = CRITICALITY_WEIGHT.get(criticality, Decimal("2"))
        confidence = D(item.get("confidence_score", 0)) / Decimal("100")
        confidence = max(ZERO, min(ONE, confidence))
        approval = ASSUMPTION_APPROVAL_SCORE.get(str(item.get("approval_status") or "DRAFT"), ZERO)
        evidence_factor = ASSUMPTION_EVIDENCE_SCORE.get(str(item.get("evidence_status") or "MISSING"), ZERO)
        row_score = confidence * approval * evidence_factor
        assumption_numerator += row_score * weight
        assumption_denominator += weight
        assumption_rows.append(
            {
                "assumption_id": item.get("id"),
                "assumption_key": item.get("assumption_key"),
                "criticality": criticality,
                "score": row_score,
                "confidence_score": confidence * Decimal("100"),
                "approval_status": item.get("approval_status"),
                "evidence_status": item.get("evidence_status"),
            }
        )
    assumption_score = assumption_numerator / assumption_denominator if assumption_denominator else ZERO
    total_score = (evidence_total * Decimal("0.70") + assumption_score * Decimal("0.30")) * Decimal("100")

    gates: dict[str, Any] = {}
    for gate in sorted(gate_denominators):
        score = gate_numerators[gate] / gate_denominators[gate] * Decimal("100")
        gates[gate] = {
            "score": score,
            "status": "PASS" if score >= Decimal("70") else "REVIEW" if score >= Decimal("50") else "FAIL",
        }

    if total_score >= Decimal("85") and not critical_not_verified:
        grade = "INSTITUTIONAL_GRADE"
    elif total_score >= Decimal("70") and not critical_missing:
        grade = "FEASIBILITY_GRADE"
    elif total_score >= Decimal("50"):
        grade = "PRELIMINARY_GRADE"
    else:
        grade = "SCREENING_ONLY"

    return J(
        {
            "score": total_score,
            "grade": grade,
            "evidence_component_score": evidence_total * Decimal("100"),
            "assumption_component_score": assumption_score * Decimal("100"),
            "critical_missing": critical_missing,
            "critical_not_verified": critical_not_verified,
            "evidence_requirements": evidence_rows,
            "assumptions": assumption_rows,
            "readiness_gates": gates,
        }
    )


def _method_row(
    method_id: str,
    value: Decimal,
    *,
    input_weight: Decimal,
    confidence: Decimal,
    uncertainty: Decimal,
    source: str,
    explanation: str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if value < ZERO:
        value = ZERO
    confidence = max(ZERO, min(ONE, confidence))
    uncertainty = max(ZERO, min(Decimal("0.95"), uncertainty))
    return {
        "method_id": method_id,
        "value": value,
        "low_value": max(ZERO, value * (ONE - uncertainty)),
        "high_value": value * (ONE + uncertainty),
        "input_weight": max(ZERO, input_weight),
        "confidence": confidence,
        "source": source,
        "explanation": explanation,
        "diagnostics": diagnostics or {},
    }


def _comparable_method(
    comparables: list[dict[str, Any]],
    *,
    subject_area: Decimal,
    input_weight: Decimal,
    default_uncertainty: Decimal,
) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    numerator = ZERO
    denominator = ZERO
    for item in comparables:
        area = D(item.get("land_area_sqm"), ZERO)
        price = D(item.get("transaction_price"), ZERO)
        reliability = max(ZERO, min(ONE, D(item.get("reliability_weight"), ONE)))
        if area <= ZERO or price <= ZERO or reliability <= ZERO:
            continue
        adjustments = sum(
            (
                D(item.get(name), ZERO)
                for name in (
                    "location_adjustment",
                    "planning_adjustment",
                    "size_adjustment",
                    "time_adjustment",
                    "other_adjustment",
                )
            ),
            ZERO,
        )
        adjustment_factor = ONE + adjustments
        if adjustment_factor <= ZERO:
            raise ValuationError("Comparable adjustments produce a non-positive adjusted price.")
        raw_unit = price / area
        adjusted_unit = raw_unit * adjustment_factor
        indicated_value = adjusted_unit * subject_area
        numerator += adjusted_unit * reliability
        denominator += reliability
        rows.append(
            {
                "comparable_id": item.get("comparable_id"),
                "label": item.get("label"),
                "raw_unit_price": raw_unit,
                "total_adjustment": adjustments,
                "adjusted_unit_price": adjusted_unit,
                "indicated_subject_value": indicated_value,
                "reliability_weight": reliability,
                "evidence_document_id": item.get("evidence_document_id"),
            }
        )
    if not rows or denominator <= ZERO:
        return None
    mean = numerator / denominator
    variance = sum(
        (D(row["reliability_weight"]) * (D(row["adjusted_unit_price"]) - mean) ** 2 for row in rows),
        ZERO,
    ) / denominator
    std = Decimal(str(sqrt(float(max(ZERO, variance)))))
    relative_dispersion = std / mean if mean > ZERO else default_uncertainty
    uncertainty = max(default_uncertainty, min(Decimal("0.50"), relative_dispersion))
    average_reliability = denominator / Decimal(len(rows))
    confidence = min(ONE, average_reliability * min(ONE, Decimal(len(rows)) / Decimal("3")))
    return _method_row(
        "MARKET_COMPARABLES",
        mean * subject_area,
        input_weight=input_weight,
        confidence=confidence,
        uncertainty=uncertainty,
        source="ADJUSTED_MARKET_COMPARABLES",
        explanation="Weighted adjusted land unit prices applied to the subject gross land area.",
        diagnostics={
            "subject_land_area_sqm": subject_area,
            "weighted_unit_price": mean,
            "unit_price_standard_deviation": std,
            "comparable_count": len(rows),
            "comparables": rows,
        },
    )


def _calculate_valuation_impl(
    *,
    calculation_output: dict[str, Any],
    policy_snapshot: dict[str, Any],
    request: dict[str, Any],
    evidence: list[dict[str, Any]],
    assumptions: list[dict[str, Any]],
) -> dict[str, Any]:
    truth = calculation_output.get("financial_truth") or ((calculation_output.get("unified_financial_result") or {}).get("financial_truth") or {})
    if truth:
        if not truth.get("result_usable") or truth.get("calculation_status") == "FAIL":
            raise ValuationError("A valid reconciled unified financial calculation is required for valuation.")
    elif calculation_output.get("status") == "FAILED" or not calculation_output.get("approved_case"):
        raise ValuationError("A successful feasibility calculation is required for valuation.")
    approved = calculation_output.get("approved_case") or {}
    legacy_metrics = approved.get("metrics") or {}
    metrics = {
        **legacy_metrics,
        **({
            "project_npv": truth.get("project_npv"),
            "government_cash_npv": truth.get("government_consideration_npv"),
        } if truth else {}),
    }
    revenue = calculation_output.get("revenue") or {}
    costs = calculation_output.get("costs") or {}
    planning = calculation_output.get("planning") or {}
    finance = calculation_output.get("finance_analysis") or {}
    finance_metrics = finance.get("metrics") or {}

    valuation_date = str(request.get("valuation_date") or calculation_output.get("valuation_date"))
    subject_area = D(planning.get("gross_land_area_sqm"), ZERO)
    if subject_area <= ZERO:
        raise ValuationError("Subject gross land area must be positive.")
    configuration = request.get("method_configuration") or {}
    valuation_policy = policy_snapshot.get("valuation_policy") or {}
    financial_policy = policy_snapshot.get("financial_constraints") or {}
    project_context = dict(request.get("project_context") or {})
    study_maturity = calculate_study_maturity(project_context)
    institutional_threshold = D(valuation_policy.get("institutional_data_quality_threshold"), Decimal("85"))
    feasibility_threshold = D(valuation_policy.get("feasibility_data_quality_threshold"), Decimal("70"))
    preliminary_threshold = D(valuation_policy.get("preliminary_data_quality_threshold"), Decimal("50"))
    minimum_reconciliation_methods = int(valuation_policy.get("minimum_reconciliation_methods") or 2)
    target_profit_on_cost = D(
        configuration.get("target_developer_profit_on_cost"),
        D(valuation_policy.get("target_developer_profit_on_cost"), D(financial_policy.get("minimum_profit_on_cost"), Decimal("0.20"))),
    )
    if target_profit_on_cost < ZERO:
        raise ValuationError("Target developer profit on cost cannot be negative.")
    quality = calculate_data_quality(evidence, assumptions, valuation_date=valuation_date)
    quality_factor = D(quality["score"]) / Decimal("100")

    methods: list[dict[str, Any]] = []
    def config(method: str, default_weight: str, default_uncertainty: str) -> tuple[bool, Decimal, Decimal, Decimal, Any]:
        raw = configuration.get(method.lower()) or {}
        return (
            bool(raw.get("enabled", True)),
            D(raw.get("weight"), Decimal(default_weight)),
            D(raw.get("confidence"), quality_factor),
            D(raw.get("uncertainty"), Decimal(default_uncertainty)),
            raw.get("override_value"),
        )

    enabled, weight, confidence, uncertainty, override = config("DCF", "0.35", "0.15")
    if enabled:
        value = D(override) if override is not None else D(metrics.get("project_npv"), ZERO)
        methods.append(
            _method_row(
                "DCF",
                value,
                input_weight=weight,
                confidence=confidence,
                uncertainty=uncertainty,
                source="MANUAL_OVERRIDE" if override is not None else "PROJECT_NPV_EXCLUDING_LAND_AND_FINANCE",
                explanation=(
                    "Present value of project development cash flow before land consideration and financing. "
                    "This is a development-residual DCF indication, not a standalone signed appraisal."
                ),
                diagnostics={"project_npv": metrics.get("project_npv")},
            )
        )

    enabled, weight, confidence, uncertainty, override = config("RESIDUAL", "0.40", "0.20")
    if enabled:
        gdv = D(truth.get("net_sales") if truth else revenue.get("net_sales_before_land_share"), D(truth.get("gross_sales") if truth else revenue.get("gross_sales"), ZERO))
        development_cost = D(truth.get("development_cost") if truth else costs.get("total_escalated_cost"), ZERO)
        include_finance = bool((configuration.get("residual") or {}).get("include_finance_costs", True))
        finance_cost = ZERO
        if include_finance:
            finance_cost = D(finance_metrics.get("total_interest"), ZERO) + D(finance_metrics.get("total_fees"), ZERO)
        value = D(override) if override is not None else gdv / (ONE + target_profit_on_cost) - development_cost - finance_cost
        methods.append(
            _method_row(
                "RESIDUAL",
                value,
                input_weight=weight,
                confidence=confidence,
                uncertainty=uncertainty,
                source="MANUAL_OVERRIDE" if override is not None else "GDV_LESS_COST_AND_TARGET_PROFIT",
                explanation="Residual development value after development costs, finance costs and target developer profit on total cost including land.",
                diagnostics={
                    "gdv": gdv,
                    "development_cost": development_cost,
                    "finance_cost": finance_cost,
                    "financial_truth_version": truth.get("financial_truth_version") if truth else None,
                    "financial_truth_calculation_hash": truth.get("calculation_hash") if truth else None,
                    "target_developer_profit_on_cost": target_profit_on_cost,
                    "formula": "GDV / (1 + target_profit_on_cost) - development_cost - finance_cost",
                },
            )
        )

    enabled, weight, _, uncertainty, _ = config("MARKET_COMPARABLES", "0.20", "0.15")
    if enabled:
        comparable_result = _comparable_method(
            request.get("comparables") or [],
            subject_area=subject_area,
            input_weight=weight,
            default_uncertainty=uncertainty,
        )
        if comparable_result is not None:
            methods.append(comparable_result)

    enabled, weight, confidence, uncertainty, override = config("BID_IMPLIED", "0.05", "0.10")
    if enabled:
        bid_value = D(override) if override is not None else D(metrics.get("government_cash_npv"), ZERO)
        if bid_value > ZERO:
            methods.append(
                _method_row(
                    "BID_IMPLIED",
                    bid_value,
                    input_weight=weight,
                    confidence=confidence,
                    uncertainty=uncertainty,
                    source="MANUAL_OVERRIDE" if override is not None else "APPROVED_PARTNERSHIP_GOVERNMENT_NPV",
                    explanation="Value implied by the selected partnership's present-value government cash consideration.",
                    diagnostics={"government_cash_npv": metrics.get("government_cash_npv")},
                )
            )

    enabled, weight, confidence, uncertainty, override = config("DIRECT_BENCHMARK", "0", "0.15")
    baseline = calculation_output.get("input_snapshot", {}).get("project", {}).get("land_value_baseline")
    baseline = baseline or request.get("direct_benchmark_value")
    if enabled and weight > ZERO and (override is not None or baseline is not None):
        value = D(override if override is not None else baseline, ZERO)
        if value > ZERO:
            methods.append(
                _method_row(
                    "DIRECT_BENCHMARK",
                    value,
                    input_weight=weight,
                    confidence=confidence,
                    uncertainty=uncertainty,
                    source="DIRECT_LAND_BENCHMARK",
                    explanation="Direct subject-land benchmark supplied by the user or project baseline.",
                )
            )

    methods = [row for row in methods if D(row["input_weight"]) > ZERO]
    if not methods:
        raise ValuationError("At least one available valuation method with a positive weight is required.")
    quality_adjusted = bool(configuration.get("quality_adjusted_weights", True))
    denominator = ZERO
    for row in methods:
        effective = D(row["input_weight"]) * (D(row["confidence"]) if quality_adjusted else ONE)
        row["effective_weight_unscaled"] = effective
        denominator += effective
    if denominator <= ZERO:
        raise ValuationError("Valuation method weights and confidence produce a zero total weight.")
    reconciled = ZERO
    low = ZERO
    high = ZERO
    for row in methods:
        normalized = D(row["effective_weight_unscaled"]) / denominator
        row["normalized_weight"] = normalized
        reconciled += D(row["value"]) * normalized
        low += D(row["low_value"]) * normalized
        high += D(row["high_value"]) * normalized

    values = [D(item["value"]) for item in methods]
    dispersion = (max(values) - min(values)) / reconciled if reconciled > ZERO and len(values) > 1 else ZERO
    gates = quality.get("readiness_gates") or {}
    readiness_components = {
        "DATA_QUALITY": D(quality["score"]),
        "LEGAL": D((gates.get("LEGAL") or {}).get("score"), ZERO),
        "PLANNING": D((gates.get("PLANNING") or {}).get("score"), ZERO),
        "MARKET": D((gates.get("MARKET") or {}).get("score"), ZERO),
        "COST": D((gates.get("COST") or {}).get("score"), ZERO),
        "COST_ESTIMATE_MATURITY": D(study_maturity["cost_estimate_class_score"]),
        "DESIGN_MATURITY": D(study_maturity["design_maturity_score"]),
        "MEASUREMENT_BASIS": D(study_maturity["measurement_basis_score"]),
    }
    institutional_readiness = (
        readiness_components["DATA_QUALITY"] * Decimal("0.30")
        + readiness_components["LEGAL"] * Decimal("0.15")
        + readiness_components["PLANNING"] * Decimal("0.10")
        + readiness_components["MARKET"] * Decimal("0.10")
        + readiness_components["COST"] * Decimal("0.10")
        + readiness_components["COST_ESTIMATE_MATURITY"] * Decimal("0.10")
        + readiness_components["DESIGN_MATURITY"] * Decimal("0.10")
        + readiness_components["MEASUREMENT_BASIS"] * Decimal("0.05")
    )
    institutional_grade = (
        institutional_readiness >= institutional_threshold
        and not quality.get("critical_not_verified")
        and len(methods) >= minimum_reconciliation_methods
        and bool(study_maturity["institutional_gate_passed"])
    )
    if institutional_grade:
        readiness_grade = "INSTITUTIONAL_GRADE"
    elif institutional_readiness >= feasibility_threshold and not quality.get("critical_missing"):
        readiness_grade = "FEASIBILITY_GRADE"
    elif institutional_readiness >= preliminary_threshold:
        readiness_grade = "PRELIMINARY_GRADE"
    else:
        readiness_grade = "SCREENING_ONLY"

    warnings: list[dict[str, str]] = []
    if len(methods) < minimum_reconciliation_methods:
        warnings.append({"code": "INSUFFICIENT_RECONCILIATION_METHODS", "message": f"Only {len(methods)} valuation method(s) were available; policy requires at least {minimum_reconciliation_methods}."})
    if quality.get("critical_missing"):
        warnings.append({"code": "CRITICAL_EVIDENCE_MISSING", "message": "Critical evidence is missing: " + ", ".join(quality["critical_missing"])})
    if dispersion > Decimal("0.30"):
        warnings.append({"code": "HIGH_METHOD_DISPERSION", "message": "Valuation method indications differ by more than 30% of the reconciled value."})
    if D(quality["score"]) < feasibility_threshold:
        warnings.append({"code": "LOW_DATA_QUALITY", "message": "Data quality is below the policy feasibility-grade threshold."})
    if not study_maturity["institutional_gate_passed"]:
        warnings.append({
            "code": "STUDY_MATURITY_BELOW_INSTITUTIONAL_GATE",
            "message": "Cost-estimate class and/or design maturity remain below the institutional-grade gate.",
        })

    output = {
        "valuation_model_version": VALUATION_MODEL_VERSION,
        "status": "SUCCESS_WITH_WARNINGS" if warnings else "SUCCESS",
        "basis_of_value": request.get("basis_of_value") or "MARKET_VALUE",
        "purpose": request.get("purpose") or "DEVELOPMENT_DECISION_SUPPORT",
        "valuation_date": valuation_date,
        "reporting_currency": calculation_output.get("reporting_currency"),
        "subject": {
            "project_id": calculation_output.get("project_id"),
            "project_name": calculation_output.get("project_name"),
            "gross_land_area_sqm": subject_area,
        },
        "methods": methods,
        "reconciliation": {
            "reconciled_value": reconciled,
            "low_value": low,
            "high_value": high,
            "value_per_gross_land_sqm": reconciled / subject_area if subject_area > ZERO else None,
            "method_dispersion": dispersion,
            "quality_adjusted_weights": quality_adjusted,
            "method_count": len(methods),
            "weight_sum": sum((D(row["normalized_weight"]) for row in methods), ZERO),
        },
        "data_quality": quality,
        "valuation_context": project_context,
        "study_maturity": study_maturity,
        "governance_thresholds": {
            "institutional_readiness": institutional_threshold,
            "feasibility_readiness": feasibility_threshold,
            "preliminary_readiness": preliminary_threshold,
            "minimum_reconciliation_methods": minimum_reconciliation_methods,
        },
        "institutional_readiness": {
            "score": institutional_readiness,
            "grade": readiness_grade,
            "institutional_gate_passed": institutional_grade,
            "components": readiness_components,
        },
        "warnings": warnings,
        "limitations": [
            "The result is a decision-support valuation indication and does not replace a signed valuation by a qualified valuer.",
            "Method weights, comparables and evidence statuses must be reviewed for the applicable jurisdiction and purpose.",
            "Tax, foreign-exchange and jurisdiction-specific legal effects are outside valuation model version 0.1.0 unless explicitly embedded in the feasibility calculation.",
        ],
    }
    output["output_hash"] = _hash(output)
    return J(output)

def calculate_valuation(
    *,
    calculation_output: dict[str, Any],
    policy_snapshot: dict[str, Any],
    request: dict[str, Any],
    evidence: list[dict[str, Any]],
    assumptions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate a valuation under an isolated high-precision decimal context.

    The feasibility and finance kernels deliberately retain their own decimal
    behavior.  Valuation precision must therefore never mutate the process-wide
    decimal context.
    """

    with localcontext() as context:
        context.prec = 50
        return _calculate_valuation_impl(
            calculation_output=calculation_output,
            policy_snapshot=policy_snapshot,
            request=request,
            evidence=evidence,
            assumptions=assumptions,
        )

