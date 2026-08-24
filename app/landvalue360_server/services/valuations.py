"""Valuation-run orchestration and data-quality preview."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from landvalue360_valuation import VALUATION_MODEL_VERSION, ValuationError, calculate_valuation
from landvalue360_valuation.engine import calculate_data_quality

from ..audit import record_audit
from ..context import AuthContext
from ..enums import CalculationMode
from ..errors import ConflictError, NotFoundError
from ..json_tools import sha256_json
from ..models import AssumptionRecord, CalculationRun, EvidenceDocument, PolicyPackVersion, ProjectVersion, ValuationRun, utc_now
from .assumptions import list_assumptions
from .calculations import get_calculation_run
from .evidence import list_evidence
from .tenant import get_policy_version, get_project_version, tenant_clause


def _evidence_snapshot(rows: list[EvidenceDocument]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "project_id": item.project_id,
            "project_version_id": item.project_version_id,
            "evidence_type": item.evidence_type,
            "title": item.title,
            "original_filename": item.original_filename,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
            "content_hash": item.content_hash,
            "status": item.status,
            "source_name": item.source_name,
            "source_reference": item.source_reference,
            "issue_date": item.issue_date.isoformat() if item.issue_date else None,
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
            "verified_by_user_id": item.verified_by_user_id,
            "verified_at": item.verified_at.isoformat() if item.verified_at else None,
        }
        for item in rows
    ]


def _assumption_snapshot(rows: list[AssumptionRecord]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "project_version_id": item.project_version_id,
            "assumption_key": item.assumption_key,
            "label": item.label,
            "category": item.category,
            "value_snapshot": deepcopy(item.value_snapshot),
            "unit": item.unit,
            "criticality": item.criticality,
            "source_type": item.source_type,
            "source_reference": item.source_reference,
            "evidence_document_ids": list(item.evidence_document_ids or []),
            "evidence_status": item.evidence_status,
            "confidence_score": item.confidence_score,
            "approval_status": item.approval_status,
            "notes": item.notes,
            "reviewed_by_user_id": item.reviewed_by_user_id,
            "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        }
        for item in rows
    ]


def preview_data_quality(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    valuation_date: date | None = None,
) -> dict[str, Any]:
    version = get_project_version(session, context, project_version_id)
    evidence = list_evidence(
        session,
        context=context,
        project_id=version.project_id,
        project_version_id=version.id,
    )
    assumptions = list_assumptions(session, context=context, project_version_id=version.id)
    as_of = valuation_date or date.fromisoformat(str(version.input_snapshot.get("valuation_date") or date.today().isoformat())[:10])
    output = calculate_data_quality(
        _evidence_snapshot(evidence),
        _assumption_snapshot(assumptions),
        valuation_date=as_of.isoformat(),
    )
    output.update(
        {
            "project_version_id": version.id,
            "project_id": version.project_id,
            "valuation_date": as_of.isoformat(),
            "evidence_count": len(evidence),
            "assumption_count": len(assumptions),
        }
    )
    return output


def create_valuation_run(
    session: Session,
    *,
    context: AuthContext,
    payload: dict[str, Any],
) -> ValuationRun:
    calculation = get_calculation_run(session, context=context, run_id=payload["calculation_run_id"])
    if calculation.status not in {"SUCCESS", "SUCCESS_WITH_WARNINGS"} or not calculation.output_snapshot:
        raise ConflictError("VALUATION_REQUIRES_SUCCESSFUL_CALCULATION", "Valuation requires a successful calculation run.")
    project_version = get_project_version(session, context, calculation.project_version_id)
    policy_version = get_policy_version(session, context, calculation.policy_pack_version_id)
    mode = CalculationMode(payload.get("mode", CalculationMode.PREVIEW))
    if mode == CalculationMode.OFFICIAL:
        if project_version.status != "APPROVED":
            raise ConflictError("OFFICIAL_VALUATION_REQUIRES_APPROVED_PROJECT", "Official valuation requires an approved project version.")
        if policy_version.status != "PUBLISHED":
            raise ConflictError("OFFICIAL_VALUATION_REQUIRES_PUBLISHED_POLICY", "Official valuation requires a published policy version.")

    evidence_rows = list_evidence(
        session,
        context=context,
        project_id=calculation.project_id,
        project_version_id=project_version.id,
    )
    assumption_rows = list_assumptions(session, context=context, project_version_id=project_version.id)
    request = deepcopy(payload)
    request.pop("calculation_run_id", None)
    if request.get("valuation_date") is None:
        request["valuation_date"] = str((calculation.output_snapshot or {}).get("valuation_date"))
    elif hasattr(request["valuation_date"], "isoformat"):
        request["valuation_date"] = request["valuation_date"].isoformat()
    request["mode"] = mode.value
    request["project_context"] = deepcopy(project_version.input_snapshot.get("valuation_context") or {})
    evidence_snapshot = _evidence_snapshot(evidence_rows)
    assumption_snapshot = _assumption_snapshot(assumption_rows)
    valuation_input = {
        "calculation_run_id": calculation.id,
        "calculation_input_hash": calculation.input_hash,
        "calculation_output_hash": calculation.output_hash,
        "project_version_id": project_version.id,
        "project_version_hash": project_version.input_hash,
        "policy_pack_version_id": policy_version.id,
        "policy_hash": policy_version.policy_hash,
        "request": request,
        "evidence": evidence_snapshot,
        "assumptions": assumption_snapshot,
    }
    try:
        output = calculate_valuation(
            calculation_output=deepcopy(calculation.output_snapshot),
            policy_snapshot=deepcopy(policy_version.policy_snapshot),
            request=request,
            evidence=evidence_snapshot,
            assumptions=assumption_snapshot,
        )
    except ValuationError as exc:
        raise ConflictError("VALUATION_NOT_CALCULABLE", str(exc)) from exc

    if mode == CalculationMode.OFFICIAL:
        data_quality = output.get("data_quality", {})
        critical_missing = data_quality.get("critical_missing") or []
        critical_not_verified = data_quality.get("critical_not_verified") or []
        if critical_missing:
            raise ConflictError(
                "OFFICIAL_VALUATION_CRITICAL_EVIDENCE_MISSING",
                "Official valuation cannot be issued while critical evidence is missing: " + ", ".join(critical_missing),
            )
        if critical_not_verified:
            raise ConflictError(
                "OFFICIAL_VALUATION_CRITICAL_EVIDENCE_UNVERIFIED",
                "Official valuation requires verified critical evidence: " + ", ".join(critical_not_verified),
            )
        thresholds = output.get("governance_thresholds") or {}
        minimum_quality = float(thresholds.get("feasibility_readiness") or 70)
        if float(data_quality.get("score") or 0) < minimum_quality:
            raise ConflictError(
                "OFFICIAL_VALUATION_DATA_QUALITY_BELOW_THRESHOLD",
                f"Official valuation requires a data-quality score of at least {minimum_quality:g}.",
            )
        required_methods = int(thresholds.get("minimum_reconciliation_methods") or 2)
        if int(output.get("reconciliation", {}).get("method_count") or 0) < required_methods:
            raise ConflictError(
                "OFFICIAL_VALUATION_REQUIRES_MULTIPLE_METHODS",
                f"Official valuation reconciliation requires at least {required_methods} available methods.",
            )

    valuation_date = date.fromisoformat(str(request["valuation_date"])[:10])
    run = ValuationRun(
        organization_id=calculation.organization_id,
        workspace_id=calculation.workspace_id,
        project_id=calculation.project_id,
        project_version_id=project_version.id,
        calculation_run_id=calculation.id,
        policy_pack_version_id=policy_version.id,
        scenario_id=calculation.scenario_id,
        mode=mode.value,
        status=str(output.get("status") or "FAILED"),
        basis_of_value=str(output.get("basis_of_value")),
        purpose=str(output.get("purpose")),
        valuation_date=valuation_date,
        reporting_currency=str(output.get("reporting_currency") or "USD"),
        valuation_model_version=VALUATION_MODEL_VERSION,
        input_snapshot=valuation_input,
        input_hash=sha256_json(valuation_input),
        output_snapshot=output,
        output_hash=sha256_json(output),
        created_by_user_id=context.user_id,
        completed_at=utc_now(),
    )
    session.add(run)
    session.flush()
    record_audit(
        session,
        context=context,
        action="VALUATION_RUN_CREATED",
        entity_type="ValuationRun",
        entity_id=run.id,
        after={
            "project_version_id": run.project_version_id,
            "calculation_run_id": run.calculation_run_id,
            "mode": run.mode,
            "status": run.status,
            "basis_of_value": run.basis_of_value,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
            "reconciled_value": output.get("reconciliation", {}).get("reconciled_value"),
            "data_quality_score": output.get("data_quality", {}).get("score"),
            "institutional_readiness_grade": output.get("institutional_readiness", {}).get("grade"),
        },
    )
    return run


def get_valuation_run(session: Session, *, context: AuthContext, run_id: str) -> ValuationRun:
    record = session.scalar(
        select(ValuationRun).where(
            ValuationRun.id == run_id,
            *tenant_clause(ValuationRun, context),
        )
    )
    if record is None:
        raise NotFoundError("Valuation run not found.")
    return record


def list_valuation_runs(
    session: Session,
    *,
    context: AuthContext,
    project_id: str | None,
    limit: int,
    offset: int,
) -> list[ValuationRun]:
    statement = select(ValuationRun).where(*tenant_clause(ValuationRun, context))
    if project_id:
        statement = statement.where(ValuationRun.project_id == project_id)
    return list(session.scalars(statement.order_by(ValuationRun.created_at.desc()).offset(offset).limit(limit)).all())
