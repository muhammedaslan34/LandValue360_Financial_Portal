from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...schemas import NegotiationAnalysisRequest, ScenarioComparisonRequest
from ...services.analysis import analyze_negotiation_rows, compare_scenarios
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1/analysis", tags=["Scenario and negotiation analysis"])


@router.post("/scenario-comparison")
def post_scenario_comparison(
    payload: ScenarioComparisonRequest,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    return compare_scenarios(
        session,
        context=context,
        project_version_id=payload.project_version_id,
        policy_pack_version_id=payload.policy_pack_version_id,
        valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id,
        scenario_ids=payload.scenario_ids,
        include_base=payload.include_base,
    )


@router.post("/negotiation")
def post_negotiation_analysis(
    payload: NegotiationAnalysisRequest,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    return analyze_negotiation_rows(
        session,
        context=context,
        project_version_id=payload.project_version_id,
        policy_pack_version_id=payload.policy_pack_version_id,
        valuation_policy_pack_version_id=payload.valuation_policy_pack_version_id,
        scenario_id=payload.scenario_id,
        rows=[item.model_dump() for item in payload.rows],
    )
