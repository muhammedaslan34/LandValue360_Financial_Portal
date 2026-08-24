"""Canonical release-2.1.1 defaults and idempotent local-development seed data.

The calculation kernel remains authoritative. These defaults only provide a
safe, transparent starting point for a new draft project and a published local
policy pack so a non-technical user can complete the browser workflow without
calling administrative APIs manually.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from landvalue360_common.versions import POLICY_MODEL_VERSION
from .json_tools import sha256_json
from .models import PolicyPack, PolicyPackVersion, User, Workspace, Organization, utc_now


def _iso_year_start(year: int) -> str:
    return date(year, 1, 1).isoformat()


def _future_year_start(base: date, years: int) -> str:
    return _iso_year_start(base.year + years)


def default_project_snapshot(*, today: date | None = None) -> dict[str, Any]:
    """Return a complete release-2.1.1 institutional project draft.

    New projects deliberately start with a representative urban programme,
    commercial products, cost plan, funding structure, risk register and
    tender assumptions.  Users edit or delete rows rather than constructing a
    blank model.  Percentages are stored as fractions and all sensitive
    numbers are strings to avoid binary floating-point ambiguity.
    """

    base = today or date.today()
    valuation_date = base.isoformat()
    y0 = _iso_year_start(base.year)
    y1 = _future_year_start(base, 1)
    y2 = _future_year_start(base, 2)
    y3 = _future_year_start(base, 3)
    y4 = _future_year_start(base, 4)

    def sales_curve() -> list[dict[str, str]]:
        return [
            {"date": y1, "weight": "0.15"},
            {"date": y2, "weight": "0.30"},
            {"date": y3, "weight": "0.35"},
            {"date": y4, "weight": "0.20"},
        ]

    def expenditure_curve() -> list[dict[str, str]]:
        return [
            {"date": y0, "weight": "0.10"},
            {"date": y1, "weight": "0.35"},
            {"date": y2, "weight": "0.35"},
            {"date": y3, "weight": "0.20"},
        ]

    products = [
        ("RESIDENTIAL", "Residential", "0.68", "0.82", "1440"),
        ("RETAIL", "Retail", "0.12", "0.88", "2160"),
        ("OFFICE", "Office", "0.12", "0.84", "1740"),
        ("HOSPITALITY", "Hospitality / serviced units", "0.08", "0.78", "1920"),
    ]
    planning_products = [
        {
            "product_id": pid,
            "name": name,
            "area_method": "GFA_ALLOCATION",
            "is_sellable": True,
            "efficiency": efficiency,
            "gfa_allocation_share": share,
        }
        for pid, name, share, efficiency, _ in products
    ]
    commercial_products = [
        {
            "product_id": pid,
            "name": name,
            "quantity_basis": "SELLABLE_AREA_SQM",
            "quantity_unit": "sqm",
            "unit_price": price,
            "description": "",
            "market_growth_rate": "0",
            "pricing_notes": "",
            "payment_plan_id": "CUSTOM",
            "construction_cost_per_sqm": {"RESIDENTIAL":"560","RETAIL":"720","OFFICE":"640","HOSPITALITY":"850"}.get(pid,"600"),
            "construction_cost_base_date": valuation_date,
            "construction_escalation_rate": "0.04",
            "construction_contingency_rate": "0.07",
            "sales_curve_type": "S_CURVE",
            "sales_start_month": 7,
            "sales_duration_months": 36,
            "construction_curve_type": "BELL",
            "construction_start_month": 1,
            "construction_duration_months": 30,
            "commercial_discount_rate": "0.03",
            "buyer_incentive_rate": "0.01",
            "refund_rate": "0.01",
            "buyer_incentive_net_sales_deduction_fraction": "1",
            "refund_net_sales_deduction_fraction": "1",
            "eligible_profit_share_revenue_fraction": "1",
            "sales_curve": sales_curve(),
            "collection_rules": [
                {"lag_days": 0, "weight": "0.50"},
                {"lag_days": 180, "weight": "0.25"},
                {"lag_days": 365, "weight": "0.25"},
            ],
        }
        for pid, name, _, _, price in products
    ]

    def cost(
        cost_id: str,
        name: str,
        category: str,
        quantity: str,
        unit_cost: str,
        *,
        direct: bool,
        contingency: str = "0.07",
        developer_share: str = "1",
        government_share: str = "0",
        calculation_method: str = "LEGACY_QUANTITY_X_RATE",
        **method_fields: Any,
    ) -> dict[str, Any]:
        return {
            "cost_id": cost_id,
            "name": name,
            "category": category,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "base_date": valuation_date,
            "escalation_rate": "0.04",
            "contingency_rate": contingency,
            "developer_responsibility_share": developer_share,
            "government_responsibility_share": government_share,
            "eligible_net_sales_deduction_fraction": "0",
            "eligible_profit_share_cost_fraction": "1",
            "is_direct_cost": direct,
            "expenditure_curve": expenditure_curve(),
            "calculation_method": calculation_method,
            **method_fields,
        }

    costs = [
        cost("STRUCTURE", "Building structure and envelope", "DIRECT_CONSTRUCTION", "22500", "310", direct=True, calculation_method="COMPUTED_QUANTITY_X_RATE", quantity_basis="TOTAL_GFA_SQM", basis_multiplier="1", basis_addition="0", calculation_note="Total project GFA × structural rate.", covered_by_product_construction=True),
        cost("MEP", "Mechanical, electrical and plumbing", "DIRECT_CONSTRUCTION", "22500", "120", direct=True, calculation_method="COMPUTED_QUANTITY_X_RATE", quantity_basis="TOTAL_GFA_SQM", basis_multiplier="1", basis_addition="0", calculation_note="Total project GFA × MEP rate.", covered_by_product_construction=True),
        cost("FITOUT", "Internal finishes and fit-out", "DIRECT_CONSTRUCTION", "22500", "135", direct=True, calculation_method="COMPUTED_QUANTITY_X_RATE", quantity_basis="TOTAL_GFA_SQM", basis_multiplier="1", basis_addition="0", calculation_note="Total project GFA × fit-out rate.", covered_by_product_construction=True),
        cost("INFRA_INTERNAL", "Internal roads and infrastructure", "INFRASTRUCTURE", "2200", "250", direct=True, contingency="0.10", calculation_method="COMPUTED_QUANTITY_X_RATE", quantity_basis="LAND_USE_AREA_SQM", basis_reference_id="ROADS", basis_multiplier="1", basis_addition="0", calculation_note="Computed roads/circulation area × infrastructure rate."),
        cost("INFRA_EXTERNAL", "External utility connections", "INFRASTRUCTURE", "1", "400000", direct=True, contingency="0.15", calculation_method="FIXED_AMOUNT", fixed_amount="400000", calculation_note="Fixed allowance pending authority quotations."),
        cost("PUBLIC_FACILITIES", "Public-service buildings and obligations", "PUBLIC_FACILITIES", "1000", "750", direct=True, contingency="0.10", calculation_method="COMPUTED_QUANTITY_X_RATE", quantity_basis="LAND_USE_AREA_SQM", basis_reference_id="PUBLIC", basis_multiplier="1", basis_addition="0", calculation_note="Public-facility land-use area × adopted obligation rate."),
        cost("DESIGN", "Design, engineering and specialist studies", "PROFESSIONAL_FEES", "1", "650000", direct=False, contingency="0.05", calculation_method="PERCENT_OF_COST", percentage_rate="0.045", percentage_basis="HARD_COST", percentage_basis_stage="BASE_COST", calculation_note="Percentage of non-percentage hard-cost base."),
        cost("PERMITS", "Permits, authorities and statutory fees", "AUTHORITIES", "1", "300000", direct=False, contingency="0.05", calculation_method="MANUAL_AMOUNT", fixed_amount="300000", calculation_note="Manual statutory allowance supported by authority evidence."),
        cost("PM", "Project management, supervision and administration", "PROJECT_MANAGEMENT", "1", "850000", direct=False, contingency="0.05", calculation_method="PERCENT_OF_COST", percentage_rate="0.059", percentage_basis="HARD_COST", percentage_basis_stage="BASE_COST", calculation_note="Percentage of non-percentage hard-cost base."),
        cost("MARKETING", "Marketing, sales and brokerage", "SALES_MARKETING", "1", "600000", direct=False, contingency="0.05", calculation_method="FIXED_AMOUNT", fixed_amount="600000", calculation_note="Fixed initial marketing and sales allowance; amend to evidence."),
    ]

    return {
        "project_id": "NEW-PROJECT",
        "project_name": "New Development Project",
        "reporting_currency": "USD",
        "valuation_date": valuation_date,
        "land_value_baseline": "2500000",
        "reference_land_value_basis": "GROSS",
        "reference_land_value_area_sqm": "10000",
        "reference_land_value_per_sqm": "250",
        "reference_land_value_total": "2500000",
        "reference_land_value_legacy_derived": False,
        "construction_cost_entry_mode": "PRODUCT",
        "developer_product_cost_mode": "UNIT_RATE",
        "developer_product_cost_plans": {
            pid: {
                "mode": "WORK_PACKAGES",
                "lines": [
                    {"line_id": "STRUCTURE", "name": "Structure and concrete", "category": "STRUCTURE", "quantity_basis": "PRODUCT_GFA_SQM", "quantity_multiplier": "1", "unit_cost": str((Decimal({"RESIDENTIAL":"560","RETAIL":"720","OFFICE":"640","HOSPITALITY":"850"}.get(pid,"600")) * Decimal("0.35")).quantize(Decimal("0.01"))), "contingency_rate": "0.07", "escalation_rate": "0.04", "start_month": 1, "duration_months": 18, "enabled": True},
                    {"line_id": "MEP", "name": "Mechanical, electrical and plumbing", "category": "MEP", "quantity_basis": "PRODUCT_GFA_SQM", "quantity_multiplier": "1", "unit_cost": str((Decimal({"RESIDENTIAL":"560","RETAIL":"720","OFFICE":"640","HOSPITALITY":"850"}.get(pid,"600")) * Decimal("0.20")).quantize(Decimal("0.01"))), "contingency_rate": "0.07", "escalation_rate": "0.04", "start_month": 4, "duration_months": 22, "enabled": True},
                    {"line_id": "ENVELOPE", "name": "Envelope and façade", "category": "ENVELOPE", "quantity_basis": "PRODUCT_GFA_SQM", "quantity_multiplier": "1", "unit_cost": str((Decimal({"RESIDENTIAL":"560","RETAIL":"720","OFFICE":"640","HOSPITALITY":"850"}.get(pid,"600")) * Decimal("0.15")).quantize(Decimal("0.01"))), "contingency_rate": "0.07", "escalation_rate": "0.04", "start_month": 8, "duration_months": 18, "enabled": True},
                    {"line_id": "FINISHES", "name": "Finishes and fit-out", "category": "FINISHES", "quantity_basis": "PRODUCT_GFA_SQM", "quantity_multiplier": "1", "unit_cost": str((Decimal({"RESIDENTIAL":"560","RETAIL":"720","OFFICE":"640","HOSPITALITY":"850"}.get(pid,"600")) * Decimal("0.25")).quantize(Decimal("0.01"))), "contingency_rate": "0.07", "escalation_rate": "0.04", "start_month": 12, "duration_months": 18, "enabled": True},
                    {"line_id": "PRODUCT_EXTERNAL", "name": "Product-specific external works", "category": "PRODUCT_EXTERNAL", "quantity_basis": "PRODUCT_GFA_SQM", "quantity_multiplier": "1", "unit_cost": str((Decimal({"RESIDENTIAL":"560","RETAIL":"720","OFFICE":"640","HOSPITALITY":"850"}.get(pid,"600")) * Decimal("0.05")).quantize(Decimal("0.01"))), "contingency_rate": "0.07", "escalation_rate": "0.04", "start_month": 18, "duration_months": 12, "enabled": True},
                ],
            }
            for pid, *_ in products
        },
        "developer_market_strategy": {"pricing_basis": "PER_PRODUCT", "market_notes": "", "absorption_notes": "", "default_payment_plan_id": "CUSTOM"},
        "developer_cost_strategy": {"procurement_savings_target": "0.05", "opening_negotiation_discount": "0.10", "final_negotiation_discount": "0.05"},
        "valuation_context": {
            "basis_of_value": "MARKET_VALUE",
            "purpose": "DEVELOPMENT_DECISION_SUPPORT",
            "cost_estimate_class": "CLASS_4",
            "design_maturity": "CONCEPT",
            "measurement_basis": "LOCAL",
            "valuation_standard": "IVS_RICS_REFERENCE",
        },
        "planning": {
            "gross_land_area_sqm": "10000",
            "excluded_land_area_sqm": "0",
            "far_land_basis": "GROSS",
            "far": "2.25",
            "bcr_land_basis": "GROSS",
            "bcr": "0.35",
            "land_uses": [
                {"land_use_id": "INVESTMENT", "name": "Investment plots", "share": "0.55"},
                {"land_use_id": "ROADS", "name": "Roads and circulation", "share": "0.22"},
                {"land_use_id": "GREEN", "name": "Green and open space", "share": "0.13"},
                {"land_use_id": "PUBLIC", "name": "Public facilities", "share": "0.10"},
            ],
        },
        "planning_products": planning_products,
        "products": commercial_products,
        "costs": costs,
        "funding": {
            "opening_cash": "3500000",
            "committed_additional_equity": "3500000",
            "committed_equity": "3500000",
            "committed_equity_is_additional": True,
            "committed_financing": "8000000",
        },
        "finance_model": {
            "enabled": True,
            "annual_interest_rate": "0.08",
            "upfront_fee_rate": "0.01",
            "commitment_fee_rate": "0.005",
            "cash_sweep_share": "1",
            "capitalize_interest": True,
            "force_terminal_repayment": True,
            "minimum_cash_balance": "0",
            "funding_draw_order": "PRO_RATA",
            "spend_policy": "HYBRID",
            "hybrid_minimum_execution_share": "0.35",
            "future_cost_reserve_share": "0.25",
            "allow_negative_cash": False,
            "defer_contractual_payments": True,
            "maximum_extension_months": 120,
            "maximum_monthly_execution_share": "0.15",
            "maximum_monthly_execution_amount": "0",
        },
        "risk_register": {
            "items": [
                {"risk_id": "R-PLANNING", "title": "Planning or permitting delay", "category": "PLANNING", "risk_type": "PROJECT", "probability": 3, "impact": 4, "mitigation_effectiveness": "0.35", "owner": "Shared", "allocation": "SHARED", "mitigation": "Early authority engagement and milestone conditions.", "contract_clause_required": True, "financial_driver": "CONSTRUCTION_DELAY"},
                {"risk_id": "R-MARKET", "title": "Sales-price and absorption underperformance", "category": "MARKET", "risk_type": "PROJECT", "probability": 3, "impact": 5, "mitigation_effectiveness": "0.25", "owner": "Developer", "allocation": "DEVELOPER", "mitigation": "Independent market study, phased release and price monitoring.", "contract_clause_required": False, "financial_driver": "SALES_PRICE"},
                {"risk_id": "R-COST", "title": "Construction cost escalation", "category": "COST", "risk_type": "PROJECT", "probability": 4, "impact": 4, "mitigation_effectiveness": "0.30", "owner": "Developer", "allocation": "DEVELOPER", "mitigation": "BOQ maturity, procurement strategy and controlled contingencies.", "contract_clause_required": True, "financial_driver": "DEVELOPMENT_COST"},
                {"risk_id": "R-FINANCE", "title": "Financing availability or interest-rate stress", "category": "FINANCE", "risk_type": "PROJECT", "probability": 3, "impact": 4, "mitigation_effectiveness": "0.30", "owner": "Developer", "allocation": "DEVELOPER", "mitigation": "Committed facilities, funding covenant and interest reserve.", "contract_clause_required": True, "financial_driver": "INTEREST_RATE"},
                {"risk_id": "R-TITLE", "title": "Land-title or access uncertainty", "category": "LEGAL", "risk_type": "PROJECT", "probability": 2, "impact": 5, "mitigation_effectiveness": "0.60", "owner": "Landowner", "allocation": "GOVERNMENT", "mitigation": "Verified title, vacant possession and access condition precedent.", "contract_clause_required": True, "financial_driver": "NONE"},
                {"risk_id": "R-MODEL", "title": "Unverified market or cost assumptions", "category": "MODEL", "risk_type": "MODEL", "probability": 3, "impact": 4, "mitigation_effectiveness": "0.45", "owner": "Model owner", "allocation": "SHARED", "mitigation": "Evidence room, assumption approval and independent model review.", "contract_clause_required": False, "financial_driver": "NONE"},
            ]
        },
        "sensitivity_studio": {
            "metric": "developer_npv",
            "price_shocks": ["-0.15", "-0.10", "-0.05", "0", "0.05", "0.10"],
            "cost_shocks": ["-0.10", "-0.05", "0", "0.05", "0.10", "0.15"],
            "delay_months": [0, 6, 12],
            "share_shocks": ["-0.03", "-0.01", "0", "0.01", "0.03"],
            "interest_shocks": ["-0.02", "0", "0.02", "0.04"],
            "monte_carlo": {"iterations": 100, "seed": 360, "price_distribution": {"type": "TRIANGULAR", "low": "-0.15", "mode": "0", "high": "0.10"}, "cost_distribution": {"type": "TRIANGULAR", "low": "-0.05", "mode": "0.05", "high": "0.20"}, "delay_distribution": {"type": "TRIANGULAR", "low": "0", "mode": "3", "high": "12"}},
        },
        "tender_studio": {
            "criteria_weights": {"financial": "0.45", "technical": "0.25", "risk_guarantees": "0.20", "integrity": "0.10"},
            "minimum_total_score": "70",
            "minimum_technical_score": "60",
            "bids": [
                {"bid_id": "BID-A", "bidder": "Bidder A", "method": "GROSS_SALES", "share_rate": "0.15", "upfront_amount": "0", "completion_months": 48, "committed_equity": "4000000", "committed_financing": "8000000", "technical_score": "82", "experience_score": "80", "guarantees_score": "75", "integrity_score": "90", "price_multiplier": "1", "cost_multiplier": "1", "annual_interest_rate": "0.08"},
                {"bid_id": "BID-B", "bidder": "Bidder B", "method": "HYBRID", "share_rate": "0.11", "upfront_amount": "1500000", "completion_months": 42, "committed_equity": "5000000", "committed_financing": "7500000", "technical_score": "86", "experience_score": "88", "guarantees_score": "82", "integrity_score": "92", "price_multiplier": "1", "cost_multiplier": "1.02", "annual_interest_rate": "0.075"},
            ],
        },
        "landowner_studio": {
            "horizon_months": 72,
            "auto_extend_horizon": True,
            "allow_negative_cash": False,
            "use_committed_financing": True,
            "initial_cash": "3500000",
            "other_cost_curve_type": "S_CURVE",
            "other_cost_start_month": 1,
            "other_cost_duration_months": 36,
            "land_value_recovery_share": "1",
            "upfront_amount": "2500000",
            "upfront_payment_month": 1,
            "upfront_search_cap": "5000000",
            "hybrid_upfront_amount": "875000",
            "hybrid_upfront_payment_month": 1,
            "hybrid_variable_basis": "GROSS_SALES",
            "minimum_guarantee_amount": "2500000",
            "minimum_guarantee_payment_month": 48,
            "minimum_guarantee_underlying_method": "GROSS_SALES",
            "minimum_guarantee_underlying_share": "0.05",
            "minimum_guarantee_search_cap": "20000000",
            "distribution_reserve_months": 12,
            "remaining_cost_reserve_share": "0.20",
            "distribution_policy": {
                "enabled": True,
                "frequency_months": 12,
                "reserve_months": 12,
                "remaining_cost_reserve_share": "0.20",
                "distribution_share": "1",
                "landowner_share": "0",
                "prohibit_while_debt_outstanding": True
            },
            "contract_methods": ["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"],
            "recommendation_objective": "BALANCED"
        },
        "negotiation_studio": {
            "rows": [
                {"row_id": "N1", "label": "Model minimum", "method": "GROSS_SALES", "share_rate": "0.10", "upfront_amount": "0"},
                {"row_id": "N2", "label": "Balanced discussion", "method": "GROSS_SALES", "share_rate": "0.15", "upfront_amount": "0"},
                {"row_id": "N3", "label": "Upper discussion", "method": "GROSS_SALES", "share_rate": "0.20", "upfront_amount": "0"},
            ]
        },
        "partnership": {
            "method": "GROSS_SALES",
            "share_rate": "0.10",
            "approved_selection": "MANUAL",
            "manual_share": "0.10",
            "net_deduction_treatment": "CUMULATIVE_CARRY_FORWARD",
            "hybrid_variable_basis": "GROSS_SALES",
            "upfront_payments": [],
        },
    }


def project_template_catalog(*, today: date | None = None) -> list[dict[str, Any]]:
    """Return complete governed project templates for non-technical users.

    Every template is a full project snapshot, not a partial preset.  Users can
    immediately calculate it and then delete or amend products, costs and
    policies.  The catalog is intentionally deterministic so template-based
    projects remain reproducible and auditable.
    """

    base = today or date.today()
    mixed = default_project_snapshot(today=base)

    economic = deepcopy(mixed)
    economic["project_id"] = "TEMPLATE-ECONOMIC-HOUSING"
    economic["project_name"] = "Economic Housing Development"
    economic["land_value_baseline"] = "7000000"
    economic["planning"].update({
        "gross_land_area_sqm": "50000",
        "far": "2.40",
        "bcr": "0.32",
        "land_uses": [
            {"land_use_id": "INVESTMENT", "name": "Residential investment plots", "share": "0.60"},
            {"land_use_id": "ROADS", "name": "Roads and circulation", "share": "0.20"},
            {"land_use_id": "GREEN", "name": "Green and open space", "share": "0.12"},
            {"land_use_id": "PUBLIC", "name": "Schools and public services", "share": "0.08"},
        ],
    })
    economic_specs = [
        ("AFFORDABLE_1BR", "Affordable 1-bedroom", "0.25", "0.84", "850", "390"),
        ("AFFORDABLE_2BR", "Affordable 2-bedroom", "0.45", "0.83", "980", "440"),
        ("FAMILY_3BR", "Family 3-bedroom", "0.25", "0.82", "1120", "490"),
        ("NEIGHBORHOOD_RETAIL", "Neighborhood retail", "0.05", "0.88", "1550", "610"),
    ]
    economic["planning_products"] = [
        {"product_id": pid, "name": name, "area_method": "GFA_ALLOCATION", "is_sellable": True, "efficiency": efficiency, "gfa_allocation_share": share}
        for pid, name, share, efficiency, _, _ in economic_specs
    ]
    economic["products"] = [
        {
            "product_id": pid,
            "name": name,
            "quantity_basis": "SELLABLE_AREA_SQM",
            "quantity_unit": "sqm",
            "unit_price": price,
            "construction_cost_per_sqm": cost,
            "sales_curve_type": "S_CURVE",
            "sales_start_month": 4,
            "sales_duration_months": 42,
            "construction_curve_type": "S_CURVE",
            "construction_start_month": 1,
            "construction_duration_months": 40,
            "commercial_discount_rate": "0.02",
            "buyer_incentive_rate": "0.01",
            "refund_rate": "0.01",
            "buyer_incentive_net_sales_deduction_fraction": "1",
            "refund_net_sales_deduction_fraction": "1",
            "eligible_profit_share_revenue_fraction": "1",
            "sales_curve": deepcopy(mixed["products"][0]["sales_curve"]),
            "collection_rules": [
                {"lag_days": 0, "weight": "0.20"},
                {"lag_days": 180, "weight": "0.30"},
                {"lag_days": 540, "weight": "0.50", "depends_on_completion": True},
            ],
            "construction_deferrable": True,
            "construction_priority": 50,
        }
        for pid, name, _, _, price, cost in economic_specs
    ]
    economic["funding"] = {"committed_equity": "9000000", "committed_financing": "18000000"}
    economic["finance_model"].update({"maximum_extension_months": 180, "maximum_monthly_execution_share": "0.10"})
    economic["landowner_studio"].update({"initial_cash": "9000000", "horizon_months": 96, "upfront_search_cap": "12000000", "recommendation_objective": "BALANCED"})

    resort = deepcopy(mixed)
    resort["project_id"] = "TEMPLATE-RESORT"
    resort["project_name"] = "Integrated Resort and Second-Home Development"
    resort["land_value_baseline"] = "12000000"
    resort["planning"].update({
        "gross_land_area_sqm": "120000",
        "far": "0.65",
        "bcr": "0.18",
        "land_uses": [
            {"land_use_id": "INVESTMENT", "name": "Hospitality and residential plots", "share": "0.38"},
            {"land_use_id": "ROADS", "name": "Roads and circulation", "share": "0.14"},
            {"land_use_id": "GREEN", "name": "Landscape, trails and recreation", "share": "0.38"},
            {"land_use_id": "PUBLIC", "name": "Community and public facilities", "share": "0.10"},
        ],
    })
    resort_specs = [
        ("VILLAS", "Detached villas", "0.35", "0.90", "2400", "980"),
        ("CHALETS", "Chalets and apartments", "0.30", "0.84", "1900", "790"),
        ("HOTEL", "Hotel and serviced suites", "0.25", "0.76", "2250", "1120"),
        ("RETAIL_FNB", "Retail and food & beverage", "0.10", "0.86", "2600", "900"),
    ]
    resort["planning_products"] = [
        {"product_id": pid, "name": name, "area_method": "GFA_ALLOCATION", "is_sellable": True, "efficiency": efficiency, "gfa_allocation_share": share}
        for pid, name, share, efficiency, _, _ in resort_specs
    ]
    resort["products"] = [
        {
            "product_id": pid,
            "name": name,
            "quantity_basis": "SELLABLE_AREA_SQM",
            "quantity_unit": "sqm",
            "unit_price": price,
            "construction_cost_per_sqm": cost,
            "sales_curve_type": "DELAYED_RAMP" if pid == "HOTEL" else "S_CURVE",
            "sales_start_month": 10 if pid == "HOTEL" else 7,
            "sales_duration_months": 48,
            "construction_curve_type": "BELL",
            "construction_start_month": 1,
            "construction_duration_months": 42 if pid == "HOTEL" else 36,
            "commercial_discount_rate": "0.04",
            "buyer_incentive_rate": "0.01",
            "refund_rate": "0.01",
            "buyer_incentive_net_sales_deduction_fraction": "1",
            "refund_net_sales_deduction_fraction": "1",
            "eligible_profit_share_revenue_fraction": "1",
            "sales_curve": deepcopy(mixed["products"][0]["sales_curve"]),
            "collection_rules": [
                {"lag_days": 0, "weight": "0.30"},
                {"lag_days": 365, "weight": "0.30"},
                {"lag_days": 730, "weight": "0.40", "depends_on_completion": True},
            ],
            "construction_deferrable": True,
            "construction_priority": 50,
        }
        for pid, name, _, _, price, cost in resort_specs
    ]
    resort["funding"] = {"committed_equity": "14000000", "committed_financing": "25000000"}
    resort["finance_model"].update({"maximum_extension_months": 180, "maximum_monthly_execution_share": "0.08"})
    resort["landowner_studio"].update({"initial_cash": "14000000", "horizon_months": 120, "upfront_search_cap": "20000000", "recommendation_objective": "BALANCED"})

    urban = deepcopy(mixed)
    urban["project_id"] = "TEMPLATE-URBAN-REDEVELOPMENT"
    urban["project_name"] = "Public-Land Urban Redevelopment"
    urban["land_value_baseline"] = "18000000"
    urban["planning"].update({"gross_land_area_sqm": "30000", "far": "3.20", "bcr": "0.42"})
    urban["funding"] = {"committed_equity": "12000000", "committed_financing": "28000000"}
    urban["finance_model"].update({"maximum_extension_months": 144, "maximum_monthly_execution_share": "0.12"})
    urban["landowner_studio"].update({"initial_cash": "12000000", "horizon_months": 96, "upfront_search_cap": "25000000", "recommendation_objective": "MAX_PUBLIC_NPV"})

    catalog = [
        ("MIXED_USE", "مشروع متعدد الاستخدامات", "Mixed-use development", "سكني وتجاري ومكاتب وضيافة مع كلف ومخاطر وتمويل افتراضي كامل.", "Residential, retail, office and hospitality with a complete default cost, risk and funding plan.", mixed),
        ("ECONOMIC_HOUSING", "إسكان اقتصادي وعائلي", "Economic and family housing", "مزيج شقق اقتصادية وعائلية مع تحصيلات ممتدة وتمويل ذاتي مرن.", "Affordable and family apartments with phased collections and flexible self-funding.", economic),
        ("RESORT", "منتجع وسكن ثانٍ", "Resort and second homes", "فلل وشاليهات وفندق وتجاري ضمن أرض كبيرة وكثافة منخفضة.", "Villas, chalets, hotel and retail over a low-density resort site.", resort),
        ("URBAN_REDEVELOPMENT", "إعادة تطوير أرض عامة", "Public-land urban redevelopment", "نموذج كثيف للجهات العامة يركز على المقابل العادل والطرح التنافسي.", "A higher-density public-land model focused on fair consideration and competitive tendering.", urban),
    ]
    return [
        {
            "template_id": template_id,
            "name_ar": name_ar,
            "name_en": name_en,
            "description_ar": description_ar,
            "description_en": description_en,
            "snapshot": snapshot,
        }
        for template_id, name_ar, name_en, description_ar, description_en, snapshot in catalog
    ]

def default_policy_snapshot(*, effective_date: date | None = None) -> dict[str, Any]:
    """Return the execution/project policy only.

    Valuation and negotiation assumptions are deliberately governed by
    :func:`default_valuation_policy_snapshot` so a calculation can record and
    audit the two policy families independently.
    """

    base = effective_date or date.today()
    snapshot = {
        "policy_id": "LV360-INSTITUTIONAL-BASELINE",
        "policy_guidance": {"product_scope": "BOTH", "policy_type": "PROJECT"},
        "version": POLICY_MODEL_VERSION,
        "effective_date": base.isoformat(),
        "funding_policy": {
            "equity_commitment_mode": "MANUAL",
            "fixed_equity_direct_cost_share": "0.10",
        },
        "financial_constraints": {
            "discount_rate": "0.12",
            "government_discount_rate": "0.12",
            "minimum_project_npv": "0",
            "minimum_developer_irr": "0.18",
            "target_developer_irr": "0.22",
            "minimum_profit_on_cost": "0.20",
            "minimum_developer_multiple": "1.50",
            "minimum_developer_npv": "0",
            "available_equity_direct_cost_share": "0.10",
            "allowed_financing_direct_cost_share": "0.50",
            "maximum_payback_years": "8",
            "maximum_funding_gap": "0",
        },
        "finance_constraints": {
            "minimum_equity_irr": "0.18",
            "minimum_dscr": "1.20",
            "maximum_ltc": "0.70",
            "maximum_ltv": "0.65",
            "maximum_structured_funding_gap": "0",
        },
        "procurement_policy": {
            "opening_discount_rate": "0.08",
            "target_discount_rate": "0.04",
            "minimum_retained_contingency_rate": "0.03",
            "classification": "VERSIONED_DEVELOPER_PROCUREMENT_GUIDANCE",
        },
        "distribution_policy": {
            "enabled": True,
            "frequency_code": "ANNUAL",
            "frequency_months": 12,
            "first_distribution_month": 12,
            "reserve_basis": "ALL_REMAINING_COSTS",
            "reserve_months": 12,
            "future_cost_reserve_share": "0.25",
            "remaining_cost_reserve_share": "0.25",
            "minimum_operating_cash": "0",
            "distribution_share": "1",
            "allocation_method": "CONTRACTUAL_ACCRUAL_FIRST",
            "residual_landowner_share": "0",
            "contractual_payment_timing": "DISTRIBUTION_DATES",
            "settle_prior_obligations_before_distribution": True,
            "recover_developer_advances_before_landowner_cash": True,
            "return_capital_first": False,
            "prohibit_while_debt_outstanding": True,
            "prohibit_before_completion": False,
            "prohibit_with_funding_gap": True,
            "prohibit_with_mandatory_shortfall": True,
            "minimum_distribution_amount": "0",
            "carry_forward_undistributed_cash": True,
        },
        "valuation_policy": {
            "target_developer_profit_on_cost": "0.25",
            "institutional_data_quality_threshold": "85",
            "feasibility_data_quality_threshold": "70",
            "minimum_reconciliation_methods": 2,
            "quality_adjusted_weights": True,
        },
        "risk_policy": {
            "maximum_residual_risk_score": "55",
            "maximum_critical_residual_risks": 0,
            "maximum_high_residual_risks": 3,
            "minimum_mitigation_coverage": "0.80",
            "monte_carlo_max_iterations": 5000,
            "monte_carlo_default_iterations": 100,
        },
        "tender_policy": {
            "minimum_tender_readiness_score": "70",
            "minimum_bid_total_score": "70",
            "minimum_bid_technical_score": "60",
            "disqualify_unfunded_bids": True,
            "disqualify_financially_infeasible_bids": True,
        },
        "share_policy": {
            "policy_minimum_share": "0.05",
            "policy_maximum_share": "0.50",
            "search_tolerance": "0.00001",
            "minimum_government_value_npv": "0",
        },
        "public_value_adjustment": {
            "high_confidence_factor": "0.95",
            "moderate_confidence_factor": "0.80",
            "low_confidence_factor": "0.60",
            "maximum_risk_haircut": "0.35",
            "classification": "VERSIONED_POLICY_HEURISTIC_NOT_MARKET_VALUE",
        },
        "fair_consideration_policy": {
            "risk_adjusted_capacity_factor": "0.45",
            "balanced_position_factor": "0.59",
            "developer_safety_buffer": "0.00",
            "minimum_capacity_factor": "0.30",
            "balanced_position_minimum": "0.45",
            "balanced_position_maximum": "0.70",
            "classification": "EXPLICIT_VERSIONED_NEGOTIATION_POLICY",
        },
        "policy_guidance": {
            "product_scope": "BOTH",
            "policy_type": "PROJECT",
            "funding_policy.equity_commitment_mode": {"unit": "policy choice", "definition": "Selects either the locked institutional 10% equity capacity rule or a verified manual committed-equity amount.", "rationale": "Separates screening policy from sponsor-specific evidence and prevents hidden changes to equity capacity.", "basis": "Approved institutional funding policy; Manual mode requires documented sponsor commitment."},
            "funding_policy.fixed_equity_direct_cost_share": {"unit": "% of developer direct cost", "definition": "Locked baseline recognized equity capacity equal to 10% of developer direct cost.", "rationale": "Provides a consistent conservative screening assumption where verified sponsor evidence is not yet available.", "basis": "Institutional baseline selected by the model owner; not a universal market rule."},
            "financial_constraints.discount_rate": {"unit": "% p.a.", "definition": "Annual rate used to convert future project cash flows into present value.", "rationale": "Reflects time value of money and project risk. It is a policy parameter, not a project input.", "basis": "Institutional investment policy; must be calibrated to market, country and project risk."},
            "financial_constraints.government_discount_rate": {"unit": "% p.a.", "definition": "Annual rate used to discount public-authority cash receipts.", "rationale": "Allows comparison of early and deferred public consideration on a consistent present-value basis.", "basis": "Public-sector valuation policy and timing risk."},
            "financial_constraints.minimum_project_npv": {"unit": "reporting currency", "definition": "Minimum acceptable unlevered project net present value.", "rationale": "Prevents approval of projects that destroy value before financing and land-partnership allocation.", "basis": "Core capital-budgeting principle: NPV should not be below the approved threshold."},
            "financial_constraints.minimum_developer_irr": {"unit": "% p.a.", "definition": "Lowest acceptable annualized return to the developer under the unlevered development cash flow.", "rationale": "Defines the technical ceiling for landowner consideration; below this level the project is considered insufficiently investable.", "basis": "Approved developer-return policy, adjusted for market and project risk."},
            "financial_constraints.target_developer_irr": {"unit": "% p.a.", "definition": "Target attractive annualized developer return used to identify the balanced landowner share.", "rationale": "Avoids setting the recommended share at the absolute viability edge.", "basis": "Institutional negotiation policy and observable required returns."},
            "financial_constraints.minimum_profit_on_cost": {"unit": "% of developer cost", "definition": "Minimum nominal developer profit divided by total developer cost.", "rationale": "Complements IRR by testing the absolute development margin, independent of timing effects.", "basis": "Development-feasibility and lender/investment-committee practice."},
            "financial_constraints.minimum_developer_multiple": {"unit": "x", "definition": "Minimum gross cash inflow divided by developer capital outflow.", "rationale": "Prevents a project with a timing-driven IRR from passing despite insufficient absolute cash return.", "basis": "Institutional multiple-on-invested-capital policy."},
            "financial_constraints.minimum_developer_npv": {"unit": "reporting currency", "definition": "Minimum present-value surplus attributable to the developer.", "rationale": "Ensures the developer receives positive economic value after the approved landowner consideration.", "basis": "Capital-budgeting policy at the institutional discount rate."},
            "financial_constraints.available_equity_direct_cost_share": {"unit": "% of direct cost", "definition": "Baseline equity capacity assumed available to the developer.", "rationale": "Tests whether the project can be funded without an unsupported cash deficit.", "basis": "Advisory screening rule; replace it with verified sponsor evidence before any external reliance."},
            "financial_constraints.allowed_financing_direct_cost_share": {"unit": "% of direct cost", "definition": "Maximum financing capacity recognized by the screening model.", "rationale": "Prevents the model from assuming unlimited debt or external funding.", "basis": "Financing policy and market bankability limits."},
            "financial_constraints.maximum_payback_years": {"unit": "years", "definition": "Latest acceptable time for cumulative developer cash flow to recover invested capital.", "rationale": "Controls duration and liquidity risk that may not be apparent from NPV alone.", "basis": "Institutional investment-duration policy."},
            "finance_constraints.minimum_equity_irr": {"unit": "% p.a.", "definition": "Minimum return on the developer's actual equity contributions after debt service.", "rationale": "Tests sponsor attractiveness under the selected capital structure.", "basis": "Equity-investor underwriting policy."},
            "finance_constraints.minimum_dscr": {"unit": "x", "definition": "Minimum debt-service coverage ratio.", "rationale": "Measures whether project cash generation is sufficient to service debt.", "basis": "Project-finance and lender covenant practice."},
            "finance_constraints.maximum_ltc": {"unit": "%", "definition": "Maximum senior debt relative to eligible development cost.", "rationale": "Limits leverage and construction-completion risk.", "basis": "Lender underwriting policy."},
            "finance_constraints.maximum_ltv": {"unit": "%", "definition": "Maximum debt relative to the value proxy used by the model.", "rationale": "Provides an asset-value leverage check in addition to LTC.", "basis": "Lender underwriting policy."},
            "risk_policy.maximum_residual_risk_score": {"unit": "/100", "definition": "Maximum acceptable aggregate risk after mitigation.", "rationale": "Prevents a financially positive project from being labelled tender-ready when material risks remain uncontrolled.", "basis": "Institutional risk appetite approved by the model owner."},
            "tender_policy.minimum_tender_readiness_score": {"unit": "/100", "definition": "Minimum composite readiness score before a project is recommended for tender.", "rationale": "Combines feasibility, evidence, risk, valuation, legal and procurement preparation.", "basis": "Procurement-governance policy; it does not replace local procurement law."},
            "financial_constraints.maximum_funding_gap": {"unit": "reporting currency", "definition": "Maximum unstructured funding deficit permitted after recognized equity and financing capacity.", "rationale": "A positive unsupported gap means the project cannot be executed with the stated funding plan.", "basis": "Institutional liquidity policy; zero is the default for official approvals."},
            "finance_constraints.maximum_structured_funding_gap": {"unit": "reporting currency", "definition": "Maximum residual funding deficit after applying the detailed debt-and-equity structure.", "rationale": "Prevents an apparently attractive return from passing when the financing schedule still contains an unfunded deficit.", "basis": "Project-finance closing requirement; normally zero unless a documented standby facility exists."},
            "share_policy.policy_minimum_share": {"unit": "%", "definition": "Lowest landowner share that the institutional policy permits the fair-share search to recommend.", "rationale": "Protects a minimum public or landowner consideration floor independently of the developer-return constraints.", "basis": "Owner policy, statutory minimum or approved negotiation mandate."},
            "share_policy.policy_maximum_share": {"unit": "%", "definition": "Highest landowner share the search engine is allowed to test.", "rationale": "Bounds the optimization domain and prevents recommendations outside the approved contracting envelope.", "basis": "Institutional negotiation and legal policy."},
            "share_policy.minimum_government_value_npv": {"unit": "reporting currency", "definition": "Minimum present value of public-authority consideration required by policy.", "rationale": "Protects the economic value of the landowner's contribution, not merely the nominal percentage.", "basis": "Approved public-value or land-value recovery policy."},
            "share_policy.search_tolerance": {"unit": "share fraction", "definition": "Numerical precision used when locating the balanced share and technical ceiling.", "rationale": "Controls the trade-off between optimization precision and calculation time without changing the underlying decision rules.", "basis": "Model-governance and numerical-method policy."},
            "public_value_adjustment.maximum_risk_haircut": {"unit": "%", "definition": "Maximum disclosed policy haircut applied to contractual NPV through the normalized risk score.", "rationale": "Produces a policy-adjusted comparison value without representing it as market value or a probability-weighted valuation.", "basis": "Versioned internal decision policy requiring calibration and sensitivity review."},
            "fair_consideration_policy.risk_adjusted_capacity_factor": {"unit": "fraction of technical capacity", "definition": "Explicit share of the interval between the public floor and technical ceiling retained as the policy-adjusted ceiling.", "rationale": "Makes institutional conservatism visible, versioned and auditable instead of deriving it from hidden confidence weights.", "basis": "Approved negotiation policy requiring periodic calibration."},
            "fair_consideration_policy.balanced_position_factor": {"unit": "fraction of policy-adjusted range", "definition": "Explicit location of the balanced recommendation between the public floor and policy-adjusted ceiling.", "rationale": "Makes the recommendation position transparent and stable across projects under the same policy.", "basis": "Approved negotiation policy requiring periodic calibration."},
            "fair_consideration_policy.developer_safety_buffer": {"unit": "% of technical interval", "definition": "Optional additional capacity reserved to protect developer execution headroom.", "rationale": "Avoids recommending a term too close to the technical edge of feasibility.", "basis": "Approved institutional risk appetite."},
            "fair_consideration_policy.minimum_capacity_factor": {"unit": "factor", "definition": "Minimum fraction of the feasible interval retained when deriving the policy-adjusted ceiling.", "rationale": "Prevents the adjusted ceiling from collapsing to the floor.", "basis": "Versioned internal negotiation policy."},
            "fair_consideration_policy.balanced_position_minimum": {"unit": "fraction of policy-adjusted range", "definition": "Lowest permitted location of the balanced recommendation between the public floor and policy-adjusted ceiling.", "rationale": "Keeps the balanced point away from the absolute minimum while retaining developer headroom.", "basis": "Versioned internal negotiation policy requiring project-specific calibration."},
            "fair_consideration_policy.balanced_position_maximum": {"unit": "fraction of policy-adjusted range", "definition": "Highest permitted location of the balanced recommendation between the public floor and policy-adjusted ceiling.", "rationale": "Prevents the balanced recommendation from silently becoming the technical edge of viability.", "basis": "Versioned internal negotiation policy requiring project-specific calibration."},
            "procurement_policy.opening_discount_rate": {"unit": "% of package budget", "definition": "Policy-based opening procurement target below the current approved package budget.", "rationale": "Provides a disciplined first negotiation position without altering the authoritative project cost forecast.", "basis": "Published developer procurement policy; advisory only until a supplier quotation is accepted."},
            "procurement_policy.target_discount_rate": {"unit": "% of package budget", "definition": "Recommended negotiated saving for each eligible procurement package.", "rationale": "Translates budget discipline into a transparent target contract ceiling.", "basis": "Published developer procurement policy and market-testing practice."},
            "procurement_policy.minimum_retained_contingency_rate": {"unit": "% of package budget", "definition": "Minimum contingency retained after procurement savings are recognized.", "rationale": "Prevents expected negotiation savings from eliminating delivery contingency.", "basis": "Published developer risk and procurement policy."},
            "distribution_policy.frequency_months": {"unit": "months", "definition": "Interval between governed cash-distribution tests.", "rationale": "Prevents all sponsor distributions from being deferred to project completion while preserving liquidity controls.", "basis": "Approved project cash-distribution policy."},
            "distribution_policy.future_cost_reserve_share": {"unit": "% of eligible future costs", "definition": "Share of eligible future costs retained before any distribution.", "rationale": "Protects construction and mandatory obligations from premature cash extraction.", "basis": "Approved liquidity and completion-risk policy."},
            "distribution_policy.allocation_method": {"unit": "policy choice", "definition": "Determines whether accrued landowner consideration is settled before developer surplus or distributable cash is split by the contract rate.", "rationale": "Separates contractual entitlement from discretionary equity distributions.", "basis": "Approved contract and cash-waterfall policy."},
            "risk_policy.maximum_critical_residual_risks": {"unit": "count", "definition": "Maximum number of critical risks allowed after mitigation.", "rationale": "A project should not be tender-ready while any unmitigated critical risk remains beyond the approved appetite.", "basis": "Institutional risk appetite and procurement-readiness governance."},
            "risk_policy.maximum_high_residual_risks": {"unit": "count", "definition": "Maximum number of high residual risks allowed after mitigation.", "rationale": "Limits the concentration of material unresolved risks even when the aggregate score appears acceptable.", "basis": "Institutional risk appetite."},
            "risk_policy.minimum_mitigation_coverage": {"unit": "%", "definition": "Minimum share of material risks that must have documented mitigation measures.", "rationale": "Tests management preparedness rather than relying only on probability and impact scoring.", "basis": "Risk-management and tender-readiness policy."},
            "tender_policy.minimum_bid_total_score": {"unit": "/100", "definition": "Minimum weighted total score for an investor bid to remain eligible for recommendation.", "rationale": "Prevents a bid from winning on one dimension while remaining institutionally weak overall.", "basis": "Approved tender evaluation methodology and applicable procurement rules."},
            "tender_policy.minimum_bid_technical_score": {"unit": "/100", "definition": "Minimum technical score an investor bid must achieve before financial ranking.", "rationale": "Protects delivery quality and prevents a financially attractive but technically incapable bid from qualifying.", "basis": "Technical prequalification and procurement policy."},
        },
    }
    # Phase-1 hardening: project policies must not own valuation, public-value
    # or negotiation-range assumptions.  Legacy policy versions are normalized
    # by the policy service, while new baselines are clean by construction.
    for valuation_only_key in (
        "valuation_policy",
        "share_policy",
        "public_value_adjustment",
        "fair_consideration_policy",
    ):
        snapshot.pop(valuation_only_key, None)
    constraints = snapshot.get("financial_constraints")
    if isinstance(constraints, dict):
        constraints.pop("government_discount_rate", None)
    guidance = snapshot.get("policy_guidance")
    if isinstance(guidance, dict):
        for key in list(guidance):
            if key == "financial_constraints.government_discount_rate" or key.startswith((
                "valuation_policy.",
                "share_policy.",
                "public_value_adjustment.",
                "fair_consideration_policy.",
            )):
                guidance.pop(key, None)
    return snapshot


def default_valuation_policy_snapshot(*, effective_date: date | None = None) -> dict[str, Any]:
    """Return the separately governed landowner valuation/negotiation policy."""

    base = effective_date or date.today()
    snapshot = {
        "policy_id": "LV360-INSTITUTIONAL-VALUATION-BASELINE",
        "version": POLICY_MODEL_VERSION,
        "effective_date": base.isoformat(),
        "policy_guidance": {
            "product_scope": "BOTH",
            "policy_type": "VALUATION",
            "financial_constraints.government_discount_rate": {"unit": "% p.a.", "definition": "Discount rate used for landowner consideration cash flows.", "rationale": "Separates the valuation time-value policy from project execution policy.", "basis": "Published valuation policy."},
            "fair_consideration_policy.institutional_conservatism": {"unit": "% of technical range", "definition": "Institutional conservatism withheld from the technically feasible interval before any additional developer protection is applied.", "rationale": "Makes the authority's risk posture explicit and prevents hidden adjustments inside the calculation code.", "basis": "Published valuation policy; requires periodic calibration."},
            "fair_consideration_policy.risk_adjusted_capacity_factor": {"unit": "% of technical range", "definition": "Maximum share of technical capacity that the policy permits the recommendation engine to retain after institutional conservatism.", "rationale": "Acts as an explicit cap on the policy-adjusted ceiling rather than a probabilistic risk estimate.", "basis": "Published valuation policy."},
            "fair_consideration_policy.developer_safety_buffer": {"unit": "% of technical range", "definition": "Additional technical capacity reserved to protect developer execution headroom.", "rationale": "Avoids recommending the technical edge of feasibility.", "basis": "Published valuation policy."},
            "fair_consideration_policy.balanced_position_factor": {"unit": "% of adjusted range", "definition": "Position of the landowner-balanced recommendation between the public floor and policy-adjusted ceiling.", "rationale": "Makes landowner recommendation placement transparent.", "basis": "Published valuation policy."},
            "fair_consideration_policy.minimum_capacity_factor": {"unit": "% of technical range", "definition": "Minimum retained technical capacity allowed after applying conservatism and developer protection.", "rationale": "Prevents a policy configuration from collapsing the negotiable interval below an explicitly approved floor.", "basis": "Published valuation policy."},
            "fair_consideration_policy.developer_competitive_position_factor": {"unit": "% of floor-to-balanced interval", "definition": "Position of the developer competitive offer between the defensible floor and the landowner-balanced recommendation.", "rationale": "Creates a separately governed developer negotiation posture without changing the financial truth.", "basis": "Published valuation policy."},
            "valuation_policy.rounding_increment_percent": {"unit": "percentage points", "definition": "Increment used to round rate-based negotiation boundaries and recommendations.", "rationale": "Produces contract-ready percentages while preserving conservative rounding at the floor and ceiling.", "basis": "Published valuation policy."},
            "valuation_policy.recommendation_method": {"unit": "policy choice", "definition": "Selects whether the balanced recommendation follows the explicit policy position or a core target-return point when it lies inside the approved interval.", "rationale": "Prevents silent substitution of one recommendation method for another.", "basis": "Published valuation policy."},
        },
        "financial_constraints": {
            "government_discount_rate": "0.10",
            "discount_rate_type": "NOMINAL",
            "discount_currency": "PROJECT_CURRENCY",
            "discount_compounding": "ANNUAL",
        },
        "share_policy": {
            "policy_minimum_share": "0.05",
            "policy_maximum_share": "0.50",
            "search_tolerance": "0.00001",
            "minimum_government_value_npv": "0",
        },
        "valuation_policy": {
            "valuation_basis": "ADVISORY_REFERENCE",
            "conservatism_method": "EXPLICIT_POLICY_RANGE_HAIRCUT",
            "developer_protection_method": "EXPLICIT_CAPACITY_RESERVE",
            "retained_capacity_method": "EXPLICIT_MINIMUM_AND_MAXIMUM",
            "conservative_ceiling_method": "TECHNICAL_CEILING_TIMES_RETAINED_CAPACITY",
            "minimum_consideration_method": "MAXIMUM_OF_POLICY_NPV_AND_EXPLICIT_AMOUNT",
            "minimum_consideration_amount": "0",
            "rounding_method": "FLOOR_UP_CEILING_DOWN_NEAREST_RECOMMENDATION",
            # Stored as a fractional rate: 0.001 equals 0.10 percentage point.
            "rounding_increment_percent": "0.001",
            "recommendation_method": "POLICY_RANGE_POSITION",
        },
        "fair_consideration_policy": {
            "institutional_conservatism": "0.45",
            "risk_adjusted_capacity_factor": "0.45",
            "developer_safety_buffer": "0.00",
            "balanced_position_factor": "0.59",
            "developer_competitive_position_factor": "0.40",
            "minimum_capacity_factor": "0.30",
            "balanced_position_minimum": "0.00",
            "balanced_position_maximum": "1.00",
            "classification": "EXPLICIT_VERSIONED_VALUATION_POLICY",
        },
        "public_value_adjustment": {
            "maximum_risk_haircut": "0.35",
            "classification": "VERSIONED_POLICY_HEURISTIC_NOT_MARKET_VALUE",
        },
    }
    return snapshot


def ensure_valuation_policy(
    session: Session,
    *,
    organization: Organization,
    workspace: Workspace,
    user: User,
) -> tuple[PolicyPack, PolicyPackVersion]:
    """Create an idempotent published valuation-policy baseline."""

    code = "INSTITUTIONAL-VALUATION-BASELINE"
    pack = session.scalar(
        select(PolicyPack).where(
            PolicyPack.organization_id == organization.id,
            PolicyPack.scope_key == workspace.id,
            PolicyPack.code == code,
        )
    )
    if pack is None:
        pack = PolicyPack(
            organization_id=organization.id,
            workspace_id=workspace.id,
            scope_key=workspace.id,
            name="LandValue360 Institutional Valuation Policy",
            code=code,
            description="Published valuation and negotiation policy for the Landowner Edition.",
            status="ACTIVE",
            created_by_user_id=user.id,
        )
        session.add(pack)
        session.flush()
    published = session.scalar(
        select(PolicyPackVersion)
        .where(PolicyPackVersion.policy_pack_id == pack.id, PolicyPackVersion.status == "PUBLISHED")
        .order_by(PolicyPackVersion.version_number.desc())
    )
    snapshot = published.policy_snapshot if published is not None and isinstance(published.policy_snapshot, dict) else {}
    expected_snapshot = default_valuation_policy_snapshot()
    if (
        published is None
        or (snapshot.get("policy_guidance") or {}).get("policy_type") != "VALUATION"
        or str(snapshot.get("version")) != str(expected_snapshot.get("version"))
    ):
        now = utc_now()
        source = expected_snapshot
        prior = published
        published = PolicyPackVersion(
            organization_id=organization.id,
            workspace_id=workspace.id,
            policy_pack_id=pack.id,
            version_number=(prior.version_number + 1) if prior is not None else 1,
            version_label=f"Institutional valuation baseline {POLICY_MODEL_VERSION}",
            status="PUBLISHED",
            effective_from=now,
            effective_to=None,
            policy_snapshot=source,
            policy_hash=sha256_json(source),
            notes="Landowner valuation/negotiation policy. Calibrate before external reliance.",
            supersedes_version_id=prior.id if prior is not None else None,
            created_by_user_id=user.id,
            published_by_user_id=user.id,
            published_at=now,
        )
        session.add(published)
        session.flush()
    return pack, published

def ensure_development_policy(
    session: Session,
    *,
    organization: Organization,
    workspace: Workspace,
    user: User,
) -> tuple[PolicyPack, PolicyPackVersion]:
    """Create an idempotent published baseline policy for local development."""

    code = "INSTITUTIONAL-BASELINE"
    pack = session.scalar(
        select(PolicyPack).where(
            PolicyPack.organization_id == organization.id,
            PolicyPack.scope_key == workspace.id,
            PolicyPack.code == code,
        )
    )
    if pack is None:
        pack = PolicyPack(
            organization_id=organization.id,
            workspace_id=workspace.id,
            scope_key=workspace.id,
            name="LandValue360 Institutional Baseline",
            code=code,
            description=(
                "Published local-development policy used by the browser MVP. "
                "Production deployments must replace it with a professionally approved policy pack."
            ),
            status="ACTIVE",
            created_by_user_id=user.id,
        )
        session.add(pack)
        session.flush()

    published = session.scalar(
        select(PolicyPackVersion)
        .where(
            PolicyPackVersion.policy_pack_id == pack.id,
            PolicyPackVersion.status == "PUBLISHED",
        )
        .order_by(PolicyPackVersion.version_number.desc())
    )
    if published is None or str((published.policy_snapshot or {}).get("version")) != POLICY_MODEL_VERSION or (published.policy_snapshot or {}).get("policy_guidance", {}).get("policy_type") != "PROJECT" or "risk_policy" not in (published.policy_snapshot or {}) or "tender_policy" not in (published.policy_snapshot or {}) or "funding_policy" not in (published.policy_snapshot or {}) or "distribution_policy" not in (published.policy_snapshot or {}) or any(key in (published.policy_snapshot or {}) for key in ("valuation_policy", "share_policy", "fair_consideration_policy", "public_value_adjustment")):
        snapshot = default_policy_snapshot()
        now = utc_now()
        prior = published
        published = PolicyPackVersion(
            organization_id=organization.id,
            workspace_id=workspace.id,
            policy_pack_id=pack.id,
            version_number=(prior.version_number + 1) if prior is not None else 1,
            version_label=f"Institutional baseline {POLICY_MODEL_VERSION}",
            status="PUBLISHED",
            effective_from=now,
            effective_to=None,
            policy_snapshot=snapshot,
            policy_hash=sha256_json(snapshot),
            notes=(
                "Development-only baseline. Replace or supersede after independent financial, "
                "valuation, legal, and investment-policy approval."
            ),
            supersedes_version_id=prior.id if prior is not None else None,
            created_by_user_id=user.id,
            published_by_user_id=user.id,
            published_at=now,
        )
        session.add(published)
        session.flush()
    return pack, published
