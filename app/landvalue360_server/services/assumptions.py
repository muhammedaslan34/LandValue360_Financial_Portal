"""Assumption register and review workflow."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..context import AuthContext
from ..errors import ConflictError, NotFoundError
from ..models import AssumptionRecord, EvidenceDocument, utc_now
from .evidence import get_evidence
from .tenant import get_project_version, tenant_clause


def list_assumptions(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
) -> list[AssumptionRecord]:
    version = get_project_version(session, context, project_version_id)
    return list(
        session.scalars(
            select(AssumptionRecord)
            .where(
                AssumptionRecord.project_version_id == version.id,
                *tenant_clause(AssumptionRecord, context),
            )
            .order_by(AssumptionRecord.category, AssumptionRecord.assumption_key)
        ).all()
    )


def get_assumption(session: Session, *, context: AuthContext, assumption_id: str) -> AssumptionRecord:
    record = session.scalar(
        select(AssumptionRecord).where(
            AssumptionRecord.id == assumption_id,
            *tenant_clause(AssumptionRecord, context),
        )
    )
    if record is None:
        raise NotFoundError("Assumption not found.")
    return record


def _validate_evidence_ids(
    session: Session,
    *,
    context: AuthContext,
    project_id: str,
    evidence_document_ids: list[str],
) -> None:
    for evidence_id in evidence_document_ids:
        evidence = get_evidence(session, context=context, evidence_id=evidence_id)
        if evidence.project_id != project_id:
            raise NotFoundError("Evidence document does not belong to the project.")


def create_assumption(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
    payload: dict[str, Any],
) -> AssumptionRecord:
    version = get_project_version(session, context, project_version_id)
    evidence_ids = list(payload.get("evidence_document_ids") or [])
    _validate_evidence_ids(
        session,
        context=context,
        project_id=version.project_id,
        evidence_document_ids=evidence_ids,
    )
    record = AssumptionRecord(
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
        project_id=version.project_id,
        project_version_id=version.id,
        assumption_key=str(payload["assumption_key"]).strip(),
        label=str(payload["label"]).strip(),
        category=payload["category"],
        value_snapshot=deepcopy(payload["value_snapshot"]),
        unit=payload.get("unit"),
        criticality=payload.get("criticality", "MEDIUM"),
        source_type=payload.get("source_type", "MANUAL"),
        source_reference=payload.get("source_reference"),
        evidence_document_ids=evidence_ids,
        evidence_status=payload.get("evidence_status", "MISSING"),
        confidence_score=int(payload.get("confidence_score", 0)),
        approval_status="DRAFT",
        notes=payload.get("notes"),
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="ASSUMPTION_CREATED",
        entity_type="AssumptionRecord",
        entity_id=record.id,
        after={
            "project_version_id": version.id,
            "assumption_key": record.assumption_key,
            "criticality": record.criticality,
            "evidence_status": record.evidence_status,
            "confidence_score": record.confidence_score,
        },
    )
    return record


def update_assumption(
    session: Session,
    *,
    context: AuthContext,
    assumption_id: str,
    changes: dict[str, Any],
) -> AssumptionRecord:
    record = get_assumption(session, context=context, assumption_id=assumption_id)
    if record.approval_status == "APPROVED":
        raise ConflictError(
            "APPROVED_ASSUMPTION_IMMUTABLE",
            "Approved assumptions are immutable; create a new project version or return the assumption to review.",
        )
    if "evidence_document_ids" in changes and changes["evidence_document_ids"] is not None:
        _validate_evidence_ids(
            session,
            context=context,
            project_id=record.project_id,
            evidence_document_ids=list(changes["evidence_document_ids"]),
        )
    before = {
        "label": record.label,
        "category": record.category,
        "value_snapshot": deepcopy(record.value_snapshot),
        "unit": record.unit,
        "criticality": record.criticality,
        "source_type": record.source_type,
        "source_reference": record.source_reference,
        "evidence_document_ids": list(record.evidence_document_ids or []),
        "evidence_status": record.evidence_status,
        "confidence_score": record.confidence_score,
        "notes": record.notes,
    }
    for key, value in changes.items():
        if value is not None or key in {"unit", "source_reference", "notes"}:
            setattr(record, key, deepcopy(value))
    session.flush()
    record_audit(
        session,
        context=context,
        action="ASSUMPTION_UPDATED",
        entity_type="AssumptionRecord",
        entity_id=record.id,
        before=before,
        after={key: deepcopy(getattr(record, key)) for key in before},
    )
    return record


def review_assumption(
    session: Session,
    *,
    context: AuthContext,
    assumption_id: str,
    approval_status: str,
    evidence_status: str | None,
    confidence_score: int | None,
    notes: str | None,
) -> AssumptionRecord:
    record = get_assumption(session, context=context, assumption_id=assumption_id)
    before = {
        "approval_status": record.approval_status,
        "evidence_status": record.evidence_status,
        "confidence_score": record.confidence_score,
        "notes": record.notes,
    }
    record.approval_status = approval_status
    if evidence_status is not None:
        record.evidence_status = evidence_status
    if confidence_score is not None:
        record.confidence_score = confidence_score
    if notes is not None:
        record.notes = notes
    record.reviewed_by_user_id = context.user_id
    record.reviewed_at = utc_now()
    session.flush()
    record_audit(
        session,
        context=context,
        action="ASSUMPTION_REVIEWED",
        entity_type="AssumptionRecord",
        entity_id=record.id,
        before=before,
        after={
            "approval_status": record.approval_status,
            "evidence_status": record.evidence_status,
            "confidence_score": record.confidence_score,
            "notes": record.notes,
            "reviewed_by_user_id": record.reviewed_by_user_id,
        },
    )
    return record


def _path_value(snapshot: dict[str, Any], path: str) -> Any:
    current: Any = snapshot
    for token in path.split("."):
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def seed_assumptions(
    session: Session,
    *,
    context: AuthContext,
    project_version_id: str,
) -> list[AssumptionRecord]:
    version = get_project_version(session, context, project_version_id)
    existing = {item.assumption_key for item in list_assumptions(session, context=context, project_version_id=version.id)}
    specifications: list[dict[str, Any]] = [
        {"key": "planning.gross_land_area_sqm", "label": "Gross land area", "category": "LAND", "unit": "sqm", "criticality": "CRITICAL"},
        {"key": "planning.far", "label": "Floor area ratio", "category": "PLANNING", "unit": "x", "criticality": "CRITICAL"},
        {"key": "planning.bcr", "label": "Building coverage ratio", "category": "PLANNING", "unit": "%", "criticality": "HIGH"},
        {"key": "land_value_baseline", "label": "Land value baseline", "category": "LAND", "unit": version.input_snapshot.get("reporting_currency", "USD"), "criticality": "HIGH"},
        {"key": "valuation_context.cost_estimate_class", "label": "Cost estimate class", "category": "COST", "unit": None, "criticality": "HIGH"},
        {"key": "valuation_context.design_maturity", "label": "Design maturity", "category": "PLANNING", "unit": None, "criticality": "HIGH"},
        {"key": "valuation_context.measurement_basis", "label": "Measurement basis", "category": "PLANNING", "unit": None, "criticality": "MEDIUM"},
        {"key": "valuation_context.valuation_standard", "label": "Valuation standards reference", "category": "OTHER", "unit": None, "criticality": "MEDIUM"},
    ]
    for index, item in enumerate(version.input_snapshot.get("planning_products") or []):
        specifications.append({
            "key": f"planning_products.{index}.efficiency",
            "label": f"{item.get('name') or item.get('product_id')} sellable efficiency",
            "category": "PLANNING",
            "unit": "%",
            "criticality": "HIGH",
            "value": item.get("efficiency"),
        })
    for index, item in enumerate(version.input_snapshot.get("products") or []):
        specifications.append({
            "key": f"products.{index}.unit_price",
            "label": f"{item.get('name') or item.get('product_id')} selling price",
            "category": "MARKET",
            "unit": f"{version.input_snapshot.get('reporting_currency', 'USD')}/{item.get('quantity_unit', 'unit')}",
            "criticality": "CRITICAL",
            "value": item.get("unit_price"),
        })
    for index, item in enumerate(version.input_snapshot.get("costs") or []):
        specifications.append({
            "key": f"costs.{index}.unit_cost",
            "label": f"{item.get('name') or item.get('cost_id')} unit cost",
            "category": "COST",
            "unit": version.input_snapshot.get("reporting_currency", "USD"),
            "criticality": "CRITICAL" if item.get("is_direct_cost") else "HIGH",
            "value": item.get("unit_cost"),
        })
    created: list[AssumptionRecord] = []
    for spec in specifications:
        if spec["key"] in existing:
            continue
        value = spec.get("value")
        if value is None and "." in spec["key"] and not any(token.isdigit() for token in spec["key"].split(".")):
            value = _path_value(version.input_snapshot, spec["key"])
        if value is None:
            value = version.input_snapshot.get(spec["key"])
        record = create_assumption(
            session,
            context=context,
            project_version_id=version.id,
            payload={
                "assumption_key": spec["key"],
                "label": spec["label"],
                "category": spec["category"],
                "value_snapshot": {"value": value},
                "unit": spec["unit"],
                "criticality": spec["criticality"],
                "source_type": "MANUAL",
                "evidence_document_ids": [],
                "evidence_status": "MISSING",
                "confidence_score": 0,
                "notes": "Seeded from the frozen project-version input snapshot; source evidence remains required.",
            },
        )
        created.append(record)
    record_audit(
        session,
        context=context,
        action="ASSUMPTION_REGISTER_SEEDED",
        entity_type="ProjectVersion",
        entity_id=version.id,
        metadata={"created_count": len(created)},
    )
    return list_assumptions(session, context=context, project_version_id=version.id)
