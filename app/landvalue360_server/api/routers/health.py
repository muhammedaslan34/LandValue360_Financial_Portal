from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from landvalue360_common.versions import ENGINE_VERSION

from ... import __version__
from ...schemas import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health/live", response_model=HealthResponse)
@router.get("/api/v1/health/live", response_model=HealthResponse)
def live(request: Request) -> HealthResponse:
    return HealthResponse(
        status="LIVE",
        application_version=__version__,
        calculation_model_version=ENGINE_VERSION,
        database=request.app.state.database.engine.dialect.name,
    )


@router.get("/health/ready", response_model=HealthResponse)
@router.get("/api/v1/health/ready", response_model=HealthResponse)
def ready(request: Request) -> HealthResponse:
    with request.app.state.database.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return HealthResponse(
        status="READY",
        application_version=__version__,
        calculation_model_version=ENGINE_VERSION,
        database=request.app.state.database.engine.dialect.name,
    )
