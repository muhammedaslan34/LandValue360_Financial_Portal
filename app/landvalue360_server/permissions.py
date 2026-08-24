"""Role-to-permission policy for LandValue360 Platform 1.0.0."""

from __future__ import annotations

from .enums import MembershipRole, Permission


ALL_PERMISSIONS = frozenset(Permission)

READ_BASE = frozenset(
    {
        Permission.PORTFOLIO_READ,
        Permission.PROJECT_READ,
        Permission.POLICY_READ,
        Permission.CALCULATION_READ,
        Permission.EVIDENCE_READ,
        Permission.ASSUMPTION_READ,
        Permission.VALUATION_READ,
        Permission.RISK_READ,
        Permission.SENSITIVITY_READ,
        Permission.TENDER_READ,
        Permission.GOVERNMENT_CASE_READ,
    }
)

ANALYSIS_RUN = frozenset(
    {
        Permission.CALCULATION_RUN,
        Permission.VALUATION_RUN,
        Permission.RISK_RUN,
        Permission.SENSITIVITY_RUN,
        Permission.TENDER_RUN,
        Permission.GOVERNMENT_RUN,
    }
)

GOVERNMENT_MAKER = frozenset(
    {
        Permission.GOVERNMENT_CASE_READ,
        Permission.GOVERNMENT_CASE_WRITE,
        Permission.GOVERNMENT_CASE_SUBMIT,
        Permission.GOVERNMENT_RUN,
        Permission.GOVERNMENT_OVERRIDE,
    }
)

GOVERNMENT_END_TO_END = frozenset(
    {
        Permission.PROJECT_APPROVE,
        Permission.GOVERNMENT_CASE_READ,
        Permission.GOVERNMENT_CASE_WRITE,
        Permission.GOVERNMENT_CASE_SUBMIT,
        Permission.GOVERNMENT_TECHNICAL_REVIEW,
        Permission.GOVERNMENT_LEGAL_REVIEW,
        Permission.GOVERNMENT_APPROVE,
        Permission.GOVERNMENT_RUN,
        Permission.GOVERNMENT_OVERRIDE,
    }
)

ROLE_PERMISSIONS: dict[MembershipRole, frozenset[Permission]] = {
    MembershipRole.ORGANIZATION_ADMIN: ALL_PERMISSIONS - frozenset({Permission.POLICY_PUBLISH}),
    MembershipRole.POLICY_ADMINISTRATOR: READ_BASE
    | ANALYSIS_RUN
    | frozenset(
        {
            Permission.POLICY_WRITE,
            Permission.POLICY_PUBLISH,
            Permission.AUDIT_READ,
            Permission.EVIDENCE_VERIFY,
            Permission.ASSUMPTION_REVIEW,
            Permission.GOVERNMENT_CASE_WRITE,
            Permission.GOVERNMENT_CASE_SUBMIT,
            Permission.GOVERNMENT_OVERRIDE,
        }
    ),
    MembershipRole.PROJECT_MANAGER: READ_BASE
    | ANALYSIS_RUN
    | GOVERNMENT_MAKER
    | GOVERNMENT_END_TO_END
    | frozenset(
        {
            Permission.PORTFOLIO_WRITE,
            Permission.PROJECT_WRITE,
            Permission.PROJECT_APPROVE,
            Permission.EVIDENCE_WRITE,
            Permission.ASSUMPTION_WRITE,
        }
    ),
    MembershipRole.ANALYST: READ_BASE
    | ANALYSIS_RUN
    | GOVERNMENT_MAKER
    | GOVERNMENT_END_TO_END
    | frozenset(
        {
            Permission.PROJECT_WRITE,
            Permission.EVIDENCE_WRITE,
            Permission.ASSUMPTION_WRITE,
        }
    ),
    MembershipRole.REVIEWER: READ_BASE
    | frozenset(
        {
            Permission.PROJECT_APPROVE,
            Permission.AUDIT_READ,
            Permission.EVIDENCE_VERIFY,
            Permission.ASSUMPTION_REVIEW,
            Permission.GOVERNMENT_TECHNICAL_REVIEW,
        }
    ),
    MembershipRole.AUDITOR: READ_BASE | frozenset({Permission.AUDIT_READ}),
    MembershipRole.VIEWER: READ_BASE,
    MembershipRole.DATA_ENTRY: READ_BASE
    | GOVERNMENT_MAKER
    | frozenset({Permission.PROJECT_WRITE, Permission.EVIDENCE_WRITE, Permission.ASSUMPTION_WRITE}),
    MembershipRole.VALUER: READ_BASE
    | ANALYSIS_RUN
    | GOVERNMENT_MAKER
    | GOVERNMENT_END_TO_END
    | frozenset({Permission.EVIDENCE_WRITE, Permission.EVIDENCE_VERIFY, Permission.ASSUMPTION_REVIEW}),
    MembershipRole.TECHNICAL_REVIEWER: READ_BASE
    | frozenset({Permission.AUDIT_READ, Permission.GOVERNMENT_TECHNICAL_REVIEW}),
    MembershipRole.LEGAL_REVIEWER: READ_BASE
    | frozenset({Permission.AUDIT_READ, Permission.GOVERNMENT_LEGAL_REVIEW}),
    MembershipRole.APPROVER: READ_BASE
    | frozenset({Permission.AUDIT_READ, Permission.GOVERNMENT_APPROVE, Permission.GOVERNMENT_RUN}),
    MembershipRole.READ_ONLY: READ_BASE,
}


def role_permissions(role: str, *, platform_admin: bool = False) -> frozenset[Permission]:
    if platform_admin:
        return ALL_PERMISSIONS
    try:
        return ROLE_PERMISSIONS.get(MembershipRole(role), frozenset())
    except ValueError:
        return frozenset()
