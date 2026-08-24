"""Deterministic constrained solver for fair public-land consideration."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import product
from typing import Any, Callable, Iterable

from .hashing import sha256_json

ZERO = Decimal("0")


def D(value: Any, default: str = "0") -> Decimal:
    try:
        result = Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid solver number: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("Non-finite solver values are not permitted.")
    return result


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(+value, "f")


@dataclass(frozen=True)
class SolverConfig:
    lower: Decimal = ZERO
    upper: Decimal = Decimal("1")
    tolerance: Decimal = Decimal("0.000001")
    maximum_iterations: int = 80
    grid_points: int = 101
    deterministic_seed: int = 360


def _candidate(value: Decimal, result: dict[str, Any]) -> dict[str, Any]:
    return {"value": _fmt(value), "feasible": bool(result.get("feasible")), "objective": _fmt(D(result.get("objective"))), "metrics": result.get("metrics") or {}, "violations": result.get("violations") or []}


def solve_single_variable(
    evaluate: Callable[[Decimal], dict[str, Any]],
    *,
    config: SolverConfig | None = None,
    monotonic: bool = True,
) -> dict[str, Any]:
    cfg = config or SolverConfig()
    if cfg.upper <= cfg.lower:
        raise ValueError("Solver upper bound must exceed lower bound.")
    evaluated: dict[Decimal, dict[str, Any]] = {}

    def run(value: Decimal) -> dict[str, Any]:
        value = min(cfg.upper, max(cfg.lower, value))
        if value not in evaluated:
            evaluated[value] = evaluate(value)
        return evaluated[value]

    if monotonic:
        low, high = cfg.lower, cfg.upper
        low_result, high_result = run(low), run(high)
        if not low_result.get("feasible"):
            return {"status": "INFEASIBLE", "reason": "Lower bound is infeasible; no monotonic feasible interval exists.", "candidates": [_candidate(low, low_result)], "solver_hash": sha256_json(evaluated)}
        if high_result.get("feasible"):
            selected = high
            status = "BOUND_REACHED"
        else:
            selected = low
            status = "SOLVED"
            for _ in range(cfg.maximum_iterations):
                mid = (low + high) / Decimal("2")
                result = run(mid)
                if result.get("feasible"):
                    low = mid
                    selected = mid
                else:
                    high = mid
                if abs(high - low) <= cfg.tolerance:
                    break
        ordered = sorted(evaluated.items())
        return {
            "status": status,
            "algorithm": "BRACKETING_BISECTION",
            "selected": _candidate(selected, run(selected)),
            "bounds": {"lower": _fmt(cfg.lower), "upper": _fmt(cfg.upper), "tolerance": _fmt(cfg.tolerance)},
            "iterations": len(evaluated),
            "candidates": [_candidate(value, result) for value, result in ordered[-20:]],
            "solver_hash": sha256_json({"selected": selected, "evaluated": evaluated}),
        }

    step = (cfg.upper - cfg.lower) / Decimal(max(1, cfg.grid_points - 1))
    values = [cfg.lower + step * index for index in range(cfg.grid_points)]
    rows = [(value, run(value)) for value in values]
    feasible = [(value, result) for value, result in rows if result.get("feasible")]
    if not feasible:
        return {"status": "INFEASIBLE", "algorithm": "GRID_SEARCH_REFINEMENT", "candidates": [_candidate(value, result) for value, result in rows], "solver_hash": sha256_json(rows)}
    local: list[tuple[Decimal, dict[str, Any]]] = []
    for index, item in enumerate(feasible):
        value, result = item
        objective = D(result.get("objective"))
        left = D(feasible[index - 1][1].get("objective")) if index else None
        right = D(feasible[index + 1][1].get("objective")) if index + 1 < len(feasible) else None
        if (left is None or objective >= left) and (right is None or objective >= right):
            local.append(item)
    selected, selected_result = max(feasible, key=lambda item: D(item[1].get("objective")))
    return {
        "status": "MULTIPLE_EQUIVALENT_SOLUTIONS" if sum(1 for _, result in feasible if abs(D(result.get("objective")) - D(selected_result.get("objective"))) <= cfg.tolerance) > 1 else "SOLVED",
        "algorithm": "GRID_SEARCH_REFINEMENT",
        "selected": _candidate(selected, selected_result),
        "local_optima": [_candidate(value, result) for value, result in local],
        "candidates": [_candidate(value, result) for value, result in rows],
        "solver_hash": sha256_json(rows),
    }


def _dominates(left: dict[str, Decimal], right: dict[str, Decimal], maximize: set[str], minimize: set[str]) -> bool:
    no_worse = all(left[key] >= right[key] for key in maximize) and all(left[key] <= right[key] for key in minimize)
    strictly = any(left[key] > right[key] for key in maximize) or any(left[key] < right[key] for key in minimize)
    return no_worse and strictly


def solve_multi_variable(
    evaluate: Callable[[dict[str, Decimal]], dict[str, Any]],
    bounds: dict[str, tuple[Any, Any, int]],
    *,
    maximize: Iterable[str] = ("public_npv", "downside_resilience", "risk_transfer"),
    minimize: Iterable[str] = ("funding_requirement", "audit_complexity"),
    maximum_candidates: int = 10000,
) -> dict[str, Any]:
    grids: dict[str, list[Decimal]] = {}
    for name, (low_raw, high_raw, count) in bounds.items():
        low, high = D(low_raw), D(high_raw)
        if high < low or count < 2:
            raise ValueError(f"Invalid bounds for {name}.")
        step = (high - low) / Decimal(count - 1)
        grids[name] = [low + step * index for index in range(count)]
    names = sorted(grids)
    combinations = 1
    for name in names:
        combinations *= len(grids[name])
    if combinations > maximum_candidates:
        raise ValueError(f"Solver grid has {combinations} candidates; maximum is {maximum_candidates}.")
    candidates: list[dict[str, Any]] = []
    for values in product(*(grids[name] for name in names)):
        variables = dict(zip(names, values))
        result = evaluate(variables)
        metrics = {key: D(value) for key, value in (result.get("metrics") or {}).items()}
        candidates.append({"variables": {key: _fmt(value) for key, value in variables.items()}, "feasible": bool(result.get("feasible")), "metrics": {key: _fmt(value) for key, value in metrics.items()}, "violations": result.get("violations") or []})
    feasible = [row for row in candidates if row["feasible"]]
    max_keys, min_keys = set(maximize), set(minimize)
    frontier: list[dict[str, Any]] = []
    for row in feasible:
        metrics = {key: D(value) for key, value in row["metrics"].items()}
        if not any(_dominates({key: D(other["metrics"].get(key)) for key in max_keys | min_keys}, metrics, max_keys, min_keys) for other in feasible if other is not row):
            frontier.append(row)
    return {
        "status": "INFEASIBLE" if not feasible else "SOLVED",
        "algorithm": "DETERMINISTIC_EPSILON_CONSTRAINT_GRID",
        "evaluated_candidates": len(candidates),
        "feasible_candidates": len(feasible),
        "pareto_frontier": frontier,
        "all_candidates": candidates,
        "solver_hash": sha256_json(candidates),
    }
