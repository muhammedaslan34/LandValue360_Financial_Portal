"""FastAPI dependencies for database sessions, authentication, and authorization."""

from __future__ import annotations

from collections.abc import Callable, Generator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..context import AuthContext
from ..database import apply_postgres_tenant_context
from ..enums import Permission
from ..errors import AuthenticationError, AuthorizationError
from ..services.auth import load_auth_context


_bearer = HTTPBearer(auto_error=False)


_DEVELOPER_API_PREFIXES = (
    "/api/v1/ui", "/api/v1/projects", "/api/v1/project-versions",
    "/api/v1/portfolios", "/api/v1/scenarios", "/api/v1/calculation-runs",
    "/api/v1/analysis", "/api/v1/evidence", "/api/v1/assumptions",
    "/api/v1/valuation", "/api/v1/risk", "/api/v1/sensitivity",
    "/api/v1/monte-carlo", "/api/v1/tender", "/api/v1/landowner",
)
_ADMIN_API_PREFIXES = ("/api/v1/organizations", "/api/v1/users", "/api/v1/memberships")


def _required_edition(request: Request) -> str | None:
    configured = request.app.state.settings.edition_scope
    if configured != "COMBINED":
        return configured
    path = request.url.path
    if path.startswith("/api/v1/policy"):
        # Published-policy reads are shared by the two operational editions.
        # Every mutation belongs exclusively to the independent administration
        # session and is also protected by server-side policy permissions.
        return None if request.method.upper() == "GET" else "ADMINISTRATION"
    if path.startswith("/api/v1/government"):
        return "GOVERNMENT"
    if path.startswith(_ADMIN_API_PREFIXES):
        return "ADMINISTRATION"
    if path.startswith(_DEVELOPER_API_PREFIXES):
        return "DEVELOPER"
    return None


def get_session(request: Request) -> Generator[Session, None, None]:
    session = request.app.state.database.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session, scope="function"),
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError()
    context = load_auth_context(
        session,
        raw_token=credentials.credentials,
        request_id=getattr(request.state, "request_id", None),
        required_edition=_required_edition(request),
    )
    apply_postgres_tenant_context(session, context.organization_id)
    request.state.auth_context = context
    return context


def require_permission(permission: Permission) -> Callable[[AuthContext], AuthContext]:
    def dependency(context: AuthContext = Depends(get_current_context)) -> AuthContext:
        if not context.has(permission):
            raise AuthorizationError()
        return context

    return dependency
