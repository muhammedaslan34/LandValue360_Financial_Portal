"""Governed equity-commitment policy resolution.

Detailed Stable release applies one explicit institutional choice:

* ``FIXED_10_PERCENT`` — recognized equity equals 10% of developer direct cost.
* ``MANUAL`` — the project must state a committed-equity amount.

Legacy policy packs without ``funding_policy`` are accepted only during migration; current calculations require a complete project policy.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from landvalue360_kernel.decimal_utils import ZERO, as_json_number, decimal
from landvalue360_kernel.exceptions import InputValidationError

FUNDING_POLICY_VERSION = "2.1.1"
FIXED_SHARE = Decimal("0.10")
SUPPORTED_MODES = {"FIXED_10_PERCENT", "FIXED_PERCENT", "MANUAL", "DECLARED_COMMITMENT"}


def apply_equity_commitment_policy(
    project_snapshot: dict[str, Any],
    policy_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return kernel-safe project/policy snapshots and an audit report."""

    project = deepcopy(project_snapshot)
    policy = deepcopy(policy_snapshot)
    funding_policy = policy.get("funding_policy")
    if funding_policy is None:
        return project, policy, {
            "funding_policy_version": FUNDING_POLICY_VERSION,
            "mode": "LEGACY_PROJECT_OR_POLICY",
            "fixed_equity_direct_cost_share": None,
            "manual_amount": (project.get("funding") or {}).get("committed_equity"),
            "explanation": (
                "This policy predates release 0.7.1. Explicit project equity is used when supplied; "
                "otherwise the historical available-equity percentage applies."
            ),
        }
    if not isinstance(funding_policy, dict):
        raise InputValidationError(
            "funding_policy must be an object.",
            path="policy.funding_policy",
            code="FUNDING_POLICY_OBJECT_REQUIRED",
        )
    policy_mode = str(funding_policy.get("equity_commitment_mode") or "FIXED_10_PERCENT").upper()
    project_funding = project.get("funding") if isinstance(project.get("funding"), dict) else {}
    project_mode = str(project_funding.get("equity_commitment_mode") or "").upper()
    if project_mode == "POLICY_SCREENING":
        mode = "FIXED_PERCENT"
    elif project_mode in {"DECLARED_COMMITMENT", "MANUAL", "MANUAL_COMMITMENT"}:
        mode = "DECLARED_COMMITMENT"
    else:
        mode = policy_mode
    if mode not in SUPPORTED_MODES:
        raise InputValidationError(
            f"Unsupported equity commitment mode: {mode}.",
            path="policy.funding_policy.equity_commitment_mode",
            code="EQUITY_COMMITMENT_MODE_UNSUPPORTED",
        )

    funding = deepcopy(project.get("funding") or {})
    opening_cash = decimal(
        funding.get("opening_cash", 0),
        path="project.funding.opening_cash",
    )
    additional_equity = decimal(
        funding.get("committed_additional_equity", funding.get("committed_equity", 0)),
        path="project.funding.committed_additional_equity",
    )
    if opening_cash < ZERO or additional_equity < ZERO:
        raise InputValidationError(
            "Opening cash and committed additional equity cannot be negative.",
            path="project.funding",
            code="EQUITY_CAPACITY_NEGATIVE",
        )
    financial = deepcopy(policy.get("financial_constraints") or {})
    if mode in {"FIXED_10_PERCENT", "FIXED_PERCENT"}:
        configured = decimal(
            funding_policy.get("fixed_equity_direct_cost_share", FIXED_SHARE),
            path="policy.funding_policy.fixed_equity_direct_cost_share",
        )
        if configured < ZERO or configured > Decimal("1"):
            raise InputValidationError(
                "The fixed equity share must be between 0% and 100% of developer direct cost.",
                path="policy.funding_policy.fixed_equity_direct_cost_share",
                code="FIXED_EQUITY_SHARE_OUT_OF_RANGE",
            )
        if mode == "FIXED_10_PERCENT" and configured != FIXED_SHARE:
            raise InputValidationError(
                "The FIXED_10_PERCENT mode is locked at 10% of developer direct cost.",
                path="policy.funding_policy.fixed_equity_direct_cost_share",
                code="FIXED_EQUITY_SHARE_MUST_BE_TEN_PERCENT",
            )
        recognized_share = FIXED_SHARE if mode == "FIXED_10_PERCENT" else configured
        financial["available_equity_direct_cost_share"] = as_json_number(recognized_share)
        manual_amount = funding.pop("committed_equity", None)
        funding.pop("opening_cash", None)
        funding.pop("committed_additional_equity", None)
        project["funding"] = funding
        policy["financial_constraints"] = financial
        report = {
            "funding_policy_version": FUNDING_POLICY_VERSION,
            "mode": mode,
            "fixed_equity_direct_cost_share": as_json_number(recognized_share),
            "manual_amount_ignored": manual_amount,
            "opening_cash": as_json_number(opening_cash),
            "committed_additional_equity": as_json_number(additional_equity),
            "explanation": (
                f"Recognized committed equity is calculated automatically as {recognized_share * Decimal('100')}% "
                "of developer direct cost. Any project-level manual amount is retained in the project draft "
                "but ignored by this policy."
            ),
        }
    else:
        amount = opening_cash + additional_equity
        if amount < ZERO:
            raise InputValidationError(
                "Manual committed equity cannot be negative.",
                path="project.funding.committed_equity",
                code="MANUAL_EQUITY_COMMITMENT_NEGATIVE",
            )
        funding["committed_equity"] = as_json_number(amount)
        funding.pop("opening_cash", None)
        funding.pop("committed_additional_equity", None)
        funding.pop("committed_equity_is_additional", None)
        project["funding"] = funding
        report = {
            "funding_policy_version": FUNDING_POLICY_VERSION,
            "mode": mode,
            "manual_amount": as_json_number(amount),
            "opening_cash": as_json_number(opening_cash),
            "committed_additional_equity": as_json_number(additional_equity),
            "recognized_total_equity_capacity": as_json_number(amount),
            "fixed_equity_direct_cost_share": as_json_number(FIXED_SHARE),
            "explanation": (
                "Recognized total equity capacity equals opening cash plus committed additional equity. "
                "Opening cash is available in month one; the additional commitment is drawn only when needed."
            ),
        }

    # funding_policy remains part of the governed unified-engine contract.
    # Earlier releases removed it for a retired kernel, which caused the
    # authoritative detailed engine to reject otherwise valid runs.
    policy["funding_policy"] = deepcopy(funding_policy)
    return project, policy, report
