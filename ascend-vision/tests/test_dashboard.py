from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from config import Config, StorageConfig, DashboardConfig
from dashboard import create_app, main
from integrations.ascend_client import AscendConnectionState
from integrations.chat_ipc import ChatIpcQueue
from assistant.memory import MemoryStore
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


def test_chat_message_is_enqueued_and_returns_only_its_safe_id(tmp_path):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    chat_client = create_app(cfg, chat_queue=queue).test_client()

    response = chat_client.post('/api/chat/messages', json={'text': '  Help me focus  '})

    assert response.status_code == 202
    assert set(response.json) == {'messageId'}
    inbound = queue.receive_inbound()
    assert inbound['message_id'] == response.json['messageId']
    assert inbound['source'] == 'dashboard'
    assert inbound['text'] == 'Help me focus'


@pytest.mark.parametrize('payload', [
    None,
    {},
    {'text': ''},
    {'text': '   '},
    {'text': 42},
    {'text': 'hello', 'accessToken': 'must-not-be-accepted'},
    {'text': 'x' * 4001},
])
def test_chat_message_rejects_malformed_empty_or_oversized_input(tmp_path, payload):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    chat_client = create_app(cfg, chat_queue=queue).test_client()

    if payload is None:
        response = chat_client.post('/api/chat/messages', data='not-json', content_type='text/plain')
    else:
        response = chat_client.post('/api/chat/messages', json=payload)

    assert response.status_code == 400
    assert set(response.json) == {'error'}
    assert queue.receive_inbound() is None


def test_chat_replies_are_ordered_mapped_and_advance_a_safe_cursor(tmp_path):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    first_id = queue.enqueue('first')
    second_id = queue.enqueue('second')
    queue.reply(first_id, 'Waiting for Vision.', 'queued')
    first_cursor = queue.reply(first_id, 'Please confirm that action.', 'confirmation_required')
    queue.reply(second_id, 'Ready.', 'reply')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    chat_client = create_app(cfg, chat_queue=queue).test_client()

    response = chat_client.get(f'/api/chat/messages?after={first_cursor}')

    assert response.status_code == 200
    assert response.json['cursor'] > first_cursor
    assert response.json['messages'] == [{
        'messageId': second_id,
        'text': 'Ready.',
        'status': 'reply',
        'createdAt': response.json['messages'][0]['createdAt'],
    }]
    empty = chat_client.get(f"/api/chat/messages?after={response.json['cursor']}")
    assert empty.json == {'messages': [], 'cursor': response.json['cursor']}
    serialized = response.get_data(as_text=True)
    assert 'first' not in serialized
    assert 'confirmation_required' not in serialized


@pytest.mark.parametrize('query', ['after=-1', 'after=wrong', 'after=1&after=2', 'cursor=0'])
def test_chat_replies_reject_invalid_or_unknown_cursors(tmp_path, query):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    response = create_app(cfg, chat_queue=queue).test_client().get('/api/chat/messages?'+query)

    assert response.status_code == 400


def test_dashboard_has_accessible_chat_controls_and_safe_live_rendering(client):
    page = client.get('/').get_data(as_text=True)
    script = client.get('/static/dashboard.js').get_data(as_text=True)

    assert 'aria-labelledby="chat-heading"' in page
    assert 'id="chat-messages"' in page
    assert 'aria-live="polite"' in page
    assert '<label for="chat-input"' in page
    assert 'id="chat-send"' in page
    assert '/api/chat/messages' in script
    assert 'confirmation_required' in script
    assert '.textContent=' in script
    assert 'innerHTML' not in script


def test_dashboard_memory_approval_edit_delete_and_export(tmp_path):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    proposal_id = store.propose('I prefer green tea')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    memory_client = create_app(cfg, memory_store=store).test_client()

    pending = memory_client.get('/api/memory')
    assert pending.status_code == 200
    assert pending.json['pending'][0]['text'] == 'I prefer green tea'
    assert pending.json['active'] == []

    approved = memory_client.post(f'/api/memory/proposals/{proposal_id}/approve')
    assert approved.status_code == 200
    memory_id = approved.json['memory']['id']
    assert memory_client.get('/api/memory?q=green').json['active'][0]['id'] == memory_id

    edited = memory_client.patch(f'/api/memory/{memory_id}', json={'text': 'I prefer black coffee'})
    assert edited.status_code == 200
    assert memory_client.get('/api/memory?q=green').json['active'] == []
    exported = memory_client.get('/api/memory/export')
    assert exported.status_code == 200
    assert [row['text'] for row in exported.json['memories']] == ['I prefer black coffee']

    assert memory_client.delete(f'/api/memory/{memory_id}').status_code == 200
    assert memory_client.get('/api/memory').json['active'] == []


def test_dashboard_memory_export_includes_all_approved_facts(tmp_path):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    for index in range(101):
        store.approve(store.propose(f'Favorite item {index}'))
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    memory_client = create_app(cfg, memory_store=store).test_client()

    exported = memory_client.get('/api/memory/export')

    assert exported.status_code == 200
    assert len(exported.json['memories']) == 101


def test_dashboard_memory_reject_and_disable_do_not_delete_approved_facts(tmp_path):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    active = store.approve(store.propose('I prefer tea'))
    rejected = store.propose('I like mango')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    memory_client = create_app(cfg, memory_store=store).test_client()

    assert memory_client.post(f'/api/memory/proposals/{rejected}/reject').status_code == 200
    changed = memory_client.put('/api/memory/settings', json={'enabled': False})

    assert changed.status_code == 200
    assert memory_client.get('/api/memory').json['enabled'] is False
    assert memory_client.get('/api/memory').json['pending'] == []
    assert memory_client.get('/api/memory').json['active'][0]['id'] == active['id']


@pytest.mark.parametrize('method,path,payload', [
    ('patch', '/api/memory/1', {'text': 'my password is secret'}),
    ('patch', '/api/memory/1', {'text': 'tea', 'extra': 'x'}),
    ('put', '/api/memory/settings', {'enabled': 'false'}),
    ('put', '/api/memory/settings', {'enabled': False, 'extra': 1}),
])
def test_dashboard_memory_rejects_malformed_or_sensitive_mutation(tmp_path, method, path, payload):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    store.approve(store.propose('I prefer tea'))
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    memory_client = create_app(cfg, memory_store=store).test_client()

    response = getattr(memory_client, method)(path, json=payload)

    assert response.status_code == 400
    assert store.active()[0]['text'] == 'I prefer tea'


def test_chat_acknowledgement_removes_rendered_reply_text(tmp_path):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    message_id = queue.enqueue('temporary message')
    queue.receive_inbound()
    cursor = queue.reply(message_id, 'temporary reply', 'reply')
    queue.replies_after()
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    chat_client = create_app(cfg, chat_queue=queue).test_client()

    response = chat_client.post('/api/chat/ack', json={'cursor': cursor})

    assert response.status_code == 200
    assert queue.replies_after() == []


@pytest.mark.parametrize('payload', [None, {}, {'cursor': -1}, {'cursor': True},
                                    {'cursor': '1'}, {'cursor': 1, 'extra': 2}])
def test_chat_acknowledgement_rejects_malformed_cursor(tmp_path, payload):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    chat_client = create_app(cfg, chat_queue=queue).test_client()

    response = chat_client.post('/api/chat/ack', json=payload)

    assert response.status_code == 400


def test_chat_acknowledgement_rejects_undelivered_cursor(tmp_path):
    queue = ChatIpcQueue(tmp_path/'chat.db')
    message_id = queue.enqueue('temporary message')
    queue.receive_inbound()
    cursor = queue.reply(message_id, 'temporary reply', 'reply')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    chat_client = create_app(cfg, chat_queue=queue).test_client()

    response = chat_client.post('/api/chat/ack', json={'cursor': cursor + 1})

    assert response.status_code == 400
    assert queue.replies_after()[0]['cursor'] == cursor


def test_dashboard_memory_mutation_keeps_local_origin_protection(tmp_path):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    proposal = store.propose('I prefer tea')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    memory_client = create_app(cfg, memory_store=store).test_client()

    response = memory_client.post(
        f'/api/memory/proposals/{proposal}/approve',
        headers={'Origin': 'https://evil.example'},
    )

    assert response.status_code == 403
    assert store.active() == []


def test_dashboard_memory_panel_has_labeled_controls(tmp_path):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    cfg = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    page = create_app(cfg, memory_store=store).test_client().get('/').get_data(as_text=True)

    assert 'id="memory-panel"' in page
    assert 'id="memory-enabled"' in page
    assert 'id="memory-pending"' in page
    assert 'id="memory-active"' in page
    assert 'id="memory-search"' in page
    assert 'href="/api/memory/export"' in page


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


def test_cli_loads_dotenv_beside_selected_config_before_starting_server(monkeypatch, tmp_path):
    config_path = tmp_path/'vision-config.yaml'
    loaded_dotenv = []
    startup_order = []

    def fake_load_dotenv(path, *, override):
        loaded_dotenv.append((path, override))
        startup_order.append('dotenv')

    class Server:
        def run(self):
            startup_order.append('run')

        def close(self):
            startup_order.append('close')

    def fake_create_server(app, **kwargs):
        startup_order.append('server')
        return Server()

    config = Config(storage=StorageConfig(database=tmp_path/'watch.db'))
    monkeypatch.setattr('dashboard.load_dotenv', fake_load_dotenv)
    monkeypatch.setattr('dashboard.load_config', lambda path: config)
    monkeypatch.setattr('waitress.create_server', fake_create_server)

    assert main(['--config', str(config_path), '--no-open-browser']) == 0
    assert loaded_dotenv == [(config_path.parent/'.env', False)]
    assert startup_order.index('dotenv') < startup_order.index('server')
