import json
import threading
import time
import uuid

import pytest

from browser.contracts import BrowserTaskRequest
from browser.ipc import BrowserIpcClient, BrowserPipeServer, MAX_FRAME_BYTES
from browser.service import BrowserTaskService


def test_owner_pipe_round_trip_uses_bounded_json_and_enforces_task_session():
    pytest.importorskip('win32security')
    address = rf'\\.\pipe\AscendVisionBrowser-test-{uuid.uuid4().hex}'
    authkey = b'test-only-random-authkey-1234567890'
    decisions = iter([{
        'schema_version': 1,
        'observation_id': 'obs-1',
        'action': 'finish',
        'arguments': {'finding': 'Verified.', 'source_observation_ids': ['obs-1']},
    }])

    class Executor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def observe(self, task_id):
            from browser.executor import BrowserObservation
            return BrowserObservation(task_id, 'page-1', 1, 'obs-1',
                                     '2026-09-28T00:00:00+00:00', 'https://example.org',
                                     'Example', 'Source text.', (), False)

    service = BrowserTaskService(lambda: Executor(), lambda *_: next(decisions))
    service.start()
    server = BrowserPipeServer(address, authkey, service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert server.ready.wait(timeout=1)
    client = BrowserIpcClient(address, authkey)
    session = ('owner', 'dashboard', 'session-1')
    try:
        impostor = BrowserIpcClient(address, b'x' * 32)
        with pytest.raises(RuntimeError, match='rejected'):
            impostor.ping()
        assert client.ping() is True
        request = BrowserTaskRequest.from_payload({
            'schema_version': 1, 'task_id': 'ipc-task', 'session_key': list(session),
            'goal': 'Read a public source', 'provider': 'gemini',
            'scope_mode': 'public_research', 'expires_at': time.time() + 30,
        })
        receipt = client.submit(request)
        assert receipt.task_id == 'ipc-task'

        with pytest.raises(PermissionError):
            client.events(receipt.task_id, 0, ('intruder', 'dashboard', 'session-1'))

        deadline = time.monotonic() + 2
        page = None
        while time.monotonic() < deadline:
            page = client.events(receipt.task_id, 0, session)
            if page.state == 'completed':
                break
            time.sleep(.01)
        assert page.state == 'completed'
        assert page.result['findings'][0]['text'] == 'Verified.'
    finally:
        try:
            client.shutdown()
        except Exception:
            server.stop()
        thread.join(timeout=2)
        service.close()


def test_browser_ipc_rejects_oversized_request_before_connecting():
    authkey = b'test-only-random-authkey-1234567890'
    client = BrowserIpcClient(r'\\.\pipe\unused', authkey)
    with pytest.raises(ValueError, match='64 KiB'):
        client._request({'operation': 'invalid', 'padding': 'x' * (MAX_FRAME_BYTES + 1)})


def test_browser_ipc_secret_must_have_sufficient_entropy():
    with pytest.raises(ValueError, match='32 bytes'):
        BrowserIpcClient(r'\\.\pipe\unused', b'too-short')


def test_browser_broker_starts_in_a_separate_process_and_cleans_up_its_credential():
    import os
    from config import BrowserAutomationConfig, LLMConfig
    from browser.ipc import BROKER_KEYRING_SERVICE, _keyring
    from browser.main import BrowserBrokerProcess

    identity = f'test-{uuid.uuid4().hex}'
    address = rf'\\.\pipe\AscendVisionBrowser-test-{uuid.uuid4().hex}'
    broker = BrowserBrokerProcess(
        BrowserAutomationConfig(), LLMConfig(), address=address, identity=identity,
    )
    try:
        client = broker.start()
        assert client.ping() is True
        assert broker._process.pid != os.getpid()
    finally:
        broker.stop()

    assert _keyring().get_password(BROKER_KEYRING_SERVICE, identity) is None


def test_browser_broker_startup_is_opt_in_and_registers_process_cleanup(monkeypatch):
    from config import BrowserAutomationConfig, LLMConfig
    import browser.main as browser_main

    calls = []
    cleanup_callbacks = []
    client = object()

    class FakeBroker:
        def __init__(self, browser_config, llm_config):
            calls.append((browser_config, llm_config))
            self.stop = lambda: cleanup_callbacks.append('stopped')

        def start(self):
            return client

    monkeypatch.setattr(browser_main, 'BrowserBrokerProcess', FakeBroker)
    resources = type('Resources', (), {'callback': lambda _self, callback: cleanup_callbacks.append(callback)})()

    assert browser_main.start_browser_broker(BrowserAutomationConfig(), LLMConfig(), resources) is None
    assert calls == []

    config = BrowserAutomationConfig(enabled=True)
    assert browser_main.start_browser_broker(config, LLMConfig(), resources) is client
    assert calls == [(config, LLMConfig())]
    assert len(cleanup_callbacks) == 1
    cleanup_callbacks[0]()
    assert cleanup_callbacks[-1] == 'stopped'
