"""Real loopback and SQLite transport; no camera, audio device or provider calls."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from config import Config
from desktop_host import DesktopHost
from focus_ui import FocusUI


class FixtureAssistant:
    def respond(self, text, context, **kwargs):
        return SimpleNamespace(text='Fixture reply', source='offline')


def make_host(tmp_path, assistant=None):
    (tmp_path / 'index.html').write_text('<title>Recovery fixture</title>')
    config = Config()
    config = replace(config, storage=replace(config.storage, database=tmp_path / 'session.db'))
    return DesktopHost(config, ui=FocusUI(build_dir=tmp_path, launch_token='fixture-token'),
                       assistant_service=assistant or FixtureAssistant())


def test_typed_chat_completes_without_camera_audio_or_core(tmp_path):
    host = make_host(tmp_path)
    try:
        url = host.start()
        assert host._ready.wait(10)
        request = Request(url.split('/?')[0] + '/api/fairy/health',
                          headers={'Authorization': 'Bearer fixture-token'})
        with urlopen(request, timeout=2) as response:
            health = json.load(response)
        assert health['state'] == 'degraded'
        assert health['capabilities']['chat'] == 'ready'
        assert health['capabilities']['camera'] == 'unavailable'
        client = host.ui.app.test_client()
        assert client.post('/api/fairy/chat', json={'text': 'Hello without sensors'}).status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = client.get('/api/fairy/chat/events').json['events']
            if any(event['kind'] == 'assistant' for event in events):
                break
            time.sleep(.02)
        assert any(event['text'] == 'Fixture reply' for event in events)
        state = client.get('/api/fairy/state').json
        assert state['runtimeMode'] == 'recovery-chat'
        assert state['cameraReady'] is False
        assert state['faceTarget'] is None
        assert client.post('/api/fairy/command', json={'command': 'toggle-voice'}).status_code == 409
    finally:
        host.close()
    assert not host.ui._thread.is_alive()
    assert host._queue.active_session_id() is None


def test_hung_model_does_not_block_health_or_bounded_stop(tmp_path):
    entered, release = threading.Event(), threading.Event()
    class HungAssistant:
        def respond(self, *args, **kwargs):
            entered.set()
            release.wait(20)
            return SimpleNamespace(text='Late reply', source='offline')
    host = make_host(tmp_path, HungAssistant())
    try:
        host.start()
        assert host._ready.wait(10)
        client = host.ui.app.test_client()
        assert client.post('/api/fairy/chat', json={'text': 'Hang fixture'}).status_code == 202
        assert entered.wait(3)
        started = time.monotonic()
        assert client.get('/api/fairy/health', headers={'Authorization': 'Bearer fixture-token'}).json['capabilities']['chat'] == 'ready'
        assert time.monotonic() - started < 1
        started = time.monotonic()
        host.close()
        assert time.monotonic() - started < 4
        assert host._queue.active_session_id() is None
    finally:
        release.set()
        host.close()


def test_initialization_failure_keeps_fairy_available(tmp_path, monkeypatch):
    import integrations.chat_ipc
    def unavailable(*args):
        raise RuntimeError('fixture-secret must not appear in health')
    monkeypatch.setattr(integrations.chat_ipc, 'ChatIpcQueue', unavailable)
    host = make_host(tmp_path)
    try:
        host.start()
        host._initializer.join(timeout=10)
        client = host.ui.app.test_client()
        assert client.get('/').status_code == 200
        health = host.ui.health.snapshot()
        assert health['failureCode'] == 'chat_initialization_failed'
        assert 'fixture-secret' not in json.dumps(health)
        assert client.post('/api/fairy/chat', json={'text': 'Hello'}).status_code == 503
    finally:
        host.close()


def test_recovery_chat_import_path_never_loads_native_hardware():
    # A fresh interpreter proves this independently of pytest's existing imports.
    code = '''
import importlib.abc, sys
blocked = {'cv2', 'torch', 'ultralytics', 'mediapipe', 'pyaudio', 'sounddevice', 'pyttsx3'}
class NoHardware(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('Hardware import attempted: ' + fullname)
sys.meta_path.insert(0, NoHardware())
import desktop_host
from assistant.service import AssistantService
from config import Config
from assistant.local_conversation import LocalConversation
from integrations.chat_runtime import ChatRuntimeBridge
cfg = Config()
service = AssistantService(cfg.feedback, cfg.llm)
service.ai_status()
assert not blocked.intersection(sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', code],
                            cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
