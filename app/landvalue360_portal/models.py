from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint,
    Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


MONEY = Numeric(24, 6)
RATE = Numeric(12, 6)
AREA = Numeric(24, 6)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Profile(Base, TimestampMixin):
    __tablename__ = "profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(120))
    preferred_language: Mapped[str] = mapped_column(String(8), default="ar", nullable=False)
    applicant_type: Mapped[str] = mapped_column(String(40), default="LANDOWNER", nullable=False)


class Organization(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="LANDOWNER", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OrganizationMember(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_org_member"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Role(Base, TimestampMixin):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name_ar: Mapped[str] = mapped_column(String(160), nullable=False)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    system_role: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Permission(Base, TimestampMixin):
    __tablename__ = "permissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[str] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False)


class MemberRole(Base):
    __tablename__ = "member_roles"
    __table_args__ = (UniqueConstraint("membership_id", "role_id", name="uq_member_role"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    membership_id: Mapped[str] = mapped_column(ForeignKey("organization_members.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)


class AccessSession(Base, TimestampMixin):
    __tablename__ = "access_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(80))
    user_agent: Mapped[str | None] = mapped_column(String(500))


class OneTimeToken(Base, TimestampMixin):
    __tablename__ = "one_time_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "reference", name="uq_project_reference"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    reference: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL", nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_for_analysis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectVersion(Base, TimestampMixin):
    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "version_number", name="uq_project_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)
    immutable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_hash: Mapped[str | None] = mapped_column(String(128))
    completeness_percent: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0"), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProjectStatusHistory(Base):
    __tablename__ = "project_status_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProjectAssignment(Base, TimestampMixin):
    __tablename__ = "project_assignments"
    __table_args__ = (UniqueConstraint("project_id", "assignment_type", name="uq_project_assignment_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assignment_type: Mapped[str] = mapped_column(String(30), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LandInput(Base, TimestampMixin):
    __tablename__ = "land_inputs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), unique=True, nullable=False)
    gross_land_area_sqm: Mapped[Decimal] = mapped_column(AREA, default=Decimal("0"), nullable=False)
    excluded_land_area_sqm: Mapped[Decimal] = mapped_column(AREA, default=Decimal("0"), nullable=False)
    title_reference: Mapped[str | None] = mapped_column(String(250))
    location: Mapped[str | None] = mapped_column(String(300))
    current_land_value: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)


class PlanningInput(Base, TimestampMixin):
    __tablename__ = "planning_inputs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), unique=True, nullable=False)
    far: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0"), nullable=False)
    bcr: Mapped[Decimal | None] = mapped_column(RATE)
    planning_status: Mapped[str | None] = mapped_column(String(200))
    project_duration_months: Mapped[int | None] = mapped_column(Integer)
    sales_duration_months: Mapped[int | None] = mapped_column(Integer)


class LandUseAllocation(Base, TimestampMixin):
    __tablename__ = "land_use_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    percentage: Mapped[Decimal] = mapped_column(RATE, nullable=False)


class ProductAllocation(Base, TimestampMixin):
    __tablename__ = "product_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    allocation_percentage: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    sellable_efficiency_percentage: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    unit_selling_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)


class ProductPricing(Base, TimestampMixin):
    __tablename__ = "product_pricing"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    product_allocation_id: Mapped[str] = mapped_column(ForeignKey("product_allocations.id", ondelete="CASCADE"), unique=True, nullable=False)
    price_source: Mapped[str | None] = mapped_column(String(250))
    evidence_confidence: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)


class CostItem(Base, TimestampMixin):
    __tablename__ = "cost_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    quantity_basis: Mapped[str | None] = mapped_column(String(80))
    quantity: Mapped[Decimal | None] = mapped_column(AREA)
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    developer_share_percentage: Mapped[Decimal] = mapped_column(RATE, default=Decimal("100"), nullable=False)
    net_sales_deductible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(250))
    evidence_confidence: Mapped[str | None] = mapped_column(String(30))


class ProjectDocument(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "project_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    project_version_id: Mapped[str | None] = mapped_column(ForeignKey("project_versions.id", ondelete="SET NULL"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    original_name: Mapped[str] = mapped_column(String(300), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(160), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)


class InformationRequest(Base, TimestampMixin):
    __tablename__ = "information_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InformationRequestMessage(Base, TimestampMixin):
    __tablename__ = "information_request_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("information_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    internal_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AnalysisExport(Base, TimestampMixin):
    __tablename__ = "analysis_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id"), nullable=False)
    export_type: Mapped[str] = mapped_column(String(40), nullable=False)
    package_version: Mapped[str] = mapped_column(String(30), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(500))


class AnalysisImport(Base, TimestampMixin):
    __tablename__ = "analysis_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id"), nullable=False)
    imported_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(250))
    calculation_run_reference: Mapped[str | None] = mapped_column(String(250))
    status: Mapped[str] = mapped_column(String(30), default="RECEIVED", nullable=False)


class Report(Base, TimestampMixin):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(60), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="ar", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportVersion(Base, TimestampMixin):
    __tablename__ = "report_versions"
    __table_args__ = (UniqueConstraint("report_id", "version_number", name="uq_report_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_name: Mapped[str] = mapped_column(String(300), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), default="application/pdf", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    calculation_run_reference: Mapped[str | None] = mapped_column(String(250))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)


class ReportDownload(Base):
    __tablename__ = "report_downloads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    report_version_id: Mapped[str] = mapped_column(ForeignKey("report_versions.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(80))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(String(500))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationOutbox(Base, TimestampMixin):
    __tablename__ = "notification_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    template_code: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    identifier: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(80), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class PrivacyRequest(Base, TimestampMixin):
    __tablename__ = "privacy_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ProjectDeclaration(Base, TimestampMixin):
    __tablename__ = "project_declarations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False)
    declaration_code: Mapped[str] = mapped_column(String(80), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    text_version: Mapped[str] = mapped_column(String(30), nullable=False)
    accepted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CalculationCheck(Base, TimestampMixin):
    __tablename__ = "calculation_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    actual_value: Mapped[str | None] = mapped_column(String(250))
    required_value: Mapped[str | None] = mapped_column(String(250))
    message_ar: Mapped[str] = mapped_column(String(500), nullable=False)
    message_en: Mapped[str] = mapped_column(String(500), nullable=False)


class UserConsent(Base, TimestampMixin):
    __tablename__ = "user_consents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(80), nullable=False)
    text_version: Mapped[str] = mapped_column(String(30), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(80))


class EmailTemplate(Base, TimestampMixin):
    __tablename__ = "email_templates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    subject_ar: Mapped[str] = mapped_column(String(250), nullable=False)
    subject_en: Mapped[str] = mapped_column(String(250), nullable=False)
    body_ar: Mapped[str] = mapped_column(Text, nullable=False)
    body_en: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FileTypePolicy(Base, TimestampMixin):
    __tablename__ = "file_type_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    extension: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    mime_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    max_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FinancialPolicy(Base, TimestampMixin):
    __tablename__ = "financial_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class FinancialPolicyVersion(Base, TimestampMixin):
    __tablename__ = "financial_policy_versions"
    __table_args__ = (UniqueConstraint("financial_policy_id", "version_number", name="uq_financial_policy_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    financial_policy_id: Mapped[str] = mapped_column(ForeignKey("financial_policies.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PUBLISHED", nullable=False, index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    immutable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class EngineVersion(Base, TimestampMixin):
    __tablename__ = "engine_versions"
    __table_args__ = (
        UniqueConstraint(
            "code", "engine_version", "adapter_version", "source_hash",
            name="uq_engine_version_release",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class CalculationRun(Base, TimestampMixin):
    __tablename__ = "calculation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    project_version_id: Mapped[str] = mapped_column(ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    financial_policy_version_id: Mapped[str] = mapped_column(ForeignKey("financial_policy_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    engine_version_id: Mapped[str] = mapped_column(ForeignKey("engine_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False, index=True)
    run_type: Mapped[str] = mapped_column(String(30), default="BASE_CASE", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    selected_contract_method: Mapped[str | None] = mapped_column(String(40))
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    executed_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)


class CalculationRunResult(Base, TimestampMixin):
    __tablename__ = "calculation_run_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    calculation_run_id: Mapped[str] = mapped_column(ForeignKey("calculation_runs.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    calculation_status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    policy_compliant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reconciliation_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    financial_truth: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    residual_valuation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    annual_cashflow: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    selected_contract: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    constraints: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    full_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MonthlyCashflowSnapshot(Base, TimestampMixin):
    __tablename__ = "monthly_cashflow_snapshots"
    __table_args__ = (UniqueConstraint("calculation_run_id", "month_number", name="uq_run_month_snapshot"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    calculation_run_id: Mapped[str] = mapped_column(ForeignKey("calculation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    month_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cashflow_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    opening_cash: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    gross_contracted_sales: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    gross_collections: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    net_collections: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    planned_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    actual_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    deferred_cost: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    equity_contribution: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    financing_draw: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    interest_paid: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    financing_fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    financing_repayment: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    landowner_payment: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    developer_distribution: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    ending_cash: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    ending_debt: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    funding_gap: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    contractual_arrears: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    cash_balance_variance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class NegotiationResult(Base, TimestampMixin):
    __tablename__ = "negotiation_results"
    __table_args__ = (UniqueConstraint("calculation_run_id", "method", name="uq_run_negotiation_method"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    calculation_run_id: Mapped[str] = mapped_column(ForeignKey("calculation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    measure_type: Mapped[str] = mapped_column(String(20), nullable=False)
    fair_floor: Mapped[Decimal | None] = mapped_column(MONEY)
    balanced: Mapped[Decimal | None] = mapped_column(MONEY)
    technical_ceiling: Mapped[Decimal | None] = mapped_column(MONEY)
    negotiation_minimum: Mapped[Decimal | None] = mapped_column(MONEY)
    negotiation_maximum: Mapped[Decimal | None] = mapped_column(MONEY)
    governing_constraint_id: Mapped[str | None] = mapped_column(String(120))
    recommendation_rank: Mapped[int | None] = mapped_column(Integer)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


Index("ix_project_status_org", Project.organization_id, Project.status)
Index("ix_version_project_status", ProjectVersion.project_id, ProjectVersion.status)
Index("ix_report_project_status", Report.project_id, Report.status)
Index("ix_calculation_run_project_created", CalculationRun.project_id, CalculationRun.created_at)
Index("ix_calculation_run_inputs", CalculationRun.project_version_id, CalculationRun.financial_policy_version_id, CalculationRun.engine_version_id, CalculationRun.input_hash)
Index("ix_monthly_cashflow_run_date", MonthlyCashflowSnapshot.calculation_run_id, MonthlyCashflowSnapshot.cashflow_date)
