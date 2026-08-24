"""Portfolio, project, immutable-version, and scenario services."""

from __future__ import annotations

from copy import deepcopy
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..context import AuthContext
from ..errors import ConflictError, NotFoundError
from ..json_tools import sha256_json
from ..models import Portfolio, Project, ProjectVersion, Scenario, utc_now
from ..project_normalization import normalize_project_snapshot
from .snapshot_validation import validate_project_snapshot_structure
from .tenant import (
    get_portfolio,
    get_project,
    get_project_version,
    get_scenario,
    require_tenant_context,
)


def create_portfolio(
    session: Session,
    *,
    context: AuthContext,
    name: str,
    code: str,
    description: str | None,
) -> Portfolio:
    organization_id, workspace_id = require_tenant_context(context)
    if workspace_id is None:
        raise ConflictError("WORKSPACE_CONTEXT_REQUIRED", "Select a workspace to create a portfolio.")
    existing = session.scalar(
        select(Portfolio).where(Portfolio.workspace_id == workspace_id, Portfolio.code == code)
    )
    if existing is not None:
        raise ConflictError("PORTFOLIO_CODE_EXISTS", "Portfolio code already exists in this workspace.")
    record = Portfolio(
        organization_id=organization_id,
        workspace_id=workspace_id,
        name=name.strip(),
        code=code,
        description=description,
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="PORTFOLIO_CREATED",
        entity_type="Portfolio",
        entity_id=record.id,
        after={"name": record.name, "code": record.code},
    )
    return record


def update_portfolio(
    session: Session,
    *,
    context: AuthContext,
    record_id: str,
    changes: dict,
) -> Portfolio:
    record = get_portfolio(session, context, record_id)
    before = {"name": record.name, "description": record.description, "status": record.status}
    for field in ("name", "status"):
        if field in changes and changes[field] is not None:
            setattr(record, field, changes[field])
    if "description" in changes:
        record.description = changes["description"]
    session.flush()
    record_audit(
        session,
        context=context,
        action="PORTFOLIO_UPDATED",
        entity_type="Portfolio",
        entity_id=record.id,
        before=before,
        after={"name": record.name, "description": record.description, "status": record.status},
    )
    return record


def create_project(
    session: Session,
    *,
    context: AuthContext,
    name: str,
    code: str,
    description: str | None,
    portfolio_id: str | None,
    project_kind: str = "DEVELOPER",
) -> Project:
    organization_id, workspace_id = require_tenant_context(context)
    if workspace_id is None:
        raise ConflictError("WORKSPACE_CONTEXT_REQUIRED", "Select a workspace to create a project.")
    if portfolio_id is not None:
        portfolio = get_portfolio(session, context, portfolio_id)
        if portfolio.workspace_id != workspace_id:
            raise NotFoundError("Portfolio not found.")
    existing = session.scalar(
        select(Project).where(Project.workspace_id == workspace_id, Project.code == code)
    )
    if existing is not None:
        raise ConflictError("PROJECT_CODE_EXISTS", "Project code already exists in this workspace.")
    record = Project(
        organization_id=organization_id,
        workspace_id=workspace_id,
        portfolio_id=portfolio_id,
        name=name.strip(),
        code=code,
        description=description,
        project_kind=str(project_kind).upper(),
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="PROJECT_CREATED",
        entity_type="Project",
        entity_id=record.id,
        after={"name": record.name, "code": record.code, "portfolio_id": record.portfolio_id, "project_kind": record.project_kind},
    )
    return record


def update_project(
    session: Session,
    *,
    context: AuthContext,
    project_id: str,
    changes: dict,
) -> Project:
    record = get_project(session, context, project_id)
    before = {
        "name": record.name,
        "description": record.description,
        "portfolio_id": record.portfolio_id,
        "status": record.status,
    }
    if "portfolio_id" in changes and changes["portfolio_id"] is not None:
        portfolio = get_portfolio(session, context, changes["portfolio_id"])
        if portfolio.workspace_id != record.workspace_id:
            raise NotFoundError("Portfolio not found.")
    for field in ("name", "status"):
        if field in changes and changes[field] is not None:
            setattr(record, field, changes[field])
    if "description" in changes:
        record.description = changes["description"]
    if "portfolio_id" in changes:
        record.portfolio_id = changes["portfolio_id"]
    session.flush()
    record_audit(
        session,
        context=context,
        action="PROJECT_UPDATED",
        entity_type="Project",
        entity_id=record.id,
        before=before,
        after={
            "name": record.name,
            "description": record.description,
            "portfolio_id": record.portfolio_id,
            "status": record.status,
        },
    )
    return record


def _normalize_project_snapshot(project: Project, snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        raise ConflictError("PROJECT_SNAPSHOT_INVALID", "Project snapshot must be a JSON object.")
    normalized = normalize_project_snapshot(snapshot)
    validate_project_snapshot_structure(normalized)
    normalized["project_id"] = project.id
    normalized["project_name"] = project.name
    return normalized


def _next_project_version_number(session: Session, project_id: str) -> int:
    current = session.scalar(
        select(func.max(ProjectVersion.version_number)).where(ProjectVersion.project_id == project_id)
    )
    return int(current or 0) + 1


def create_project_version(
    session: Session,
    *,
    context: AuthContext,
    project_id: str,
    input_snapshot: dict,
    label: str | None,
    notes: str | None,
    supersedes_version_id: str | None,
    source_input_schema: str | None = None,
    source_input_snapshot: dict | None = None,
    source_input_hash: str | None = None,
) -> ProjectVersion:
    project = get_project(session, context, project_id)
    if supersedes_version_id is not None:
        source = get_project_version(session, context, supersedes_version_id)
        if source.project_id != project.id:
            raise NotFoundError("Superseded version not found in this project.")
    normalized = _normalize_project_snapshot(project, input_snapshot)
    record = ProjectVersion(
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        version_number=_next_project_version_number(session, project.id),
        label=label,
        notes=notes,
        input_snapshot=normalized,
        input_hash=sha256_json(normalized),
        source_input_schema=source_input_schema,
        source_input_snapshot=deepcopy(source_input_snapshot) if source_input_snapshot is not None else None,
        source_input_hash=source_input_hash,
        supersedes_version_id=supersedes_version_id,
        row_version=1,
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="PROJECT_VERSION_CREATED",
        entity_type="ProjectVersion",
        entity_id=record.id,
        after={
            "project_id": project.id,
            "version_number": record.version_number,
            "input_hash": record.input_hash,
        },
    )
    return record


def update_project_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    changes: dict,
) -> ProjectVersion:
    record = get_project_version(session, context, version_id)
    if record.status != "DRAFT":
        raise ConflictError(
            "PROJECT_VERSION_IMMUTABLE",
            "Only a draft project version can be edited. Clone an approved version first.",
        )
    expected_hash = changes.pop("expected_input_hash", None)
    if expected_hash is not None and expected_hash != record.input_hash:
        raise ConflictError(
            "PROJECT_VERSION_CONCURRENCY_CONFLICT",
            "The project version changed after it was loaded. Reload before saving.",
        )
    before = {
        "label": record.label,
        "notes": record.notes,
        "input_hash": record.input_hash,
    }
    if changes.get("input_snapshot") is not None:
        project = get_project(session, context, record.project_id)
        record.input_snapshot = _normalize_project_snapshot(project, changes["input_snapshot"])
        record.input_hash = sha256_json(record.input_snapshot)
        record.row_version = int(record.row_version or 0) + 1
    if "source_input_schema" in changes:
        record.source_input_schema = changes["source_input_schema"]
    if "source_input_snapshot" in changes:
        record.source_input_snapshot = deepcopy(changes["source_input_snapshot"]) if changes["source_input_snapshot"] is not None else None
    if "label" in changes:
        record.label = changes["label"]
    if "notes" in changes:
        record.notes = changes["notes"]
    session.flush()
    record_audit(
        session,
        context=context,
        action="PROJECT_VERSION_UPDATED",
        entity_type="ProjectVersion",
        entity_id=record.id,
        before=before,
        after={"label": record.label, "notes": record.notes, "input_hash": record.input_hash},
    )
    return record


def approve_project_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
) -> ProjectVersion:
    record = get_project_version(session, context, version_id)
    if record.status != "DRAFT":
        raise ConflictError("PROJECT_VERSION_NOT_DRAFT", "Only a draft version can be approved.")
    record.status = "APPROVED"
    record.approved_by_user_id = context.user_id
    record.approved_at = utc_now()
    session.flush()
    scenarios = session.scalars(
        select(Scenario).where(Scenario.project_version_id == record.id, Scenario.status == "DRAFT")
    ).all()
    for scenario in scenarios:
        scenario.status = "LOCKED"
    session.flush()
    record_audit(
        session,
        context=context,
        action="PROJECT_VERSION_APPROVED",
        entity_type="ProjectVersion",
        entity_id=record.id,
        after={"status": record.status, "approved_at": record.approved_at.isoformat()},
    )
    return record


def clone_project_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    label: str | None,
    notes: str | None,
) -> ProjectVersion:
    source = get_project_version(session, context, version_id)
    project = get_project(session, context, source.project_id)
    clone = create_project_version(
        session,
        context=context,
        project_id=project.id,
        input_snapshot=source.input_snapshot,
        label=label or f"Clone of v{source.version_number}",
        notes=notes,
        supersedes_version_id=source.id,
        source_input_schema=source.source_input_schema,
        source_input_snapshot=source.source_input_snapshot,
        source_input_hash=source.source_input_hash,
    )
    source_scenarios = session.scalars(
        select(Scenario).where(Scenario.project_version_id == source.id, Scenario.status != "ARCHIVED")
    ).all()
    for source_scenario in source_scenarios:
        scenario = Scenario(
            organization_id=clone.organization_id,
            workspace_id=clone.workspace_id,
            project_version_id=clone.id,
            name=source_scenario.name,
            code=source_scenario.code,
            description=source_scenario.description,
            status="DRAFT",
            override_snapshot=deepcopy(source_scenario.override_snapshot),
            override_hash=source_scenario.override_hash,
            created_by_user_id=context.user_id,
        )
        session.add(scenario)
    session.flush()
    record_audit(
        session,
        context=context,
        action="PROJECT_VERSION_CLONED",
        entity_type="ProjectVersion",
        entity_id=clone.id,
        metadata={"source_version_id": source.id},
    )
    return clone



def _validate_scenario_override(override_snapshot: dict) -> None:
    if not isinstance(override_snapshot, dict):
        raise ConflictError("SCENARIO_OVERRIDE_INVALID", "Scenario override must be a JSON object.")
    forbidden = sorted({"project_id", "project_name"}.intersection(override_snapshot))
    if forbidden:
        raise ConflictError(
            "SCENARIO_IDENTITY_OVERRIDE_FORBIDDEN",
            f"Scenario overrides cannot replace project identity field(s): {', '.join(forbidden)}.",
        )


def create_scenario(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    name: str,
    code: str,
    description: str | None,
    override_snapshot: dict,
) -> Scenario:
    version = get_project_version(session, context, version_id)
    if version.status != "DRAFT":
        raise ConflictError(
            "PROJECT_VERSION_IMMUTABLE",
            "Scenarios can be changed only on a draft project version.",
        )
    _validate_scenario_override(override_snapshot)
    existing = session.scalar(
        select(Scenario).where(Scenario.project_version_id == version.id, Scenario.code == code)
    )
    if existing is not None:
        raise ConflictError("SCENARIO_CODE_EXISTS", "Scenario code already exists in this project version.")
    record = Scenario(
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
        project_version_id=version.id,
        name=name.strip(),
        code=code,
        description=description,
        override_snapshot=deepcopy(override_snapshot),
        override_hash=sha256_json(override_snapshot),
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="SCENARIO_CREATED",
        entity_type="Scenario",
        entity_id=record.id,
        after={"name": record.name, "code": record.code, "override_hash": record.override_hash},
    )
    return record


def update_scenario(
    session: Session,
    *,
    context: AuthContext,
    scenario_id: str,
    changes: dict,
) -> Scenario:
    record = get_scenario(session, context, scenario_id)
    version = get_project_version(session, context, record.project_version_id)
    if version.status != "DRAFT":
        raise ConflictError(
            "PROJECT_VERSION_IMMUTABLE",
            "Scenarios can be changed only on a draft project version.",
        )
    before = {
        "name": record.name,
        "description": record.description,
        "status": record.status,
        "override_hash": record.override_hash,
    }
    for field in ("name", "description", "status"):
        if field in changes and changes[field] is not None:
            setattr(record, field, changes[field])
    if changes.get("override_snapshot") is not None:
        _validate_scenario_override(changes["override_snapshot"])
        record.override_snapshot = deepcopy(changes["override_snapshot"])
        record.override_hash = sha256_json(record.override_snapshot)
    session.flush()
    record_audit(
        session,
        context=context,
        action="SCENARIO_UPDATED",
        entity_type="Scenario",
        entity_id=record.id,
        before=before,
        after={
            "name": record.name,
            "description": record.description,
            "status": record.status,
            "override_hash": record.override_hash,
        },
    )
    return record
