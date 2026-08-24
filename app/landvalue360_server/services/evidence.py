"""Evidence-room storage and governed metadata workflows."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..config import Settings
from ..context import AuthContext
from ..errors import ConflictError, NotFoundError
from ..models import EvidenceDocument, ProjectVersion, utc_now
from .tenant import get_project, get_project_version, tenant_clause

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_ALLOWED_TYPES = {
    "TITLE", "PLANNING", "MARKET_STUDY", "COST_ESTIMATE", "LEGAL_OPINION",
    "INFRASTRUCTURE", "FINANCE", "ENVIRONMENT_SOCIAL", "MEASUREMENT", "OTHER",
}


def _safe_filename(value: str) -> str:
    name = Path(value or "evidence.bin").name.strip() or "evidence.bin"
    sanitized = _SAFE_FILENAME.sub("-", name).strip(".-") or "evidence.bin"
    return sanitized[:180]


def _storage_path(settings: Settings, record: EvidenceDocument) -> Path:
    root = settings.evidence_storage_path
    path = (root / record.storage_key).resolve()
    if root not in path.parents:
        raise ConflictError("EVIDENCE_STORAGE_INVALID", "Evidence storage path is invalid.")
    return path


def list_evidence(
    session: Session,
    *,
    context: AuthContext,
    project_id: str,
    project_version_id: str | None = None,
) -> list[EvidenceDocument]:
    get_project(session, context, project_id)
    statement = select(EvidenceDocument).where(
        EvidenceDocument.project_id == project_id,
        *tenant_clause(EvidenceDocument, context),
    )
    if project_version_id is not None:
        version = get_project_version(session, context, project_version_id)
        if version.project_id != project_id:
            raise NotFoundError("Project version not found for project.")
        statement = statement.where(
            (EvidenceDocument.project_version_id.is_(None))
            | (EvidenceDocument.project_version_id == project_version_id)
        )
    return list(session.scalars(statement.order_by(EvidenceDocument.created_at.desc())).all())


def get_evidence(session: Session, *, context: AuthContext, evidence_id: str) -> EvidenceDocument:
    record = session.scalar(
        select(EvidenceDocument).where(
            EvidenceDocument.id == evidence_id,
            *tenant_clause(EvidenceDocument, context),
        )
    )
    if record is None:
        raise NotFoundError("Evidence document not found.")
    return record


def create_evidence(
    session: Session,
    *,
    settings: Settings,
    context: AuthContext,
    project_id: str,
    project_version_id: str | None,
    evidence_type: str,
    title: str,
    original_filename: str,
    media_type: str,
    content: bytes,
    source_name: str | None = None,
    source_reference: str | None = None,
    issue_date: date | None = None,
    expiry_date: date | None = None,
    notes: str | None = None,
) -> EvidenceDocument:
    project = get_project(session, context, project_id)
    if evidence_type not in _ALLOWED_TYPES:
        raise ConflictError("EVIDENCE_TYPE_INVALID", "Unsupported evidence type.")
    if not content:
        raise ConflictError("EVIDENCE_EMPTY", "Evidence file cannot be empty.")
    if len(content) > settings.max_evidence_file_bytes:
        raise ConflictError(
            "EVIDENCE_FILE_TOO_LARGE",
            f"Evidence file exceeds the configured {settings.max_evidence_file_bytes} byte limit.",
        )
    if expiry_date is not None and issue_date is not None and expiry_date < issue_date:
        raise ConflictError("EVIDENCE_DATES_INVALID", "Expiry date cannot be before issue date.")
    version = None
    if project_version_id:
        version = get_project_version(session, context, project_version_id)
        if version.project_id != project.id:
            raise NotFoundError("Project version not found for project.")
    original = _safe_filename(original_filename)
    content_hash = sha256(content).hexdigest()
    stored = f"{uuid4().hex}-{original}"
    storage_key = f"{project.organization_id}/{project.workspace_id}/{project.id}/{stored}"
    record = EvidenceDocument(
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        project_version_id=version.id if version else None,
        evidence_type=evidence_type,
        title=title.strip(),
        original_filename=original,
        stored_filename=stored,
        storage_key=storage_key,
        media_type=(media_type or "application/octet-stream")[:160],
        size_bytes=len(content),
        content_hash=content_hash,
        status="UPLOADED",
        source_name=source_name,
        source_reference=source_reference,
        issue_date=issue_date,
        expiry_date=expiry_date,
        notes=notes,
        created_by_user_id=context.user_id,
    )
    path = _storage_path(settings, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(content)
    temp.replace(path)
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="EVIDENCE_UPLOADED",
        entity_type="EvidenceDocument",
        entity_id=record.id,
        after={
            "project_id": project.id,
            "project_version_id": record.project_version_id,
            "evidence_type": record.evidence_type,
            "title": record.title,
            "original_filename": record.original_filename,
            "size_bytes": record.size_bytes,
            "content_hash": record.content_hash,
            "status": record.status,
        },
    )
    return record


def update_evidence(
    session: Session,
    *,
    context: AuthContext,
    evidence_id: str,
    changes: dict,
) -> EvidenceDocument:
    record = get_evidence(session, context=context, evidence_id=evidence_id)
    before = {
        "title": record.title,
        "evidence_type": record.evidence_type,
        "source_name": record.source_name,
        "source_reference": record.source_reference,
        "issue_date": record.issue_date.isoformat() if record.issue_date else None,
        "expiry_date": record.expiry_date.isoformat() if record.expiry_date else None,
        "notes": record.notes,
        "project_version_id": record.project_version_id,
    }
    if "project_version_id" in changes and changes["project_version_id"]:
        version = get_project_version(session, context, changes["project_version_id"])
        if version.project_id != record.project_id:
            raise NotFoundError("Project version not found for project.")
    if changes.get("evidence_type") and changes["evidence_type"] not in _ALLOWED_TYPES:
        raise ConflictError("EVIDENCE_TYPE_INVALID", "Unsupported evidence type.")
    for key, value in changes.items():
        if value is not None or key in {"source_name", "source_reference", "issue_date", "expiry_date", "notes", "project_version_id"}:
            setattr(record, key, value)
    if record.expiry_date is not None and record.issue_date is not None and record.expiry_date < record.issue_date:
        raise ConflictError("EVIDENCE_DATES_INVALID", "Expiry date cannot be before issue date.")
    session.flush()
    record_audit(
        session,
        context=context,
        action="EVIDENCE_METADATA_UPDATED",
        entity_type="EvidenceDocument",
        entity_id=record.id,
        before=before,
        after={key: getattr(record, key) for key in before},
    )
    return record


def verify_evidence(
    session: Session,
    *,
    context: AuthContext,
    evidence_id: str,
    status: str,
    notes: str | None,
) -> EvidenceDocument:
    if status not in {"VERIFIED", "UNDER_REVIEW", "REJECTED", "ARCHIVED"}:
        raise ConflictError("EVIDENCE_STATUS_INVALID", "Unsupported evidence verification status.")
    record = get_evidence(session, context=context, evidence_id=evidence_id)
    before = {"status": record.status, "notes": record.notes}
    record.status = status
    if notes is not None:
        record.notes = notes
    if status == "VERIFIED":
        record.verified_by_user_id = context.user_id
        record.verified_at = utc_now()
    else:
        record.verified_by_user_id = None
        record.verified_at = None
    session.flush()
    record_audit(
        session,
        context=context,
        action="EVIDENCE_STATUS_CHANGED",
        entity_type="EvidenceDocument",
        entity_id=record.id,
        before=before,
        after={"status": record.status, "notes": record.notes, "verified_by_user_id": record.verified_by_user_id},
    )
    return record


def evidence_file_path(
    session: Session,
    *,
    settings: Settings,
    context: AuthContext,
    evidence_id: str,
) -> tuple[EvidenceDocument, Path]:
    record = get_evidence(session, context=context, evidence_id=evidence_id)
    path = _storage_path(settings, record)
    if not path.is_file():
        raise NotFoundError("Evidence file is missing from storage.")
    if sha256(path.read_bytes()).hexdigest() != record.content_hash:
        raise ConflictError("EVIDENCE_HASH_MISMATCH", "Evidence file integrity verification failed.")
    return record, path
