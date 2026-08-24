from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...errors import AppError
from ...schemas import (
    AnalysisRunDetailOut,
    AnalysisRunSummaryOut,
    MonteCarloRunRequest,
    RiskAssessmentRequest,
    SensitivityRunRequest,
    TenderEvaluationRequest,
    TenderReadinessRequest,
)
from ...services.risk_tender import (
    evaluate_tender_bids,
    get_analysis_run,
    list_analysis_runs,
    run_monte_carlo,
    run_risk_assessment,
    run_sensitivity,
    tender_readiness,
)
from ...reporting import analysis_report_html, analysis_xlsx
from ..dependencies import get_session, require_permission

router = APIRouter(tags=["Risk, sensitivity and tender"])


@router.post("/api/v1/analysis/live-preview", include_in_schema=False)
def retired_live_preview(
    _context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
):
    raise AppError(
        "LIVE_PREVIEW_RETIRED",
        "Automatic financial live preview was retired in Platform 0.12.0. Use the local input summary and run an explicit calculation.",
        status_code=410,
        title="Live preview retired",
    )


@router.post("/api/v1/risk-assessments", response_model=AnalysisRunDetailOut, status_code=201)
def post_risk_assessment(
    payload: RiskAssessmentRequest,
    context: AuthContext = Depends(require_permission(Permission.RISK_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    return run_risk_assessment(session, context=context, project_version_id=payload.project_version_id, policy_pack_version_id=payload.policy_pack_version_id, valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id, scenario_id=payload.scenario_id, items=[item.model_dump() for item in payload.items])


@router.post("/api/v1/sensitivity-runs", response_model=AnalysisRunDetailOut, status_code=201)
def post_sensitivity_run(
    payload: SensitivityRunRequest,
    context: AuthContext = Depends(require_permission(Permission.SENSITIVITY_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    data = payload.model_dump(exclude_none=True)
    for key in ("project_version_id", "policy_pack_version_id", "valuation_policy_pack_version_id", "scenario_id"):
        data.pop(key, None)
    return run_sensitivity(session, context=context, project_version_id=payload.project_version_id, policy_pack_version_id=payload.policy_pack_version_id, valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id, scenario_id=payload.scenario_id, configuration=data)


@router.post("/api/v1/monte-carlo-runs", response_model=AnalysisRunDetailOut, status_code=201)
def post_monte_carlo_run(
    payload: MonteCarloRunRequest,
    context: AuthContext = Depends(require_permission(Permission.SENSITIVITY_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    data = payload.model_dump(exclude_none=True)
    for key in ("project_version_id", "policy_pack_version_id", "valuation_policy_pack_version_id", "scenario_id"):
        data.pop(key, None)
    data["distributions"] = {key: value.model_dump(exclude_none=True) for key, value in payload.distributions.items()}
    return run_monte_carlo(session, context=context, project_version_id=payload.project_version_id, policy_pack_version_id=payload.policy_pack_version_id, valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id, scenario_id=payload.scenario_id, configuration=data)


@router.post("/api/v1/tender-readiness-runs", response_model=AnalysisRunDetailOut, status_code=201)
def post_tender_readiness(
    payload: TenderReadinessRequest,
    context: AuthContext = Depends(require_permission(Permission.TENDER_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    return tender_readiness(session, context=context, project_version_id=payload.project_version_id, policy_pack_version_id=payload.policy_pack_version_id, valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id, scenario_id=payload.scenario_id, risk_items=None if payload.risk_items is None else [item.model_dump() for item in payload.risk_items])


@router.post("/api/v1/tender-evaluation-runs", response_model=AnalysisRunDetailOut, status_code=201)
def post_tender_evaluation(
    payload: TenderEvaluationRequest,
    context: AuthContext = Depends(require_permission(Permission.TENDER_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    return evaluate_tender_bids(session, context=context, project_version_id=payload.project_version_id, policy_pack_version_id=payload.policy_pack_version_id, valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id, scenario_id=payload.scenario_id, criteria_weights=payload.criteria_weights, bids=[item.model_dump() for item in payload.bids])


@router.get("/api/v1/analysis-runs", response_model=list[AnalysisRunSummaryOut])
def get_analysis_runs(
    project_id: str | None = None,
    analysis_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.RISK_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return list_analysis_runs(session, context=context, project_id=project_id, analysis_type=analysis_type, limit=limit, offset=offset)


@router.get("/api/v1/analysis-runs/{run_id}", response_model=AnalysisRunDetailOut)
def get_analysis_run_detail(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.RISK_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return get_analysis_run(session, context=context, run_id=run_id)


@router.get("/api/v1/analysis-runs/{run_id}/report.html")
def get_analysis_html(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.RISK_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    run = get_analysis_run(session, context=context, run_id=run_id)
    return HTMLResponse(analysis_report_html(run))


@router.get("/api/v1/analysis-runs/{run_id}/export.xlsx")
def get_analysis_excel(
    run_id: str,
    context: AuthContext = Depends(require_permission(Permission.RISK_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    run = get_analysis_run(session, context=context, run_id=run_id)
    return Response(content=analysis_xlsx(run), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="landvalue360-{run.analysis_type.lower()}-{run.id}.xlsx"'})
