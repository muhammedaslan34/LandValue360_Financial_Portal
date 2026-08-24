from io import BytesIO
from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import Organization, Project, Report, ReportVersion, User
from landvalue360_portal.services import create_staff_user


def register(client, email, org):
    r = client.post('/api/auth/register', json={'email': email, 'password': 'StrongPass123!', 'full_name': email.split('@')[0], 'organization_name': org, 'country': 'SY', 'phone': '', 'accepted_terms': True})
    assert r.status_code == 200, r.text
    return r.json()['csrf_token']


def login(client, email, password='StrongPass123!'):
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return r.json()['csrf_token']


def project_payload():
    return {'name':'Portal Project','description':'Operational submission','currency':'USD','gross_land_area_sqm':'10000','excluded_land_area_sqm':'0','title_reference':'TITLE-1','location':'Damascus','current_land_value':'2500000','far':'2','bcr':'0.4','planning_status':'concept','project_duration_months':36,'sales_duration_months':36,'land_uses':[{'code':'INVESTMENT','name':'Investment','percentage':'60'},{'code':'ROADS','name':'Roads','percentage':'20'},{'code':'GREEN','name':'Green','percentage':'10'},{'code':'PUBLIC','name':'Public','percentage':'10'}],'products':[{'code':'RES','name':'Residential','allocation_percentage':'100','sellable_efficiency_percentage':'80','unit_selling_price':'1000','currency':'USD','price_source':'market note','evidence_confidence':'MEDIUM'}],'costs':[{'name':'Construction','category':'CONSTRUCTION','amount':'8000000','currency':'USD','quantity_basis':None,'quantity':None,'unit_cost':None,'developer_share_percentage':'100','net_sales_deductible':False,'notes':None,'source':'estimate','evidence_confidence':'MEDIUM'}]}


def test_full_client_to_report_workflow(client):
    owner_csrf = register(client, 'owner@example.com', 'Owner Organization')
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == 'owner-organization'))
    r = client.post('/api/projects', json={'organization_id': org.id, 'name': 'Portal Project', 'currency': 'USD'}, headers={'X-CSRF-Token': owner_csrf})
    assert r.status_code == 201, r.text
    project_id = r.json()['id']
    r = client.put(f'/api/projects/{project_id}', json=project_payload(), headers={'X-CSRF-Token': owner_csrf})
    assert r.status_code == 200 and r.json()['calculations']['can_submit'] is True
    # Private document upload.
    r = client.post(f'/api/projects/{project_id}/documents', data={'category':'TITLE'}, files={'file':('title.pdf', b'%PDF-1.4\n%%EOF', 'application/pdf')}, headers={'X-CSRF-Token': owner_csrf})
    assert r.status_code == 201, r.text
    r = client.post(f'/api/projects/{project_id}/submit', headers={'X-CSRF-Token': owner_csrf})
    assert r.status_code == 200 and r.json()['status'] == 'SUBMITTED'
    # Create operations team directly as platform bootstrap would.
    with session_scope() as db:
        manager = create_staff_user(db, email='manager@example.com', password='StrongPass123!', full_name='Manager', role_code='TEAM_MANAGER')
        analyst = create_staff_user(db, email='analyst@example.com', password='StrongPass123!', full_name='Analyst', role_code='ANALYST')
        reviewer = create_staff_user(db, email='reviewer@example.com', password='StrongPass123!', full_name='Reviewer', role_code='REVIEWER')
        manager_id, analyst_id, reviewer_id = manager.id, analyst.id, reviewer.id
    client.post('/api/auth/logout', headers={'X-CSRF-Token': owner_csrf})
    manager_csrf = login(client, 'manager@example.com')
    for target in ('DATA_REVIEW', 'READY_FOR_ANALYSIS'):
        r=client.post(f'/api/operations/projects/{project_id}/status',json={'target_status':target,'reason':'workflow'},headers={'X-CSRF-Token':manager_csrf});assert r.status_code==200,r.text
    for uid, typ in ((analyst_id,'ANALYST'),(reviewer_id,'REVIEWER')):
        r=client.post(f'/api/operations/projects/{project_id}/assign',json={'user_id':uid,'assignment_type':typ},headers={'X-CSRF-Token':manager_csrf});assert r.status_code==200,r.text
    client.post('/api/auth/logout', headers={'X-CSRF-Token': manager_csrf})
    analyst_csrf = login(client, 'analyst@example.com')
    r=client.post(f'/api/operations/projects/{project_id}/status',json={'target_status':'IN_ANALYSIS','reason':'analysis started'},headers={'X-CSRF-Token':analyst_csrf});assert r.status_code==200,r.text
    # Export is accepted by the internal package contract.
    r=client.get(f'/api/operations/projects/{project_id}/export/internal.lv360');assert r.status_code==200 and r.content.startswith(b'PK')
    for report_type in ('EXECUTIVE','DETAILED'):
        r=client.post(f'/api/operations/projects/{project_id}/reports',data={'report_type':report_type,'language':'ar','calculation_run_reference':'INT-RUN-001'},files={'file':(f'{report_type}.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},headers={'X-CSRF-Token':analyst_csrf});assert r.status_code==201,r.text
    client.post('/api/auth/logout', headers={'X-CSRF-Token': analyst_csrf})
    reviewer_csrf = login(client, 'reviewer@example.com')
    with session_scope() as db:
        versions=list(db.scalars(select(ReportVersion)).all())
    for rv in versions:
        r=client.post(f'/api/operations/report-versions/{rv.id}/review',json={'action':'APPROVE'},headers={'X-CSRF-Token':reviewer_csrf});assert r.status_code==200,r.text
    client.post('/api/auth/logout', headers={'X-CSRF-Token': reviewer_csrf})
    manager_csrf = login(client, 'manager@example.com')
    r=client.post(f'/api/operations/projects/{project_id}/publish',headers={'X-CSRF-Token':manager_csrf});assert r.status_code==200,r.text
    client.post('/api/auth/logout', headers={'X-CSRF-Token': manager_csrf})
    owner_csrf = login(client, 'owner@example.com')
    with session_scope() as db:
        project=db.get(Project,project_id);assert project.status=='COMPLETED'
        published=list(db.scalars(select(ReportVersion).where(ReportVersion.status=='PUBLISHED')).all());assert len(published)==2
    for rv in published:
        r=client.get(f'/api/reports/{rv.id}/download');assert r.status_code==200 and r.content.startswith(b'%PDF')


def test_cross_tenant_isolation(client):
    csrf1=register(client,'one@example.com','Org One')
    with session_scope() as db: org=db.scalar(select(Organization).where(Organization.slug=='org-one'))
    r=client.post('/api/projects',json={'organization_id':org.id,'name':'Private','currency':'USD'},headers={'X-CSRF-Token':csrf1});pid=r.json()['id']
    client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf1})
    register(client,'two@example.com','Org Two')
    r=client.get(f'/api/projects/{pid}')
    assert r.status_code==403


def test_submitted_version_is_immutable(client):
    csrf=register(client,'immutable@example.com','Immutable Org')
    with session_scope() as db: org=db.scalar(select(Organization).where(Organization.slug=='immutable-org'))
    p=client.post('/api/projects',json={'organization_id':org.id,'name':'Immutable','currency':'USD'},headers={'X-CSRF-Token':csrf}).json();pid=p['id']
    client.put(f'/api/projects/{pid}',json=project_payload(),headers={'X-CSRF-Token':csrf})
    client.post(f'/api/projects/{pid}/submit',headers={'X-CSRF-Token':csrf})
    r=client.put(f'/api/projects/{pid}',json=project_payload(),headers={'X-CSRF-Token':csrf})
    assert r.status_code==409
    r=client.post(f'/api/projects/{pid}/revisions',json={'reason':'Required update'},headers={'X-CSRF-Token':csrf})
    assert r.status_code==201 and r.json()['version_number']==2
