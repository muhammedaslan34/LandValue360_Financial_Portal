"""Effective-dated institutional policy-pack services."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..context import AuthContext
from ..errors import ConflictError, NotFoundError
from ..json_tools import sha256_json
from ..models import PolicyPack, PolicyPackVersion, utc_now
from .tenant import get_policy_pack, get_policy_version, get_workspace, require_tenant_context

POLICY_PRODUCT_SCOPES = {"BOTH", "DEVELOPER", "LANDOWNER"}
POLICY_TYPES = {"PROJECT", "VALUATION"}


def _deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _canonical_policy_template(kind: str) -> dict:
    # Local import avoids a module cycle during application bootstrap.
    from ..web_defaults import default_policy_snapshot, default_valuation_policy_snapshot

    return (
        default_valuation_policy_snapshot()
        if kind == "VALUATION"
        else default_policy_snapshot()
    )


def _fraction_value(source: dict, path: tuple[str, ...], *, default: str = "0") -> Decimal:
    value: object = source
    for key in path:
        if not isinstance(value, dict):
            value = None
            break
        value = value.get(key)
    try:
        return Decimal(str(default if value in (None, "") else value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConflictError("POLICY_VALUE_INVALID", f"Invalid policy value at {'.'.join(path)}.") from exc


def _path_value(source: dict, path: tuple[str, ...]):
    value: object = source
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _require_explicit_policy_inputs(snapshot: dict, kind: str, *, effective_from) -> None:
    """Reject incomplete API payloads instead of filling governed values silently.

    The administration UI starts from a family-specific template, so a normal
    create/update submits all of these values.  This boundary check protects
    API callers and migrated drafts from acquiring materially different
    assumptions merely because a newer application template has defaults.
    """

    if kind == "VALUATION":
        required_paths = (
            ("financial_constraints", "government_discount_rate"),
            ("financial_constraints", "discount_rate_type"),
            ("financial_constraints", "discount_currency"),
            ("financial_constraints", "discount_compounding"),
            ("share_policy", "policy_minimum_share"),
            ("share_policy", "policy_maximum_share"),
            ("fair_consideration_policy", "institutional_conservatism"),
            ("fair_consideration_policy", "developer_safety_buffer"),
            ("fair_consideration_policy", "risk_adjusted_capacity_factor"),
            ("fair_consideration_policy", "minimum_capacity_factor"),
            ("fair_consideration_policy", "balanced_position_factor"),
            ("fair_consideration_policy", "developer_competitive_position_factor"),
            ("valuation_policy", "conservative_ceiling_method"),
            ("valuation_policy", "minimum_consideration_method"),
            ("valuation_policy", "minimum_consideration_amount"),
            ("valuation_policy", "rounding_method"),
            ("valuation_policy", "rounding_increment_percent"),
            ("valuation_policy", "recommendation_method"),
        )
    else:
        required_paths = (
            ("funding_policy", "equity_commitment_mode"),
            ("financial_constraints", "discount_rate"),
            ("financial_constraints", "minimum_developer_irr"),
            ("financial_constraints", "target_developer_irr"),
            ("financial_constraints", "minimum_profit_on_cost"),
            ("financial_constraints", "minimum_developer_multiple"),
            ("financial_constraints", "maximum_funding_gap"),
            ("finance_constraints", "minimum_dscr"),
            ("finance_constraints", "maximum_ltc"),
            ("finance_constraints", "maximum_ltv"),
            ("distribution_policy", "enabled"),
            ("distribution_policy", "future_cost_reserve_share"),
            ("distribution_policy", "minimum_operating_cash"),
            ("distribution_policy", "allocation_method"),
            ("distribution_policy", "contractual_payment_timing"),
            ("distribution_policy", "recover_developer_advances_before_landowner_cash"),
        )
    missing = [".".join(path) for path in required_paths if _path_value(snapshot, path) in (None, "")]
    if kind == "VALUATION" and snapshot.get("effective_date") in (None, "") and effective_from is None:
        missing.append("effective_date")
    if kind == "PROJECT":
        distribution = snapshot.get("distribution_policy") or {}
        if distribution.get("frequency_code") in (None, "") and distribution.get("frequency_months") in (None, ""):
            missing.append("distribution_policy.frequency_code")
        mode = str((snapshot.get("funding_policy") or {}).get("equity_commitment_mode") or "").upper()
        if mode in {"FIXED", "FIXED_PERCENT", "FIXED_10_PERCENT"} and _path_value(
            snapshot, ("funding_policy", "fixed_equity_direct_cost_share")
        ) in (None, ""):
            missing.append("funding_policy.fixed_equity_direct_cost_share")
    if missing:
        raise ConflictError(
            "POLICY_EXPLICIT_FIELDS_REQUIRED",
            f"{kind.title()} policy payload must explicitly define: {', '.join(sorted(set(missing)))}.",
        )


def _validate_policy_snapshot(snapshot: dict, kind: str) -> None:
    if kind != "VALUATION":
        return
    fraction_paths = (
        ("share_policy", "policy_minimum_share"),
        ("share_policy", "policy_maximum_share"),
        ("fair_consideration_policy", "institutional_conservatism"),
        ("fair_consideration_policy", "risk_adjusted_capacity_factor"),
        ("fair_consideration_policy", "developer_safety_buffer"),
        ("fair_consideration_policy", "balanced_position_factor"),
        ("fair_consideration_policy", "developer_competitive_position_factor"),
        ("fair_consideration_policy", "minimum_capacity_factor"),
        ("fair_consideration_policy", "balanced_position_minimum"),
        ("fair_consideration_policy", "balanced_position_maximum"),
        ("valuation_policy", "rounding_increment_percent"),
    )
    for path in fraction_paths:
        value = _fraction_value(snapshot, path)
        if value < 0 or value > 1:
            raise ConflictError(
                "POLICY_PERCENT_OUT_OF_RANGE",
                f"{'.'.join(path)} must be between 0% and 100%.",
            )
    minimum = _fraction_value(snapshot, ("share_policy", "policy_minimum_share"))
    maximum = _fraction_value(snapshot, ("share_policy", "policy_maximum_share"), default="1")
    if minimum > maximum:
        raise ConflictError("POLICY_SHARE_RANGE_INVALID", "Minimum share cannot exceed maximum share.")
    position_min = _fraction_value(snapshot, ("fair_consideration_policy", "balanced_position_minimum"))
    position_max = _fraction_value(snapshot, ("fair_consideration_policy", "balanced_position_maximum"), default="1")
    position = _fraction_value(snapshot, ("fair_consideration_policy", "balanced_position_factor"), default="0.5")
    if position_min > position_max or not position_min <= position <= position_max:
        raise ConflictError(
            "POLICY_RECOMMENDATION_POSITION_INVALID",
            "The recommendation position must fall inside its configured minimum and maximum.",
        )
    minimum_capacity = _fraction_value(snapshot, ("fair_consideration_policy", "minimum_capacity_factor"), default="0")
    configured_capacity = _fraction_value(snapshot, ("fair_consideration_policy", "risk_adjusted_capacity_factor"), default="1")
    if configured_capacity < minimum_capacity:
        raise ConflictError(
            "POLICY_CAPACITY_RANGE_INVALID",
            "The maximum retained technical capacity cannot be below the minimum retained capacity.",
        )
    recommendation_method = str((snapshot.get("valuation_policy") or {}).get("recommendation_method") or "POLICY_RANGE_POSITION").upper()
    if recommendation_method not in {"POLICY_RANGE_POSITION", "CORE_TARGET_RETURN"}:
        raise ConflictError(
            "POLICY_RECOMMENDATION_METHOD_INVALID",
            "Recommendation method must be POLICY_RANGE_POSITION or CORE_TARGET_RETURN.",
        )
    financial = snapshot.get("financial_constraints") or {}
    required_financial = {
        "government_discount_rate",
        "discount_rate_type",
        "discount_currency",
        "discount_compounding",
    }
    missing_financial = sorted(
        key for key in required_financial if financial.get(key) in (None, "")
    )
    valuation = snapshot.get("valuation_policy") or {}
    required_valuation = {
        "valuation_basis",
        "conservatism_method",
        "developer_protection_method",
        "retained_capacity_method",
        "conservative_ceiling_method",
        "minimum_consideration_method",
        "minimum_consideration_amount",
        "rounding_method",
        "rounding_increment_percent",
        "recommendation_method",
    }
    missing_valuation = sorted(
        key for key in required_valuation if valuation.get(key) in (None, "")
    )
    if missing_financial or missing_valuation or snapshot.get("effective_date") in (None, ""):
        fields = [
            *(f"financial_constraints.{key}" for key in missing_financial),
            *(f"valuation_policy.{key}" for key in missing_valuation),
        ]
        if snapshot.get("effective_date") in (None, ""):
            fields.append("effective_date")
        raise ConflictError(
            "VALUATION_POLICY_EXPLICIT_FIELDS_REQUIRED",
            "Valuation policy requires explicit governed values for: " + ", ".join(fields) + ".",
        )
    try:
        date.fromisoformat(str(snapshot["effective_date"]))
    except (TypeError, ValueError) as exc:
        raise ConflictError("POLICY_EFFECTIVE_DATE_INVALID", "effective_date must use YYYY-MM-DD.") from exc
    rate_type = str(financial["discount_rate_type"]).upper()
    if rate_type not in {"NOMINAL", "REAL"}:
        raise ConflictError("POLICY_DISCOUNT_TYPE_INVALID", "discount_rate_type must be NOMINAL or REAL.")
    currency = str(financial["discount_currency"]).upper()
    if currency != "PROJECT_CURRENCY" and (len(currency) != 3 or not currency.isalpha()):
        raise ConflictError(
            "POLICY_DISCOUNT_CURRENCY_INVALID",
            "discount_currency must be PROJECT_CURRENCY or a three-letter currency code.",
        )
    if str(financial["discount_compounding"]).upper() not in {"ANNUAL", "MONTHLY", "CONTINUOUS"}:
        raise ConflictError(
            "POLICY_DISCOUNT_COMPOUNDING_INVALID",
            "discount_compounding must be ANNUAL, MONTHLY, or CONTINUOUS.",
        )
    discount = _fraction_value(snapshot, ("financial_constraints", "government_discount_rate"))
    if discount <= Decimal("-1"):
        raise ConflictError("POLICY_DISCOUNT_RATE_INVALID", "Discount rate must be greater than -100%.")




def policy_type(snapshot: dict | None) -> str:
    """Return the governed policy family. Legacy policies are project policies."""

    source = snapshot if isinstance(snapshot, dict) else {}
    guidance = source.get("policy_guidance") if isinstance(source.get("policy_guidance"), dict) else {}
    value = str(guidance.get("policy_type") or "PROJECT").upper()
    return value if value in POLICY_TYPES else "PROJECT"

def policy_product_scope(snapshot: dict | None) -> str:
    """Return the governed product scope; legacy policies apply to both editions."""

    source = snapshot if isinstance(snapshot, dict) else {}
    guidance = source.get("policy_guidance") if isinstance(source.get("policy_guidance"), dict) else {}
    scope = str(guidance.get("product_scope") or "BOTH").upper()
    return scope if scope in POLICY_PRODUCT_SCOPES else "BOTH"


def policy_applies_to(snapshot: dict | None, edition: str) -> bool:
    scope = policy_product_scope(snapshot)
    requested = str(edition or "BOTH").upper()
    return scope == "BOTH" or scope == requested


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def policy_is_effective(version, *, at: datetime | None = None) -> bool:
    """Published policies are operational only inside their effective window."""

    if str(getattr(version, "status", "")).upper() != "PUBLISHED":
        return False
    moment = _as_utc(at or utc_now())
    effective_from = _as_utc(getattr(version, "effective_from", None))
    effective_to = _as_utc(getattr(version, "effective_to", None))
    if effective_from is not None and effective_from > moment:
        return False
    if effective_to is not None and effective_to <= moment:
        return False
    return True


def require_operational_policy(version: PolicyPackVersion, *, edition: str, expected_type: str | None = None) -> PolicyPackVersion:
    """Reject draft, expired, future-dated or edition-mismatched policies."""

    if not policy_is_effective(version):
        raise ConflictError(
            "POLICY_NOT_OPERATIONAL",
            "Select a published policy inside its effective period.",
        )
    if not policy_applies_to(version.policy_snapshot, edition):
        raise ConflictError(
            "POLICY_SCOPE_MISMATCH",
            f"The selected policy does not apply to the {edition.title()} Edition.",
        )
    if expected_type and policy_type(version.policy_snapshot) != str(expected_type).upper():
        raise ConflictError(
            "POLICY_TYPE_MISMATCH",
            f"Select a published {str(expected_type).lower()} policy.",
        )
    return version


def policy_option_payload(pack: PolicyPack, version: PolicyPackVersion) -> dict:
    snapshot = version.policy_snapshot if isinstance(version.policy_snapshot, dict) else {}
    constraints = snapshot.get("financial_constraints") if isinstance(snapshot.get("financial_constraints"), dict) else {}
    finance_constraints = snapshot.get("finance_constraints") if isinstance(snapshot.get("finance_constraints"), dict) else {}
    funding_policy = snapshot.get("funding_policy") if isinstance(snapshot.get("funding_policy"), dict) else {}
    fair_policy = snapshot.get("fair_consideration_policy") if isinstance(snapshot.get("fair_consideration_policy"), dict) else {}
    valuation_policy = snapshot.get("valuation_policy") if isinstance(snapshot.get("valuation_policy"), dict) else {}
    distribution_policy = snapshot.get("distribution_policy") if isinstance(snapshot.get("distribution_policy"), dict) else {}
    procurement_policy = snapshot.get("procurement_policy") if isinstance(snapshot.get("procurement_policy"), dict) else {}
    return {
        "pack_id": pack.id,
        "pack_name": pack.name,
        "pack_code": pack.code,
        "version_id": version.id,
        "version_number": version.version_number,
        "version_label": version.version_label,
        "status": version.status,
        "effective_from": version.effective_from,
        "effective_to": version.effective_to,
        "policy_hash": version.policy_hash,
        "product_scope": policy_product_scope(snapshot),
        "policy_type": policy_type(snapshot),
        "summary": {
            "discount_rate": constraints.get("discount_rate"),
            "government_discount_rate": constraints.get("government_discount_rate"),
            "minimum_developer_irr": constraints.get("minimum_developer_irr"),
            "target_developer_irr": constraints.get("target_developer_irr"),
            "minimum_profit_on_cost": constraints.get("minimum_profit_on_cost"),
            "minimum_developer_multiple": constraints.get("minimum_developer_multiple"),
            "maximum_payback_years": constraints.get("maximum_payback_years"),
            "minimum_equity_irr": finance_constraints.get("minimum_equity_irr"),
            "minimum_dscr": finance_constraints.get("minimum_dscr"),
            "maximum_ltc": finance_constraints.get("maximum_ltc"),
            "maximum_ltv": finance_constraints.get("maximum_ltv"),
            "equity_commitment_mode": funding_policy.get("equity_commitment_mode", "MANUAL"),
            "fixed_equity_direct_cost_share": funding_policy.get("fixed_equity_direct_cost_share"),
            "risk_adjusted_capacity_factor": fair_policy.get("risk_adjusted_capacity_factor"),
            "institutional_conservatism": fair_policy.get("institutional_conservatism"),
            "balanced_position_factor": fair_policy.get("balanced_position_factor"),
            "developer_safety_buffer": fair_policy.get("developer_safety_buffer"),
            "developer_competitive_position_factor": fair_policy.get("developer_competitive_position_factor"),
            "minimum_capacity_factor": fair_policy.get("minimum_capacity_factor"),
            "maximum_capacity_factor": fair_policy.get("maximum_capacity_factor"),
            "rounding_increment_percent": valuation_policy.get("rounding_increment_percent"),
            "recommendation_method": valuation_policy.get("recommendation_method"),
            "distribution_enabled": distribution_policy.get("enabled"),
            "distribution_frequency_months": distribution_policy.get("frequency_months"),
            "future_cost_reserve_share": distribution_policy.get("future_cost_reserve_share", distribution_policy.get("remaining_cost_reserve_share")),
            "distribution_allocation_method": distribution_policy.get("allocation_method"),
            "procurement_opening_discount_rate": procurement_policy.get("opening_discount_rate"),
            "procurement_target_discount_rate": procurement_policy.get("target_discount_rate"),
            "procurement_minimum_retained_contingency_rate": procurement_policy.get("minimum_retained_contingency_rate"),
        },
    }


def _scope_key(organization_id: str, workspace_id: str | None) -> str:
    return workspace_id or organization_id


def create_policy_pack(
    session: Session,
    *,
    context: AuthContext,
    name: str,
    code: str,
    description: str | None,
    workspace_id: str | None,
) -> PolicyPack:
    organization_id, context_workspace_id = require_tenant_context(context)
    if workspace_id is not None:
        get_workspace(session, context, workspace_id)
        if context_workspace_id is not None and workspace_id != context_workspace_id:
            raise NotFoundError("Workspace not found.")
    scope_key = _scope_key(organization_id, workspace_id)
    existing = session.scalar(
        select(PolicyPack).where(
            PolicyPack.organization_id == organization_id,
            PolicyPack.scope_key == scope_key,
            PolicyPack.code == code,
        )
    )
    if existing is not None:
        raise ConflictError("POLICY_PACK_CODE_EXISTS", "Policy-pack code already exists in this scope.")
    record = PolicyPack(
        organization_id=organization_id,
        workspace_id=workspace_id,
        scope_key=scope_key,
        name=name.strip(),
        code=code,
        description=description,
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="POLICY_PACK_CREATED",
        entity_type="PolicyPack",
        entity_id=record.id,
        after={"name": record.name, "code": record.code, "workspace_id": record.workspace_id},
    )
    return record


def _next_policy_version_number(session: Session, policy_pack_id: str) -> int:
    current = session.scalar(
        select(func.max(PolicyPackVersion.version_number)).where(
            PolicyPackVersion.policy_pack_id == policy_pack_id
        )
    )
    return int(current or 0) + 1


def _normalize_policy_snapshot(
    pack: PolicyPack,
    version_label: str,
    snapshot: dict,
    effective_from,
) -> dict:
    if not isinstance(snapshot, dict):
        raise ConflictError("POLICY_SNAPSHOT_INVALID", "Policy snapshot must be a JSON object.")
    incoming = deepcopy(snapshot)
    incoming_guidance = incoming.get("policy_guidance") if isinstance(incoming.get("policy_guidance"), dict) else {}
    requested_kind = str(incoming_guidance.get("policy_type") or "PROJECT").upper()
    if requested_kind not in POLICY_TYPES:
        raise ConflictError("POLICY_TYPE_INVALID", "Policy type must be PROJECT or VALUATION.")
    _require_explicit_policy_inputs(incoming, requested_kind, effective_from=effective_from)
    normalized = _deep_merge(_canonical_policy_template(requested_kind), incoming)
    if requested_kind == "VALUATION":
        # Remove project-only sections that could have leaked from the old
        # cross-family clone workflow.
        for key in ("funding_policy", "finance_constraints", "distribution_policy", "procurement_policy", "risk_policy", "tender_policy"):
            normalized.pop(key, None)
        constraints = normalized.get("financial_constraints")
        if isinstance(constraints, dict):
            constraints.pop("discount_rate", None)
    else:
        # Project/execution policies must never silently own valuation and
        # negotiation parameters.  Imported legacy policies are normalized at
        # the boundary so downstream services have one unambiguous owner.
        for key in ("valuation_policy", "share_policy", "fair_consideration_policy", "public_value_adjustment"):
            normalized.pop(key, None)
        constraints = normalized.get("financial_constraints")
        if isinstance(constraints, dict):
            constraints.pop("government_discount_rate", None)
        guidance = normalized.get("policy_guidance")
        if isinstance(guidance, dict):
            for key in list(guidance):
                if key == "financial_constraints.government_discount_rate" or key.startswith((
                    "valuation_policy.",
                    "share_policy.",
                    "fair_consideration_policy.",
                    "public_value_adjustment.",
                )):
                    guidance.pop(key, None)
    normalized["policy_id"] = pack.code
    normalized["version"] = version_label
    guidance = normalized.get("policy_guidance")
    if not isinstance(guidance, dict):
        guidance = {}
    scope = str(guidance.get("product_scope") or "BOTH").upper()
    if scope not in POLICY_PRODUCT_SCOPES:
        raise ConflictError("POLICY_PRODUCT_SCOPE_INVALID", "Policy product scope must be BOTH, DEVELOPER or LANDOWNER.")
    guidance["product_scope"] = scope
    kind = str(guidance.get("policy_type") or "PROJECT").upper()
    if kind not in POLICY_TYPES:
        raise ConflictError("POLICY_TYPE_INVALID", "Policy type must be PROJECT or VALUATION.")
    guidance["policy_type"] = kind
    normalized["policy_guidance"] = guidance
    if "effective_date" not in normalized and effective_from is not None:
        normalized["effective_date"] = effective_from.date().isoformat()
    _validate_policy_snapshot(normalized, kind)
    return normalized


def create_policy_version(
    session: Session,
    *,
    context: AuthContext,
    policy_pack_id: str,
    version_label: str,
    policy_snapshot: dict,
    notes: str | None,
    effective_from,
    effective_to,
    supersedes_version_id: str | None,
) -> PolicyPackVersion:
    pack = get_policy_pack(session, context, policy_pack_id)
    if effective_from and effective_to and effective_to <= effective_from:
        raise ConflictError("POLICY_EFFECTIVE_RANGE_INVALID", "effective_to must be after effective_from.")
    if supersedes_version_id is not None:
        source = get_policy_version(session, context, supersedes_version_id)
        if source.policy_pack_id != pack.id:
            raise NotFoundError("Superseded policy version not found in this pack.")
    normalized = _normalize_policy_snapshot(pack, version_label, policy_snapshot, effective_from)
    record = PolicyPackVersion(
        organization_id=pack.organization_id,
        workspace_id=pack.workspace_id,
        policy_pack_id=pack.id,
        version_number=_next_policy_version_number(session, pack.id),
        version_label=version_label,
        status="DRAFT",
        effective_from=effective_from,
        effective_to=effective_to,
        policy_snapshot=normalized,
        policy_hash=sha256_json(normalized),
        notes=notes,
        supersedes_version_id=supersedes_version_id,
        created_by_user_id=context.user_id,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        context=context,
        action="POLICY_VERSION_CREATED",
        entity_type="PolicyPackVersion",
        entity_id=record.id,
        after={
            "policy_pack_id": pack.id,
            "version_number": record.version_number,
            "version_label": record.version_label,
            "policy_hash": record.policy_hash,
        },
    )
    return record


def update_policy_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    changes: dict,
) -> PolicyPackVersion:
    record = get_policy_version(session, context, version_id)
    if record.status != "DRAFT":
        raise ConflictError(
            "POLICY_VERSION_IMMUTABLE",
            "Published or retired policy versions cannot be edited. Create a new version.",
        )
    pack = get_policy_pack(session, context, record.policy_pack_id)
    before = {
        "version_label": record.version_label,
        "policy_hash": record.policy_hash,
        "effective_from": record.effective_from.isoformat() if record.effective_from else None,
        "effective_to": record.effective_to.isoformat() if record.effective_to else None,
    }
    new_label = changes.get("version_label") or record.version_label
    new_effective_from = changes.get("effective_from", record.effective_from)
    new_effective_to = changes.get("effective_to", record.effective_to)
    if new_effective_from and new_effective_to and new_effective_to <= new_effective_from:
        raise ConflictError("POLICY_EFFECTIVE_RANGE_INVALID", "effective_to must be after effective_from.")
    if changes.get("policy_snapshot") is not None:
        record.policy_snapshot = _normalize_policy_snapshot(
            pack,
            new_label,
            changes["policy_snapshot"],
            new_effective_from,
        )
    elif new_label != record.version_label:
        record.policy_snapshot = deepcopy(record.policy_snapshot)
        record.policy_snapshot["version"] = new_label
    record.version_label = new_label
    record.effective_from = new_effective_from
    record.effective_to = new_effective_to
    if "notes" in changes:
        record.notes = changes["notes"]
    record.policy_hash = sha256_json(record.policy_snapshot)
    session.flush()
    record_audit(
        session,
        context=context,
        action="POLICY_VERSION_UPDATED",
        entity_type="PolicyPackVersion",
        entity_id=record.id,
        before=before,
        after={
            "version_label": record.version_label,
            "policy_hash": record.policy_hash,
            "effective_from": record.effective_from.isoformat() if record.effective_from else None,
            "effective_to": record.effective_to.isoformat() if record.effective_to else None,
        },
    )
    return record


def _validate_policy_minimum_structure(snapshot: dict) -> None:
    kind = policy_type(snapshot)
    common_required = {"policy_id", "version", "effective_date", "financial_constraints"}
    family_required = (
        {"share_policy", "valuation_policy", "fair_consideration_policy"}
        if kind == "VALUATION"
        else {"funding_policy", "finance_constraints", "distribution_policy"}
    )
    required = common_required | family_required
    missing = sorted(required - set(snapshot))
    if missing:
        raise ConflictError(
            "POLICY_SNAPSHOT_INCOMPLETE",
            f"{kind.title()} policy snapshot is missing required field(s): {', '.join(missing)}.",
        )
    for key in required:
        if key in {"policy_id", "version", "effective_date"}:
            continue
        if not isinstance(snapshot.get(key), dict):
            raise ConflictError(
                "POLICY_SNAPSHOT_INVALID",
                f"{key} must be a JSON object.",
            )
    product_scope = policy_product_scope(snapshot)
    if product_scope not in POLICY_PRODUCT_SCOPES:
        raise ConflictError("POLICY_PRODUCT_SCOPE_INVALID", "Unsupported policy product scope.")
    if kind not in POLICY_TYPES:
        raise ConflictError("POLICY_TYPE_INVALID", "Unsupported policy type.")
    forbidden = (
        {"funding_policy", "finance_constraints", "distribution_policy", "procurement_policy", "risk_policy", "tender_policy"}
        if kind == "VALUATION"
        else {"valuation_policy", "share_policy", "fair_consideration_policy", "public_value_adjustment"}
    )
    leaked = sorted(key for key in forbidden if key in snapshot)
    if leaked:
        raise ConflictError(
            "POLICY_FAMILY_OWNERSHIP_INVALID",
            f"{kind.title()} policy contains field(s) governed by the other policy family: {', '.join(leaked)}.",
        )
    if kind == "PROJECT":
        funding_policy = snapshot.get("funding_policy")
        raw_mode = funding_policy.get("equity_commitment_mode")
        if raw_mode in (None, ""):
            raise ConflictError(
                "POLICY_EQUITY_MODE_REQUIRED",
                "Project policy must explicitly define funding_policy.equity_commitment_mode.",
            )
        mode = str(raw_mode).upper()
        if mode not in {"FIXED_10_PERCENT", "FIXED_PERCENT", "MANUAL", "DECLARED_COMMITMENT"}:
            raise ConflictError("POLICY_EQUITY_MODE_INVALID", "Equity commitment mode must be MANUAL or a supported fixed-percentage rule.")
        if mode in {"FIXED_10_PERCENT", "FIXED_PERCENT"}:
            if funding_policy.get("fixed_equity_direct_cost_share") in (None, ""):
                raise ConflictError(
                    "POLICY_FIXED_EQUITY_REQUIRED",
                    "A fixed-percentage equity policy must explicitly define fixed_equity_direct_cost_share.",
                )
            try:
                fixed_share = Decimal(str(funding_policy["fixed_equity_direct_cost_share"]))
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise ConflictError("POLICY_FIXED_EQUITY_INVALID", "The fixed institutional equity share must be numeric.") from exc
            if fixed_share < 0 or fixed_share > 1:
                raise ConflictError("POLICY_FIXED_EQUITY_INVALID", "The fixed institutional equity share must be between 0% and 100%.")
    _validate_policy_snapshot(snapshot, kind)


def publish_policy_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
) -> PolicyPackVersion:
    record = get_policy_version(session, context, version_id)
    if record.status != "DRAFT":
        raise ConflictError("POLICY_VERSION_NOT_DRAFT", "Only a draft policy version can be published.")
    # Basic administration publishes policies immediately.  The effective
    # timestamp remains stored for audit/versioning, but an administrator is
    # not forced to type it for the common case.
    publish_time = utc_now()
    if record.effective_from is None:
        record.effective_from = publish_time
    record.policy_snapshot = deepcopy(record.policy_snapshot)
    record.policy_snapshot["effective_date"] = record.effective_from.date().isoformat()
    record.policy_hash = sha256_json(record.policy_snapshot)
    _validate_policy_minimum_structure(record.policy_snapshot)
    record.status = "PUBLISHED"
    record.published_by_user_id = context.user_id
    record.published_at = publish_time
    session.flush()
    record_audit(
        session,
        context=context,
        action="POLICY_VERSION_PUBLISHED",
        entity_type="PolicyPackVersion",
        entity_id=record.id,
        after={
            "status": record.status,
            "effective_from": record.effective_from.isoformat(),
            "policy_hash": record.policy_hash,
        },
    )
    return record


def clone_policy_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: str,
    version_label: str,
    notes: str | None = None,
) -> PolicyPackVersion:
    source = get_policy_version(session, context, version_id)
    return create_policy_version(
        session,
        context=context,
        policy_pack_id=source.policy_pack_id,
        version_label=version_label,
        policy_snapshot=source.policy_snapshot,
        notes=notes,
        effective_from=None,
        effective_to=None,
        supersedes_version_id=source.id,
    )
