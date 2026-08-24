from urllib.parse import parse_qs, urlparse
from sqlalchemy import select

from landvalue360_portal.database import session_scope
from landvalue360_portal.models import AccessSession, NotificationOutbox


def register(client, email="secure@example.com"):
    response = client.post('/api/auth/register', json={
        'email': email, 'password': 'StrongPass123!', 'full_name': 'Secure User',
        'organization_name': 'Secure Org', 'country': 'SY', 'phone': '', 'accepted_terms': True,
    })
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def test_password_reset_revokes_sessions(client):
    csrf = register(client)
    sessions = client.get('/api/auth/sessions')
    assert sessions.status_code == 200 and len(sessions.json()) == 1
    response = client.post('/api/auth/forgot-password', json={'email': 'secure@example.com'})
    assert response.status_code == 200
    with session_scope() as db:
        outbox = db.scalar(select(NotificationOutbox).where(NotificationOutbox.template_code == 'PASSWORD_RESET').order_by(NotificationOutbox.created_at.desc()))
        token = parse_qs(urlparse(outbox.payload['link']).query)['token'][0]
    response = client.post('/api/auth/reset-password', json={'token': token, 'password': 'NewStrongPass456!'})
    assert response.status_code == 200
    assert client.get('/api/auth/sessions').status_code == 401
    response = client.post('/api/auth/login', json={'email': 'secure@example.com', 'password': 'NewStrongPass456!'})
    assert response.status_code == 200


def test_csrf_required_for_mutations(client):
    register(client, 'csrf@example.com')
    response = client.post('/api/account/privacy-requests', json={'request_type': 'EXPORT'})
    assert response.status_code == 403


def test_session_can_be_revoked(client):
    csrf = register(client, 'session@example.com')
    session_id = client.get('/api/auth/sessions').json()[0]['id']
    response = client.delete(f'/api/auth/sessions/{session_id}', headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    assert client.get('/api/auth/me').status_code == 401
