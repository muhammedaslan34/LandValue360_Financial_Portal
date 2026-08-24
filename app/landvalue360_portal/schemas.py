from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)
    country: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=80)
    accepted_terms: bool

    @model_validator(mode="after")
    def terms(self):
        if not self.accepted_terms:
            raise ValueError("Terms and professional declaration must be accepted")
        return self


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class LandUseIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    percentage: Decimal = Field(ge=0, le=100)


class ProductIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    allocation_percentage: Decimal = Field(ge=0, le=100)
    sellable_efficiency_percentage: Decimal = Field(gt=0, le=100)
    unit_selling_price: Decimal = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    price_source: str | None = Field(default=None, max_length=250)
    evidence_confidence: str | None = Field(default=None, max_length=30)


class CostItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=80)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    quantity_basis: str | None = Field(default=None, max_length=80)
    quantity: Decimal | None = Field(default=None, ge=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)
    developer_share_percentage: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    net_sales_deductible: bool = False
    notes: str | None = None
    source: str | None = Field(default=None, max_length=250)
    evidence_confidence: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def amount_or_rate(self):
        if self.amount is None and (self.quantity is None or self.unit_cost is None):
            raise ValueError("Provide an amount or both quantity and unit cost")
        return self


class ProjectDraftIn(BaseModel):
    name: str = Field(min_length=2, max_length=240)
    description: str | None = None
    currency: str = Field(default="USD", min_length=3, max_length=8)
    gross_land_area_sqm: Decimal = Field(ge=0)
    excluded_land_area_sqm: Decimal = Field(ge=0)
    title_reference: str | None = Field(default=None, max_length=250)
    location: str | None = Field(default=None, max_length=300)
    current_land_value: Decimal | None = Field(default=None, ge=0)
    far: Decimal = Field(ge=0)
    bcr: Decimal | None = Field(default=None, ge=0)
    planning_status: str | None = Field(default=None, max_length=200)
    project_duration_months: int | None = Field(default=None, ge=1, le=600)
    sales_duration_months: int | None = Field(default=None, ge=1, le=600)
    land_uses: list[LandUseIn] = Field(default_factory=list)
    products: list[ProductIn] = Field(default_factory=list)
    costs: list[CostItemIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def geometry(self):
        if self.excluded_land_area_sqm > self.gross_land_area_sqm:
            raise ValueError("Excluded land area cannot exceed gross land area")
        return self


class InformationRequestIn(BaseModel):
    subject: str = Field(min_length=3, max_length=250)
    message: str = Field(min_length=3, max_length=5000)


class AssignmentIn(BaseModel):
    user_id: str
    assignment_type: Literal["ANALYST", "REVIEWER"]


class StatusIn(BaseModel):
    target_status: str
    reason: str | None = Field(default=None, max_length=2000)


class ReportReviewIn(BaseModel):
    action: Literal["APPROVE", "REJECT", "PUBLISH"]
    notes: str | None = Field(default=None, max_length=2000)
