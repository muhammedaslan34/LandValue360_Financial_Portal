"""SQLAlchemy persistence model for release 0.3.

The model intentionally stores immutable JSON snapshots for project inputs,
policies, calculation inputs, and calculation outputs. Release 0.3 does not
normalize the calculation kernel's full domain into hundreds of tables; that
normalization is introduced incrementally after the versioning and audit
contracts are proven.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


UUID_LENGTH = 36
HASH_LENGTH = 64


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_workspace_org_slug"),
        Index("ix_workspaces_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "organization_id", "scope_key", name="uq_membership_user_scope"
        ),
        Index("ix_memberships_org_workspace", "organization_id", "workspace_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    scope_key: Mapped[str] = mapped_column(String(UUID_LENGTH), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    product_access: Mapped[str] = mapped_column(String(24), nullable=False, default="BOTH")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AccessSession(Base):
    __tablename__ = "access_sessions"
    __table_args__ = (
        Index("ix_access_sessions_user_active", "user_id", "revoked_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    membership_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    edition_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="COMBINED")
    token_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_portfolio_workspace_code"),
        Index("ix_portfolios_tenant", "organization_id", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_project_workspace_code"),
        Index("ix_projects_tenant", "organization_id", "workspace_id", "status"),
        Index("ix_projects_kind", "organization_id", "workspace_id", "project_kind", "status"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    portfolio_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("portfolios.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="DEVELOPER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )


class ProjectVersion(Base, TimestampMixin):
    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_project_version_number"),
        CheckConstraint("version_number > 0", name="ck_project_version_positive"),
        Index("ix_project_versions_tenant", "organization_id", "workspace_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    source_input_schema: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source_input_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Scenario(Base, TimestampMixin):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("project_version_id", "code", name="uq_scenario_version_code"),
        Index("ix_scenarios_tenant", "organization_id", "workspace_id", "project_version_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    override_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    override_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )


class PolicyPack(Base, TimestampMixin):
    __tablename__ = "policy_packs"
    __table_args__ = (
        UniqueConstraint("organization_id", "scope_key", "code", name="uq_policy_pack_scope_code"),
        Index("ix_policy_packs_tenant", "organization_id", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    scope_key: Mapped[str] = mapped_column(String(UUID_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )


class PolicyPackVersion(Base, TimestampMixin):
    __tablename__ = "policy_pack_versions"
    __table_args__ = (
        UniqueConstraint("policy_pack_id", "version_number", name="uq_policy_version_number"),
        CheckConstraint("version_number > 0", name="ck_policy_version_positive"),
        Index("ix_policy_versions_tenant", "organization_id", "workspace_id", "policy_pack_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    policy_pack_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_packs.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    published_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CalculationRun(Base):
    __tablename__ = "calculation_runs"
    __table_args__ = (
        Index("ix_calculation_runs_tenant", "organization_id", "workspace_id", "project_id"),
        Index("ix_calculation_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    policy_pack_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    valuation_policy_pack_version_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=True
    )
    replayed_from_run_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("calculation_runs.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="PENDING")
    case_id: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_version: Mapped[str] = mapped_column(String(32), nullable=False)
    calculation_model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    output_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    calculation_validity: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_RUN")
    economic_feasibility: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_ASSESSED")
    policy_compliance: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_ASSESSED")
    evidence_readiness: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_REQUIRED")
    report_readiness: Mapped[str] = mapped_column(String(40), nullable=False, default="NOT_READY")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceDocument(Base, TimestampMixin):
    __tablename__ = "evidence_documents"
    __table_args__ = (
        Index("ix_evidence_tenant_project", "organization_id", "workspace_id", "project_id"),
        Index("ix_evidence_project_version", "project_version_id", "evidence_type", "status"),
        UniqueConstraint("storage_key", name="uq_evidence_storage_key"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True
    )
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(160), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(800), nullable=False)
    media_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    source_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    verified_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssumptionRecord(Base, TimestampMixin):
    __tablename__ = "assumption_records"
    __table_args__ = (
        UniqueConstraint("project_version_id", "assumption_key", name="uq_assumption_version_key"),
        Index("ix_assumptions_tenant_project", "organization_id", "workspace_id", "project_id"),
        Index("ix_assumptions_version_category", "project_version_id", "category", "criticality"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False
    )
    assumption_key: Mapped[str] = mapped_column(String(400), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    value_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    criticality: Mapped[str] = mapped_column(String(24), nullable=False, default="MEDIUM")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="MANUAL")
    source_reference: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evidence_document_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False, default="MISSING")
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValuationRun(Base):
    __tablename__ = "valuation_runs"
    __table_args__ = (
        Index("ix_valuation_runs_tenant", "organization_id", "workspace_id", "project_id"),
        Index("ix_valuation_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    calculation_run_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    policy_pack_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(24), nullable=False, default="PREVIEW")
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    basis_of_value: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    reporting_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    valuation_model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    output_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_tenant", "organization_id", "workspace_id", "project_id"),
        Index("ix_analysis_runs_type_created", "analysis_type", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    policy_pack_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    valuation_policy_pack_version_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=True
    )
    scenario_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    analysis_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    analysis_model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    output_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GovernmentCase(Base, TimestampMixin):
    """Governed public-land decision case with maker-checker workflow."""

    __tablename__ = "government_cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "case_code", name="uq_government_case_workspace_code"),
        Index("ix_government_cases_tenant", "organization_id", "workspace_id", "status"),
        Index("ix_government_cases_project", "project_id", "project_version_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    project_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("project_versions.id", ondelete="RESTRICT"), nullable=False
    )
    policy_pack_version_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    valuation_policy_pack_version_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("policy_pack_versions.id", ondelete="RESTRICT"), nullable=True
    )
    scenario_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    calculation_run_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("calculation_runs.id", ondelete="SET NULL"), nullable=True
    )
    case_code: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    mode: Mapped[str] = mapped_column(String(40), nullable=False, default="STRUCTURING")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="DRAFT")
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), nullable=False)
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)
    ledger_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), nullable=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    submitted_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    technical_reviewer_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    technical_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    technical_review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_reviewer_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    legal_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    legal_review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OverrideRecord(Base):
    """Append-only record of a governed input override."""

    __tablename__ = "override_records"
    __table_args__ = (
        Index("ix_override_records_tenant", "organization_id", "workspace_id", "government_case_id"),
        Index("ix_override_records_created", "government_case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    government_case_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("government_cases.id", ondelete="CASCADE"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(String(500), nullable=False)
    previous_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    document_reference: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=False
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tenant_time", "organization_id", "workspace_id", "occurred_at"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(UUID_LENGTH), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(UUID_LENGTH), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    edition_scope: Mapped[str | None] = mapped_column(String(24), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(UUID_LENGTH), nullable=True)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


def _immutable_record_before_update(mapper, connection, target) -> None:  # noqa: ANN001
    del mapper, connection
    raise ValueError(f"{target.__class__.__name__} records are append-only and cannot be updated.")


def _immutable_record_before_delete(mapper, connection, target) -> None:  # noqa: ANN001
    del mapper, connection
    raise ValueError(f"{target.__class__.__name__} records are append-only and cannot be deleted.")


def _calculation_run_before_update(mapper, connection, target: CalculationRun) -> None:  # noqa: ANN001
    """Keep calculation results append-only while allowing the explicit lock transition.

    Locking does not modify input/output snapshots, hashes or metrics.  It only
    records the timestamp and report-readiness state after the readiness gate
    has passed.  Every other mutation remains forbidden.
    """
    del mapper, connection
    state = inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.history.has_changes()
    }
    allowed = {"locked_at", "report_readiness"}
    if changed - allowed:
        raise ValueError(
            "CalculationRun financial records are append-only; only the governed lock fields may be updated."
        )


event.listen(CalculationRun, "before_update", _calculation_run_before_update)
event.listen(CalculationRun, "before_delete", _immutable_record_before_delete)
for _model in (ValuationRun, AnalysisRun, OverrideRecord, AuditEvent):
    event.listen(_model, "before_update", _immutable_record_before_update)
    event.listen(_model, "before_delete", _immutable_record_before_delete)


def _approved_version_before_update(mapper, connection, target: ProjectVersion) -> None:  # noqa: ANN001
    del mapper, connection
    history = inspect(target).attrs.status.history
    previous = history.deleted[0] if history.deleted else target.status
    if previous in {"APPROVED", "ARCHIVED"}:
        raise ValueError("Approved or archived project versions are immutable; clone a new draft.")


def _published_policy_before_update(mapper, connection, target: PolicyPackVersion) -> None:  # noqa: ANN001
    del mapper, connection
    history = inspect(target).attrs.status.history
    previous = history.deleted[0] if history.deleted else target.status
    if previous in {"PUBLISHED", "RETIRED"}:
        raise ValueError("Published or retired policy versions are immutable; clone a new draft.")


event.listen(ProjectVersion, "before_update", _approved_version_before_update)
event.listen(PolicyPackVersion, "before_update", _published_policy_before_update)



def _approved_government_case_before_update(mapper, connection, target: GovernmentCase) -> None:  # noqa: ANN001
    del mapper, connection
    history = inspect(target).attrs.status.history
    previous = history.deleted[0] if history.deleted else target.status
    if previous in {"READY", "APPROVED"}:
        raise ValueError("Completed government cases are immutable; create a new case for a revised analysis.")


event.listen(GovernmentCase, "before_update", _approved_government_case_before_update)
event.listen(GovernmentCase, "before_delete", _immutable_record_before_delete)
