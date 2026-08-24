"""Tenant-scoped record lookup helpers.

Cross-tenant object identifiers deliberately resolve as 404 rather than
revealing the existence of another organization's records.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ..context import AuthContext
from ..errors import AuthorizationError, NotFoundError
from ..models import (
    Organization,
    PolicyPack,
    PolicyPackVersion,
    Portfolio,
    Project,
    ProjectVersion,
    Scenario,
    Workspace,
)

T = TypeVar("T")


def require_tenant_context(context: AuthContext) -> tuple[str, str | None]:
    if context.organization_id is None:
        raise AuthorizationError("Select an organization context for this operation.")
    return context.organization_id, context.workspace_id


def assert_organization_access(context: AuthContext, organization_id: str) -> None:
    if context.is_platform_admin:
        return
    if context.organization_id != organization_id:
        raise NotFoundError()


def assert_workspace_access(context: AuthContext, workspace_id: str) -> None:
    if context.is_platform_admin:
        return
    if context.workspace_id is not None and context.workspace_id != workspace_id:
        raise NotFoundError()


def tenant_clause(model, context: AuthContext):  # noqa: ANN001
    organization_id, workspace_id = require_tenant_context(context)
    clauses = [model.organization_id == organization_id]
    if workspace_id is not None and hasattr(model, "workspace_id"):
        clauses.append(model.workspace_id == workspace_id)
    return clauses


def get_workspace(session: Session, context: AuthContext, workspace_id: str) -> Workspace:
    organization_id, _ = require_tenant_context(context)
    record = session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == organization_id,
        )
    )
    if record is None:
        raise NotFoundError("Workspace not found.")
    assert_workspace_access(context, record.id)
    return record


def get_portfolio(session: Session, context: AuthContext, record_id: str) -> Portfolio:
    record = session.scalar(select(Portfolio).where(Portfolio.id == record_id, *tenant_clause(Portfolio, context)))
    if record is None:
        raise NotFoundError("Portfolio not found.")
    return record


def get_project(session: Session, context: AuthContext, record_id: str) -> Project:
    record = session.scalar(select(Project).where(Project.id == record_id, *tenant_clause(Project, context)))
    if record is None:
        raise NotFoundError("Project not found.")
    return record


def get_project_version(session: Session, context: AuthContext, record_id: str) -> ProjectVersion:
    record = session.scalar(
        select(ProjectVersion).where(ProjectVersion.id == record_id, *tenant_clause(ProjectVersion, context))
    )
    if record is None:
        raise NotFoundError("Project version not found.")
    return record


def get_scenario(session: Session, context: AuthContext, record_id: str) -> Scenario:
    record = session.scalar(select(Scenario).where(Scenario.id == record_id, *tenant_clause(Scenario, context)))
    if record is None:
        raise NotFoundError("Scenario not found.")
    return record


def get_policy_pack(session: Session, context: AuthContext, record_id: str) -> PolicyPack:
    organization_id, workspace_id = require_tenant_context(context)
    statement = select(PolicyPack).where(
        PolicyPack.id == record_id,
        PolicyPack.organization_id == organization_id,
    )
    record = session.scalar(statement)
    if record is None:
        raise NotFoundError("Policy pack not found.")
    if workspace_id is not None and record.workspace_id not in {None, workspace_id}:
        raise NotFoundError("Policy pack not found.")
    return record


def get_policy_version(session: Session, context: AuthContext, record_id: str) -> PolicyPackVersion:
    organization_id, workspace_id = require_tenant_context(context)
    record = session.scalar(
        select(PolicyPackVersion).where(
            PolicyPackVersion.id == record_id,
            PolicyPackVersion.organization_id == organization_id,
        )
    )
    if record is None:
        raise NotFoundError("Policy version not found.")
    if workspace_id is not None and record.workspace_id not in {None, workspace_id}:
        raise NotFoundError("Policy version not found.")
    return record
