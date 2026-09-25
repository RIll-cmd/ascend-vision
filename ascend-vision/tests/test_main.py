import logging
from dataclasses import replace
from datetime import datetime, timezone
import time

import numpy as np
import pytest
import sqlite3

from capture import Frame, CaptureError
from config import Config, RuntimeConfig, DetectorConfig
from detector import PhoneBox
from main import main, run


class Stream:
    def __init__(self):
        self.closed = False
        self.sequence = 0
        self.captured_count = 0

    def start(self):
        return self

    def read(self, after_sequence, timeout):
        self.sequence += 1
        self.captured_count += 1
        if self.sequence > 4:
            raise KeyboardInterrupt
        return Frame(self.sequence, datetime.now(timezone.utc), self.sequence * .1,
                     np.zeros((4, 4, 3), dtype=np.uint8))

    def close(self):
        self.closed = True


class Detector:
    def detect(self, frame):
        return PhoneBox((0., 0., 2., 2.), .8)


class Hands:
    def detect(self, frame, timestamp):
        return []
    def close(self):
        self.closed = True


def test_loop_logs_each_detection_and_closes_on_interrupt(caplog):
    stream = Stream()
    config = Config(runtime=RuntimeConfig(preview=False),
                    detector=DetectorConfig(every_n_frames=2))
    with caplog.at_level(logging.INFO):
        run(config, detector=Detector(), capture=stream, hand_tracker=Hands())
    assert stream.closed
    assert sum('PHONE_VISIBLE' in record.message for record in caplog.records) == 2
    assert any('STATS' in record.message for record in caplog.records)


def test_loop_cleans_up_on_inference_error():
    stream = Stream()
    class BrokenDetector:
        def detect(self, frame):
            raise RuntimeError('inference failed')
    with pytest.raises(RuntimeError, match='inference failed'):
        run(Config(runtime=RuntimeConfig(preview=False)),
            detector=BrokenDetector(), capture=stream, hand_tracker=Hands())
    assert stream.closed


def test_cli_config_error_returns_nonzero(tmp_path):
    assert main(['--config', str(tmp_path / 'missing.yaml')]) == 1


def test_same_frame_hold_confirmation_and_shutdown_finalize(caplog):
    from config import HoldConfig
    stream = Stream()
    seen = []
    class NearHands(Hands):
        def detect(self, frame, timestamp):
            seen.append((frame, timestamp))
            return [[(1., 1., 0.)] * 21]
    hands = NearHands()
    cfg = Config(runtime=RuntimeConfig(preview=False), hold=HoldConfig(threshold_frames=2))
    with caplog.at_level(logging.INFO):
        run(cfg, detector=Detector(), capture=stream, hand_tracker=hands)
    starts = [r.message for r in caplog.records if 'HOLD_STARTED' in r.message]
    ends = [r.message for r in caplog.records if 'HOLD_ENDED' in r.message]
    assert len(starts) == len(ends) == 1
    assert 'duration_s=0.200' in ends[0]
    assert 'reason=shutdown' in ends[0]
    assert len(seen) == 4
    assert stream.closed and hands.closed


def test_hand_inference_failure_closes_both_resources():
    stream = Stream()
    class BrokenHands(Hands):
        def detect(self, *args):
            raise RuntimeError('hand inference failed')
    hands = BrokenHands()
    with pytest.raises(RuntimeError, match='hand inference failed'):
        run(Config(runtime=RuntimeConfig(preview=False)), detector=Detector(),
            capture=stream, hand_tracker=hands)
    assert stream.closed and hands.closed


def test_persistence_failure_still_closes_camera_and_hands(monkeypatch):
    from config import HoldConfig
    stream = Stream()
    class NearHands(Hands):
        def detect(self, *args):
            return [[(1., 1., 0.)] * 21]
    hands = NearHands()
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr('session_manager.SessionManager.record_start', fail)
    with pytest.raises(OSError, match='disk full'):
        run(Config(runtime=RuntimeConfig(preview=False), hold=HoldConfig(threshold_frames=1)),
            detector=Detector(), capture=stream, hand_tracker=hands)
    assert stream.closed and hands.closed


def test_full_loop_persists_both_modes_without_splitting_a_hold(monkeypatch):
    from config import HoldConfig
    managers = []
    class Controls:
        def __init__(self, config, manager):
            managers.append(manager)
        def start(self): pass
        def refresh(self): pass
        def close(self): pass
    monkeypatch.setattr('main.DesktopControls', Controls)
    class SwitchingStream(Stream):
        def read(self, after_sequence, timeout):
            self.sequence += 1
            self.captured_count += 1
            if self.sequence > 6:
                raise KeyboardInterrupt
            if self.sequence == 2:
                managers[0].request('focus')
            return Frame(self.sequence, datetime.now(timezone.utc), self.sequence * .1,
                         np.full((4, 4, 3), self.sequence, dtype=np.uint8))
    class SwitchingHands(Hands):
        def detect(self, frame, timestamp):
            return [] if int(frame[0, 0, 0]) in (4, 6) else [[(1., 1., 0.)] * 21]
    run(Config(runtime=RuntimeConfig(preview=False), hold=HoldConfig(threshold_frames=1)),
        detector=Detector(), capture=SwitchingStream(), hand_tracker=SwitchingHands())
    with sqlite3.connect('data/phone_watch.db') as db:
        rows = db.execute('SELECT mode,duration_seconds FROM phone_events ORDER BY id').fetchall()
        assert [row[0] for row in rows] == ['background', 'focus']
        assert [row[1] for row in rows] == pytest.approx([.3, .1])
        assert db.execute('SELECT COUNT(*) FROM sessions WHERE end_time IS NULL').fetchone()[0] == 0


@pytest.mark.parametrize('args', [['--duration', '-1'], ['--duration', 'nan'],
                                 ['--camera', '-1'], ['--confidence', '2']])
def test_bad_cli_values_do_not_start_camera(args):
    assert main(args) == 1


@pytest.mark.parametrize('mode', ['background', 'focus'])
def test_feedback_result_persists_to_original_event_at_shutdown(monkeypatch, mode):
    from config import HoldConfig, SessionConfig
    from feedback import FeedbackResult
    services = []
    class Feedback:
        enabled = True
        def __init__(self, *args):
            self.jobs = []
            self.closed = False
            services.append(self)
        def start(self): pass
        def set_session(self, sid): self.sid = sid
        def submit(self, eid, sid, context):
            assert sid == self.sid
            self.jobs.append((eid, context))
            return True
        def close(self): self.closed = True
        def drain(self):
            if not self.closed:
                return []
            result = [FeedbackResult(eid, 'Your phone can wait.', True, True) for eid, _ in self.jobs]
            self.jobs = []
            return result
    class NearHands(Hands):
        def detect(self, *args): return [[(1., 1., 0.)] * 21]
    monkeypatch.setattr('main.FeedbackService', Feedback)
    run(Config(runtime=RuntimeConfig(preview=False), hold=HoldConfig(threshold_frames=1),
               sessions=SessionConfig(initial_mode=mode)),
        detector=Detector(), capture=Stream(), hand_tracker=NearHands())
    assert services[0].closed
    with sqlite3.connect('data/phone_watch.db') as db:
        rows = db.execute('SELECT mode, roast_text FROM phone_events').fetchall()
        assert rows == [(mode, 'Your phone can wait.' if mode == 'focus' else None)]


def test_dashboard_queue_receives_the_shared_assistant_answer(tmp_path):
    from assistant.service import AssistantReply
    from dashboard import create_app
    from integrations.chat_ipc import ChatIpcQueue

    database_path = tmp_path / "session.db"
    config = Config(runtime=RuntimeConfig(preview=False))
    config = replace(
        config,
        storage=replace(config.storage, database=database_path),
        ascend=replace(config.ascend, enabled=False),
        feedback=replace(config.feedback, enabled=False),
    )
    app = create_app(config, chat_queue=ChatIpcQueue(tmp_path / "chat_ipc.db"))
    client = app.test_client()
    submission = client.post('/api/chat/messages', json={'text': 'How is focus going?'})
    assert submission.status_code == 202
    message_id = submission.json['messageId']

    class Assistant:
        def respond(self, user_text, context, *, max_words):
            assert user_text == "How is focus going?"
            assert context.user_query == user_text
            assert max_words == 25
            return AssistantReply("Your focus is steady.", "model")

    class WaitingStream(Stream):
        def read(self, after_sequence, timeout):
            deadline = time.monotonic() + 2.0
            while not client.get('/api/chat/messages?after=0').json['messages'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not client.get('/api/chat/messages?after=0').json['messages']:
                raise AssertionError("Vision did not answer the queued dashboard message")
            raise KeyboardInterrupt

    class Face:
        def close(self):
            pass

    run(
        config, detector=Detector(), capture=WaitingStream(),
        hand_tracker=Hands(), face_tracker=Face(), voice_listener=False,
        assistant_service=Assistant(),
    )

    replies = client.get('/api/chat/messages?after=0').json['messages']
    assert len(replies) == 1
    assert replies[0]["messageId"] == message_id
    assert replies[0]["status"] == "reply"
    assert replies[0]["text"] == "Your focus is steady."
