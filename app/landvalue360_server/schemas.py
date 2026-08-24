"""Versioned REST API request and response schemas for release 0.3."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import CalculationMode, MembershipRole, ProductAccess


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class SlugMixin:
    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SLUG_RE.fullmatch(normalized):
            raise ValueError("Slug may contain lowercase letters, digits, and internal hyphens only.")
        return normalized


class CodeMixin:
    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip()
        if not _CODE_RE.fullmatch(normalized):
            raise ValueError("Code may contain letters, digits, periods, underscores, and hyphens.")
        return normalized


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)
    organization_slug: str | None = Field(default=None, max_length=80)
    workspace_slug: str | None = Field(default=None, max_length=80)
    edition: Literal["DEVELOPER", "GOVERNMENT", "ADMINISTRATION"] | None = None


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


class TokenResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: dict[str, Any]
    context: dict[str, Any]


class HealthResponse(StrictModel):
    status: str
    application_version: str
    calculation_model_version: str
    database: str


class OrganizationCreate(StrictModel, SlugMixin):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=1, max_length=80)
    default_currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("default_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalpha() or len(normalized) != 3:
            raise ValueError("Currency must be a three-letter code.")
        return normalized


class WorkspaceCreate(StrictModel, SlugMixin):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=1, max_length=80)


class UserCreate(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=512)
    is_platform_admin: bool = False


class MembershipCreate(StrictModel):
    user_id: str
    workspace_id: str | None = None
    role: MembershipRole
    product_access: ProductAccess = ProductAccess.BOTH


class AdministrationMemberCreate(StrictModel):
    """Create a login account and its access membership atomically."""

    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=512)
    organization_id: str
    workspace_id: str | None = None
    role: MembershipRole
    product_access: ProductAccess = ProductAccess.BOTH
    is_platform_admin: bool = False


class PortfolioCreate(StrictModel, CodeMixin):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=10_000)


class PortfolioUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


class ProjectCreate(StrictModel, CodeMixin):
    name: str = Field(min_length=2, max_length=240)
    code: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=20_000)
    portfolio_id: str | None = None


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=240)
    description: str | None = Field(default=None, max_length=20_000)
    portfolio_id: str | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


class ProjectVersionCreate(StrictModel):
    input_snapshot: dict[str, Any]
    label: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=20_000)
    supersedes_version_id: str | None = None


class ProjectVersionUpdate(StrictModel):
    input_snapshot: dict[str, Any] | None = None
    label: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=20_000)
    expected_input_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ProjectVersionClone(StrictModel):
    label: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=20_000)




class ProjectPackageImportOut(StrictModel):
    project_id: str
    project_name: str
    project_code: str
    latest_version_id: str | None = None
    version_count: int
    scenario_count: int
    source_platform_version: str | None = None
    package_format_version: str
    message: str
    migration_impact_report: dict[str, Any] | None = None


class ScenarioCreate(StrictModel, CodeMixin):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=20_000)
    override_snapshot: dict[str, Any] = Field(default_factory=dict)


class ScenarioUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    override_snapshot: dict[str, Any] | None = None
    status: Literal["DRAFT", "LOCKED", "ARCHIVED"] | None = None


class PolicyPackCreate(StrictModel, CodeMixin):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=20_000)
    workspace_id: str | None = None


class PolicyVersionCreate(StrictModel):
    version_label: str = Field(min_length=1, max_length=80)
    policy_snapshot: dict[str, Any]
    notes: str | None = Field(default=None, max_length=20_000)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    supersedes_version_id: str | None = None


class PolicyVersionClone(StrictModel):
    version_label: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=20_000)


class PolicyVersionUpdate(StrictModel):
    policy_snapshot: dict[str, Any] | None = None
    version_label: str | None = Field(default=None, min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=20_000)
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class CalculationRunCreate(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valuation_policy_pack_version_id", "valuation_policy_version_id"),
    )
    scenario_id: str | None = None
    mode: CalculationMode = CalculationMode.PREVIEW
    analysis_level: Literal["STANDARD", "FULL"] = "FULL"
    case_id: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=20_000)


class Pagination(StrictModel):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class OrganizationOut(ORMModel):
    id: str
    name: str
    slug: str
    status: str
    default_currency: str
    created_at: datetime
    updated_at: datetime


class WorkspaceOut(ORMModel):
    id: str
    organization_id: str
    name: str
    slug: str
    status: str
    created_at: datetime
    updated_at: datetime


class UserOut(ORMModel):
    id: str
    email: str
    full_name: str
    is_active: bool
    is_platform_admin: bool
    created_at: datetime
    updated_at: datetime


class MembershipOut(ORMModel):
    id: str
    user_id: str
    organization_id: str
    workspace_id: str | None
    role: str
    product_access: str = "BOTH"
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PortfolioOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    name: str
    code: str
    description: str | None
    status: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class ProjectOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    portfolio_id: str | None
    name: str
    code: str
    description: str | None
    project_kind: str = "DEVELOPER"
    status: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class ProjectVersionOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    version_number: int
    status: str
    label: str | None
    notes: str | None
    input_snapshot: dict[str, Any]
    input_hash: str
    source_input_schema: str | None = None
    source_input_snapshot: dict[str, Any] | None = None
    source_input_hash: str | None = None
    supersedes_version_id: str | None
    created_by_user_id: str
    approved_by_user_id: str | None
    approved_at: datetime | None
    row_version: int = 1
    created_at: datetime
    updated_at: datetime


class ScenarioOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_version_id: str
    name: str
    code: str
    description: str | None
    status: str
    override_snapshot: dict[str, Any]
    override_hash: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class PolicyPackOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str | None
    name: str
    code: str
    description: str | None
    status: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class PolicyVersionOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str | None
    policy_pack_id: str
    version_number: int
    version_label: str
    status: str
    effective_from: datetime | None
    effective_to: datetime | None
    policy_snapshot: dict[str, Any]
    policy_hash: str
    notes: str | None
    supersedes_version_id: str | None
    created_by_user_id: str
    published_by_user_id: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CalculationRunSummaryOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str
    scenario_id: str | None
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str | None
    replayed_from_run_id: str | None
    mode: str
    status: str
    case_id: str
    description: str | None
    application_version: str
    calculation_model_version: str
    input_schema_version: str
    output_schema_version: str | None
    input_hash: str
    output_hash: str | None
    error_summary: str | None
    calculation_validity: str = "NOT_RUN"
    economic_feasibility: str = "NOT_ASSESSED"
    policy_compliance: str = "NOT_ASSESSED"
    evidence_readiness: str = "NOT_REQUIRED"
    report_readiness: str = "NOT_READY"
    locked_at: datetime | None = None
    created_by_user_id: str
    created_at: datetime
    completed_at: datetime | None


class CalculationRunDetailOut(CalculationRunSummaryOut):
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any] | None


class CalculationReplayOut(StrictModel):
    output_matches_original: bool
    run: CalculationRunDetailOut


class AuditEventOut(ORMModel):
    id: str
    organization_id: str | None
    workspace_id: str | None
    actor_user_id: str | None
    request_id: str | None
    edition_scope: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    event_metadata: dict[str, Any] | None
    occurred_at: datetime


class ScenarioComparisonRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_ids: list[str] = Field(default_factory=list, max_length=12)
    include_base: bool = True


class NegotiationRowInput(StrictModel):
    row_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"]
    share_rate: str = "0"
    upfront_amount: str = "0"
    hybrid_variable_basis: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE"] = "GROSS_SALES"


class NegotiationAnalysisRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    rows: list[NegotiationRowInput] = Field(min_length=1, max_length=50)


class EvidenceMetadataUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    evidence_type: Literal[
        "TITLE", "PLANNING", "MARKET_STUDY", "COST_ESTIMATE", "LEGAL_OPINION",
        "INFRASTRUCTURE", "FINANCE", "ENVIRONMENT_SOCIAL", "MEASUREMENT", "OTHER"
    ] | None = None
    source_name: str | None = Field(default=None, max_length=240)
    source_reference: str | None = Field(default=None, max_length=1000)
    issue_date: date | None = None
    expiry_date: date | None = None
    notes: str | None = Field(default=None, max_length=20_000)
    project_version_id: str | None = None


class EvidenceVerification(StrictModel):
    status: Literal["VERIFIED", "UNDER_REVIEW", "REJECTED", "ARCHIVED"]
    notes: str | None = Field(default=None, max_length=20_000)


class EvidenceDocumentOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str | None
    evidence_type: str
    title: str
    original_filename: str
    media_type: str
    size_bytes: int
    content_hash: str
    status: str
    source_name: str | None
    source_reference: str | None
    issue_date: date | None
    expiry_date: date | None
    notes: str | None
    created_by_user_id: str
    verified_by_user_id: str | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AssumptionCreate(StrictModel):
    assumption_key: str = Field(min_length=1, max_length=400)
    label: str = Field(min_length=1, max_length=240)
    category: Literal["LAND", "PLANNING", "MARKET", "COST", "FINANCE", "LEGAL", "E_AND_S", "OTHER"]
    value_snapshot: dict[str, Any]
    unit: str | None = Field(default=None, max_length=64)
    criticality: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    source_type: Literal["MANUAL", "MARKET_STUDY", "BOQ", "VALUATION_REPORT", "LEGAL_OPINION", "GOVERNMENT_RECORD", "BENCHMARK", "OTHER"] = "MANUAL"
    source_reference: str | None = Field(default=None, max_length=1000)
    evidence_document_ids: list[str] = Field(default_factory=list, max_length=50)
    evidence_status: Literal["VERIFIED", "PARTIAL", "UNVERIFIED", "MISSING", "NOT_APPLICABLE"] = "MISSING"
    confidence_score: int = Field(default=0, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=20_000)


class AssumptionUpdate(StrictModel):
    label: str | None = Field(default=None, min_length=1, max_length=240)
    category: Literal["LAND", "PLANNING", "MARKET", "COST", "FINANCE", "LEGAL", "E_AND_S", "OTHER"] | None = None
    value_snapshot: dict[str, Any] | None = None
    unit: str | None = Field(default=None, max_length=64)
    criticality: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = None
    source_type: Literal["MANUAL", "MARKET_STUDY", "BOQ", "VALUATION_REPORT", "LEGAL_OPINION", "GOVERNMENT_RECORD", "BENCHMARK", "OTHER"] | None = None
    source_reference: str | None = Field(default=None, max_length=1000)
    evidence_document_ids: list[str] | None = Field(default=None, max_length=50)
    evidence_status: Literal["VERIFIED", "PARTIAL", "UNVERIFIED", "MISSING", "NOT_APPLICABLE"] | None = None
    confidence_score: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=20_000)


class AssumptionReview(StrictModel):
    approval_status: Literal["REVIEWED", "APPROVED", "REJECTED"]
    evidence_status: Literal["VERIFIED", "PARTIAL", "UNVERIFIED", "MISSING", "NOT_APPLICABLE"] | None = None
    confidence_score: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=20_000)


class AssumptionRecordOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str
    assumption_key: str
    label: str
    category: str
    value_snapshot: dict[str, Any]
    unit: str | None
    criticality: str
    source_type: str
    source_reference: str | None
    evidence_document_ids: list[str]
    evidence_status: str
    confidence_score: int
    approval_status: str
    notes: str | None
    created_by_user_id: str
    reviewed_by_user_id: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ValuationComparableInput(StrictModel):
    comparable_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=240)
    land_area_sqm: str
    transaction_price: str
    transaction_date: date | None = None
    location_adjustment: str = "0"
    planning_adjustment: str = "0"
    size_adjustment: str = "0"
    time_adjustment: str = "0"
    other_adjustment: str = "0"
    reliability_weight: str = "1"
    evidence_document_id: str | None = None


class ValuationMethodInput(StrictModel):
    enabled: bool = True
    weight: str = "0"
    confidence: str | None = None
    uncertainty: str | None = None
    override_value: str | None = None
    include_finance_costs: bool | None = None


class ValuationMethodConfiguration(StrictModel):
    quality_adjusted_weights: bool = True
    target_developer_profit_on_cost: str | None = None
    dcf: ValuationMethodInput = Field(default_factory=lambda: ValuationMethodInput(weight="0.35"))
    residual: ValuationMethodInput = Field(default_factory=lambda: ValuationMethodInput(weight="0.40", include_finance_costs=True))
    market_comparables: ValuationMethodInput = Field(default_factory=lambda: ValuationMethodInput(weight="0.20"))
    bid_implied: ValuationMethodInput = Field(default_factory=lambda: ValuationMethodInput(weight="0.05"))
    direct_benchmark: ValuationMethodInput = Field(default_factory=lambda: ValuationMethodInput(enabled=False, weight="0"))


class ValuationRunCreate(StrictModel):
    calculation_run_id: str
    mode: CalculationMode = CalculationMode.PREVIEW
    basis_of_value: Literal["MARKET_VALUE", "INVESTMENT_VALUE", "FAIR_VALUE", "RESIDUAL_DEVELOPMENT_VALUE", "PUBLIC_SECTOR_ECONOMIC_VALUE"] = "MARKET_VALUE"
    purpose: Literal["DEVELOPMENT_DECISION_SUPPORT", "LAND_PARTNERSHIP", "TENDER_PREPARATION", "FINANCIAL_REPORTING", "INTERNAL_INVESTMENT_DECISION"] = "DEVELOPMENT_DECISION_SUPPORT"
    valuation_date: date | None = None
    method_configuration: ValuationMethodConfiguration = Field(default_factory=ValuationMethodConfiguration)
    comparables: list[ValuationComparableInput] = Field(default_factory=list, max_length=100)
    direct_benchmark_value: str | None = None
    notes: str | None = Field(default=None, max_length=20_000)


class ValuationRunSummaryOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str
    calculation_run_id: str
    policy_pack_version_id: str
    scenario_id: str | None
    mode: str
    status: str
    basis_of_value: str
    purpose: str
    valuation_date: date
    reporting_currency: str
    valuation_model_version: str
    input_hash: str
    output_hash: str
    created_by_user_id: str
    created_at: datetime
    completed_at: datetime


class ValuationRunDetailOut(ValuationRunSummaryOut):
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any]


class DataQualityPreviewRequest(StrictModel):
    project_version_id: str
    valuation_date: date | None = None

class LivePreviewRequest(StrictModel):
    project_snapshot: dict[str, Any]
    policy_pack_version_id: str


class RiskItemInput(StrictModel):
    risk_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    category: str = Field(default="OTHER", max_length=80)
    risk_type: Literal["PROJECT", "MODEL", "COUNTERPARTY"] = "PROJECT"
    probability: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    mitigation_effectiveness: str = "0"
    owner: str = Field(default="Unassigned", max_length=160)
    allocation: Literal["DEVELOPER", "GOVERNMENT", "SHARED", "INSURABLE"] = "SHARED"
    mitigation: str = Field(default="", max_length=20_000)
    contract_clause_required: bool = False
    contract_clause_reference: str = Field(default="", max_length=500)
    financial_driver: Literal["NONE", "SALES_PRICE", "DEVELOPMENT_COST", "SALES_DELAY", "CONSTRUCTION_DELAY", "INTEREST_RATE"] = "NONE"


class RiskAssessmentRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    items: list[RiskItemInput] = Field(min_length=1, max_length=200)


class SensitivityDriverInput(StrictModel):
    driver: Literal["SALES_PRICE", "DEVELOPMENT_COST", "SALES_DELAY", "CONSTRUCTION_DELAY", "LANDOWNER_SHARE", "INTEREST_RATE", "FAR", "EFFICIENCY"]
    unit: str = Field(default="", max_length=64)
    values: list[str | int] = Field(min_length=2, max_length=21)


class TwoWaySensitivityInput(StrictModel):
    price_values: list[str] = Field(min_length=2, max_length=15)
    cost_values: list[str] = Field(min_length=2, max_length=15)
    metric: str = Field(default="developer_irr", max_length=80)


class SensitivityRunRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    target_metric: str = Field(default="developer_npv", max_length=80)
    drivers: list[SensitivityDriverInput] = Field(default_factory=list, max_length=12)
    two_way: TwoWaySensitivityInput | None = None
    include_break_evens: bool = True


class DistributionInput(StrictModel):
    type: Literal["TRIANGULAR", "UNIFORM", "NORMAL", "LOGNORMAL"] = "TRIANGULAR"
    low: str
    high: str
    mode: str | None = None
    mean: str | None = None
    stddev: str | None = None
    sigma: str | None = None


class MonteCarloRunRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    iterations: int = Field(default=500, ge=50, le=5000)
    seed: int = 360
    distributions: dict[str, DistributionInput] = Field(default_factory=dict)
    metrics: list[str] = Field(default_factory=lambda: ["developer_irr", "developer_npv", "government_npv", "peak_funding", "funding_gap"], max_length=20)


class TenderReadinessRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    risk_items: list[RiskItemInput] | None = Field(default=None, max_length=200)


class TenderBidInput(StrictModel):
    bid_id: str = Field(min_length=1, max_length=80)
    bidder: str = Field(min_length=1, max_length=240)
    method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"] = "GROSS_SALES"
    share_rate: str = "0"
    upfront_amount: str = "0"
    completion_months: int = Field(default=48, ge=1, le=360)
    committed_equity: str = "0"
    committed_financing: str = "0"
    technical_score: str = "0"
    experience_score: str = "0"
    guarantees_score: str = "0"
    integrity_score: str = "0"
    price_multiplier: str = "1"
    cost_multiplier: str = "1"
    annual_interest_rate: str = "0"


class TenderEvaluationRequest(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    criteria_weights: dict[str, str]
    bids: list[TenderBidInput] = Field(min_length=1, max_length=100)


class AnalysisRunSummaryOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str | None
    scenario_id: str | None
    analysis_type: str
    status: str
    analysis_model_version: str
    input_hash: str
    output_hash: str
    created_by_user_id: str
    created_at: datetime
    completed_at: datetime


class AnalysisRunDetailOut(AnalysisRunSummaryOut):
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any]



class GovernmentProductInput(StrictModel):
    product_code: Literal["RESIDENTIAL", "RETAIL", "OFFICE", "HOSPITALITY", "INDUSTRIAL", "OTHER"]
    name: str = Field(min_length=2, max_length=160)
    gfa_share_percent: Decimal = Field(ge=0, le=100)
    efficiency_percent: Decimal = Field(gt=0, le=100)
    unit_price_per_sqm: Decimal = Field(ge=0)
    construction_cost_per_sqm: Decimal = Field(ge=0)


class GovernmentCostTreatmentInput(StrictModel):
    """Contract treatment for one Government-Edition cost category.

    Cash responsibility and economic burden are deliberately separate from the
    contractual deduction rule.  Legacy projects omit this list and are
    migrated to NOT_DEDUCTIBLE by the service layer.
    """

    cost_key: Literal[
        "BUILDING", "INFRA_INTERNAL", "INFRA_EXTERNAL", "PUBLIC_FACILITIES",
        "PERMITS", "DESIGN", "PROJECT_MANAGEMENT", "MARKETING",
    ]
    cash_payer: Literal["DEVELOPER", "PUBLIC_AUTHORITY", "SHARED"] = "DEVELOPER"
    economic_bearer: Literal["DEVELOPER", "PUBLIC_AUTHORITY", "SHARED"] = "DEVELOPER"
    developer_cash_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    developer_economic_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    developer_advances_landowner_share: bool = False
    advance_recovery_method: Literal["NONE", "FIRST_LANDOWNER_DISTRIBUTIONS", "SCHEDULED"] = Field(
        default="FIRST_LANDOWNER_DISTRIBUTIONS",
        validation_alias=AliasChoices("advance_recovery_method", "recovery_method"),
    )
    advance_recovery_priority: int = Field(
        default=50, ge=0, le=1000,
        validation_alias=AliasChoices("advance_recovery_priority", "recovery_priority"),
    )
    reimbursable: bool = False
    deduction_treatment: Literal[
        "NOT_DEDUCTIBLE", "FULL", "PERCENTAGE", "CAPPED", "CONDITIONAL"
    ] = "NOT_DEDUCTIBLE"
    deduction_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    deduction_cap: Decimal | None = Field(default=None, ge=0)
    deduction_basis: Literal["PAID", "ACCRUED"] = "PAID"
    approval_required: bool = False
    approval_obtained: bool = False
    evidence_required: bool = False
    evidence_status: Literal["NOT_REQUIRED", "MISSING", "PROVIDED", "VERIFIED"] = "NOT_REQUIRED"
    related_party: bool = False
    market_test_required: bool = False
    market_test_passed: bool = False
    public_borne_deduction_authorized: bool = False
    include_in_profit_share_base: bool = True
    deduction_category: str = Field(default="project_cost", min_length=2, max_length=120)
    contract_rule: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_cost_treatment(self):
        if self.cash_payer == "DEVELOPER" and self.developer_cash_share_percent != Decimal("100"):
            raise ValueError("Developer cash payer requires a 100% developer cash share.")
        if self.cash_payer == "PUBLIC_AUTHORITY" and self.developer_cash_share_percent != Decimal("0"):
            raise ValueError("Public-authority cash payer requires a 0% developer cash share.")
        if self.economic_bearer == "DEVELOPER" and self.developer_economic_share_percent != Decimal("100"):
            raise ValueError("Developer economic bearer requires a 100% developer economic share.")
        if self.economic_bearer == "PUBLIC_AUTHORITY" and self.developer_economic_share_percent != Decimal("0"):
            raise ValueError("Public-authority economic bearer requires a 0% developer economic share.")
        if self.developer_advances_landowner_share:
            if self.developer_economic_share_percent >= Decimal("100"):
                raise ValueError("Developer advance recovery requires a positive landowner economic share.")
            if self.developer_cash_share_percent != Decimal("100") or self.cash_payer != "DEVELOPER":
                raise ValueError("A developer advance requires the developer to fund 100% of the cash cost.")
            if self.advance_recovery_method == "NONE":
                raise ValueError("A developer advance requires a recovery method.")
        if self.deduction_treatment == "NOT_DEDUCTIBLE":
            if self.deduction_percentage not in {Decimal("0"), Decimal("100")}:
                raise ValueError("A non-deductible cost cannot have a partial deduction percentage.")
        elif self.deduction_treatment == "FULL":
            if self.deduction_percentage not in {Decimal("0"), Decimal("100")}:
                raise ValueError("A full deduction must use 100% (or zero, which is normalized to 100%).")
        elif self.deduction_treatment in {"PERCENTAGE", "CAPPED", "CONDITIONAL"}:
            if self.deduction_percentage <= 0:
                raise ValueError("Percentage, capped and conditional deductions require a positive deduction percentage.")
        if self.deduction_treatment == "CAPPED" and self.deduction_cap is None:
            raise ValueError("A capped deduction requires a deduction cap.")
        return self




class GovernmentCollectionRuleInput(StrictModel):
    lag_months: int = Field(default=0, ge=0, le=600)
    weight_percent: Decimal = Field(ge=0, le=100)
    label: str | None = Field(default=None, max_length=160)

class LandownerProjectInput(StrictModel):
    input_status: Literal["DEMO_NOT_VALIDATED", "UNVALIDATED", "VALIDATED", "APPROVED"] = "UNVALIDATED"
    valuation_date: date
    base_date: date
    currency: str = Field(default="USD", min_length=3, max_length=3)
    basis_of_value: Literal["MARKET_VALUE", "FAIR_VALUE", "INVESTMENT_VALUE", "SPECIAL_VALUE"] = "MARKET_VALUE"
    gross_land_area_sqm: Decimal | None = Field(default=None, gt=0, le=Decimal("1000000000"))
    excluded_land_area_sqm: Decimal = Field(default=Decimal("0"), ge=0)
    far_land_basis: Literal["GROSS", "NET", "INVESTMENT"] = "NET"
    bcr_land_basis: Literal["GROSS", "NET", "INVESTMENT"] = "NET"
    far: Decimal | None = Field(default=None, gt=0, le=Decimal("20"))
    bcr_percent: Decimal | None = Field(default=None, gt=0, le=100)
    maximum_storeys: int = Field(default=0, ge=0, le=300)
    reference_land_value_per_sqm: Decimal | None = Field(default=None, ge=0)
    reference_land_value_basis: Literal["GROSS", "NET", "INVESTMENT", "DIRECT_TOTAL"] = "GROSS"
    reference_land_value_total: Decimal | None = Field(default=None, ge=0)
    reference_land_value_legacy_derived: bool = False
    land_value_baseline: Decimal = Field(default=Decimal("0"), ge=0)
    existing_use_value: Decimal = Field(default=Decimal("0"), ge=0)
    alternative_use_value: Decimal = Field(default=Decimal("0"), ge=0)
    land_value_evidence_classification: str = Field(default="USER_INPUT", min_length=2, max_length=120)
    existing_use_evidence_classification: str = Field(default="USER_INPUT", min_length=2, max_length=120)
    alternative_use_evidence_classification: str = Field(default="USER_INPUT", min_length=2, max_length=120)
    investment_land_share_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    roads_land_share_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    green_land_share_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    public_land_share_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    products: list[GovernmentProductInput] = Field(default_factory=list, max_length=8)
    sales_start_month: int = Field(default=7, ge=1, le=240)
    sales_duration_months: int = Field(default=36, ge=1, le=360)
    construction_start_month: int = Field(default=1, ge=1, le=240)
    construction_duration_months: int = Field(default=30, ge=1, le=360)
    collection_plan_code: Literal["CASH", "DOWN_20_INSTALLMENTS_24", "DOWN_30_HANDOVER_70", "DOWN_30_MONTH6_HANDOVER_40", "DOWN_40_INSTALLMENTS_12", "DOWN_50_INSTALLMENTS_24", "CONSTRUCTION_LINKED", "CUSTOM", "LEGACY_THREE_POINT"] = "DOWN_30_MONTH6_HANDOVER_40"
    collection_custom_rules: list[GovernmentCollectionRuleInput] = Field(default_factory=list, max_length=60)
    # Backward-compatible legacy fields retained for v9 project migration.
    collection_upfront_percent: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    collection_six_month_percent: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    collection_twelve_month_percent: Decimal = Field(default=Decimal("40"), ge=0, le=100)
    annual_escalation_percent: Decimal = Field(default=Decimal("4"), ge=0, le=100)
    contingency_percent: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    internal_infrastructure_cost_per_sqm: Decimal = Field(default=Decimal("250"), ge=0)
    internal_infrastructure_quantity_basis: Literal["ROADS_AREA", "GROSS_LAND", "NET_LAND", "INVESTMENT_LAND", "FIXED_QUANTITY"] = "ROADS_AREA"
    internal_infrastructure_fixed_quantity_sqm: Decimal = Field(default=Decimal("0"), ge=0)
    # Backward-compatible field: this is site/land works on the public-facility land area.
    public_facility_cost_per_sqm: Decimal = Field(default=Decimal("750"), ge=0)
    public_facility_building_cost_per_sqm: Decimal = Field(default=Decimal("0"), ge=0)
    public_facility_far: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("20"))
    # Backward-compatible calculated field retained for v9 imports.
    public_facility_built_area_sqm: Decimal = Field(default=Decimal("0"), ge=0)
    external_infrastructure_amount: Decimal = Field(default=Decimal("400000"), ge=0)
    permits_and_fees_amount: Decimal = Field(default=Decimal("300000"), ge=0)
    professional_fees_percent: Decimal = Field(default=Decimal("4.5"), ge=0, le=100)
    project_management_percent: Decimal = Field(default=Decimal("5.9"), ge=0, le=100)
    marketing_percent_of_revenue: Decimal = Field(default=Decimal("3"), ge=0, le=100)
    building_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    internal_infrastructure_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    external_infrastructure_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    public_facilities_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    permits_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    professional_fees_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    project_management_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    marketing_developer_share_percent: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    cost_treatments: list[GovernmentCostTreatmentInput] = Field(default_factory=list, max_length=8)
    hybrid_variable_basis: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE"] = "GROSS_SALES"
    opening_cash: Decimal = Field(default=Decimal("0"), ge=0)
    committed_equity: Decimal = Field(default=Decimal("3500000"), ge=0)
    equity_commitment_mode: Literal["DECLARED_COMMITMENT", "POLICY_SCREENING"] = "DECLARED_COMMITMENT"
    committed_financing: Decimal = Field(default=Decimal("0"), ge=0, description="Deprecated in Landowner Edition; bank financing is modeled only in Developer Edition.")
    annual_interest_rate_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100, description="Deprecated in Landowner Edition; bank financing is modeled only in Developer Edition.")
    partnership_method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"] = "GROSS_SALES"
    offered_share_percent: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    upfront_amount: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_guarantee_amount: Decimal = Field(default=Decimal("0"), ge=0)
    minimum_guarantee_payment_month: int = Field(default=48, ge=1, le=600)
    minimum_guarantee_underlying_method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE"] = "GROSS_SALES"
    minimum_guarantee_underlying_share_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    # Compatibility-only import field. The authoritative discount rate is
    # always read from the selected valuation-policy version.
    public_discount_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)
    data_confidence_percent: Decimal = Field(default=Decimal("65"), ge=0, le=100)
    contract_enforceability_percent: Decimal = Field(default=Decimal("65"), ge=0, le=100)
    planning_status: str = Field(default="Current planning assumptions supplied by the public authority.", min_length=2, max_length=2000)
    title_assumptions: str = Field(default="Landowner title is assumed valid and subject to legal verification.", min_length=2, max_length=2000)
    encumbrances: str = Field(default="No undisclosed encumbrances assumed; legal verification required.", min_length=2, max_length=2000)
    infrastructure_obligations: str = Field(default="Developer obligations are modeled from the stated cost and responsibility inputs.", min_length=2, max_length=2000)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must be a three-letter alphabetic code.")
        return normalized

    @model_validator(mode="after")
    def validate_totals(self):
        tolerance = Decimal("0.01")
        if self.base_date > self.valuation_date:
            raise ValueError("Base date cannot be after valuation date.")
        if self.gross_land_area_sqm is None or self.far is None or self.bcr_percent is None:
            raise ValueError("Detailed analysis requires land area, FAR and BCR.")
        if self.excluded_land_area_sqm >= self.gross_land_area_sqm:
            raise ValueError("Excluded land area must be less than gross land area.")
        land_total = (
            self.investment_land_share_percent
            + self.roads_land_share_percent
            + self.green_land_share_percent
            + self.public_land_share_percent
        )
        if abs(land_total - Decimal("100")) > tolerance:
            raise ValueError(f"Land-use shares must total 100%; current total is {land_total}%.")
        if not self.products:
            raise ValueError("Detailed analysis requires at least one product.")
        product_total = sum((row.gfa_share_percent for row in self.products), Decimal("0"))
        if abs(product_total - Decimal("100")) > tolerance:
            raise ValueError(f"Product GFA shares must total 100%; current total is {product_total}%.")
        product_codes = [row.product_code for row in self.products]
        if len(product_codes) != len(set(product_codes)):
            raise ValueError("Product codes must be unique.")
        active_products = [row for row in self.products if row.gfa_share_percent > 0]
        if not active_products:
            raise ValueError("At least one product must have a positive GFA share.")
        for row in active_products:
            if row.unit_price_per_sqm <= 0:
                raise ValueError(f"Unit price must be positive for product {row.product_code}.")
            if row.construction_cost_per_sqm <= 0:
                raise ValueError(f"Construction cost must be positive for product {row.product_code}.")
        if self.collection_plan_code == "CUSTOM":
            if not self.collection_custom_rules:
                raise ValueError("A custom collection plan requires at least one collection rule.")
            collection_total = sum((row.weight_percent for row in self.collection_custom_rules), Decimal("0"))
        elif self.collection_plan_code == "LEGACY_THREE_POINT":
            collection_total = self.collection_upfront_percent + self.collection_six_month_percent + self.collection_twelve_month_percent
        else:
            collection_total = Decimal("100")
        if abs(collection_total - Decimal("100")) > tolerance:
            raise ValueError(f"Collection percentages must total 100%; current total is {collection_total}%.")
        treatment_keys = [item.cost_key for item in self.cost_treatments]
        if len(treatment_keys) != len(set(treatment_keys)):
            raise ValueError("Cost-treatment keys must be unique.")
        return self



class GovernmentProjectPreviewRequest(StrictModel):
    name: str = Field(default="Landowner project preview", min_length=2, max_length=240)
    input: LandownerProjectInput
    policy_pack_version_id: str | None = None
    partnership_method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"] = "GROSS_SALES"
    hybrid_variable_basis: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE"] = "GROSS_SALES"
    offered_share_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class GovernmentProjectCreate(StrictModel, CodeMixin):
    name: str = Field(min_length=2, max_length=240)
    code: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=20_000)
    input: LandownerProjectInput


class GovernmentProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=240)
    description: str | None = Field(default=None, max_length=20_000)
    input: LandownerProjectInput | None = None
    expected_input_hash: str | None = Field(default=None, min_length=64, max_length=64)


class GovernmentProjectSummaryOut(StrictModel):
    id: str
    name: str
    code: str
    description: str | None
    status: str
    project_kind: str
    latest_version_id: str
    version_number: int
    version_status: str
    input_hash: str
    source_input_hash: str | None
    updated_at: datetime


class GovernmentProjectDetailOut(StrictModel):
    project: ProjectOut
    version: ProjectVersionOut
    derived_summary: dict[str, Any]


class GovernmentProjectAssessmentRequest(StrictModel):
    policy_pack_version_id: str | None = None
    valuation_policy_pack_version_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valuation_policy_pack_version_id", "valuation_policy_version_id"),
    )
    mode: Literal["STRUCTURING", "OFFER_ASSESSMENT", "BID_COMPARISON", "RENEGOTIATION"] = "STRUCTURING"
    partnership_method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"] | None = None
    offered_share_percent: Decimal | None = Field(default=None, ge=0, le=100)
    upfront_amount: Decimal | None = Field(default=None, ge=0)
    public_discount_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)


class GovernmentProjectCaseCreate(StrictModel):
    policy_pack_version_id: str | None = None
    valuation_policy_pack_version_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("valuation_policy_pack_version_id", "valuation_policy_version_id"),
    )
    case_code: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    title: str = Field(min_length=2, max_length=240)
    mode: Literal["STRUCTURING", "OFFER_ASSESSMENT", "BID_COMPARISON", "RENEGOTIATION"] = "STRUCTURING"
    partnership_method: Literal["GROSS_SALES", "NET_SALES", "PROFIT_SHARE", "UPFRONT", "HYBRID", "MINIMUM_GUARANTEE"] | None = None
    offered_share_percent: Decimal | None = Field(default=None, ge=0, le=100)
    upfront_amount: Decimal | None = Field(default=None, ge=0)
    public_discount_rate_percent: Decimal | None = Field(default=None, ge=0, le=100)


class GovernmentCaseCreate(StrictModel):
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str
    scenario_id: str | None = None
    case_code: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    title: str = Field(min_length=2, max_length=240)
    mode: Literal["STRUCTURING", "OFFER_ASSESSMENT", "BID_COMPARISON", "RENEGOTIATION"] = "STRUCTURING"
    input_snapshot: dict[str, Any] = Field(default_factory=dict)


class GovernmentCaseUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    mode: Literal["STRUCTURING", "OFFER_ASSESSMENT", "BID_COMPARISON", "RENEGOTIATION"] | None = None
    input_snapshot: dict[str, Any] | None = None
    scenario_id: str | None = None


class GovernmentReviewRequest(StrictModel):
    notes: str = Field(min_length=1, max_length=20_000)


class GovernmentApprovalRequest(StrictModel):
    notes: str = Field(default="Approved subject to the conditions precedent recorded in the decision memorandum.", min_length=1, max_length=20_000)


class GovernmentOverrideCreate(StrictModel):
    field_path: str = Field(min_length=1, max_length=500, pattern=r"^[A-Za-z0-9_.-]+$")
    new_value: Any
    reason: str = Field(min_length=5, max_length=20_000)
    document_reference: str = Field(min_length=1, max_length=1000)


class GovernmentOverrideOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    government_case_id: str
    field_path: str
    previous_value: Any | None
    new_value: Any
    reason: str
    document_reference: str
    created_by_user_id: str
    approved_by_user_id: str | None
    created_at: datetime


class GovernmentCaseOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str | None
    scenario_id: str | None
    calculation_run_id: str | None
    case_code: str
    title: str
    mode: str
    status: str
    input_snapshot: dict[str, Any]
    input_hash: str
    output_snapshot: dict[str, Any] | None
    output_hash: str | None
    ledger_hash: str | None
    created_by_user_id: str
    submitted_by_user_id: str | None
    submitted_at: datetime | None
    technical_reviewer_user_id: str | None
    technical_reviewed_at: datetime | None
    technical_review_notes: str | None
    legal_reviewer_user_id: str | None
    legal_reviewed_at: datetime | None
    legal_review_notes: str | None
    approved_by_user_id: str | None
    approved_at: datetime | None
    approval_notes: str | None
    locked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GovernmentCaseSummaryOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str
    project_id: str
    project_version_id: str
    policy_pack_version_id: str
    valuation_policy_pack_version_id: str | None
    scenario_id: str | None
    calculation_run_id: str | None
    case_code: str
    title: str
    mode: str
    status: str
    input_hash: str
    output_hash: str | None
    ledger_hash: str | None
    created_by_user_id: str
    submitted_by_user_id: str | None
    technical_reviewer_user_id: str | None
    legal_reviewer_user_id: str | None
    approved_by_user_id: str | None
    created_at: datetime
    updated_at: datetime


# Backwards-compatible Python import alias for integrations compiled against pre-v2.1.
