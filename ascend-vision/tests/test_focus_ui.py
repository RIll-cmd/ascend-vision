"""Verify the UI bridge without camera, LLM, or Core side effects."""
import time
from urllib.request import urlopen

import cv2
import numpy as np
import pytest

from focus_ui import FocusUI


def test_preview_subscription_does_not_control_capture():
    ui = FocusUI()
    client = ui.app.test_client()
    image = np.full((24, 32, 3), 120, dtype=np.uint8)
    ui.publish(image, mode='focus', audioLevel=.4)
    assert ui._frame is None
    assert client.get('/api/fairy/state').json['cameraReady'] is True
    assert client.get('/api/fairy/state').json['audioLevel'] == .4
    assert client.get('/api/fairy/frame.jpg').status_code == 204
    ui.publish(image)
    response = client.get('/api/fairy/frame.jpg')
    assert response.mimetype == 'image/jpeg'
    decoded = cv2.imdecode(np.frombuffer(response.data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == image.shape
    ui._preview_until = time.monotonic() - 1
    ui.publish(image, elapsedSeconds=5)
    assert ui._frame is None
    assert client.get('/api/fairy/state').json['elapsedSeconds'] == 5


def test_controls_are_queued_for_main_thread_and_cross_origin_is_blocked():
    ui = FocusUI()
    client = ui.app.test_client()
    assert client.post('/api/fairy/command', json={'command': 'toggle-focus'}).status_code == 202
    assert client.post('/api/fairy/command', json={'command': 'toggle-voice'}).status_code == 202
    assert list(ui.drain_commands()) == ['toggle-focus', 'toggle-voice']
    assert list(ui.drain_commands()) == []
    assert client.post('/api/fairy/command', json={'command': 'arbitrary'}).status_code == 400
    assert client.post('/api/fairy/command', json={'command': 'toggle-focus'}, headers={'Origin': 'https://example.com'}).status_code == 403
    assert client.get('/api/fairy/frame.jpg', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert client.get('/api/fairy/state', headers={'Host': 'attacker.example'}).status_code == 400


def test_fairy_chat_uses_runtime_queue_and_reads_session_events(tmp_path):
    from integrations.chat_ipc import ChatIpcQueue

    queue = ChatIpcQueue(tmp_path / 'chat.db')
    queue.begin_session('vision-run')
    ui = FocusUI()
    ui.bind_chat(queue, 'vision-run')
    client = ui.app.test_client()

    response = client.post('/api/fairy/chat', json={'text': 'typed without a mic'})
    assert response.status_code == 202
    inbound = queue.receive_inbound()
    assert inbound['source'] == 'fairy'
    queue.publish_event('vision-run', turn_id=inbound['message_id'], source='fairy',
                        kind='user', text=inbound['text'], status='received')
    events = client.get('/api/fairy/chat/events?after=0').json
    assert events['sessionId'] == 'vision-run'
    assert events['events'][0]['text'] == 'typed without a mic'

    queue.stop_accepting_session('vision-run')
    late = client.post('/api/fairy/chat', json={'text': 'too late'})
    assert late.status_code == 503
    assert queue.receive_inbound() is None

    ui.publish_core_connection(configured=True, state='offline',
                               last_checked_at='2026-09-30T00:00:00+00:00')
    state = client.get('/api/fairy/state').json
    assert state['coreConnection']['state'] == 'offline'
    queue.end_session('vision-run')


def test_server_lifecycle_and_static_assets(tmp_path):
    (tmp_path / 'index.html').write_text('<title>Fairy test</title>')
    ui = FocusUI(build_dir=tmp_path)
    try:
        url = ui.start(open_browser=False)
        with urlopen(url, timeout=2) as response:
            assert response.read() == b'<title>Fairy test</title>'
            assert response.headers['Cache-Control'] == 'no-store'
    finally:
        ui.close()
    assert not ui._thread.is_alive()
    assert ui._frame is None


def test_missing_build_has_actionable_error(tmp_path):
    with pytest.raises(RuntimeError, match='npm run build'):
        FocusUI(build_dir=tmp_path).start(open_browser=False)


def test_face_target_is_normalized_mirrored_and_cleared_on_face_loss():
    ui = FocusUI()
    ui.publish_face([(80, 60, 0), (240, 180, 0)], 640, 480)
    target = ui.app.test_client().get('/api/fairy/state').json['faceTarget']
    assert target == {'x': .9, 'y': -.9}
    ui.publish_face([(float('nan'), 0, 0)], 640, 480)
    assert ui.app.test_client().get('/api/fairy/state').json['faceTarget'] is None
    ui.publish_face([], 640, 480)
    assert ui.app.test_client().get('/api/fairy/state').json['faceTarget'] is None


@pytest.mark.parametrize('flags, expected_fairy, expected_preview', [
    (['--focus'], True, False),
    (['--focus', '--no-fairy-ui'], False, True),
    (['--focus', '--preview'], True, True),
    (['--fairy-ui'], True, False),
])
def test_cli_launch_selection(monkeypatch, tmp_path, flags, expected_fairy, expected_preview):
    import main
    from config import Config, RuntimeConfig
    (tmp_path / 'index.html').write_text('<title>Built</title>')
    monkeypatch.setattr('focus_ui.BUILD_DIR', tmp_path)
    monkeypatch.setattr(main, 'load_config', lambda _path: Config(runtime=RuntimeConfig(preview=True)))
    calls = []
    monkeypatch.setattr(main, 'run', lambda config, **kwargs: calls.append((config, kwargs)))
    assert main.main(flags) == 0
    assert len(calls) == 1
    config, kwargs = calls[0]
    assert kwargs.get('fairy_ui', False) is expected_fairy
    assert config.runtime.preview is expected_preview
    if '--focus' in flags:
        assert config.sessions.initial_mode == 'focus'


def test_vision_loop_publishes_existing_capture_and_cleans_up(monkeypatch, tmp_path):
    from dataclasses import replace
    from datetime import datetime, timezone
    from unittest.mock import Mock
    import main
    from capture import Frame
    from config import Config, RuntimeConfig, SessionConfig

    # All capture/model/speech inputs are synthetic; no physical device or Core calls.
    class Capture:
        captured_count = 0
        closed = False
        def start(self): pass
        def read(self, _sequence, _timeout):
            if self.captured_count == 3:
                raise KeyboardInterrupt
            self.captured_count += 1
            return Frame(self.captured_count, datetime.now(timezone.utc), time.perf_counter(),
                         np.zeros((24, 32, 3), dtype=np.uint8))
        def close(self): self.closed = True

    class Bridge(FocusUI):
        closed = False
        def start(self):
            self.commands.put_nowait('toggle-focus')
            return 'http://127.0.0.1/'
        def close(self):
            self.closed = True
            super().close()

    bridge = Bridge()
    monkeypatch.setattr('focus_ui.FocusUI', lambda: bridge)
    monkeypatch.setattr(main, 'DesktopControls', Mock())
    feedback = Mock()
    feedback.drain.return_value = []
    feedback.is_speaking.return_value = False
    feedback.is_muted.return_value = False
    monkeypatch.setattr(main, 'FeedbackService', lambda *args: feedback)
    capture = Capture()
    config = Config(runtime=RuntimeConfig(preview=False), sessions=SessionConfig(initial_mode='focus'))
    config = replace(config, storage=replace(config.storage, database=tmp_path / 'session.db'),
                     ascend=replace(config.ascend, enabled=False))
    main.run(config, capture=capture, detector=Mock(detect=Mock(return_value=None)),
             hand_tracker=Mock(detect=Mock(return_value=[])), face_tracker=Mock(spec=['detect', 'close'], detect=Mock(return_value=[])),
             voice_listener=False, fairy_ui=True)
    assert capture.captured_count == 3
    state = bridge.app.test_client().get('/api/fairy/state').json
    assert state['cameraReady'] is True
    assert state['mode'] == 'background'
    assert state['audioLevel'] == 0
    assert capture.closed and bridge.closed
