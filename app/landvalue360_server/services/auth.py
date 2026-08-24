"""Authentication, bootstrap, and membership-context services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..config import Settings
from ..context import AuthContext
from ..enums import EditionScope, MembershipRole, ProductAccess
from ..errors import AuthenticationError, ConflictError, NotFoundError
from ..models import AccessSession, Membership, Organization, User, Workspace, utc_now
from ..security import (
    create_bearer_token,
    hash_bearer_token,
    hash_password,
    normalize_email,
    verify_password,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def create_user(
    session: Session,
    *,
    settings: Settings,
    email: str,
    full_name: str,
    password: str,
    is_platform_admin: bool = False,
) -> User:
    normalized_email = normalize_email(email)
    existing = session.scalar(select(User).where(User.email == normalized_email))
    if existing is not None:
        raise ConflictError("USER_EMAIL_EXISTS", "A user with this email address already exists.")
    user = User(
        email=normalized_email,
        full_name=full_name.strip(),
        password_hash=hash_password(password, iterations=settings.password_iterations),
        is_platform_admin=is_platform_admin,
    )
    session.add(user)
    session.flush()
    return user


def create_membership(
    session: Session,
    *,
    user: User,
    organization: Organization,
    workspace: Workspace | None,
    role: MembershipRole,
    product_access: ProductAccess = ProductAccess.BOTH,
) -> Membership:
    if workspace is not None and workspace.organization_id != organization.id:
        raise NotFoundError("Workspace does not belong to the organization.")
    scope_key = workspace.id if workspace else organization.id
    existing = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == organization.id,
            Membership.scope_key == scope_key,
        )
    )
    if existing is not None:
        raise ConflictError("MEMBERSHIP_EXISTS", "The user already has a membership in this scope.")
    membership = Membership(
        user_id=user.id,
        organization_id=organization.id,
        workspace_id=workspace.id if workspace else None,
        scope_key=scope_key,
        role=role.value,
        product_access=product_access.value,
        is_active=True,
    )
    session.add(membership)
    session.flush()
    return membership


def bootstrap_development(
    session: Session,
    *,
    settings: Settings,
    email: str,
    password: str,
) -> tuple[Organization, Workspace, User, Membership]:
    organization = session.scalar(
        select(Organization).where(Organization.slug == settings.bootstrap_organization_slug)
    )
    if organization is None:
        organization = Organization(
            name=settings.bootstrap_organization,
            slug=settings.bootstrap_organization_slug,
        )
        session.add(organization)
        session.flush()

    workspace = session.scalar(
        select(Workspace).where(
            Workspace.organization_id == organization.id,
            Workspace.slug == settings.bootstrap_workspace_slug,
        )
    )
    if workspace is None:
        workspace = Workspace(
            organization_id=organization.id,
            name=settings.bootstrap_workspace,
            slug=settings.bootstrap_workspace_slug,
        )
        session.add(workspace)
        session.flush()

    normalized_email = normalize_email(email)
    user = session.scalar(select(User).where(User.email == normalized_email))
    if user is None:
        user = create_user(
            session,
            settings=settings,
            email=normalized_email,
            full_name="LandValue360 Platform Administrator",
            password=password,
            is_platform_admin=True,
        )

    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == organization.id,
            Membership.scope_key == organization.id,
        )
    )
    if membership is None:
        membership = create_membership(
            session,
            user=user,
            organization=organization,
            workspace=None,
            role=MembershipRole.ORGANIZATION_ADMIN,
        )

    record_audit(
        session,
        action="SYSTEM_BOOTSTRAPPED",
        entity_type="Organization",
        entity_id=organization.id,
        organization_id=organization.id,
        workspace_id=workspace.id,
        after={
            "organization_slug": organization.slug,
            "workspace_slug": workspace.slug,
            "admin_email": user.email,
        },
    )
    return organization, workspace, user, membership


def authenticate(
    session: Session,
    *,
    settings: Settings,
    email: str,
    password: str,
    organization_slug: str | None,
    workspace_slug: str | None,
    edition_scope: str = "COMBINED",
) -> tuple[str, AccessSession, User, Organization | None, Workspace | None]:
    normalized_email = normalize_email(email)
    normalized_edition = str(edition_scope or "COMBINED").strip().upper()
    try:
        EditionScope(normalized_edition)
    except ValueError as exc:
        raise AuthenticationError("The requested LandValue360 edition is invalid.") from exc
    user = session.scalar(select(User).where(User.email == normalized_email))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AuthenticationError("Invalid email or password.")

    organization: Organization | None = None
    workspace: Workspace | None = None
    membership: Membership | None = None

    if organization_slug:
        organization = session.scalar(
            select(Organization).where(
                Organization.slug == organization_slug.strip().lower(),
                Organization.status == "ACTIVE",
            )
        )
        if organization is None:
            raise AuthenticationError("The organization context is invalid.")
        if workspace_slug:
            workspace = session.scalar(
                select(Workspace).where(
                    Workspace.organization_id == organization.id,
                    Workspace.slug == workspace_slug.strip().lower(),
                    Workspace.status == "ACTIVE",
                )
            )
            if workspace is None:
                raise AuthenticationError("The workspace context is invalid.")

        if not user.is_platform_admin:
            scope_keys = [organization.id]
            if workspace is not None:
                scope_keys.insert(0, workspace.id)
            memberships = session.scalars(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.organization_id == organization.id,
                    Membership.scope_key.in_(scope_keys),
                    Membership.is_active.is_(True),
                )
            ).all()
            by_scope = {item.scope_key: item for item in memberships}
            if workspace is not None and workspace.id in by_scope:
                membership = by_scope[workspace.id]
            else:
                membership = by_scope.get(organization.id)
            if membership is None:
                raise AuthenticationError("The user has no active membership in this context.")
    elif not user.is_platform_admin:
        raise AuthenticationError("Organization slug is required for non-platform users.")

    if not user.is_platform_admin and membership is not None:
        product_access = str(getattr(membership, "product_access", ProductAccess.BOTH.value) or ProductAccess.BOTH.value)
        if normalized_edition == EditionScope.DEVELOPER.value and product_access not in {ProductAccess.DEVELOPER.value, ProductAccess.BOTH.value}:
            raise AuthenticationError("This account is not enabled for LandValue360 Developer.")
        if normalized_edition == EditionScope.GOVERNMENT.value and product_access not in {ProductAccess.GOVERNMENT.value, ProductAccess.BOTH.value}:
            raise AuthenticationError("This account is not enabled for the LandValue360 Landowner interface.")
        if normalized_edition == EditionScope.COMBINED.value and product_access != ProductAccess.BOTH.value:
            raise AuthenticationError("Select the LandValue360 edition enabled for this account.")
        if normalized_edition == EditionScope.ADMINISTRATION.value and membership.role not in {MembershipRole.ORGANIZATION_ADMIN.value, MembershipRole.POLICY_ADMINISTRATOR.value}:
            raise AuthenticationError("This account is not enabled for Platform Administration.")

    role = "PLATFORM_SUPER_ADMIN" if user.is_platform_admin else str(membership.role)
    token = create_bearer_token()
    now = utc_now()
    access = AccessSession(
        user_id=user.id,
        organization_id=organization.id if organization else None,
        workspace_id=workspace.id if workspace else None,
        membership_id=membership.id if membership else None,
        role=role,
        edition_scope=normalized_edition,
        token_hash=hash_bearer_token(token),
        created_at=now,
        expires_at=now + timedelta(minutes=settings.token_ttl_minutes),
        last_used_at=now,
    )
    session.add(access)
    session.flush()

    record_audit(
        session,
        action="AUTH_LOGIN_SUCCEEDED",
        entity_type="AccessSession",
        entity_id=access.id,
        organization_id=access.organization_id,
        workspace_id=access.workspace_id,
        after={"user_id": user.id, "role": role, "edition_scope": normalized_edition},
    )
    return token, access, user, organization, workspace


def load_auth_context(
    session: Session,
    *,
    raw_token: str,
    request_id: str | None,
    required_edition: str | None = None,
) -> AuthContext:
    token_hash = hash_bearer_token(raw_token)
    access = session.scalar(
        select(AccessSession).where(AccessSession.token_hash == token_hash)
    )
    now = utc_now()
    if (
        access is None
        or access.revoked_at is not None
        or _aware(access.expires_at) <= now
    ):
        raise AuthenticationError("The access token is invalid or expired.")
    token_edition = str(getattr(access, "edition_scope", "COMBINED") or "COMBINED").upper()
    required = str(required_edition or "").upper()
    if required and token_edition not in {required, EditionScope.COMBINED.value}:
        raise AuthenticationError("This session belongs to a different LandValue360 edition.")
    user = session.get(User, access.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("The user account is inactive.")

    if not user.is_platform_admin:
        if access.membership_id is None:
            raise AuthenticationError("The access context has no membership.")
        membership = session.get(Membership, access.membership_id)
        if (
            membership is None
            or not membership.is_active
            or membership.organization_id != access.organization_id
            or (
                membership.workspace_id is not None
                and membership.workspace_id != access.workspace_id
            )
        ):
            raise AuthenticationError("The membership associated with this token is no longer valid.")
        product_access = str(getattr(membership, "product_access", ProductAccess.BOTH.value) or ProductAccess.BOTH.value)
        effective_edition = required or token_edition
        if effective_edition == EditionScope.DEVELOPER.value and product_access not in {ProductAccess.DEVELOPER.value, ProductAccess.BOTH.value}:
            raise AuthenticationError("Developer access has been revoked for this membership.")
        if effective_edition == EditionScope.GOVERNMENT.value and product_access not in {ProductAccess.GOVERNMENT.value, ProductAccess.BOTH.value}:
            raise AuthenticationError("Government access has been revoked for this membership.")
        if effective_edition == EditionScope.ADMINISTRATION.value and membership.role not in {MembershipRole.ORGANIZATION_ADMIN.value, MembershipRole.POLICY_ADMINISTRATOR.value}:
            raise AuthenticationError("Administration access has been revoked for this membership.")
        role = membership.role
    else:
        role = "PLATFORM_SUPER_ADMIN"

    # Authentication remains read-only inside request transactions. Updating
    # last_used_at on every request creates an unnecessary SQLite write lock
    # before long calculations; login/re-authentication records activity.
    return AuthContext(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        organization_id=access.organization_id,
        workspace_id=access.workspace_id,
        membership_id=access.membership_id,
        role=role,
        is_platform_admin=user.is_platform_admin,
        session_id=access.id,
        edition_scope=token_edition,
        request_id=request_id,
    )


def change_password(
    session: Session,
    *,
    settings: Settings,
    context: AuthContext,
    current_password: str,
    new_password: str,
) -> None:
    user = session.get(User, context.user_id)
    if user is None or not verify_password(current_password, user.password_hash):
        raise AuthenticationError("The current password is incorrect.")
    user.password_hash = hash_password(new_password, iterations=settings.password_iterations)
    user.password_changed_at = utc_now()
    other_sessions = session.scalars(
        select(AccessSession).where(
            AccessSession.user_id == user.id,
            AccessSession.id != context.session_id,
            AccessSession.revoked_at.is_(None),
        )
    ).all()
    for access in other_sessions:
        access.revoked_at = utc_now()
    record_audit(
        session,
        context=context,
        action="AUTH_PASSWORD_CHANGED",
        entity_type="User",
        entity_id=user.id,
        metadata={"other_sessions_revoked": len(other_sessions)},
    )


def revoke_session(session: Session, *, context: AuthContext) -> None:
    access = session.get(AccessSession, context.session_id)
    if access is None:
        return
    access.revoked_at = utc_now()
    record_audit(
        session,
        action="AUTH_LOGOUT",
        entity_type="AccessSession",
        entity_id=access.id,
        context=context,
    )
