from decimal import Decimal

from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import Organization


def _register(client):
    r=client.post('/api/auth/register',json={
        'email':'v230@example.com','password':'StrongPass123!','full_name':'V230',
        'organization_name':'V230 Org','country':'SY','phone':'','accepted_terms':True,
    })
    assert r.status_code==200, r.text
    return r.json()['csrf_token']


def _project(client):
    csrf=_register(client)
    with session_scope() as db:
        org=db.scalar(select(Organization).where(Organization.slug=='v230-org'))
    r=client.post('/api/projects',json={'organization_id':org.id,'name':'Contract Semantics','currency':'USD'},headers={'X-CSRF-Token':csrf})
    assert r.status_code==201, r.text
    pid=r.json()['id']
    payload={
        'name':'Contract Semantics','currency':'USD','gross_land_area_sqm':'10000','excluded_land_area_sqm':'0',
        'current_land_value':'2500000','far':'2','bcr':'0.4','project_duration_months':36,'sales_duration_months':30,
        'land_uses':[{'code':'INVESTMENT','name':'Investment','percentage':'100'}],
        'products':[{'code':'RES','name':'Residential','allocation_percentage':'100','sellable_efficiency_percentage':'80','unit_selling_price':'1000','currency':'USD'}],
        # This legacy flag intentionally remains TRUE to prove v2.3 ignores development cost as a Net Sales deduction.
        'costs':[{'name':'Construction','category':'CONSTRUCTION','amount':'8000000','currency':'USD','developer_share_percentage':'100','net_sales_deductible':True}],
    }
    r=client.put(f'/api/projects/{pid}',json=payload,headers={'X-CSRF-Token':csrf})
    assert r.status_code==200, r.text
    return pid,csrf


def test_v230_net_sales_share_uses_revenue_net_sales_not_development_cost(client):
    pid,csrf=_project(client)
    state=client.get(f'/api/projects/{pid}/financial').json()
    model=state['financial_model']
    model['funding']['opening_cash']='12000000'
    model['funding']['total_developer_equity']='12000000'
    r=client.put(f'/api/projects/{pid}/financial',json=model,headers={'X-CSRF-Token':csrf})
    assert r.status_code==200, r.text
    r=client.post(f'/api/projects/{pid}/financial/runs',json={},headers={'X-CSRF-Token':csrf})
    assert r.status_code==201, r.text
    run=r.json()
    assert run['contract_engine_version']=='3.1.0'
    net=next(x for x in run['negotiation_results'] if x['method']=='NET_SALES')
    gross=Decimal(str(run['summary']['gross_sales']))
    net_sales=Decimal(str(run['summary']['net_sales']))
    base=Decimal(str(net['eligible_base_total']))
    # No sales discounts/incentives/refunds in this case, so Gross == Net and the contract base must match it.
    assert abs(gross-net_sales) < Decimal('0.01')
    assert abs(base-net_sales) < Decimal('0.01')
    # The 8m construction cost must NOT be deducted from the Net Sales base.
    assert abs(base-(net_sales-Decimal('8000000'))) > Decimal('1000000')
    assert net['residual_land_value'] is not None
    assert net['residual_equivalent_measure'] is not None


def test_v230_residual_reference_and_policy_cap_metadata(client):
    pid,csrf=_project(client)
    state=client.get(f'/api/projects/{pid}/financial').json()
    model=state['financial_model']; model['funding']['opening_cash']='12000000'; model['funding']['total_developer_equity']='12000000'
    client.put(f'/api/projects/{pid}/financial',json=model,headers={'X-CSRF-Token':csrf})
    run=client.post(f'/api/projects/{pid}/financial/runs',json={},headers={'X-CSRF-Token':csrf}).json()
    for row in run['negotiation_results']:
        assert 'ceiling_kind' in row
        assert 'residual_land_value' in row
        assert 'balanced_vs_residual_status' in row
        if row['ceiling_kind']=='POLICY_CAP_REACHED':
            assert row['technical_ceiling_established'] is False
            assert row['governing_constraint_id']=='MAX_LANDOWNER_SHARE_POLICY_CAP'


def test_user_report_regression_596m_sales_50pct_cannot_be_valid(client):
    csrf=_register(client)
    with session_scope() as db:
        org=db.scalar(select(Organization).where(Organization.slug=='v230-org'))
    r=client.post('/api/projects',json={'organization_id':org.id,'name':'Douma Regression','currency':'USD'},headers={'X-CSRF-Token':csrf})
    assert r.status_code==201, r.text
    pid=r.json()['id']
    # 100,000 sqm * FAR 7.45115 * 80% sellable = 596,092 sqm at $1,000/sqm.
    payload={
        'name':'Douma Regression','currency':'USD','gross_land_area_sqm':'100000','excluded_land_area_sqm':'0',
        'current_land_value':'1000000','far':'7.45115','bcr':'0.4','project_duration_months':60,'sales_duration_months':36,
        'land_uses':[{'code':'INVESTMENT','name':'Investment','percentage':'100'}],
        'products':[{'code':'MIX','name':'Mixed','allocation_percentage':'100','sellable_efficiency_percentage':'80','unit_selling_price':'1000','currency':'USD'}],
        'costs':[{'name':'Development','category':'CONSTRUCTION','amount':'340814826','currency':'USD','developer_share_percentage':'100','net_sales_deductible':True}],
    }
    r=client.put(f'/api/projects/{pid}',json=payload,headers={'X-CSRF-Token':csrf}); assert r.status_code==200,r.text
    state=client.get(f'/api/projects/{pid}/financial').json(); model=state['financial_model']
    model['funding']['opening_cash']='350000000'; model['funding']['total_developer_equity']='350000000'
    r=client.put(f'/api/projects/{pid}/financial',json=model,headers={'X-CSRF-Token':csrf}); assert r.status_code==200,r.text
    r=client.post(f'/api/projects/{pid}/financial/runs',json={},headers={'X-CSRF-Token':csrf}); assert r.status_code==201,r.text
    run=r.json(); net=next(x for x in run['negotiation_results'] if x['method']=='NET_SALES')
    net_sales=Decimal(str(run['summary']['net_sales']))
    assert abs(net_sales-Decimal('596092000')) < Decimal('1')
    # Mathematical sanity: 50% of Net Sales is 298.046m, leaving a 42.768826m loss before financing.
    fifty=net_sales*Decimal('0.50')
    assert abs(fifty-Decimal('298046000')) < Decimal('1')
    assert net_sales-Decimal('340814826')-fifty < 0
    # The governed negotiation solver therefore must not present 50% as a valid technical ceiling.
    ceiling=Decimal(str(net['technical_ceiling'])) if net.get('technical_ceiling') is not None else Decimal('0')
    assert ceiling < Decimal('0.50')
    assert net.get('ceiling_kind') == 'TECHNICAL_CEILING'
