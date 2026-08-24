from decimal import Decimal
import pytest
from landvalue360_portal.calculations import calculate_project


def sample():
    return {
        "identity": {"name": "Test", "currency": "USD"},
        "land": {"gross_land_area_sqm": "10000", "excluded_land_area_sqm": "1000"},
        "planning": {"far": "2"},
        "land_uses": [
            {"code": "INV", "name": "Investment", "percentage": "60"},
            {"code": "ROADS", "name": "Roads", "percentage": "20"},
            {"code": "GREEN", "name": "Green", "percentage": "10"},
            {"code": "PUBLIC", "name": "Public", "percentage": "10"},
        ],
        "products": [
            {"code": "RES", "name": "Residential", "allocation_percentage": "75", "sellable_efficiency_percentage": "80", "unit_selling_price": "1000", "currency": "USD"},
            {"code": "RET", "name": "Retail", "allocation_percentage": "25", "sellable_efficiency_percentage": "90", "unit_selling_price": "1500", "currency": "USD"},
        ],
        "costs": [
            {"name": "Construction", "category": "CONSTRUCTION", "amount": "8000000", "developer_share_percentage": "70", "net_sales_deductible": True},
            {"name": "Permit", "category": "PERMITS", "quantity": "1", "unit_cost": "250000", "developer_share_percentage": "100", "net_sales_deductible": False},
        ],
    }


def test_formula_results():
    r = calculate_project(sample())
    assert Decimal(r["net_land_area_sqm"]) == Decimal("9000.000000")
    assert Decimal(r["total_gfa_sqm"]) == Decimal("18000.000000")
    assert Decimal(r["total_sellable_area_sqm"]) == Decimal("14850.000000")
    assert Decimal(r["gross_sales_nominal"]) == Decimal("16875000.000000")
    assert Decimal(r["total_costs"]) == Decimal("8250000.000000")
    assert Decimal(r["developer_costs"]) == Decimal("5850000.000000")
    assert Decimal(r["landowner_costs"]) == Decimal("2400000.000000")
    assert r["can_submit"] is True


@pytest.mark.parametrize("land_total,product_total", [(99, 100), (101, 100), (100, 99), (100, 101)])
def test_percentage_totals_rejected(land_total, product_total):
    s = sample()
    s["land_uses"] = [{"code": "A", "name": "A", "percentage": str(land_total)}]
    s["products"] = [{"code": "P", "name": "P", "allocation_percentage": str(product_total), "sellable_efficiency_percentage": "80", "unit_selling_price": "1000"}]
    r = calculate_project(s)
    assert r["can_submit"] is False


def test_responsibility_out_of_range():
    s = sample(); s["costs"][0]["developer_share_percentage"] = "105"
    with pytest.raises(ValueError): calculate_project(s)


def test_zero_product_price_blocks_submission():
    snapshot = sample()
    snapshot['products'][0]['unit_selling_price'] = '0'
    result = calculate_project(snapshot)
    assert result['can_submit'] is False
    assert next(row for row in result['checks'] if row['code'] == 'PRODUCT_PRICES_COMPLETE')['status'] == 'FAIL'
