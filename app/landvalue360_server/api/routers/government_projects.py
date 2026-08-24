"""Government-owned project routes with governed, detailed inputs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission, ProjectKind
from ...errors import NotFoundError
from ...models import ProjectVersion
from ...schemas import (
    GovernmentCaseOut,
    GovernmentProjectAssessmentRequest,
    GovernmentProjectPreviewRequest,
    GovernmentProjectCaseCreate,
    GovernmentProjectCreate,
    GovernmentProjectDetailOut,
    GovernmentProjectSummaryOut,
    GovernmentProjectUpdate,
    ProjectVersionClone,
    ProjectVersionOut,
)
from ...services.government_projects import (
    assess_government_project,
    clone_government_project_version,
    create_case_for_government_project,
    create_government_project,
    get_government_project_detail,
    government_project_template as build_government_project_template,
    list_government_project_versions,
    list_government_projects,
    preview_government_project_input,
    update_government_project,
)
from ...services.projects import approve_project_version
from ...services.tenant import get_project, get_project_version
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1/government", tags=["Government projects"])


def _detail(project, version, summary) -> GovernmentProjectDetailOut:
    return GovernmentProjectDetailOut.model_validate(
        {
            "project": project,
            "version": version,
            "derived_summary": summary,
        }
    )


@router.post("/project-preview")
def government_project_preview(
    payload: GovernmentProjectPreviewRequest,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> dict[str, Any]:
    return preview_government_project_input(
        session,
        context=context,
        project_name=payload.name,
        landowner_input=payload.input.model_dump(mode="json"),
        policy_pack_version_id=payload.policy_pack_version_id,
        partnership_method=payload.partnership_method,
        hybrid_variable_basis=payload.hybrid_variable_basis,
        offered_share_percent=payload.offered_share_percent,
    )


@router.get("/project-template")
def government_project_template(
    _context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
) -> dict[str, Any]:
    return build_government_project_template()


@router.get("/projects", response_model=list[GovernmentProjectSummaryOut])
def read_government_projects(
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict[str, Any]]:
    return list_government_projects(session, context=context)


@router.post("/projects", response_model=GovernmentProjectDetailOut, status_code=201)
def post_government_project(
    payload: GovernmentProjectCreate,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentProjectDetailOut:
    project, version, summary = create_government_project(
        session,
        context=context,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        landowner_input=payload.input.model_dump(mode="json"),
    )
    return _detail(project, version, summary)


@router.get("/projects/{project_id}", response_model=GovernmentProjectDetailOut)
def read_government_project(
    project_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentProjectDetailOut:
    project, version, summary = get_government_project_detail(session, context=context, project_id=project_id)
    return _detail(project, version, summary)


@router.get("/projects/{project_id}/versions", response_model=list[ProjectVersionOut])
def read_government_project_versions(
    project_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[ProjectVersion]:
    return list_government_project_versions(session, context=context, project_id=project_id)


@router.post("/project-versions/{version_id}/clone", response_model=GovernmentProjectDetailOut, status_code=201)
def clone_government_project_revision(
    version_id: str,
    payload: ProjectVersionClone,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentProjectDetailOut:
    project, version, summary = clone_government_project_version(
        session,
        context=context,
        version_id=version_id,
        label=payload.label,
        notes=payload.notes,
    )
    return _detail(project, version, summary)


@router.patch("/projects/{project_id}", response_model=GovernmentProjectDetailOut)
def patch_government_project(
    project_id: str,
    payload: GovernmentProjectUpdate,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentProjectDetailOut:
    project, version, summary = update_government_project(
        session,
        context=context,
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        landowner_input=payload.input.model_dump(mode="json") if payload.input is not None else None,
        expected_input_hash=payload.expected_input_hash,
    )
    return _detail(project, version, summary)


@router.post("/project-versions/{version_id}/assess")
def post_government_assessment(
    version_id: str,
    payload: GovernmentProjectAssessmentRequest,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> dict[str, Any]:
    return assess_government_project(
        session,
        context=context,
        version_id=version_id,
        policy_version_id=payload.policy_pack_version_id,
        valuation_policy_version_id=payload.valuation_policy_pack_version_id,
        mode=payload.mode,
        partnership_method=payload.partnership_method,
        offered_share_percent=payload.offered_share_percent,
        upfront_amount=payload.upfront_amount,
        public_discount_rate_percent=payload.public_discount_rate_percent,
    )


@router.post("/project-versions/{version_id}/cases", response_model=GovernmentCaseOut, status_code=201)
def post_government_project_case(
    version_id: str,
    payload: GovernmentProjectCaseCreate,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
):
    return create_case_for_government_project(
        session,
        context=context,
        version_id=version_id,
        policy_version_id=payload.policy_pack_version_id,
        valuation_policy_version_id=payload.valuation_policy_pack_version_id,
        case_code=payload.case_code,
        title=payload.title,
        mode=payload.mode,
        partnership_method=payload.partnership_method,
        offered_share_percent=payload.offered_share_percent,
        upfront_amount=payload.upfront_amount,
        public_discount_rate_percent=payload.public_discount_rate_percent,
    )


@router.post("/project-versions/{version_id}/approve", response_model=dict)
def approve_government_project_baseline(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_APPROVE)),
    session: Session = Depends(get_session, scope="function"),
) -> dict[str, Any]:
    version: ProjectVersion = get_project_version(session, context, version_id)
    project = get_project(session, context, version.project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Landowner project version not found.")
    approved = approve_project_version(session, context=context, version_id=version.id)
    return {
        "id": approved.id,
        "project_id": approved.project_id,
        "version_number": approved.version_number,
        "status": approved.status,
        "input_hash": approved.input_hash,
        "approved_at": approved.approved_at,
    }
