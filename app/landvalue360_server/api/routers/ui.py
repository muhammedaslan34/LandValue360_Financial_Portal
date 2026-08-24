"""Browser application aggregation endpoints.

These endpoints do not introduce alternative business logic. They aggregate
existing persisted records and expose the canonical draft template used by the
non-technical web workflow.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Request

from landvalue360_valuation import VALUATION_MODEL_VERSION
from landvalue360_common.versions import ENGINE_VERSION, FINANCE_MODEL_VERSION
from landvalue360_government.registries import registry_snapshot

from ... import __version__
from ...context import AuthContext
from ...enums import ProjectKind
from ...models import Membership, Organization, PolicyPack, PolicyPackVersion, Project, ProjectVersion, User, Workspace
from ...services.policies import policy_applies_to, policy_is_effective, policy_option_payload, policy_type
from ...services.tenant import require_tenant_context, tenant_clause
from ...web_defaults import default_project_snapshot, project_template_catalog
from ..dependencies import get_current_context, get_session

router = APIRouter(prefix="/api/v1/ui", tags=["Browser application"])


@router.get("/bootstrap")
def bootstrap_ui(
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    organization_id, workspace_id = require_tenant_context(context)
    projects = list(
        session.scalars(
            select(Project)
            .where(
                *tenant_clause(Project, context),
                Project.status == "ACTIVE",
                Project.project_kind.in_([ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value]),
            )
            .order_by(Project.updated_at.desc(), Project.name)
        ).all()
    )
    packs_statement = select(PolicyPack).where(
        PolicyPack.organization_id == organization_id,
        PolicyPack.status == "ACTIVE",
    )
    if workspace_id is not None:
        packs_statement = packs_statement.where(
            or_(PolicyPack.workspace_id.is_(None), PolicyPack.workspace_id == workspace_id)
        )
    packs = list(session.scalars(packs_statement.order_by(PolicyPack.name)).all())
    policy_options: list[dict] = []
    valuation_policy_options: list[dict] = []
    for pack in packs:
        versions = list(
            session.scalars(
                select(PolicyPackVersion)
                .where(PolicyPackVersion.policy_pack_id == pack.id)
                .order_by(PolicyPackVersion.version_number.desc())
            ).all()
        )
        for version in versions:
            if not policy_is_effective(version):
                continue
            if not policy_applies_to(version.policy_snapshot, "DEVELOPER"):
                continue
            payload = policy_option_payload(pack, version)
            if policy_type(version.policy_snapshot) == "PROJECT":
                policy_options.append(payload)
            elif policy_type(version.policy_snapshot) == "VALUATION":
                valuation_policy_options.append(payload)

    return {
        "application": {
            "name": "LandValue360 Developer",
            "application_version": __version__,
            "calculation_model_version": ENGINE_VERSION,
            "finance_model_version": FINANCE_MODEL_VERSION,
            "valuation_model_version": VALUATION_MODEL_VERSION,
            "phase": "2.1.1 Stabilized — Developer 2.1.1 / Landowner 2.1.1 / Engine 2.1.1",
        },
        "context": {
            "user_id": context.user_id,
            "email": context.email,
            "full_name": context.full_name,
            "organization_id": context.organization_id,
            "workspace_id": context.workspace_id,
            "role": context.role,
            "is_platform_admin": context.is_platform_admin,
            "permissions": sorted(permission.value for permission in context.permissions),
        },
        "projects": [
            {
                "id": item.id,
                "name": item.name,
                "code": item.code,
                "description": item.description,
                "status": item.status,
                "portfolio_id": item.portfolio_id,
                "updated_at": item.updated_at,
                "project_kind": item.project_kind,
            }
            for item in projects
        ],
        "policy_options": policy_options,
        "valuation_policy_options": valuation_policy_options,
        "project_template": default_project_snapshot(),
        "project_templates": project_template_catalog(),
        "registries": registry_snapshot(),
    }


def _require_platform_admin(context: AuthContext) -> None:
    if not context.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access is required.")


@router.get("/admin/projects")
def admin_project_register(
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    _require_platform_admin(context)
    projects = list(session.scalars(select(Project).where(*tenant_clause(Project, context)).order_by(Project.updated_at.desc())).all())
    rows: list[dict] = []
    for project in projects:
        latest = session.scalar(select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.version_number.desc()).limit(1))
        rows.append({
            "id": project.id, "name": project.name, "code": project.code,
            "description": project.description, "project_kind": project.project_kind,
            "status": project.status, "updated_at": project.updated_at,
            "latest_version_id": latest.id if latest else None,
            "version_number": latest.version_number if latest else None,
            "version_status": latest.status if latest else None,
            "input_hash": latest.input_hash if latest else None,
        })
    return rows


@router.get("/admin/registries")
def admin_registry_snapshot(
    context: AuthContext = Depends(get_current_context),
) -> dict:
    _require_platform_admin(context)
    return registry_snapshot()


@router.get("/admin/diagnostics")
def admin_diagnostics(
    request: Request,
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    _require_platform_admin(context)
    counts = {
        "organizations": session.scalar(select(func.count()).select_from(Organization)) or 0,
        "workspaces": session.scalar(select(func.count()).select_from(Workspace)) or 0,
        "users": session.scalar(select(func.count()).select_from(User)) or 0,
        "memberships": session.scalar(select(func.count()).select_from(Membership)) or 0,
        "projects": session.scalar(select(func.count()).select_from(Project)) or 0,
        "project_versions": session.scalar(select(func.count()).select_from(ProjectVersion)) or 0,
        "policy_versions": session.scalar(select(func.count()).select_from(PolicyPackVersion)) or 0,
    }
    return {
        "status": "READY",
        "application_version": __version__,
        "calculation_model_version": ENGINE_VERSION,
        "finance_model_version": FINANCE_MODEL_VERSION,
        "valuation_model_version": VALUATION_MODEL_VERSION,
        "database": request.app.state.database.engine.dialect.name,
        "counts": counts,
        "project_workspace_model": "1.0.0",
    }
