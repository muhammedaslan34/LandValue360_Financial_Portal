from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...audit import record_audit
from ...context import AuthContext
from ...enums import Permission
from ...json_tools import json_merge_patch, sha256_json
from ...landowner_studio import MODEL_VERSION
from ...unified_engine import run_unified_financial_engine
from ...models import AnalysisRun, utc_now
from ...schemas import AnalysisRunDetailOut
from ...services.tenant import get_policy_version, get_project_version, get_scenario
from ..dependencies import get_session, require_permission

router = APIRouter(tags=["Landowner fair share and monthly cashflow"])


class LandownerStudioRequest(BaseModel):
    project_snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)


class LandownerStudioRunRequest(BaseModel):
    project_version_id: str
    policy_pack_version_id: str
    scenario_id: str | None = None


@router.post("/api/v1/landowner-studio/preview")
def preview(
    payload: LandownerStudioRequest,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
):
    del context
    return run_unified_financial_engine(payload.project_snapshot, payload.policy_snapshot)


@router.post(
    "/api/v1/landowner-studio/runs",
    response_model=AnalysisRunDetailOut,
    status_code=201,
)
def create_landowner_run(
    payload: LandownerStudioRunRequest,
    context: AuthContext = Depends(require_permission(Permission.CALCULATION_RUN)),
    session: Session = Depends(get_session, scope="function"),
):
    """Persist an immutable, auditable fair-share calculation run."""

    version = get_project_version(session, context, payload.project_version_id)
    policy = get_policy_version(session, context, payload.policy_pack_version_id)
    scenario = get_scenario(session, context, payload.scenario_id) if payload.scenario_id else None
    if scenario is not None and scenario.project_version_id != version.id:
        from ...errors import NotFoundError

        raise NotFoundError("Scenario does not belong to the selected project version.")

    project_snapshot = deepcopy(version.input_snapshot)
    if scenario is not None:
        project_snapshot = json_merge_patch(project_snapshot, deepcopy(scenario.override_snapshot))
    project_snapshot["project_id"] = version.input_snapshot.get("project_id")
    project_snapshot["project_name"] = version.input_snapshot.get("project_name")
    policy_snapshot = deepcopy(policy.policy_snapshot)
    output = run_unified_financial_engine(project_snapshot, policy_snapshot)
    input_snapshot = {
        "project_snapshot": project_snapshot,
        "policy_snapshot": policy_snapshot,
        "scenario_id": scenario.id if scenario else None,
    }
    run = AnalysisRun(
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
        project_id=version.project_id,
        project_version_id=version.id,
        policy_pack_version_id=policy.id,
        scenario_id=scenario.id if scenario else None,
        analysis_type="LANDOWNER_FAIR_SHARE",
        status="SUCCESS" if output.get("summary", {}).get("status") == "PASS" else "SUCCESS_WITH_WARNINGS",
        analysis_model_version=MODEL_VERSION,
        input_snapshot=input_snapshot,
        input_hash=sha256_json(input_snapshot),
        output_snapshot=output,
        output_hash=str(output.get("calculation_hash") or sha256_json(output)),
        created_by_user_id=context.user_id,
        completed_at=utc_now(),
    )
    session.add(run)
    session.flush()
    record_audit(
        session,
        context=context,
        action="LANDOWNER_FAIR_SHARE_RUN_CREATED",
        entity_type="AnalysisRun",
        entity_id=run.id,
        after={
            "analysis_type": run.analysis_type,
            "status": run.status,
            "input_hash": run.input_hash,
            "output_hash": run.output_hash,
        },
    )
    return run
