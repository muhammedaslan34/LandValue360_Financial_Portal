from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import Organization, OrganizationMember, Project, ReportVersion, User
from landvalue360_portal.services import create_staff_user


def register(client, email, org):
    response = client.post('/api/auth/register', json={'email':email,'password':'StrongPass123!','full_name':email.split('@')[0],'organization_name':org,'country':'SY','phone':'','accepted_terms':True})
    assert response.status_code == 200
    return response.json()['csrf_token']


def login(client, email):
    response = client.post('/api/auth/login', json={'email':email,'password':'StrongPass123!'})
    assert response.status_code == 200
    return response.json()['csrf_token']


def payload():
    return {'name':'Workflow','description':'','currency':'USD','gross_land_area_sqm':'1000','excluded_land_area_sqm':'0','title_reference':'T','location':'X','current_land_value':'100','far':'1','bcr':None,'planning_status':'concept','project_duration_months':12,'sales_duration_months':12,'land_uses':[{'code':'INV','name':'Investment','percentage':'100'}],'products':[{'code':'P','name':'Product','allocation_percentage':'100','sellable_efficiency_percentage':'100','unit_selling_price':'1000','currency':'USD','price_source':None,'evidence_confidence':None}],'costs':[{'name':'Cost','category':'CONSTRUCTION','amount':'500000','currency':'USD','quantity_basis':None,'quantity':None,'unit_cost':None,'developer_share_percentage':'100','net_sales_deductible':False,'notes':None,'source':None,'evidence_confidence':None}]}


def setup_submitted(client):
    csrf = register(client, 'workflow-owner@example.com', 'Workflow Owner')
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == 'workflow-owner'))
    project_id = client.post('/api/projects', json={'organization_id':org.id,'name':'Workflow','currency':'USD'}, headers={'X-CSRF-Token':csrf}).json()['id']
    client.put(f'/api/projects/{project_id}', json=payload(), headers={'X-CSRF-Token':csrf})
    client.post(f'/api/projects/{project_id}/submit', headers={'X-CSRF-Token':csrf})
    return project_id, csrf


def test_missing_information_revision_cycle(client):
    project_id, owner_csrf = setup_submitted(client)
    with session_scope() as db:
        manager = create_staff_user(db,email='wf-manager@example.com',password='StrongPass123!',full_name='Manager',role_code='TEAM_MANAGER')
    client.post('/api/auth/logout', headers={'X-CSRF-Token':owner_csrf})
    manager_csrf = login(client,'wf-manager@example.com')
    client.post(f'/api/operations/projects/{project_id}/status',json={'target_status':'DATA_REVIEW','reason':'review'},headers={'X-CSRF-Token':manager_csrf})
    response=client.post(f'/api/operations/projects/{project_id}/information-requests',json={'subject':'Missing title','message':'Upload title document'},headers={'X-CSRF-Token':manager_csrf})
    assert response.status_code==201
    request_id=response.json()['id']
    client.post('/api/auth/logout',headers={'X-CSRF-Token':manager_csrf})
    owner_csrf=login(client,'workflow-owner@example.com')
    assert client.post(f'/api/information-requests/{request_id}/messages',json={'message':'Will provide'},headers={'X-CSRF-Token':owner_csrf}).status_code==200
    response=client.post(f'/api/projects/{project_id}/revisions',json={'reason':'Requested completion'},headers={'X-CSRF-Token':owner_csrf})
    assert response.status_code==201 and response.json()['version_number']==2
    assert client.put(f'/api/projects/{project_id}',json=payload(),headers={'X-CSRF-Token':owner_csrf}).status_code==200
    response=client.post(f'/api/projects/{project_id}/submit',headers={'X-CSRF-Token':owner_csrf})
    assert response.status_code==200 and response.json()['status']=='DATA_REVIEW'


def test_report_publication_requires_two_approved_reports(client):
    project_id, owner_csrf = setup_submitted(client)
    with session_scope() as db:
        manager=create_staff_user(db,email='pub-manager@example.com',password='StrongPass123!',full_name='Manager',role_code='TEAM_MANAGER')
        analyst=create_staff_user(db,email='pub-analyst@example.com',password='StrongPass123!',full_name='Analyst',role_code='ANALYST')
        reviewer=create_staff_user(db,email='pub-reviewer@example.com',password='StrongPass123!',full_name='Reviewer',role_code='REVIEWER')
        analyst_id,reviewer_id=analyst.id,reviewer.id
    client.post('/api/auth/logout',headers={'X-CSRF-Token':owner_csrf})
    manager_csrf=login(client,'pub-manager@example.com')
    for status in ('DATA_REVIEW','READY_FOR_ANALYSIS'):
        assert client.post(f'/api/operations/projects/{project_id}/status',json={'target_status':status},headers={'X-CSRF-Token':manager_csrf}).status_code==200
    for user_id,kind in ((analyst_id,'ANALYST'),(reviewer_id,'REVIEWER')):
        client.post(f'/api/operations/projects/{project_id}/assign',json={'user_id':user_id,'assignment_type':kind},headers={'X-CSRF-Token':manager_csrf})
    client.post('/api/auth/logout',headers={'X-CSRF-Token':manager_csrf})
    analyst_csrf=login(client,'pub-analyst@example.com')
    client.post(f'/api/operations/projects/{project_id}/status',json={'target_status':'IN_ANALYSIS'},headers={'X-CSRF-Token':analyst_csrf})
    response=client.post(f'/api/operations/projects/{project_id}/reports',data={'report_type':'EXECUTIVE','language':'ar','calculation_run_reference':'RUN'},files={'file':('executive.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},headers={'X-CSRF-Token':analyst_csrf})
    assert response.status_code==201
    rv_id=response.json()['report_version_id']
    client.post('/api/auth/logout',headers={'X-CSRF-Token':analyst_csrf})
    reviewer_csrf=login(client,'pub-reviewer@example.com')
    client.post(f'/api/operations/report-versions/{rv_id}/review',json={'action':'APPROVE'},headers={'X-CSRF-Token':reviewer_csrf})
    client.post('/api/auth/logout',headers={'X-CSRF-Token':reviewer_csrf})
    manager_csrf=login(client,'pub-manager@example.com')
    response=client.post(f'/api/operations/projects/{project_id}/publish',headers={'X-CSRF-Token':manager_csrf})
    assert response.status_code==409


def test_admin_can_create_org_and_membership(client):
    owner_csrf=register(client,'member@example.com','Member Org')
    with session_scope() as db:
        admin=create_staff_user(db,email='admin@example.com',password='StrongPass123!',full_name='Admin',role_code='PLATFORM_ADMIN')
        target=db.scalar(select(User).where(User.email=='member@example.com'))
        target_id=target.id
    client.post('/api/auth/logout',headers={'X-CSRF-Token':owner_csrf})
    admin_csrf=login(client,'admin@example.com')
    org=client.post('/api/admin/organizations',json={'name':'Second Org','kind':'LANDOWNER'},headers={'X-CSRF-Token':admin_csrf})
    assert org.status_code==201
    response=client.post('/api/admin/memberships',json={'organization_id':org.json()['id'],'user_id':target_id,'role':'LANDOWNER'},headers={'X-CSRF-Token':admin_csrf})
    assert response.status_code==201
    rows=client.get('/api/admin/memberships').json()
    assert any(row['organization_id']==org.json()['id'] and row['user_id']==target_id for row in rows)


def test_assignment_requires_matching_staff_role(client):
    project_id, owner_csrf = setup_submitted(client)
    with session_scope() as db:
        manager = create_staff_user(db, email='role-manager@example.com', password='StrongPass123!', full_name='Manager', role_code='TEAM_MANAGER')
        plain = db.scalar(select(User).where(User.email == 'workflow-owner@example.com'))
        plain_id = plain.id
    client.post('/api/auth/logout', headers={'X-CSRF-Token': owner_csrf})
    manager_csrf = login(client, 'role-manager@example.com')
    response = client.post(
        f'/api/operations/projects/{project_id}/assign',
        json={'user_id': plain_id, 'assignment_type': 'ANALYST'},
        headers={'X-CSRF-Token': manager_csrf},
    )
    assert response.status_code == 422


def test_analyst_cannot_cancel_project(client):
    project_id, owner_csrf = setup_submitted(client)
    with session_scope() as db:
        manager = create_staff_user(db, email='state-manager@example.com', password='StrongPass123!', full_name='Manager', role_code='TEAM_MANAGER')
        analyst = create_staff_user(db, email='state-analyst@example.com', password='StrongPass123!', full_name='Analyst', role_code='ANALYST')
        analyst_id = analyst.id
    client.post('/api/auth/logout', headers={'X-CSRF-Token': owner_csrf})
    manager_csrf = login(client, 'state-manager@example.com')
    for status in ('DATA_REVIEW', 'READY_FOR_ANALYSIS'):
        assert client.post(f'/api/operations/projects/{project_id}/status', json={'target_status': status}, headers={'X-CSRF-Token': manager_csrf}).status_code == 200
    assert client.post(f'/api/operations/projects/{project_id}/assign', json={'user_id': analyst_id, 'assignment_type': 'ANALYST'}, headers={'X-CSRF-Token': manager_csrf}).status_code == 200
    client.post('/api/auth/logout', headers={'X-CSRF-Token': manager_csrf})
    analyst_csrf = login(client, 'state-analyst@example.com')
    response = client.post(f'/api/operations/projects/{project_id}/status', json={'target_status': 'CANCELLED'}, headers={'X-CSRF-Token': analyst_csrf})
    assert response.status_code == 403


def test_admin_manages_templates_file_policies_and_privacy(client):
    owner_csrf = register(client, 'privacy-member@example.com', 'Privacy Member Org')
    response = client.post('/api/account/privacy-requests', json={'request_type': 'EXPORT', 'notes': 'Please export my data'}, headers={'X-CSRF-Token': owner_csrf})
    assert response.status_code == 201
    with session_scope() as db:
        create_staff_user(db, email='settings-admin@example.com', password='StrongPass123!', full_name='Settings Admin', role_code='PLATFORM_ADMIN')
    client.post('/api/auth/logout', headers={'X-CSRF-Token': owner_csrf})
    csrf = login(client, 'settings-admin@example.com')
    templates = client.get('/api/admin/email-templates')
    assert templates.status_code == 200 and any(row['code'] == 'REPORT_READY' for row in templates.json())
    response = client.put('/api/admin/email-templates/REPORT_READY', json={
        'subject_ar': 'التقرير جاهز', 'subject_en': 'Report ready',
        'body_ar': '{body}\n{link}', 'body_en': '{body}\n{link}', 'active': True,
    }, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    policies = client.get('/api/admin/file-policies')
    assert policies.status_code == 200 and any(row['extension'] == '.pdf' for row in policies.json())
    response = client.put('/api/admin/file-policies/.pdf', json={
        'mime_types': ['application/pdf'], 'max_size_bytes': 8 * 1024 * 1024, 'active': True,
    }, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    rows = client.get('/api/admin/privacy-requests').json()
    row = next(item for item in rows if item['email'] == 'privacy-member@example.com')
    response = client.patch(f"/api/admin/privacy-requests/{row['id']}", json={'status': 'IN_PROGRESS'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200 and response.json()['status'] == 'IN_PROGRESS'


def test_platform_admin_can_open_and_download_cross_organization_project(client):
    from hashlib import sha256

    from landvalue360_portal.models import AuditLog, ProjectVersion, Report
    from landvalue360_portal.models import ReportVersion
    from landvalue360_portal.storage import get_storage

    owner_csrf = register(client, 'cross-owner@example.com', 'Cross Owner')
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == 'cross-owner'))
    create = client.post(
        '/api/projects', json={'organization_id': org.id, 'name': 'Cross Organization Project', 'currency': 'USD'},
        headers={'X-CSRF-Token': owner_csrf},
    )
    assert create.status_code == 201, create.text
    project_id = create.json()['id']
    assert client.put(f'/api/projects/{project_id}', json=payload(), headers={'X-CSRF-Token': owner_csrf}).status_code == 200

    report_bytes = b'%PDF-1.4\n% admin draft report\n%%EOF\n'
    with session_scope() as db:
        project = db.get(Project, project_id)
        version = db.get(ProjectVersion, project.current_version_id)
        owner = db.scalar(select(User).where(User.email == 'cross-owner@example.com'))
        key = get_storage().put(project_id=project.id, data=report_bytes, suffix='.pdf')
        report = Report(
            project_id=project.id, project_version_id=version.id, report_type='EXECUTIVE',
            language='ar', status='DRAFT', created_by=owner.id, updated_by=owner.id,
        )
        db.add(report); db.flush()
        report_version = ReportVersion(
            report_id=report.id, version_number=1, uploaded_by=owner.id, storage_key=key,
            original_name='draft-report.pdf', mime_type='application/pdf', size_bytes=len(report_bytes),
            checksum=sha256(report_bytes).hexdigest(), status='DRAFT', created_by=owner.id, updated_by=owner.id,
        )
        db.add(report_version); db.flush()
        report.current_version_id = report_version.id
        report_version_id = report_version.id
        create_staff_user(
            db, email='cross-admin@example.com', password='StrongPass123!',
            full_name='Cross Admin', role_code='PLATFORM_ADMIN',
        )

    client.post('/api/auth/logout', headers={'X-CSRF-Token': owner_csrf})
    admin_csrf = login(client, 'cross-admin@example.com')

    projects = client.get('/api/admin/projects?q=cross-owner')
    assert projects.status_code == 200, projects.text
    row = next(item for item in projects.json() if item['id'] == project_id)
    assert row['actions']['project'] == f'/portal/projects/{project_id}'
    assert row['actions']['financial'] == f'/portal/projects/{project_id}/financial'

    assert client.get(f'/portal/projects/{project_id}').status_code == 200
    assert client.get(f'/portal/projects/{project_id}/financial').status_code == 200
    assert client.get(f'/api/projects/{project_id}').status_code == 200
    assert client.get(f'/api/projects/{project_id}/financial').status_code == 200
    assert client.get(f'/api/projects/{project_id}/export.xlsx').status_code == 200

    overview = client.get(f'/api/admin/projects/{project_id}/overview')
    assert overview.status_code == 200, overview.text
    draft = next(item for item in overview.json()['reports'] if item['version_id'] == report_version_id)
    assert draft['status'] == 'DRAFT'
    assert draft['download_url'] == f'/api/reports/{report_version_id}/download'

    download = client.get(draft['download_url'])
    assert download.status_code == 200
    assert download.content == report_bytes

    with session_scope() as db:
        actions = set(db.scalars(select(AuditLog.action).where(AuditLog.project_id == project_id)).all())
        assert {'ADMIN_PROJECT_VIEWED', 'ADMIN_FINANCIAL_VIEWED', 'PROJECT_EXCEL_EXPORTED', 'ADMIN_REPORT_VERSION_DOWNLOADED'} <= actions


def test_admin_user_activity_and_password_recovery_controls(client):
    from landvalue360_portal.models import AccessSession, AuditLog, NotificationOutbox, OneTimeToken

    target_csrf = register(client, 'recovery-user@example.com', 'Recovery User')
    with session_scope() as db:
        target = db.scalar(select(User).where(User.email == 'recovery-user@example.com'))
        target_id = target.id
        create_staff_user(
            db, email='recovery-admin@example.com', password='StrongPass123!',
            full_name='Recovery Admin', role_code='PLATFORM_ADMIN',
        )
    client.post('/api/auth/logout', headers={'X-CSRF-Token': target_csrf})
    target_login_csrf = login(client, 'recovery-user@example.com')
    client.post('/api/auth/logout', headers={'X-CSRF-Token': target_login_csrf})
    admin_csrf = login(client, 'recovery-admin@example.com')

    users = client.get('/api/admin/users')
    assert users.status_code == 200
    target_row = next(item for item in users.json() if item['id'] == target_id)
    assert target_row['membership_count'] == 1
    assert target_row['project_count'] == 0

    activity = client.get(f'/api/admin/users/{target_id}/activity')
    assert activity.status_code == 200, activity.text
    assert activity.json()['user']['email'] == 'recovery-user@example.com'
    assert activity.json()['memberships']
    assert activity.json()['login_attempts']

    reset = client.post(
        f'/api/admin/users/{target_id}/send-password-reset',
        headers={'X-CSRF-Token': admin_csrf},
    )
    assert reset.status_code == 200, reset.text
    with session_scope() as db:
        assert db.scalar(select(OneTimeToken).where(OneTimeToken.user_id == target_id, OneTimeToken.kind == 'RESET_PASSWORD'))
        assert db.scalar(select(NotificationOutbox).where(NotificationOutbox.recipient == 'recovery-user@example.com'))

    temporary = client.post(
        f'/api/admin/users/{target_id}/temporary-password', json={},
        headers={'X-CSRF-Token': admin_csrf},
    )
    assert temporary.status_code == 200, temporary.text
    temporary_password = temporary.json()['temporary_password']
    assert len(temporary_password) >= 12
    assert temporary.json()['must_change_password'] is True
    assert 'password_hash' not in temporary.json()

    client.post('/api/auth/logout', headers={'X-CSRF-Token': admin_csrf})
    login_response = client.post('/api/auth/login', json={
        'email': 'recovery-user@example.com', 'password': temporary_password,
    })
    assert login_response.status_code == 200, login_response.text
    assert login_response.json()['redirect'] == '/change-password'
    assert login_response.json()['must_change_password'] is True
    temporary_csrf = login_response.json()['csrf_token']
    assert client.get('/portal').status_code == 428
    assert client.get('/change-password').status_code == 200

    changed = client.post('/api/auth/change-password', json={
        'current_password': temporary_password,
        'new_password': 'ReplacementPass789!',
        'confirm_password': 'ReplacementPass789!',
    }, headers={'X-CSRF-Token': temporary_csrf})
    assert changed.status_code == 200, changed.text
    assert changed.json()['redirect'] == '/portal'
    assert client.get('/portal').status_code == 200

    with session_scope() as db:
        target = db.get(User, target_id)
        assert target.must_change_password is False
        assert target.password_changed_at is not None
        actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == target_id)).all())
        assert {'ADMIN_PASSWORD_RESET_LINK_SENT', 'ADMIN_TEMPORARY_PASSWORD_ISSUED', 'PASSWORD_CHANGED'} <= actions
        active = list(db.scalars(select(AccessSession).where(AccessSession.user_id == target_id, AccessSession.revoked_at.is_(None))).all())
        assert len(active) == 1
