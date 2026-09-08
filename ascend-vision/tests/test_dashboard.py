from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from config import Config, StorageConfig, DashboardConfig
from dashboard import create_app, main
from integrations.ascend_client import AscendConnectionState
from integrations.vision_context import VisionAuthContext
from integrations.vision_token_store import VisionToken


def future_iso():
    return (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()


def local_handoff_payload(**overrides):
    payload = {
        'accessToken': 'short-lived-vision-token',
        'expiresAt': future_iso(),
        'character': {'id': 'guest-character', 'name': 'Guest_d8d7'},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(tmp_path):
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    app = create_app(cfg)
    app.config['TESTING'] = True
    return app.test_client()


def test_dashboard_starts_without_database_and_serves_only_local_assets(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'/static/dashboard.js' in response.data
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    assert client.get('/api/stats').json['database_exists'] is False
    assert client.get('/static/dashboard.js').status_code == 200
    assert client.get('/static/dashboard.css').status_code == 200
    assert client.get('/.env').status_code == 404
    assert client.post('/api/stats').status_code == 405


def test_local_status_response_never_contains_token(monkeypatch, client):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    class TokenStore:
        def load(self):
            return VisionToken('vision-jwt-secret', expires_at)

    class ContextStore:
        def load(self):
            return VisionAuthContext('guest-character', 'Guest_d8d7', expires_at)

    class CoreClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_automation_capabilities(self):
            return type('Result', (), {'state': AscendConnectionState.CONNECTED})()

    monkeypatch.setattr('dashboard.VisionTokenStore', TokenStore)
    monkeypatch.setattr('dashboard.VisionContextStore', ContextStore)
    monkeypatch.setattr('dashboard.AscendClient', CoreClient)

    response = client.get('/api/auth/local-vision-status')

    assert response.status_code == 200
    assert response.json == {
        'status': 'connected',
        'character': {'id': 'guest-character', 'name': 'Guest_d8d7'},
        'expiresAt': expires_at.isoformat(),
        'coreApi': 'reachable',
    }
    assert b'accessToken' not in response.data
    assert b'vision-jwt-secret' not in response.data


def test_local_dashboard_checks_stored_status_before_cookie_handoff(client):
    script = client.get('/static/dashboard.js').get_data(as_text=True)

    assert "fetch('/api/auth/local-vision-status'" in script
    assert script.index("fetch('/api/auth/local-vision-status'") < script.index("coreSessionRequest('/api/auth/me')")
    assert 'forceHandoff' in script
    assert "connectLocalCore({forceHandoff:true})" in script


def test_local_dashboard_renders_safe_expiry_and_core_api_status(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    page = client.get('/')
    script = client.get('/static/dashboard.js').get_data(as_text=True)

    assert b'id="local-vision-expiry"' in page.data
    assert b'id="local-core-api-state"' in page.data
    assert 'Expires in:' in script
    assert 'Core API:' in script
    assert 'accessToken' not in page.get_data(as_text=True)


def test_local_dashboard_hides_password_form_and_includes_auto_connect_controls(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    page = client.get('/')

    assert page.status_code == 200
    assert b'Retry Connection' in page.data
    assert b'core-password' not in page.data
    assert b'data-local-auto-connect="true"' in page.data
    assert 'connect-src \'self\' http://localhost:8000' in page.headers['Content-Security-Policy']


def test_nonlocal_dashboard_keeps_existing_password_handoff_form(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'https://core.example')

    page = client.get('/')

    assert page.status_code == 200
    assert b'core-password' in page.data
    assert b'data-local-auto-connect="false"' in page.data


def test_dashboard_script_uses_cookie_handoff_without_browser_token_storage(client):
    script = client.get('/static/dashboard.js').get_data(as_text=True)

    assert '/api/auth/me' in script
    assert '/api/auth/vision-token' in script
    assert "credentials:'include'" in script
    assert '/api/auth/local-vision-handoff' in script
    assert 'localStorage' not in script


def test_local_dashboard_exchanges_sign_in_only_for_secure_vision_handoff(monkeypatch, client):
    calls = []
    monkeypatch.setenv('ASCEND_BASE_URL', 'https://core.example')

    class CoreClient:
        def __init__(self, base_url, *args, **kwargs):
            calls.append((base_url, args, kwargs))
        def login_and_obtain_vision_token(self, identifier, password):
            assert identifier == 'hunter' and password == 'not-logged'
            return type('Result', (), {'state': AscendConnectionState.CONNECTED})()

    monkeypatch.setattr('dashboard.AscendClient', CoreClient)
    response = client.post('/api/auth/vision-handoff', json={'identifier': 'hunter', 'password': 'not-logged'})

    assert response.status_code == 200
    assert response.json == {'status': 'connected'}
    assert calls and 'not-logged' not in response.get_data(as_text=True)


def test_local_handoff_stores_only_vision_token_and_core_character(monkeypatch, client):
    saved = {}
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    class TokenStore:
        def save(self, token):
            saved['token'] = token

    class ContextStore:
        def save(self, context):
            saved['context'] = context

    monkeypatch.setattr('dashboard.VisionTokenStore', TokenStore, raising=False)
    monkeypatch.setattr('dashboard.VisionContextStore', ContextStore, raising=False)

    response = client.post('/api/auth/local-vision-handoff', json={
        'accessToken': 'short-lived-vision-token',
        'expiresAt': future_iso(),
        'character': {'id': 'guest-character', 'name': 'Guest_d8d7'},
    })

    assert response.status_code == 200
    assert response.json['status'] == 'connected'
    assert response.json['character'] == {'id': 'guest-character', 'name': 'Guest_d8d7'}
    assert 'accessToken' not in response.json
    assert saved['token'].access_token == 'short-lived-vision-token'
    assert saved['context'].character_id == 'guest-character'
    assert saved['context'].character_name == 'Guest_d8d7'
    assert saved['context'].expires_at == saved['token'].expires_at


def test_local_handoff_rejects_invalid_non_loopback_and_nonlocal_requests(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    assert client.post('/api/auth/local-vision-handoff', json={}).status_code == 400
    assert client.post('/api/auth/local-vision-handoff', headers={'Host': 'example.test'}, json={
        'accessToken': 'short-lived-vision-token',
        'expiresAt': future_iso(),
        'character': {'id': 'guest-character', 'name': 'Guest_d8d7'},
    }).status_code in {400, 403}

    monkeypatch.setenv('ASCEND_BASE_URL', 'https://core.example')
    assert client.post('/api/auth/local-vision-handoff', json={
        'accessToken': 'short-lived-vision-token',
        'expiresAt': future_iso(),
        'character': {'id': 'guest-character', 'name': 'Guest_d8d7'},
    }).status_code == 403


@pytest.mark.parametrize('token', ['', '   ', None, 42])
def test_local_handoff_rejects_blank_or_non_string_tokens(monkeypatch, client, token):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload(accessToken=token))

    assert response.status_code == 400


@pytest.mark.parametrize('expiry', [
    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    (datetime.now() + timedelta(minutes=15)).replace(microsecond=0).isoformat(),
])
def test_local_handoff_rejects_expired_or_naive_expiry(monkeypatch, client, expiry):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload(expiresAt=expiry))

    assert response.status_code == 400


def test_local_handoff_rejects_extra_character_metadata(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')

    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload(character={
        'id': 'guest-character', 'name': 'Guest_d8d7', 'email': 'not-safe-to-store@example.test',
    }))

    assert response.status_code == 400


def test_local_handoff_store_failure_returns_safe_error_without_token(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')
    cleared = []

    class TokenStore:
        def save(self, token):
            raise OSError('credential backend unavailable')

        def clear(self):
            cleared.append('token')

    class ContextStore:
        def save(self, context):
            raise AssertionError('context store must not be written after token failure')

        def clear(self):
            cleared.append('context')

    monkeypatch.setattr('dashboard.VisionTokenStore', TokenStore)
    monkeypatch.setattr('dashboard.VisionContextStore', ContextStore)
    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload())

    assert response.status_code == 503
    assert response.json == {'error': 'Vision authorization could not be saved.'}
    assert 'short-lived-vision-token' not in response.get_data(as_text=True)
    assert cleared == ['token', 'context']


def test_local_handoff_rolls_back_token_when_context_store_fails(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')
    stored = {}

    class TokenStore:
        def save(self, token):
            stored['token'] = token

        def clear(self):
            stored.pop('token', None)
            stored['token_cleared'] = True

    class ContextStore:
        def save(self, context):
            raise OSError('context backend unavailable')

        def clear(self):
            stored['context_cleared'] = True

    monkeypatch.setattr('dashboard.VisionTokenStore', TokenStore)
    monkeypatch.setattr('dashboard.VisionContextStore', ContextStore)
    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload())

    assert response.status_code == 503
    assert 'accessToken' not in response.json
    assert 'short-lived-vision-token' not in response.get_data(as_text=True)
    assert 'token' not in stored
    assert stored['token_cleared'] is True
    assert stored['context_cleared'] is True


def test_local_handoff_returns_safe_error_when_cleanup_fails(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')
    cleanup_attempts = []

    class TokenStore:
        def save(self, token):
            raise OSError('credential backend unavailable')

        def clear(self):
            cleanup_attempts.append('token')
            raise OSError('token cleanup unavailable')

    class ContextStore:
        def save(self, context):
            raise AssertionError('context store must not be written after token failure')

        def clear(self):
            cleanup_attempts.append('context')
            raise OSError('context cleanup unavailable')

    monkeypatch.setattr('dashboard.VisionTokenStore', TokenStore)
    monkeypatch.setattr('dashboard.VisionContextStore', ContextStore)
    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload())

    assert response.status_code == 503
    assert response.json == {'error': 'Vision authorization could not be saved.'}
    assert 'short-lived-vision-token' not in response.get_data(as_text=True)
    assert cleanup_attempts == ['token', 'context']


def test_local_handoff_returns_safe_error_when_partial_write_cleanup_fails(monkeypatch, client):
    monkeypatch.setenv('ASCEND_BASE_URL', 'http://localhost:8000')
    cleanup_attempts = []

    class TokenStore:
        def save(self, token):
            self.saved_token = token

        def clear(self):
            cleanup_attempts.append('token')
            raise OSError('token cleanup unavailable')

    class ContextStore:
        def save(self, context):
            raise OSError('context backend unavailable')

        def clear(self):
            cleanup_attempts.append('context')
            raise OSError('context cleanup unavailable')

    monkeypatch.setattr('dashboard.VisionTokenStore', TokenStore)
    monkeypatch.setattr('dashboard.VisionContextStore', ContextStore)
    response = client.post('/api/auth/local-vision-handoff', json=local_handoff_payload())

    assert response.status_code == 503
    assert response.json == {'error': 'Vision authorization could not be saved.'}
    assert 'accessToken' not in response.json
    assert 'short-lived-vision-token' not in response.get_data(as_text=True)
    assert cleanup_attempts == ['token', 'context']


@pytest.mark.parametrize('query', ['mode=other', 'start=bad', 'start=2026-09-02&end=2026-09-01',
    'start=2020-01-01&end=2026-01-01', 'mode=focus&mode=background', 'file=secret', 'end=9999-12-31'])
def test_invalid_filters(client, query):
    assert client.get('/api/stats?'+query).status_code == 400


def test_rebinding_and_cross_site_reads_rejected(client):
    assert client.get('/api/stats', headers={'Host': 'evil.example'}).status_code == 400
    assert client.get('/api/stats', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/stats', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert 'Access-Control-Allow-Origin' not in client.get('/api/stats').headers


def test_database_failure_is_not_a_zero_total(tmp_path):
    path = tmp_path/'bad.db'
    path.write_bytes(b'not sqlite')
    app = create_app(Config(storage=StorageConfig(database=path)))
    response = app.test_client().get('/api/stats')
    assert response.status_code == 503
    assert 'error' in response.json and 'summary' not in response.json
    assert str(tmp_path) not in response.get_data(as_text=True)


@pytest.mark.parametrize('values', [dict(port=80), dict(port=True), dict(default_days=0),
    dict(refresh_seconds=0), dict(timezone='invalid/zone'), dict(open_browser='yes')])
def test_dashboard_config_validation(values):
    with pytest.raises(ValueError):
        DashboardConfig(**values)


def test_cli_invalid_config_does_not_start_server(tmp_path):
    assert main(['--config', str(tmp_path/'missing.yaml'), '--no-open-browser']) == 1
