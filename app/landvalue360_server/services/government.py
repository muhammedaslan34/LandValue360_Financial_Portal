"""Landowner interface case orchestration and maker-checker controls."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from landvalue360_government.decision import prepare_government_project, run_government_decision
from landvalue360_government.hashing import sha256_json
from landvalue360_government.reports import (
    REPORT_PURPOSES,
    REPORT_TYPES,
    render_html_to_pdf,
    render_report,
)

from ..audit import record_audit
from ..context import AuthContext
from ..enums import CalculationMode, GovernmentCaseStatus, ProjectKind
from ..errors import ConflictError, NotFoundError
from ..models import (
    GovernmentCase,
    OverrideRecord,
    PolicyPack,
    PolicyPackVersion,
    Project,
    ProjectVersion,
    Scenario,
    User,
    utc_now,
)
from .calculations import compose_calculation_envelope, create_calculation_run, _compose_governed_policy
from .policies import policy_applies_to, policy_is_effective, policy_option_payload, policy_type, require_operational_policy
from .tenant import (
    get_policy_version,
    get_project,
    get_project_version,
    get_scenario,
    require_tenant_context,
    tenant_clause,
)


def _case_hash(record: GovernmentCase | None, *, mode: str, input_snapshot: dict[str, Any]) -> str:
    return sha256_json({"mode": mode, "input_snapshot": input_snapshot})


def get_government_case(session: Session, context: AuthContext, case_id: str) -> GovernmentCase:
    record = session.scalar(
        select(GovernmentCase).where(
            GovernmentCase.id == case_id,
            *tenant_clause(GovernmentCase, context),
        )
    )
    if record is None:
        raise NotFoundError("Government case not found.")
    return record


def list_government_cases(
    session: Session,
    *,
    context: AuthContext,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[GovernmentCase]:
    statement = select(GovernmentCase).where(*tenant_clause(GovernmentCase, context))
    if status:
        statement = statement.where(GovernmentCase.status == status.upper())
    statement = statement.order_by(GovernmentCase.updated_at.desc()).offset(offset).limit(limit)
    return list(session.scalars(statement).all())


def _validate_links(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    scenario_id: str | None,
) -> tuple[ProjectVersion, PolicyPackVersion, Scenario | None, Project]:
    project_version = get_project_version(session, context, project_version_id)
    policy_version = require_operational_policy(
        get_policy_version(session, context, policy_pack_version_id), edition="LANDOWNER", expected_type="PROJECT"
    )
    scenario = get_scenario(session, context, scenario_id) if scenario_id else None
    if scenario is not None and scenario.project_version_id != project_version.id:
        raise ConflictError("SCENARIO_VERSION_MISMATCH", "Scenario does not belong to the selected project version.")
    project = get_project(session, context, project_version.project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Landowner project version not found.")
    return project_version, policy_version, scenario, project


def _valuation_policy_for_case(
    session: Session,
    *,
    context: AuthContext,
    record: GovernmentCase,
    case_input: dict[str, Any],
) -> PolicyPackVersion:
    """Resolve the explicit valuation policy, including migrated legacy cases."""

    policy_sources = case_input.get("assessment_policy_sources") or {}
    version_id = (
        record.valuation_policy_pack_version_id
        or policy_sources.get("valuation_policy_version_id")
        or policy_sources.get("valuation_policy_pack_version_id")
    )
    if not version_id:
        raise ConflictError(
            "GOVERNMENT_CASE_VALUATION_POLICY_REQUIRED",
            "The case does not record a valuation-policy version and cannot be calculated without an explicit migration.",
        )
    return require_operational_policy(
        get_policy_version(session, context, version_id),
        edition="LANDOWNER",
        expected_type="VALUATION",
    )


def create_government_case(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    policy_pack_version_id: str,
    valuation_policy_pack_version_id: str,
    scenario_id: str | None,
    case_code: str,
    title: str,
    mode: str,
    input_snapshot: dict[str, Any],
) -> GovernmentCase:
    organization_id, workspace_id = require_tenant_context(context)
    if workspace_id is None:
        raise ConflictError("WORKSPACE_CONTEXT_REQUIRED", "Select a workspace to create a government case.")
    project_version, _policy, _scenario, project = _validate_links(
        session,
        context=context,
        project_version_id=project_version_id,
        policy_pack_version_id=policy_pack_version_id,
        scenario_id=scenario_id,
    )
    valuation_policy = require_operational_policy(
        get_policy_version(session, context, valuation_policy_pack_version_id),
        edition="LANDOWNER",
        expected_type="VALUATION",
    )
    existing = session.scalar(
        select(GovernmentCase).where(
            GovernmentCase.workspace_id == workspace_id,
            GovernmentCase.case_code == case_code,
        )
    )
    if existing is not None:
        raise ConflictError("GOVERNMENT_CASE_CODE_EXISTS", "Case code already exists in this workspace.")
    normalized_input = deepcopy(input_snapshot or {})
    normalized_mode = mode.upper()
    record = GovernmentCase(
        organization_id=organization_id,
        workspace_id=workspace_id,
        project_id=project.id,
        project_version_id=project_version.id,
        policy_pack_version_id=policy_pack_version_id,
        valuation_policy_pack_version_id=valuation_policy.id,
        scenario_id=scenario_id,
        case_code=case_code,
        title=title.strip(),
        mode=normalized_mode,
        status=GovernmentCaseStatus.DRAFT.value,
        input_snapshot=normalized_input,
        input_hash=_case_hash(None, mode=normalized_mode, input_snapshot=normalized_input),
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_CREATED",
        entity_type="GovernmentCase",
        entity_id=record.id,
        after={
            "case_code": record.case_code,
            "mode": record.mode,
            "status": record.status,
            "project_version_id": record.project_version_id,
            "policy_pack_version_id": record.policy_pack_version_id,
            "valuation_policy_pack_version_id": record.valuation_policy_pack_version_id,
            "input_hash": record.input_hash,
        },
    )
    return record


def update_government_case(
    session: Session,
    *,
    context: AuthContext,
    case_id: str,
    changes: dict[str, Any],
) -> GovernmentCase:
    record = get_government_case(session, context, case_id)
    if record.status != GovernmentCaseStatus.DRAFT.value:
        raise ConflictError("GOVERNMENT_CASE_IMMUTABLE", "Only a draft government case can be edited.")
    before = {
        "title": record.title,
        "mode": record.mode,
        "scenario_id": record.scenario_id,
        "input_hash": record.input_hash,
    }
    if "scenario_id" in changes:
        scenario_id = changes["scenario_id"]
        if scenario_id:
            scenario = get_scenario(session, context, scenario_id)
            if scenario.project_version_id != record.project_version_id:
                raise ConflictError("SCENARIO_VERSION_MISMATCH", "Scenario does not belong to the selected project version.")
        record.scenario_id = scenario_id
    if changes.get("title") is not None:
        record.title = changes["title"].strip()
    if changes.get("mode") is not None:
        record.mode = str(changes["mode"]).upper()
    if changes.get("input_snapshot") is not None:
        record.input_snapshot = deepcopy(changes["input_snapshot"])
    record.input_hash = _case_hash(record, mode=record.mode, input_snapshot=record.input_snapshot)
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_UPDATED",
        entity_type="GovernmentCase",
        entity_id=record.id,
        before=before,
        after={
            "title": record.title,
            "mode": record.mode,
            "scenario_id": record.scenario_id,
            "input_hash": record.input_hash,
        },
    )
    return record


def _set_path(target: dict[str, Any], path: str, value: Any) -> Any:
    parts = [part for part in path.split(".") if part]
    if not parts:
        raise ConflictError("OVERRIDE_PATH_INVALID", "Override field path is empty.")
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        child = cursor.get(part)
        if child is None:
            child = {}
            cursor[part] = child
        if not isinstance(child, dict):
            raise ConflictError("OVERRIDE_PATH_INVALID", f"Override path component '{part}' is not an object.")
        cursor = child
    previous = deepcopy(cursor.get(parts[-1]))
    cursor[parts[-1]] = deepcopy(value)
    return previous


def create_override(
    session: Session,
    *,
    context: AuthContext,
    case_id: str,
    field_path: str,
    new_value: Any,
    reason: str,
    document_reference: str,
) -> tuple[GovernmentCase, OverrideRecord]:
    record = get_government_case(session, context, case_id)
    if record.status != GovernmentCaseStatus.DRAFT.value:
        raise ConflictError("OVERRIDE_CASE_LOCKED", "Overrides can be recorded only while the case is a draft.")
    snapshot = deepcopy(record.input_snapshot)
    previous = _set_path(snapshot, field_path, new_value)
    record.input_snapshot = snapshot
    record.input_hash = _case_hash(record, mode=record.mode, input_snapshot=snapshot)
    override = OverrideRecord(
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        government_case_id=record.id,
        field_path=field_path,
        previous_value=previous,
        new_value=deepcopy(new_value),
        reason=reason.strip(),
        document_reference=document_reference.strip(),
        created_by_user_id=context.user_id,
    )
    session.add(override)
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_OVERRIDE_RECORDED",
        entity_type="OverrideRecord",
        entity_id=override.id,
        before={"field_path": field_path, "value": previous},
        after={"field_path": field_path, "value": new_value, "case_input_hash": record.input_hash},
        metadata={"reason": reason, "document_reference": document_reference},
    )
    return record, override


def list_overrides(session: Session, *, context: AuthContext, case_id: str) -> list[OverrideRecord]:
    record = get_government_case(session, context, case_id)
    statement = (
        select(OverrideRecord)
        .where(
            OverrideRecord.government_case_id == record.id,
            *tenant_clause(OverrideRecord, context),
        )
        .order_by(OverrideRecord.created_at)
    )
    return list(session.scalars(statement).all())


def _project_for_case(
    project_version: ProjectVersion,
    scenario: Scenario | None,
    case_input: dict[str, Any],
) -> dict[str, Any]:
    """Apply the linked scenario and materialize the case contract once."""

    base = deepcopy(project_version.input_snapshot)
    snapshot = json_merge_patch(base, scenario.override_snapshot) if scenario is not None else base
    snapshot["project_id"] = base.get("project_id")
    snapshot["project_name"] = base.get("project_name")
    materialized, _contract = prepare_government_project(snapshot, case_input)
    return materialized


def preview_government_case(session: Session, *, context: AuthContext, case_id: str) -> dict[str, Any]:
    record = get_government_case(session, context, case_id)
    project_version, policy_version, scenario, _project = _validate_links(
        session,
        context=context,
        project_version_id=record.project_version_id,
        policy_pack_version_id=record.policy_pack_version_id,
        scenario_id=record.scenario_id,
    )
    case_input = deepcopy(record.input_snapshot)
    case_input["mode"] = record.mode
    valuation_policy = _valuation_policy_for_case(
        session,
        context=context,
        record=record,
        case_input=case_input,
    )
    governed_policy = _compose_governed_policy(
        policy_version.policy_snapshot,
        valuation_policy.policy_snapshot,
    )
    project_snapshot = _project_for_case(project_version, scenario, case_input)
    result = run_government_decision(project_snapshot, governed_policy, case_input)
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_PREVIEWED",
        entity_type="GovernmentCase",
        entity_id=record.id,
        metadata={"input_hash": record.input_hash, "output_hash": result.get("output_hash")},
    )
    return result



def _closure_failure_message(decision: dict[str, Any], *, prefix: str) -> str:
    """Return a compact bilingual explanation of every blocking closure check."""

    closure = decision.get("closure") or {}
    failed = closure.get("failed") or []
    if not failed:
        return prefix
    parts: list[str] = []
    for item in failed[:6]:
        identifier = str(item.get("code") or item.get("id") or "UNKNOWN")
        actual = item.get("actual")
        required = item.get("required")
        reason_ar = str(item.get("reason_ar") or identifier)
        reason_en = str(item.get("reason_en") or identifier)
        action_ar = str(item.get("corrective_action_ar") or "راجع المدخلات وأعد تشغيل التحليل.")
        action_en = str(item.get("corrective_action_en") or item.get("corrective_action") or "Review the inputs and rerun the assessment.")
        text = (
            f"{identifier} — {reason_ar} القيمة الحالية: {actual}; المطلوب: {required}. "
            f"الإجراء: {action_ar} / {reason_en} Actual: {actual}; required: {required}. Action: {action_en}"
        )
        subchecks = item.get("failed_subchecks") or []
        if subchecks:
            sub_ids = ", ".join(str(row.get("id") or row.get("label") or "UNKNOWN") for row in subchecks[:5])
            text += f"; الاختبارات الفرعية الفاشلة / failed sub-checks: {sub_ids}"
        parts.append(text)
    return f"{prefix} " + " | ".join(parts)


def _execute_official_approval(
    session: Session,
    *,
    context: AuthContext,
    record: GovernmentCase,
    notes: str,
    audit_action: str,
    workflow_mode: str,
) -> GovernmentCase:
    project_version, policy_version, scenario, _project = _validate_links(
        session,
        context=context,
        project_version_id=record.project_version_id,
        policy_pack_version_id=record.policy_pack_version_id,
        scenario_id=record.scenario_id,
    )
    if project_version.status != "APPROVED":
        raise ConflictError("OFFICIAL_RUN_REQUIRES_APPROVED_PROJECT_VERSION", "Approve the linked project version first.")
    if policy_version.status != "PUBLISHED":
        raise ConflictError("OFFICIAL_RUN_REQUIRES_PUBLISHED_POLICY", "Publish the linked policy version first.")

    case_input = deepcopy(record.input_snapshot)
    case_input["mode"] = record.mode
    valuation_policy_version = _valuation_policy_for_case(
        session,
        context=context,
        record=record,
        case_input=case_input,
    )
    governed_policy = _compose_governed_policy(
        policy_version.policy_snapshot,
        valuation_policy_version.policy_snapshot,
    )
    project_snapshot = _project_for_case(project_version, scenario, case_input)
    envelope = compose_calculation_envelope(
        project_snapshot=project_snapshot,
        policy_snapshot=deepcopy(policy_version.policy_snapshot),
        valuation_policy_snapshot=deepcopy(valuation_policy_version.policy_snapshot),
        case_id=record.case_code,
        description=f"Government advisory decision case {record.case_code}",
    )
    # The Government decision already performs the contract range, scenarios and
    # reconciliation.  The linked CalculationRun is therefore a contract-specific
    # STANDARD provenance run, not a second generic optimization/solver pass.
    calc = create_calculation_run(
        session,
        context=context,
        project_version_id=record.project_version_id,
        policy_pack_version_id=record.policy_pack_version_id,
        valuation_policy_pack_version_id=valuation_policy_version.id,
        scenario_id=record.scenario_id,
        mode=CalculationMode.OFFICIAL,
        case_id=record.case_code,
        description=f"Government advisory decision case {record.case_code}",
        analysis_level="STANDARD",
        fixed_input_snapshot=envelope,
        optimize_share=False,
    )
    if calc.status == "FAILED":
        raise ConflictError("GOVERNMENT_OFFICIAL_CALCULATION_FAILED", calc.error_summary or "Official calculation failed.")
    decision = run_government_decision(
        project_snapshot,
        governed_policy,
        case_input,
        calculation_run_id=calc.id,
    )
    if not (decision.get("closure") or {}).get("passed"):
        raise ConflictError(
            "GOVERNMENT_CASE_CLOSURE_FAILED",
            _closure_failure_message(decision, prefix="Official decision did not pass all closure checks."),
        )
    record.calculation_run_id = calc.id
    record.output_snapshot = decision
    record.output_hash = str(decision.get("output_hash") or sha256_json(decision))
    record.ledger_hash = decision.get("ledger_hash")
    record.status = GovernmentCaseStatus.APPROVED.value
    record.approved_by_user_id = context.user_id
    record.approved_at = utc_now()
    record.approval_notes = notes.strip()
    record.locked_at = utc_now()
    session.flush()
    record_audit(
        session,
        context=context,
        action=audit_action,
        entity_type="GovernmentCase",
        entity_id=record.id,
        after={
            "status": record.status,
            "calculation_run_id": record.calculation_run_id,
            "input_hash": record.input_hash,
            "output_hash": record.output_hash,
            "ledger_hash": record.ledger_hash,
            "approver": context.user_id,
            "workflow_mode": workflow_mode,
        },
    )
    return record


def submit_government_case(session: Session, *, context: AuthContext, case_id: str) -> GovernmentCase:
    record = get_government_case(session, context, case_id)
    if record.status != GovernmentCaseStatus.DRAFT.value:
        raise ConflictError("GOVERNMENT_CASE_NOT_DRAFT", "Only a draft case can be submitted.")
    # Execute a deterministic preview at submission so invalid or non-closing cases cannot enter review silently.
    preview = preview_government_case(session, context=context, case_id=record.id)
    if not (preview.get("closure") or {}).get("passed"):
        raise ConflictError(
            "GOVERNMENT_CASE_CLOSURE_FAILED",
            _closure_failure_message(preview, prefix="Case cannot be submitted because mandatory closure checks failed."),
        )
    record.status = GovernmentCaseStatus.SUBMITTED.value
    record.submitted_by_user_id = context.user_id
    record.submitted_at = utc_now()
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_SUBMITTED",
        entity_type="GovernmentCase",
        entity_id=record.id,
        after={"status": record.status, "input_hash": record.input_hash, "preview_output_hash": preview.get("output_hash")},
    )
    return record


def _assert_checker(record: GovernmentCase, context: AuthContext, *, stage: str) -> None:
    used = {record.created_by_user_id, record.submitted_by_user_id}
    if stage in {"LEGAL", "APPROVAL"}:
        used.add(record.technical_reviewer_user_id)
    if stage == "APPROVAL":
        used.add(record.legal_reviewer_user_id)
    if context.user_id in {value for value in used if value}:
        raise ConflictError(
            "MAKER_CHECKER_SEPARATION_REQUIRED",
            "The maker, submitter, technical reviewer, legal reviewer and approver must be separate users.",
        )


def technical_review_government_case(
    session: Session, *, context: AuthContext, case_id: str, notes: str
) -> GovernmentCase:
    record = get_government_case(session, context, case_id)
    if record.status != GovernmentCaseStatus.SUBMITTED.value:
        raise ConflictError("GOVERNMENT_CASE_NOT_SUBMITTED", "Technical review requires a submitted case.")
    _assert_checker(record, context, stage="TECHNICAL")
    record.status = GovernmentCaseStatus.TECHNICALLY_REVIEWED.value
    record.technical_reviewer_user_id = context.user_id
    record.technical_reviewed_at = utc_now()
    record.technical_review_notes = notes.strip()
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_TECHNICALLY_REVIEWED",
        entity_type="GovernmentCase",
        entity_id=record.id,
        after={"status": record.status, "reviewer": context.user_id, "notes": notes},
    )
    return record


def legal_review_government_case(
    session: Session, *, context: AuthContext, case_id: str, notes: str
) -> GovernmentCase:
    record = get_government_case(session, context, case_id)
    if record.status != GovernmentCaseStatus.TECHNICALLY_REVIEWED.value:
        raise ConflictError("GOVERNMENT_CASE_NOT_TECHNICALLY_REVIEWED", "Legal review follows technical review.")
    _assert_checker(record, context, stage="LEGAL")
    record.status = GovernmentCaseStatus.LEGALLY_REVIEWED.value
    record.legal_reviewer_user_id = context.user_id
    record.legal_reviewed_at = utc_now()
    record.legal_review_notes = notes.strip()
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_LEGALLY_REVIEWED",
        entity_type="GovernmentCase",
        entity_id=record.id,
        after={"status": record.status, "reviewer": context.user_id, "notes": notes},
    )
    return record


def approve_government_case(
    session: Session, *, context: AuthContext, case_id: str, notes: str
) -> GovernmentCase:
    record = get_government_case(session, context, case_id)
    if record.status != GovernmentCaseStatus.LEGALLY_REVIEWED.value:
        raise ConflictError("GOVERNMENT_CASE_NOT_LEGALLY_REVIEWED", "Approval follows legal review.")
    _assert_checker(record, context, stage="APPROVAL")
    return _execute_official_approval(
        session,
        context=context,
        record=record,
        notes=notes,
        audit_action="GOVERNMENT_CASE_APPROVED",
        workflow_mode="institutional",
    )




def finalize_government_case(
    session: Session,
    *,
    context: AuthContext,
    case_id: str,
    notes: str,
    workflow_mode: str,
) -> GovernmentCase:
    """Lock an advisory result and make its reports available in simple mode.

    This is deliberately not an institutional approval.  The one-user workflow
    stores the deterministic preview, its hashes and an explicit READY status
    without claiming a maker-checker review or creating an official run.
    """

    if workflow_mode != "direct":
        raise ConflictError(
            "SIMPLE_WORKFLOW_DISABLED",
            "Direct approval is disabled. Use the institutional review workflow configured for this deployment.",
        )
    record = get_government_case(session, context, case_id)
    if record.status not in {GovernmentCaseStatus.DRAFT.value, GovernmentCaseStatus.SUBMITTED.value}:
        raise ConflictError("GOVERNMENT_CASE_NOT_FINALIZABLE", "Only a draft or submitted case can be completed.")
    decision = preview_government_case(session, context=context, case_id=case_id)
    if record.submitted_by_user_id is None:
        record.submitted_by_user_id = context.user_id
        record.submitted_at = utc_now()
    record.technical_reviewer_user_id = None
    record.technical_reviewed_at = None
    record.technical_review_notes = "Not applicable: direct one-user advisory analysis."
    record.legal_reviewer_user_id = None
    record.legal_reviewed_at = None
    record.legal_review_notes = "Not applicable: direct one-user advisory analysis."
    record.calculation_run_id = None
    record.output_snapshot = decision
    record.output_hash = str(decision.get("output_hash") or sha256_json(decision))
    record.ledger_hash = decision.get("ledger_hash")
    record.status = GovernmentCaseStatus.READY.value
    record.approved_by_user_id = None
    record.approved_at = None
    record.approval_notes = notes.strip()
    record.locked_at = utc_now()
    session.flush()
    record_audit(
        session,
        context=context,
        action="GOVERNMENT_CASE_REPORTS_READY",
        entity_type="GovernmentCase",
        entity_id=record.id,
        after={
            "status": record.status,
            "input_hash": record.input_hash,
            "output_hash": record.output_hash,
            "ledger_hash": record.ledger_hash,
            "completed_by": context.user_id,
            "workflow_mode": workflow_mode,
            "institutional_approval": False,
        },
    )
    return record


def official_government_result(session: Session, *, context: AuthContext, case_id: str) -> dict[str, Any]:
    record = get_government_case(session, context, case_id)
    if record.status not in {GovernmentCaseStatus.READY.value, GovernmentCaseStatus.APPROVED.value} or record.output_snapshot is None:
        raise ConflictError("GOVERNMENT_CASE_NOT_READY", "The advisory result is available after analysis completion.")
    return deepcopy(record.output_snapshot)


def government_report(
    session: Session,
    *,
    context: AuthContext,
    case_id: str,
    report_type: str,
    language: str,
) -> str:
    record = get_government_case(session, context, case_id)
    if record.status not in {GovernmentCaseStatus.READY.value, GovernmentCaseStatus.APPROVED.value} or record.output_snapshot is None:
        raise ConflictError("GOVERNMENT_CASE_NOT_READY", "Reports are available after analysis completion.")
    report_type = {
        "comprehensive-advisory-report": "technical-financial-report",
        "technical-analysis-report": "technical-financial-report",
    }.get(report_type, report_type)
    if report_type not in REPORT_TYPES:
        raise NotFoundError("Government report type not found.")
    project_version = get_project_version(session, context, record.project_version_id)
    policy_version = get_policy_version(session, context, record.policy_pack_version_id)
    user_ids = [value for value in [record.technical_reviewer_user_id, record.legal_reviewer_user_id, record.approved_by_user_id] if value]
    users = {user.id: user.full_name for user in session.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    reviewer = " / ".join(
        filter(
            None,
            [
                users.get(record.technical_reviewer_user_id or ""),
                users.get(record.legal_reviewer_user_id or ""),
            ],
        )
    )
    if record.status == GovernmentCaseStatus.READY.value:
        reviewer = "Not applicable - direct one-user advisory analysis"
        approver = "Not applicable - no institutional approval required"
    else:
        if not reviewer and record.approved_by_user_id:
            reviewer = "Not applicable - simple one-user workflow"
        approver = users.get(record.approved_by_user_id or "")
    project = session.get(Project, project_version.project_id)
    report_payload = deepcopy(record.output_snapshot)
    report_payload["project_name"] = project.name if project is not None else record.title
    report_payload["project_code"] = project.code if project is not None else record.case_code
    report_payload["case_reference"] = record.case_code
    report_payload["case_title"] = record.title
    return render_report(
        report_type,
        report_payload,
        language=language,
        reviewer=reviewer or None,
        approver=approver,
        project_version=f"{project_version.version_number}:{project_version.input_hash[:12]}",
        policy_version=f"{policy_version.version_label}:{policy_version.policy_hash[:12]}",
        scenario_version=record.scenario_id or "base",
    )


def government_report_pdf(
    session: Session,
    *,
    context: AuthContext,
    case_id: str,
    report_type: str,
    language: str,
) -> bytes:
    """Render the locked advisory report as a downloadable PDF."""
    html = government_report(
        session, context=context, case_id=case_id, report_type=report_type, language=language
    )
    try:
        return render_html_to_pdf(html)
    except RuntimeError as exc:  # pragma: no cover - environment specific
        raise ConflictError(
            "PDF_RENDERER_UNAVAILABLE",
            str(exc),
        ) from exc


def government_options(
    session: Session, *, context: AuthContext, workflow_mode: str
) -> dict[str, Any]:
    organization_id, workspace_id = require_tenant_context(context)
    if workspace_id is None:
        raise ConflictError("WORKSPACE_CONTEXT_REQUIRED", "Select a workspace to use Landowner interface.")
    projects = list(
        session.scalars(
            select(Project).where(
                Project.organization_id == organization_id,
                Project.workspace_id == workspace_id,
                Project.project_kind.in_([ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value]),
            ).order_by(Project.name)
        ).all()
    )
    project_versions = list(
        session.scalars(
            select(ProjectVersion).join(Project, Project.id == ProjectVersion.project_id).where(
                ProjectVersion.organization_id == organization_id,
                ProjectVersion.workspace_id == workspace_id,
                Project.project_kind.in_([ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value]),
            ).order_by(ProjectVersion.project_id, ProjectVersion.version_number.desc())
        ).all()
    )
    policy_records = list(
        session.scalars(
            select(PolicyPackVersion).where(
                PolicyPackVersion.organization_id == organization_id,
                (PolicyPackVersion.workspace_id == workspace_id) | (PolicyPackVersion.workspace_id.is_(None)),
            ).order_by(PolicyPackVersion.created_at.desc())
        ).all()
    )
    pack_ids = {row.policy_pack_id for row in policy_records}
    policy_packs = {
        row.id: row
        for row in session.scalars(select(PolicyPack).where(PolicyPack.id.in_(pack_ids))).all()
    } if pack_ids else {}
    policy_options = [
        policy_option_payload(policy_packs[row.policy_pack_id], row)
        for row in policy_records
        if row.policy_pack_id in policy_packs
        and policy_is_effective(row)
        and policy_applies_to(row.policy_snapshot, "LANDOWNER")
    ]
    return {
        "projects": [{"id": row.id, "name": row.name, "code": row.code, "status": row.status} for row in projects],
        "project_versions": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "version_number": row.version_number,
                "status": row.status,
                "label": row.label,
                "input_hash": row.input_hash,
            }
            for row in project_versions
        ],
        "policy_versions": [
            {
                "id": row["version_id"],
                "policy_pack_id": row["pack_id"],
                "pack_name": row["pack_name"],
                "pack_code": row["pack_code"],
                "version_number": row["version_number"],
                "version_label": row["version_label"],
                "status": row["status"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
                "policy_hash": row["policy_hash"],
                "product_scope": row["product_scope"],
                "policy_type": row.get("policy_type", "PROJECT"),
                "summary": row["summary"],
            }
            for row in policy_options
        ],
        "case_modes": ["STRUCTURING", "OFFER_ASSESSMENT", "BID_COMPARISON", "RENEGOTIATION"],
        "workflow": {
            "mode": workflow_mode,
            "single_user_completion": workflow_mode == "direct",
            "single_user_required_permissions": ["government:case:write", "government:run"],
            "institutional_review_available": True,
        },
        "report_types": REPORT_TYPES,
        "report_purposes": REPORT_PURPOSES,
    }
