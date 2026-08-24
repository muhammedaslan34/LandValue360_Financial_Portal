"""Landowner API routes (legacy internal module name retained for compatibility)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from landvalue360_government.manifest import REPORT_REGISTRY_VERSION, platform_manifest
from landvalue360_government.registries import registry_snapshot
from landvalue360_government.reports import REPORT_PURPOSES, REPORT_TYPES

from ...context import AuthContext
from ...enums import Permission
from ...models import GovernmentCase, OverrideRecord
from ...errors import ConflictError
from ...report_readiness import government_report_readiness
from ...schemas import (
    GovernmentApprovalRequest,
    GovernmentCaseCreate,
    GovernmentCaseOut,
    GovernmentCaseSummaryOut,
    GovernmentCaseUpdate,
    GovernmentOverrideCreate,
    GovernmentOverrideOut,
    GovernmentReviewRequest,
)
from ...services.government import (
    approve_government_case,
    create_government_case,
    create_override,
    finalize_government_case,
    get_government_case,
    government_options,
    government_report,
    government_report_pdf,
    legal_review_government_case,
    list_government_cases,
    list_overrides,
    official_government_result,
    preview_government_case,
    submit_government_case,
    technical_review_government_case,
    update_government_case,
)
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1/government", tags=["Landowner"])


@router.get("/manifest")
def read_government_manifest(
    _context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
) -> dict:
    return platform_manifest()


@router.get("/registries")
def read_government_registries(
    _context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
) -> dict:
    return registry_snapshot()


@router.get("/options")
def read_government_options(
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    return government_options(
        session,
        context=context,
        workflow_mode=request.app.state.settings.government_workflow_mode,
    )


@router.get("/cases", response_model=list[GovernmentCaseSummaryOut])
def get_government_cases(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[GovernmentCase]:
    return list_government_cases(session, context=context, status=status, limit=limit, offset=offset)


@router.post("/cases", response_model=GovernmentCaseOut, status_code=201)
def post_government_case(
    payload: GovernmentCaseCreate,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return create_government_case(session, context=context, **payload.model_dump())


@router.get("/cases/{case_id}", response_model=GovernmentCaseOut)
def read_government_case(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return get_government_case(session, context, case_id)


@router.patch("/cases/{case_id}", response_model=GovernmentCaseOut)
def patch_government_case(
    case_id: str,
    payload: GovernmentCaseUpdate,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return update_government_case(
        session,
        context=context,
        case_id=case_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post("/cases/{case_id}/overrides", response_model=GovernmentOverrideOut, status_code=201)
def post_government_override(
    case_id: str,
    payload: GovernmentOverrideCreate,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_OVERRIDE)),
    session: Session = Depends(get_session, scope="function"),
) -> OverrideRecord:
    _case, override = create_override(session, context=context, case_id=case_id, **payload.model_dump())
    return override


@router.get("/cases/{case_id}/overrides", response_model=list[GovernmentOverrideOut])
def get_government_overrides(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[OverrideRecord]:
    return list_overrides(session, context=context, case_id=case_id)


@router.post("/cases/{case_id}/preview")
def post_government_preview(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    return preview_government_case(session, context=context, case_id=case_id)


@router.post("/cases/{case_id}/submit", response_model=GovernmentCaseOut)
def post_government_submit(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_SUBMIT)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return submit_government_case(session, context=context, case_id=case_id)


@router.post("/cases/{case_id}/finalize", response_model=GovernmentCaseOut)
def post_government_finalize(
    case_id: str,
    payload: GovernmentApprovalRequest,
    request: Request,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return finalize_government_case(
        session,
        context=context,
        case_id=case_id,
        notes=payload.notes,
        workflow_mode=request.app.state.settings.government_workflow_mode,
    )


@router.post("/cases/{case_id}/technical-review", response_model=GovernmentCaseOut)
def post_government_technical_review(
    case_id: str,
    payload: GovernmentReviewRequest,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_TECHNICAL_REVIEW)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return technical_review_government_case(session, context=context, case_id=case_id, notes=payload.notes)


@router.post("/cases/{case_id}/legal-review", response_model=GovernmentCaseOut)
def post_government_legal_review(
    case_id: str,
    payload: GovernmentReviewRequest,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_LEGAL_REVIEW)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return legal_review_government_case(session, context=context, case_id=case_id, notes=payload.notes)


@router.post("/cases/{case_id}/approve", response_model=GovernmentCaseOut)
def post_government_approve(
    case_id: str,
    payload: GovernmentApprovalRequest,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_APPROVE)),
    session: Session = Depends(get_session, scope="function"),
) -> GovernmentCase:
    return approve_government_case(session, context=context, case_id=case_id, notes=payload.notes)


@router.post("/cases/{case_id}/run")
def post_government_official_run(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_RUN)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    return official_government_result(session, context=context, case_id=case_id)


@router.get("/cases/{case_id}/reports")
def get_government_reports(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    record = get_government_case(session, context, case_id)
    return {
        "case_id": record.id,
        "status": record.status,
        "calculation_run_id": record.calculation_run_id,
        "input_hash": record.input_hash,
        "output_hash": record.output_hash,
        "ledger_hash": record.ledger_hash,
        "reports": [
            {
                "type": key,
                "title_ar": titles[0],
                "title_en": titles[1],
                "purpose_ar": REPORT_PURPOSES[key][0],
                "purpose_en": REPORT_PURPOSES[key][1],
                "en_url": f"/api/v1/government/cases/{record.id}/reports/{key}.html?language=en",
                "ar_url": f"/api/v1/government/cases/{record.id}/reports/{key}.html?language=ar",
                "download_en_url": f"/api/v1/government/cases/{record.id}/reports/{key}.pdf?language=en",
                "download_ar_url": f"/api/v1/government/cases/{record.id}/reports/{key}.pdf?language=ar",
                "pdf_en_url": f"/api/v1/government/cases/{record.id}/reports/{key}.pdf?language=en",
                "pdf_ar_url": f"/api/v1/government/cases/{record.id}/reports/{key}.pdf?language=ar",
            }
            for key, titles in REPORT_TYPES.items()
        ],
    }


@router.get("/cases/{case_id}/report-readiness")
def read_government_report_readiness(
    case_id: str,
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> dict:
    record = get_government_case(session, context, case_id)
    return government_report_readiness(session, record)


def _require_government_report_ready(session: Session, record: GovernmentCase, *, official: bool) -> dict:
    readiness = government_report_readiness(session, record)
    if official and not readiness["ready"]:
        codes = ", ".join(row["code"] for row in readiness["failures"])
        raise ConflictError(
            "OFFICIAL_REPORT_NOT_READY",
            f"An official report cannot be issued until the readiness gate passes. Failed checks: {codes}.",
        )
    return readiness


@router.get("/cases/{case_id}/reports/{report_type}.html", response_class=HTMLResponse)
def read_government_report(
    case_id: str,
    report_type: str,
    language: str = Query(default="en", pattern="^(en|ar)$"),
    download: bool = Query(default=False),
    official: bool = Query(default=False),
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> HTMLResponse:
    record = get_government_case(session, context, case_id)
    readiness = _require_government_report_ready(session, record, official=official)
    html = government_report(
        session,
        context=context,
        case_id=case_id,
        report_type=report_type,
        language=language,
    )
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-LV360-Report-Registry": REPORT_REGISTRY_VERSION,
        "X-LV360-Report-Readiness": readiness["status"],
        "X-LV360-Report-Class": "OFFICIAL" if official else "ADVISORY",
    }
    if download:
        suffix = "ar" if language == "ar" else "en"
        safe_type = "".join(ch for ch in report_type if ch.isalnum() or ch in "-_")
        headers["Content-Disposition"] = f'attachment; filename="{safe_type}-{suffix}.html"'
    return HTMLResponse(content=html, headers=headers)


@router.get("/cases/{case_id}/reports/{report_type}.pdf")
def read_government_report_pdf(
    case_id: str,
    report_type: str,
    language: str = Query(default="en", pattern="^(en|ar)$"),
    official: bool = Query(default=False),
    context: AuthContext = Depends(require_permission(Permission.GOVERNMENT_CASE_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> Response:
    record = get_government_case(session, context, case_id)
    readiness = _require_government_report_ready(session, record, official=official)
    pdf = government_report_pdf(
        session, context=context, case_id=case_id, report_type=report_type, language=language
    )
    suffix = "ar" if language == "ar" else "en"
    safe_type = "".join(ch for ch in report_type if ch.isalnum() or ch in "-_")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-LV360-Report-Registry": REPORT_REGISTRY_VERSION,
            "X-LV360-Report-Readiness": readiness["status"],
            "X-LV360-Report-Class": "OFFICIAL" if official else "ADVISORY",
            "Content-Disposition": f'attachment; filename="{safe_type}-{suffix}.pdf"',
        },
    )
