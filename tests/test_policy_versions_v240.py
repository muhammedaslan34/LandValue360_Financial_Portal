from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import CalculationRun, FinancialPolicyVersion, Organization
from landvalue360_portal.services import create_staff_user


def _register(client):
    response = client.post('/api/auth/register', json={
        'email': 'policy-user-v240@example.com', 'password': 'StrongPass123!',
        'full_name': 'Policy User', 'organization_name': 'Policy User Org',
        'country': 'SY', 'phone': '', 'accepted_terms': True,
    })
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def _login(client, email):
    response = client.post('/api/auth/login', json={'email': email, 'password': 'StrongPass123!'})
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def _project(client, csrf):
    with session_scope() as db:
        org = db.scalar(select(Organization).where(Organization.slug == 'policy-user-org'))
    response = client.post('/api/projects', json={
        'organization_id': org.id, 'name': 'Policy Choice Project', 'currency': 'USD',
    }, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 201, response.text
    project_id = response.json()['id']
    payload = {
        'name': 'Policy Choice Project', 'currency': 'USD', 'gross_land_area_sqm': '10000',
        'excluded_land_area_sqm': '0', 'current_land_value': '2500000', 'far': '2', 'bcr': '0.4',
        'project_duration_months': 36, 'sales_duration_months': 30,
        'land_uses': [{'code': 'INVESTMENT', 'name': 'Investment', 'percentage': '100'}],
        'products': [{'code': 'RES', 'name': 'Residential', 'allocation_percentage': '100',
                      'sellable_efficiency_percentage': '80', 'unit_selling_price': '1000', 'currency': 'USD'}],
        'costs': [{'name': 'Construction', 'category': 'CONSTRUCTION', 'amount': '8000000',
                   'currency': 'USD', 'developer_share_percentage': '100', 'net_sales_deductible': False}],
    }
    response = client.put(f'/api/projects/{project_id}', json=payload, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200, response.text
    state = client.get(f'/api/projects/{project_id}/financial').json()
    model = state['financial_model']
    model['funding']['opening_cash'] = '12000000'
    model['funding']['total_developer_equity'] = '12000000'
    response = client.put(f'/api/projects/{project_id}/financial', json=model, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200, response.text
    return project_id


def test_admin_publishes_multiple_immutable_policies_and_user_selects(client):
    owner_csrf = _register(client)
    project_id = _project(client, owner_csrf)
    with session_scope() as db:
        create_staff_user(db, email='policy-admin-v240@example.com', password='StrongPass123!',
                          full_name='Policy Admin', role_code='PLATFORM_ADMIN')

    client.post('/api/auth/logout', headers={'X-CSRF-Token': owner_csrf})
    admin_csrf = _login(client, 'policy-admin-v240@example.com')
    current_response = client.get('/api/admin/financial-policy')
    assert current_response.status_code == 200, current_response.text
    current = current_response.json()['current']

    selectable = dict(current['controls'])
    selectable['display_name_ar'] = 'سياسة محافظة للمستخدم'
    selectable['display_name_en'] = 'User Conservative Policy'
    selectable['description_ar'] = 'نسخة قابلة للاختيار مع موضع متوازن أكثر تحفظاً.'
    selectable['description_en'] = 'Selectable policy with a more conservative balanced position.'
    selectable['user_selectable'] = True
    selectable['balanced_position_factor'] = '0.40'
    response = client.post('/api/admin/financial-policy/versions', json={
        'controls': selectable, 'change_reason': 'Publish selectable conservative policy',
        'source_version_id': current['id'], 'activate': False,
    }, headers={'X-CSRF-Token': admin_csrf})
    assert response.status_code == 201, response.text
    v2 = response.json()

    internal = dict(selectable)
    internal['display_name_ar'] = 'سياسة داخلية'
    internal['display_name_en'] = 'Internal Policy'
    internal['user_selectable'] = False
    response = client.post('/api/admin/financial-policy/versions', json={
        'controls': internal, 'change_reason': 'Internal analyst-only policy',
        'source_version_id': v2['id'], 'activate': False,
    }, headers={'X-CSRF-Token': admin_csrf})
    assert response.status_code == 201, response.text
    v3 = response.json()

    response = client.post(f"/api/admin/financial-policy/versions/{v2['id']}/activate", json={}, headers={'X-CSRF-Token': admin_csrf})
    assert response.status_code == 200, response.text

    client.post('/api/auth/logout', headers={'X-CSRF-Token': admin_csrf})
    user_csrf = _login(client, 'policy-user-v240@example.com')
    state_response = client.get(f'/api/projects/{project_id}/financial')
    assert state_response.status_code == 200, state_response.text
    state = state_response.json()
    ids = {row['id'] for row in state['policy_versions']}
    assert current['id'] in ids and v2['id'] in ids and v3['id'] not in ids
    assert state['policy']['id'] == v2['id']
    assert state['policy']['is_default'] is True

    run_v1 = client.post(f'/api/projects/{project_id}/financial/runs', json={
        'policy_version_id': current['id'],
    }, headers={'X-CSRF-Token': user_csrf})
    assert run_v1.status_code == 201, run_v1.text
    run_v2 = client.post(f'/api/projects/{project_id}/financial/runs', json={
        'policy_version_id': v2['id'],
    }, headers={'X-CSRF-Token': user_csrf})
    assert run_v2.status_code == 201, run_v2.text
    assert run_v1.json()['financial_policy_version_id'] == current['id']
    assert run_v2.json()['financial_policy_version_id'] == v2['id']
    assert run_v1.json()['input_hash'] != run_v2.json()['input_hash']
    run_v2_payload = run_v2.json()
    run_v2_id = run_v2_payload['id']
    run_v2_input_hash = run_v2_payload['input_hash']
    run_v2_result_hash = run_v2_payload['result_hash']

    blocked = client.post(f'/api/projects/{project_id}/financial/runs', json={
        'policy_version_id': v3['id'],
    }, headers={'X-CSRF-Token': user_csrf})
    assert blocked.status_code == 403, blocked.text

    with session_scope() as db:
        versions = list(db.scalars(select(FinancialPolicyVersion).order_by(FinancialPolicyVersion.version_number)).all())
        assert len(versions) == 3
        assert all(row.immutable for row in versions)
        runs = list(db.scalars(select(CalculationRun).where(CalculationRun.project_id == project_id)).all())
        assert {row.financial_policy_version_id for row in runs} == {current['id'], v2['id']}


    # Archiving is lifecycle management only: the active default cannot be
    # archived, historical runs remain reproducible, and archived policies are
    # unavailable for new standard-user runs until republished.
    client.post('/api/auth/logout', headers={'X-CSRF-Token': user_csrf})
    admin_csrf = _login(client, 'policy-admin-v240@example.com')
    blocked_archive = client.patch(
        f"/api/admin/financial-policy/versions/{v2['id']}/status",
        json={'status': 'ARCHIVED'}, headers={'X-CSRF-Token': admin_csrf},
    )
    assert blocked_archive.status_code == 422, blocked_archive.text

    activate_original = client.post(
        f"/api/admin/financial-policy/versions/{current['id']}/activate",
        json={}, headers={'X-CSRF-Token': admin_csrf},
    )
    assert activate_original.status_code == 200, activate_original.text
    archived = client.patch(
        f"/api/admin/financial-policy/versions/{v2['id']}/status",
        json={'status': 'ARCHIVED'}, headers={'X-CSRF-Token': admin_csrf},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()['status'] == 'ARCHIVED'

    client.post('/api/auth/logout', headers={'X-CSRF-Token': admin_csrf})
    user_csrf = _login(client, 'policy-user-v240@example.com')
    archived_state = client.get(f'/api/projects/{project_id}/financial')
    assert archived_state.status_code == 200, archived_state.text
    archived_ids = {row['id'] for row in archived_state.json()['policy_versions']}
    assert v2['id'] not in archived_ids
    assert archived_state.json()['policy']['id'] == current['id']

    archived_new_run = client.post(f'/api/projects/{project_id}/financial/runs', json={
        'policy_version_id': v2['id'],
    }, headers={'X-CSRF-Token': user_csrf})
    assert archived_new_run.status_code == 404, archived_new_run.text

    historical = client.get(f'/api/projects/{project_id}/financial/runs/{run_v2_id}')
    assert historical.status_code == 200, historical.text
    historical_payload = historical.json()
    assert historical_payload['financial_policy_version_id'] == v2['id']
    assert historical_payload['financial_policy_status'] == 'ARCHIVED'
    assert historical_payload['financial_policy_display_name_en'] == 'User Conservative Policy'
    assert historical_payload['input_hash'] == run_v2_input_hash
    assert historical_payload['result_hash'] == run_v2_result_hash

    client.post('/api/auth/logout', headers={'X-CSRF-Token': user_csrf})
    admin_csrf = _login(client, 'policy-admin-v240@example.com')
    republished = client.patch(
        f"/api/admin/financial-policy/versions/{v2['id']}/status",
        json={'status': 'PUBLISHED'}, headers={'X-CSRF-Token': admin_csrf},
    )
    assert republished.status_code == 200, republished.text
    assert republished.json()['status'] == 'PUBLISHED'

    client.post('/api/auth/logout', headers={'X-CSRF-Token': admin_csrf})
    _login(client, 'policy-user-v240@example.com')
    republished_state = client.get(f'/api/projects/{project_id}/financial')
    assert v2['id'] in {row['id'] for row in republished_state.json()['policy_versions']}


def test_legacy_policy_is_materialized_into_explicit_v240_version(client):
    from copy import deepcopy

    from landvalue360_portal.financial_engine import policy_controls, sha256_json
    from landvalue360_portal.financial_service import current_policy_version, seed_financial_defaults
    from landvalue360_portal.models import FinancialPolicy

    with session_scope() as db:
        legacy = current_policy_version(db)
        snapshot = deepcopy(legacy.policy_snapshot)
        controls = snapshot['portal_policy']
        controls['schema_version'] = 'financial-policy-controls-2.1.0'
        for key in (
            'display_name_ar', 'display_name_en', 'description_ar', 'description_en',
            'user_selectable', 'negotiation_recommendation_method',
            'institutional_conservatism', 'risk_adjusted_capacity_factor',
            'minimum_capacity_factor', 'developer_safety_buffer',
            'balanced_position_factor', 'balanced_position_minimum',
            'balanced_position_maximum', 'developer_competitive_position_factor',
            'rounding_increment_percent',
        ):
            controls.pop(key, None)
        legacy.policy_snapshot = snapshot
        legacy.snapshot_hash = sha256_json(snapshot)
        db.flush()

        upgraded, _ = seed_financial_defaults(db)
        db.flush()
        assert upgraded.id != legacy.id
        assert upgraded.version_number == legacy.version_number + 1
        assert upgraded.status == 'PUBLISHED'
        assert legacy.status == 'ARCHIVED'
        policy = db.get(FinancialPolicy, upgraded.financial_policy_id)
        assert policy.current_version_id == upgraded.id
        explicit = upgraded.policy_snapshot['portal_policy']
        assert explicit['schema_version'] == 'financial-policy-controls-2.4.0'
        assert explicit['risk_adjusted_capacity_factor'] == '0.42'
        assert explicit['balanced_position_factor'] == '0.56'
        assert explicit['advanced_defaults']['finance_enabled'] is False
        assert explicit['advanced_defaults']['collection_rules']
        assert policy_controls(upgraded.policy_snapshot) == explicit
