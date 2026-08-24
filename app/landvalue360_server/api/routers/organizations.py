from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...models import AuditEvent, CalculationRun, Membership, Organization, Project, ProjectVersion, User, Workspace
from ...schemas import (
    AdministrationMemberCreate,
    MembershipCreate,
    MembershipOut,
    OrganizationCreate,
    OrganizationOut,
    UserCreate,
    UserOut,
    WorkspaceCreate,
    WorkspaceOut,
)
from ...services.organizations import (
    add_membership,
    create_organization,
    create_tenant_user,
    create_workspace,
)
from ...services.tenant import assert_organization_access, require_tenant_context, tenant_clause
from ..dependencies import get_current_context, get_session, require_permission

router = APIRouter(prefix="/api/v1", tags=["Organizations and access"])


@router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> list[Organization]:
    if context.is_platform_admin:
        return list(session.scalars(select(Organization).order_by(Organization.name)).all())
    organization_id, _ = require_tenant_context(context)
    record = session.get(Organization, organization_id)
    return [record] if record else []


@router.post("/organizations", response_model=OrganizationOut, status_code=201)
def post_organization(
    payload: OrganizationCreate,
    context: AuthContext = Depends(require_permission(Permission.ORGANIZATION_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> Organization:
    return create_organization(
        session,
        context=context,
        name=payload.name,
        slug=payload.slug,
        default_currency=payload.default_currency,
    )


@router.get("/organizations/{organization_id}/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    organization_id: str,
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> list[Workspace]:
    assert_organization_access(context, organization_id)
    statement = select(Workspace).where(Workspace.organization_id == organization_id)
    if not context.is_platform_admin and context.workspace_id is not None:
        statement = statement.where(Workspace.id == context.workspace_id)
    return list(session.scalars(statement.order_by(Workspace.name)).all())


@router.post(
    "/organizations/{organization_id}/workspaces",
    response_model=WorkspaceOut,
    status_code=201,
)
def post_workspace(
    organization_id: str,
    payload: WorkspaceCreate,
    context: AuthContext = Depends(require_permission(Permission.WORKSPACE_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> Workspace:
    return create_workspace(
        session,
        context=context,
        organization_id=organization_id,
        name=payload.name,
        slug=payload.slug,
    )


@router.post("/administration/members", status_code=201)
def post_administration_member(
    payload: AdministrationMemberCreate,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.USER_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Create a user and membership in one database transaction.

    If membership validation fails, the request transaction is rolled back so
    the administrator is not left with an orphaned login account.
    """

    if not context.is_platform_admin and Permission.MEMBERSHIP_MANAGE not in context.permissions:
        from ...errors import AuthorizationError
        raise AuthorizationError("Membership-management permission is required.")
    user = create_tenant_user(
        session,
        settings=request.app.state.settings,
        context=context,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        is_platform_admin=payload.is_platform_admin,
    )
    membership = add_membership(
        session,
        context=context,
        organization_id=payload.organization_id,
        user_id=user.id,
        workspace_id=payload.workspace_id,
        role=payload.role,
        product_access=payload.product_access,
    )
    return {"user": UserOut.model_validate(user), "membership": MembershipOut.model_validate(membership)}


@router.post("/users", response_model=UserOut, status_code=201)
def post_user(
    payload: UserCreate,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.USER_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> User:
    return create_tenant_user(
        session,
        settings=request.app.state.settings,
        context=context,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        is_platform_admin=payload.is_platform_admin,
    )


@router.get("/users", response_model=list[UserOut])
def list_users(
    context: AuthContext = Depends(require_permission(Permission.USER_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> list[User]:
    if context.is_platform_admin and context.organization_id is None:
        return list(session.scalars(select(User).order_by(User.email)).all())
    organization_id, workspace_id = require_tenant_context(context)
    statement = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.organization_id == organization_id, Membership.is_active.is_(True))
    )
    if workspace_id is not None:
        statement = statement.where(
            (Membership.workspace_id == workspace_id) | (Membership.workspace_id.is_(None))
        )
    return list(session.scalars(statement.distinct().order_by(User.email)).all())


@router.post(
    "/organizations/{organization_id}/memberships",
    response_model=MembershipOut,
    status_code=201,
)
def post_membership(
    organization_id: str,
    payload: MembershipCreate,
    context: AuthContext = Depends(require_permission(Permission.MEMBERSHIP_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> Membership:
    return add_membership(
        session,
        context=context,
        organization_id=organization_id,
        user_id=payload.user_id,
        workspace_id=payload.workspace_id,
        role=payload.role,
        product_access=payload.product_access,
    )


@router.get(
    "/organizations/{organization_id}/memberships",
    response_model=list[MembershipOut],
)
def list_memberships(
    organization_id: str,
    context: AuthContext = Depends(require_permission(Permission.MEMBERSHIP_MANAGE)),
    session: Session = Depends(get_session, scope="function"),
) -> list[Membership]:
    assert_organization_access(context, organization_id)
    statement = select(Membership).where(Membership.organization_id == organization_id)
    if not context.is_platform_admin and context.workspace_id is not None:
        statement = statement.where(
            (Membership.workspace_id == context.workspace_id) | (Membership.workspace_id.is_(None))
        )
    return list(session.scalars(statement.order_by(Membership.created_at)).all())


@router.get("/administration/projects")
def administration_projects(
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """Return all projects visible to the administrator, across both editions.

    This read-only endpoint powers the Administration project register.  It does
    not alter project ownership or the edition-specific calculation workflow.
    """

    statement = select(Project)
    if not (context.is_platform_admin and context.organization_id is None):
        statement = statement.where(*tenant_clause(Project, context))
    projects = list(session.scalars(statement.order_by(Project.updated_at.desc(), Project.name)).all())
    rows: list[dict] = []
    for project in projects:
        latest = session.scalar(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id)
            .order_by(ProjectVersion.version_number.desc())
            .limit(1)
        )
        rows.append({
            "id": project.id,
            "name": project.name,
            "code": project.code,
            "project_kind": project.project_kind,
            "status": project.status,
            "updated_at": project.updated_at,
            "latest_version_id": latest.id if latest else None,
            "latest_version_number": latest.version_number if latest else None,
            "latest_version_status": latest.status if latest else None,
        })
    return rows


@router.get("/administration/diagnostics")
def administration_diagnostics(
    request: Request,
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    """Return a compact, read-only operational diagnostic snapshot."""

    if not context.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform super administrator access is required for diagnostics.")

    from landvalue360_common.versions import ENGINE_VERSION
    from ... import __version__
    from landvalue360_common.versions import ENGINE_VERSION

    def count(model):
        statement = select(func.count()).select_from(model)
        if hasattr(model, "organization_id") and not (context.is_platform_admin and context.organization_id is None):
            statement = statement.where(*tenant_clause(model, context))
        return int(session.scalar(statement) or 0)

    latest_audits_statement = select(AuditEvent)
    if not (context.is_platform_admin and context.organization_id is None):
        organization_id, workspace_id = require_tenant_context(context)
        latest_audits_statement = latest_audits_statement.where(AuditEvent.organization_id == organization_id)
        if workspace_id is not None:
            latest_audits_statement = latest_audits_statement.where(
                (AuditEvent.workspace_id == workspace_id) | (AuditEvent.workspace_id.is_(None))
            )
    latest_audits = list(session.scalars(latest_audits_statement.order_by(AuditEvent.occurred_at.desc()).limit(12)).all())
    engine = request.app.state.database.engine
    migration_versions: list[str] = []
    integrity_status = "UNKNOWN"
    integrity_detail = "Integrity check is not available for the configured database."
    try:
        with engine.connect() as connection:
            try:
                migration_versions = [
                    str(value)
                    for value in connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
                ]
            except Exception:
                # ``create_schema`` test databases intentionally have no Alembic
                # version table. Production launchers always execute migrations.
                migration_versions = []
            if engine.dialect.name == "sqlite":
                integrity_detail = str(connection.execute(text("PRAGMA integrity_check")).scalar_one())
                integrity_status = "PASS" if integrity_detail.lower() == "ok" else "FAIL"
            else:
                connection.execute(text("SELECT 1"))
                integrity_status = "PASS"
                integrity_detail = "Database connection and simple query succeeded."
    except Exception as exc:  # read-only diagnostic; do not hide the failure
        integrity_status = "FAIL"
        integrity_detail = f"{type(exc).__name__}: {exc}"

    recent_errors = [
        row for row in latest_audits
        if any(token in str(row.action or "").upper() for token in ("ERROR", "FAIL", "REJECT"))
    ]
    return {
        "status": "READY",
        "application_version": __version__,
        "calculation_model_version": ENGINE_VERSION,
        "engine_version": ENGINE_VERSION,
        "database": request.app.state.database.engine.dialect.name,
        "database_status": "READY" if integrity_status == "PASS" else "ATTENTION",
        "migration": {
            "current": migration_versions,
            "head": "0011_detailed_only_cleanup",
            "status": "CURRENT" if migration_versions == ["0011_detailed_only_cleanup"] else "SCHEMA_CREATED_OR_MIGRATION_REQUIRED",
        },
        "integrity": {"status": integrity_status, "detail": integrity_detail},
        "cache": {
            "status": "STATELESS",
            "rebuild_required": False,
            "detail": "Financial results are persisted calculation runs; no mutable financial-result cache is used.",
        },
        "maintenance_commands": {
            "migrate": "START_LANDVALUE360.bat (runs alembic upgrade head automatically)",
            "backup": "python -m landvalue360_server.cli backup --output <archive.lv360backup>",
            "verify_backup": "python -m landvalue360_server.cli verify-backup <archive.lv360backup>",
            "restore": "python -m landvalue360_server.cli restore <archive.lv360backup> --force",
            "cache_rebuild": "Not applicable: restart the runtime to reload static assets and policy registries.",
        },
        "counts": {
            "projects": count(Project),
            "project_versions": count(ProjectVersion),
            "calculation_runs": count(CalculationRun),
            "audit_events": count(AuditEvent),
        },
        "latest_audit_events": [
            {
                "occurred_at": row.occurred_at,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "actor_user_id": row.actor_user_id,
            }
            for row in latest_audits
        ],
        "recent_error_events": [
            {
                "occurred_at": row.occurred_at,
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "actor_user_id": row.actor_user_id,
            }
            for row in recent_errors
        ],
    }
