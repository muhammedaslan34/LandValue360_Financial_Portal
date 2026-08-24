from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...models import AuditEvent
from ...schemas import AuditEventOut
from ...services.tenant import require_tenant_context
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1/audit-events", tags=["Audit"])


@router.get("", response_model=list[AuditEventOut])
def list_audit_events(
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[AuditEvent]:
    if context.is_platform_admin and context.organization_id is None:
        statement = select(AuditEvent)
    else:
        organization_id, workspace_id = require_tenant_context(context)
        statement = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
        if workspace_id is not None:
            statement = statement.where(
                (AuditEvent.workspace_id == workspace_id) | (AuditEvent.workspace_id.is_(None))
            )
    if entity_type is not None:
        statement = statement.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        statement = statement.where(AuditEvent.entity_id == entity_id)
    if action is not None:
        statement = statement.where(AuditEvent.action == action)
    statement = statement.order_by(AuditEvent.occurred_at.desc()).offset(offset).limit(limit)
    return list(session.scalars(statement).all())
