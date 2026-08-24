from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import get_settings
from .database import Base, engine, session_scope
from .routers import admin, auth, financial, operations, portal
from .services import seed_defaults


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.is_production:
        Base.metadata.create_all(engine)
    with session_scope() as db:
        seed_defaults(db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LandValue360 Standalone Financial Portal", version=__version__, docs_url="/docs" if not settings.is_production else None, lifespan=lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[x.strip() for x in settings.trusted_hosts.split(",") if x.strip()] + (["*"] if not settings.is_production else []))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(PermissionError)
    async def permission_error(_: Request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    package_root = Path(__file__).resolve().parent
    app.mount("/static", StaticFiles(directory=str(package_root / "static")), name="static")
    app.include_router(auth.router)
    app.include_router(portal.router)
    app.include_router(financial.router)
    app.include_router(operations.router)
    app.include_router(admin.router)

    @app.get("/api/health/live")
    def health_live():
        return {"status": "live", "version": __version__}

    @app.get("/api/health/ready")
    def health_ready():
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return {"status": "ready", "version": __version__, "database": "ok"}
        except Exception as exc:
            return JSONResponse(status_code=503, content={"status": "not_ready", "error": str(exc)})

    return app


app = create_app()
