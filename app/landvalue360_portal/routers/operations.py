from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import (
    AnalysisImport, InformationRequest, InformationRequestMessage, OrganizationMember, Project, ProjectAssignment,
    ProjectDocument, Report, ReportVersion, Role, MemberRole, User, FileTypePolicy, utcnow
)
from ..notifications import notify_user
from ..packages import export_internal_package, export_portal_package
from ..schemas import AssignmentIn, InformationRequestIn, StatusIn
from ..security import csrf_protect, current_user, require_permissions, user_permission_codes, user_role_codes
from ..services import audit, current_version, record_analysis_export, require_project
from ..storage import get_storage, sha256_bytes, validate_upload
from ..web import templates
from ..workflow import TRANSITIONS, transition_project

router = APIRouter()
STAFF_ROLES = {"ANALYST", "REVIEWER", "TEAM_MANAGER", "PLATFORM_ADMIN"}


def _operations_projects(db: Session, user: User) -> list[Project]:
    perms = user_permission_codes(db, user.id)
    if "ops.view_all" in perms or "admin.projects" in perms:
        return list(db.scalars(select(Project).where(Project.deleted_at.is_(None)).order_by(Project.updated_at.desc())).all())
    ids = list(db.scalars(select(ProjectAssignment.project_id).where(ProjectAssignment.user_id == user.id, ProjectAssignment.active.is_(True))).all())
    return list(db.scalars(select(Project).where(Project.id.in_(ids) if ids else False).order_by(Project.updated_at.desc())).all())


def _staff_users(db: Session) -> list[dict]:
    rows = db.execute(
        select(User, Role.code)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .join(MemberRole, MemberRole.membership_id == OrganizationMember.id)
        .join(Role, Role.id == MemberRole.role_id)
        .where(
            Role.code.in_(STAFF_ROLES),
            OrganizationMember.status == "ACTIVE",
            OrganizationMember.deleted_at.is_(None),
            User.active.is_(True),
            User.suspended.is_(False),
            User.deleted_at.is_(None),
        )
        .order_by(User.full_name, Role.code)
    ).all()
    grouped: dict[str, dict] = {}
    for user, role_code in rows:
        item = grouped.setdefault(user.id, {"id": user.id, "full_name": user.full_name, "email": user.email, "roles": []})
        if role_code not in item["roles"]:
            item["roles"].append(role_code)
    return list(grouped.values())


@router.get("/operations", response_class=RedirectResponse)
def operations_dashboard(user: User = Depends(require_permissions("ops.view_assigned"))):
    """Legacy workflow entry point.

    The standalone financial portal no longer exposes the analyst/status workspace.
    Operational APIs remain available for backward compatibility and controlled
    integrations, while interactive users are directed to the administration area.
    """
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/operations/projects/{project_id}", response_class=RedirectResponse)
def operations_project(project_id: str, user: User = Depends(require_permissions("ops.view_assigned")), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id)
    return RedirectResponse(url=f"/portal/projects/{project.id}/financial", status_code=303)


@router.get("/api/operations/projects")
def api_operations_projects(user: User = Depends(require_permissions("ops.view_assigned")), db: Session = Depends(get_db)):
    rows = _operations_projects(db, user)
    return [{"id": p.id, "reference": p.reference, "name": p.name, "status": p.status, "priority": p.priority, "submitted_at": p.submitted_at, "sla_due_at": p.sla_due_at} for p in rows]


@router.get("/api/operations/staff")
def operations_staff(user: User = Depends(require_permissions("ops.assign")), db: Session = Depends(get_db)):
    return _staff_users(db)


@router.post("/api/operations/projects/{project_id}/assign")
def assign_project(project_id: str, payload: AssignmentIn, request: Request, user: User = Depends(require_permissions("ops.assign")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    target = db.get(User, payload.user_id)
    if not target or not target.active or target.suspended or target.deleted_at:
        raise HTTPException(status_code=404, detail="Eligible staff user not found")
    if payload.assignment_type not in user_role_codes(db, target.id):
        raise HTTPException(status_code=422, detail=f"Selected user does not hold the {payload.assignment_type} role")
    old = db.scalar(select(ProjectAssignment).where(ProjectAssignment.project_id == project.id, ProjectAssignment.assignment_type == payload.assignment_type))
    if old:
        old.user_id = target.id; old.active = True; old.updated_by = user.id
    else:
        db.add(ProjectAssignment(project_id=project.id, user_id=target.id, assignment_type=payload.assignment_type, active=True, created_by=user.id, updated_by=user.id))
    notify_user(db, target, kind="PROJECT_ASSIGNED", title="تم إسناد مشروع", body=f"تم إسناد المشروع {project.reference} إليك.", link=f"/operations/projects/{project.id}", email_template="PROJECT_ASSIGNED")
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="PROJECT_ASSIGNED", entity_type="PROJECT", entity_id=project.id, after=payload.model_dump())
    db.commit(); return {"ok": True}


@router.post("/api/operations/projects/{project_id}/status")
def change_status(project_id: str, payload: StatusIn, request: Request, user: User = Depends(require_permissions("ops.change_status")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    roles = user_role_codes(db, user.id)
    if not roles.intersection({"TEAM_MANAGER", "PLATFORM_ADMIN"}):
        role_targets = {
            "ANALYST": {"IN_ANALYSIS", "IN_REVIEW", "MISSING_INFORMATION"},
            "REVIEWER": {"IN_REVIEW", "IN_ANALYSIS", "MISSING_INFORMATION"},
        }
        allowed = set().union(*(role_targets.get(role, set()) for role in roles))
        if payload.target_status not in allowed:
            raise HTTPException(status_code=403, detail="This role cannot apply the requested project status")
    try:
        transition_project(db, project, payload.target_status, user=user, reason=payload.reason, sla_hours=get_settings().sla_hours)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    owner = db.get(User, project.owner_user_id)
    notify_user(db, owner, kind="PROJECT_STATUS", title="تحديث حالة المشروع", body=f"أصبحت حالة المشروع {project.reference}: {project.status}", link=f"/portal/projects/{project.id}/status", email_template="PROJECT_STATUS")
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="PROJECT_STATUS_CHANGED", entity_type="PROJECT", entity_id=project.id, after={"status": project.status, "reason": payload.reason})
    db.commit(); return {"ok": True, "status": project.status}


@router.post("/api/operations/projects/{project_id}/information-requests", status_code=201)
def request_information(project_id: str, payload: InformationRequestIn, request: Request, user: User = Depends(require_permissions("ops.request_information")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    row = InformationRequest(project_id=project.id, project_version_id=version.id, requested_by=user.id, status="OPEN", subject=payload.subject, created_by=user.id, updated_by=user.id)
    db.add(row); db.flush()
    db.add(InformationRequestMessage(request_id=row.id, author_user_id=user.id, body=payload.message, internal_only=False, created_by=user.id, updated_by=user.id))
    if project.status != "MISSING_INFORMATION":
        try:
            transition_project(db, project, "MISSING_INFORMATION", user=user, reason=payload.subject, sla_hours=get_settings().sla_hours)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    owner = db.get(User, project.owner_user_id)
    notify_user(db, owner, kind="MISSING_INFORMATION", title="معلومات إضافية مطلوبة", body=payload.subject, link=f"/portal/projects/{project.id}/status", email_template="MISSING_INFORMATION")
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="INFORMATION_REQUEST_CREATED", entity_type="INFORMATION_REQUEST", entity_id=row.id, after={"subject": payload.subject})
    db.commit(); return {"id": row.id, "status": row.status}


@router.post("/api/operations/projects/{project_id}/notes")
def internal_note(project_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("ops.internal_note")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id)
    body = str(payload.get("body") or "").strip()
    if not body: raise HTTPException(status_code=422, detail="Note is required")
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="INTERNAL_NOTE", entity_type="PROJECT", entity_id=project.id, after={"body": body})
    db.commit(); return {"ok": True}


@router.get("/api/operations/projects/{project_id}/export/{kind}")
def operations_export(project_id: str, kind: str, user: User = Depends(require_permissions("ops.export")), db: Session = Depends(get_db)):
    project = require_project(db, user, project_id); version = current_version(db, project); owner = db.get(User, project.owner_user_id)
    if kind == "internal.lv360":
        data = export_internal_package(db, project, version); media = "application/vnd.landvalue360.project+zip"; name = f"{project.reference}-internal.lv360"
    elif kind == "portal.lv360":
        data = export_portal_package(db, project, version, owner); media = "application/vnd.landvalue360.portal+zip"; name = f"{project.reference}-portal.lv360"
    else:
        raise HTTPException(status_code=404, detail="Unknown export type")
    record_analysis_export(db, project=project, version=version, export_type="INTERNAL" if kind == "internal.lv360" else "PORTAL", package_version="2.1.1" if kind == "internal.lv360" else "1.0.0", data=data, user=user)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="ANALYSIS_PACKAGE_EXPORTED", entity_type="PROJECT_VERSION", entity_id=version.id, after={"kind": kind}); db.commit()
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/api/operations/projects/{project_id}/analysis-imports", status_code=201)
def record_analysis_import(project_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("ops.report_upload")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    source_reference = str(payload.get("source_reference") or "").strip() or None
    calculation_reference = str(payload.get("calculation_run_reference") or "").strip() or None
    if not source_reference and not calculation_reference:
        raise HTTPException(status_code=422, detail="Source or calculation reference is required")
    row = AnalysisImport(project_id=project.id, project_version_id=version.id, imported_by=user.id, source_reference=source_reference, calculation_run_reference=calculation_reference, status="RECEIVED", created_by=user.id, updated_by=user.id)
    db.add(row); db.flush()
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="ANALYSIS_RESULT_REGISTERED", entity_type="ANALYSIS_IMPORT", entity_id=row.id, after={"source_reference": source_reference, "calculation_run_reference": calculation_reference})
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/api/operations/projects/{project_id}/reports", status_code=201)
async def upload_report(project_id: str, request: Request, report_type: str = Form(...), language: str = Form("ar"), calculation_run_reference: str = Form(""), file: UploadFile = File(...), user: User = Depends(require_permissions("ops.report_upload")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    if report_type not in {"EXECUTIVE", "DETAILED"}:
        raise HTTPException(status_code=422, detail="Invalid report type")
    calculation_run_reference = calculation_run_reference.strip()
    if not calculation_run_reference:
        raise HTTPException(status_code=422, detail="Calculation run reference is required")
    policy = db.scalar(select(FileTypePolicy).where(FileTypePolicy.extension == ".pdf", FileTypePolicy.active.is_(True)))
    if not policy:
        raise HTTPException(status_code=422, detail="PDF uploads are disabled by platform policy")
    max_bytes = min(get_settings().max_upload_mb * 1024 * 1024, policy.max_size_bytes)
    data = await file.read(max_bytes + 1)
    try: suffix, mime = validate_upload(file.filename or "report.pdf", file.content_type, data, max_bytes=max_bytes, allowed_mime=set(policy.mime_types))
    except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc
    if suffix != ".pdf": raise HTTPException(status_code=422, detail="Reports must be PDF files")
    report = db.scalar(select(Report).where(Report.project_id == project.id, Report.project_version_id == version.id, Report.report_type == report_type, Report.language == language))
    if not report:
        report = Report(project_id=project.id, project_version_id=version.id, report_type=report_type, language=language, status="IN_REVIEW", created_by=user.id, updated_by=user.id); db.add(report); db.flush()
    number = (db.scalar(select(func.max(ReportVersion.version_number)).where(ReportVersion.report_id == report.id)) or 0) + 1
    key = get_storage().put(project_id=project.id, data=data, suffix=".pdf")
    rv = ReportVersion(report_id=report.id, version_number=number, uploaded_by=user.id, storage_key=key, original_name=Path(file.filename or "report.pdf").name, mime_type=mime, size_bytes=len(data), checksum=sha256_bytes(data), calculation_run_reference=calculation_run_reference, status="IN_REVIEW", created_by=user.id, updated_by=user.id)
    db.add(rv); db.flush(); report.current_version_id = rv.id; report.status = "IN_REVIEW"
    if project.status == "IN_ANALYSIS":
        transition_project(db, project, "IN_REVIEW", user=user, reason="Report submitted for review", sla_hours=get_settings().sla_hours)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="REPORT_UPLOADED", entity_type="REPORT_VERSION", entity_id=rv.id, after={"type": report_type, "version": number})
    db.commit(); return {"report_id": report.id, "report_version_id": rv.id, "status": rv.status}


@router.post("/api/operations/report-versions/{version_id}/review")
def review_report(version_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("ops.report_review")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    rv = db.get(ReportVersion, version_id)
    if not rv: raise HTTPException(status_code=404, detail="Report not found")
    report = db.get(Report, rv.report_id); project = require_project(db, user, report.project_id)
    if rv.uploaded_by == user.id:
        raise HTTPException(status_code=409, detail="A report must be reviewed by a different user")
    action = str(payload.get("action") or "").upper()
    if action == "APPROVE":
        rv.status = "APPROVED"; rv.approved_by = user.id; rv.approved_at = utcnow(); report.status = "APPROVED"
    elif action == "REJECT":
        rv.status = "REJECTED"; report.status = "REJECTED"
        if project.status == "IN_REVIEW": transition_project(db, project, "IN_ANALYSIS", user=user, reason="Report rejected", sla_hours=get_settings().sla_hours)
    else: raise HTTPException(status_code=422, detail="Action must be APPROVE or REJECT")
    db.flush()
    approved_types = set(db.scalars(select(Report.report_type).where(Report.project_id == project.id, Report.project_version_id == report.project_version_id, Report.status == "APPROVED")).all())
    if {"EXECUTIVE", "DETAILED"}.issubset(approved_types) and project.status == "IN_REVIEW":
        transition_project(db, project, "REPORT_READY", user=user, reason="Required reports approved", sla_hours=get_settings().sla_hours)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action=f"REPORT_{action}", entity_type="REPORT_VERSION", entity_id=rv.id, after={"notes": payload.get("notes")})
    db.commit(); return {"ok": True, "status": rv.status, "project_status": project.status}


@router.post("/api/operations/projects/{project_id}/publish")
def publish_reports(project_id: str, request: Request, user: User = Depends(require_permissions("ops.report_publish")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    project = require_project(db, user, project_id); version = current_version(db, project)
    reports = list(db.scalars(select(Report).where(Report.project_id == project.id, Report.project_version_id == version.id, Report.status == "APPROVED")).all())
    if {r.report_type for r in reports} != {"EXECUTIVE", "DETAILED"}:
        raise HTTPException(status_code=409, detail="Both Executive and Detailed reports must be approved")
    for report in reports:
        report.status = "PUBLISHED"; report.published_at = utcnow(); report.updated_by = user.id
        rv = db.get(ReportVersion, report.current_version_id); rv.status = "PUBLISHED"; rv.updated_by = user.id
    if project.status != "REPORT_READY": raise HTTPException(status_code=409, detail="Project is not ready for publication")
    transition_project(db, project, "COMPLETED", user=user, reason="Approved report set published", sla_hours=get_settings().sla_hours)
    owner = db.get(User, project.owner_user_id)
    notify_user(db, owner, kind="REPORT_READY", title="تقارير المشروع جاهزة", body=f"تم نشر تقارير المشروع {project.reference}.", link=f"/portal/projects/{project.id}/status", email_template="REPORT_READY")
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="REPORTS_PUBLISHED", entity_type="PROJECT", entity_id=project.id)
    db.commit(); return {"ok": True, "status": project.status}
