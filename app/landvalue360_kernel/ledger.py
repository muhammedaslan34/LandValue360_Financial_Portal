"""Canonical monthly ledger, reconciliation, and engine invariants.

The module does not calculate commercial assumptions.  It receives the monthly
rows emitted by the deterministic execution engine and converts them into a
balanced double-entry-like event ledger.  Reconciliation is mandatory before a
result can be presented as an official PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable

from .manifest import MONTHLY_LEDGER_VERSION

ZERO = Decimal("0")
CENT = Decimal("0.01")


def D(value: Any) -> Decimal:
    if value in (None, ""):
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ledger amount: {value!r}") from exc


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def sha256_payload(value: Any) -> str:
    payload = json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_id: str
    month: int
    date: str
    event_type: str
    account: str
    counterparty: str
    amount: Decimal
    cash_effect: Decimal
    debt_effect: Decimal = ZERO
    equity_effect: Decimal = ZERO
    mandatory: bool = False
    source: str = "MONTHLY_ENGINE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "month": self.month,
            "date": self.date,
            "event_type": self.event_type,
            "account": self.account,
            "counterparty": self.counterparty,
            "amount": format(self.amount, "f"),
            "cash_effect": format(self.cash_effect, "f"),
            "debt_effect": format(self.debt_effect, "f"),
            "equity_effect": format(self.equity_effect, "f"),
            "mandatory": self.mandatory,
            "source": self.source,
        }


def _event(
    *,
    month: int,
    date: str,
    event_type: str,
    account: str,
    counterparty: str,
    amount: Any,
    cash_sign: int,
    debt_sign: int = 0,
    equity_sign: int = 0,
    mandatory: bool = False,
) -> LedgerEvent | None:
    parsed = D(amount)
    if abs(parsed) < CENT:
        return None
    identity = f"{month}|{date}|{event_type}|{account}|{counterparty}|{format(parsed, 'f')}"
    return LedgerEvent(
        event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        month=month,
        date=date,
        event_type=event_type,
        account=account,
        counterparty=counterparty,
        amount=parsed,
        cash_effect=parsed * Decimal(cash_sign),
        debt_effect=parsed * Decimal(debt_sign),
        equity_effect=parsed * Decimal(equity_sign),
        mandatory=mandatory,
    )


def build_event_ledger(monthly_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build the canonical event ledger and reconcile monthly roll-forwards."""

    events: list[LedgerEvent] = []
    reconciliations: list[dict[str, Any]] = []
    max_cash_variance = ZERO
    max_debt_variance = ZERO
    previous_date: str | None = None
    chronological = True

    for index, row in enumerate(monthly_rows, start=1):
        month = int(row.get("month") or index)
        row_date = str(row.get("date") or "")
        if previous_date is not None and row_date < previous_date:
            chronological = False
        previous_date = row_date

        opening_cash = D(row.get("opening_cash"))
        equity_contribution = D(row.get("equity_contribution"))
        # Engine v0.3.3 defines first-period opening cash as developer equity
        # contributed at the base date.  It is an opening balance, not a
        # same-month cash source.  Record the equity ownership event without
        # adding the cash a second time, and keep all row contributions as
        # incremental cash sources.
        opening_equity = opening_cash if index == 1 else ZERO
        incremental_equity = equity_contribution

        candidates = (
            _event(month=month, date=row_date, event_type="OPENING_EQUITY_BALANCE", account="OPENING_CASH", counterparty="DEVELOPER_EQUITY", amount=opening_equity, cash_sign=0, equity_sign=1),
            _event(month=month, date=row_date, event_type="CUSTOMER_COLLECTION", account="CASH", counterparty="CUSTOMERS", amount=row.get("sales_collections", row.get("receipts")), cash_sign=1),
            _event(month=month, date=row_date, event_type="DEBT_DRAW", account="CASH", counterparty="LENDERS", amount=row.get("financing_draw", row.get("debt_draw")), cash_sign=1, debt_sign=1),
            _event(month=month, date=row_date, event_type="EQUITY_CONTRIBUTION", account="CASH", counterparty="DEVELOPER_EQUITY", amount=incremental_equity, cash_sign=1, equity_sign=1),
            _event(month=month, date=row_date, event_type="DEVELOPMENT_COST", account="DEVELOPMENT_COST", counterparty="SUPPLIERS", amount=row.get("actual_cost", row.get("executed_development_cost")), cash_sign=-1),
            _event(month=month, date=row_date, event_type="PUBLIC_CONSIDERATION", account="LANDOWNER_CONSIDERATION", counterparty="PUBLIC_LANDOWNER", amount=row.get("government_payment", row.get("contractual_payment")), cash_sign=-1, mandatory=True),
            _event(month=month, date=row_date, event_type="INTEREST", account="FINANCE_COST", counterparty="LENDERS", amount=row.get("interest_paid", row.get("interest_accrued")), cash_sign=-1, mandatory=True),
            _event(month=month, date=row_date, event_type="FINANCING_FEE", account="FINANCE_COST", counterparty="LENDERS", amount=row.get("financing_fees"), cash_sign=-1, mandatory=True),
            _event(month=month, date=row_date, event_type="DEBT_REPAYMENT", account="DEBT", counterparty="LENDERS", amount=row.get("financing_repayment", row.get("principal_repayment")), cash_sign=-1, debt_sign=-1, mandatory=True),
            _event(month=month, date=row_date, event_type="DEVELOPER_DISTRIBUTION", account="DISTRIBUTION", counterparty="DEVELOPER_EQUITY", amount=row.get("developer_distribution"), cash_sign=-1, equity_sign=-1),
            _event(month=month, date=row_date, event_type="LANDOWNER_DISTRIBUTION", account="DISTRIBUTION", counterparty="PUBLIC_LANDOWNER", amount=row.get("landowner_distribution"), cash_sign=-1),
        )
        month_events = [item for item in candidates if item is not None]
        events.extend(month_events)

        ending_cash = D(row.get("ending_cash"))
        cash_effect = sum((item.cash_effect for item in month_events), ZERO)
        cash_variance = opening_cash + cash_effect - ending_cash
        max_cash_variance = max(max_cash_variance, abs(cash_variance))

        opening_debt = D(row.get("opening_debt"))
        ending_debt = D(row.get("ending_debt", row.get("closing_debt")))
        debt_effect = sum((item.debt_effect for item in month_events), ZERO)
        debt_variance = opening_debt + debt_effect - ending_debt
        max_debt_variance = max(max_debt_variance, abs(debt_variance))

        reconciliations.append(
            {
                "month": month,
                "date": row_date,
                "opening_cash": format(opening_cash, "f"),
                "net_cash_events": format(cash_effect, "f"),
                "ending_cash": format(ending_cash, "f"),
                "cash_variance": format(cash_variance, "f"),
                "opening_debt": format(opening_debt, "f"),
                "net_debt_events": format(debt_effect, "f"),
                "ending_debt": format(ending_debt, "f"),
                "debt_variance": format(debt_variance, "f"),
                "balanced": abs(cash_variance) <= CENT and abs(debt_variance) <= CENT,
            }
        )

    event_payload = [item.to_dict() for item in events]
    balanced = chronological and max_cash_variance <= CENT and max_debt_variance <= CENT
    return {
        "ledger_version": MONTHLY_LEDGER_VERSION,
        "status": "RECONCILED" if balanced else "OUT_OF_BALANCE",
        "balanced": balanced,
        "chronological": chronological,
        "tolerance": format(CENT, "f"),
        "maximum_cash_variance": format(max_cash_variance, "f"),
        "maximum_debt_variance": format(max_debt_variance, "f"),
        "event_count": len(event_payload),
        "events": event_payload,
        "monthly_reconciliation": reconciliations,
        "ledger_hash": sha256_payload(event_payload),
    }


def build_engine_invariants(model: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """Evaluate mandatory financial closure and ledger integrity invariants."""

    summary = model.get("summary") or {}
    selected = model.get("selected_contract") or {}

    def value(name: str, *fallbacks: str) -> tuple[Decimal | None, str | None]:
        for key in (name, *fallbacks):
            if key in selected and selected.get(key) not in (None, ""):
                return D(selected.get(key)), f"selected_contract.{key}"
            if key in summary and summary.get(key) not in (None, ""):
                return D(summary.get(key)), f"summary.{key}"
        return None, None

    terminal_month = None
    monthly_reconciliation = ledger.get("monthly_reconciliation") or []
    if monthly_reconciliation:
        terminal_month = monthly_reconciliation[-1].get("month")

    def closure_check(
        invariant_id: str,
        label: str,
        name: str,
        *fallbacks: str,
        corrective_action: str,
    ) -> dict[str, Any]:
        actual, source = value(name, *fallbacks)
        present = actual is not None
        passed = present and abs(actual) <= CENT
        if not present:
            reason = "The required terminal metric is missing; absence is not interpreted as zero."
            impact = None
        elif passed:
            reason = "The terminal balance is within the configured monetary tolerance."
            impact = format(abs(actual), "f")
        else:
            reason = "The terminal balance is outside the configured monetary tolerance."
            impact = format(abs(actual), "f")
        return {
            "invariant_id": invariant_id,
            "label": label,
            "actual": None if actual is None else format(actual, "f"),
            "operator": "abs() <=",
            "threshold": format(CENT, "f"),
            "passed": passed,
            "mandatory": True,
            "month": terminal_month,
            "reason": reason,
            "affected_inputs": [source] if source else [f"selected_contract.{name}", f"summary.{name}"],
            "financial_impact": impact,
            "corrective_action": corrective_action,
        }

    first_unbalanced = next(
        (row for row in monthly_reconciliation if not bool(row.get("balanced"))),
        None,
    )

    checks = [
        {
            "invariant_id": "MONTHLY_LEDGER_BALANCED",
            "label": "Every monthly cash and debt roll-forward reconciles",
            "actual": ledger.get("status"),
            "operator": "==",
            "threshold": "RECONCILED",
            "passed": bool(ledger.get("balanced")),
            "mandatory": True,
            "month": None if first_unbalanced is None else first_unbalanced.get("month"),
            "reason": (
                "Every monthly cash and debt roll-forward is within tolerance."
                if ledger.get("balanced")
                else "At least one monthly cash/debt roll-forward is out of balance or the dates are not chronological."
            ),
            "affected_inputs": ["monthly_cashflow"],
            "financial_impact": format(
                max(D(ledger.get("maximum_cash_variance")), D(ledger.get("maximum_debt_variance"))),
                "f",
            ),
            "corrective_action": "Inspect the first failed monthly row and reconcile every cash and debt event before relying on results.",
        },
        closure_check(
            "TERMINAL_DEBT_ZERO",
            "Terminal debt is fully repaid",
            "terminal_debt",
            corrective_action="Extend the horizon or add a funded principal-repayment event until the terminal debt is zero.",
        ),
        closure_check(
            "DEFERRED_COST_ZERO",
            "All developer-borne development cost is executed",
            "terminal_deferred_cost",
            "deferred_development_cost",
            corrective_action="Extend the construction horizon or fund the deferred developer-borne cost.",
        ),
        closure_check(
            "CONTRACTUAL_ARREARS_ZERO",
            "All contractual public-land payments are settled",
            "terminal_contractual_arrears",
            "deferred_contractual_payment",
            corrective_action="Add the missing public-consideration settlement or extend the contract horizon.",
        ),
        closure_check(
            "MANDATORY_SHORTFALL_ZERO",
            "No mandatory payment or non-deferrable execution shortfall remains",
            "mandatory_shortfall",
            corrective_action="Provide committed funding or revise the schedule so every mandatory obligation is settled.",
        ),
        closure_check(
            "UNMODELED_SCOPE_ZERO",
            "The model horizon includes every sale, collection, cost, debt and contractual flow",
            "unmodeled_scope",
            corrective_action="Extend the modeled horizon and include every omitted sale, collection, cost, debt and contract flow.",
        ),
    ]
    selected_constraints = selected.get("constraints") or []
    failed_selected = [row for row in selected_constraints if bool(row.get("mandatory", True)) and not bool(row.get("passed"))]
    checks.append(
        {
            "invariant_id": "SELECTED_CONTRACT_CONSTRAINTS_PASS",
            "label": "The selected contractual structure satisfies every mandatory institutional constraint",
            "actual": len(failed_selected),
            "operator": "==",
            "threshold": 0,
            "passed": not failed_selected,
            "mandatory": True,
            "failed_constraint_ids": [row.get("constraint_id") for row in failed_selected],
        }
    )
    failed = [item for item in checks if item["mandatory"] and not item["passed"]]
    return {
        "status": "PASS" if not failed else "FAIL",
        "passed": not failed,
        "checks": checks,
        "failed_invariant_ids": [item["invariant_id"] for item in failed],
        "invariant_hash": sha256_payload(checks),
    }
