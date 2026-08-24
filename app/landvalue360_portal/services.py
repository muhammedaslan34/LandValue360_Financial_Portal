from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .calculations import calculate_project
from .config import get_settings
from .models import *
from .security import hash_password, user_permission_codes, user_role_codes
from .workflow import transition_project

ROLE_DEFINITIONS = {
    "LANDOWNER": ("صاحب أرض", "Landowner"),
    "ANALYST": ("محلل", "Analyst"),
    "REVIEWER": ("مدقق", "Reviewer"),
    "TEAM_MANAGER": ("مدير فريق", "Team Manager"),
    "PLATFORM_ADMIN": ("مدير المنصة", "Platform Admin"),
}

PERMISSIONS = {
    "project.create": "Create projects",
    "project.view_own": "View organization projects",
    "project.edit_draft": "Edit draft versions",
    "project.submit": "Submit project versions",
    "project.respond_information": "Respond to information requests",
    "report.download_published": "Download published reports",
    "ops.view_assigned": "View assigned operational projects",
    "ops.view_all": "View all operational projects",
    "ops.assign": "Assign analyst and reviewer",
    "ops.change_status": "Change project workflow status",
    "ops.request_information": "Request additional information",
    "ops.internal_note": "Create internal notes",
    "ops.export": "Export analysis packages",
    "ops.report_upload": "Upload report drafts",
    "ops.report_review": "Review reports",
    "ops.report_publish": "Publish approved reports",
    "admin.users": "Manage users and memberships",
    "admin.settings": "Manage system settings",
    "admin.audit": "Read audit logs",
    "admin.projects": "Manage all projects",
    "financial.view": "View financial models and calculation runs",
    "financial.edit": "Edit standard financial model inputs",
    "financial.advanced_inputs": "View and edit advanced financing, collection and timing controls",
    "financial.run": "Execute authoritative financial calculations",
    "financial.export": "Export financial PDF and Excel reports",
    "admin.financial_policy": "Manage versioned financial policies",
}

ROLE_PERMISSIONS = {
    "LANDOWNER": {"project.create", "project.view_own", "project.edit_draft", "project.submit", "project.respond_information", "report.download_published", "financial.view", "financial.edit", "financial.run", "financial.export"},
    "ANALYST": {"ops.view_assigned", "ops.change_status", "ops.request_information", "ops.internal_note", "ops.export", "ops.report_upload", "financial.view", "financial.edit", "financial.advanced_inputs", "financial.run", "financial.export"},
    "REVIEWER": {"ops.view_assigned", "ops.change_status", "ops.internal_note", "ops.report_review", "financial.view", "financial.export"},
    "TEAM_MANAGER": {"ops.view_assigned", "ops.view_all", "ops.assign", "ops.change_status", "ops.request_information", "ops.internal_note", "ops.export", "ops.report_upload", "ops.report_review", "ops.report_publish", "financial.view", "financial.edit", "financial.advanced_inputs", "financial.run", "financial.export"},
    "PLATFORM_ADMIN": set(PERMISSIONS),
}


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value[:100] or "organization"


def seed_defaults(db: Session) -> None:
    permission_by_code: dict[str, Permission] = {}
    for code, description in PERMISSIONS.items():
        row = db.scalar(select(Permission).where(Permission.code == code))
        if not row:
            row = Permission(code=code, description=description)
            db.add(row)
            db.flush()
        permission_by_code[code] = row
    for code, (ar, en) in ROLE_DEFINITIONS.items():
        role = db.scalar(select(Role).where(Role.code == code))
        if not role:
            role = Role(code=code, name_ar=ar, name_en=en, system_role=True)
            db.add(role)
            db.flush()
        existing = set(db.scalars(
            select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id).where(RolePermission.role_id == role.id)
        ).all())
        for pcode in ROLE_PERMISSIONS[code] - existing:
            db.add(RolePermission(role_id=role.id, permission_id=permission_by_code[pcode].id))
    for ext, mimes in {
        ".pdf": ["application/pdf"],
        ".docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"],
        ".xlsx": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"],
        ".jpg": ["image/jpeg"], ".jpeg": ["image/jpeg"], ".png": ["image/png"],
    }.items():
        row = db.scalar(select(FileTypePolicy).where(FileTypePolicy.extension == ext))
        if not row:
            db.add(FileTypePolicy(extension=ext, mime_types=mimes, max_size_bytes=get_settings().max_upload_mb * 1024 * 1024, active=True))
    email_defaults = {
        "VERIFY_EMAIL": ("تأكيد البريد الإلكتروني", "Email verification", "يرجى تأكيد بريدك الإلكتروني عبر الرابط التالي:\n{link}", "Please verify your email using the following link:\n{link}"),
        "PASSWORD_RESET": ("استعادة كلمة المرور", "Password reset", "استخدم الرابط الآمن لتعيين كلمة مرور جديدة:\n{link}", "Use the secure link to set a new password:\n{link}"),
        "MISSING_INFORMATION": ("معلومات إضافية مطلوبة", "Additional information required", "{body}\n{link}", "{body}\n{link}"),
        "PROJECT_STATUS": ("تحديث حالة المشروع", "Project status update", "{body}\n{link}", "{body}\n{link}"),
        "PROJECT_ASSIGNED": ("تم إسناد مشروع", "Project assigned", "{body}\n{link}", "{body}\n{link}"),
        "REPORT_READY": ("تقارير المشروع جاهزة", "Project reports are ready", "{body}\n{link}", "{body}\n{link}"),
    }
    for code, (subject_ar, subject_en, body_ar, body_en) in email_defaults.items():
        if not db.scalar(select(EmailTemplate).where(EmailTemplate.code == code)):
            db.add(EmailTemplate(code=code, subject_ar=subject_ar, subject_en=subject_en, body_ar=body_ar, body_en=body_en, active=True))
    defaults = {
        "sla": {"hours": get_settings().sla_hours, "timezone": get_settings().timezone, "pause_during_missing_information": True},
        "retention": {"completed_project_years": 7, "deleted_account_grace_days": 30},
        "portal": {"default_language": "ar", "developer_registration_enabled": False},
    }
    for key, value in defaults.items():
        if not db.scalar(select(SystemSetting).where(SystemSetting.key == key)):
            db.add(SystemSetting(key=key, value=value))
    from .financial_service import seed_financial_defaults
    seed_financial_defaults(db)
    db.flush()


def assign_role(db: Session, membership: OrganizationMember, role_code: str) -> None:
    role = db.scalar(select(Role).where(Role.code == role_code))
    if not role:
        raise ValueError(f"Unknown role: {role_code}")
    if not db.scalar(select(MemberRole).where(MemberRole.membership_id == membership.id, MemberRole.role_id == role.id)):
        db.add(MemberRole(membership_id=membership.id, role_id=role.id))
        db.flush()


def create_personal_landowner(db: Session, *, email: str, password: str, full_name: str, organization_name: str, country: str | None, phone: str | None) -> tuple[User, Organization]:
    if db.scalar(select(User).where(func.lower(User.email) == email.lower())):
        raise ValueError("Email is already registered")
    user = User(
        email=email.lower(), password_hash=hash_password(password), full_name=full_name,
        email_verified_at=utcnow() if get_settings().auto_verify_email else None,
        password_changed_at=utcnow(), must_change_password=False,
    )
    db.add(user); db.flush()
    db.add(Profile(user_id=user.id, phone=phone, country=country, preferred_language="ar", applicant_type="LANDOWNER", created_by=user.id, updated_by=user.id))
    base = slugify(organization_name)
    slug = base
    counter = 2
    while db.scalar(select(Organization).where(Organization.slug == slug)):
        slug = f"{base}-{counter}"; counter += 1
    org = Organization(name=organization_name, slug=slug, kind="LANDOWNER", created_by=user.id, updated_by=user.id)
    db.add(org); db.flush()
    membership = OrganizationMember(organization_id=org.id, user_id=user.id, status="ACTIVE", is_owner=True, created_by=user.id, updated_by=user.id)
    db.add(membership); db.flush()
    assign_role(db, membership, "LANDOWNER")
    db.add(UserConsent(user_id=user.id, consent_type="TERMS_AND_PROFESSIONAL_DECLARATION", text_version="1.0", accepted=True, created_by=user.id, updated_by=user.id))
    audit(db, user=user, organization_id=org.id, action="USER_REGISTERED", entity_type="USER", entity_id=user.id, after={"email": user.email})
    return user, org


def create_staff_user(
    db: Session, *, email: str, password: str, full_name: str, role_code: str,
    must_change_password: bool = False,
) -> User:
    org = db.scalar(select(Organization).where(Organization.slug == "lv360-operations"))
    if not org:
        org = Organization(name="LandValue360 Operations", slug="lv360-operations", kind="INTERNAL")
        db.add(org); db.flush()
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower()))
    if not user:
        user = User(
            email=email.lower(), password_hash=hash_password(password), full_name=full_name,
            email_verified_at=utcnow(), password_changed_at=utcnow(),
            must_change_password=must_change_password,
        )
        db.add(user); db.flush()
        db.add(Profile(user_id=user.id, preferred_language="ar", applicant_type="INTERNAL", created_by=user.id, updated_by=user.id))
    membership = db.scalar(select(OrganizationMember).where(OrganizationMember.organization_id == org.id, OrganizationMember.user_id == user.id))
    if not membership:
        membership = OrganizationMember(organization_id=org.id, user_id=user.id, status="ACTIVE", is_owner=role_code == "PLATFORM_ADMIN")
        db.add(membership); db.flush()
    assign_role(db, membership, role_code)
    return user


def audit(db: Session, *, user: User | None, action: str, entity_type: str, entity_id: str | None = None, organization_id: str | None = None, project_id: str | None = None, before: Any = None, after: Any = None, ip: str | None = None) -> None:
    db.add(AuditLog(user_id=user.id if user else None, organization_id=organization_id, project_id=project_id, action=action, entity_type=entity_type, entity_id=entity_id, before_data=before, after_data=after, ip_address=ip))


def user_org_ids(db: Session, user_id: str) -> set[str]:
    return set(db.scalars(select(OrganizationMember.organization_id).where(OrganizationMember.user_id == user_id, OrganizationMember.status == "ACTIVE", OrganizationMember.deleted_at.is_(None))).all())


def may_view_project(db: Session, user: User, project: Project) -> bool:
    perms = user_permission_codes(db, user.id)
    if "ops.view_all" in perms or "admin.projects" in perms:
        return True
    if "ops.view_assigned" in perms:
        if db.scalar(select(ProjectAssignment.id).where(ProjectAssignment.project_id == project.id, ProjectAssignment.user_id == user.id, ProjectAssignment.active.is_(True))):
            return True
    return project.organization_id in user_org_ids(db, user.id)


def require_project(db: Session, user: User, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at or not may_view_project(db, user, project):
        raise PermissionError("Project is unavailable")
    return project


def current_version(db: Session, project: Project) -> ProjectVersion:
    if project.current_version_id:
        row = db.get(ProjectVersion, project.current_version_id)
        if row:
            return row
    row = db.scalar(select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.version_number.desc()))
    if not row:
        raise ValueError("Project has no version")
    return row


def empty_snapshot(name: str, currency: str = "USD", financial_controls: dict[str, Any] | None = None) -> dict[str, Any]:
    from .financial_engine import default_financial_model
    planning = {"far": "0", "bcr": None, "planning_status": "", "project_duration_months": 36, "sales_duration_months": 36}
    return {
        "identity": {"name": name, "description": "", "currency": currency},
        "land": {"gross_land_area_sqm": "0", "excluded_land_area_sqm": "0", "title_reference": "", "location": "", "current_land_value": None, "currency": currency},
        "planning": planning,
        "land_uses": [], "products": [], "costs": [],
        "financial_model": default_financial_model(planning=planning, controls=financial_controls),
    }


def create_project(db: Session, *, user: User, organization_id: str, name: str, description: str | None, currency: str = "USD") -> Project:
    if organization_id not in user_org_ids(db, user.id):
        raise PermissionError("Organization is unavailable")
    count = db.scalar(select(func.count(Project.id)).where(Project.organization_id == organization_id)) or 0
    reference = f"LV-{utcnow().strftime('%Y%m%d')}-{count + 1:04d}"
    project = Project(organization_id=organization_id, owner_user_id=user.id, reference=reference, name=name, description=description, status="DRAFT", created_by=user.id, updated_by=user.id)
    db.add(project); db.flush()
    from .financial_engine import policy_controls
    from .financial_service import current_policy_version
    snapshot = empty_snapshot(name, currency, policy_controls(current_policy_version(db).policy_snapshot))
    version = ProjectVersion(project_id=project.id, version_number=1, status="DRAFT", immutable=False, input_snapshot=snapshot, created_by=user.id, updated_by=user.id)
    db.add(version); db.flush()
    project.current_version_id = version.id
    persist_snapshot(db, version, snapshot, user=user)
    db.add(ProjectStatusHistory(project_id=project.id, from_status=None, to_status="DRAFT", reason="Project created", changed_by=user.id))
    audit(db, user=user, organization_id=organization_id, project_id=project.id, action="PROJECT_CREATED", entity_type="PROJECT", entity_id=project.id, after={"reference": reference})
    return project




def record_analysis_export(db: Session, *, project: Project, version: ProjectVersion, export_type: str, package_version: str, data: bytes, user: User) -> AnalysisExport:
    row = AnalysisExport(
        project_id=project.id, project_version_id=version.id, export_type=export_type,
        package_version=package_version, checksum=hashlib.sha256(data).hexdigest(),
        created_by=user.id, updated_by=user.id,
    )
    db.add(row); db.flush()
    return row

def _decimal_str(value: Any) -> str:
    if value is None:
        return "0"
    return str(value)


def persist_snapshot(db: Session, version: ProjectVersion, snapshot: dict[str, Any], *, user: User) -> dict[str, Any]:
    if version.immutable:
        raise ValueError("Submitted versions are immutable")
    from .financial_engine import default_financial_model, normalize_financial_model, policy_controls
    from .financial_service import current_policy_version
    snapshot = deepcopy(snapshot)
    controls = policy_controls(current_policy_version(db).policy_snapshot)
    existing_financial = (version.input_snapshot or {}).get("financial_model")
    snapshot["financial_model"] = normalize_financial_model(
        snapshot.get("financial_model") or existing_financial or default_financial_model(planning=snapshot.get("planning") or {}, controls=controls),
        planning=snapshot.get("planning") or {},
        controls=controls,
    )
    # Rebuild normalized version rows atomically.
    for model in (LandInput, PlanningInput, LandUseAllocation, ProductAllocation, CostItem, CalculationCheck):
        db.execute(delete(model).where(model.project_version_id == version.id))
    land = snapshot.get("land") or {}
    planning = snapshot.get("planning") or {}
    db.add(LandInput(
        project_version_id=version.id,
        gross_land_area_sqm=Decimal(str(land.get("gross_land_area_sqm") or "0")),
        excluded_land_area_sqm=Decimal(str(land.get("excluded_land_area_sqm") or "0")),
        title_reference=land.get("title_reference"), location=land.get("location"),
        current_land_value=Decimal(str(land["current_land_value"])) if land.get("current_land_value") not in (None, "") else None,
        currency=land.get("currency") or snapshot.get("identity", {}).get("currency") or "USD",
        created_by=user.id, updated_by=user.id,
    ))
    db.add(PlanningInput(
        project_version_id=version.id,
        far=Decimal(str(planning.get("far") or "0")),
        bcr=Decimal(str(planning["bcr"])) if planning.get("bcr") not in (None, "") else None,
        planning_status=planning.get("planning_status"),
        project_duration_months=planning.get("project_duration_months"),
        sales_duration_months=planning.get("sales_duration_months"),
        created_by=user.id, updated_by=user.id,
    ))
    for row in snapshot.get("land_uses") or []:
        db.add(LandUseAllocation(project_version_id=version.id, code=row["code"], name=row["name"], percentage=Decimal(str(row["percentage"])), created_by=user.id, updated_by=user.id))
    for row in snapshot.get("products") or []:
        product = ProductAllocation(project_version_id=version.id, code=row["code"], name=row["name"], allocation_percentage=Decimal(str(row["allocation_percentage"])), sellable_efficiency_percentage=Decimal(str(row["sellable_efficiency_percentage"])), unit_selling_price=Decimal(str(row["unit_selling_price"])), currency=row.get("currency") or "USD", created_by=user.id, updated_by=user.id)
        db.add(product); db.flush()
        db.add(ProductPricing(product_allocation_id=product.id, price_source=row.get("price_source"), evidence_confidence=row.get("evidence_confidence"), created_by=user.id, updated_by=user.id))
    for row in snapshot.get("costs") or []:
        amount = row.get("amount")
        db.add(CostItem(project_version_id=version.id, name=row["name"], category=row["category"], amount=Decimal(str(amount)) if amount not in (None, "") else None, currency=row.get("currency") or "USD", quantity_basis=row.get("quantity_basis"), quantity=Decimal(str(row["quantity"])) if row.get("quantity") not in (None, "") else None, unit_cost=Decimal(str(row["unit_cost"])) if row.get("unit_cost") not in (None, "") else None, developer_share_percentage=Decimal(str(row.get("developer_share_percentage", "100"))), net_sales_deductible=bool(row.get("net_sales_deductible")), notes=row.get("notes"), source=row.get("source"), evidence_confidence=row.get("evidence_confidence"), created_by=user.id, updated_by=user.id))
    result = calculate_project(snapshot)
    for check in result["checks"]:
        db.add(CalculationCheck(project_version_id=version.id, code=check["code"], status=check["status"], actual_value=check["actual_value"], required_value=check["required_value"], message_ar=check["message_ar"], message_en=check["message_en"], created_by=user.id, updated_by=user.id))
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    version.input_snapshot = deepcopy(snapshot)
    version.snapshot_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    version.completeness_percent = completeness(snapshot, result)
    version.updated_by = user.id
    db.flush()
    return result


def completeness(snapshot: dict[str, Any], result: dict[str, Any]) -> Decimal:
    expected = 8
    score = 0
    identity = snapshot.get("identity") or {}
    land = snapshot.get("land") or {}
    planning = snapshot.get("planning") or {}
    score += bool(identity.get("name"))
    score += Decimal(str(land.get("gross_land_area_sqm") or 0)) > 0
    score += Decimal(str(planning.get("far") or 0)) > 0
    score += bool(snapshot.get("land_uses"))
    score += bool(snapshot.get("products"))
    score += bool(snapshot.get("costs"))
    score += abs(Decimal(result["land_use_percentage_total"]) - Decimal("100")) <= Decimal("0.0001")
    score += abs(Decimal(result["product_allocation_percentage_total"]) - Decimal("100")) <= Decimal("0.0001")
    return (Decimal(score) / Decimal(expected) * Decimal("100")).quantize(Decimal("0.01"))


def snapshot_from_db(db: Session, version: ProjectVersion) -> dict[str, Any]:
    # Stored JSON is authoritative; normalized rows support operations/reporting.
    return deepcopy(version.input_snapshot or {})


def create_revision(db: Session, *, project: Project, user: User, reason: str) -> ProjectVersion:
    source = current_version(db, project)
    next_number = (db.scalar(select(func.max(ProjectVersion.version_number)).where(ProjectVersion.project_id == project.id)) or 0) + 1
    version = ProjectVersion(project_id=project.id, version_number=next_number, status="DRAFT", immutable=False, change_reason=reason, input_snapshot=deepcopy(source.input_snapshot), created_by=user.id, updated_by=user.id)
    db.add(version); db.flush()
    project.current_version_id = version.id
    persist_snapshot(db, version, version.input_snapshot, user=user)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="PROJECT_REVISION_CREATED", entity_type="PROJECT_VERSION", entity_id=version.id, after={"version_number": next_number, "reason": reason})
    return version


def submit_project(db: Session, *, project: Project, user: User) -> dict[str, Any]:
    version = current_version(db, project)
    if version.immutable:
        raise ValueError("Current version is already submitted")
    result = calculate_project(version.input_snapshot)
    if not result["can_submit"]:
        raise ValueError("Project cannot be submitted until all validation checks pass")
    version.immutable = True
    version.status = "SUBMITTED"
    version.submitted_at = utcnow()
    target = "DATA_REVIEW" if project.status == "MISSING_INFORMATION" else "SUBMITTED"
    transition_project(db, project, target, user=user, reason="Client submitted a complete project version", sla_hours=get_settings().sla_hours)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="PROJECT_SUBMITTED", entity_type="PROJECT_VERSION", entity_id=version.id, after={"version": version.version_number, "hash": version.snapshot_hash})
    return result
