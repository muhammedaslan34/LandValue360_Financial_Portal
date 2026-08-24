"""Append-only audit-event helper."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .context import AuthContext
from .json_tools import sanitize_audit_value
from .models import AuditEvent


def record_audit(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    context: AuthContext | None = None,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    before: Any = None,
    after: Any = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=context.organization_id if context else organization_id,
        workspace_id=context.workspace_id if context else workspace_id,
        actor_user_id=context.user_id if context else None,
        request_id=context.request_id if context else None,
        edition_scope=context.edition_scope if context else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=sanitize_audit_value(before) if before is not None else None,
        after_state=sanitize_audit_value(after) if after is not None else None,
        event_metadata=sanitize_audit_value(metadata) if metadata else None,
    )
    session.add(event)
    return event
