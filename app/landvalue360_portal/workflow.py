from __future__ import annotations

from datetime import timedelta
from .models import Project, ProjectStatusHistory, User, utcnow

TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"SUBMITTED", "CANCELLED"},
    "SUBMITTED": {"DATA_REVIEW", "CANCELLED"},
    "DATA_REVIEW": {"MISSING_INFORMATION", "READY_FOR_ANALYSIS", "CANCELLED"},
    "MISSING_INFORMATION": {"DATA_REVIEW", "CANCELLED"},
    "READY_FOR_ANALYSIS": {"IN_ANALYSIS", "MISSING_INFORMATION", "CANCELLED"},
    "IN_ANALYSIS": {"IN_REVIEW", "MISSING_INFORMATION", "CANCELLED"},
    "IN_REVIEW": {"IN_ANALYSIS", "REPORT_READY", "MISSING_INFORMATION", "CANCELLED"},
    "REPORT_READY": {"COMPLETED", "IN_REVIEW", "CANCELLED"},
    "COMPLETED": {"ARCHIVED"},
    "CANCELLED": {"ARCHIVED"},
    "ARCHIVED": set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def transition_project(db, project: Project, target: str, *, user: User, reason: str | None, sla_hours: int = 24) -> None:
    if not can_transition(project.status, target):
        raise ValueError(f"Transition {project.status} -> {target} is not allowed")
    old = project.status
    project.status = target
    now = utcnow()
    if target == "SUBMITTED":
        project.submitted_at = now
    if target == "READY_FOR_ANALYSIS":
        project.ready_for_analysis_at = now
        project.sla_due_at = now + timedelta(hours=sla_hours)
    if target == "MISSING_INFORMATION":
        project.sla_due_at = None
    if target == "COMPLETED":
        project.completed_at = now
    db.add(ProjectStatusHistory(
        project_id=project.id,
        from_status=old,
        to_status=target,
        reason=reason,
        changed_by=user.id,
        changed_at=now,
    ))
    project.updated_by = user.id
    db.flush()
