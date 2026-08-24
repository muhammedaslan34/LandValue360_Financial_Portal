from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission, ProjectKind
from ...errors import ConflictError, NotFoundError
from ...models import Portfolio, Project, ProjectVersion, Scenario
from ...project_contract import ProjectContractError, parse_project_contract_xlsx, project_contract_xlsx
from ...project_package import export_project_package, import_project_package
from ...request_limits import read_limited_body
from ...schemas import (
    PortfolioCreate,
    PortfolioOut,
    PortfolioUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    ProjectVersionClone,
    ProjectPackageImportOut,
    ProjectVersionCreate,
    ProjectVersionOut,
    ProjectVersionUpdate,
    ScenarioCreate,
    ScenarioOut,
    ScenarioUpdate,
)
from ...services.projects import (
    approve_project_version,
    clone_project_version,
    create_portfolio,
    create_project,
    create_project_version,
    create_scenario,
    update_portfolio,
    update_project,
    update_project_version,
    update_scenario,
)
from ...services.tenant import (
    get_portfolio,
    get_project,
    get_project_version,
    get_scenario,
    tenant_clause,
)
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1", tags=["Portfolios and projects"])


def _latest_project_version(session: Session, project_id: str) -> ProjectVersion | None:
    return session.scalar(
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project_id)
        .order_by(ProjectVersion.version_number.desc())
        .limit(1)
    )


def _linked_developer_project_map(session: Session, context: AuthContext) -> dict[str, Project]:
    mapping: dict[str, Project] = {}
    projects = list(
        session.scalars(
            select(Project).where(
                *tenant_clause(Project, context),
                Project.project_kind.in_([ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value]),
                Project.status == "ACTIVE",
            )
        ).all()
    )
    for project in projects:
        version = _latest_project_version(session, project.id)
        source = (version.input_snapshot or {}).get("shared_project_source") if version else None
        source_id = str((source or {}).get("source_project_id") or "")
        if source_id:
            mapping[source_id] = project
    return mapping


def _developer_project(session: Session, context: AuthContext, project_id: str) -> Project:
    """Return a project usable by the Developer edition."""
    project = get_project(session, context, project_id)
    if project.project_kind not in {ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value}:
        raise NotFoundError("Developer project not found.")
    return project


def _developer_version(session: Session, context: AuthContext, version_id: str) -> ProjectVersion:
    version = get_project_version(session, context, version_id)
    _developer_project(session, context, version.project_id)
    return version


def _developer_scenario(session: Session, context: AuthContext, scenario_id: str) -> Scenario:
    scenario = get_scenario(session, context, scenario_id)
    _developer_version(session, context, scenario.project_version_id)
    return scenario


@router.get("/portfolios", response_model=list[PortfolioOut])
def list_portfolios(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.PORTFOLIO_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[Portfolio]:
    statement = (
        select(Portfolio)
        .where(*tenant_clause(Portfolio, context))
        .order_by(Portfolio.name)
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(statement).all())


@router.post("/portfolios", response_model=PortfolioOut, status_code=201)
def post_portfolio(
    payload: PortfolioCreate,
    context: AuthContext = Depends(require_permission(Permission.PORTFOLIO_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> Portfolio:
    return create_portfolio(
        session,
        context=context,
        name=payload.name,
        code=payload.code,
        description=payload.description,
    )


@router.get("/portfolios/{portfolio_id}", response_model=PortfolioOut)
def read_portfolio(
    portfolio_id: str,
    context: AuthContext = Depends(require_permission(Permission.PORTFOLIO_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> Portfolio:
    return get_portfolio(session, context, portfolio_id)


@router.patch("/portfolios/{portfolio_id}", response_model=PortfolioOut)
def patch_portfolio(
    portfolio_id: str,
    payload: PortfolioUpdate,
    context: AuthContext = Depends(require_permission(Permission.PORTFOLIO_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> Portfolio:
    return update_portfolio(
        session,
        context=context,
        record_id=portfolio_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    portfolio_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[Project]:
    statement = select(Project).where(
        *tenant_clause(Project, context),
        Project.project_kind.in_([ProjectKind.GOVERNMENT.value, ProjectKind.DEVELOPER.value, ProjectKind.SHARED.value]),
    )
    if portfolio_id is not None:
        get_portfolio(session, context, portfolio_id)
        statement = statement.where(Project.portfolio_id == portfolio_id)
    statement = statement.order_by(Project.name).offset(offset).limit(limit)
    return list(session.scalars(statement).all())


@router.post("/projects", response_model=ProjectOut, status_code=201)
def post_project(
    payload: ProjectCreate,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> Project:
    return create_project(
        session,
        context=context,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        portfolio_id=payload.portfolio_id,
        project_kind=ProjectKind.SHARED.value,
    )






@router.get("/shared-project-sources")
def list_shared_project_sources(
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[dict]:
    """List Landowner projects that can seed a Developer analysis.

    The base project remains owned by the same workspace.  Creating a developer
    analysis produces a linked developer project/version from the canonical
    snapshot, so users do not re-enter land, products, costs and schedule data.
    """

    linked = _linked_developer_project_map(session, context)
    projects = list(
        session.scalars(
            select(Project)
            .where(
                *tenant_clause(Project, context),
                Project.project_kind == ProjectKind.GOVERNMENT.value,
                Project.status == "ACTIVE",
            )
            .order_by(Project.updated_at.desc(), Project.name)
        ).all()
    )
    rows: list[dict] = []
    for project in projects:
        latest = _latest_project_version(session, project.id)
        linked_project = linked.get(project.id)
        rows.append({
            "id": project.id,
            "name": project.name,
            "code": project.code,
            "updated_at": project.updated_at,
            "latest_version_id": latest.id if latest else None,
            "latest_version_number": latest.version_number if latest else None,
            "latest_version_status": latest.status if latest else None,
            "linked_developer_project_id": linked_project.id if linked_project else None,
            "linked_developer_project_code": linked_project.code if linked_project else None,
        })
    return rows


@router.post("/shared-project-sources/{source_project_id}/create-developer-analysis", status_code=201)
def create_developer_analysis_from_landowner(
    source_project_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    source_project = get_project(session, context, source_project_id)
    if source_project.project_kind != ProjectKind.GOVERNMENT.value:
        raise ConflictError("SHARED_SOURCE_NOT_LANDOWNER", "The selected source is not a Landowner project.")
    source_version = _latest_project_version(session, source_project.id)
    if source_version is None:
        raise ConflictError("SHARED_SOURCE_HAS_NO_VERSION", "The Landowner project has no saved version.")

    existing = _linked_developer_project_map(session, context).get(source_project.id)
    if existing is not None:
        latest = _latest_project_version(session, existing.id)
        return {"project": ProjectOut.model_validate(existing), "version": ProjectVersionOut.model_validate(latest) if latest else None, "created": False}

    base_code = f"{source_project.code}-DEV"[:72]
    code = base_code
    suffix = 2
    while session.scalar(select(Project).where(Project.workspace_id == source_project.workspace_id, Project.code == code)) is not None:
        code = f"{base_code[:68]}-{suffix}"
        suffix += 1

    project = create_project(
        session,
        context=context,
        name=source_project.name,
        code=code,
        description=(source_project.description or "") + "\nLinked Developer analysis created from the Landowner project.",
        portfolio_id=source_project.portfolio_id,
        project_kind=ProjectKind.SHARED.value,
    )
    snapshot = deepcopy(source_version.input_snapshot or {})
    snapshot["shared_project_source"] = {
        "source_project_id": source_project.id,
        "source_project_version_id": source_version.id,
        "source_project_version_number": source_version.version_number,
        "source_project_kind": source_project.project_kind,
    }
    version = create_project_version(
        session,
        context=context,
        project_id=project.id,
        input_snapshot=snapshot,
        label=f"Developer analysis from {source_project.code} v{source_version.version_number}",
        notes="Canonical project data shared from the Landowner Edition; developer-only assumptions may be expanded in this draft.",
        supersedes_version_id=None,
        source_input_schema=source_version.source_input_schema or "SHARED_LANDOWNER_CANONICAL",
        source_input_snapshot=source_version.source_input_snapshot,
        source_input_hash=source_version.source_input_hash,
    )
    return {"project": ProjectOut.model_validate(project), "version": ProjectVersionOut.model_validate(version), "created": True}


@router.get("/projects/{project_id}/export.lv360")
def export_portable_project(
    project_id: str,
    include_reference_results: bool = Query(default=True),
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from fastapi.responses import Response

    project = _developer_project(session, context, project_id)
    payload = export_project_package(
        session, context=context, project_id=project.id, include_reference_results=include_reference_results
    )
    filename = f"{project.code}-LandValue360.lv360".replace(" ", "-")
    return Response(
        content=payload,
        media_type="application/vnd.landvalue360.project+zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/projects/import.lv360", response_model=ProjectPackageImportOut, status_code=201)
async def import_portable_project(
    request: Request,
    name: str | None = Query(default=None, max_length=240),
    code: str | None = Query(default=None, max_length=80),
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    settings = request.app.state.settings
    payload = await read_limited_body(
        request,
        max_bytes=settings.max_project_package_bytes,
        error_code="PROJECT_PACKAGE_TOO_LARGE",
        error_message="The uploaded project package exceeds the configured size limit.",
    )
    return import_project_package(
        session,
        context=context,
        payload=payload,
        name_override=name,
        code_override=code,
        max_payload_bytes=settings.max_project_package_bytes,
        max_uncompressed_bytes=settings.max_project_package_uncompressed_bytes,
        max_entries=settings.max_project_package_entries,
        max_compression_ratio=settings.max_project_package_compression_ratio,
        max_json_depth=settings.max_json_depth,
    )


@router.get("/projects/{project_id}", response_model=ProjectOut)
def read_project(
    project_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> Project:
    return _developer_project(session, context, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: str,
    payload: ProjectUpdate,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> Project:
    _developer_project(session, context, project_id)
    return update_project(
        session,
        context=context,
        project_id=project_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.get("/projects/{project_id}/versions", response_model=list[ProjectVersionOut])
def list_project_versions(
    project_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[ProjectVersion]:
    project = _developer_project(session, context, project_id)
    statement = (
        select(ProjectVersion)
        .where(ProjectVersion.project_id == project.id, *tenant_clause(ProjectVersion, context))
        .order_by(ProjectVersion.version_number.desc())
    )
    return list(session.scalars(statement).all())


@router.post(
    "/projects/{project_id}/versions",
    response_model=ProjectVersionOut,
    status_code=201,
)
def post_project_version(
    project_id: str,
    payload: ProjectVersionCreate,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> ProjectVersion:
    _developer_project(session, context, project_id)
    return create_project_version(
        session,
        context=context,
        project_id=project_id,
        input_snapshot=payload.input_snapshot,
        label=payload.label,
        notes=payload.notes,
        supersedes_version_id=payload.supersedes_version_id,
    )


@router.get("/project-versions/{version_id}", response_model=ProjectVersionOut)
def read_project_version(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> ProjectVersion:
    return _developer_version(session, context, version_id)


@router.patch("/project-versions/{version_id}", response_model=ProjectVersionOut)
def patch_project_version(
    version_id: str,
    payload: ProjectVersionUpdate,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> ProjectVersion:
    _developer_version(session, context, version_id)
    return update_project_version(
        session,
        context=context,
        version_id=version_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post("/project-versions/{version_id}/approve", response_model=ProjectVersionOut)
def post_approve_project_version(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_APPROVE)),
    session: Session = Depends(get_session, scope="function"),
) -> ProjectVersion:
    _developer_version(session, context, version_id)
    return approve_project_version(session, context=context, version_id=version_id)


@router.post(
    "/project-versions/{version_id}/clone",
    response_model=ProjectVersionOut,
    status_code=201,
)
def post_clone_project_version(
    version_id: str,
    payload: ProjectVersionClone,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> ProjectVersion:
    _developer_version(session, context, version_id)
    return clone_project_version(
        session,
        context=context,
        version_id=version_id,
        label=payload.label,
        notes=payload.notes,
    )


@router.get("/project-versions/{version_id}/input-contract.xlsx")
def export_project_input_contract(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from fastapi.responses import Response

    version = _developer_version(session, context, version_id)
    project = _developer_project(session, context, version.project_id)
    payload = project_contract_xlsx(
        snapshot=version.input_snapshot,
        project_id=project.id,
        project_name=project.name,
        version_id=version.id,
        version_number=version.version_number,
    )
    filename = f"landvalue360-{project.code}-v{version.version_number}-input-contract.xlsx".replace(" ", "-")
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/projects/{project_id}/versions/import-contract.xlsx",
    response_model=ProjectVersionOut,
    status_code=201,
)
def import_project_input_contract(
    project_id: str,
    request: Request,
    payload: bytes = Body(media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    label: str | None = Query(default=None, max_length=240),
    notes: str | None = Query(default=None, max_length=20000),
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> ProjectVersion:
    _developer_project(session, context, project_id)
    if len(payload) > request.app.state.settings.max_excel_import_bytes:
        raise ConflictError("PROJECT_CONTRACT_TOO_LARGE", "The Excel input contract exceeds the configured size limit.")
    try:
        snapshot = parse_project_contract_xlsx(payload)
    except ProjectContractError as exc:
        raise ConflictError("PROJECT_CONTRACT_INVALID", str(exc)) from exc
    return create_project_version(
        session,
        context=context,
        project_id=project_id,
        input_snapshot=snapshot,
        label=label or "Imported Excel contract",
        notes=notes or "Created from a LandValue360 v0.5 project-input workbook.",
        supersedes_version_id=None,
    )


@router.get("/project-versions/{version_id}/scenarios", response_model=list[ScenarioOut])
def list_scenarios(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[Scenario]:
    version = _developer_version(session, context, version_id)
    statement = (
        select(Scenario)
        .where(Scenario.project_version_id == version.id, *tenant_clause(Scenario, context))
        .order_by(Scenario.name)
    )
    return list(session.scalars(statement).all())


@router.post(
    "/project-versions/{version_id}/scenarios",
    response_model=ScenarioOut,
    status_code=201,
)
def post_scenario(
    version_id: str,
    payload: ScenarioCreate,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> Scenario:
    _developer_version(session, context, version_id)
    return create_scenario(
        session,
        context=context,
        version_id=version_id,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        override_snapshot=payload.override_snapshot,
    )


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def read_scenario(
    scenario_id: str,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> Scenario:
    return _developer_scenario(session, context, scenario_id)


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def patch_scenario(
    scenario_id: str,
    payload: ScenarioUpdate,
    context: AuthContext = Depends(require_permission(Permission.PROJECT_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> Scenario:
    _developer_scenario(session, context, scenario_id)
    return update_scenario(
        session,
        context=context,
        scenario_id=scenario_id,
        changes=payload.model_dump(exclude_unset=True),
    )
