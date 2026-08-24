from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta, timezone
from typing import Iterable

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import (
    AccessSession, MemberRole, OrganizationMember, Permission, RolePermission, Role, User, utcnow
)

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
SESSION_COOKIE = "lv360_portal_session"


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(48)


def create_session(db: Session, user: User, *, request: Request) -> tuple[str, AccessSession]:
    settings = get_settings()
    raw = new_token()
    row = AccessSession(
        user_id=user.id,
        token_hash=token_hash(raw),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=settings.session_hours),
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500],
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    return raw, row


def revoke_session(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    row = db.scalar(select(AccessSession).where(AccessSession.token_hash == token_hash(raw_token)))
    if row:
        row.revoked_at = utcnow()
        db.flush()


def _session_for_request(request: Request, db: Session) -> AccessSession | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    row = db.scalar(select(AccessSession).where(AccessSession.token_hash == token_hash(raw)))
    if not row or row.revoked_at:
        return None
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires <= utcnow():
        return None
    return row


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    session = _session_for_request(request, db)
    if not session:
        return None
    return db.get(User, session.user_id)



def apply_rls_context(db: Session, user: User) -> None:
    if not db.bind or db.bind.dialect.name != "postgresql":
        return
    org_ids = ",".join(m.organization_id for m in user_memberships(db, user.id))
    permissions = user_permission_codes(db, user.id)
    staff = bool(permissions.intersection({"ops.view_assigned", "ops.view_all", "admin.projects"}))
    can_view_all = bool(permissions.intersection({"ops.view_all", "admin.projects"}))
    db.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": user.id})
    db.execute(text("SELECT set_config('app.organization_ids', :v, true)"), {"v": org_ids})
    db.execute(text("SELECT set_config('app.is_staff', :v, true)"), {"v": "true" if staff else "false"})
    db.execute(text("SELECT set_config('app.can_view_all_projects', :v, true)"), {"v": "true" if can_view_all else "false"})

def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user_optional(request, db)
    if not user or not user.active or user.suspended or user.deleted_at:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    # A temporary administrator-issued password may authenticate only long enough
    # to let the user replace it. This is enforced on the server, not just by UI.
    if user.must_change_password and request.url.path not in {
        "/change-password", "/api/auth/me", "/api/auth/change-password",
        "/api/auth/logout", "/api/auth/sessions",
    }:
        raise HTTPException(status_code=428, detail="Password change required")
    apply_rls_context(db, user)
    return user


def current_session(request: Request, db: Session = Depends(get_db)) -> AccessSession:
    row = _session_for_request(request, db)
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return row


def csrf_protect(request: Request, session: AccessSession = Depends(current_session)) -> None:
    supplied = request.headers.get("x-csrf-token")
    if not supplied:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing CSRF token")
    if not secrets.compare_digest(supplied, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def user_memberships(db: Session, user_id: str) -> list[OrganizationMember]:
    return list(db.scalars(select(OrganizationMember).where(
        OrganizationMember.user_id == user_id,
        OrganizationMember.status == "ACTIVE",
        OrganizationMember.deleted_at.is_(None),
    )).all())


def user_role_codes(db: Session, user_id: str) -> set[str]:
    membership_ids = [m.id for m in user_memberships(db, user_id)]
    if not membership_ids:
        return set()
    rows = db.execute(
        select(MemberRole, Role)
        .join(Role, Role.id == MemberRole.role_id)
        .where(MemberRole.membership_id.in_(membership_ids))
    ).all()
    return {role.code for _, role in rows}


def user_permission_codes(db: Session, user_id: str) -> set[str]:
    membership_ids = [m.id for m in user_memberships(db, user_id)]
    if not membership_ids:
        return set()
    role_ids = list(db.scalars(select(MemberRole.role_id).where(MemberRole.membership_id.in_(membership_ids))).all())
    if not role_ids:
        return set()
    return set(db.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id.in_(role_ids))
    ).all())


def require_permissions(*required: str):
    def dep(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        actual = user_permission_codes(db, user.id)
        missing = set(required) - actual
        if missing:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permissions: {', '.join(sorted(missing))}")
        return user
    return dep


def has_any_role(db: Session, user_id: str, roles: Iterable[str]) -> bool:
    return bool(user_role_codes(db, user_id).intersection(set(roles)))


def assert_form_csrf(form_value: str | None, session: AccessSession) -> None:
    if not form_value or not secrets.compare_digest(form_value, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
