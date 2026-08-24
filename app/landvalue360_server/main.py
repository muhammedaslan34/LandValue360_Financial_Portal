"""FastAPI application factory with hard product-edition boundaries."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from landvalue360_government.manifest import (
    DEVELOPER_VERSION,
    ENGINE_VERSION,
    GOVERNMENT_VERSION,
    PLATFORM_VERSION,
)
from landvalue360_kernel.exceptions import (
    CalculationError,
    InputValidationError,
    LandValue360Error,
    UnsupportedMethodError,
)
from landvalue360_valuation import VALUATION_MODEL_VERSION
from landvalue360_common.versions import ENGINE_VERSION, FINANCE_MODEL_VERSION

from . import __version__
from .api.middleware import CredentialQueryGuardMiddleware, RequestBodyLimitMiddleware, RequestIdMiddleware
from .api.routers import (
    analysis,
    audit,
    auth,
    calculations,
    evidence,
    government,
    government_projects,
    health,
    landowner,
    organizations,
    policies,
    projects,
    risk_tender,
    ui,
    valuations,
)
from .config import Settings, get_settings
from .database import Database
from .errors import AppError
from .project_contract import ProjectContractError
from .security import LoginRateLimiter


PRODUCT_VERSIONS = {
    "platform": PLATFORM_VERSION,
    "developer": DEVELOPER_VERSION,
    "landowner": GOVERNMENT_VERSION,
    "government": GOVERNMENT_VERSION,
    "engine": ENGINE_VERSION,
}


def _problem(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    errors: object | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"https://landvalue360.example/problems/{code.lower()}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "instance": str(request.url.path),
        "request_id": getattr(request.state, "request_id", None),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(status_code=status, content=body)


def _edition_title(mode: str) -> str:
    return {
        "developer": "LandValue360 Developer API",
        "government": "LandValue360 Landowner API",
        "administration": "LandValue360 Platform Administration API",
        "combined": "LandValue360 Platform API",
    }[mode]


def create_app(*, settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate()
    mode = resolved_settings.edition_mode
    resolved_database = database or Database(resolved_settings)
    package_root = Path(__file__).resolve().parent
    portal_web_root = package_root / "web_portal"
    developer_web_root = package_root / "web"
    government_web_root = package_root / "web_government"
    admin_web_root = package_root / "web_admin"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.auto_create_schema:
            resolved_database.create_schema()
        yield

    app = FastAPI(
        title=_edition_title(mode),
        summary="Edition-isolated landowner decision and development-feasibility platform.",
        description=(
            f"Platform {PLATFORM_VERSION} exposes Developer, Landowner and Administration as separate runtime "
            f"products while retaining Engine {ENGINE_VERSION} as the single deterministic financial source of truth."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if resolved_settings.enable_docs else None,
        redoc_url="/redoc" if resolved_settings.enable_docs else None,
        openapi_url="/openapi.json" if resolved_settings.enable_docs else None,
        contact={"name": "LandValue360 Product Team"},
        license_info={"name": "Proprietary — subject to executed commercial licence"},
    )
    app.state.settings = resolved_settings
    app.state.database = resolved_database
    app.state.login_limiter = LoginRateLimiter(
        attempts=resolved_settings.login_rate_limit_attempts,
        window_seconds=resolved_settings.login_rate_limit_window_seconds,
    )

    app.add_middleware(
        RequestBodyLimitMiddleware,
        default_limit=max(2 * 1024 * 1024, resolved_settings.max_excel_import_bytes),
        route_limits={
            "/api/v1/projects/import.lv360": resolved_settings.max_project_package_bytes,
            "/api/v1/projects/": max(resolved_settings.max_excel_import_bytes, resolved_settings.max_evidence_file_bytes),
        },
    )

    if resolved_settings.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(resolved_settings.trusted_hosts))
    app.add_middleware(RequestIdMiddleware, behind_https_proxy=resolved_settings.behind_https_proxy)
    app.add_middleware(CredentialQueryGuardMiddleware)

    if mode == "combined" and portal_web_root.exists():
        app.mount("/portal/assets", StaticFiles(directory=str(portal_web_root / "assets")), name="portal-assets")
    if mode in {"combined", "developer"} and developer_web_root.exists():
        app.mount("/app/assets", StaticFiles(directory=str(developer_web_root / "assets")), name="developer-assets")
    if mode in {"combined", "government"} and government_web_root.exists():
        app.mount("/government/assets", StaticFiles(directory=str(government_web_root / "assets")), name="government-assets")
        app.mount("/landowner/assets", StaticFiles(directory=str(government_web_root / "assets")), name="landowner-assets")
    if mode in {"combined", "administration"} and admin_web_root.exists():
        app.mount("/admin/assets", StaticFiles(directory=str(admin_web_root / "assets")), name="administration-assets")

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        response = _problem(
            request,
            status=exc.status_code,
            title=exc.title,
            detail=exc.detail,
            code=exc.code,
        )
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        if exc.status_code == 429:
            response.headers["Retry-After"] = str(resolved_settings.login_rate_limit_window_seconds)
        return response

    @app.exception_handler(InputValidationError)
    async def kernel_input_error_handler(request: Request, exc: InputValidationError) -> JSONResponse:
        error = {
            "code": exc.code or "FINANCIAL_INPUT_INVALID",
            "path": exc.path,
            "detail": str(exc),
        }
        return _problem(
            request,
            status=422,
            title="Financial input validation failed",
            detail=str(exc),
            code=error["code"],
            errors=[error],
        )

    @app.exception_handler(ProjectContractError)
    async def project_contract_error_handler(request: Request, exc: ProjectContractError) -> JSONResponse:
        return _problem(
            request,
            status=422,
            title="Project contract validation failed",
            detail=str(exc),
            code="PROJECT_CONTRACT_INVALID",
            errors=[{"code": "PROJECT_CONTRACT_INVALID", "path": "project_contract", "detail": str(exc)}],
        )

    @app.exception_handler(UnsupportedMethodError)
    async def unsupported_method_error_handler(request: Request, exc: UnsupportedMethodError) -> JSONResponse:
        return _problem(
            request,
            status=422,
            title="Unsupported calculation method",
            detail=str(exc),
            code="UNSUPPORTED_METHOD",
            errors=[{"code": "UNSUPPORTED_METHOD", "path": None, "detail": str(exc)}],
        )

    @app.exception_handler(CalculationError)
    async def calculation_error_handler(request: Request, exc: CalculationError) -> JSONResponse:
        return _problem(
            request,
            status=422,
            title="Calculation could not be completed safely",
            detail=str(exc),
            code="CALCULATION_FAILED",
            errors=[{"code": "CALCULATION_FAILED", "path": None, "detail": str(exc)}],
        )

    @app.exception_handler(LandValue360Error)
    async def kernel_error_handler(request: Request, exc: LandValue360Error) -> JSONResponse:
        return _problem(
            request,
            status=422,
            title="Financial model error",
            detail=str(exc),
            code="FINANCIAL_MODEL_ERROR",
            errors=[{"code": "FINANCIAL_MODEL_ERROR", "path": None, "detail": str(exc)}],
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        validation_errors: list[dict[str, object]] = []
        for raw in exc.errors():
            item = dict(raw)
            context = item.get("ctx")
            if isinstance(context, dict):
                item["ctx"] = {key: str(value) for key, value in context.items()}
            validation_errors.append(jsonable_encoder(item))
        return _problem(
            request,
            status=422,
            title="Validation failed",
            detail="The request does not satisfy the API contract.",
            code="REQUEST_VALIDATION_FAILED",
            errors=validation_errors,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        del exc
        return _problem(
            request,
            status=409,
            title="Conflict",
            detail="The operation conflicts with an existing record or database invariant.",
            code="DATABASE_CONFLICT",
        )

    @app.get("/", include_in_schema=False)
    def browser_root():  # noqa: ANN202
        if mode == "developer":
            return RedirectResponse("/app/", status_code=307)
        if mode == "government":
            return RedirectResponse("/landowner/", status_code=307)
        if mode == "administration":
            return RedirectResponse("/admin/", status_code=307)
        return FileResponse(portal_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/api", tags=["Service information"])
    def service_information() -> dict[str, object]:
        exposed = {
            "developer": mode in {"combined", "developer"},
            "government": mode in {"combined", "government"},
            "administration": mode in {"combined", "administration"},
        }
        return {
            "name": _edition_title(mode).removesuffix(" API"),
            "application_version": __version__,
            "edition_mode": mode,
            "exposed_editions": exposed,
            "product_versions": PRODUCT_VERSIONS,
            "calculation_model_version": ENGINE_VERSION,
            "finance_model_version": FINANCE_MODEL_VERSION,
            "valuation_model_version": VALUATION_MODEL_VERSION,
            "platform_portal": "/" if mode == "combined" else None,
            "developer_application": "/app/" if exposed["developer"] else None,
            "landowner_application": "/landowner/" if exposed["government"] else None,
            "government_application": "/government/" if exposed["government"] else None,
            "administration_application": "/admin/" if exposed["administration"] else None,
            "documentation": "/docs" if resolved_settings.enable_docs else None,
        }

    if mode in {"combined", "developer"}:
        @app.get("/app", include_in_schema=False)
        @app.get("/app/", include_in_schema=False)
        def developer_application() -> FileResponse:
            return FileResponse(developer_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

        @app.get("/app/manifest.webmanifest", include_in_schema=False)
        def web_manifest() -> FileResponse:
            return FileResponse(developer_web_root / "manifest.webmanifest", media_type="application/manifest+json")

        @app.get("/app/sw.js", include_in_schema=False)
        def service_worker() -> FileResponse:
            return FileResponse(developer_web_root / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

        @app.get("/app/{client_path:path}", include_in_schema=False)
        def developer_client_route(client_path: str) -> FileResponse:
            del client_path
            return FileResponse(developer_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    if mode in {"combined", "government"}:
        @app.get("/landowner", include_in_schema=False)
        @app.get("/landowner/", include_in_schema=False)
        def landowner_application() -> FileResponse:
            return FileResponse(government_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

        @app.get("/landowner/{client_path:path}", include_in_schema=False)
        def landowner_client_route(client_path: str) -> FileResponse:
            del client_path
            return FileResponse(government_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

        # Legacy browser route retained for saved bookmarks and integrations.
        @app.get("/government", include_in_schema=False)
        @app.get("/government/", include_in_schema=False)
        def government_application() -> FileResponse:
            return FileResponse(government_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

        @app.get("/government/{client_path:path}", include_in_schema=False)
        def government_client_route(client_path: str) -> FileResponse:
            del client_path
            return FileResponse(government_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    if mode in {"combined", "administration"}:
        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        def administration_application() -> FileResponse:
            return FileResponse(admin_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

        @app.get("/admin/{client_path:path}", include_in_schema=False)
        def administration_client_route(client_path: str) -> FileResponse:
            del client_path
            return FileResponse(admin_web_root / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    # Common service surface.
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(audit.router)

    # Operational surfaces are mounted only in their edition.
    if mode in {"combined", "developer"}:
        app.include_router(projects.router)
        app.include_router(policies.router)
        app.include_router(calculations.router)
        app.include_router(analysis.router)
        app.include_router(evidence.router)
        app.include_router(valuations.router)
        app.include_router(risk_tender.router)
        app.include_router(landowner.router)
        app.include_router(ui.router)
    if mode in {"combined", "government"}:
        app.include_router(government.router)
        app.include_router(government_projects.router)
    if mode in {"combined", "administration"}:
        app.include_router(organizations.router)
        if mode == "administration":
            app.include_router(policies.router)

    return app


app = create_app()
