import hashlib
import io
import json
import zipfile
from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import Organization


def register(client, email='files@example.com', org_name='Files Org'):
    response = client.post('/api/auth/register', json={
        'email': email, 'password': 'StrongPass123!', 'full_name': 'Files User',
        'organization_name': org_name, 'country': 'SY', 'phone': '', 'accepted_terms': True,
    })
    assert response.status_code == 200
    return response.json()['csrf_token']


def create_project(client, csrf, slug='files-org'):
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == slug))
    response = client.post('/api/projects', json={'organization_id': org.id, 'name': 'Files Project', 'currency': 'USD'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 201
    return response.json()['id']


def valid_payload():
    return {'name':'Files Project','description':'','currency':'USD','gross_land_area_sqm':'10000','excluded_land_area_sqm':'0','title_reference':'T','location':'X','current_land_value':'1','far':'2','bcr':'0.4','planning_status':'concept','project_duration_months':36,'sales_duration_months':36,'land_uses':[{'code':'INV','name':'Investment','percentage':'100'}],'products':[{'code':'RES','name':'Residential','allocation_percentage':'100','sellable_efficiency_percentage':'80','unit_selling_price':'1000','currency':'USD','price_source':None,'evidence_confidence':None}],'costs':[{'name':'Construction','category':'CONSTRUCTION','amount':'5000000','currency':'USD','quantity_basis':None,'quantity':None,'unit_cost':None,'developer_share_percentage':'100','net_sales_deductible':False,'notes':None,'source':None,'evidence_confidence':None}]}


def test_rejects_fake_office_file(client):
    csrf = register(client)
    project_id = create_project(client, csrf)
    fake_zip = io.BytesIO()
    with zipfile.ZipFile(fake_zip, 'w') as archive:
        archive.writestr('random.txt', 'not office')
    response = client.post(f'/api/projects/{project_id}/documents', data={'category':'OTHER'}, files={'file':('fake.docx', fake_zip.getvalue(), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 422


def test_document_list_and_delete(client):
    csrf = register(client, 'doc@example.com', 'Doc Org')
    project_id = create_project(client, csrf, 'doc-org')
    response = client.post(f'/api/projects/{project_id}/documents', data={'category':'TITLE'}, files={'file':('title.pdf', b'%PDF-1.4\n%%EOF', 'application/pdf')}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 201
    document_id = response.json()['id']
    assert len(client.get(f'/api/projects/{project_id}/documents').json()) == 1
    response = client.delete(f'/api/documents/{document_id}', headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    assert client.get(f'/api/projects/{project_id}/documents').json() == []


def test_portal_package_checksums(client):
    csrf = register(client, 'package@example.com', 'Package Org')
    project_id = create_project(client, csrf, 'package-org')
    assert client.put(f'/api/projects/{project_id}', json=valid_payload(), headers={'X-CSRF-Token': csrf}).status_code == 200
    response = client.get(f'/api/projects/{project_id}/export/portal.lv360')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert {'manifest.json','submission.json','schema.json','documents-manifest.json','declarations.json','checksums.json'} <= names
        checksums = json.loads(archive.read('checksums.json'))
        for name, expected in checksums.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected


def test_internal_package_uses_native_platform_contract(client):
    csrf = register(client, 'internal-package@example.com', 'Internal Package Org')
    project_id = create_project(client, csrf, 'internal-package-org')
    assert client.put(f'/api/projects/{project_id}', json=valid_payload(), headers={'X-CSRF-Token': csrf}).status_code == 200
    response = client.get(f'/api/projects/{project_id}/export/internal.lv360')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {'manifest.json', 'project.json', 'versions.json', 'scenarios.json'}
        manifest = json.loads(archive.read('manifest.json'))
        project = json.loads(archive.read('project.json'))
        versions = json.loads(archive.read('versions.json'))
        assert manifest['format'] == 'LANDVALUE360_PROJECT_PACKAGE'
        assert manifest['format_version'] == '2.1.1'
        assert manifest['source_platform_version'] == 'financial-portal-2.5.0'
        assert manifest['project']['project_kind'] == 'SHARED'
        assert manifest['compatibility']['native_detailed_contract'] is True
        assert project['project_kind'] == 'SHARED'
        snapshot = versions[0]['input_snapshot']
        assert snapshot['portal_submission']['requires_analyst_completion'] is True
        assert snapshot['portal_submission']['target_internal_contract'] == '2.1.1'
        assert snapshot['planning_products'][0]['product_id'] == 'RES'
        assert snapshot['planning_products'][0]['gfa_allocation_share'] == '1.0'
        assert snapshot['planning_products'][0]['efficiency'] == '0.8'
        assert snapshot['products'][0]['product_id'] == 'RES'
        assert snapshot['products'][0]['unit_price'] == '1000'
        assert 'gfa_allocation_share' not in snapshot['products'][0]
        assert versions[0]['source_input_snapshot']['products'][0]['code'] == 'RES'
        effective_hash = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')).hexdigest()
        assert versions[0]['input_hash'] == effective_hash
        assert manifest['compatibility']['effective_input_hash'] == effective_hash
        assert manifest['compatibility']['source_input_hash'] == versions[0]['source_input_hash']
        assert manifest['compatibility']['monthly_financial_inputs_included'] is True
        assert snapshot['finance_model']['allow_negative_cash'] is False
        assert snapshot['finance_model']['spend_policy'] == 'CASH_DRIVEN'
        assert snapshot['portal_financial_model']['sales']['collection_rules']
        for name, meta in manifest['files'].items():
            assert len(archive.read(name)) == meta['bytes']
            assert hashlib.sha256(archive.read(name)).hexdigest() == meta['sha256']
