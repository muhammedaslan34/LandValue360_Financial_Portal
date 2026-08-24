from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

ZERO = Decimal("0")
HUNDRED = Decimal("100")
Q = Decimal("0.000001")


def D(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def q(value: Decimal) -> Decimal:
    return value.quantize(Q, rounding=ROUND_HALF_UP)


def calculate_project(snapshot: dict[str, Any]) -> dict[str, Any]:
    land = snapshot.get("land") or {}
    planning = snapshot.get("planning") or {}
    land_uses = snapshot.get("land_uses") or []
    products = snapshot.get("products") or []
    costs = snapshot.get("costs") or []

    gross = D(land.get("gross_land_area_sqm"))
    excluded = D(land.get("excluded_land_area_sqm"))
    far = D(planning.get("far"))
    net = gross - excluded
    total_gfa = net * far

    land_use_total = sum((D(row.get("percentage")) for row in land_uses), ZERO)
    product_total = sum((D(row.get("allocation_percentage")) for row in products), ZERO)

    computed_land_uses = []
    for row in land_uses:
        pct = D(row.get("percentage"))
        computed_land_uses.append({**row, "area_sqm": str(q(net * pct / HUNDRED))})

    computed_products = []
    total_sellable = ZERO
    total_sales = ZERO
    for row in products:
        allocation = D(row.get("allocation_percentage"))
        efficiency = D(row.get("sellable_efficiency_percentage"))
        price = D(row.get("unit_selling_price"))
        product_gfa = total_gfa * allocation / HUNDRED
        sellable = product_gfa * efficiency / HUNDRED
        sales = sellable * price
        total_sellable += sellable
        total_sales += sales
        computed_products.append({
            **row,
            "product_gfa_sqm": str(q(product_gfa)),
            "sellable_area_sqm": str(q(sellable)),
            "gross_sales": str(q(sales)),
        })

    total_costs = ZERO
    developer_costs = ZERO
    landowner_costs = ZERO
    deductible = ZERO
    computed_costs = []
    for row in costs:
        amount = D(row.get("amount"))
        quantity = D(row.get("quantity"))
        unit_cost = D(row.get("unit_cost"))
        if amount <= ZERO and quantity > ZERO and unit_cost >= ZERO:
            amount = quantity * unit_cost
        developer_share = D(row.get("developer_share_percentage"), "100")
        if developer_share < ZERO or developer_share > HUNDRED:
            raise ValueError("Developer responsibility must be between 0 and 100")
        landowner_share = HUNDRED - developer_share
        dev_amount = amount * developer_share / HUNDRED
        land_amount = amount - dev_amount
        total_costs += amount
        developer_costs += dev_amount
        landowner_costs += land_amount
        if bool(row.get("net_sales_deductible")):
            deductible += amount
        computed_costs.append({
            **row,
            "amount": str(q(amount)),
            "landowner_share_percentage": str(q(landowner_share)),
            "developer_amount": str(q(dev_amount)),
            "landowner_amount": str(q(land_amount)),
        })

    checks = []
    def add(code: str, passed: bool, actual: Any, required: Any, ar: str, en: str):
        checks.append({
            "code": code,
            "status": "PASS" if passed else "FAIL",
            "actual_value": str(actual),
            "required_value": str(required),
            "message_ar": ar,
            "message_en": en,
        })

    add("GROSS_LAND_POSITIVE", gross > ZERO, gross, "> 0", "يجب أن تكون مساحة الأرض الإجمالية أكبر من صفر.", "Gross land area must be greater than zero.")
    add("EXCLUDED_AREA_VALID", excluded >= ZERO and excluded < gross if gross > ZERO else False, excluded, f"0 <= value < {gross}", "المساحة المستبعدة يجب أن تكون غير سالبة وأقل من مساحة الأرض.", "Excluded area must be non-negative and below gross land area.")
    add("FAR_POSITIVE", far > ZERO, far, "> 0", "يجب أن يكون معامل البناء أكبر من صفر.", "FAR must be greater than zero.")
    add("LAND_USE_TOTAL_100", abs(land_use_total - HUNDRED) <= Decimal("0.0001"), land_use_total, "100", "يجب أن يساوي مجموع نسب استخدامات الأرض 100%.", "Land-use percentages must total 100%.")
    add("PRODUCT_TOTAL_100", abs(product_total - HUNDRED) <= Decimal("0.0001"), product_total, "100", "يجب أن يساوي مجموع نسب المنتجات 100%.", "Product allocations must total 100%.")
    add("PRODUCTS_PRESENT", len(products) > 0, len(products), "> 0", "يجب إضافة منتج عقاري واحد على الأقل.", "At least one product is required.")
    add("PRODUCT_PRICES_COMPLETE", all(D(row.get("unit_selling_price")) > ZERO for row in products), "all", ">= 0", "يجب إدخال سعر بيع أكبر من صفر لكل منتج.", "All product prices must be greater than zero.")
    add("EFFICIENCIES_VALID", all(ZERO < D(row.get("sellable_efficiency_percentage")) <= HUNDRED for row in products), "all", "0 < value <= 100", "كفاءة البيع لكل منتج يجب أن تكون أكبر من صفر ولا تتجاوز 100%.", "Sellable efficiency must be above zero and at most 100%.")
    add("COST_RESPONSIBILITIES_VALID", all(ZERO <= D(row.get("developer_share_percentage"), "100") <= HUNDRED for row in costs), "all", "0..100", "نسبة مسؤولية المطور لكل كلفة يجب أن تكون بين 0 و100%.", "Developer cost responsibility must be within 0..100%.")

    missing = []
    for field, label in [(gross, "gross_land_area_sqm"), (far, "far")]:
        if field <= ZERO:
            missing.append(label)
    if not land_uses:
        missing.append("land_uses")
    if not products:
        missing.append("products")

    return {
        "gross_land_area_sqm": str(q(gross)),
        "excluded_land_area_sqm": str(q(excluded)),
        "net_land_area_sqm": str(q(net)),
        "total_gfa_sqm": str(q(total_gfa)),
        "land_use_percentage_total": str(q(land_use_total)),
        "product_allocation_percentage_total": str(q(product_total)),
        "total_sellable_area_sqm": str(q(total_sellable)),
        "gross_sales_nominal": str(q(total_sales)),
        "total_costs": str(q(total_costs)),
        "developer_costs": str(q(developer_costs)),
        "landowner_costs": str(q(landowner_costs)),
        "net_sales_deductible_costs": str(q(deductible)),
        "land_uses": computed_land_uses,
        "products": computed_products,
        "costs": computed_costs,
        "checks": checks,
        "missing_fields": missing,
        "can_submit": not missing and all(row["status"] == "PASS" for row in checks),
        "disclaimer_ar": "حسابات أولية للتحقق من المدخلات، وليست تقييماً معتمداً أو توصية استثمارية.",
        "disclaimer_en": "Preliminary input checks only; not an accredited valuation or investment recommendation.",
    }
