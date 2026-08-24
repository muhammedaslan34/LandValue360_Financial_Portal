from __future__ import annotations

from copy import deepcopy
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...context import AuthContext
from ...enums import Permission
from ...models import PolicyPack, PolicyPackVersion
from ...schemas import (
    PolicyPackCreate,
    PolicyPackOut,
    PolicyVersionClone,
    PolicyVersionCreate,
    PolicyVersionOut,
    PolicyVersionUpdate,
)
from ...services.policies import (
    clone_policy_version,
    create_policy_pack,
    create_policy_version,
    publish_policy_version,
    update_policy_version,
)
from ...web_defaults import default_policy_snapshot, default_valuation_policy_snapshot
from ...services.tenant import (
    get_policy_pack,
    get_policy_version,
    require_tenant_context,
)
from ..dependencies import get_session, require_permission

router = APIRouter(prefix="/api/v1", tags=["Institutional policy packs"])


@router.get("/policy-templates/{policy_type}")
def read_policy_template(
    policy_type: Literal["PROJECT", "VALUATION"],
    _context: AuthContext = Depends(require_permission(Permission.POLICY_READ)),
) -> dict:
    """Return the canonical template for a new policy family.

    New policy packs must never inherit a snapshot from a different family.
    The browser may still clone a selected policy of the same family, but uses
    this endpoint whenever the requested family differs.
    """

    source = default_valuation_policy_snapshot() if policy_type == "VALUATION" else default_policy_snapshot()
    template = deepcopy(source)
    template["policy_id"] = "NEW-POLICY"
    template["version"] = "1.0.0-draft"
    return template


@router.get("/policy-packs", response_model=list[PolicyPackOut])
def list_policy_packs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    context: AuthContext = Depends(require_permission(Permission.POLICY_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[PolicyPack]:
    organization_id, workspace_id = require_tenant_context(context)
    statement = select(PolicyPack).where(PolicyPack.organization_id == organization_id)
    if workspace_id is not None:
        statement = statement.where(
            or_(PolicyPack.workspace_id.is_(None), PolicyPack.workspace_id == workspace_id)
        )
    statement = statement.order_by(PolicyPack.name).offset(offset).limit(limit)
    return list(session.scalars(statement).all())


@router.post("/policy-packs", response_model=PolicyPackOut, status_code=201)
def post_policy_pack(
    payload: PolicyPackCreate,
    context: AuthContext = Depends(require_permission(Permission.POLICY_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPack:
    return create_policy_pack(
        session,
        context=context,
        name=payload.name,
        code=payload.code,
        description=payload.description,
        workspace_id=payload.workspace_id,
    )


@router.get("/policy-packs/{policy_pack_id}", response_model=PolicyPackOut)
def read_policy_pack(
    policy_pack_id: str,
    context: AuthContext = Depends(require_permission(Permission.POLICY_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPack:
    return get_policy_pack(session, context, policy_pack_id)


@router.get("/policy-packs/{policy_pack_id}/versions", response_model=list[PolicyVersionOut])
def list_policy_versions(
    policy_pack_id: str,
    context: AuthContext = Depends(require_permission(Permission.POLICY_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> list[PolicyPackVersion]:
    pack = get_policy_pack(session, context, policy_pack_id)
    statement = (
        select(PolicyPackVersion)
        .where(PolicyPackVersion.policy_pack_id == pack.id)
        .order_by(PolicyPackVersion.version_number.desc())
    )
    return list(session.scalars(statement).all())


@router.post(
    "/policy-packs/{policy_pack_id}/versions",
    response_model=PolicyVersionOut,
    status_code=201,
)
def post_policy_version(
    policy_pack_id: str,
    payload: PolicyVersionCreate,
    context: AuthContext = Depends(require_permission(Permission.POLICY_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPackVersion:
    return create_policy_version(
        session,
        context=context,
        policy_pack_id=policy_pack_id,
        version_label=payload.version_label,
        policy_snapshot=payload.policy_snapshot,
        notes=payload.notes,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        supersedes_version_id=payload.supersedes_version_id,
    )


@router.get("/policy-versions/{version_id}", response_model=PolicyVersionOut)
def read_policy_version(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.POLICY_READ)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPackVersion:
    return get_policy_version(session, context, version_id)


@router.patch("/policy-versions/{version_id}", response_model=PolicyVersionOut)
def patch_policy_version(
    version_id: str,
    payload: PolicyVersionUpdate,
    context: AuthContext = Depends(require_permission(Permission.POLICY_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPackVersion:
    return update_policy_version(
        session,
        context=context,
        version_id=version_id,
        changes=payload.model_dump(exclude_unset=True),
    )


@router.post("/policy-versions/{version_id}/publish", response_model=PolicyVersionOut)
def post_publish_policy_version(
    version_id: str,
    context: AuthContext = Depends(require_permission(Permission.POLICY_PUBLISH)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPackVersion:
    return publish_policy_version(session, context=context, version_id=version_id)


@router.post(
    "/policy-versions/{version_id}/clone",
    response_model=PolicyVersionOut,
    status_code=201,
)
def post_clone_policy_version(
    version_id: str,
    payload: PolicyVersionClone,
    context: AuthContext = Depends(require_permission(Permission.POLICY_WRITE)),
    session: Session = Depends(get_session, scope="function"),
) -> PolicyPackVersion:
    return clone_policy_version(
        session,
        context=context,
        version_id=version_id,
        version_label=payload.version_label,
        notes=payload.notes,
    )
