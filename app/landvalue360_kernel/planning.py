"""Land, planning, and area-reconciliation calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable

from .decimal_utils import ONE, ZERO, as_json_number, decimal
from .exceptions import InputValidationError
from .validation import Severity, ValidationMessage, strict_boolean, strict_integer


class LandBasis(str, Enum):
    GROSS = "GROSS"
    NET = "NET"
    INVESTMENT = "INVESTMENT"


class AreaMethod(str, Enum):
    GFA_ALLOCATION = "GFA_ALLOCATION"
    UNIT_MIX = "UNIT_MIX"
    DIRECT_AREA = "DIRECT_AREA"


@dataclass(frozen=True, slots=True)
class LandUseInput:
    land_use_id: str
    name: str
    share: Decimal

    def __post_init__(self) -> None:
        if not self.land_use_id.strip():
            raise InputValidationError(
                "Land-use identifier cannot be empty.",
                path="planning.land_uses.land_use_id",
                code="LAND_USE_ID_EMPTY",
            )
        share = decimal(self.share, path=f"land_uses.{self.land_use_id}.share")
        if share < ZERO or share > ONE:
            raise InputValidationError("Land-use share must be between 0 and 1.")
        object.__setattr__(self, "share", share)


@dataclass(frozen=True, slots=True)
class ProductAreaInput:
    product_id: str
    name: str
    area_method: AreaMethod
    is_sellable: bool = True
    efficiency: Decimal | None = None
    gfa_allocation_share: Decimal | None = None
    unit_count: int | None = None
    average_net_unit_area_sqm: Decimal | None = None
    direct_sellable_area_sqm: Decimal | None = None
    direct_gfa_sqm: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.product_id.strip():
            raise InputValidationError(
                "Planning product identifier cannot be empty.",
                path="planning.products.product_id",
                code="PLANNING_PRODUCT_ID_EMPTY",
            )
        if not isinstance(self.is_sellable, bool):
            raise InputValidationError(
                "is_sellable must be a boolean.",
                path=f"planning.products.{self.product_id}.is_sellable",
                code="BOOLEAN_REQUIRED",
            )


@dataclass(frozen=True, slots=True)
class ProductAreaResult:
    product_id: str
    name: str
    area_method: AreaMethod
    gfa_sqm: Decimal
    sellable_area_sqm: Decimal
    efficiency: Decimal | None
    unit_count: int | None

    def to_dict(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "area_method": self.area_method.value,
            "gfa_sqm": as_json_number(self.gfa_sqm),
            "sellable_area_sqm": as_json_number(self.sellable_area_sqm),
            "efficiency": as_json_number(self.efficiency),
            "unit_count": self.unit_count,
        }


@dataclass(frozen=True, slots=True)
class PlanningInput:
    gross_land_area_sqm: Decimal
    excluded_land_area_sqm: Decimal
    far_land_basis: LandBasis
    far: Decimal
    bcr_land_basis: LandBasis
    bcr: Decimal
    land_uses: tuple[LandUseInput, ...]
    products: tuple[ProductAreaInput, ...]
    reconciliation_tolerance: Decimal = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class PlanningResult:
    gross_land_area_sqm: Decimal
    net_developable_land_sqm: Decimal
    far_basis_area_sqm: Decimal
    bcr_basis_area_sqm: Decimal
    total_gfa_sqm: Decimal
    maximum_footprint_sqm: Decimal
    indicative_floors: Decimal | None
    total_sellable_area_sqm: Decimal
    unallocated_gfa_sqm: Decimal
    land_use_areas: dict[str, Decimal]
    products: tuple[ProductAreaResult, ...]
    validation_messages: tuple[ValidationMessage, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "gross_land_area_sqm": as_json_number(self.gross_land_area_sqm),
            "net_developable_land_sqm": as_json_number(self.net_developable_land_sqm),
            "far_basis_area_sqm": as_json_number(self.far_basis_area_sqm),
            "bcr_basis_area_sqm": as_json_number(self.bcr_basis_area_sqm),
            "total_gfa_sqm": as_json_number(self.total_gfa_sqm),
            "maximum_footprint_sqm": as_json_number(self.maximum_footprint_sqm),
            "indicative_floors": as_json_number(self.indicative_floors),
            "total_sellable_area_sqm": as_json_number(self.total_sellable_area_sqm),
            "unallocated_gfa_sqm": as_json_number(self.unallocated_gfa_sqm),
            "land_use_areas": {key: as_json_number(value) for key, value in self.land_use_areas.items()},
            "products": [product.to_dict() for product in self.products],
            "validation_messages": [message.to_dict() for message in self.validation_messages],
        }


def _basis_area(
    basis: LandBasis,
    gross: Decimal,
    net: Decimal,
    investment: Decimal,
) -> Decimal:
    if basis == LandBasis.GROSS:
        return gross
    if basis == LandBasis.NET:
        return net
    return investment


def _require_unique_ids(values: Iterable[str], *, path: str, code: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise InputValidationError(
            f"Duplicate identifiers are not allowed: {', '.join(sorted(duplicates))}.",
            path=path,
            code=code,
        )


def calculate_planning(input_data: PlanningInput) -> PlanningResult:
    gross = decimal(input_data.gross_land_area_sqm, path="planning.gross_land_area_sqm")
    excluded = decimal(input_data.excluded_land_area_sqm, path="planning.excluded_land_area_sqm")
    far = decimal(input_data.far, path="planning.far")
    bcr = decimal(input_data.bcr, path="planning.bcr")
    tolerance = decimal(input_data.reconciliation_tolerance)

    if gross <= ZERO:
        raise InputValidationError("Gross land area must be greater than zero.", path="planning.gross_land_area_sqm")
    if excluded < ZERO or excluded > gross:
        raise InputValidationError(
            "Excluded land area must be between zero and gross land area.",
            path="planning.excluded_land_area_sqm",
        )
    if far < ZERO:
        raise InputValidationError("FAR cannot be negative.", path="planning.far")
    if bcr < ZERO or bcr > ONE:
        raise InputValidationError("BCR must be between 0 and 1.", path="planning.bcr")

    _require_unique_ids(
        (item.land_use_id for item in input_data.land_uses),
        path="planning.land_uses",
        code="DUPLICATE_LAND_USE_ID",
    )
    _require_unique_ids(
        (item.product_id for item in input_data.products),
        path="planning.products",
        code="DUPLICATE_PLANNING_PRODUCT_ID",
    )

    land_use_total = sum((item.share for item in input_data.land_uses), ZERO)
    if abs(land_use_total - ONE) > tolerance:
        raise InputValidationError(
            f"Land-use shares must sum to 1. Current total: {land_use_total}.",
            path="planning.land_uses",
            code="LAND_USE_NOT_RECONCILED",
        )
    land_use_areas = {item.land_use_id: gross * item.share for item in input_data.land_uses}
    net = gross - excluded
    investment_area = land_use_areas.get("INVESTMENT")
    if (
        input_data.far_land_basis == LandBasis.INVESTMENT
        or input_data.bcr_land_basis == LandBasis.INVESTMENT
    ) and investment_area is None:
        raise InputValidationError(
            "INVESTMENT land basis requires a land-use row with land_use_id='INVESTMENT'.",
            path="planning.land_uses",
            code="INVESTMENT_BASIS_AREA_MISSING",
        )
    investment_area = investment_area or ZERO
    far_basis_area = _basis_area(input_data.far_land_basis, gross, net, investment_area)
    bcr_basis_area = _basis_area(input_data.bcr_land_basis, gross, net, investment_area)
    total_gfa = far_basis_area * far
    maximum_footprint = bcr_basis_area * bcr
    indicative_floors = total_gfa / maximum_footprint if maximum_footprint > ZERO else None

    product_results: list[ProductAreaResult] = []
    validation_messages: list[ValidationMessage] = []
    allocated_gfa = ZERO
    total_sellable = ZERO

    for product in input_data.products:
        method = AreaMethod(product.area_method)
        efficiency: Decimal | None = None
        if product.is_sellable:
            if product.efficiency is None:
                raise InputValidationError(
                    "Sellable products require an efficiency.",
                    path=f"planning.products.{product.product_id}.efficiency",
                )
            efficiency = decimal(product.efficiency)
            if efficiency <= ZERO or efficiency > ONE:
                raise InputValidationError(
                    "Efficiency must be greater than 0 and no more than 1.",
                    path=f"planning.products.{product.product_id}.efficiency",
                )

        if method == AreaMethod.GFA_ALLOCATION:
            if product.gfa_allocation_share is None:
                raise InputValidationError(
                    "GFA allocation products require gfa_allocation_share.",
                    path=f"planning.products.{product.product_id}.gfa_allocation_share",
                )
            allocation = decimal(product.gfa_allocation_share)
            if allocation < ZERO or allocation > ONE:
                raise InputValidationError("GFA allocation share must be between 0 and 1.")
            gfa = total_gfa * allocation
            sellable = gfa * efficiency if product.is_sellable and efficiency is not None else ZERO
        elif method == AreaMethod.UNIT_MIX:
            if product.unit_count is None or product.average_net_unit_area_sqm is None:
                raise InputValidationError(
                    "Unit-mix products require unit_count and average_net_unit_area_sqm.",
                    path=f"planning.products.{product.product_id}",
                )
            if product.unit_count < 0:
                raise InputValidationError("Unit count cannot be negative.")
            average_area = decimal(product.average_net_unit_area_sqm)
            if average_area < ZERO:
                raise InputValidationError("Average unit area cannot be negative.")
            sellable = Decimal(product.unit_count) * average_area
            if not product.is_sellable or efficiency is None:
                raise InputValidationError("UNIT_MIX currently requires a sellable product with efficiency.")
            gfa = sellable / efficiency
        elif method == AreaMethod.DIRECT_AREA:
            if product.direct_gfa_sqm is not None and product.direct_sellable_area_sqm is not None:
                raise InputValidationError(
                    "DIRECT_AREA accepts either direct_gfa_sqm or direct_sellable_area_sqm, not both.",
                    path=f"planning.products.{product.product_id}",
                    code="DIRECT_AREA_AMBIGUOUS",
                )
            if product.direct_gfa_sqm is not None:
                gfa = decimal(product.direct_gfa_sqm)
                sellable = gfa * efficiency if product.is_sellable and efficiency is not None else ZERO
            elif product.direct_sellable_area_sqm is not None:
                if not product.is_sellable or efficiency is None:
                    raise InputValidationError("Direct sellable area requires a sellable product with efficiency.")
                sellable = decimal(product.direct_sellable_area_sqm)
                gfa = sellable / efficiency
            else:
                raise InputValidationError(
                    "DIRECT_AREA requires direct_gfa_sqm or direct_sellable_area_sqm.",
                    path=f"planning.products.{product.product_id}",
                )
            if gfa < ZERO or sellable < ZERO:
                raise InputValidationError("Direct areas cannot be negative.")
        else:  # pragma: no cover - protected by enum
            raise InputValidationError(f"Unsupported area method: {method}")

        allocated_gfa += gfa
        total_sellable += sellable
        product_results.append(
            ProductAreaResult(
                product_id=product.product_id,
                name=product.name,
                area_method=method,
                gfa_sqm=gfa,
                sellable_area_sqm=sellable,
                efficiency=efficiency,
                unit_count=product.unit_count,
            )
        )

    reconciliation_amount_tolerance = max(Decimal("0.01"), total_gfa * tolerance)
    difference = total_gfa - allocated_gfa
    if difference < -reconciliation_amount_tolerance:
        raise InputValidationError(
            f"Product GFA exceeds planned GFA by {abs(difference)} sqm.",
            path="planning.products",
            code="PRODUCT_GFA_EXCEEDS_PLAN",
        )
    if abs(difference) > reconciliation_amount_tolerance:
        validation_messages.append(
            ValidationMessage(
                Severity.WARNING,
                "UNALLOCATED_GFA",
                f"There are {difference} sqm of planned GFA not assigned to products.",
                "planning.products",
            )
        )

    return PlanningResult(
        gross_land_area_sqm=gross,
        net_developable_land_sqm=net,
        far_basis_area_sqm=far_basis_area,
        bcr_basis_area_sqm=bcr_basis_area,
        total_gfa_sqm=total_gfa,
        maximum_footprint_sqm=maximum_footprint,
        indicative_floors=indicative_floors,
        total_sellable_area_sqm=total_sellable,
        unallocated_gfa_sqm=max(ZERO, difference),
        land_use_areas=land_use_areas,
        products=tuple(product_results),
        validation_messages=tuple(validation_messages),
    )


def planning_input_from_dict(raw: dict[str, object]) -> PlanningInput:
    land_uses = tuple(
        LandUseInput(
            land_use_id=str(item["land_use_id"]),
            name=str(item.get("name", item["land_use_id"])),
            share=decimal(item["share"]),
        )
        for item in raw.get("land_uses", [])  # type: ignore[arg-type]
    )
    products: list[ProductAreaInput] = []
    for item in raw.get("products", []):  # type: ignore[assignment]
        products.append(
            ProductAreaInput(
                product_id=str(item["product_id"]),
                name=str(item.get("name", item["product_id"])),
                area_method=AreaMethod(str(item["area_method"])),
                is_sellable=strict_boolean(
                    item.get("is_sellable", True),
                    path=f"planning.products[{len(products)}].is_sellable",
                ),
                efficiency=decimal(item["efficiency"]) if item.get("efficiency") is not None else None,
                gfa_allocation_share=decimal(item["gfa_allocation_share"])
                if item.get("gfa_allocation_share") is not None
                else None,
                unit_count=strict_integer(
                    item["unit_count"],
                    path=f"planning.products[{len(products)}].unit_count",
                    minimum=0,
                )
                if item.get("unit_count") is not None
                else None,
                average_net_unit_area_sqm=decimal(item["average_net_unit_area_sqm"])
                if item.get("average_net_unit_area_sqm") is not None
                else None,
                direct_sellable_area_sqm=decimal(item["direct_sellable_area_sqm"])
                if item.get("direct_sellable_area_sqm") is not None
                else None,
                direct_gfa_sqm=decimal(item["direct_gfa_sqm"]) if item.get("direct_gfa_sqm") is not None else None,
            )
        )
    return PlanningInput(
        gross_land_area_sqm=decimal(raw["gross_land_area_sqm"]),
        excluded_land_area_sqm=decimal(raw.get("excluded_land_area_sqm", 0)),
        far_land_basis=LandBasis(str(raw.get("far_land_basis", "GROSS"))),
        far=decimal(raw["far"]),
        bcr_land_basis=LandBasis(str(raw.get("bcr_land_basis", "GROSS"))),
        bcr=decimal(raw.get("bcr", 0)),
        land_uses=land_uses,
        products=tuple(products),
        reconciliation_tolerance=decimal(raw.get("reconciliation_tolerance", "0.000001")),
    )
