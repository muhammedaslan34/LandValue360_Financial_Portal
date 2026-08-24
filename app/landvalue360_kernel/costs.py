"""Cost estimation, escalation, phasing, and responsibility allocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .cashflow import CashFlowSeries, DatedCashFlow, combine_series
from .curves import CurvePoint
from .dates import DayCountBasis, parse_date, year_fraction
from .decimal_utils import ONE, ZERO, as_json_number, decimal, decimal_power
from .exceptions import InputValidationError


@dataclass(frozen=True, slots=True)
class CostItemInput:
    cost_id: str
    name: str
    category: str
    quantity: Decimal
    unit_cost: Decimal
    base_date: date
    escalation_rate: Decimal
    contingency_rate: Decimal
    developer_responsibility_share: Decimal
    government_responsibility_share: Decimal
    eligible_net_sales_deduction_fraction: Decimal
    eligible_profit_share_cost_fraction: Decimal
    is_direct_cost: bool
    expenditure_curve: tuple[CurvePoint, ...]


@dataclass(frozen=True, slots=True)
class CostItemResult:
    cost_id: str
    name: str
    category: str
    base_cost: Decimal
    contingency_amount: Decimal
    escalated_total_cost: Decimal
    developer_total_cost: Decimal
    government_total_cost: Decimal
    eligible_net_sales_deduction_total: Decimal
    eligible_profit_share_cost_total: Decimal
    project_cost_series: CashFlowSeries
    developer_cost_series: CashFlowSeries
    government_cost_series: CashFlowSeries
    eligible_net_sales_deduction_series: CashFlowSeries
    eligible_profit_share_cost_series: CashFlowSeries
    is_direct_cost: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "cost_id": self.cost_id,
            "name": self.name,
            "category": self.category,
            "base_cost": as_json_number(self.base_cost),
            "contingency_amount": as_json_number(self.contingency_amount),
            "escalated_total_cost": as_json_number(self.escalated_total_cost),
            "developer_total_cost": as_json_number(self.developer_total_cost),
            "government_total_cost": as_json_number(self.government_total_cost),
            "eligible_net_sales_deduction_total": as_json_number(self.eligible_net_sales_deduction_total),
            "eligible_profit_share_cost_total": as_json_number(self.eligible_profit_share_cost_total),
            "is_direct_cost": self.is_direct_cost,
            "project_cost_series": self.project_cost_series.to_dict(),
            "developer_cost_series": self.developer_cost_series.to_dict(),
            "government_cost_series": self.government_cost_series.to_dict(),
            "eligible_net_sales_deduction_series": self.eligible_net_sales_deduction_series.to_dict(),
            "eligible_profit_share_cost_series": self.eligible_profit_share_cost_series.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CostResult:
    currency: str
    items: tuple[CostItemResult, ...]
    base_cost: Decimal
    contingency_amount: Decimal
    total_escalated_cost: Decimal
    developer_total_cost: Decimal
    government_total_cost: Decimal
    direct_cost: Decimal
    developer_direct_cost: Decimal
    government_direct_cost: Decimal
    project_cost_series: CashFlowSeries
    developer_cost_series: CashFlowSeries
    government_cost_series: CashFlowSeries
    eligible_net_sales_deduction_series: CashFlowSeries
    eligible_profit_share_cost_series: CashFlowSeries

    def to_dict(self) -> dict[str, object]:
        return {
            "currency": self.currency,
            "base_cost": as_json_number(self.base_cost),
            "contingency_amount": as_json_number(self.contingency_amount),
            "total_escalated_cost": as_json_number(self.total_escalated_cost),
            "developer_total_cost": as_json_number(self.developer_total_cost),
            "government_total_cost": as_json_number(self.government_total_cost),
            "direct_cost": as_json_number(self.direct_cost),
            "developer_direct_cost": as_json_number(self.developer_direct_cost),
            "government_direct_cost": as_json_number(self.government_direct_cost),
            "project_cost_series": self.project_cost_series.to_dict(),
            "developer_cost_series": self.developer_cost_series.to_dict(),
            "government_cost_series": self.government_cost_series.to_dict(),
            "eligible_net_sales_deduction_series": self.eligible_net_sales_deduction_series.to_dict(),
            "eligible_profit_share_cost_series": self.eligible_profit_share_cost_series.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }


def _validate_share(value: Decimal, *, path: str) -> Decimal:
    value = decimal(value, path=path)
    if value < ZERO or value > ONE:
        raise InputValidationError("Share must be between 0 and 1.", path=path)
    return value


def calculate_cost_item(input_data: CostItemInput, *, currency: str) -> CostItemResult:
    quantity = decimal(input_data.quantity)
    unit_cost = decimal(input_data.unit_cost)
    escalation_rate = decimal(input_data.escalation_rate)
    contingency_rate = decimal(input_data.contingency_rate)
    if quantity < ZERO or unit_cost < ZERO:
        raise InputValidationError("Cost quantity and unit cost cannot be negative.")
    if escalation_rate <= Decimal("-1"):
        raise InputValidationError("Escalation rate must be greater than -100%.")
    if contingency_rate < ZERO:
        raise InputValidationError("Contingency rate cannot be negative.")

    developer_share = _validate_share(
        input_data.developer_responsibility_share,
        path=f"costs.{input_data.cost_id}.developer_responsibility_share",
    )
    government_share = _validate_share(
        input_data.government_responsibility_share,
        path=f"costs.{input_data.cost_id}.government_responsibility_share",
    )
    if abs((developer_share + government_share) - ONE) > Decimal("0.00000001"):
        raise InputValidationError(
            "Developer and government responsibility shares must sum to 1.",
            path=f"costs.{input_data.cost_id}",
            code="COST_RESPONSIBILITY_NOT_RECONCILED",
        )
    deduction_fraction = _validate_share(
        input_data.eligible_net_sales_deduction_fraction,
        path=f"costs.{input_data.cost_id}.eligible_net_sales_deduction_fraction",
    )
    profit_share_cost_fraction = _validate_share(
        input_data.eligible_profit_share_cost_fraction,
        path=f"costs.{input_data.cost_id}.eligible_profit_share_cost_fraction",
    )

    base_date = parse_date(input_data.base_date)
    base_cost = quantity * unit_cost
    contingency_amount = base_cost * contingency_rate
    pre_escalation_cost = base_cost + contingency_amount

    project_flows: list[DatedCashFlow] = []
    for point in input_data.expenditure_curve:
        years = year_fraction(base_date, point.date, DayCountBasis.ACT_365F)
        escalation_factor = decimal_power(ONE + escalation_rate, years)
        amount = pre_escalation_cost * point.weight * escalation_factor
        project_flows.append(DatedCashFlow(point.date, amount, input_data.name))

    project_series = CashFlowSeries.from_iterable(
        f"cost:{input_data.cost_id}:project",
        currency,
        project_flows,
    )
    developer_series = project_series.scale(
        developer_share,
        series_id=f"cost:{input_data.cost_id}:developer",
    )
    government_series = project_series.scale(
        government_share,
        series_id=f"cost:{input_data.cost_id}:government",
    )
    deduction_series = project_series.scale(
        deduction_fraction,
        series_id=f"cost:{input_data.cost_id}:eligible_net_sales_deduction",
    )
    profit_share_cost_series = project_series.scale(
        profit_share_cost_fraction,
        series_id=f"cost:{input_data.cost_id}:eligible_profit_share_cost",
    )

    return CostItemResult(
        cost_id=input_data.cost_id,
        name=input_data.name,
        category=input_data.category,
        base_cost=base_cost,
        contingency_amount=contingency_amount,
        escalated_total_cost=project_series.total,
        developer_total_cost=developer_series.total,
        government_total_cost=government_series.total,
        eligible_net_sales_deduction_total=deduction_series.total,
        eligible_profit_share_cost_total=profit_share_cost_series.total,
        project_cost_series=project_series,
        developer_cost_series=developer_series,
        government_cost_series=government_series,
        eligible_net_sales_deduction_series=deduction_series,
        eligible_profit_share_cost_series=profit_share_cost_series,
        is_direct_cost=input_data.is_direct_cost,
    )


def calculate_costs(costs: Iterable[CostItemInput], *, currency: str) -> CostResult:
    items = tuple(calculate_cost_item(item, currency=currency) for item in costs)
    if not items:
        raise InputValidationError("At least one cost item is required.", path="costs")
    return CostResult(
        currency=currency,
        items=items,
        base_cost=sum((item.base_cost for item in items), ZERO),
        contingency_amount=sum((item.contingency_amount for item in items), ZERO),
        total_escalated_cost=sum((item.escalated_total_cost for item in items), ZERO),
        developer_total_cost=sum((item.developer_total_cost for item in items), ZERO),
        government_total_cost=sum((item.government_total_cost for item in items), ZERO),
        direct_cost=sum((item.escalated_total_cost for item in items if item.is_direct_cost), ZERO),
        developer_direct_cost=sum((item.developer_total_cost for item in items if item.is_direct_cost), ZERO),
        government_direct_cost=sum((item.government_total_cost for item in items if item.is_direct_cost), ZERO),
        project_cost_series=combine_series(
            "project:costs",
            currency,
            *(item.project_cost_series for item in items),
        ),
        developer_cost_series=combine_series(
            "developer:costs",
            currency,
            *(item.developer_cost_series for item in items),
        ),
        government_cost_series=combine_series(
            "government:costs",
            currency,
            *(item.government_cost_series for item in items),
        ),
        eligible_net_sales_deduction_series=combine_series(
            "project:eligible_net_sales_deductions",
            currency,
            *(item.eligible_net_sales_deduction_series for item in items),
        ),
        eligible_profit_share_cost_series=combine_series(
            "project:eligible_profit_share_costs",
            currency,
            *(item.eligible_profit_share_cost_series for item in items),
            description="Costs expressly allowed in the cash-profit partnership base.",
        ),
    )
