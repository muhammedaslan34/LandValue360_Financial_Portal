"""Application exceptions exposed through RFC 9457-style problem details."""

from __future__ import annotations


class AppError(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int = 400,
        title: str = "Request could not be completed",
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.title = title


class NotFoundError(AppError):
    def __init__(self, detail: str = "The requested record was not found.") -> None:
        super().__init__("NOT_FOUND", detail, status_code=404, title="Not found")


class ConflictError(AppError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, status_code=409, title="Conflict")


class AuthorizationError(AppError):
    def __init__(self, detail: str = "You do not have permission to perform this action.") -> None:
        super().__init__("FORBIDDEN", detail, status_code=403, title="Forbidden")


class AuthenticationError(AppError):
    def __init__(self, detail: str = "Authentication is required or invalid.") -> None:
        super().__init__("UNAUTHENTICATED", detail, status_code=401, title="Unauthenticated")
