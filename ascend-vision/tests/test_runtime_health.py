from assistant.runtime_health import RuntimeHealth, microphone_availability
from focus_ui import FocusUI


def test_health_requires_bound_chat_and_does_not_leak_launch_token(monkeypatch):
    monkeypatch.setenv('ASCEND_INSTANCE_ID', 'fixture-boot')
    monkeypatch.setenv('ASCEND_BUILD_ID', 'fixture-build')
    monkeypatch.setenv('ASCEND_LAUNCH_TOKEN', 'fixture-secret')
    ui = FocusUI()
    client = ui.app.test_client()
    assert client.get('/api/fairy/health').status_code == 403
    assert client.get('/api/fairy/health', headers={'Authorization': 'Bearer wrong'}).status_code == 403
    assert client.post('/api/fairy/shutdown').status_code == 403
    assert client.post('/api/fairy/shutdown', headers={'Authorization': 'Bearer wrong'}).status_code == 403
    assert not ui.shutdown_requested.is_set()
    ui.health.update(ui='ready')
    response = client.get('/api/fairy/health', headers={'Authorization': 'Bearer fixture-secret'})
    assert response.json['state'] == 'starting'
    assert response.json['instanceId'] == 'fixture-boot'
    assert response.json['buildId'] == 'fixture-build'
    assert b'fixture-secret' not in response.data
    assert b'fixture-secret' not in client.get('/api/fairy/state').data
    ui.bind_chat(object(), 'chat-session')
    ui.mark_chat_ready()
    ui.health.update(camera='unavailable', microphone='unavailable')
    assert ui.health.snapshot()['state'] == 'degraded'
    assert client.post('/api/fairy/shutdown', headers={'Authorization': 'Bearer fixture-secret'}).status_code == 202
    assert ui.shutdown_requested.is_set()


def test_health_boot_ids_are_distinct_and_failure_is_allowlisted(monkeypatch):
    monkeypatch.delenv('ASCEND_INSTANCE_ID', raising=False)
    assert RuntimeHealth().instance_id != RuntimeHealth().instance_id
    health = RuntimeHealth()
    health.fail('chat_initialization_failed')
    assert health.snapshot()['state'] == 'failed'


def test_microphone_health_uses_stream_thread_result_not_config_only():
    class Thread:
        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

    class Listener:
        enabled = True
        _thread = Thread(True)

    assert microphone_availability(None, configured=False) == 'disabled'
    assert microphone_availability(None, configured=True) == 'starting'
    listener = Listener()
    assert microphone_availability(listener, configured=True) == 'ready'
    listener._thread.alive = False
    assert microphone_availability(listener, configured=True) == 'unavailable'
    listener.enabled = False
    assert microphone_availability(listener, configured=True) == 'unavailable'

