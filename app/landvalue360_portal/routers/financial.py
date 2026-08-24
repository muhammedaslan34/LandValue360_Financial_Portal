from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..financial_engine import default_financial_model, normalize_financial_model, policy_controls
from ..financial_reports import build_financial_excel, build_financial_pdf
from ..financial_service import (
    active_engine_version,
    current_policy_version,
    execute_calculation_run,
    list_policy_versions,
    resolve_policy_version,
    get_run_result,
    run_payload,
    update_financial_model,
)
from ..models import CalculationRun, EngineVersion, FinancialPolicyVersion, ProjectVersion, User
from ..security import csrf_protect, current_user, user_permission_codes
from ..services import audit, current_version, require_project, user_org_ids
from ..web import templates

router = APIRouter()


def _require_permission(db: Session, user: User, code: str) -> None:
    if code not in user_permission_codes(db, user.id):
        raise HTTPException(status_code=403, detail=f"Missing permission: {code}")


def _project_version(db: Session, project_id: str, requested: str | None, fallback: ProjectVersion) -> ProjectVersion:
    if not requested:
        return fallback
    version = db.get(ProjectVersion, requested)
    if not version or version.project_id != project_id:
        raise HTTPException(status_code=404, detail="Project version not found")
    return version


def _run_for_project(db: Session, project_id: str, run_id: str) -> CalculationRun:
    run = db.get(CalculationRun, run_id)
    if not run or run.project_id != project_id:
        raise HTTPException(status_code=404, detail="Calculation run not found")
    return run


def _copy_path(source: dict[str, Any], target: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    src: Any = source
    for part in parts:
        if not isinstance(src, dict) or part not in src:
            return
        src = src[part]
    dst = target
    for part in parts[:-1]:
        dst = dst.setdefault(part, {})
    dst[parts[-1]] = deepcopy(src)


STANDARD_FINANCIAL_PATHS = (
    "valuation_date",
    "sales.start_month", "sales.duration_months", "sales.commercial_discount_rate",
    "sales.buyer_incentive_rate", "sales.refund_rate",
    "delivery.construction_start_month", "delivery.construction_duration_months",
    "delivery.cost_escalation_rate", "delivery.cost_contingency_rate",
    "funding.opening_cash", "funding.total_developer_equity", "funding.committed_additional_equity",
    "contract.method", "contract.share_rate", "contract.upfront_amount",
    "contract.upfront_payment_month", "contract.hybrid_upfront_amount",
    "contract.hybrid_upfront_payment_month", "contract.hybrid_variable_basis",
    "contract.minimum_guarantee_amount", "contract.minimum_guarantee_payment_month",
    "contract.minimum_guarantee_underlying_method", "contract.minimum_guarantee_underlying_share",
    "contract.net_deduction_treatment",
)

STANDARD_POLICY_CONTROL_KEYS = (
    "schema_version", "discount_rate", "government_discount_rate", "minimum_project_npv",
    "minimum_developer_equity_irr", "target_developer_irr", "minimum_developer_npv",
    "minimum_profit_on_cost", "target_developer_profit_on_cost", "minimum_developer_multiple",
    "maximum_funding_gap", "minimum_landowner_npv", "minimum_landowner_value_recovery",
    "minimum_landowner_share", "maximum_landowner_share", "allowed_contract_methods",
    "proposal_selection_method",
)


def _standard_user_financial_payload(existing: dict[str, Any], submitted: dict[str, Any]) -> dict[str, Any]:
    """Project standard-user edits onto the authoritative model without touching advanced inputs.

    The browser is not a security boundary. Hidden financing, collection and curve
    settings remain exactly as last approved by policy or an authorized analyst,
    even when a standard account submits a handcrafted JSON body.
    """
    result = deepcopy(existing or {})
    for path in STANDARD_FINANCIAL_PATHS:
        _copy_path(submitted, result, path)
    result["advanced_overrides_enabled"] = bool((existing or {}).get("advanced_overrides_enabled", False))
    return result


def _standard_financial_model_view(model: dict[str, Any] | None) -> dict[str, Any]:
    """Return only decision inputs exposed to a standard portal user."""
    source = model or {}
    result: dict[str, Any] = {
        "schema_version": source.get("schema_version"),
        "advanced_overrides_enabled": False,
    }
    for path in STANDARD_FINANCIAL_PATHS:
        _copy_path(source, result, path)
    return result


def _standard_policy_controls_view(controls: dict[str, Any] | None) -> dict[str, Any]:
    source = controls or {}
    return {key: deepcopy(source[key]) for key in STANDARD_POLICY_CONTROL_KEYS if key in source}


def _standard_run_view(payload: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(payload)
    if "financial_model" in result:
        result["financial_model"] = _standard_financial_model_view(result.get("financial_model"))
    result.pop("full_result", None)
    return result


def _policy_version_payload(row: FinancialPolicyVersion, *, current_id: str, advanced: bool) -> dict[str, Any]:
    controls = policy_controls(row.policy_snapshot)
    return {
        "id": row.id,
        "version_number": row.version_number,
        "status": row.status,
        "effective_from": row.effective_from,
        "snapshot_hash": row.snapshot_hash,
        "change_reason": row.change_reason,
        "display_name_ar": controls.get("display_name_ar") or f"السياسة v{row.version_number}",
        "display_name_en": controls.get("display_name_en") or f"Policy v{row.version_number}",
        "description_ar": controls.get("description_ar") or "",
        "description_en": controls.get("description_en") or "",
        "user_selectable": bool(controls.get("user_selectable", True)),
        "is_default": row.id == current_id,
        **({"controls": controls} if advanced else {}),
    }


@router.get("/portal/projects/{project_id}/financial", response_class=HTMLResponse)
def financial_page(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.view")
    project = require_project(db, user, project_id)
    version = current_version(db, project)
    permissions = user_permission_codes(db, user.id)
    admin_access = "admin.projects" in permissions and project.organization_id not in user_org_ids(db, user.id)
    if admin_access:
        audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="ADMIN_FINANCIAL_VIEWED", entity_type="PROJECT", entity_id=project.id, ip=request.client.host if request.client else None)
        db.commit()
    return templates.TemplateResponse(request, "financial.html", {
        "title": f"النموذج المالي - {project.name}",
        "user": user, "project": project, "project_version": version,
        "admin_access": admin_access,
    })


@router.get("/api/projects/{project_id}/financial")
def financial_state(
    project_id: str,
    project_version_id: str | None = None,
    policy_version_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _require_permission(db, user, "financial.view")
    project = require_project(db, user, project_id)
    current = current_version(db, project)
    version = _project_version(db, project.id, project_version_id, current)
    snapshot = deepcopy(version.input_snapshot or {})
    permissions = user_permission_codes(db, user.id)
    advanced_access = "financial.advanced_inputs" in permissions
    policy_admin = "admin.financial_policy" in permissions
    try:
        policy = resolve_policy_version(db, policy_version_id, allow_nonselectable=policy_admin)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    current_policy = current_policy_version(db)
    available_policies = list_policy_versions(db, include_nonselectable=policy_admin)
    controls = policy_controls(policy.policy_snapshot)
    raw_model = snapshot.get("financial_model")
    model = normalize_financial_model(
        raw_model or default_financial_model(planning=snapshot.get("planning") or {}, controls=controls),
        planning=snapshot.get("planning") or {},
        controls=controls,
    )
    engine = active_engine_version(db)
    visible_model = model if advanced_access else _standard_financial_model_view(model)
    visible_controls = controls if advanced_access else _standard_policy_controls_view(controls)
    latest = db.scalar(select(CalculationRun).where(
        CalculationRun.project_id == project.id,
        CalculationRun.project_version_id == version.id,
        CalculationRun.financial_policy_version_id == policy.id,
    ).order_by(CalculationRun.created_at.desc()))
    versions = list(db.scalars(select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.version_number.desc())).all())
    return {
        "project": {
            "id": project.id, "name": project.name, "reference": project.reference, "status": project.status,
            "currency": ((snapshot.get("identity") or {}).get("currency") or "USD"),
        },
        "current_project_version_id": current.id,
        "project_version": {"id": version.id, "version_number": version.version_number, "immutable": version.immutable, "status": version.status, "snapshot_hash": version.snapshot_hash},
        "project_versions": [{"id": row.id, "version_number": row.version_number, "immutable": row.immutable, "status": row.status, "snapshot_hash": row.snapshot_hash} for row in versions],
        "financial_model": visible_model,
        "policy": {
            "id": policy.id, "version_number": policy.version_number, "snapshot_hash": policy.snapshot_hash,
            "controls": visible_controls, "is_default": policy.id == current_policy.id,
            "display_name_ar": controls.get("display_name_ar"), "display_name_en": controls.get("display_name_en"),
            "description_ar": controls.get("description_ar"), "description_en": controls.get("description_en"),
        },
        "policy_versions": [_policy_version_payload(row, current_id=current_policy.id, advanced=policy_admin) for row in available_policies],
        "engine": {"id": engine.id, "engine_version": engine.engine_version, "adapter_version": engine.adapter_version, "source_hash": engine.source_hash, "manifest": engine.manifest if advanced_access else {}},
        "permissions": sorted(permissions),
        "advanced_financial_access": advanced_access,
        "latest_run": (_standard_run_view(run_payload(db, latest)) if not advanced_access else run_payload(db, latest)) if latest else None,
    }


@router.put("/api/projects/{project_id}/financial")
def save_financial_model(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
    policy_version_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    _=Depends(csrf_protect),
):
    _require_permission(db, user, "financial.edit")
    project = require_project(db, user, project_id)
    version = current_version(db, project)
    before = deepcopy((version.input_snapshot or {}).get("financial_model"))
    permissions = user_permission_codes(db, user.id)
    advanced_access = "financial.advanced_inputs" in permissions
    policy_admin = "admin.financial_policy" in permissions
    try:
        selected_policy = resolve_policy_version(db, policy_version_id, allow_nonselectable=policy_admin)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not advanced_access:
        existing = before or {}
        payload = _standard_user_financial_payload(existing, payload)
    else:
        payload = deepcopy(payload)
        payload["advanced_overrides_enabled"] = bool(payload.get("advanced_overrides_enabled", True))
    try:
        model = update_financial_model(db, version=version, payload=payload, user=user, policy_version=selected_policy)
        audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="FINANCIAL_MODEL_SAVED", entity_type="PROJECT_VERSION", entity_id=version.id, before=before, after={"financial_model": model, "snapshot_hash": version.snapshot_hash}, ip=request.client.host if request.client else None)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409 if version.immutable else 422, detail=str(exc)) from exc
    visible_model = model if advanced_access else _standard_financial_model_view(model)
    return {"ok": True, "financial_model": visible_model, "project_version_id": version.id, "snapshot_hash": version.snapshot_hash}


@router.post("/api/projects/{project_id}/financial/runs", status_code=201)
def create_calculation_run(project_id: str, payload: dict[str, Any] | None, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    _require_permission(db, user, "financial.run")
    project = require_project(db, user, project_id)
    current = current_version(db, project)
    payload = payload or {}
    version = _project_version(db, project.id, payload.get("project_version_id"), current)
    permissions = user_permission_codes(db, user.id)
    can_override_engine = "admin.financial_policy" in permissions
    try:
        policy = resolve_policy_version(
            db, payload.get("policy_version_id"), allow_nonselectable=can_override_engine
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    engine = active_engine_version(db)
    if payload.get("engine_version_id") and not can_override_engine:
        raise HTTPException(status_code=403, detail="Only a financial-policy administrator may override the engine version")
    if payload.get("engine_version_id"):
        candidate_engine = db.get(EngineVersion, str(payload["engine_version_id"]))
        if not candidate_engine:
            raise HTTPException(status_code=404, detail="Engine version not found")
        engine = candidate_engine
    try:
        run = execute_calculation_run(db, project=project, version=version, user=user, policy_version=policy, engine_version=engine)
        audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="FINANCIAL_CALCULATION_COMPLETED", entity_type="CALCULATION_RUN", entity_id=run.id, after={"input_hash": run.input_hash, "result_hash": run.result_hash, "policy_version_id": policy.id, "engine_version_id": engine.id}, ip=request.client.host if request.client else None)
        db.commit()
    except Exception as exc:
        try:
            audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="FINANCIAL_CALCULATION_FAILED", entity_type="CALCULATION_RUN", entity_id=None, after={"error": str(exc)[:2000]}, ip=request.client.host if request.client else None)
            db.commit()
        except Exception:
            db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response_payload = run_payload(db, run, include_monthly=True)
    return response_payload if "financial.advanced_inputs" in permissions else _standard_run_view(response_payload)


@router.get("/api/projects/{project_id}/financial/runs")
def list_calculation_runs(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.view")
    project = require_project(db, user, project_id)
    rows = list(db.scalars(select(CalculationRun).where(CalculationRun.project_id == project.id).order_by(CalculationRun.created_at.desc())).all())
    permissions = user_permission_codes(db, user.id)
    payloads = [run_payload(db, row) for row in rows]
    return payloads if "financial.advanced_inputs" in permissions else [_standard_run_view(row) for row in payloads]


@router.get("/api/projects/{project_id}/financial/runs/{run_id}")
def get_calculation_run(project_id: str, run_id: str, include_full: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.view")
    project = require_project(db, user, project_id)
    run = _run_for_project(db, project.id, run_id)
    permissions = user_permission_codes(db, user.id)
    advanced_access = "financial.advanced_inputs" in permissions
    if include_full and not advanced_access:
        raise HTTPException(status_code=403, detail="Full calculation payload requires advanced financial access")
    payload = run_payload(db, run, include_monthly=True, include_full=include_full)
    return payload if advanced_access else _standard_run_view(payload)


@router.get("/api/projects/{project_id}/financial/runs/{run_id}/audit")
def get_calculation_audit(project_id: str, run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.view")
    project = require_project(db, user, project_id)
    run = _run_for_project(db, project.id, run_id)
    payload = run_payload(db, run)
    return {
        "calculation_run_id": run.id,
        "status": run.status,
        "financial_audit": payload.get("financial_audit") or {},
        "recommendation_validation": payload.get("recommendation_validation") or {},
        "policy_compliant": payload.get("policy_compliant"),
        "reconciliation_passed": payload.get("reconciliation_passed"),
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
    }


@router.get("/api/projects/{project_id}/financial/runs/{run_id}/cashflow")
def get_calculation_cashflow(project_id: str, run_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.view")
    project = require_project(db, user, project_id)
    run = _run_for_project(db, project.id, run_id)
    payload = run_payload(db, run, include_monthly=True)
    return {"monthly": payload.get("monthly_cashflow") or [], "annual": payload.get("annual_cashflow") or []}


def _report_context(db: Session, project_id: str, run_id: str, user: User):
    project = require_project(db, user, project_id)
    run = _run_for_project(db, project.id, run_id)
    if run.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only completed calculation runs can be exported")
    project_version = db.get(ProjectVersion, run.project_version_id)
    policy_version = db.get(FinancialPolicyVersion, run.financial_policy_version_id)
    engine_version = db.get(EngineVersion, run.engine_version_id)
    if not project_version or not policy_version or not engine_version:
        raise HTTPException(status_code=409, detail="Calculation provenance is incomplete")
    payload = run_payload(db, run, include_monthly=True)
    return project, project_version, run, policy_version, engine_version, payload


@router.get("/api/projects/{project_id}/financial/runs/{run_id}/report.xlsx")
def export_financial_excel(project_id: str, run_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.export")
    project, project_version, run, policy_version, engine_version, payload = _report_context(db, project_id, run_id, user)
    data = build_financial_excel(project=project, project_version=project_version, run=run, policy_version=policy_version, engine_version=engine_version, payload=payload)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="FINANCIAL_EXCEL_EXPORTED", entity_type="CALCULATION_RUN", entity_id=run.id, after={"result_hash": run.result_hash}, ip=request.client.host if request.client else None)
    db.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{project.reference}-financial-{run.id[:8]}.xlsx"'})


@router.get("/api/projects/{project_id}/financial/runs/{run_id}/report.pdf")
def export_financial_pdf(project_id: str, run_id: str, request: Request, lang: str = "ar", user: User = Depends(current_user), db: Session = Depends(get_db)):
    _require_permission(db, user, "financial.export")
    project, project_version, run, policy_version, engine_version, payload = _report_context(db, project_id, run_id, user)
    data = build_financial_pdf(project=project, project_version=project_version, run=run, policy_version=policy_version, engine_version=engine_version, payload=payload, language=lang)
    audit(db, user=user, organization_id=project.organization_id, project_id=project.id, action="FINANCIAL_PDF_EXPORTED", entity_type="CALCULATION_RUN", entity_id=run.id, after={"result_hash": run.result_hash}, ip=request.client.host if request.client else None)
    db.commit()
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{project.reference}-financial-{run.id[:8]}.pdf"'})
