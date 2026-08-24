from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calculations import calculate_project
from ..config import get_settings
from ..database import get_db
from ..models import (
    InformationRequest, InformationRequestMessage, Organization, OrganizationMember, Project,
    ProjectDocument, ProjectVersion, Report, ReportDownload, ReportVersion, User, FileTypePolicy, utcnow
)
from ..packages import export_excel, export_internal_package, export_portal_package
from ..schemas import ProjectDraftIn
from ..security import apply_rls_context, csrf_protect, current_session, current_user, user_permission_codes
from ..services import (
    audit, create_project, create_revision, current_version, may_view_project, persist_snapshot,
    record_analysis_export, require_project, snapshot_from_db, submit_project, user_org_ids
)
from ..storage import get_storage, sha256_bytes, validate_upload
from ..web import templates

router = APIRouter()


def _project_json(db: Session, project: Project) -> dict[str, Any]:
    version = current_version(db, project)
    result = calculate_project(version.input_snapshot or {})
    documents = list(db.scalars(select(ProjectDocument).where(
        ProjectDocument.project_id == project.id,
        ProjectDocument.project_version_id == version.id,
        ProjectDocument.deleted_at.is_(None),
    ).order_by(ProjectDocument.created_at.desc())).all())
    return {
        "id": project.id, "reference": project.reference, "name": project.name, "description": project.description,
        "status": project.status, "priority": project.priority, "version_id": version.id,
        "version_number": version.version_number, "immutable": version.immutable,
        "completeness_percent": str(version.completeness_percent), "snapshot": version.input_snapshot,
        "calculations": result,
        "documents": [{
            "id": row.id, "category": row.category, "name": row.original_name,
            "size_bytes": row.size_bytes, "sha256": row.sha256, "scan_status": row.scan_status,
        } for row in documents],
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"title": "LandValue360"})


@router.get("/portal", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    org_ids = user_org_ids(db, user.id)
    projects = list(db.scalars(select(Project).where(Project.organization_id.in_(org_ids), Project.deleted_at.is_(None)).order_by(Project.updated_at.desc())).all()) if org_ids else []
    return templates.TemplateResponse(request, "dashboard.html", {"title": "مشروعاتي", "user": user, "projects": projects})


@router.get("/portal/projects/new", response_class=HTMLResponse)
def new_project_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    orgs = list(db.scalars(select(Organization).where(Organization.id.in_(user_org_ids(db, user.id)), Organization.deleted_at.is_(None))).all())
    return templates.TemplateResponse(request, "new_project.html", {"title": "مشروع جديد", "user": user, "organizations": orgs})


@router.get("/portal/projects/{project_id}", response_class=HTMLResponse)
def project_editor(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    permissions = user_permission_codes(db, user.id)
    admin_access = "admin.projects" in permissions and project.organization_id not in user_org_ids(db, user.id)
    if admin_access:
        audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="ADMIN_PROJECT_VIEWED", entity_type="PROJECT", entity_id=project.id, ip=request.client.host if request.client else None)
        db.commit()
    return templates.TemplateResponse(request, "project_editor.html", {
        "title": project.name, "user": user, "project": project,
        "project_data": _project_json(db, project), "admin_access": admin_access,
    })


@router.get("/portal/projects/{project_id}/status", response_class=HTMLResponse)
def project_status(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    info_requests = list(db.scalars(select(InformationRequest).where(InformationRequest.project_id == project.id).order_by(InformationRequest.created_at.desc())).all())
    published = list(db.execute(
        select(Report, ReportVersion)
        .join(ReportVersion, ReportVersion.id == Report.current_version_id)
        .where(Report.project_id == project.id, Report.status == "PUBLISHED", ReportVersion.status == "PUBLISHED")
    ).all())
    return templates.TemplateResponse(request, "project_status.html", {"title": "حالة المشروع", "user": user, "project": project, "info_requests": info_requests, "published_reports": published})


@router.post("/api/projects", status_code=201)
def api_create_project(payload: dict, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    perms = user_permission_codes(db, user.id)
    if "project.create" not in perms:
        raise HTTPException(status_code=403, detail="Not permitted")
    try:
        project = create_project(db, user=user, organization_id=str(payload.get("organization_id")), name=str(payload.get("name") or "").strip(), description=payload.get("description"), currency=str(payload.get("currency") or "USD"))
        db.commit()
        # RLS settings are transaction-local. Reapply them before the response
        # queries the newly-created project version in a new transaction.
        apply_rls_context(db, user)
    except (ValueError, PermissionError) as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _project_json(db, project)


@router.get("/api/projects/{project_id}")
def api_get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _project_json(db, require_project(db, user, project_id))


@router.put("/api/projects/{project_id}")
def api_update_project(project_id: str, payload: ProjectDraftIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    if "project.edit_draft" not in user_permission_codes(db, user.id):
        raise HTTPException(status_code=403, detail="Not permitted")
    version = current_version(db, project)
    if version.immutable:
        raise HTTPException(status_code=409, detail="Submitted versions are immutable; create a revision")
    snapshot = {
        "identity": {"name": payload.name, "description": payload.description or "", "currency": payload.currency},
        "land": {"gross_land_area_sqm": str(payload.gross_land_area_sqm), "excluded_land_area_sqm": str(payload.excluded_land_area_sqm), "title_reference": payload.title_reference or "", "location": payload.location or "", "current_land_value": str(payload.current_land_value) if payload.current_land_value is not None else None, "currency": payload.currency},
        "planning": {"far": str(payload.far), "bcr": str(payload.bcr) if payload.bcr is not None else None, "planning_status": payload.planning_status or "", "project_duration_months": payload.project_duration_months, "sales_duration_months": payload.sales_duration_months},
        "land_uses": [row.model_dump(mode="json") for row in payload.land_uses],
        "products": [row.model_dump(mode="json") for row in payload.products],
        "costs": [row.model_dump(mode="json") for row in payload.costs],
    }
    project.name = payload.name; project.description = payload.description; project.updated_by = user.id
    try:
        calculations = persist_snapshot(db, version, snapshot, user=user)
        audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="PROJECT_DRAFT_SAVED", entity_type="PROJECT_VERSION", entity_id=version.id, after={"snapshot_hash": version.snapshot_hash}, ip=request.client.host if request.client else None)
        db.commit()
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"project": _project_json(db, project), "calculations": calculations}


@router.post("/api/projects/{project_id}/submit")
def api_submit_project(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    if "project.submit" not in user_permission_codes(db, user.id):
        raise HTTPException(status_code=403, detail="Not permitted")
    try:
        result = submit_project(db, project=project, user=user)
        db.commit()
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "status": project.status, "calculations": result}


@router.post("/api/projects/{project_id}/revisions", status_code=201)
def api_create_revision(project_id: str, payload: dict, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    reason = str(payload.get("reason") or "Client revision").strip()
    try:
        version = create_revision(db, project=project, user=user, reason=reason)
        db.commit()
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "version_id": version.id, "version_number": version.version_number}


@router.get("/api/projects/{project_id}/versions")
def api_versions(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    rows = list(db.scalars(select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.version_number.desc())).all())
    return [{"id": row.id, "version_number": row.version_number, "status": row.status, "immutable": row.immutable, "submitted_at": row.submitted_at, "snapshot_hash": row.snapshot_hash} for row in rows]


@router.post("/api/projects/{project_id}/documents", status_code=201)
async def upload_document(project_id: str, request: Request, category: str = Form(...), file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    version = current_version(db, project)
    if version.immutable:
        raise HTTPException(status_code=409, detail="Create a new revision before adding documents")
    suffix_hint = Path(file.filename or "upload").suffix.lower()
    policy = db.scalar(select(FileTypePolicy).where(FileTypePolicy.extension == suffix_hint, FileTypePolicy.active.is_(True)))
    if not policy:
        raise HTTPException(status_code=422, detail="File type is not permitted")
    max_bytes = min(get_settings().max_upload_mb * 1024 * 1024, policy.max_size_bytes)
    data = await file.read(max_bytes + 1)
    try:
        suffix, mime = validate_upload(file.filename or "upload", file.content_type, data, max_bytes=max_bytes, allowed_mime=set(policy.mime_types))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing_bytes = db.scalar(select(__import__('sqlalchemy').func.coalesce(__import__('sqlalchemy').func.sum(ProjectDocument.size_bytes), 0)).where(ProjectDocument.project_id == project.id, ProjectDocument.deleted_at.is_(None))) or 0
    if existing_bytes + len(data) > get_settings().project_storage_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Project storage quota exceeded")
    storage = get_storage(); key = storage.put(project_id=project.id, data=data, suffix=suffix)
    row = ProjectDocument(project_id=project.id, project_version_id=version.id, owner_user_id=user.id, category=category, original_name=Path(file.filename or "upload").name, stored_name=Path(key).name, storage_key=key, mime_type=mime, size_bytes=len(data), sha256=sha256_bytes(data), scan_status="PENDING", created_by=user.id, updated_by=user.id)
    db.add(row); audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="DOCUMENT_UPLOADED", entity_type="PROJECT_DOCUMENT", entity_id=row.id, after={"name": row.original_name, "sha256": row.sha256}); db.commit()
    return {"id": row.id, "name": row.original_name, "size_bytes": row.size_bytes, "sha256": row.sha256}


@router.get("/api/projects/{project_id}/documents")
def list_documents(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    rows = list(db.scalars(select(ProjectDocument).where(
        ProjectDocument.project_id == project.id, ProjectDocument.deleted_at.is_(None)
    ).order_by(ProjectDocument.created_at.desc())).all())
    return [{
        "id": row.id, "project_version_id": row.project_version_id, "category": row.category,
        "name": row.original_name, "mime_type": row.mime_type, "size_bytes": row.size_bytes,
        "sha256": row.sha256, "scan_status": row.scan_status, "created_at": row.created_at,
    } for row in rows]


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    row = db.get(ProjectDocument, document_id)
    if not row or row.deleted_at:
        raise HTTPException(status_code=404, detail="Document not found")
    project = require_project(db, user, row.project_id)
    version = db.get(ProjectVersion, row.project_version_id)
    if not version or version.immutable:
        raise HTTPException(status_code=409, detail="Submitted-version documents cannot be deleted")
    row.deleted_at = utcnow(); row.updated_by = user.id
    try:
        get_storage().delete(row.storage_key)
    except Exception:
        pass
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="DOCUMENT_DELETED", entity_type="PROJECT_DOCUMENT", entity_id=row.id)
    db.commit()
    return {"ok": True}


@router.get("/api/documents/{document_id}/download")
def download_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(ProjectDocument, document_id)
    if not row or row.deleted_at:
        raise HTTPException(status_code=404, detail="Document not found")
    project = require_project(db, user, row.project_id)
    storage = get_storage(); url = storage.signed_url(row.storage_key)
    if url:
        return RedirectResponse(url, status_code=307)
    data = storage.get(row.storage_key)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="DOCUMENT_DOWNLOADED", entity_type="PROJECT_DOCUMENT", entity_id=row.id); db.commit()
    return Response(data, media_type=row.mime_type, headers={"Content-Disposition": f'attachment; filename="{row.original_name}"'})


@router.get("/api/projects/{project_id}/export/portal.lv360")
def export_portal(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    data = export_portal_package(db, project, version, user)
    record_analysis_export(db, project=project, version=version, export_type="PORTAL", package_version="1.0.0", data=data, user=user)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="PORTAL_PACKAGE_EXPORTED", entity_type="PROJECT_VERSION", entity_id=version.id); db.commit()
    return Response(data, media_type="application/vnd.landvalue360.portal+zip", headers={"Content-Disposition": f'attachment; filename="{project.reference}-portal.lv360"'})


@router.get("/api/projects/{project_id}/export/internal.lv360")
def export_internal(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    data = export_internal_package(db, project, version)
    record_analysis_export(db, project=project, version=version, export_type="INTERNAL", package_version="2.1.1", data=data, user=user)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="INTERNAL_PACKAGE_EXPORTED", entity_type="PROJECT_VERSION", entity_id=version.id); db.commit()
    return Response(data, media_type="application/vnd.landvalue360.project+zip", headers={"Content-Disposition": f'attachment; filename="{project.reference}-internal.lv360"'})


@router.get("/api/projects/{project_id}/export.xlsx")
def export_human_excel(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    data = export_excel(project, version)
    audit(
        db, user=user, organization_id=project.organization_id, project_id=project.id,
        action="PROJECT_EXCEL_EXPORTED", entity_type="PROJECT_VERSION", entity_id=version.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{project.reference}.xlsx"'})


@router.post("/api/information-requests/{request_id}/messages")
def respond_information(request_id: str, payload: dict, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    info = db.get(InformationRequest, request_id)
    if not info:
        raise HTTPException(status_code=404, detail="Request not found")
    project = require_project(db, user, info.project_id)
    body = str(payload.get("message") or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message is required")
    db.add(InformationRequestMessage(request_id=info.id, author_user_id=user.id, body=body, internal_only=False, created_by=user.id, updated_by=user.id))
    info.status = "RESPONDED"
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="INFORMATION_REQUEST_RESPONDED", entity_type="INFORMATION_REQUEST", entity_id=info.id)
    db.commit(); return {"ok": True}


@router.get("/api/reports/{report_version_id}/download")
def download_report(report_version_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    version = db.get(ReportVersion, report_version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Report not available")
    report = db.get(Report, version.report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not available")
    project = require_project(db, user, report.project_id)
    permissions = user_permission_codes(db, user.id)
    if version.status != "PUBLISHED" and "admin.projects" not in permissions:
        raise HTTPException(status_code=404, detail="Report not available")
    data = get_storage().get(version.storage_key)
    db.add(ReportDownload(report_version_id=version.id, user_id=user.id, ip_address=request.client.host if request.client else None))
    action = "ADMIN_REPORT_VERSION_DOWNLOADED" if version.status != "PUBLISHED" else "REPORT_DOWNLOADED"
    audit(
        db, user=user, organization_id=project.organization_id, project_id=project.id,
        action=action, entity_type="REPORT_VERSION", entity_id=version.id,
        after={"status": version.status}, ip=request.client.host if request.client else None,
    )
    db.commit()
    return Response(data, media_type=version.mime_type, headers={"Content-Disposition": f'attachment; filename="{version.original_name}"'})
