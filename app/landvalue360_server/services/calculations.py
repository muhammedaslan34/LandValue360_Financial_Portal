"""Canonical calculation-run orchestration for detailed LandValue360 projects.

Version 2.1 removes the frozen feasibility and finance engines from the
operational path.  Every calculation, screen, solver and report reads one
Unified Monthly Engine result and one Financial Truth contract.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__ as APPLICATION_VERSION
from ..audit import record_audit
from ..context import AuthContext
from ..costing import resolve_project_costs
from ..decision import build_decision_explanation
from ..developer_advisory import build_developer_advisory
from ..funding_policy import apply_equity_commitment_policy
from ..report_readiness import find_demo_inputs
from ..solver import solve_constraints
from ..enums import CalculationMode, ProjectKind
from ..errors import ConflictError, NotFoundError
from ..json_tools import json_merge_patch, sha256_json
from ..unified_engine import run_unified_financial_engine
from ..models import CalculationRun, PolicyPackVersion, Project, utc_now
from ..project_normalization import materialize_project_for_calculation
from ..state_machine import derive_run_readiness
from .policies import policy_applies_to, policy_is_effective, policy_type, require_operational_policy
from .tenant import get_policy_version, get_project_version, get_scenario, tenant_clause

from landvalue360_common.versions import (
    APPLICATION_INPUT_CONTRACT_VERSION,
    ENGINE_VERSION,
    FINANCE_MODEL_VERSION,
)


def _compose_governed_policy(project_policy: dict[str, Any], valuation_policy: dict[str, Any] | None) -> dict[str, Any]:
    """Compose project and valuation policy families without ambiguous ownership."""
    merged = deepcopy(project_policy or {})
    valuation = deepcopy(valuation_policy or {})
    for key in ("share_policy", "fair_consideration_policy", "public_value_adjustment", "valuation_policy"):
        if isinstance(valuation.get(key), dict):
            merged[key] = deepcopy(valuation[key])
    project_financial = deepcopy(merged.get("financial_constraints") or {})
    valuation_financial = valuation.get("financial_constraints") if isinstance(valuation.get("financial_constraints"), dict) else {}
    if valuation_financial.get("government_discount_rate") in (None, ""):
        raise ConflictError(
            "VALUATION_POLICY_DISCOUNT_RATE_REQUIRED",
            "The selected valuation policy must define government_discount_rate; no hidden default is permitted.",
        )
    project_financial["government_discount_rate"] = valuation_financial["government_discount_rate"]
    for metadata_key in ("discount_rate_type", "discount_currency", "discount_compounding"):
        if valuation_financial.get(metadata_key) not in (None, ""):
            project_financial[metadata_key] = valuation_financial[metadata_key]
            project_financial[f"government_{metadata_key}"] = valuation_financial[metadata_key]
    merged["financial_constraints"] = project_financial
    merged["policy_sources"] = {
        "project_policy_id": project_policy.get("policy_id"),
        "project_policy_version": project_policy.get("version"),
        "valuation_policy_id": valuation.get("policy_id"),
        "valuation_policy_version": valuation.get("version"),
        "valuation_policy_effective_date": valuation.get("effective_date"),
    }
    merged["valuation_policy_context"] = {
        "policy_id": valuation.get("policy_id"),
        "policy_version": valuation.get("version"),
        "effective_date": valuation.get("effective_date"),
    }
    return merged


def compose_calculation_envelope(
    *,
    project_snapshot: dict[str, Any],
    policy_snapshot: dict[str, Any],
    valuation_policy_snapshot: dict[str, Any] | None = None,
    case_id: str,
    description: str | None,
) -> dict[str, Any]:
    """Build the only supported detailed calculation contract.

    The envelope intentionally stores the calculation-ready project and the
    exact source snapshots used to derive it.  No stripped legacy kernel
    representation is produced.
    """
    if valuation_policy_snapshot is None:
        raise ConflictError(
            "CALCULATION_VALUATION_POLICY_REQUIRED",
            "A complete valuation-policy snapshot is required; no default discount or valuation policy is applied.",
        )
    source_project = materialize_project_for_calculation(project_snapshot)
    source_project_policy = deepcopy(policy_snapshot)
    source_valuation_policy = deepcopy(valuation_policy_snapshot)
    governed_policy = _compose_governed_policy(source_project_policy, source_valuation_policy)
    # Resolve costs once for the audit/report payload, but pass the normalized
    # source project to the unified engine. The engine owns the authoritative
    # cost resolution. Passing an already-resolved project caused generated
    # product-construction rows to be generated a second time.
    _cost_resolved_project, cost_report = resolve_project_costs(source_project)
    calculation_project, calculation_policy, funding_report = apply_equity_commitment_policy(
        source_project, governed_policy
    )
    return {
        "schema_version": APPLICATION_INPUT_CONTRACT_VERSION,
        "application_contract_version": APPLICATION_INPUT_CONTRACT_VERSION,
        "case_id": case_id,
        "description": description or "Detailed calculation run generated by LandValue360.",
        "project": deepcopy(calculation_project),
        "policy": deepcopy(calculation_policy),
        "application_extensions": {
            "governed_project_snapshot": deepcopy(calculation_project),
            "source_project_snapshot": deepcopy(source_project),
            "governed_policy_snapshot": deepcopy(calculation_policy),
            "governed_project_policy_snapshot": source_project_policy,
            "governed_valuation_policy_snapshot": source_valuation_policy,
            "cost_source_items": deepcopy(source_project.get("costs") or []),
            "cost_calculation": cost_report,
            "equity_commitment_policy": funding_report,
            "valuation_policy_selection": "EXPLICIT_VERSIONED_POLICY",
        },
    }


def _compose_envelope(*, project_version, policy_version, valuation_policy_version, scenario, case_id: str, description: str | None) -> dict[str, Any]:
    base = deepcopy(project_version.input_snapshot)
    project = json_merge_patch(base, scenario.override_snapshot) if scenario is not None else deepcopy(base)
    project["project_id"] = base.get("project_id")
    project["project_name"] = base.get("project_name")
    return compose_calculation_envelope(
        project_snapshot=project,
        policy_snapshot=deepcopy(policy_version.policy_snapshot),
        valuation_policy_snapshot=deepcopy(valuation_policy_version.policy_snapshot),
        case_id=case_id,
        description=description,
    )


def _resolve_valuation_policy_version(session: Session, *, context: AuthContext, edition: str, version_id: str | None) -> PolicyPackVersion:
    if version_id:
        return require_operational_policy(get_policy_version(session, context, version_id), edition=edition, expected_type="VALUATION")
    candidates = list(session.scalars(select(PolicyPackVersion).where(*tenant_clause(PolicyPackVersion, context)).order_by(PolicyPackVersion.published_at.desc(), PolicyPackVersion.created_at.desc())).all())
    for version in candidates:
        if policy_is_effective(version) and policy_type(version.policy_snapshot) == "VALUATION" and policy_applies_to(version.policy_snapshot, edition):
            return version
    raise ConflictError("CALCULATION_VALUATION_POLICY_REQUIRED", "Select a published and currently effective valuation-policy version for this calculation.")


def _unified_approved_case(unified: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """Compatibility view populated exclusively from Financial Truth."""
    selected = unified.get("selected_contract") or {}
    return {
        "source": "UNIFIED_FINANCIAL_TRUTH_COMPATIBILITY_VIEW",
        "feasible": bool(truth.get("feasible")),
        "calculation_valid": bool(truth.get("result_usable")),
        "evaluation_status": truth.get("evaluation_status"),
        "metrics": {
            "project_npv": truth.get("project_npv"),
            "project_irr": truth.get("project_irr"),
            "developer_npv": truth.get("developer_npv"),
            "developer_irr": truth.get("developer_irr"),
            "developer_nominal_profit": truth.get("developer_profit"),
            "developer_profit_on_cost": truth.get("developer_profit_on_cost"),
            "developer_capital_multiple": truth.get("developer_multiple"),
            "peak_developer_funding": truth.get("peak_equity"),
            "funding_gap": truth.get("funding_gap"),
            "government_cash_total": truth.get("government_consideration"),
            "government_cash_npv": truth.get("government_npv"),
            "developer_total_cost_including_land_consideration": str(
                (truth.get("developer_planned_cost") or 0)
            ),
        },
        "constraints": deepcopy(truth.get("constraints") or []),
        "cash_flows": {},
        "partnership": {
            "method": truth.get("method"),
            "measure": truth.get("approved_share"),
            "government_value": truth.get("government_consideration"),
            "government_npv": truth.get("government_npv"),
        },
        "selected_contract": deepcopy(selected),
    }


def _canonical_finance_analysis(unified: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    """Stable compatibility surface sourced only from the unified ledger."""
    metrics = {
        "developer_unlevered_irr": truth.get("developer_irr"),
        "developer_unlevered_npv": truth.get("developer_npv"),
        "developer_equity_irr": truth.get("developer_equity_irr"),
        "developer_equity_npv": truth.get("developer_equity_npv"),
        "developer_equity_multiple": truth.get("developer_multiple"),
        "peak_equity": truth.get("peak_equity"),
        "peak_senior_debt": truth.get("peak_debt"),
        "structured_funding_gap": truth.get("funding_gap"),
        "terminal_debt_balance": truth.get("terminal_debt"),
        "deferred_development_cost": truth.get("deferred_development_cost"),
        "deferred_contractual_payment": truth.get("deferred_contractual_payment"),
        "finance_arrears": truth.get("finance_arrears"),
        "minimum_dscr": truth.get("minimum_dscr"),
        "aggregate_dscr": truth.get("aggregate_dscr"),
        "minimum_period_dscr": truth.get("minimum_dscr"),
        "loan_to_cost": truth.get("loan_to_cost"),
        "loan_to_value_proxy": truth.get("loan_to_value_proxy"),
        "schedule_extension_months": truth.get("schedule_extension_months"),
        "original_completion_date": truth.get("original_completion_date"),
        "adjusted_completion_date": truth.get("adjusted_completion_date"),
    }
    return {
        "finance_model_version": FINANCE_MODEL_VERSION,
        "status": "PASS" if truth.get("result_usable") else "FAIL",
        "source": "UNIFIED_FINANCIAL_TRUTH",
        "currency": truth.get("currency") or unified.get("reporting_currency"),
        "metrics": metrics,
        "constraints": deepcopy(truth.get("finance_constraints") or []),
        "schedule": deepcopy(unified.get("monthly_cashflow") or []),
        "cash_flow_series": [],
        "assumptions": {
            "finance_mode": truth.get("finance_mode"),
            "spend_policy": truth.get("spend_policy"),
        },
    }


def execute_calculation_envelope(
    envelope: dict[str, Any],
    *,
    optimize_share: bool = True,
    include_decision: bool = True,
    include_solver: bool = True,
    unified_selected_only: bool = True,
    include_legacy_audit: bool = False,
) -> dict[str, Any]:
    """Execute the detailed contract through the unified monthly engine only."""
    extensions = deepcopy(envelope.get("application_extensions") or {})
    project = deepcopy(extensions.get("governed_project_snapshot") or envelope.get("project") or {})
    policy = deepcopy(extensions.get("governed_policy_snapshot") or envelope.get("policy") or {})
    if not project or not policy:
        raise ConflictError(
            "LEGACY_CALCULATION_CONTRACT_UNSUPPORTED",
            "This release accepts only the versioned detailed application contract. Import the project through the migration service first.",
        )
    contract_version = str(envelope.get("application_contract_version") or envelope.get("schema_version") or "")
    output: dict[str, Any] = {
        "schema_version": APPLICATION_INPUT_CONTRACT_VERSION,
        "calculation_model_version": ENGINE_VERSION,
        "application_input_contract_version": contract_version or APPLICATION_INPUT_CONTRACT_VERSION,
        "application_input_hash": sha256_json(envelope),
        "reporting_currency": project.get("reporting_currency") or project.get("currency") or "USD",
        "validation_messages": [],
        "legacy_audit_status": "REMOVED_FROM_RUNTIME",
        "legacy_audit_result": {
            "status": "REMOVED_FROM_RUNTIME",
            "message": "Legacy equations are not executed by the detailed-stable release.",
        },
        "cost_calculation": deepcopy(extensions.get("cost_calculation") or {}),
        "equity_commitment_policy": deepcopy(extensions.get("equity_commitment_policy") or {}),
    }
    try:
        unified = run_unified_financial_engine(project, policy, legacy_output=None, selected_only=unified_selected_only)
    except Exception as exc:
        output["status"] = "FAILED"
        output["validation_messages"].append({
            "severity": "ERROR",
            "code": "UNIFIED_FINANCIAL_ENGINE_FAILED",
            "message": f"The authoritative unified monthly engine failed: {exc}",
            "path": "unified_financial_result",
            "visibility": "USER",
        })
        return output
    truth = deepcopy(unified.get("financial_truth") or {})
    output.update({
        "unified_financial_result": unified,
        "financial_truth": truth,
        "approved_case": _unified_approved_case(unified, truth),
        "approved_case_source": "UNIFIED_FINANCIAL_TRUTH",
        "display_authority": "FINANCIAL_TRUTH",
        "finance_model_version": FINANCE_MODEL_VERSION,
        "finance_analysis": _canonical_finance_analysis(unified, truth),
        "engine_manifest": deepcopy(unified.get("engine_manifest") or {}),
        "event_ledger": deepcopy(unified.get("event_ledger") or {}),
        "engine_invariants": deepcopy(unified.get("engine_invariants") or {}),
        "cash_flow_series": [],
        "financial_reconciliation": {
            "status": "RECONCILED" if truth.get("cash_reconciliation_passed") and truth.get("ledger_invariants_passed") else "OUT_OF_BALANCE",
            "source": unified.get("single_source_financial_kernel"),
            "engine_version": unified.get("engine_version"),
            "calculation_hash": unified.get("calculation_hash"),
            "ledger_hash": (unified.get("event_ledger") or {}).get("ledger_hash"),
            "invariant_hash": (unified.get("engine_invariants") or {}).get("invariant_hash"),
            "message": "Every financial surface is derived from one unified monthly ledger.",
        },
    })
    scope = (output["cost_calculation"].get("scope_coverage") or {})
    if scope.get("status") == "INCOMPLETE":
        output["validation_messages"].append({
            "severity": "WARNING", "code": "COST_SCOPE_INCOMPLETE",
            "message": scope.get("message_en") or "Required cost scope is missing.",
            "message_ar": scope.get("message_ar"), "path": "project.costs", "visibility": "USER",
            "missing_scope_ids": scope.get("missing_required_scope_ids") or [],
        })
    if not truth.get("result_usable"):
        output["status"] = "FAILED"
    elif truth.get("feasible") and truth.get("policy_compliant", True):
        output["status"] = "SUCCESS"
    else:
        output["status"] = "SUCCESS_WITH_WARNINGS"
    if scope.get("status") == "INCOMPLETE" and output["status"] == "SUCCESS":
        output["status"] = "SUCCESS_WITH_WARNINGS"
    if include_decision:
        output["decision_explanation"] = build_decision_explanation(output)
    if include_solver:
        output["constraint_solver"] = solve_constraints(
            envelope,
            output,
            evaluate=lambda candidate: execute_calculation_envelope(
                candidate,
                optimize_share=False,
                include_decision=True,
                include_solver=False,
                unified_selected_only=True,
            ),
        )
    output["developer_advisory"] = build_developer_advisory(output, project_snapshot=project, policy_snapshot=policy)
    return output

def create_calculation_run(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str | None = None,
    scenario_id: str | None,
    mode: CalculationMode,
    case_id: str | None,
    description: str | None,
    analysis_level: str = "FULL",
    replayed_from_run_id: str | None = None,
    fixed_input_snapshot: dict[str, Any] | None = None,
    optimize_share: bool = True,
) -> CalculationRun:
    project_version = get_project_version(session, context, project_version_id)
    policy_version = get_policy_version(session, context, policy_pack_version_id)
    scenario = get_scenario(session, context, scenario_id) if scenario_id else None
    if scenario is not None and scenario.project_version_id != project_version.id:
        raise NotFoundError("Scenario does not belong to the selected project version.")
    if mode == CalculationMode.OFFICIAL:
        if project_version.status != "APPROVED":
            raise ConflictError(
                "OFFICIAL_RUN_REQUIRES_APPROVED_PROJECT_VERSION",
                "Official calculations require an approved project version.",
            )
        if policy_version.status != "PUBLISHED":
            raise ConflictError(
                "OFFICIAL_RUN_REQUIRES_PUBLISHED_POLICY",
                "Official calculations require a published policy version.",
            )
        demo_findings = find_demo_inputs(project_version.input_snapshot or {})
        if demo_findings:
            raise ConflictError(
                "OFFICIAL_RUN_DEMO_INPUTS",
                "Official calculations cannot use demo or unconfirmed template values. Confirm or replace all training inputs first.",
            )

    project = session.scalar(
        select(Project).where(Project.id == project_version.project_id, *tenant_clause(Project, context))
    )
    if project is None:
        raise NotFoundError("Project not found.")
    edition = "LANDOWNER" if project.project_kind == ProjectKind.GOVERNMENT.value else "DEVELOPER"
    policy_version = require_operational_policy(
        policy_version, edition=edition, expected_type="PROJECT"
    )
    valuation_policy_version = _resolve_valuation_policy_version(
        session,
        context=context,
        edition=edition,
        version_id=valuation_policy_pack_version_id,
    )

    generated_case_id = case_id or f"{project.code}-V{project_version.version_number}"
    if fixed_input_snapshot is not None:
        envelope = deepcopy(fixed_input_snapshot)
        extensions = envelope.setdefault("application_extensions", {})
        project_policy_snapshot = deepcopy(
            extensions.get("governed_project_policy_snapshot")
            or extensions.get("governed_policy_snapshot")
            or policy_version.policy_snapshot
        )
        extensions["governed_project_policy_snapshot"] = project_policy_snapshot
        extensions["governed_valuation_policy_snapshot"] = deepcopy(
            valuation_policy_version.policy_snapshot
        )
        extensions["governed_policy_snapshot"] = _compose_governed_policy(
            project_policy_snapshot, valuation_policy_version.policy_snapshot
        )
    else:
        envelope = _compose_envelope(
            project_version=project_version,
            policy_version=policy_version,
            valuation_policy_version=valuation_policy_version,
            scenario=scenario,
            case_id=generated_case_id,
            description=description,
        )
    normalized_analysis_level = str(analysis_level or "FULL").upper()
    if normalized_analysis_level not in {"STANDARD", "FULL"}:
        raise ConflictError(
            "CALCULATION_ANALYSIS_LEVEL_INVALID",
            "analysis_level must be STANDARD or FULL.",
        )
    output = execute_calculation_envelope(
        envelope,
        optimize_share=bool(optimize_share),
        include_decision=True,
        include_solver=normalized_analysis_level == "FULL",
        # A calculation run evaluates the selected governed contract only.
        # Cross-contract fair-share comparison is an explicit analytical route,
        # not an implicit side effect of every project calculation.
        unified_selected_only=normalized_analysis_level != "FULL",
    )
    output["analysis_level"] = normalized_analysis_level
    output["run_context"] = {
        "project_id": project.id,
        "project_version_id": project_version.id,
        "project_version_number": project_version.version_number,
        "project_version_input_hash": project_version.input_hash,
        "policy_pack_version_id": policy_version.id,
        "policy_hash": policy_version.policy_hash,
        "valuation_policy_pack_version_id": valuation_policy_version.id,
        "valuation_policy_hash": valuation_policy_version.policy_hash,
        "scenario_id": scenario.id if scenario else None,
        "scenario_hash": scenario.override_hash if scenario else None,
        "analysis_level": normalized_analysis_level,
        "display_authority": "financial_truth",
    }
    completed_at = utc_now()
    advisory_status = str(output.get("status", "FAILED"))
    output_status = "FAILED" if advisory_status == "FAILED" else "SUCCESS"
    readiness = derive_run_readiness(output, run_locked=False, official_requested=mode == CalculationMode.OFFICIAL)
    output["state"] = {
        "calculation_validity": readiness.calculation_validity.value,
        "economic_feasibility": readiness.economic_feasibility.value,
        "policy_compliance": readiness.policy_compliance.value,
        "evidence_readiness": readiness.evidence_readiness.value,
        "report_readiness": readiness.report_readiness.value,
        "advisory_status": advisory_status,
    }
    error_summary = None
    if output_status == "FAILED":
        messages = output.get("validation_messages") or []
        error_summary = "; ".join(str(item.get("message", "Calculation failed.")) for item in messages[:5])
    if mode == CalculationMode.OFFICIAL:
        truth = output.get("financial_truth") or {}
        reconciliation = output.get("financial_reconciliation") or {}
        if not truth.get("result_usable") or str(reconciliation.get("status") or "").upper() != "RECONCILED":
            raise ConflictError(
                "OFFICIAL_RUN_FINANCIAL_TRUTH_NOT_READY",
                "Official calculations require a usable and fully reconciled Financial Truth.",
            )

    run = CalculationRun(
        organization_id=project_version.organization_id,
        workspace_id=project_version.workspace_id,
        project_id=project_version.project_id,
        project_version_id=project_version.id,
        scenario_id=scenario.id if scenario else None,
        policy_pack_version_id=policy_version.id,
        valuation_policy_pack_version_id=valuation_policy_version.id,
        replayed_from_run_id=replayed_from_run_id,
        mode=mode.value,
        status=output_status,
        case_id=str(envelope.get("case_id", generated_case_id)),
        description=description,
        application_version=APPLICATION_VERSION,
        calculation_model_version=str(output.get("calculation_model_version", ENGINE_VERSION)),
        input_schema_version=str(envelope.get("application_contract_version") or envelope.get("schema_version") or APPLICATION_INPUT_CONTRACT_VERSION),
        output_schema_version=str(output.get("schema_version")) if output.get("schema_version") else None,
        input_snapshot=envelope,
        input_hash=sha256_json(envelope),
        output_snapshot=output,
        output_hash=sha256_json(output),
        error_summary=error_summary,
        calculation_validity=readiness.calculation_validity.value,
        economic_feasibility=readiness.economic_feasibility.value,
        policy_compliance=readiness.policy_compliance.value,
        evidence_readiness=readiness.evidence_readiness.value,
        report_readiness=readiness.report_readiness.value,
        created_by_user_id=context.user_id,
        completed_at=completed_at,
    )
    session.add(run)
    session.flush()
    record_audit(
        session,
        context=context,
        action="CALCULATION_RUN_CREATED",
        entity_type="CalculationRun",
        entity_id=run.id,
        after={
            "status": run.status,
            "mode": run.mode,
            "analysis_level": normalized_analysis_level,
            "project_version_id": run.project_version_id,
            "policy_pack_version_id": run.policy_pack_version_id,
            "valuation_policy_pack_version_id": run.valuation_policy_pack_version_id,
            "scenario_id": run.scenario_id,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
            "calculation_model_version": run.calculation_model_version,
            "finance_model_version": output.get("finance_model_version", FINANCE_MODEL_VERSION),
        },
    )
    return run


def get_calculation_run(session: Session, *, context: AuthContext, run_id: str) -> CalculationRun:
    record = session.scalar(
        select(CalculationRun).where(
            CalculationRun.id == run_id,
            *tenant_clause(CalculationRun, context),
        )
    )
    if record is None:
        raise NotFoundError("Calculation run not found.")
    return record


def replay_calculation_run(
    session: Session,
    *,
    context: AuthContext,
    run_id: str,
) -> tuple[CalculationRun, bool]:
    source = get_calculation_run(session, context=context, run_id=run_id)
    new_run = create_calculation_run(
        session,
        context=context,
        project_version_id=source.project_version_id,
        policy_pack_version_id=source.policy_pack_version_id,
        valuation_policy_pack_version_id=source.valuation_policy_pack_version_id,
        scenario_id=source.scenario_id,
        mode=CalculationMode(source.mode),
        case_id=source.case_id,
        description=f"Replay of calculation run {source.id}",
        analysis_level=str((source.output_snapshot or {}).get("analysis_level") or "FULL"),
        replayed_from_run_id=source.id,
        fixed_input_snapshot=source.input_snapshot,
    )
    matches = new_run.output_hash == source.output_hash
    record_audit(
        session,
        context=context,
        action="CALCULATION_RUN_REPLAYED",
        entity_type="CalculationRun",
        entity_id=new_run.id,
        metadata={"source_run_id": source.id, "output_matches_original": matches},
    )
    return new_run, matches
