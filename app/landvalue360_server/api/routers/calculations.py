from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...models import CalculationRun, utc_now
from ...errors import ConflictError
from ...report_readiness import calculation_report_readiness
from ...schemas import (
    CalculationReplayOut,
    CalculationRunCreate,
    CalculationRunDetailOut,
    CalculationRunSummaryOut,
)
from ...services.calculations import (
    create_calculation_run,
    get_calculation_run,
    replay_calculation_run,
)
from ...services.tenant import tenant_clause
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1/calculation-runs", tags=["Calculation runs"])


@router.get("", response_model=list[CalculationRunSummaryOut])
def list_calculation_runs(
    project_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[CalculationRun]:
    statement = select(CalculationRun).where(*tenant_clause(CalculationRun, context))
    if project_id is not None:
        statement = statement.where(CalculationRun.project_id == project_id)
    if status is not None:
        statement = statement.where(CalculationRun.status == status)
    statement = statement.order_by(CalculationRun.created_at.desc()).offset(offset).limit(limit)
    return list(session.scalars(statement).all())


@router.post("", response_model=CalculationRunDetailOut, status_code=201)
def post_calculation_run(
    payload: CalculationRunCreate,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> CalculationRun:
    return create_calculation_run(
        session,
        context=context,
        project_version_id=payload.project_version_id,
        policy_pack_version_id=payload.policy_pack_version_id,
        valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id,
        scenario_id=payload.scenario_id,
        mode=payload.mode,
        case_id=payload.case_id,
        description=payload.description,
        analysis_level=payload.analysis_level,
    )


@router.get("/{run_id}", response_model=CalculationRunDetailOut)
def read_calculation_run(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> CalculationRun:
    return get_calculation_run(session, context=context, run_id=run_id)


@router.post("/{run_id}/replay", response_model=CalculationReplayOut, status_code=201)
def post_replay_calculation_run(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> CalculationReplayOut:
    run, matches = replay_calculation_run(session, context=context, run_id=run_id)
    return CalculationReplayOut(output_matches_original=matches, run=run)


@router.post("/{run_id}/lock", response_model=CalculationRunDetailOut)
def lock_calculation_run(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> CalculationRun:
    run = get_calculation_run(session, context=context, run_id=run_id)
    if run.locked_at is not None:
        return run
    readiness = calculation_report_readiness(session, run, require_locked=False)
    if not readiness["ready"]:
        codes = ", ".join(row["code"] for row in readiness["failures"])
        raise ConflictError("CALCULATION_RUN_NOT_LOCKABLE", f"The run cannot be locked until readiness checks pass: {codes}.")
    run.locked_at = utc_now()
    run.report_readiness = "READY_FOR_OFFICIAL_REPORT"
    session.flush()
    return run


@router.get("/{run_id}/report-readiness")
def get_developer_report_readiness(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    run = get_calculation_run(session, context=context, run_id=run_id)
    return calculation_report_readiness(session, run)


def _require_report_ready(session: Session, run: CalculationRun, *, official: bool) -> dict:
    readiness = calculation_report_readiness(session, run, require_locked=official)
    if official and not readiness["ready"]:
        codes = ", ".join(row["code"] for row in readiness["failures"])
        raise ConflictError(
            "OFFICIAL_REPORT_NOT_READY",
            f"An official report cannot be issued until the readiness gate passes. Failed checks: {codes}.",
        )
    return readiness


@router.get("/{run_id}/reports/{report_type}.html")
def export_developer_report_html(
    run_id: str,
    report_type: str,
    language: str = Query(default="en", pattern="^(ar|en)$"),
    official: bool = Query(default=False),
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from fastapi.responses import HTMLResponse
    from ...developer_reports import render_developer_report_html

    if report_type not in {"executive", "technical"}:
        from ...errors import NotFoundError
        raise NotFoundError("Developer report type not found.")
    run = get_calculation_run(session, context=context, run_id=run_id)
    readiness = _require_report_ready(session, run, official=official)
    return HTMLResponse(
        render_developer_report_html(run, language=language, report_type=report_type),
        headers={
            "Cache-Control": "no-store",
            "X-LV360-Report-Readiness": readiness["status"],
            "X-LV360-Report-Class": "OFFICIAL" if official else "ADVISORY",
        },
    )


@router.get("/{run_id}/reports/{report_type}.pdf")
def export_developer_report_pdf(
    run_id: str,
    report_type: str,
    language: str = Query(default="en", pattern="^(ar|en)$"),
    official: bool = Query(default=False),
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from fastapi.responses import Response
    from ...developer_reports import render_developer_report_pdf
    from ...errors import NotFoundError

    if report_type not in {"executive", "technical"}:
        raise NotFoundError("Developer report type not found.")
    run = get_calculation_run(session, context=context, run_id=run_id)
    readiness = _require_report_ready(session, run, official=official)
    try:
        payload = render_developer_report_pdf(run, language=language, report_type=report_type)
    except RuntimeError as exc:
        raise ConflictError("PDF_RENDERER_UNAVAILABLE", str(exc)) from exc
    filename = f"landvalue360-{run.case_id}-{report_type}-{language}.pdf".replace(" ", "-")
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-LV360-Report-Readiness": readiness["status"],
            "X-LV360-Report-Class": "OFFICIAL" if official else "ADVISORY",
        },
    )


@router.get("/{run_id}/cash-flow.csv")
def export_calculation_cash_flow_csv(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from fastapi.responses import Response
    from ...reporting import cash_flow_csv

    run = get_calculation_run(session, context=context, run_id=run_id)
    payload = cash_flow_csv(run.output_snapshot or {})
    filename = f"landvalue360-{run.case_id}-cash-flow.csv".replace(" ", "-")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/export.xlsx")
def export_calculation_xlsx(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    from fastapi.responses import Response
    from ...reporting import calculation_xlsx

    run = get_calculation_run(session, context=context, run_id=run_id)
    payload = calculation_xlsx(run)
    filename = f"landvalue360-{run.case_id}-analysis.xlsx".replace(" ", "-")
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
