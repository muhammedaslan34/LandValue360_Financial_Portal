from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...schemas import LoginRequest, PasswordChange, TokenResponse
from ...errors import AppError, AuthenticationError
from ...services.auth import authenticate, change_password, revoke_session
from ..dependencies import get_current_context, get_session

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: Session = Depends(get_session, scope="function"),
) -> TokenResponse:
    settings = request.app.state.settings
    configured = settings.edition_scope
    requested = (payload.edition or (configured if configured != "COMBINED" else "COMBINED")).upper()
    if configured != "COMBINED" and requested != configured:
        raise AppError(
            "EDITION_MISMATCH",
            f"This service is configured for LandValue360 {configured.title()} only.",
            status_code=403,
            title="Wrong product edition",
        )
    client_host = request.client.host if request.client else "unknown"
    limiter_key = f"{client_host}|{payload.email.strip().casefold()}|{requested}"
    limiter = request.app.state.login_limiter
    try:
        limiter.assert_allowed(limiter_key)
    except PermissionError as exc:
        raise AppError(
            "LOGIN_RATE_LIMITED",
            str(exc),
            status_code=429,
            title="Too many login attempts",
        ) from exc
    try:
        token, access, user, organization, workspace = authenticate(
            session,
            settings=settings,
            email=payload.email,
            password=payload.password,
            organization_slug=payload.organization_slug,
            workspace_slug=payload.workspace_slug,
            edition_scope=requested,
        )
    except AuthenticationError:
        limiter.record_failure(limiter_key)
        raise
    limiter.record_success(limiter_key)
    return TokenResponse(
        access_token=token,
        expires_at=access.expires_at,
        user={
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "is_platform_admin": user.is_platform_admin,
        },
        context={
            "organization_id": organization.id if organization else None,
            "organization_slug": organization.slug if organization else None,
            "workspace_id": workspace.id if workspace else None,
            "workspace_slug": workspace.slug if workspace else None,
            "role": access.role,
            "edition_scope": access.edition_scope,
        },
    )


@router.get("/me")
def me(context: AuthContext = Depends(get_current_context)) -> dict:
    return {
        "user_id": context.user_id,
        "email": context.email,
        "full_name": context.full_name,
        "organization_id": context.organization_id,
        "workspace_id": context.workspace_id,
        "role": context.role,
        "is_platform_admin": context.is_platform_admin,
        "edition_scope": context.edition_scope,
        "permissions": sorted(permission.value for permission in context.permissions),
    }


@router.post("/logout", status_code=204)
def logout(
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    revoke_session(session, context=context)


@router.post("/change-password", status_code=204)
def post_change_password(
    payload: PasswordChange,
    request: Request,
    context: AuthContext = Depends(get_current_context),
    session: Session = Depends(get_session, scope="function"),
) -> None:
    change_password(
        session,
        settings=request.app.state.settings,
        context=context,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
