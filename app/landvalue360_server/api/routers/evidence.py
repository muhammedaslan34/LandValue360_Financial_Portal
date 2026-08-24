from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...schemas import (
    AssumptionCreate,
    AssumptionRecordOut,
    AssumptionReview,
    AssumptionUpdate,
    EvidenceDocumentOut,
    EvidenceMetadataUpdate,
    EvidenceVerification,
)
from ...services.assumptions import (
    create_assumption,
    get_assumption,
    list_assumptions,
    review_assumption,
    seed_assumptions,
    update_assumption,
)
from ...request_limits import read_limited_body
from ...services.evidence import (
    create_evidence,
    evidence_file_path,
    get_evidence,
    list_evidence,
    update_evidence,
    verify_evidence,
)
from ..dependencies import get_current_context, get_session, require_permission

router = APIRouter(prefix="/api/v1", tags=["Evidence and assumptions"])


@router.get("/projects/{project_id}/evidence", response_model=list[EvidenceDocumentOut])
def get_project_evidence(
    project_id: str,
    project_version_id: str | None = None,
    context: AuthContext = Depends(require_permission(Permission.EVIDENCE_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return list_evidence(
        session,
        context=context,
        project_id=project_id,
        project_version_id=project_version_id,
    )


@router.post("/projects/{project_id}/evidence", response_model=EvidenceDocumentOut, status_code=201)
async def post_project_evidence(
    project_id: str,
    request: Request,
    evidence_type: str = Query(..., max_length=64),
    title: str = Query(..., min_length=1, max_length=240),
    project_version_id: str | None = Query(default=None),
    source_name: str | None = Query(default=None, max_length=240),
    source_reference: str | None = Query(default=None, max_length=1000),
    issue_date: date | None = Query(default=None),
    expiry_date: date | None = Query(default=None),
    notes: str | None = Query(default=None, max_length=20_000),
    x_filename: str = Header(default="evidence.bin", alias="X-Filename"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
    context: AuthContext = Depends(require_permission(Permission.EVIDENCE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
):
    settings = request.app.state.settings
    payload = await read_limited_body(
        request,
        max_bytes=settings.max_evidence_file_bytes,
        error_code="EVIDENCE_FILE_TOO_LARGE",
        error_message="The evidence file exceeds the configured size limit.",
    )
    return create_evidence(
        session,
        settings=settings,
        context=context,
        project_id=project_id,
        project_version_id=project_version_id,
        evidence_type=evidence_type,
        title=title,
        original_filename=x_filename,
        media_type=content_type or "application/octet-stream",
        content=payload,
        source_name=source_name,
        source_reference=source_reference,
        issue_date=issue_date,
        expiry_date=expiry_date,
        notes=notes,
    )


@router.get("/evidence/{evidence_id}", response_model=EvidenceDocumentOut)
def read_evidence(
    evidence_id: str,
    context: AuthContext = Depends(require_permission(Permission.EVIDENCE_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return get_evidence(session, context=context, evidence_id=evidence_id)


@router.patch("/evidence/{evidence_id}", response_model=EvidenceDocumentOut)
def patch_evidence(
    evidence_id: str,
    payload: EvidenceMetadataUpdate,
    context: AuthContext = Depends(require_permission(Permission.EVIDENCE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
):
    return update_evidence(
        session,
        context=context,
        evidence_id=evidence_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post("/evidence/{evidence_id}/verify", response_model=EvidenceDocumentOut)
def post_verify_evidence(
    evidence_id: str,
    payload: EvidenceVerification,
    context: AuthContext = Depends(require_permission(Permission.EVIDENCE_VERIFY)),
    session: Session = Depends(get_session, scope="function"),
):
    return verify_evidence(
        session,
        context=context,
        evidence_id=evidence_id,
        status=payload.status,
        notes=payload.notes,
    )


@router.get("/evidence/{evidence_id}/download")
def download_evidence(
    evidence_id: str,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.EVIDENCE_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    record, path = evidence_file_path(
        session,
        settings=request.app.state.settings,
        context=context,
        evidence_id=evidence_id,
    )
    return FileResponse(
        path,
        media_type=record.media_type,
        filename=record.original_filename,
        headers={"ETag": record.content_hash, "Cache-Control": "private, no-store"},
    )


@router.get("/project-versions/{version_id}/assumptions", response_model=list[AssumptionRecordOut])
def get_assumption_register(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.ASSUMPTION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return list_assumptions(session, context=context, project_version_id=version_id)


@router.post("/project-versions/{version_id}/assumptions", response_model=AssumptionRecordOut, status_code=201)
def post_assumption(
    version_id: str,
    payload: AssumptionCreate,
    context: AuthContext = Depends(require_permission(Permission.ASSUMPTION_WRITE)),
    session: Session = Depends(get_session, scope="function"),
):
    return create_assumption(
        session,
        context=context,
        project_version_id=version_id,
        payload=payload.model_dump(),
    )


@router.post("/project-versions/{version_id}/assumptions/seed", response_model=list[AssumptionRecordOut])
def post_seed_assumptions(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.ASSUMPTION_WRITE)),
    session: Session = Depends(get_session, scope="function"),
):
    return seed_assumptions(session, context=context, project_version_id=version_id)


@router.get("/assumptions/{assumption_id}", response_model=AssumptionRecordOut)
def read_assumption(
    assumption_id: str,
    context: AuthContext = Depends(require_permission(Permission.ASSUMPTION_READ)),
    session: Session = Depends(get_session, scope="function"),
):
    return get_assumption(session, context=context, assumption_id=assumption_id)


@router.patch("/assumptions/{assumption_id}", response_model=AssumptionRecordOut)
def patch_assumption(
    assumption_id: str,
    payload: AssumptionUpdate,
    context: AuthContext = Depends(require_permission(Permission.ASSUMPTION_WRITE)),
    session: Session = Depends(get_session, scope="function"),
):
    return update_assumption(
        session,
        context=context,
        assumption_id=assumption_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post("/assumptions/{assumption_id}/review", response_model=AssumptionRecordOut)
def post_review_assumption(
    assumption_id: str,
    payload: AssumptionReview,
    context: AuthContext = Depends(require_permission(Permission.ASSUMPTION_REVIEW)),
    session: Session = Depends(get_session, scope="function"),
):
    return review_assumption(
        session,
        context=context,
        assumption_id=assumption_id,
        approval_status=payload.approval_status,
        evidence_status=payload.evidence_status,
        confidence_score=payload.confidence_score,
        notes=payload.notes,
    )
