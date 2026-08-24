"""Risk, sensitivity, fair-share preview and tender orchestration."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import random
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from landvalue360_risk import RISK_MODEL_VERSION, apply_project_shocks, assess_risk_register, percentile, sample_distribution

from ..audit import record_audit
from ..context import AuthContext
from ..errors import ConflictError, NotFoundError
from ..json_tools import json_merge_patch, sha256_json
from ..unified_engine import run_unified_financial_engine
from ..models import AnalysisRun, ValuationRun, utc_now
from .calculations import compose_calculation_envelope, execute_calculation_envelope, _compose_governed_policy
from .policies import require_operational_policy
from .tenant import get_policy_version, get_project_version, get_scenario, tenant_clause
from .valuations import preview_data_quality

ANALYSIS_MODEL_VERSION = "2.1.1"


def D(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ConflictError("INVALID_ANALYSIS_NUMBER", f"Invalid numeric value: {value!r}") from exc




def _scenario_for_version(session: Session, context: AuthContext, scenario_id: str | None, project_version_id: str):
    if not scenario_id:
        return None
    scenario = get_scenario(session, context, scenario_id)
    if str(scenario.project_version_id) != str(project_version_id):
        raise ConflictError(
            "SCENARIO_PROJECT_VERSION_MISMATCH",
            "The selected scenario belongs to a different project version and cannot be used for this analysis.",
        )
    return scenario

def _project_snapshot(version, scenario=None) -> dict[str, Any]:
    base = deepcopy(version.input_snapshot)
    result = deepcopy(base)
    if scenario is not None:
        result = json_merge_patch(result, scenario.override_snapshot)
    result["project_id"] = base.get("project_id")
    result["project_name"] = base.get("project_name")
    return result


def _policy_pair(
    session: Session,
    context: AuthContext,
    *,
    project_policy_version_id: str,
    valuation_policy_version_id: str,
):
    project_policy = require_operational_policy(
        get_policy_version(session, context, project_policy_version_id),
        edition="DEVELOPER",
        expected_type="PROJECT",
    )
    valuation_policy = require_operational_policy(
        get_policy_version(session, context, valuation_policy_version_id),
        edition="DEVELOPER",
        expected_type="VALUATION",
    )
    combined = _compose_governed_policy(
        project_policy.policy_snapshot,
        valuation_policy.policy_snapshot,
    )
    return project_policy, valuation_policy, combined


def _calculate(
    project: dict[str, Any],
    project_policy: dict[str, Any],
    valuation_policy: dict[str, Any],
    case_id: str,
    *,
    optimize_share: bool = False,
    selected_only: bool = True,
) -> dict[str, Any]:
    return execute_calculation_envelope(
        compose_calculation_envelope(
            project_snapshot=project,
            policy_snapshot=project_policy,
            valuation_policy_snapshot=valuation_policy,
            case_id=case_id,
            description=f"Platform 2.1.1 / Engine 2.1.1 analytical run: {case_id}",
        ),
        optimize_share=optimize_share,
        include_solver=False,
        unified_selected_only=selected_only,
    )


def _materialize_analysis_share(
    project: dict[str, Any],
    policy: dict[str, Any],
    case_id: str,
    *,
    exact: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Freeze a model-selected share before running repeated analytical cases.

    Fair-share optimization is executed once, then all sensitivity, Monte Carlo
    and tender cases use the resulting approved share as a manual fixed share.
    This prevents the analytical result from being contaminated by re-optimizing
    the government share after every market shock.
    """

    prepared = deepcopy(project)
    partnership = prepared.setdefault("partnership", {})
    selection = str(partnership.get("approved_selection") or "MANUAL")
    if selection == "MANUAL" or str(partnership.get("method") or "") in {"UPFRONT"}:
        return prepared, None
    if not exact:
        proxy = partnership.get("manual_share", partnership.get("share_rate", "0"))
        partnership["approved_selection"] = "MANUAL"
        partnership["manual_share"] = str(proxy)
        partnership["share_rate"] = str(proxy)
        return prepared, {"source_selection": selection, "source": "CURRENT_SHARE_PROXY", "approved_share": str(proxy)}
    landowner = run_unified_financial_engine(prepared, policy)
    selected_contract = landowner.get("selected_contract") or {}
    share = selected_contract.get("measure")
    if share in (None, ""):
        raise ConflictError("ANALYSIS_SHARE_NOT_AVAILABLE", "The selected model consideration could not be resolved by the unified monthly fair-share engine.")
    partnership["approved_selection"] = "MANUAL"
    if str(partnership.get("method") or "").upper() == "UPFRONT":
        partnership["manual_amount"] = str(share)
    else:
        partnership["manual_share"] = str(share)
        partnership["share_rate"] = str(share)
    method = str(partnership.get("method") or "GROSS_SALES").upper()
    comparison = next((row for row in landowner.get("contract_comparison") or [] if row.get("method") == method), None)
    return prepared, {
        "source_selection": selection,
        "source": "UNIFIED_MONTHLY_MODEL_RESOLVED",
        "approved_share": str(share),
        "fair_share": comparison,
        "calculation_hash": landowner.get("calculation_hash"),
    }


def metric_summary(output: dict[str, Any]) -> dict[str, Any]:
    """Return analytical metrics exclusively from Financial Truth."""

    truth = output.get("financial_truth") or ((output.get("unified_financial_result") or {}).get("financial_truth") or {})
    if not truth:
        raise ConflictError(
            "FINANCIAL_TRUTH_REQUIRED",
            "Analytical runs require a Unified Monthly Engine Financial Truth result.",
        )
    unified = output.get("unified_financial_result") or {}
    selected = unified.get("selected_contract") or {}
    failed = list(truth.get("failed_constraints") or []) + list(
        (unified.get("engine_invariants") or {}).get("failed_invariant_ids") or []
    )
    return {
        "status": "SUCCESS" if truth.get("calculation_status") == "PASS" else "SUCCESS_WITH_WARNINGS",
        "feasible": bool(truth.get("feasible")) and not failed,
        "approved_share": truth.get("approved_share"),
        "project_npv": truth.get("project_npv"),
        "project_irr": truth.get("project_irr"),
        "developer_npv": truth.get("developer_npv"),
        "developer_irr": truth.get("developer_irr"),
        "developer_profit": truth.get("developer_profit"),
        "developer_profit_on_cost": truth.get("developer_profit_on_cost"),
        "developer_multiple": truth.get("developer_multiple"),
        "peak_funding": truth.get("peak_equity"),
        "funding_gap": truth.get("funding_gap"),
        "terminal_debt": truth.get("terminal_debt"),
        "deferred_development_cost": truth.get("deferred_development_cost"),
        "deferred_contractual_payment": truth.get("deferred_contractual_payment"),
        "execution_completion_ratio": "1" if truth.get("feasible") else "0",
        "schedule_extension_months": truth.get("schedule_extension_months"),
        "original_completion_date": truth.get("original_completion_date"),
        "adjusted_completion_date": truth.get("adjusted_completion_date"),
        "finance_mode": truth.get("finance_mode"),
        "spend_policy": truth.get("spend_policy"),
        "equity_irr": truth.get("developer_equity_irr"),
        "equity_npv": truth.get("developer_equity_npv"),
        "equity_multiple": truth.get("developer_multiple"),
        "dscr": (output.get("finance_analysis") or {}).get("metrics", {}).get("aggregate_dscr"),
        "ltc": (output.get("finance_analysis") or {}).get("metrics", {}).get("loan_to_cost"),
        "government_cash": truth.get("government_consideration"),
        "government_npv": truth.get("government_npv"),
        "government_multiple": selected.get("government_multiple_on_land_value"),
        "failed_constraints": failed,
        "engine_version": (output.get("engine_manifest") or {}).get("engine_version") or unified.get("engine_version"),
        "calculation_hash": truth.get("calculation_hash"),
    }


def _metric_value(summary: dict[str, Any], metric: str) -> Decimal | None:
    value = summary.get(metric)
    if value in (None, ""):
        return None
    return D(value)


def _persist_run(
    session: Session,
    *,
    context: AuthContext,
    version,
    policy,
    valuation_policy,
    scenario,
    analysis_type: str,
    input_snapshot: dict[str, Any],
    output_snapshot: dict[str, Any],
    status: str = "SUCCESS",
) -> AnalysisRun:
    run = AnalysisRun(
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
        project_id=version.project_id,
        project_version_id=version.id,
        policy_pack_version_id=policy.id,
        valuation_policy_pack_version_id=valuation_policy.id,
        scenario_id=scenario.id if scenario else None,
        analysis_type=analysis_type,
        status=status,
        analysis_model_version=ANALYSIS_MODEL_VERSION,
        input_snapshot=deepcopy(input_snapshot),
        input_hash=sha256_json(input_snapshot),
        output_snapshot=deepcopy(output_snapshot),
        output_hash=sha256_json(output_snapshot),
        created_by_user_id=context.user_id,
        completed_at=utc_now(),
    )
    session.add(run)
    session.flush()
    record_audit(
        session,
        context=context,
        action=f"{analysis_type}_RUN_CREATED",
        entity_type="AnalysisRun",
        entity_id=run.id,
        after={
            "analysis_type": analysis_type,
            "status": status,
            "project_policy_version_id": policy.id,
            "valuation_policy_version_id": valuation_policy.id,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
        },
    )
    return run


def get_analysis_run(session: Session, *, context: AuthContext, run_id: str) -> AnalysisRun:
    run = session.scalar(select(AnalysisRun).where(AnalysisRun.id == run_id, *tenant_clause(AnalysisRun, context)))
    if run is None:
        raise NotFoundError("Analysis run not found.")
    return run


def list_analysis_runs(
    session: Session,
    *,
    context: AuthContext,
    project_id: str | None = None,
    analysis_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AnalysisRun]:
    statement = select(AnalysisRun).where(*tenant_clause(AnalysisRun, context))
    if project_id:
        statement = statement.where(AnalysisRun.project_id == project_id)
    if analysis_type:
        statement = statement.where(AnalysisRun.analysis_type == analysis_type)
    return list(session.scalars(statement.order_by(AnalysisRun.created_at.desc()).offset(offset).limit(limit)).all())


def _live_share_option(
    project_snapshot: dict[str, Any],
    project_policy_snapshot: dict[str, Any],
    valuation_policy_snapshot: dict[str, Any],
    *,
    selection: str,
    share: Any,
    case_id: str,
) -> dict[str, Any]:
    candidate = deepcopy(project_snapshot)
    partnership = candidate.setdefault("partnership", {})
    partnership["approved_selection"] = "MANUAL"
    partnership["manual_share"] = str(share)
    partnership["share_rate"] = str(share)
    output = _calculate(
        candidate,
        deepcopy(project_policy_snapshot),
        deepcopy(valuation_policy_snapshot),
        case_id,
        optimize_share=False,
    )
    summary = metric_summary(output)
    return {
        "selection": selection,
        "share": str(share),
        "summary": summary,
        "status": output.get("status"),
        "constraints": (output.get("approved_case") or {}).get("constraints") or [],
        "finance_constraints": (output.get("finance_analysis") or {}).get("constraints") or [],
    }


def _share_recommendation(
    *,
    fair_share: dict[str, Any] | None,
    policy_snapshot: dict[str, Any],
    selected: str,
) -> dict[str, Any] | None:
    if not fair_share:
        return None
    financial = policy_snapshot.get("financial_constraints") or {}
    reasons = [
        "The minimum protects the approved public-land value floor and the institutional policy floor.",
        "The balanced share targets the approved developer return while remaining above the public minimum.",
        "The technical ceiling is the last feasible share before a mandatory return, liquidity, debt or completion constraint fails.",
    ]
    reasons_ar = [
        "الحد الأدنى يحمي قيمة الأرض العامة المعتمدة والحد الأدنى المحدد في السياسة المؤسسية.",
        "النسبة المتوازنة تستهدف عائد المطور المعتمد مع بقائها فوق الحد الأدنى للجهة العامة.",
        "السقف الفني هو آخر نسبة قابلة للتطبيق قبل فشل قيد إلزامي متعلق بالعائد أو السيولة أو الدين أو اكتمال التنفيذ.",
    ]
    governing = fair_share.get("governing_constraint_id")
    if governing:
        reasons.append(f"The ceiling is governed by constraint {governing}.")
        reasons_ar.append(f"القيد الحاكم للسقف الفني هو {governing}.")
    target_irr = financial.get("target_developer_irr")
    if target_irr not in (None, ""):
        reasons.append(f"The balanced recommendation is tested against a target developer IRR of {target_irr}.")
        reasons_ar.append(f"تم اختبار التوصية المتوازنة مقابل عائد داخلي مستهدف للمطور قدره {target_irr}.")
    if not fair_share.get("monotonic", True):
        reasons.append("The share-response curve is not fully monotonic; the engine conservatively uses the first contiguous feasible range.")
        reasons_ar.append("منحنى استجابة النسبة غير رتيب بالكامل؛ لذلك يستخدم المحرك بصورة تحفظية أول نطاق جدوى متصل.")
    return {
        "recommended_selection": "MODEL_BALANCED",
        "current_selection": selected,
        "recommended_share": fair_share.get("balanced_share"),
        "minimum_share": fair_share.get("minimum_share"),
        "technical_ceiling": fair_share.get("technical_ceiling"),
        "status": fair_share.get("status"),
        "governing_constraint_id": governing,
        "reasons": reasons,
        "reasons_ar": reasons_ar,
        "explanation": fair_share.get("explanation"),
    }



def _landowner_case_summary(case: dict[str, Any] | None, *, share: Any) -> dict[str, Any]:
    case = case or {}
    return {
        "feasible": bool(case.get("feasible")),
        "approved_share": None if share in (None, "") else str(share),
        "project_npv": case.get("project_npv"),
        "project_irr": case.get("project_irr"),
        "developer_npv": case.get("developer_npv"),
        "developer_irr": case.get("developer_irr"),
        "developer_profit": case.get("developer_profit"),
        "developer_profit_on_cost": case.get("developer_profit_on_cost"),
        "developer_multiple": case.get("developer_multiple"),
        "peak_funding": case.get("peak_equity"),
        "funding_gap": case.get("peak_funding_gap"),
        "terminal_debt": case.get("terminal_debt"),
        "deferred_development_cost": case.get("terminal_deferred_cost"),
        "deferred_contractual_payment": case.get("terminal_contractual_arrears"),
        "schedule_extension_months": case.get("schedule_extension_months"),
        "original_completion_date": case.get("original_completion_date"),
        "adjusted_completion_date": case.get("adjusted_completion_date"),
        "finance_mode": case.get("finance_mode"),
        "spend_policy": case.get("spend_policy"),
        "equity_irr": case.get("developer_equity_irr"),
        "equity_npv": case.get("developer_npv"),
        "equity_multiple": case.get("developer_multiple"),
        "government_cash": case.get("government_value"),
        "government_npv": case.get("government_npv"),
        "failed_constraints": [row.get("constraint_id") for row in case.get("constraints") or [] if not row.get("passed")],
    }


def _landowner_live_summary(landowner: dict[str, Any]) -> dict[str, Any]:
    """Map the exact unified monthly engine financial truth to the historical summary contract."""

    truth = landowner.get("financial_truth") or {}
    invariants = landowner.get("engine_invariants") or {}
    return {
        "status": "SUCCESS" if truth.get("status") == "PASS" else "SUCCESS_WITH_WARNINGS",
        "feasible": bool(truth.get("feasible")) and bool(invariants.get("passed")),
        "approved_share": truth.get("approved_share"),
        "project_npv": truth.get("project_npv"),
        "project_irr": truth.get("project_irr"),
        "developer_npv": truth.get("developer_npv"),
        "developer_irr": truth.get("developer_irr"),
        "developer_profit": truth.get("developer_profit"),
        "developer_profit_on_cost": truth.get("developer_profit_on_cost"),
        "developer_multiple": truth.get("developer_multiple"),
        "peak_funding": truth.get("peak_equity"),
        "funding_gap": truth.get("funding_gap"),
        "terminal_debt": truth.get("terminal_debt"),
        "deferred_development_cost": truth.get("deferred_development_cost"),
        "deferred_contractual_payment": truth.get("deferred_contractual_payment"),
        "mandatory_shortfall": truth.get("mandatory_shortfall"),
        "unmodeled_scope": truth.get("unmodeled_scope"),
        "terminal_unpaid_obligations": truth.get("terminal_unpaid_obligations"),
        "execution_completion_ratio": "1" if truth.get("feasible") else "0",
        "schedule_extension_months": truth.get("schedule_extension_months"),
        "original_completion_date": truth.get("original_completion_date"),
        "adjusted_completion_date": truth.get("adjusted_completion_date"),
        "finance_mode": truth.get("finance_mode"),
        "spend_policy": truth.get("spend_policy"),
        "equity_irr": truth.get("developer_equity_irr"),
        "equity_npv": truth.get("developer_equity_npv"),
        "equity_multiple": truth.get("developer_multiple"),
        "dscr": None,
        "ltc": None,
        "government_cash": truth.get("government_consideration"),
        "government_npv": truth.get("government_npv"),
        "government_multiple": None,
        "failed_constraints": list(truth.get("failed_constraints") or []) + list(invariants.get("failed_invariant_ids") or []),
        "calculation_hash": landowner.get("calculation_hash"),
        "ledger_hash": (landowner.get("event_ledger") or {}).get("ledger_hash"),
        "invariant_hash": invariants.get("invariant_hash"),
        "engine_version": landowner.get("engine_version"),
        "single_source_financial_kernel": landowner.get("single_source_financial_kernel"),
    }


def live_preview(
    session: Session,
    *,
    context: AuthContext,
    project_snapshot: dict[str, Any],
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
) -> dict[str, Any]:
    """Return live financial impact from the unified monthly kernel.

    Minimum, balanced, ceiling and the currently approved source are all
    evaluated by the same landowner/finance engine. No proxy share is used.
    """

    policy, valuation_policy, policy_snapshot = _policy_pair(
        session,
        context,
        project_policy_version_id=policy_pack_version_id,
        valuation_policy_version_id=valuation_policy_pack_version_id,
    )
    project = deepcopy(project_snapshot)
    # Keep the frozen feasibility calculation for schema validation and legacy
    # result compatibility, but source the live finance/share values from the
    # unified monthly kernel.
    core_output = _calculate(
        project,
        policy.policy_snapshot,
        valuation_policy.policy_snapshot,
        "LIVE-PREVIEW",
        optimize_share=False,
        selected_only=False,
    )
    landowner = core_output.get("unified_financial_result") or run_unified_financial_engine(project, policy_snapshot, legacy_output=core_output)
    partnership = project.get("partnership") or {}
    selected = str(partnership.get("approved_selection") or "MANUAL")
    method = str(partnership.get("method") or "GROSS_SALES").upper()
    comparison = next((row for row in landowner.get("contract_comparison") or [] if row.get("method") == method), None)

    options: list[dict[str, Any]] = []
    if comparison and comparison.get("fair_floor") is not None:
        for selection, measure_key, case_key in (
            ("MODEL_MINIMUM", "fair_floor", "minimum_case"),
            ("MODEL_BALANCED", "balanced", "balanced_case"),
            ("MODEL_CEILING", "technical_ceiling", "ceiling_case"),
        ):
            share = comparison.get(measure_key)
            if share in (None, ""):
                continue
            case = comparison.get(case_key) or {}
            options.append(
                {
                    "selection": selection,
                    "share": str(share),
                    "summary": _landowner_case_summary(case, share=share),
                    "status": "SUCCESS" if case.get("feasible") else "SUCCESS_WITH_WARNINGS",
                    "constraints": case.get("constraints") or [],
                    "finance_constraints": case.get("constraints") or [],
                }
            )

    fair_share = None
    if comparison:
        fair_share = {
            "status": comparison.get("status"),
            "minimum_share": comparison.get("fair_floor"),
            "balanced_share": comparison.get("balanced"),
            "technical_ceiling": comparison.get("technical_ceiling"),
            "governing_constraint_id": comparison.get("governing_constraint_id"),
            "monotonic": comparison.get("monotonic", True),
            "explanation": (
                "The range is calculated by the unified monthly cash-flow engine and is constrained by public value, developer return, liquidity, completion and terminal debt."
            ),
        }
    selected_contract = landowner.get("selected_contract") or {}
    approved = selected_contract.get("measure")
    return {
        "summary": _landowner_live_summary(landowner),
        "validation_messages": core_output.get("validation_messages") or [],
        "status": "SUCCESS" if (landowner.get("financial_truth") or {}).get("status") == "PASS" else "SUCCESS_WITH_WARNINGS",
        "share_basis": {
            "source_selection": selected,
            "source": "LANDVALUE360_UNIFIED_MONTHLY_ENGINE",
            "approved_share": approved,
            "fair_share": fair_share,
            "calculation_hash": landowner.get("calculation_hash"),
            "ledger_hash": (landowner.get("event_ledger") or {}).get("ledger_hash"),
            "engine_version": landowner.get("engine_version"),
        },
        "share_report": {
            "method": method,
            "selected": selected,
            "approved_share": approved,
            "fair_share": fair_share,
            "options": options,
            "recommendation": _share_recommendation(
                fair_share=fair_share,
                policy_snapshot=policy_snapshot,
                selected=selected,
            ),
        },
    }


def run_risk_assessment(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    items: list[dict[str, Any]],
) -> AnalysisRun:
    version = get_project_version(session, context, project_version_id)
    policy, valuation_policy, _combined_policy = _policy_pair(
        session,
        context,
        project_policy_version_id=policy_pack_version_id,
        valuation_policy_version_id=valuation_policy_pack_version_id,
    )
    scenario = _scenario_for_version(session, context, scenario_id, version.id)
    if scenario and scenario.project_version_id != version.id:
        raise NotFoundError("Scenario does not belong to the selected project version.")
    assessed = assess_risk_register(items)
    risk_policy = policy.policy_snapshot.get("risk_policy") or {}
    score = D(assessed["score"])
    maximum = D(risk_policy.get("maximum_residual_risk_score"), "55")
    max_critical = int(risk_policy.get("maximum_critical_residual_risks", 0))
    max_high = int(risk_policy.get("maximum_high_residual_risks", 3))
    min_coverage = D(risk_policy.get("minimum_mitigation_coverage"), "0.8")
    gates = [
        {"gate_id": "RISK_SCORE", "passed": score <= maximum, "actual": assessed["score"], "operator": "<=", "threshold": format(maximum, "f")},
        {"gate_id": "CRITICAL_RISKS", "passed": assessed["counts"]["CRITICAL"] <= max_critical, "actual": assessed["counts"]["CRITICAL"], "operator": "<=", "threshold": max_critical},
        {"gate_id": "HIGH_RISKS", "passed": assessed["counts"]["HIGH"] <= max_high, "actual": assessed["counts"]["HIGH"], "operator": "<=", "threshold": max_high},
        {"gate_id": "MITIGATION_COVERAGE", "passed": D(assessed["mitigation_coverage"]) >= min_coverage, "actual": assessed["mitigation_coverage"], "operator": ">=", "threshold": format(min_coverage, "f")},
    ]
    assessed["gates"] = gates
    assessed["gate_passed"] = all(item["passed"] for item in gates)
    assessed["project_version_id"] = version.id
    input_snapshot = {
        "items": deepcopy(items),
        "risk_policy": deepcopy(risk_policy),
        "project_policy_version_id": policy.id,
        "valuation_policy_version_id": valuation_policy.id,
    }
    return _persist_run(session, context=context, version=version, policy=policy, valuation_policy=valuation_policy, scenario=scenario, analysis_type="RISK", input_snapshot=input_snapshot, output_snapshot=assessed, status="SUCCESS" if assessed["gate_passed"] else "SUCCESS_WITH_WARNINGS")


def _shift_series(project: dict[str, Any], *, driver: str, shock: Decimal | int) -> dict[str, Any]:
    shocks: dict[str, Any] = {}
    if driver == "SALES_PRICE": shocks["price_change"] = format(D(shock), "f")
    elif driver == "DEVELOPMENT_COST": shocks["cost_change"] = format(D(shock), "f")
    elif driver == "SALES_DELAY": shocks["sales_delay_months"] = int(shock)
    elif driver == "CONSTRUCTION_DELAY": shocks["construction_delay_months"] = int(shock)
    elif driver == "LANDOWNER_SHARE": shocks["share_change"] = format(D(shock), "f")
    elif driver == "INTEREST_RATE": shocks["interest_change"] = format(D(shock), "f")
    elif driver == "FAR": shocks["far_change"] = format(D(shock), "f")
    elif driver == "EFFICIENCY": shocks["efficiency_change"] = format(D(shock), "f")
    else: raise ConflictError("UNKNOWN_SENSITIVITY_DRIVER", f"Unsupported sensitivity driver: {driver}")
    return apply_project_shocks(project, shocks)


def _one_way(
    project: dict[str, Any],
    project_policy: dict[str, Any],
    valuation_policy: dict[str, Any],
    drivers: list[dict[str, Any]],
    target_metric: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    tornado: list[dict[str, Any]] = []
    base_output = _calculate(project, project_policy, valuation_policy, "SENSITIVITY-BASE")
    base_summary = metric_summary(base_output)
    base_value = _metric_value(base_summary, target_metric)
    for driver in drivers:
        values = driver.get("values") or []
        driver_rows = []
        for value in values:
            shocked = _shift_series(project, driver=str(driver["driver"]), shock=value)
            output = _calculate(shocked, project_policy, valuation_policy, f"SENS-{driver['driver']}-{value}")
            summary = metric_summary(output)
            driver_rows.append({"shock": value, "summary": summary})
            rows.append({"driver": driver["driver"], "unit": driver.get("unit"), "shock": value, "summary": summary})
        values_metric = [_metric_value(row["summary"], target_metric) for row in driver_rows]
        valid = [value for value in values_metric if value is not None]
        swing = (max(valid) - min(valid)) if valid else Decimal("0")
        tornado.append({"driver": driver["driver"], "target_metric": target_metric, "base_value": None if base_value is None else format(base_value, "f"), "low_value": None if not valid else format(min(valid), "f"), "high_value": None if not valid else format(max(valid), "f"), "swing": format(swing, "f")})
    tornado.sort(key=lambda item: D(item["swing"]), reverse=True)
    return rows, tornado


def _two_way(project: dict[str, Any], project_policy: dict[str, Any], valuation_policy: dict[str, Any], price_values: list[Any], cost_values: list[Any], metric: str) -> dict[str, Any]:
    matrix: list[list[dict[str, Any]]] = []
    for price in price_values:
        row = []
        for cost in cost_values:
            shocked = apply_project_shocks(project, {"price_change": price, "cost_change": cost})
            summary = metric_summary(_calculate(shocked, project_policy, valuation_policy, f"2D-P{price}-C{cost}"))
            row.append({"price_shock": price, "cost_shock": cost, "value": summary.get(metric), "feasible": summary["feasible"]})
        matrix.append(row)
    return {"metric": metric, "price_values": price_values, "cost_values": cost_values, "matrix": matrix}


def _break_even(project: dict[str, Any], project_policy: dict[str, Any], valuation_policy: dict[str, Any], driver: str, low: Decimal, high: Decimal, iterations: int = 40) -> dict[str, Any]:
    """Find the boundary where all mandatory constraints still pass."""
    def feasible(value: Decimal) -> bool:
        shocked = _shift_series(project, driver=driver, shock=value)
        return metric_summary(_calculate(shocked, project_policy, valuation_policy, f"BE-{driver}-{value}"))["feasible"]
    # Direction: price/far/efficiency fail low; cost/share/rate/delay fail high.
    fail_low = driver in {"SALES_PRICE", "FAR", "EFFICIENCY"}
    left, right = low, high
    for _ in range(iterations):
        mid = (left + right) / 2
        ok = feasible(mid)
        if fail_low:
            if ok: right = mid
            else: left = mid
        else:
            if ok: left = mid
            else: right = mid
    boundary = right if fail_low else left
    return {"driver": driver, "boundary": format(boundary, "f"), "feasible_at_boundary": feasible(boundary)}


def run_sensitivity(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    configuration: dict[str, Any],
) -> AnalysisRun:
    version = get_project_version(session, context, project_version_id)
    policy, valuation_policy, combined_policy = _policy_pair(
        session,
        context,
        project_policy_version_id=policy_pack_version_id,
        valuation_policy_version_id=valuation_policy_pack_version_id,
    )
    scenario = _scenario_for_version(session, context, scenario_id, version.id)
    if scenario and scenario.project_version_id != version.id:
        raise NotFoundError("Scenario does not belong to the selected project version.")
    project = _project_snapshot(version, scenario)
    project, share_basis = _materialize_analysis_share(project, combined_policy, "SENSITIVITY")
    drivers = configuration.get("drivers") or [
        {"driver": "SALES_PRICE", "unit": "%", "values": ["-0.15", "-0.10", "-0.05", "0", "0.05", "0.10"]},
        {"driver": "DEVELOPMENT_COST", "unit": "%", "values": ["-0.10", "-0.05", "0", "0.05", "0.10", "0.15"]},
        {"driver": "SALES_DELAY", "unit": "months", "values": [0, 6, 12]},
        {"driver": "CONSTRUCTION_DELAY", "unit": "months", "values": [0, 6, 12]},
        {"driver": "LANDOWNER_SHARE", "unit": "% points", "values": ["-0.03", "-0.01", "0", "0.01", "0.03"]},
        {"driver": "INTEREST_RATE", "unit": "% points", "values": ["-0.02", "0", "0.02", "0.04"]},
    ]
    target_metric = str(configuration.get("target_metric") or "developer_npv")
    rows, tornado = _one_way(project, policy.policy_snapshot, valuation_policy.policy_snapshot, drivers, target_metric)
    two_way_config = configuration.get("two_way") or {"price_values": ["-0.10", "-0.05", "0", "0.05"], "cost_values": ["0", "0.05", "0.10", "0.15"], "metric": "developer_irr"}
    two_way = _two_way(project, policy.policy_snapshot, valuation_policy.policy_snapshot, list(two_way_config["price_values"]), list(two_way_config["cost_values"]), str(two_way_config.get("metric") or "developer_irr"))
    break_evens = []
    if bool(configuration.get("include_break_evens", True)):
        break_evens = [
            _break_even(project, policy.policy_snapshot, valuation_policy.policy_snapshot, "SALES_PRICE", Decimal("-0.60"), Decimal("0.20"), iterations=16),
            _break_even(project, policy.policy_snapshot, valuation_policy.policy_snapshot, "DEVELOPMENT_COST", Decimal("-0.10"), Decimal("1.00"), iterations=16),
            _break_even(project, policy.policy_snapshot, valuation_policy.policy_snapshot, "LANDOWNER_SHARE", Decimal("-0.20"), Decimal("0.50"), iterations=16),
            _break_even(project, policy.policy_snapshot, valuation_policy.policy_snapshot, "SALES_DELAY", Decimal("0"), Decimal("60"), iterations=12),
        ]
    base = metric_summary(_calculate(project, policy.policy_snapshot, valuation_policy.policy_snapshot, "SENSITIVITY-BASE"))
    output = {"analysis_model_version": ANALYSIS_MODEL_VERSION, "share_basis": share_basis, "policy_sources": {"project_policy_version_id": policy.id, "valuation_policy_version_id": valuation_policy.id}, "base": base, "target_metric": target_metric, "one_way": rows, "tornado": tornado, "two_way": two_way, "break_evens": break_evens}
    return _persist_run(session, context=context, version=version, policy=policy, valuation_policy=valuation_policy, scenario=scenario, analysis_type="SENSITIVITY", input_snapshot=configuration, output_snapshot=output)


def run_monte_carlo(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    configuration: dict[str, Any],
) -> AnalysisRun:
    version = get_project_version(session, context, project_version_id)
    policy, valuation_policy, combined_policy = _policy_pair(
        session,
        context,
        project_policy_version_id=policy_pack_version_id,
        valuation_policy_version_id=valuation_policy_pack_version_id,
    )
    scenario = _scenario_for_version(session, context, scenario_id, version.id)
    project = _project_snapshot(version, scenario)
    project, share_basis = _materialize_analysis_share(project, combined_policy, "MONTE-CARLO")
    risk_policy = policy.policy_snapshot.get("risk_policy") or {}
    maximum = int(risk_policy.get("monte_carlo_max_iterations", 5000))
    iterations = int(configuration.get("iterations", risk_policy.get("monte_carlo_default_iterations", 500)))
    if iterations < 50 or iterations > maximum:
        raise ConflictError("MONTE_CARLO_ITERATIONS_OUT_OF_RANGE", f"Iterations must be between 50 and {maximum}.")
    seed = int(configuration.get("seed", 360))
    rng = random.Random(seed)
    distributions = configuration.get("distributions") or {
        "price_change": {"type": "TRIANGULAR", "low": "-0.15", "mode": "0", "high": "0.10"},
        "cost_change": {"type": "TRIANGULAR", "low": "-0.05", "mode": "0.05", "high": "0.20"},
        "sales_delay_months": {"type": "TRIANGULAR", "low": "0", "mode": "3", "high": "12"},
        "interest_change": {"type": "TRIANGULAR", "low": "0", "mode": "0.01", "high": "0.04"},
    }
    metric_names = list(configuration.get("metrics") or ["developer_irr", "developer_npv", "government_npv", "peak_funding", "funding_gap"])
    samples: dict[str, list[Decimal]] = {name: [] for name in metric_names}
    failures = 0
    funding_failures = 0
    sample_rows: list[dict[str, Any]] = []
    for index in range(iterations):
        shocks = {key: sample_distribution(config, rng) for key, config in distributions.items()}
        if "sales_delay_months" in shocks:
            shocks["sales_delay_months"] = int(shocks["sales_delay_months"].quantize(Decimal("1")))
        shocked = apply_project_shocks(project, shocks)
        summary = metric_summary(_calculate(shocked, policy.policy_snapshot, valuation_policy.policy_snapshot, f"MC-{seed}-{index}"))
        if not summary["feasible"]:
            failures += 1
        if D(summary.get("funding_gap")) > 0:
            funding_failures += 1
        for name in metric_names:
            value = _metric_value(summary, name)
            if value is not None:
                samples[name].append(value)
        if index < 100:
            sample_rows.append({"iteration": index + 1, "shocks": {key: format(D(value), "f") if not isinstance(value, int) else value for key, value in shocks.items()}, "summary": summary})
    statistics: dict[str, Any] = {}
    for name, values in samples.items():
        statistics[name] = {
            "count": len(values),
            "p10": None if percentile(values, 10) is None else format(percentile(values, 10), "f"),
            "p50": None if percentile(values, 50) is None else format(percentile(values, 50), "f"),
            "p90": None if percentile(values, 90) is None else format(percentile(values, 90), "f"),
            "minimum": None if not values else format(min(values), "f"),
            "maximum": None if not values else format(max(values), "f"),
            "mean": None if not values else format(sum(values, Decimal("0")) / Decimal(len(values)), "f"),
        }
    output = {
        "analysis_model_version": ANALYSIS_MODEL_VERSION,
        "share_basis": share_basis,
        "policy_sources": {"project_policy_version_id": policy.id, "valuation_policy_version_id": valuation_policy.id},
        "seed": seed,
        "iterations": iterations,
        "distributions": deepcopy(distributions),
        "statistics": statistics,
        "probability_any_constraint_failure": format(Decimal(failures) / Decimal(iterations), "f"),
        "probability_funding_gap": format(Decimal(funding_failures) / Decimal(iterations), "f"),
        "sample_rows": sample_rows,
        "reproducibility_hash": sha256_json({"seed": seed, "iterations": iterations, "distributions": distributions, "statistics": statistics, "failures": failures, "funding_failures": funding_failures}),
        "limitations": ["Baseline Monte Carlo assumes independent drivers; correlations require an approved correlation matrix in a future policy pack.", "The first 100 iterations are retained for diagnostics; aggregate statistics use all iterations."],
    }
    return _persist_run(session, context=context, version=version, policy=policy, valuation_policy=valuation_policy, scenario=scenario, analysis_type="MONTE_CARLO", input_snapshot=configuration, output_snapshot=output)


def _latest_valuation(session: Session, context: AuthContext, project_version_id: str) -> ValuationRun | None:
    return session.scalar(select(ValuationRun).where(ValuationRun.project_version_id == project_version_id, *tenant_clause(ValuationRun, context)).order_by(ValuationRun.created_at.desc()))


def tender_readiness(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    risk_items: list[dict[str, Any]] | None = None,
) -> AnalysisRun:
    version = get_project_version(session, context, project_version_id)
    policy, valuation_policy, combined_policy = _policy_pair(
        session,
        context,
        project_policy_version_id=policy_pack_version_id,
        valuation_policy_version_id=valuation_policy_pack_version_id,
    )
    scenario = _scenario_for_version(session, context, scenario_id, version.id)
    project = _project_snapshot(version, scenario)
    project, share_basis = _materialize_analysis_share(project, combined_policy, "TENDER-READINESS")
    calculation = metric_summary(_calculate(project, policy.policy_snapshot, valuation_policy.policy_snapshot, "TENDER-READINESS"))
    quality = preview_data_quality(session, context=context, project_version_id=version.id)
    risk = assess_risk_register(risk_items if risk_items is not None else (project.get("risk_register") or {}).get("items") or [])
    valuation = _latest_valuation(session, context, version.id)
    valuation_output = valuation.output_snapshot if valuation else {}
    valuation_methods = int((valuation_output.get("reconciliation") or {}).get("method_count") or 0)
    valuation_score = 100 if valuation and valuation.status in {"SUCCESS", "SUCCESS_WITH_WARNINGS"} else 50 if valuation else 0
    legal_score = D((quality.get("readiness_gates") or {}).get("legal", {}).get("score"), "0")
    evidence_score = D(quality.get("score"), "0")
    risk_score = Decimal("100") - D(risk.get("score"), "100")
    feasibility_score = Decimal("100") if calculation["feasible"] else Decimal("35") if calculation["status"] != "FAILED" else Decimal("0")
    planning_context = project.get("valuation_context") or {}
    maturity_map = {"STRATEGIC": 20, "CONCEPT": 45, "SPATIAL_COORDINATION": 65, "TECHNICAL_DESIGN": 85, "CONSTRUCTION": 100}
    maturity_score = Decimal(maturity_map.get(str(planning_context.get("design_maturity") or "CONCEPT"), 45))
    components = {
        "financial_feasibility": format(feasibility_score, "f"),
        "data_and_evidence": format(evidence_score, "f"),
        "risk_control": format(max(Decimal("0"), risk_score), "f"),
        "valuation_support": str(valuation_score),
        "legal_readiness": format(legal_score, "f"),
        "design_maturity": format(maturity_score, "f"),
    }
    weights = {"financial_feasibility": Decimal("0.25"), "data_and_evidence": Decimal("0.20"), "risk_control": Decimal("0.20"), "valuation_support": Decimal("0.15"), "legal_readiness": Decimal("0.10"), "design_maturity": Decimal("0.10")}
    score = sum(D(components[key]) * weight for key, weight in weights.items())
    threshold = D((policy.policy_snapshot.get("tender_policy") or {}).get("minimum_tender_readiness_score"), "70")
    gates = [
        {"gate_id": "FINANCIAL_FEASIBILITY", "passed": calculation["feasible"], "actual": calculation["status"], "threshold": "FEASIBLE"},
        {"gate_id": "DATA_QUALITY", "passed": evidence_score >= D((policy.policy_snapshot.get("valuation_policy") or {}).get("feasibility_data_quality_threshold"), "70"), "actual": format(evidence_score, "f"), "threshold": (policy.policy_snapshot.get("valuation_policy") or {}).get("feasibility_data_quality_threshold", "70")},
        {"gate_id": "RISK", "passed": D(risk["score"]) <= D((policy.policy_snapshot.get("risk_policy") or {}).get("maximum_residual_risk_score"), "55"), "actual": risk["score"], "threshold": (policy.policy_snapshot.get("risk_policy") or {}).get("maximum_residual_risk_score", "55")},
        {"gate_id": "VALUATION_METHODS", "passed": valuation_methods >= int((policy.policy_snapshot.get("valuation_policy") or {}).get("minimum_reconciliation_methods", 2)), "actual": valuation_methods, "threshold": int((policy.policy_snapshot.get("valuation_policy") or {}).get("minimum_reconciliation_methods", 2))},
    ]
    grade = "READY" if score >= threshold and all(g["passed"] for g in gates) else "READY_SUBJECT_TO_CONDITIONS" if score >= threshold * Decimal("0.8") else "NOT_READY"
    output = {"analysis_model_version": ANALYSIS_MODEL_VERSION, "share_basis": share_basis, "policy_sources": {"project_policy_version_id": policy.id, "valuation_policy_version_id": valuation_policy.id}, "score": format(score.quantize(Decimal("0.01")), "f"), "threshold": format(threshold, "f"), "grade": grade, "components": components, "weights": {k: format(v, "f") for k, v in weights.items()}, "gates": gates, "calculation_summary": calculation, "data_quality": quality, "risk": risk, "valuation_run_id": valuation.id if valuation else None, "recommendation": "Proceed to tender preparation." if grade == "READY" else "Complete failed gates before formal tender."}
    return _persist_run(session, context=context, version=version, policy=policy, valuation_policy=valuation_policy, scenario=scenario, analysis_type="TENDER_READINESS", input_snapshot={"risk_items": risk_items}, output_snapshot=output, status="SUCCESS" if grade == "READY" else "SUCCESS_WITH_WARNINGS")


def _apply_bid(project: dict[str, Any], bid: dict[str, Any]) -> dict[str, Any]:
    result = apply_project_shocks(project, {"price_change": D(bid.get("price_multiplier"), "1") - 1, "cost_change": D(bid.get("cost_multiplier"), "1") - 1})
    result.setdefault("funding", {})["committed_equity"] = str(bid.get("committed_equity") or "0")
    result.setdefault("funding", {})["committed_financing"] = str(bid.get("committed_financing") or "0")
    result.setdefault("finance_model", {})["annual_interest_rate"] = str(bid.get("annual_interest_rate") or "0")
    completion_months = int(bid.get("completion_months") or 0)
    if completion_months > 0:
        for product in result.get("products") or []:
            if product.get("active", True):
                product["construction_duration_months"] = completion_months
        for cost in result.get("costs") or []:
            category = str(cost.get("category") or "").upper()
            if category in {
                "BUILDING", "CONSTRUCTION", "DIRECT_CONSTRUCTION", "PRODUCT_CONSTRUCTION",
                "INTERNAL_INFRASTRUCTURE", "EXTERNAL_INFRASTRUCTURE", "INFRASTRUCTURE",
                "PUBLIC_FACILITIES", "PROFESSIONAL_FEES", "PROJECT_MANAGEMENT",
            }:
                # The advisory monthly studio reads monthly_duration_months.
                # Keep the historical duration_months key as metadata only.
                cost["monthly_duration_months"] = completion_months
                cost["duration_months"] = completion_months
        result.setdefault("tender_assumptions", {})["completion_months"] = completion_months
        result["tender_assumptions"]["completion_schedule_applied"] = True
    partnership = result.setdefault("partnership", {})
    method = str(bid.get("method") or "GROSS_SALES")
    share = D(bid.get("share_rate"))
    partnership.update({"method": method, "share_rate": format(share, "f"), "manual_share": format(share, "f"), "approved_selection": "MANUAL", "upfront_payments": []})
    upfront = D(bid.get("upfront_amount"))
    if method in {"UPFRONT", "HYBRID"} and upfront > 0:
        partnership["upfront_payments"] = [{"date": result.get("valuation_date"), "amount": format(upfront, "f"), "label": "Bid upfront consideration"}]
    if method == "MINIMUM_GUARANTEE":
        partnership["manual_amount"] = format(upfront, "f")
        studio = result.setdefault("landowner_studio", {})
        studio["minimum_guarantee_amount"] = format(upfront, "f")
        studio["minimum_guarantee_underlying_share"] = format(share, "f")
        studio.setdefault("minimum_guarantee_underlying_method", "GROSS_SALES")
    return result


def evaluate_tender_bids(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    criteria_weights: dict[str, Any],
    bids: list[dict[str, Any]],
) -> AnalysisRun:
    version = get_project_version(session, context, project_version_id)
    policy, valuation_policy, _combined_policy = _policy_pair(
        session,
        context,
        project_policy_version_id=policy_pack_version_id,
        valuation_policy_version_id=valuation_policy_pack_version_id,
    )
    scenario = _scenario_for_version(session, context, scenario_id, version.id)
    project = _project_snapshot(version, scenario)
    keys = ("financial", "technical", "risk_guarantees", "integrity")
    weights = {key: D(criteria_weights.get(key)) for key in keys}
    if abs(sum(weights.values(), Decimal("0")) - Decimal("1")) > Decimal("0.0001"):
        raise ConflictError("TENDER_WEIGHTS_MUST_TOTAL_100", "Tender evaluation weights must total 100%.")
    tender_policy = policy.policy_snapshot.get("tender_policy") or {}
    min_total = D(tender_policy.get("minimum_bid_total_score"), "70")
    min_technical = D(tender_policy.get("minimum_bid_technical_score"), "60")
    raw_rows = []
    public_values: list[Decimal] = []
    for bid in bids:
        shocked = _apply_bid(project, bid)
        summary = metric_summary(_calculate(shocked, policy.policy_snapshot, valuation_policy.policy_snapshot, f"BID-{bid.get('bid_id')}"))
        public_value = D(summary.get("government_npv"))
        public_values.append(public_value)
        technical = (D(bid.get("technical_score")) + D(bid.get("experience_score"))) / 2
        risk_guarantees = D(bid.get("guarantees_score"))
        integrity = D(bid.get("integrity_score"))
        raw_rows.append({"bid": deepcopy(bid), "summary": summary, "public_value": public_value, "technical": technical, "risk_guarantees": risk_guarantees, "integrity": integrity})
    max_public = max(public_values, default=Decimal("0"))
    rows = []
    for item in raw_rows:
        financial = Decimal("0") if max_public <= 0 else max(Decimal("0"), item["public_value"] / max_public * Decimal("100"))
        total = financial * weights["financial"] + item["technical"] * weights["technical"] + item["risk_guarantees"] * weights["risk_guarantees"] + item["integrity"] * weights["integrity"]
        summary = item["summary"]
        disqualifications = []
        if D(item["technical"]) < min_technical: disqualifications.append("TECHNICAL_SCORE_BELOW_MINIMUM")
        if bool(tender_policy.get("disqualify_unfunded_bids", True)) and D(summary.get("funding_gap")) > 0: disqualifications.append("UNFUNDED_BID")
        if bool(tender_policy.get("disqualify_financially_infeasible_bids", True)) and not summary.get("feasible"): disqualifications.append("FINANCIALLY_INFEASIBLE")
        if total < min_total: disqualifications.append("TOTAL_SCORE_BELOW_MINIMUM")
        gross_land = D(project.get("planning", {}).get("gross_land_area_sqm"), "0")
        implied_land_value = item["public_value"]
        rows.append({
            "bid_id": item["bid"].get("bid_id"), "bidder": item["bid"].get("bidder"), "method": item["bid"].get("method"),
            "financial_score": format(financial.quantize(Decimal("0.01")), "f"), "technical_score": format(item["technical"].quantize(Decimal("0.01")), "f"), "risk_guarantees_score": format(item["risk_guarantees"], "f"), "integrity_score": format(item["integrity"], "f"), "total_score": format(total.quantize(Decimal("0.01")), "f"),
            "eligible": not disqualifications, "disqualifications": disqualifications, "summary": summary,
            "bid_implied_land_value": format(implied_land_value, "f"), "bid_implied_land_value_per_sqm": None if gross_land <= 0 else format(implied_land_value / gross_land, "f"),
            "completion_months": item["bid"].get("completion_months"),
        })
    rows.sort(key=lambda row: (row["eligible"], D(row["total_score"])), reverse=True)
    for index, row in enumerate(rows, start=1): row["rank"] = index if row["eligible"] else None
    recommended = next((row for row in rows if row["eligible"]), None)
    output = {"analysis_model_version": ANALYSIS_MODEL_VERSION, "policy_sources": {"project_policy_version_id": policy.id, "valuation_policy_version_id": valuation_policy.id}, "criteria_weights": {k: format(v, "f") for k, v in weights.items()}, "rows": rows, "recommended_bid_id": recommended["bid_id"] if recommended else None, "recommendation": "Committee review required; the model does not replace procurement law or the tender committee.", "warnings": [] if recommended else ["No bid passed all mandatory financial, technical and funding gates."]}
    return _persist_run(session, context=context, version=version, policy=policy, valuation_policy=valuation_policy, scenario=scenario, analysis_type="TENDER_EVALUATION", input_snapshot={"criteria_weights": criteria_weights, "bids": bids}, output_snapshot=output, status="SUCCESS" if recommended else "SUCCESS_WITH_WARNINGS")
