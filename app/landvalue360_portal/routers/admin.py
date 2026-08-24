from __future__ import annotations

import secrets
import string
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import (
    AccessSession, AuditLog, CalculationRun, EmailTemplate, EngineVersion,
    FileTypePolicy, FinancialPolicy, FinancialPolicyVersion, LoginAttempt,
    MemberRole, OneTimeToken, Organization, OrganizationMember, PrivacyRequest,
    Project, ProjectDocument, ProjectVersion, Report, ReportVersion, Role,
    SystemSetting, User, utcnow,
)
from ..notifications import notify_user
from ..security import (
    csrf_protect, hash_password, new_token, require_permissions, token_hash,
    user_role_codes,
)
from ..financial_engine import policy_controls
from ..financial_service import activate_policy_version, create_policy_version, current_policy_version, set_policy_version_status
from ..services import assign_role, audit, create_staff_user, seed_defaults, slugify
from ..web import templates

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "admin.html", {"title": "إدارة المنصة", "user": user})


@router.get("/api/admin/summary")
def admin_summary(user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db)):
    return {
        "users": db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0,
        "organizations": db.scalar(select(func.count(Organization.id)).where(Organization.deleted_at.is_(None))) or 0,
        "projects": db.scalar(select(func.count(Project.id)).where(Project.deleted_at.is_(None))) or 0,
        "submitted": db.scalar(select(func.count(Project.id)).where(Project.status.not_in(["DRAFT", "ARCHIVED", "CANCELLED"]))) or 0,
    }


@router.get("/api/admin/users")
def admin_users(user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())).all())
    result = []
    for row in rows:
        active_sessions = db.scalar(select(func.count(AccessSession.id)).where(
            AccessSession.user_id == row.id, AccessSession.revoked_at.is_(None), AccessSession.expires_at > utcnow(),
        )) or 0
        membership_count = db.scalar(select(func.count(OrganizationMember.id)).where(
            OrganizationMember.user_id == row.id, OrganizationMember.deleted_at.is_(None),
        )) or 0
        project_count = db.scalar(select(func.count(Project.id)).where(
            Project.owner_user_id == row.id, Project.deleted_at.is_(None),
        )) or 0
        result.append({
            "id": row.id, "email": row.email, "full_name": row.full_name,
            "active": row.active, "suspended": row.suspended,
            "verified": bool(row.email_verified_at), "roles": sorted(user_role_codes(db, row.id)),
            "created_at": row.created_at, "last_login_at": row.last_login_at,
            "password_changed_at": row.password_changed_at,
            "must_change_password": bool(row.must_change_password),
            "active_sessions": active_sessions, "membership_count": membership_count,
            "project_count": project_count,
        })
    return result


@router.post("/api/admin/users", status_code=201)
def create_admin_user(payload: dict, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    role = str(payload.get("role") or "ANALYST").upper()
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    full_name = str(payload.get("full_name") or "").strip()
    if role not in {"ANALYST", "REVIEWER", "TEAM_MANAGER", "PLATFORM_ADMIN"}:
        raise HTTPException(status_code=422, detail="Invalid staff role")
    if "@" not in email or len(full_name) < 2 or len(password) < 10:
        raise HTTPException(status_code=422, detail="Valid name, email and a password of at least 10 characters are required")
    try:
        row = create_staff_user(db, email=email, password=password, full_name=full_name, role_code=role, must_change_password=True)
    except Exception as exc:
        db.rollback(); raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(db, user=user, action="ADMIN_USER_CREATED", entity_type="USER", entity_id=row.id, after={"role": role})
    db.commit(); return {"id": row.id, "email": row.email, "role": role}


@router.patch("/api/admin/users/{user_id}")
def update_admin_user(user_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == user.id and (payload.get("active") is False or payload.get("suspended") is True):
        raise HTTPException(status_code=422, detail="You cannot deactivate or suspend your own administrator account")
    before = {"active": target.active, "suspended": target.suspended}
    if "active" in payload: target.active = bool(payload["active"])
    if "suspended" in payload: target.suspended = bool(payload["suspended"])
    target.updated_by = user.id
    audit(db, user=user, action="ADMIN_USER_UPDATED", entity_type="USER", entity_id=target.id, before=before, after={"active": target.active, "suspended": target.suspended})
    db.commit(); return {"ok": True}


def _temporary_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%*+-_"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(c in "!@#$%*+-_" for c in value)):
            return value


def _revoke_user_sessions(db: Session, target_user_id: str) -> int:
    rows = list(db.scalars(select(AccessSession).where(
        AccessSession.user_id == target_user_id, AccessSession.revoked_at.is_(None),
    )).all())
    now = utcnow()
    for row in rows:
        row.revoked_at = now
    return len(rows)


@router.get("/api/admin/users/{user_id}/activity")
def admin_user_activity(user_id: str, limit: int = 100, user: User = Depends(require_permissions("admin.audit")), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        raise HTTPException(status_code=404, detail="User not found")
    audits = list(db.scalars(select(AuditLog).where(
        (AuditLog.user_id == target.id) | ((AuditLog.entity_type == "USER") & (AuditLog.entity_id == target.id))
    ).order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 300))).all())
    attempts = list(db.scalars(select(LoginAttempt).where(
        func.lower(LoginAttempt.identifier) == target.email.lower()
    ).order_by(LoginAttempt.attempted_at.desc()).limit(50)).all())
    sessions = list(db.scalars(select(AccessSession).where(
        AccessSession.user_id == target.id
    ).order_by(AccessSession.created_at.desc()).limit(50)).all())
    memberships = db.execute(
        select(OrganizationMember, Organization).join(Organization, Organization.id == OrganizationMember.organization_id)
        .where(OrganizationMember.user_id == target.id, OrganizationMember.deleted_at.is_(None))
        .order_by(Organization.name)
    ).all()
    return {
        "user": {
            "id": target.id, "full_name": target.full_name, "email": target.email,
            "active": target.active, "suspended": target.suspended, "verified": bool(target.email_verified_at),
            "roles": sorted(user_role_codes(db, target.id)), "created_at": target.created_at,
            "last_login_at": target.last_login_at, "password_changed_at": target.password_changed_at,
            "must_change_password": bool(target.must_change_password),
        },
        "memberships": [{
            "id": membership.id, "organization_id": organization.id, "organization_name": organization.name,
            "status": membership.status, "is_owner": membership.is_owner,
        } for membership, organization in memberships],
        "sessions": [{
            "id": row.id, "created_at": row.created_at, "expires_at": row.expires_at,
            "revoked_at": row.revoked_at, "ip_address": row.ip_address, "user_agent": row.user_agent,
        } for row in sessions],
        "login_attempts": [{
            "success": row.success, "attempted_at": row.attempted_at, "ip_address": row.ip_address,
        } for row in attempts],
        "audit": [{
            "id": row.id, "created_at": row.created_at, "action": row.action,
            "project_id": row.project_id, "entity_type": row.entity_type,
            "entity_id": row.entity_id, "after_data": row.after_data,
        } for row in audits],
    }


@router.post("/api/admin/users/{user_id}/send-password-reset")
def admin_send_password_reset(user_id: str, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        raise HTTPException(status_code=404, detail="User not found")
    now = utcnow()
    for old in db.scalars(select(OneTimeToken).where(
        OneTimeToken.user_id == target.id, OneTimeToken.kind == "RESET_PASSWORD", OneTimeToken.used_at.is_(None),
    )).all():
        old.used_at = now
    raw = new_token()
    db.add(OneTimeToken(
        user_id=target.id, kind="RESET_PASSWORD", token_hash=token_hash(raw),
        expires_at=now + timedelta(hours=1), created_by=user.id, updated_by=user.id,
    ))
    link = f"{get_settings().base_url}/reset-password?token={raw}"
    notify_user(
        db, target, kind="PASSWORD_RESET", title="إعادة تعيين كلمة المرور",
        body="أرسل مدير المنصة رابطاً آمناً لإعادة تعيين كلمة المرور.",
        link=link, email_template="PASSWORD_RESET",
    )
    audit(db, user=user, action="ADMIN_PASSWORD_RESET_LINK_SENT", entity_type="USER", entity_id=target.id, after={"expires_in_minutes": 60}, ip=request.client.host if request.client else None)
    db.commit()
    return {"ok": True, "message": "Password reset link queued for delivery"}


@router.post("/api/admin/users/{user_id}/temporary-password")
def admin_temporary_password(user_id: str, request: Request, payload: dict | None = None, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == user.id:
        raise HTTPException(status_code=422, detail="Use your account security page to change your own password")
    supplied = str((payload or {}).get("temporary_password") or "")
    password = supplied or _temporary_password()
    if len(password) < 12:
        raise HTTPException(status_code=422, detail="Temporary password must be at least 12 characters")
    target.password_hash = hash_password(password)
    target.password_changed_at = utcnow()
    target.must_change_password = True
    target.failed_login_count = 0
    target.locked_until = None
    target.updated_by = user.id
    revoked = _revoke_user_sessions(db, target.id)
    audit(
        db, user=user, action="ADMIN_TEMPORARY_PASSWORD_ISSUED", entity_type="USER", entity_id=target.id,
        after={"force_change_on_login": True, "revoked_sessions": revoked},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {
        "ok": True, "temporary_password": password, "must_change_password": True,
        "revoked_sessions": revoked,
        "warning": "This password is returned once and is not stored in readable form.",
    }


@router.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke_sessions(user_id: str, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    target = db.get(User, user_id)
    if not target or target.deleted_at:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == user.id:
        raise HTTPException(status_code=422, detail="Use Sign Out to close your own session")
    revoked = _revoke_user_sessions(db, target.id)
    audit(db, user=user, action="ADMIN_SESSIONS_REVOKED", entity_type="USER", entity_id=target.id, after={"revoked_sessions": revoked}, ip=request.client.host if request.client else None)
    db.commit()
    return {"ok": True, "revoked_sessions": revoked}


@router.get("/api/admin/organizations")
def admin_organizations(user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Organization).where(Organization.deleted_at.is_(None)).order_by(Organization.name)).all())
    result = []
    for row in rows:
        members = db.scalar(select(func.count(OrganizationMember.id)).where(
            OrganizationMember.organization_id == row.id, OrganizationMember.deleted_at.is_(None),
        )) or 0
        projects = db.scalar(select(func.count(Project.id)).where(
            Project.organization_id == row.id, Project.deleted_at.is_(None),
        )) or 0
        result.append({
            "id": row.id, "name": row.name, "slug": row.slug, "kind": row.kind,
            "active": row.active, "membership_count": members, "project_count": projects,
            "created_at": row.created_at,
        })
    return result


@router.get("/api/admin/roles")
def admin_roles(user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Role).order_by(Role.code)).all())
    return [{"id": row.id, "code": row.code, "name_ar": row.name_ar, "name_en": row.name_en} for row in rows]


@router.post("/api/admin/organizations", status_code=201)
def create_organization(payload: dict, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    name = str(payload.get("name") or "").strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Organization name is required")
    base = slugify(str(payload.get("slug") or name))
    slug = base; counter = 2
    while db.scalar(select(Organization).where(Organization.slug == slug)):
        slug = f"{base}-{counter}"; counter += 1
    row = Organization(name=name, slug=slug, kind=str(payload.get("kind") or "LANDOWNER").upper(), active=True, created_by=user.id, updated_by=user.id)
    db.add(row); db.flush()
    audit(db, user=user, action="ORGANIZATION_CREATED", entity_type="ORGANIZATION", entity_id=row.id, organization_id=row.id, after={"name": name, "slug": slug})
    db.commit()
    return {"id": row.id, "name": row.name, "slug": row.slug, "kind": row.kind}


@router.get("/api/admin/memberships")
def admin_memberships(user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db)):
    rows = db.execute(
        select(OrganizationMember, Organization, User)
        .join(Organization, Organization.id == OrganizationMember.organization_id)
        .join(User, User.id == OrganizationMember.user_id)
        .where(OrganizationMember.deleted_at.is_(None))
        .order_by(Organization.name, User.full_name)
    ).all()
    result = []
    for membership, organization, member_user in rows:
        role_codes = list(db.scalars(
            select(Role.code).join(MemberRole, MemberRole.role_id == Role.id).where(MemberRole.membership_id == membership.id)
        ).all())
        result.append({
            "id": membership.id, "organization_id": organization.id, "organization_name": organization.name,
            "user_id": member_user.id, "user_name": member_user.full_name, "email": member_user.email,
            "status": membership.status, "is_owner": membership.is_owner, "roles": sorted(role_codes),
        })
    return result


@router.post("/api/admin/memberships", status_code=201)
def create_membership(payload: dict, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    organization = db.get(Organization, str(payload.get("organization_id") or ""))
    member_user = db.get(User, str(payload.get("user_id") or ""))
    if not organization or not member_user:
        raise HTTPException(status_code=404, detail="Organization or user not found")
    membership = db.scalar(select(OrganizationMember).where(
        OrganizationMember.organization_id == organization.id, OrganizationMember.user_id == member_user.id
    ))
    if not membership:
        membership = OrganizationMember(organization_id=organization.id, user_id=member_user.id, status="ACTIVE", is_owner=bool(payload.get("is_owner")), created_by=user.id, updated_by=user.id)
        db.add(membership); db.flush()
    else:
        membership.status = "ACTIVE"; membership.deleted_at = None; membership.updated_by = user.id
    role_code = str(payload.get("role") or "LANDOWNER").upper()
    assign_role(db, membership, role_code)
    audit(db, user=user, organization_id=organization.id, action="MEMBERSHIP_CREATED", entity_type="ORGANIZATION_MEMBER", entity_id=membership.id, after={"user_id": member_user.id, "role": role_code})
    db.commit()
    return {"id": membership.id, "status": membership.status, "role": role_code}


@router.patch("/api/admin/memberships/{membership_id}")
def update_membership(membership_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.users")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    membership = db.get(OrganizationMember, membership_id)
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")
    if "status" in payload:
        status_value = str(payload["status"]).upper()
        if status_value not in {"ACTIVE", "SUSPENDED"}:
            raise HTTPException(status_code=422, detail="Invalid membership status")
        membership.status = status_value
    membership.updated_by = user.id
    audit(db, user=user, organization_id=membership.organization_id, action="MEMBERSHIP_UPDATED", entity_type="ORGANIZATION_MEMBER", entity_id=membership.id, after={"status": membership.status})
    db.commit(); return {"ok": True, "status": membership.status}


@router.get("/api/admin/projects")
def admin_projects(q: str = "", user: User = Depends(require_permissions("admin.projects")), db: Session = Depends(get_db)):
    statement = (
        select(Project, Organization, User)
        .join(Organization, Organization.id == Project.organization_id)
        .join(User, User.id == Project.owner_user_id)
        .where(Project.deleted_at.is_(None))
        .order_by(Project.updated_at.desc())
    )
    query = q.strip().lower()
    rows = db.execute(statement).all()
    result = []
    for project, organization, owner in rows:
        if query and query not in " ".join([project.reference, project.name, organization.name, owner.full_name, owner.email]).lower():
            continue
        latest_run = db.scalar(select(CalculationRun).where(
            CalculationRun.project_id == project.id
        ).order_by(CalculationRun.created_at.desc()))
        document_count = db.scalar(select(func.count(ProjectDocument.id)).where(
            ProjectDocument.project_id == project.id, ProjectDocument.deleted_at.is_(None),
        )) or 0
        actions = {
            "project": f"/portal/projects/{project.id}",
            "financial": f"/portal/projects/{project.id}/financial",
            "project_xlsx": f"/api/projects/{project.id}/export.xlsx",
            "portal_package": f"/api/projects/{project.id}/export/portal.lv360",
        }
        if latest_run and latest_run.status == "COMPLETED":
            actions.update({
                "financial_pdf": f"/api/projects/{project.id}/financial/runs/{latest_run.id}/report.pdf?lang=ar",
                "financial_excel": f"/api/projects/{project.id}/financial/runs/{latest_run.id}/report.xlsx?lang=ar",
            })
        result.append({
            "id": project.id, "reference": project.reference, "name": project.name,
            "status": project.status, "priority": project.priority,
            "organization_id": organization.id, "organization_name": organization.name,
            "owner_user_id": owner.id, "owner_name": owner.full_name, "owner_email": owner.email,
            "submitted_at": project.submitted_at, "updated_at": project.updated_at,
            "current_version_id": project.current_version_id, "document_count": document_count,
            "latest_run": None if latest_run is None else {
                "id": latest_run.id, "status": latest_run.status, "created_at": latest_run.created_at,
                "completed_at": latest_run.completed_at, "result_hash": latest_run.result_hash,
            },
            "actions": actions,
        })
    return result


@router.get("/api/admin/projects/{project_id}/overview")
def admin_project_overview(project_id: str, user: User = Depends(require_permissions("admin.projects")), db: Session = Depends(get_db)):
    row = db.execute(
        select(Project, Organization, User)
        .join(Organization, Organization.id == Project.organization_id)
        .join(User, User.id == Project.owner_user_id)
        .where(Project.id == project_id, Project.deleted_at.is_(None))
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    project, organization, owner = row
    versions = list(db.scalars(select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.version_number.desc())).all())
    documents = list(db.scalars(select(ProjectDocument).where(ProjectDocument.project_id == project.id, ProjectDocument.deleted_at.is_(None)).order_by(ProjectDocument.created_at.desc())).all())
    runs = list(db.scalars(select(CalculationRun).where(CalculationRun.project_id == project.id).order_by(CalculationRun.created_at.desc()).limit(50)).all())
    reports = db.execute(
        select(Report, ReportVersion).join(ReportVersion, ReportVersion.report_id == Report.id)
        .where(Report.project_id == project.id).order_by(Report.created_at.desc(), ReportVersion.version_number.desc())
    ).all()
    return {
        "project": {
            "id": project.id, "reference": project.reference, "name": project.name, "status": project.status,
            "priority": project.priority, "updated_at": project.updated_at, "organization_name": organization.name,
            "owner_name": owner.full_name, "owner_email": owner.email,
            "project_url": f"/portal/projects/{project.id}",
            "financial_url": f"/portal/projects/{project.id}/financial",
        },
        "versions": [{
            "id": item.id, "version_number": item.version_number, "status": item.status,
            "immutable": item.immutable, "created_at": item.created_at, "snapshot_hash": item.snapshot_hash,
        } for item in versions],
        "documents": [{
            "id": item.id, "name": item.original_name, "category": item.category, "size_bytes": item.size_bytes,
            "created_at": item.created_at, "download_url": f"/api/documents/{item.id}/download",
        } for item in documents],
        "runs": [{
            "id": item.id, "status": item.status, "created_at": item.created_at, "completed_at": item.completed_at,
            "result_hash": item.result_hash,
            "pdf_url": f"/api/projects/{project.id}/financial/runs/{item.id}/report.pdf?lang=ar" if item.status == "COMPLETED" else None,
            "excel_url": f"/api/projects/{project.id}/financial/runs/{item.id}/report.xlsx?lang=ar" if item.status == "COMPLETED" else None,
        } for item in runs],
        "reports": [{
            "report_id": report.id, "version_id": version.id, "report_type": report.report_type,
            "status": version.status, "version_number": version.version_number, "created_at": version.created_at,
            # Platform administrators may inspect and download every report
            # version, including drafts and returned review versions. The
            # download route records the access in the audit trail.
            "download_url": f"/api/reports/{version.id}/download",
        } for report, version in reports],
    }


@router.get("/api/admin/audit")
def admin_audit(
    limit: int = 200, user_id: str | None = None, project_id: str | None = None,
    organization_id: str | None = None, action: str | None = None,
    user: User = Depends(require_permissions("admin.audit")), db: Session = Depends(get_db),
):
    statement = select(AuditLog).order_by(AuditLog.created_at.desc())
    if user_id:
        statement = statement.where(AuditLog.user_id == user_id)
    if project_id:
        statement = statement.where(AuditLog.project_id == project_id)
    if organization_id:
        statement = statement.where(AuditLog.organization_id == organization_id)
    if action:
        statement = statement.where(AuditLog.action.ilike(f"%{action.strip()}%"))
    rows = list(db.scalars(statement.limit(min(max(limit, 1), 500))).all())
    user_ids = {row.user_id for row in rows if row.user_id}
    users = {row.id: row for row in db.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    project_ids = {row.project_id for row in rows if row.project_id}
    projects = {row.id: row for row in db.scalars(select(Project).where(Project.id.in_(project_ids))).all()} if project_ids else {}
    return [{
        "id": row.id, "created_at": row.created_at, "user_id": row.user_id,
        "user_name": users[row.user_id].full_name if row.user_id in users else None,
        "user_email": users[row.user_id].email if row.user_id in users else None,
        "organization_id": row.organization_id, "project_id": row.project_id,
        "project_name": projects[row.project_id].name if row.project_id in projects else None,
        "action": row.action, "entity_type": row.entity_type, "entity_id": row.entity_id,
        "before_data": row.before_data, "after_data": row.after_data, "ip_address": row.ip_address,
    } for row in rows]


@router.get("/api/admin/settings")
def admin_settings(user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db)):
    return {row.key: row.value for row in db.scalars(select(SystemSetting)).all()}


@router.put("/api/admin/settings/{key}")
def update_setting(key: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if not row:
        row = SystemSetting(key=key, value=payload, created_by=user.id, updated_by=user.id); db.add(row)
    else:
        row.value = payload; row.updated_by = user.id
    audit(db, user=user, action="SYSTEM_SETTING_UPDATED", entity_type="SYSTEM_SETTING", entity_id=row.id, after={"key": key, "value": payload})
    db.commit(); return {"ok": True}


@router.get("/api/admin/email-templates")
def admin_email_templates(user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(EmailTemplate).order_by(EmailTemplate.code)).all())
    return [{"id": row.id, "code": row.code, "subject_ar": row.subject_ar, "subject_en": row.subject_en, "body_ar": row.body_ar, "body_en": row.body_en, "active": row.active} for row in rows]


@router.put("/api/admin/email-templates/{code}")
def update_email_template(code: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    code = code.strip().upper()
    row = db.scalar(select(EmailTemplate).where(EmailTemplate.code == code))
    values = {
        "subject_ar": str(payload.get("subject_ar") or "").strip(),
        "subject_en": str(payload.get("subject_en") or "").strip(),
        "body_ar": str(payload.get("body_ar") or "").strip(),
        "body_en": str(payload.get("body_en") or "").strip(),
        "active": bool(payload.get("active", True)),
    }
    if not all(values[key] for key in ("subject_ar", "subject_en", "body_ar", "body_en")):
        raise HTTPException(status_code=422, detail="All bilingual template fields are required")
    if row is None:
        row = EmailTemplate(code=code, created_by=user.id, updated_by=user.id, **values)
        db.add(row); db.flush()
    else:
        before = {key: getattr(row, key) for key in values}
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_by = user.id
        audit(db, user=user, action="EMAIL_TEMPLATE_UPDATED", entity_type="EMAIL_TEMPLATE", entity_id=row.id, before=before, after=values)
    db.commit()
    return {"ok": True, "code": row.code}


@router.get("/api/admin/file-policies")
def admin_file_policies(user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(FileTypePolicy).order_by(FileTypePolicy.extension)).all())
    return [{"id": row.id, "extension": row.extension, "mime_types": row.mime_types, "max_size_bytes": row.max_size_bytes, "active": row.active} for row in rows]


@router.put("/api/admin/file-policies/{extension}")
def update_file_policy(extension: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    ext = extension.strip().lower()
    if not ext.startswith(".") or len(ext) > 20:
        raise HTTPException(status_code=422, detail="Invalid file extension")
    mime_types = payload.get("mime_types") or []
    if isinstance(mime_types, str):
        mime_types = [item.strip() for item in mime_types.split(",") if item.strip()]
    max_size_bytes = int(payload.get("max_size_bytes") or 0)
    if not mime_types or max_size_bytes < 1024 or max_size_bytes > 1024 * 1024 * 250:
        raise HTTPException(status_code=422, detail="Valid MIME types and a size between 1 KB and 250 MB are required")
    row = db.scalar(select(FileTypePolicy).where(FileTypePolicy.extension == ext))
    values = {"mime_types": mime_types, "max_size_bytes": max_size_bytes, "active": bool(payload.get("active", True))}
    if row is None:
        row = FileTypePolicy(extension=ext, created_by=user.id, updated_by=user.id, **values)
        db.add(row); db.flush()
    else:
        before = {"mime_types": row.mime_types, "max_size_bytes": row.max_size_bytes, "active": row.active}
        row.mime_types = mime_types; row.max_size_bytes = max_size_bytes; row.active = values["active"]; row.updated_by = user.id
        audit(db, user=user, action="FILE_POLICY_UPDATED", entity_type="FILE_TYPE_POLICY", entity_id=row.id, before=before, after=values)
    db.commit()
    return {"ok": True, "extension": row.extension}


@router.get("/api/admin/privacy-requests")
def admin_privacy_requests(user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db)):
    rows = db.execute(select(PrivacyRequest, User).join(User, User.id == PrivacyRequest.user_id).order_by(PrivacyRequest.created_at.desc())).all()
    return [{"id": row.id, "request_type": row.request_type, "status": row.status, "notes": row.notes, "created_at": row.created_at, "user_id": member.id, "email": member.email, "full_name": member.full_name} for row, member in rows]


@router.patch("/api/admin/privacy-requests/{request_id}")
def update_privacy_request(request_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.settings")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    row = db.get(PrivacyRequest, request_id)
    if not row:
        raise HTTPException(status_code=404, detail="Privacy request not found")
    status_value = str(payload.get("status") or "").upper()
    if status_value not in {"OPEN", "IN_PROGRESS", "COMPLETED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="Invalid privacy request status")
    row.status = status_value
    row.notes = str(payload.get("notes") or row.notes or "")[:5000]
    row.completed_at = utcnow() if status_value in {"COMPLETED", "REJECTED"} else None
    row.updated_by = user.id
    audit(db, user=user, action="PRIVACY_REQUEST_UPDATED", entity_type="PRIVACY_REQUEST", entity_id=row.id, after={"status": status_value})
    db.commit()
    return {"ok": True, "status": row.status}


@router.get("/api/admin/financial-policy")
def admin_financial_policy(user: User = Depends(require_permissions("admin.financial_policy")), db: Session = Depends(get_db)):
    current = current_policy_version(db)
    versions = list(db.scalars(
        select(FinancialPolicyVersion)
        .where(FinancialPolicyVersion.financial_policy_id == current.financial_policy_id)
        .order_by(FinancialPolicyVersion.version_number.desc())
    ).all())
    engines = list(db.scalars(select(EngineVersion).order_by(EngineVersion.created_at.desc())).all())
    return {
        "current": {
            "id": current.id, "version_number": current.version_number, "status": current.status,
            "effective_from": current.effective_from, "snapshot_hash": current.snapshot_hash,
            "change_reason": current.change_reason, "controls": policy_controls(current.policy_snapshot),
        },
        "versions": [{
            "id": row.id, "version_number": row.version_number, "status": row.status,
            "effective_from": row.effective_from, "snapshot_hash": row.snapshot_hash,
            "change_reason": row.change_reason, "created_by": row.created_by,
            "is_current": row.id == current.id,
            "display_name_ar": policy_controls(row.policy_snapshot).get("display_name_ar"),
            "display_name_en": policy_controls(row.policy_snapshot).get("display_name_en"),
            "description_ar": policy_controls(row.policy_snapshot).get("description_ar"),
            "description_en": policy_controls(row.policy_snapshot).get("description_en"),
            "user_selectable": bool(policy_controls(row.policy_snapshot).get("user_selectable", True)),
        } for row in versions],
        "engines": [{
            "id": row.id, "code": row.code, "engine_version": row.engine_version,
            "adapter_version": row.adapter_version, "source_hash": row.source_hash,
            "active": row.active, "manifest": row.manifest,
        } for row in engines],
    }


@router.get("/api/admin/financial-policy/versions/{version_id}")
def get_admin_financial_policy_version(version_id: str, user: User = Depends(require_permissions("admin.financial_policy")), db: Session = Depends(get_db)):
    current = current_policy_version(db)
    row = db.get(FinancialPolicyVersion, version_id)
    if not row or row.financial_policy_id != current.financial_policy_id:
        raise HTTPException(status_code=404, detail="Financial policy version not found")
    return {
        "id": row.id, "version_number": row.version_number, "status": row.status,
        "effective_from": row.effective_from, "snapshot_hash": row.snapshot_hash,
        "change_reason": row.change_reason, "is_current": row.id == current.id,
        "controls": policy_controls(row.policy_snapshot),
    }


@router.post("/api/admin/financial-policy/versions/{version_id}/activate")
def activate_admin_financial_policy_version(version_id: str, request: Request, user: User = Depends(require_permissions("admin.financial_policy")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    current = current_policy_version(db)
    row = db.get(FinancialPolicyVersion, version_id)
    if not row or row.financial_policy_id != current.financial_policy_id:
        raise HTTPException(status_code=404, detail="Financial policy version not found")
    try:
        activate_policy_version(db, version=row, user=user)
        audit(db, user=user, action="FINANCIAL_POLICY_VERSION_ACTIVATED", entity_type="FINANCIAL_POLICY_VERSION", entity_id=row.id, before={"current_version_id": current.id}, after={"current_version_id": row.id, "version_number": row.version_number}, ip=request.client.host if request.client else None)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "id": row.id, "version_number": row.version_number}


@router.patch("/api/admin/financial-policy/versions/{version_id}/status")
def update_admin_financial_policy_version_status(version_id: str, payload: dict, request: Request, user: User = Depends(require_permissions("admin.financial_policy")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    current = current_policy_version(db)
    row = db.get(FinancialPolicyVersion, version_id)
    if not row or row.financial_policy_id != current.financial_policy_id:
        raise HTTPException(status_code=404, detail="Financial policy version not found")
    before = {"status": row.status}
    try:
        set_policy_version_status(db, version=row, status=str(payload.get("status") or ""), user=user)
        audit(db, user=user, action="FINANCIAL_POLICY_VERSION_STATUS_CHANGED", entity_type="FINANCIAL_POLICY_VERSION", entity_id=row.id, before=before, after={"status": row.status}, ip=request.client.host if request.client else None)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "id": row.id, "version_number": row.version_number, "status": row.status}


@router.post("/api/admin/financial-policy/versions", status_code=201)
def create_admin_financial_policy_version(payload: dict, request: Request, user: User = Depends(require_permissions("admin.financial_policy")), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    controls = payload.get("controls") if isinstance(payload.get("controls"), dict) else payload
    reason = str(payload.get("change_reason") or "Financial policy update").strip()
    source_version = None
    if payload.get("source_version_id"):
        source_version = db.get(FinancialPolicyVersion, str(payload.get("source_version_id")))
        current = current_policy_version(db)
        if not source_version or source_version.financial_policy_id != current.financial_policy_id:
            raise HTTPException(status_code=404, detail="Source financial policy version not found")
    activate = bool(payload.get("activate", True))
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="A policy change reason is required")
    try:
        row = create_policy_version(
            db, controls=controls, user=user, change_reason=reason,
            source_version=source_version, activate=activate,
        )
        audit(db, user=user, action="FINANCIAL_POLICY_VERSION_PUBLISHED", entity_type="FINANCIAL_POLICY_VERSION", entity_id=row.id, after={"version_number": row.version_number, "snapshot_hash": row.snapshot_hash, "change_reason": reason, "source_version_id": source_version.id if source_version else None, "activated": activate}, ip=request.client.host if request.client else None)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": row.id, "version_number": row.version_number, "snapshot_hash": row.snapshot_hash, "controls": policy_controls(row.policy_snapshot)}
