"""Organization, workspace, user, and membership administration."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..config import Settings
from ..context import AuthContext
from ..enums import MembershipRole, ProductAccess
from ..errors import AuthorizationError, ConflictError, NotFoundError
from ..models import Membership, Organization, User, Workspace
from ..security import normalize_email
from .auth import create_membership, create_user
from .tenant import assert_organization_access, get_workspace, require_tenant_context


def create_organization(
    session: Session,
    *,
    context: AuthContext,
    name: str,
    slug: str,
    default_currency: str,
) -> Organization:
    if not context.is_platform_admin:
        raise AuthorizationError("Only a platform administrator can create organizations.")
    existing = session.scalar(select(Organization).where(Organization.slug == slug))
    if existing is not None:
        raise ConflictError("ORGANIZATION_SLUG_EXISTS", "Organization slug already exists.")
    organization = Organization(name=name.strip(), slug=slug, default_currency=default_currency)
    session.add(organization)
    session.flush()
    record_audit(
        session,
        context=context,
        organization_id=organization.id,
        action="ORGANIZATION_CREATED",
        entity_type="Organization",
        entity_id=organization.id,
        after={"name": organization.name, "slug": organization.slug},
    )
    return organization


def create_workspace(
    session: Session,
    *,
    context: AuthContext,
    organization_id: str,
    name: str,
    slug: str,
) -> Workspace:
    assert_organization_access(context, organization_id)
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise NotFoundError("Organization not found.")
    existing = session.scalar(
        select(Workspace).where(
            Workspace.organization_id == organization_id,
            Workspace.slug == slug,
        )
    )
    if existing is not None:
        raise ConflictError("WORKSPACE_SLUG_EXISTS", "Workspace slug already exists in this organization.")
    workspace = Workspace(organization_id=organization_id, name=name.strip(), slug=slug)
    session.add(workspace)
    session.flush()
    record_audit(
        session,
        context=context,
        organization_id=organization_id,
        workspace_id=workspace.id,
        action="WORKSPACE_CREATED",
        entity_type="Workspace",
        entity_id=workspace.id,
        after={"name": workspace.name, "slug": workspace.slug},
    )
    return workspace


def create_tenant_user(
    session: Session,
    *,
    settings: Settings,
    context: AuthContext,
    email: str,
    full_name: str,
    password: str,
    is_platform_admin: bool,
) -> User:
    if is_platform_admin and not context.is_platform_admin:
        raise AuthorizationError("Only a platform administrator can create another platform administrator.")
    user = create_user(
        session,
        settings=settings,
        email=email,
        full_name=full_name,
        password=password,
        is_platform_admin=is_platform_admin,
    )
    record_audit(
        session,
        context=context,
        action="USER_CREATED",
        entity_type="User",
        entity_id=user.id,
        after={"email": user.email, "full_name": user.full_name, "is_platform_admin": user.is_platform_admin},
    )
    return user


def add_membership(
    session: Session,
    *,
    context: AuthContext,
    organization_id: str,
    user_id: str,
    workspace_id: str | None,
    role: MembershipRole,
    product_access: ProductAccess = ProductAccess.BOTH,
) -> Membership:
    assert_organization_access(context, organization_id)
    organization = session.get(Organization, organization_id)
    user = session.get(User, user_id)
    if organization is None or user is None:
        raise NotFoundError("Organization or user not found.")
    workspace = get_workspace(session, context, workspace_id) if workspace_id else None
    membership = create_membership(
        session,
        user=user,
        organization=organization,
        workspace=workspace,
        role=role,
        product_access=product_access,
    )
    record_audit(
        session,
        context=context,
        action="MEMBERSHIP_CREATED",
        entity_type="Membership",
        entity_id=membership.id,
        after={
            "user_id": user.id,
            "organization_id": organization.id,
            "workspace_id": workspace.id if workspace else None,
            "role": role.value,
            "product_access": product_access.value,
        },
    )
    return membership
