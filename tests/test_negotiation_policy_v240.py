from decimal import Decimal

from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import Organization


def _d(value):
    return Decimal(str(value))


def _reference_project(client):
    response = client.post('/api/auth/register', json={
        'email': 'old-platform-reference-v240@example.com', 'password': 'StrongPass123!',
        'full_name': 'Reference User', 'organization_name': 'Reference User Org',
        'country': 'SY', 'phone': '', 'accepted_terms': True,
    })
    assert response.status_code == 200, response.text
    csrf = response.json()['csrf_token']
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == 'reference-user-org'))
    response = client.post('/api/projects', json={
        'organization_id': org.id, 'name': 'Old Platform Golden Reference', 'currency': 'USD',
    }, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 201, response.text
    pid = response.json()['id']
    payload = {
        'name': 'Old Platform Golden Reference', 'currency': 'USD',
        'gross_land_area_sqm': '600000', 'excluded_land_area_sqm': '0',
        'current_land_value': '60000000', 'far': '2.25', 'bcr': '0.40',
        'project_duration_months': 60, 'sales_duration_months': 36,
        'land_uses': [{'code': 'INVESTMENT', 'name': 'Investment', 'percentage': '100'}],
        'products': [{'code': 'MIX', 'name': 'Mixed', 'allocation_percentage': '100',
                      'sellable_efficiency_percentage': '100', 'unit_selling_price': '650', 'currency': 'USD'}],
        'costs': [{'name': 'Total development cost', 'category': 'CONSTRUCTION', 'amount': '556522674',
                   'currency': 'USD', 'developer_share_percentage': '100', 'net_sales_deductible': False}],
    }
    response = client.put(f'/api/projects/{pid}', json=payload, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200, response.text
    state = client.get(f'/api/projects/{pid}/financial').json()
    model = state['financial_model']
    model['sales']['duration_months'] = 36
    model['funding']['opening_cash'] = '7000000'
    model['funding']['total_developer_equity'] = '7000000'
    model['contract']['method'] = 'GROSS_SALES'
    model['contract']['share_rate'] = '0.18'
    response = client.put(f'/api/projects/{pid}/financial', json=model, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200, response.text
    response = client.post(f'/api/projects/{pid}/financial/runs', json={}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 201, response.text
    return response.json()


def test_old_platform_reference_balanced_is_separate_from_technical_ceiling(client):
    run = _reference_project(client)
    summary = run['summary']
    assert abs(_d(summary['gross_sales']) - Decimal('877500000')) < Decimal('0.01')
    assert abs(_d(summary['development_cost']) - Decimal('556522674')) < Decimal('0.01')
    assert abs(_d(summary['government_consideration']) - Decimal('157950000')) < Decimal('0.01')
    assert abs(_d(summary['developer_profit']) - Decimal('163027326')) < Decimal('0.01')

    row = next(item for item in run['negotiation_results'] if item['method'] == 'GROSS_SALES')
    floor = _d(row['fair_floor'])
    balanced = _d(row['balanced'])
    policy_ceiling = _d(row['policy_adjusted_ceiling'])
    technical = _d(row['technical_ceiling'])
    residual = _d(row['residual_equivalent_measure'])

    assert floor < balanced < policy_ceiling < technical
    assert Decimal('0.120') <= balanced <= Decimal('0.126')
    assert Decimal('0.138') <= policy_ceiling <= Decimal('0.144')
    assert Decimal('0.185') <= technical <= Decimal('0.210')
    assert Decimal('0.15') <= residual <= Decimal('0.18')
    assert balanced != technical
    assert row['offer_position'] in {'ABOVE_RISK_ADJUSTED_CEILING', 'BETWEEN_BALANCED_AND_RISK_CEILING'}

    # Policy formula: the policy ceiling is inside the technical range, then
    # Balanced is placed inside the Fair-Floor -> policy-ceiling range.
    expected_policy = floor + (technical - floor) * Decimal('0.42')
    expected_balanced = floor + (policy_ceiling - floor) * Decimal('0.56')
    assert abs(policy_ceiling - expected_policy) <= Decimal('0.002')
    assert abs(balanced - expected_balanced) <= Decimal('0.002')
