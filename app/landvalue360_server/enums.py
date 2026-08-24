"""Stable string enums used by the persistence and API contracts."""

from __future__ import annotations

from enum import StrEnum


class RecordStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class MembershipRole(StrEnum):
    ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN"
    POLICY_ADMINISTRATOR = "POLICY_ADMINISTRATOR"
    PROJECT_MANAGER = "PROJECT_MANAGER"
    ANALYST = "ANALYST"
    REVIEWER = "REVIEWER"
    AUDITOR = "AUDITOR"
    VIEWER = "VIEWER"
    DATA_ENTRY = "DATA_ENTRY"
    VALUER = "VALUER"
    TECHNICAL_REVIEWER = "TECHNICAL_REVIEWER"
    LEGAL_REVIEWER = "LEGAL_REVIEWER"
    APPROVER = "APPROVER"
    READ_ONLY = "READ_ONLY"




class EditionScope(StrEnum):
    DEVELOPER = "DEVELOPER"
    GOVERNMENT = "GOVERNMENT"
    ADMINISTRATION = "ADMINISTRATION"
    COMBINED = "COMBINED"


class ProductAccess(StrEnum):
    DEVELOPER = "DEVELOPER"
    GOVERNMENT = "GOVERNMENT"
    BOTH = "BOTH"


class ProjectKind(StrEnum):
    DEVELOPER = "DEVELOPER"
    GOVERNMENT = "GOVERNMENT"
    SHARED = "SHARED"


class ProjectVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class ScenarioStatus(StrEnum):
    DRAFT = "DRAFT"
    LOCKED = "LOCKED"
    ARCHIVED = "ARCHIVED"


class PolicyVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class CalculationMode(StrEnum):
    PREVIEW = "PREVIEW"
    OFFICIAL = "OFFICIAL"


class GovernmentCaseStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    SUBMITTED = "SUBMITTED"
    TECHNICALLY_REVIEWED = "TECHNICALLY_REVIEWED"
    LEGALLY_REVIEWED = "LEGALLY_REVIEWED"
    APPROVED = "APPROVED"


class CalculationRunStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
    FAILED = "FAILED"


class Permission(StrEnum):
    ORGANIZATION_MANAGE = "organization:manage"
    WORKSPACE_MANAGE = "workspace:manage"
    USER_MANAGE = "user:manage"
    MEMBERSHIP_MANAGE = "membership:manage"
    PORTFOLIO_READ = "portfolio:read"
    PORTFOLIO_WRITE = "portfolio:write"
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_APPROVE = "project:approve"
    POLICY_READ = "policy:read"
    POLICY_WRITE = "policy:write"
    POLICY_PUBLISH = "policy:publish"
    CALCULATION_READ = "calculation:read"
    CALCULATION_RUN = "calculation:run"
    AUDIT_READ = "audit:read"
    EVIDENCE_READ = "evidence:read"
    EVIDENCE_WRITE = "evidence:write"
    EVIDENCE_VERIFY = "evidence:verify"
    ASSUMPTION_READ = "assumption:read"
    ASSUMPTION_WRITE = "assumption:write"
    ASSUMPTION_REVIEW = "assumption:review"
    VALUATION_READ = "valuation:read"
    VALUATION_RUN = "valuation:run"
    RISK_READ = "risk:read"
    RISK_RUN = "risk:run"
    SENSITIVITY_READ = "sensitivity:read"
    SENSITIVITY_RUN = "sensitivity:run"
    TENDER_READ = "tender:read"
    TENDER_RUN = "tender:run"
    GOVERNMENT_CASE_READ = "government:case:read"
    GOVERNMENT_CASE_WRITE = "government:case:write"
    GOVERNMENT_CASE_SUBMIT = "government:case:submit"
    GOVERNMENT_TECHNICAL_REVIEW = "government:technical-review"
    GOVERNMENT_LEGAL_REVIEW = "government:legal-review"
    GOVERNMENT_APPROVE = "government:approve"
    GOVERNMENT_RUN = "government:run"
    GOVERNMENT_OVERRIDE = "government:override"
