"""Authoritative project, calculation and report state machine.

The browser is a client of this module; it is never the source of state.
Every command validates the current state before mutating persisted data.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .errors import ConflictError


class ProjectVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


class CalculationValidity(StrEnum):
    NOT_RUN = "NOT_RUN"
    VALID = "VALID"
    INVALID = "INVALID"
    NUMERICALLY_UNRESOLVED = "NUMERICALLY_UNRESOLVED"


class EconomicFeasibility(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"


class PolicyCompliance(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"


class EvidenceReadiness(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    INCOMPLETE = "INCOMPLETE"
    READY = "READY"


class ReportReadiness(StrEnum):
    NOT_READY = "NOT_READY"
    DRAFT_READY = "DRAFT_READY"
    OFFICIAL_READY = "OFFICIAL_READY"


class CalculationRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    LOCKED = "LOCKED"



PROJECT_TRANSITIONS: dict[ProjectVersionStatus, frozenset[ProjectVersionStatus]] = {
    ProjectVersionStatus.DRAFT: frozenset({ProjectVersionStatus.APPROVED, ProjectVersionStatus.ARCHIVED}),
    ProjectVersionStatus.APPROVED: frozenset({ProjectVersionStatus.ARCHIVED}),
    ProjectVersionStatus.ARCHIVED: frozenset(),
}

RUN_TRANSITIONS: dict[CalculationRunStatus, frozenset[CalculationRunStatus]] = {
    CalculationRunStatus.PENDING: frozenset({CalculationRunStatus.RUNNING, CalculationRunStatus.FAILED}),
    CalculationRunStatus.RUNNING: frozenset({CalculationRunStatus.SUCCESS, CalculationRunStatus.FAILED}),
    CalculationRunStatus.SUCCESS: frozenset({CalculationRunStatus.LOCKED, CalculationRunStatus.SUPERSEDED}),
    CalculationRunStatus.FAILED: frozenset({CalculationRunStatus.SUPERSEDED}),
    CalculationRunStatus.LOCKED: frozenset({CalculationRunStatus.SUPERSEDED}),
    CalculationRunStatus.SUPERSEDED: frozenset(),
}


def _assert_transition(current: StrEnum, target: StrEnum, allowed: dict[StrEnum, frozenset[StrEnum]], code: str) -> None:
    if target not in allowed.get(current, frozenset()):
        raise ConflictError(code, f"Transition {current.value} -> {target.value} is not permitted.")


def assert_project_transition(current: str, target: str) -> None:
    _assert_transition(ProjectVersionStatus(current), ProjectVersionStatus(target), PROJECT_TRANSITIONS, "PROJECT_STATE_TRANSITION_INVALID")


def assert_run_transition(current: str, target: str) -> None:
    _assert_transition(CalculationRunStatus(current), CalculationRunStatus(target), RUN_TRANSITIONS, "CALCULATION_STATE_TRANSITION_INVALID")



@dataclass(frozen=True)
class RunReadiness:
    calculation_validity: CalculationValidity
    economic_feasibility: EconomicFeasibility
    policy_compliance: PolicyCompliance
    evidence_readiness: EvidenceReadiness
    report_readiness: ReportReadiness


def derive_run_readiness(output: dict, *, run_locked: bool = False, official_requested: bool = False) -> RunReadiness:
    truth = output.get("financial_truth") or {}
    evaluation = str(truth.get("evaluation_status") or "").upper()
    if evaluation == "NUMERICALLY_UNRESOLVED":
        validity = CalculationValidity.NUMERICALLY_UNRESOLVED
    elif bool(truth.get("result_usable")):
        validity = CalculationValidity.VALID
    else:
        validity = CalculationValidity.INVALID
    if validity != CalculationValidity.VALID:
        feasibility = EconomicFeasibility.NOT_ASSESSED
        compliance = PolicyCompliance.NOT_ASSESSED
    else:
        feasibility = EconomicFeasibility.FEASIBLE if bool(truth.get("economic_feasible", truth.get("feasible"))) else EconomicFeasibility.INFEASIBLE
        compliance = PolicyCompliance.COMPLIANT if bool(truth.get("policy_compliant")) else PolicyCompliance.NON_COMPLIANT
    evidence_raw = str((output.get("report_readiness") or {}).get("evidence_readiness") or truth.get("evidence_readiness") or "NOT_REQUIRED").upper()
    evidence = EvidenceReadiness(evidence_raw) if evidence_raw in EvidenceReadiness._value2member_map_ else EvidenceReadiness.NOT_REQUIRED
    draft_ready = validity == CalculationValidity.VALID and bool(truth.get("cash_reconciliation_passed")) and bool(truth.get("ledger_invariants_passed"))
    official_ready = draft_ready and run_locked and evidence in {EvidenceReadiness.READY, EvidenceReadiness.NOT_REQUIRED}
    report = ReportReadiness.OFFICIAL_READY if official_ready else (ReportReadiness.DRAFT_READY if draft_ready else ReportReadiness.NOT_READY)
    if official_requested and report != ReportReadiness.OFFICIAL_READY:
        report = ReportReadiness.NOT_READY
    return RunReadiness(validity, feasibility, compliance, evidence, report)


def transition_table_markdown() -> str:
    def rows(title: str, mapping: dict[StrEnum, frozenset[StrEnum]]) -> list[str]:
        result = [f"## {title}", "", "| Current | Allowed next states |", "|---|---|"]
        for current, targets in mapping.items():
            result.append(f"| `{current.value}` | {', '.join(f'`{item.value}`' for item in sorted(targets, key=lambda x: x.value)) or '—'} |")
        result.append("")
        return result
    return "\n".join(["# State transition table", "", *rows("Project versions", PROJECT_TRANSITIONS), *rows("Calculation runs", RUN_TRANSITIONS)])
