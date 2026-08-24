"""Request correlation, security headers, and credential-leak prevention."""

from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


_SENSITIVE_QUERY_KEYS = {
    "password",
    "pass",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "secret",
}


class CredentialQueryGuardMiddleware(BaseHTTPMiddleware):
    """Reject credentials in URLs before routing.

    URLs are copied into history, bookmarks, reverse-proxy logs and referrer
    metadata. Authentication is accepted only in a POST body or Authorization
    header.
    """

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        keys = {key.casefold() for key in request.query_params.keys()}
        if keys & _SENSITIVE_QUERY_KEYS:
            return JSONResponse(
                status_code=400,
                content={
                    "type": "https://landvalue360.example/problems/credentials-in-url",
                    "title": "Credentials are not accepted in URLs",
                    "status": 400,
                    "detail": "Remove passwords and tokens from the address bar and use the login form.",
                    "code": "CREDENTIALS_IN_URL_REJECTED",
                    "instance": request.url.path,
                },
                headers={"Cache-Control": "no-store"},
            )
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, behind_https_proxy: bool = False) -> None:  # noqa: ANN001
        super().__init__(app)
        self.behind_https_proxy = behind_https_proxy

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        is_official_report = (
            request.url.path.startswith("/api/v1/government/cases/")
            and "/reports/" in request.url.path
            and request.url.path.endswith(".html")
        )
        if is_official_report:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; "
                "style-src 'unsafe-inline'; "
                "img-src data:; "
                "font-src 'self'; "
                "object-src 'none'; "
                "base-uri 'none'; "
                "frame-ancestors 'none'; "
                "form-action 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "frame-ancestors 'none'; "
                "form-action 'self'"
            )
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if request.url.scheme == "https" or (self.behind_https_proxy and forwarded_proto == "https"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/v1/auth"):
            response.headers["Cache-Control"] = "no-store"
        return response

class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before application code reads them.

    Content-Length is checked immediately. Chunked bodies are counted while
    streaming and terminated with HTTP 413 once the route-specific limit is
    exceeded. This protects binary import and evidence endpoints from memory
    exhaustion even though their current FastAPI contracts expose ``bytes``.
    """

    def __init__(self, app, *, default_limit: int, route_limits: dict[str, int] | None = None) -> None:  # noqa: ANN001
        self.app = app
        self.default_limit = int(default_limit)
        self.route_limits = dict(route_limits or {})

    def _limit(self, path: str) -> int:
        matches = [(prefix, limit) for prefix, limit in self.route_limits.items() if path.startswith(prefix)]
        if not matches:
            return self.default_limit
        return max(matches, key=lambda item: len(item[0]))[1]

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if method not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        limit = self._limit(path)
        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > limit:
                    await self._reject(scope, send, limit)
                    return
            except ValueError:
                await self._reject(scope, send, limit, code="INVALID_CONTENT_LENGTH")
                return
        consumed = 0
        rejected = False

        async def limited_receive():
            nonlocal consumed, rejected
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body") or b"")
                if consumed > limit:
                    rejected = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not rejected:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)
        if rejected:
            await self._reject(scope, send, limit)

    @staticmethod
    async def _reject(scope, send, limit: int, code: str = "REQUEST_BODY_TOO_LARGE") -> None:  # noqa: ANN001
        import json
        path = str(scope.get("path") or "")
        body = json.dumps({
            "type": f"https://landvalue360.example/problems/{code.lower()}",
            "title": "Request body rejected",
            "status": 413 if code == "REQUEST_BODY_TOO_LARGE" else 400,
            "detail": f"The request body exceeds the permitted limit of {limit} bytes." if code == "REQUEST_BODY_TOO_LARGE" else "The Content-Length header is invalid.",
            "code": code,
            "instance": path,
        }).encode("utf-8")
        status = 413 if code == "REQUEST_BODY_TOO_LARGE" else 400
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode("ascii")), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
