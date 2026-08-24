from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from landvalue360_common.versions import UNIFIED_ENGINE_ADAPTER_VERSION
from landvalue360_kernel.manifest import ENGINE_VERSION

from .financial_engine import (
    PORTAL_FINANCIAL_ADAPTER_VERSION,
    PORTAL_POLICY_CODE,
    D,
    apply_policy_controls,
    default_financial_policy_snapshot,
    effective_project_input_snapshot,
    engine_registration_manifest,
    engine_source_hash,
    json_ready,
    normalize_financial_model,
    policy_controls,
    run_financial_model,
    sha256_json,
)
from .models import (
    CalculationRun,
    CalculationRunResult,
    EngineVersion,
    FinancialPolicy,
    FinancialPolicyVersion,
    MonthlyCashflowSnapshot,
    NegotiationResult,
    Project,
    ProjectVersion,
    User,
    utcnow,
)


def seed_financial_defaults(db: Session) -> tuple[FinancialPolicyVersion, EngineVersion]:
    policy = db.scalar(select(FinancialPolicy).where(FinancialPolicy.code == PORTAL_POLICY_CODE))
    if not policy:
        policy = FinancialPolicy(
            code=PORTAL_POLICY_CODE,
            name="Standalone Financial Portal Policy",
            description="Versioned policy governing monthly feasibility, residual land value and landowner negotiation limits.",
            active=True,
        )
        db.add(policy)
        db.flush()
    versions = list(db.scalars(
        select(FinancialPolicyVersion)
        .where(FinancialPolicyVersion.financial_policy_id == policy.id)
        .order_by(FinancialPolicyVersion.version_number.desc())
    ).all())
    version = versions[0] if versions else None
    if not version:
        snapshot = default_financial_policy_snapshot()
        version = FinancialPolicyVersion(
            financial_policy_id=policy.id,
            version_number=1,
            status="PUBLISHED",
            effective_from=utcnow(),
            immutable=True,
            change_reason="Initial standalone financial policy",
            policy_snapshot=snapshot,
            snapshot_hash=sha256_json(snapshot),
        )
        db.add(version)
        db.flush()
        policy.current_version_id = version.id
        versions = [version]
    elif not policy.current_version_id:
        policy.current_version_id = version.id

    # v2.4 makes every policy-controlled assumption explicit inside the
    # immutable snapshot. Older releases relied on code defaults for fields
    # introduced later, which made a historical policy depend on the deployed
    # adapter. On first upgrade, clone the active legacy policy into one fully
    # materialized v2.4 policy and archive the legacy versions. Historical runs
    # remain linked to their original rows, while all new runs use an explicit,
    # reproducible policy snapshot.
    v240_versions = [
        row for row in versions
        if str(((row.policy_snapshot or {}).get("portal_policy") or {}).get("schema_version") or "")
        == "financial-policy-controls-2.4.0"
    ]
    if not v240_versions:
        source = db.get(FinancialPolicyVersion, policy.current_version_id) or version
        source_controls = deepcopy(((source.policy_snapshot or {}).get("portal_policy") or {}))
        source_controls.pop("schema_version", None)
        source_controls.setdefault("display_name_ar", "السياسة المالية القياسية المحدثة")
        source_controls.setdefault("display_name_en", "Updated Standard Financial Policy")
        source_controls.setdefault(
            "description_ar",
            "نسخة سياسة محدثة تفصل النقطة المتوازنة عن السقف الفني وتثبت جميع الافتراضات داخل النسخة.",
        )
        source_controls.setdefault(
            "description_en",
            "Upgraded policy separating the balanced point from the technical ceiling and freezing all assumptions in-version.",
        )
        source_controls.setdefault("user_selectable", True)
        source_controls.setdefault("negotiation_recommendation_method", "POLICY_RANGE_POSITION")
        source_controls.setdefault("institutional_conservatism", "0.58")
        source_controls.setdefault("risk_adjusted_capacity_factor", "0.42")
        source_controls.setdefault("minimum_capacity_factor", "0.30")
        source_controls.setdefault("balanced_position_factor", "0.56")
        source_controls.setdefault("balanced_position_minimum", "0")
        source_controls.setdefault("balanced_position_maximum", "1")
        # Contract Engine 3.x defines Net Sales as revenue after sales-side
        # deductions only. Legacy development-cost categories are preserved in
        # old snapshots for audit, but are not carried into the upgraded policy.
        source_controls["net_sales_deductible_categories"] = []
        next_number = max(row.version_number for row in versions) + 1
        snapshot = apply_policy_controls(deepcopy(source.policy_snapshot), source_controls)
        snapshot["version"] = f"{PORTAL_FINANCIAL_ADAPTER_VERSION}-policy-{next_number}"
        upgraded = FinancialPolicyVersion(
            financial_policy_id=policy.id,
            version_number=next_number,
            status="PUBLISHED",
            effective_from=utcnow(),
            immutable=True,
            change_reason="Automatic v2.4 policy-schema upgrade; legacy versions preserved for historical audit",
            policy_snapshot=snapshot,
            snapshot_hash=sha256_json(snapshot),
        )
        db.add(upgraded)
        db.flush()
        for legacy in versions:
            if legacy.status == "PUBLISHED":
                legacy.status = "ARCHIVED"
        policy.current_version_id = upgraded.id
        version = upgraded
        versions.insert(0, upgraded)
    else:
        version = db.get(FinancialPolicyVersion, policy.current_version_id) or v240_versions[0]
    manifest = engine_registration_manifest()
    engine = db.scalar(
        select(EngineVersion).where(
            EngineVersion.code == "LANDVALUE360_MONTHLY_KERNEL",
            EngineVersion.engine_version == ENGINE_VERSION,
            EngineVersion.adapter_version == PORTAL_FINANCIAL_ADAPTER_VERSION,
            EngineVersion.source_hash == manifest["source_hash"],
        )
    )
    if not engine:
        for row in db.scalars(
            select(EngineVersion).where(
                EngineVersion.code == "LANDVALUE360_MONTHLY_KERNEL",
                EngineVersion.active.is_(True),
            )
        ).all():
            row.active = False
        engine = EngineVersion(
            code="LANDVALUE360_MONTHLY_KERNEL",
            engine_version=ENGINE_VERSION,
            adapter_version=PORTAL_FINANCIAL_ADAPTER_VERSION,
            source_hash=manifest["source_hash"],
            manifest=manifest,
            active=True,
        )
        db.add(engine)
        db.flush()
    return version, engine


def current_policy_version(db: Session) -> FinancialPolicyVersion:
    policy = db.scalar(select(FinancialPolicy).where(FinancialPolicy.code == PORTAL_POLICY_CODE, FinancialPolicy.active.is_(True)))
    if not policy:
        return seed_financial_defaults(db)[0]
    if policy.current_version_id:
        version = db.get(FinancialPolicyVersion, policy.current_version_id)
        if version and version.status == "PUBLISHED":
            return version
    version = db.scalar(
        select(FinancialPolicyVersion)
        .where(FinancialPolicyVersion.financial_policy_id == policy.id, FinancialPolicyVersion.status == "PUBLISHED")
        .order_by(FinancialPolicyVersion.version_number.desc())
    )
    if not version:
        return seed_financial_defaults(db)[0]
    policy.current_version_id = version.id
    db.flush()
    return version


def list_policy_versions(
    db: Session,
    *,
    include_nonselectable: bool = False,
) -> list[FinancialPolicyVersion]:
    current = current_policy_version(db)
    rows = list(db.scalars(
        select(FinancialPolicyVersion)
        .where(
            FinancialPolicyVersion.financial_policy_id == current.financial_policy_id,
            FinancialPolicyVersion.status == "PUBLISHED",
        )
        .order_by(FinancialPolicyVersion.version_number.desc())
    ).all())
    if include_nonselectable:
        return rows
    return [row for row in rows if bool(policy_controls(row.policy_snapshot).get("user_selectable", True))]


def resolve_policy_version(
    db: Session,
    version_id: str | None,
    *,
    allow_nonselectable: bool = False,
) -> FinancialPolicyVersion:
    current = current_policy_version(db)
    if not version_id:
        return current
    row = db.get(FinancialPolicyVersion, str(version_id))
    if (
        not row
        or row.financial_policy_id != current.financial_policy_id
        or row.status != "PUBLISHED"
    ):
        raise ValueError("Financial policy version is not available")
    if not allow_nonselectable and not bool(policy_controls(row.policy_snapshot).get("user_selectable", True)):
        raise PermissionError("The selected financial policy version is not available to standard users")
    return row


def activate_policy_version(db: Session, *, version: FinancialPolicyVersion, user: User) -> FinancialPolicyVersion:
    policy = db.get(FinancialPolicy, version.financial_policy_id)
    if not policy or version.status != "PUBLISHED":
        raise ValueError("Only a published financial policy version may be activated")
    policy.current_version_id = version.id
    policy.updated_by = user.id
    db.flush()
    return version



def set_policy_version_status(
    db: Session, *, version: FinancialPolicyVersion, status: str, user: User
) -> FinancialPolicyVersion:
    normalized = str(status or "").strip().upper()
    if normalized not in {"PUBLISHED", "ARCHIVED"}:
        raise ValueError("Financial policy status must be PUBLISHED or ARCHIVED")
    policy = db.get(FinancialPolicy, version.financial_policy_id)
    if not policy:
        raise ValueError("Financial policy not found")
    if normalized == "ARCHIVED" and policy.current_version_id == version.id:
        raise ValueError("The default financial policy version cannot be archived. Activate another version first.")
    version.status = normalized
    version.updated_by = user.id
    db.flush()
    return version

def active_engine_version(db: Session) -> EngineVersion:
    row = db.scalar(
        select(EngineVersion)
        .where(
            EngineVersion.code == "LANDVALUE360_MONTHLY_KERNEL",
            EngineVersion.active.is_(True),
        )
        .order_by(EngineVersion.created_at.desc())
    )
    if not row:
        return seed_financial_defaults(db)[1]
    return row


def create_policy_version(
    db: Session,
    *,
    controls: dict[str, Any],
    user: User,
    change_reason: str,
    source_version: FinancialPolicyVersion | None = None,
    activate: bool = True,
) -> FinancialPolicyVersion:
    policy = db.scalar(select(FinancialPolicy).where(FinancialPolicy.code == PORTAL_POLICY_CODE))
    if not policy:
        seed_financial_defaults(db)
        policy = db.scalar(select(FinancialPolicy).where(FinancialPolicy.code == PORTAL_POLICY_CODE))
    current = current_policy_version(db)
    source_version = source_version or current
    if source_version.financial_policy_id != policy.id:
        raise ValueError("The source policy version belongs to another policy")
    next_version = (db.scalar(select(func.max(FinancialPolicyVersion.version_number)).where(FinancialPolicyVersion.financial_policy_id == policy.id)) or 0) + 1
    snapshot = apply_policy_controls(deepcopy(source_version.policy_snapshot), controls)
    snapshot["version"] = f"{PORTAL_FINANCIAL_ADAPTER_VERSION}-policy-{next_version}"
    row = FinancialPolicyVersion(
        financial_policy_id=policy.id,
        version_number=next_version,
        status="PUBLISHED",
        effective_from=utcnow(),
        immutable=True,
        change_reason=change_reason,
        policy_snapshot=snapshot,
        snapshot_hash=sha256_json(snapshot),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(row)
    db.flush()
    if activate:
        policy.current_version_id = row.id
        policy.updated_by = user.id
    db.flush()
    return row


def update_financial_model(
    db: Session,
    *,
    version: ProjectVersion,
    payload: dict[str, Any],
    user: User,
    policy_version: FinancialPolicyVersion | None = None,
) -> dict[str, Any]:
    if version.immutable:
        raise ValueError("Submitted project versions are immutable. Create a revision before editing financial assumptions.")
    snapshot = deepcopy(version.input_snapshot or {})
    controls = policy_controls((policy_version or current_policy_version(db)).policy_snapshot)
    normalized = normalize_financial_model(payload, planning=snapshot.get("planning") or {}, controls=controls)
    snapshot["financial_model"] = normalized
    version.input_snapshot = snapshot
    version.snapshot_hash = sha256_json(snapshot)
    version.updated_by = user.id
    db.flush()
    return normalized


def _cashflow_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = date.fromisoformat(str(value))
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


def validate_executable_engine_version(engine_version: EngineVersion) -> None:
    current_hash = engine_source_hash()
    if (
        not engine_version.active
        or engine_version.engine_version != ENGINE_VERSION
        or engine_version.adapter_version != PORTAL_FINANCIAL_ADAPTER_VERSION
        or engine_version.source_hash != current_hash
    ):
        raise ValueError(
            "The selected Engine Version is not executable by this deployed portal build. "
            "Activate a version whose engine, adapter and source hash match the installed calculation core."
        )


def execute_calculation_run(
    db: Session,
    *,
    project: Project,
    version: ProjectVersion,
    user: User,
    policy_version: FinancialPolicyVersion | None = None,
    engine_version: EngineVersion | None = None,
) -> CalculationRun:
    policy_version = policy_version or current_policy_version(db)
    engine_version = engine_version or active_engine_version(db)
    validate_executable_engine_version(engine_version)
    effective_project_snapshot = effective_project_input_snapshot(version, policy_version.policy_snapshot)
    input_snapshot = {
        "project_id": project.id,
        "project_version_id": version.id,
        "source_project_snapshot_hash": version.snapshot_hash,
        "project_snapshot": effective_project_snapshot,
        "policy_version_id": policy_version.id,
        "policy_snapshot": deepcopy(policy_version.policy_snapshot),
        "engine_version_id": engine_version.id,
        "engine_manifest": deepcopy(engine_version.manifest),
    }
    input_hash = sha256_json(input_snapshot)
    currency = ((version.input_snapshot or {}).get("identity") or {}).get("currency") or "USD"
    run = CalculationRun(
        project_id=project.id,
        project_version_id=version.id,
        financial_policy_version_id=policy_version.id,
        engine_version_id=engine_version.id,
        status="RUNNING",
        run_type="BASE_CASE",
        currency=currency,
        input_snapshot=json_ready(input_snapshot),
        input_hash=input_hash,
        executed_by=user.id,
        started_at=utcnow(),
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(run)
    db.flush()
    started = perf_counter()
    try:
        output = run_financial_model(
            project, version, policy_version.policy_snapshot, source_snapshot=effective_project_snapshot
        )
        truth = output["financial_truth"]
        summary = output["summary"]
        selected = output.get("selected_contract") or {}
        run.status = "COMPLETED"
        run.completed_at = utcnow()
        run.duration_ms = int((perf_counter() - started) * 1000)
        run.result_hash = output["result_hash"]
        run.selected_contract_method = str(selected.get("method") or summary.get("method") or "") or None
        result_row = CalculationRunResult(
            calculation_run_id=run.id,
            calculation_status=str(truth.get("calculation_status") or summary.get("calculation_status") or "FAIL"),
            policy_compliant=bool(truth.get("policy_compliant")),
            reconciliation_passed=bool(truth.get("cash_reconciliation_passed")),
            summary=output["summary"],
            financial_truth=truth,
            residual_valuation=output["residual_valuation"],
            annual_cashflow=output["annual_cashflow"],
            selected_contract=selected,
            constraints=output.get("constraints") or [],
            full_result=output,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(result_row)
        for row in output.get("monthly_cashflow") or []:
            db.add(MonthlyCashflowSnapshot(
                calculation_run_id=run.id,
                month_number=int(row.get("month") or 0),
                cashflow_date=_cashflow_datetime(row.get("date")),
                opening_cash=D(row.get("opening_cash")),
                gross_contracted_sales=D(row.get("gross_contracted_sales")),
                gross_collections=D(row.get("gross_collections")),
                net_collections=D(row.get("net_collections")),
                planned_cost=D(row.get("planned_cost")),
                actual_cost=D(row.get("actual_cost")),
                deferred_cost=D(row.get("deferred_cost")),
                equity_contribution=D(row.get("equity_contribution")),
                financing_draw=D(row.get("financing_draw")),
                interest_paid=D(row.get("interest_paid")),
                financing_fees=D(row.get("financing_fees")),
                financing_repayment=D(row.get("financing_repayment")),
                landowner_payment=D(row.get("landowner_cash_receipt", row.get("government_payment"))),
                developer_distribution=D(row.get("developer_distribution")),
                ending_cash=D(row.get("ending_cash")),
                ending_debt=D(row.get("ending_debt")),
                funding_gap=D(row.get("unsupported_funding_gap")),
                contractual_arrears=D(row.get("government_payment_arrears", row.get("contractual_arrears"))),
                cash_balance_variance=D(row.get("cash_balance_variance")),
                data=row,
                created_by=user.id,
                updated_by=user.id,
            ))
        for row in output.get("negotiation_results") or []:
            db.add(NegotiationResult(
                calculation_run_id=run.id,
                method=str(row.get("method") or ""),
                status=str(row.get("status") or "UNKNOWN"),
                measure_type=str(row.get("measure_type") or "RATE"),
                fair_floor=D(row.get("fair_floor")) if row.get("fair_floor") not in (None, "") else None,
                balanced=D(row.get("balanced", row.get("recommended"))) if row.get("balanced", row.get("recommended")) not in (None, "") else None,
                technical_ceiling=D(row.get("technical_ceiling")) if row.get("technical_ceiling") not in (None, "") else None,
                negotiation_minimum=D(row.get("negotiation_minimum", row.get("fair_floor"))) if row.get("negotiation_minimum", row.get("fair_floor")) not in (None, "") else None,
                negotiation_maximum=D(row.get("negotiation_maximum", row.get("technical_ceiling"))) if row.get("negotiation_maximum", row.get("technical_ceiling")) not in (None, "") else None,
                governing_constraint_id=row.get("governing_constraint_id"),
                recommendation_rank=row.get("recommendation_rank"),
                result_snapshot=row,
                created_by=user.id,
                updated_by=user.id,
            ))
        db.flush()
        return run
    except Exception as exc:
        run.status = "FAILED"
        run.completed_at = utcnow()
        run.duration_ms = int((perf_counter() - started) * 1000)
        run.error_message = str(exc)[:8000]
        run.updated_by = user.id
        db.flush()
        raise


def get_run_result(db: Session, run_id: str) -> CalculationRunResult | None:
    return db.scalar(select(CalculationRunResult).where(CalculationRunResult.calculation_run_id == run_id))


def run_payload(db: Session, run: CalculationRun, *, include_monthly: bool = False, include_full: bool = False) -> dict[str, Any]:
    result = get_run_result(db, run.id)
    project_version = db.get(ProjectVersion, run.project_version_id)
    policy_version = db.get(FinancialPolicyVersion, run.financial_policy_version_id)
    engine_version = db.get(EngineVersion, run.engine_version_id)
    frozen_input = run.input_snapshot or {}
    frozen_project_snapshot = frozen_input.get("project_snapshot") or {}
    source_project_snapshot_hash = frozen_input.get("source_project_snapshot_hash")
    payload: dict[str, Any] = {
        "id": run.id,
        "project_id": run.project_id,
        "project_version_id": run.project_version_id,
        "financial_policy_version_id": run.financial_policy_version_id,
        "engine_version_id": run.engine_version_id,
        "status": run.status,
        "run_type": run.run_type,
        "currency": run.currency,
        "selected_contract_method": run.selected_contract_method,
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "executed_by": run.executed_by,
        "error_message": run.error_message,
        "project_version_number": project_version.version_number if project_version else None,
        "project_snapshot_hash": source_project_snapshot_hash,
        "source_project_snapshot_hash": source_project_snapshot_hash,
        "effective_project_input_hash": sha256_json(frozen_project_snapshot),
        "current_project_snapshot_hash": project_version.snapshot_hash if project_version else None,
        "project_input_snapshot_frozen": True,
        "financial_policy_version_number": policy_version.version_number if policy_version else None,
        "financial_policy_snapshot_hash": policy_version.snapshot_hash if policy_version else None,
        "financial_policy_status": policy_version.status if policy_version else None,
        "financial_policy_display_name_ar": policy_controls(policy_version.policy_snapshot).get("display_name_ar") if policy_version else None,
        "financial_policy_display_name_en": policy_controls(policy_version.policy_snapshot).get("display_name_en") if policy_version else None,
        "financial_policy_description_ar": policy_controls(policy_version.policy_snapshot).get("description_ar") if policy_version else None,
        "financial_policy_description_en": policy_controls(policy_version.policy_snapshot).get("description_en") if policy_version else None,
        "engine_version_label": engine_version.engine_version if engine_version else None,
        "engine_adapter_version": engine_version.adapter_version if engine_version else None,
        "engine_source_hash": engine_version.source_hash if engine_version else None,
    }
    if result:
        full_result = result.full_result or {}
        payload.update({
            "calculation_status": result.calculation_status,
            "policy_compliant": result.policy_compliant,
            "reconciliation_passed": result.reconciliation_passed,
            "summary": result.summary,
            "financial_truth": result.financial_truth,
            "residual_valuation": result.residual_valuation,
            "annual_cashflow": result.annual_cashflow,
            "selected_contract": result.selected_contract,
            "constraints": result.constraints,
            "financial_audit": full_result.get("financial_audit") or {},
            "recommendation_validation": full_result.get("recommendation_validation") or {},
            "financial_model": full_result.get("financial_model") or (frozen_project_snapshot.get("financial_model") or {}),
            "contract_engine_version": full_result.get("contract_engine_version"),
        })
        if include_full:
            payload["full_result"] = result.full_result
    payload["negotiation_results"] = [row.result_snapshot for row in db.scalars(
        select(NegotiationResult).where(NegotiationResult.calculation_run_id == run.id).order_by(NegotiationResult.recommendation_rank.asc().nullslast(), NegotiationResult.method)
    ).all()]
    if include_monthly:
        payload["monthly_cashflow"] = [row.data for row in db.scalars(
            select(MonthlyCashflowSnapshot).where(MonthlyCashflowSnapshot.calculation_run_id == run.id).order_by(MonthlyCashflowSnapshot.month_number)
        ).all()]
    return json_ready(payload)
