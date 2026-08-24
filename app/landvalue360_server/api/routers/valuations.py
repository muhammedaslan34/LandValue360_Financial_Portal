from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...schemas import (
    DataQualityPreviewRequest,
    ValuationRunCreate,
    ValuationRunDetailOut,
    ValuationRunSummaryOut,
)
from ...services.valuations import (
    create_valuation_run,
    get_valuation_run,
    list_valuation_runs,
    preview_data_quality,
)
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1", tags=["Valuation and data quality"])


@router.post("/data-quality/preview")
def post_data_quality_preview(
    payload: DataQualityPreviewRequest,
    context: AuthContext = Depends(require_permission(Permission.VALUATION_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    return preview_data_quality(
        session,
        context=context,
        project_version_id=payload.project_version_id,
        valuation_date=payload.valuation_date,
    )


@router.get("/valuation-runs", response_model=list[ValuationRunSummaryOut])
def get_valuation_runs(
    project_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.VALUATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return list_valuation_runs(
        session,
        context=context,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.post("/valuation-runs", response_model=ValuationRunDetailOut, status_code=201)
def post_valuation_run(
    payload: ValuationRunCreate,
    context: AuthContext = Depends(require_permission(Permission.VALUATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    return create_valuation_run(
        session,
        context=context,
        payload=payload.model_dump(mode="json"),
    )


@router.get("/valuation-runs/{run_id}", response_model=ValuationRunDetailOut)
def read_valuation_run(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.VALUATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return get_valuation_run(session, context=context, run_id=run_id)


@router.get("/valuation-runs/{run_id}/report.html", response_class=HTMLResponse)
def valuation_report(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.VALUATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from ...reporting import valuation_report_html

    run = get_valuation_run(session, context=context, run_id=run_id)
    return HTMLResponse(valuation_report_html(run), headers={"Cache-Control": "no-store"})


@router.get("/valuation-runs/{run_id}/export.xlsx")
def valuation_export_xlsx(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.VALUATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from ...reporting import valuation_xlsx

    run = get_valuation_run(session, context=context, run_id=run_id)
    payload = valuation_xlsx(run)
    filename = f"landvalue360-valuation-{run.id}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
