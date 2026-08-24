"""Deterministic risk primitives used by release 0.7.

This module contains no database or HTTP dependencies.  It deliberately keeps
risk scoring transparent: every score can be reconstructed from probability,
impact and mitigation effectiveness.  Financial sensitivity execution remains
in the application service because it calls the frozen feasibility kernel.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import exp, log, sqrt
import random
from typing import Any, Iterable

RISK_MODEL_VERSION = "0.2.0"


def D(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


def _level(score_25: Decimal) -> str:
    if score_25 >= 20:
        return "CRITICAL"
    if score_25 >= 12:
        return "HIGH"
    if score_25 >= 6:
        return "MEDIUM"
    return "LOW"


def assess_risk_register(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Score a risk register using a transparent 5x5 matrix.

    Residual probability and impact are reduced separately so that mitigation
    cannot erase a risk.  The aggregate score is a weighted mean on a 0-100
    scale, where higher inherent risks carry more weight.
    """

    rows: list[dict[str, Any]] = []
    weighted_inherent = Decimal("0")
    weighted_residual = Decimal("0")
    total_weight = Decimal("0")
    mitigation_complete = 0
    contract_required = 0
    contract_covered = 0

    for index, raw in enumerate(items):
        probability = D(raw.get("probability"))
        impact = D(raw.get("impact"))
        if probability < 1 or probability > 5 or impact < 1 or impact > 5:
            raise ValueError("Risk probability and impact must be between 1 and 5.")
        effectiveness = clamp(D(raw.get("mitigation_effectiveness")), Decimal("0"), Decimal("1"))
        inherent = probability * impact
        residual_probability = max(Decimal("1"), probability * (Decimal("1") - effectiveness * Decimal("0.60")))
        residual_impact = max(Decimal("1"), impact * (Decimal("1") - effectiveness * Decimal("0.40")))
        residual = residual_probability * residual_impact
        weight = inherent
        weighted_inherent += inherent * weight
        weighted_residual += residual * weight
        total_weight += weight
        mitigation_text = str(raw.get("mitigation") or "").strip()
        if mitigation_text:
            mitigation_complete += 1
        clause_required = bool(raw.get("contract_clause_required"))
        if clause_required:
            contract_required += 1
            if str(raw.get("contract_clause_reference") or "").strip() or mitigation_text:
                contract_covered += 1
        rows.append(
            {
                "risk_id": str(raw.get("risk_id") or f"R-{index + 1}"),
                "title": str(raw.get("title") or "Untitled risk"),
                "category": str(raw.get("category") or "OTHER"),
                "risk_type": str(raw.get("risk_type") or "PROJECT"),
                "probability": format(probability, "f"),
                "impact": format(impact, "f"),
                "inherent_score": format(inherent, "f"),
                "inherent_level": _level(inherent),
                "mitigation_effectiveness": format(effectiveness, "f"),
                "residual_probability": format(residual_probability.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
                "residual_impact": format(residual_impact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
                "residual_score": format(residual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
                "residual_level": _level(residual),
                "owner": str(raw.get("owner") or "Unassigned"),
                "allocation": str(raw.get("allocation") or "SHARED"),
                "mitigation": mitigation_text,
                "contract_clause_required": clause_required,
                "contract_clause_reference": str(raw.get("contract_clause_reference") or ""),
                "financial_driver": str(raw.get("financial_driver") or "NONE"),
            }
        )

    count = len(rows)
    if not count:
        return {
            "risk_model_version": RISK_MODEL_VERSION,
            "score": "0",
            "inherent_score": "0",
            "grade": "NOT_ASSESSED",
            "items": [],
            "counts": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0},
            "mitigation_coverage": "0",
            "contract_coverage": "0",
        }
    inherent_normalized = (weighted_inherent / total_weight) / Decimal("25") * Decimal("100")
    residual_normalized = (weighted_residual / total_weight) / Decimal("25") * Decimal("100")
    counts = {level: sum(1 for row in rows if row["residual_level"] == level) for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    grade = "LOW" if residual_normalized < 25 else "MODERATE" if residual_normalized < 50 else "HIGH" if residual_normalized < 75 else "CRITICAL"
    return {
        "risk_model_version": RISK_MODEL_VERSION,
        "score": format(residual_normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
        "inherent_score": format(inherent_normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f"),
        "grade": grade,
        "items": sorted(rows, key=lambda row: D(row["residual_score"]), reverse=True),
        "counts": counts,
        "mitigation_coverage": format((Decimal(mitigation_complete) / Decimal(count)).quantize(Decimal("0.0001")), "f"),
        "contract_coverage": format((Decimal(contract_covered) / Decimal(contract_required)).quantize(Decimal("0.0001")), "f") if contract_required else "1",
    }


def _shift_iso_date(value: str, months: int) -> str:
    source = date.fromisoformat(value)
    total = source.year * 12 + (source.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    # preserve day where possible; project curves normally use first day
    days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(source.day, days[month - 1])).isoformat()


def _shift_curve_points(points: list[dict[str, Any]], months: int, *, anchor_shifted: bool) -> None:
    """Shift absolute-date or relative-month curves without double shifting.

    A curve point may be anchored by an ISO ``date`` or be relative to a
    product/cost start month through ``month``.  When the parent start month
    was already shifted, relative points must remain unchanged.
    """

    if not months:
        return
    for point in points or []:
        if point.get("date") not in (None, ""):
            point["date"] = _shift_iso_date(str(point["date"]), months)
        elif not anchor_shifted and point.get("month") not in (None, ""):
            point["month"] = int(D(point.get("month"))) + months


def apply_project_shocks(project: dict[str, Any], shocks: dict[str, Any]) -> dict[str, Any]:
    """Return a shocked copy without mutating the project snapshot."""

    result = deepcopy(project)
    price_change = D(shocks.get("price_change"))
    cost_change = D(shocks.get("cost_change"))
    far_change = D(shocks.get("far_change"))
    efficiency_change = D(shocks.get("efficiency_change"))
    share_change = D(shocks.get("share_change"))
    interest_change = D(shocks.get("interest_change"))
    sales_delay = int(D(shocks.get("sales_delay_months")))
    construction_delay = int(D(shocks.get("construction_delay_months")))

    applied: dict[str, Any] = {}
    for product in result.get("products") or []:
        product["unit_price"] = format(max(Decimal("0"), D(product.get("unit_price")) * (Decimal("1") + price_change)), "f")
        # Product-mode construction is generated from this field, not from the
        # legacy cost rows.  Earlier scenario runs changed only ``costs[*]`` and
        # therefore a cost-overrun scenario had no effect in product mode.
        if "construction_cost_per_sqm" in product:
            product["construction_cost_per_sqm"] = format(
                max(Decimal("0"), D(product.get("construction_cost_per_sqm")) * (Decimal("1") + cost_change)),
                "f",
            )
        if sales_delay:
            sales_anchor_shifted = product.get("sales_start_month") not in (None, "")
            if sales_anchor_shifted:
                product["sales_start_month"] = int(product.get("sales_start_month")) + sales_delay
            _shift_curve_points(product.get("sales_curve") or [], sales_delay, anchor_shifted=sales_anchor_shifted)
        if construction_delay:
            construction_anchor_shifted = product.get("construction_start_month") not in (None, "")
            if construction_anchor_shifted:
                product["construction_start_month"] = int(product.get("construction_start_month")) + construction_delay
            _shift_curve_points(product.get("construction_curve") or [], construction_delay, anchor_shifted=construction_anchor_shifted)
            # A delivery/construction delay must not improve equity returns merely
            # by moving costs later while leaving handover collections unchanged.
            # Shift only collection rules explicitly tied to completion; for
            # legacy products the final positive-lag instalment is the handover
            # rule by convention.
            rules = product.get("collection_rules") or []
            for index, rule in enumerate(rules):
                raw_flag = rule.get("depends_on_completion")
                if raw_flag is None:
                    existing_lag = D(rule.get("lag_months"), "0") if rule.get("lag_months") not in (None, "") else D(rule.get("lag_days"), "0")
                    depends = index == len(rules) - 1 and existing_lag > 0
                else:
                    depends = str(raw_flag).strip().lower() in {"1", "true", "yes", "on"}
                if not depends:
                    continue
                if rule.get("lag_months") not in (None, ""):
                    rule["lag_months"] = int(D(rule.get("lag_months"))) + construction_delay
                else:
                    rule["lag_days"] = format(D(rule.get("lag_days"), "0") + Decimal(construction_delay) * Decimal("30.4375"), "f")
    for item in result.get("costs") or []:
        category = str(item.get("category") or "").upper()
        if cost_change:
            item["unit_cost"] = format(max(Decimal("0"), D(item.get("unit_cost")) * (Decimal("1") + cost_change)), "f")
            if item.get("fixed_amount") not in (None, ""):
                item["fixed_amount"] = format(max(Decimal("0"), D(item.get("fixed_amount")) * (Decimal("1") + cost_change)), "f")
        # Marketing entered as a percentage of revenue is materialized as a
        # fixed governed cost row.  Keep it proportional when prices are shocked.
        if price_change and category == "SALES_MARKETING" and item.get("fixed_amount") not in (None, ""):
            item["fixed_amount"] = format(max(Decimal("0"), D(item.get("fixed_amount")) * (Decimal("1") + price_change)), "f")
            item["unit_cost"] = item["fixed_amount"]
        if construction_delay:
            cost_anchor_shifted = item.get("monthly_start_month") not in (None, "")
            if cost_anchor_shifted:
                item["monthly_start_month"] = int(item.get("monthly_start_month")) + construction_delay
            _shift_curve_points(item.get("expenditure_curve") or [], construction_delay, anchor_shifted=cost_anchor_shifted)
    if price_change:
        applied["price_change"] = format(price_change, "f")
    if cost_change:
        applied["cost_change"] = format(cost_change, "f")
    if sales_delay:
        applied["sales_delay_months"] = sales_delay
    if construction_delay:
        applied["construction_delay_months"] = construction_delay
        applied["handover_collection_delay_months"] = construction_delay
    planning = result.get("planning") or {}
    if far_change:
        planning["far"] = format(max(Decimal("0.01"), D(planning.get("far")) * (Decimal("1") + far_change)), "f")
    if efficiency_change:
        for product in result.get("planning_products") or []:
            product["efficiency"] = format(clamp(D(product.get("efficiency")) * (Decimal("1") + efficiency_change), Decimal("0.01"), Decimal("1")), "f")
    partnership = result.get("partnership") or {}
    if share_change:
        current = D(partnership.get("manual_share") or partnership.get("share_rate"))
        new_share = clamp(current + share_change, Decimal("0"), Decimal("1"))
        partnership["share_rate"] = format(new_share, "f")
        partnership["manual_share"] = format(new_share, "f")
        partnership["approved_selection"] = "MANUAL"
    finance = result.get("finance_model") or {}
    if interest_change:
        finance["annual_interest_rate"] = format(max(Decimal("0"), D(finance.get("annual_interest_rate")) + interest_change), "f")
        applied["interest_change"] = format(interest_change, "f")
    result["scenario_shock_application"] = {
        "model_version": RISK_MODEL_VERSION,
        "requested": deepcopy(shocks),
        "applied": applied,
    }
    return result


def percentile(values: Iterable[Any], percentile_value: Decimal | float | int) -> Decimal | None:
    ordered = sorted(D(value) for value in values if value is not None)
    if not ordered:
        return None
    p = clamp(D(percentile_value), Decimal("0"), Decimal("100")) / Decimal("100")
    if len(ordered) == 1:
        return ordered[0]
    rank = p * Decimal(len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def sample_distribution(config: dict[str, Any], rng: random.Random) -> Decimal:
    kind = str(config.get("type") or "TRIANGULAR").upper()
    low = D(config.get("low"))
    high = D(config.get("high"))
    if high < low:
        raise ValueError("Distribution high must not be below low.")
    if kind == "UNIFORM":
        return low + (high - low) * D(rng.random())
    if kind == "NORMAL":
        mean = D(config.get("mean"), format((low + high) / 2, "f"))
        stddev = max(D(config.get("stddev"), "0"), Decimal("0"))
        sampled = D(rng.gauss(float(mean), float(stddev)))
        return clamp(sampled, low, high)
    if kind == "LOGNORMAL":
        mean = float(D(config.get("mean"), "0"))
        sigma = max(float(D(config.get("sigma"), "0.1")), 0.0)
        sampled = Decimal(str(exp(rng.gauss(mean, sigma))))
        return clamp(sampled, low, high)
    mode = clamp(D(config.get("mode")), low, high)
    return D(rng.triangular(float(low), float(high), float(mode)))
