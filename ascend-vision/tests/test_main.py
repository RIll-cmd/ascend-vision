import logging
import threading
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


def test_dashboard_chat_uses_stable_session_start_time(tmp_path, monkeypatch):
    from assistant.service import AssistantReply
    from dashboard import create_app
    from integrations.chat_ipc import ChatIpcQueue
    from integrations.chat_runtime import ChatRuntimeBridge

    config = replace(
        Config(runtime=RuntimeConfig(preview=False)),
        storage=replace(Config().storage, database=tmp_path / "session.db"),
        ascend=replace(Config().ascend, enabled=False),
        feedback=replace(Config().feedback, enabled=False),
    )
    queue = ChatIpcQueue(tmp_path / "chat_ipc.db")
    client = create_app(config, chat_queue=queue).test_client()
    assert client.post('/api/chat/messages', json={'text': 'early chat'}).status_code == 202
    first_seen = threading.Event()
    second_seen = threading.Event()
    observed = []

    class Assistant:
        def respond(self, user_text, context, *, max_words):
            observed.append((context.session_duration_minutes, time.perf_counter()))
            (first_seen if user_text == 'early chat' else second_seen).set()
            return AssistantReply('Acknowledged.', 'offline')

    class DelayedBridge(ChatRuntimeBridge):
        def __init__(self, queue, handler):
            super().__init__(queue, handler, poll_seconds=0.01)

        def start(self):
            time.sleep(0.25)
            super().start()
            assert first_seen.wait(2), 'queued chat was not handled before timer reset'

    monkeypatch.setattr('integrations.chat_runtime.ChatRuntimeBridge', DelayedBridge)

    class SecondChatStream(Stream):
        def read(self, after_sequence, timeout):
            assert client.post('/api/chat/messages', json={'text': 'later chat'}).status_code == 202
            assert second_seen.wait(2), 'chat was not handled after timer reset'
            raise KeyboardInterrupt

    class Face:
        def close(self):
            pass

    run(config, detector=Detector(), capture=SecondChatStream(), hand_tracker=Hands(),
        face_tracker=Face(), voice_listener=False, assistant_service=Assistant())

    assert len(observed) == 2
    first_minutes, first_time = observed[0]
    later_minutes, later_time = observed[1]
    assert later_minutes >= first_minutes
    assert (later_minutes - first_minutes) * 60 == pytest.approx(later_time - first_time, abs=0.02)


def test_camera_runtime_continues_when_dashboard_queue_is_unavailable(tmp_path, monkeypatch):
    def unavailable(_path):
        raise OSError("private storage path")

    monkeypatch.setattr('integrations.chat_ipc.ChatIpcQueue', unavailable)

    class Face:
        def detect(self, _frame, _timestamp):
            return []

        def close(self):
            pass

    config = Config(runtime=RuntimeConfig(preview=False))
    config = replace(
        config,
        storage=replace(config.storage, database=tmp_path / 'session.db'),
        ascend=replace(config.ascend, enabled=False),
        feedback=replace(config.feedback, enabled=False),
    )
    stream = Stream()

    run(config, detector=Detector(), capture=stream, hand_tracker=Hands(),
        face_tracker=Face(), voice_listener=False)

    assert stream.closed
    assert stream.captured_count >= 4


def test_runtime_discards_old_proposals_but_keeps_approved_memories(tmp_path):
    from assistant.memory import MemoryStore
    from dashboard import create_app
    from integrations.chat_ipc import ChatIpcQueue

    database_path = tmp_path / 'session.db'
    memory_path = tmp_path / 'assistant_memory.db'
    memory = MemoryStore(memory_path)
    memory.propose('I like mango')
    memory.approve(memory.propose('I prefer green tea'))
    queue = ChatIpcQueue(tmp_path / 'chat_ipc.db')
    config = replace(
        Config(runtime=RuntimeConfig(preview=False)),
        storage=replace(Config().storage, database=database_path),
        ascend=replace(Config().ascend, enabled=False),
        feedback=replace(Config().feedback, enabled=False),
    )
    app = create_app(config, chat_queue=queue, memory_store=memory)
    client = app.test_client()
    submission = client.post('/api/chat/messages', json={'text': 'What do you remember about me?'})
    message_id = submission.json['messageId']

    class WaitingStream(Stream):
        def read(self, after_sequence, timeout):
            frame = super().read(after_sequence, timeout)
            if self.sequence == 1:
                return frame
            deadline = time.monotonic() + 2.0
            while not client.get('/api/chat/messages?after=0').json['messages'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not client.get('/api/chat/messages?after=0').json['messages']:
                raise AssertionError('Vision did not answer the memory query')
            raise KeyboardInterrupt

    processed = []

    class Face:
        def detect(self, _frame, _timestamp):
            processed.append(_frame)
            return []

        def close(self):
            pass

    run(config, detector=Detector(), capture=WaitingStream(), hand_tracker=Hands(),
        face_tracker=Face(), voice_listener=False)

    assert memory.pending() == []
    assert [item['text'] for item in memory.active()] == ['I prefer green tea']
    assert len(processed) == 1
    reply = client.get('/api/chat/messages?after=0').json['messages'][0]
    assert reply['messageId'] == message_id
    assert 'green tea' in reply['text'].lower()


def test_runtime_answers_dashboard_hub_status_with_dedicated_credential(tmp_path, monkeypatch):
    from dashboard import create_app
    from integrations.chat_ipc import ChatIpcQueue
    from integrations.status_shelf import ShelfService, ShelfSnapshot

    now = datetime.now(timezone.utc)
    opened = []

    class Reader:
        def __init__(self, base_url, credential, *, timeout_seconds):
            opened.append((base_url, credential, timeout_seconds))

        def read(self):
            return ShelfSnapshot(now, (
                ShelfService('codex-cli', 'desktop', 'agent', 'working', now, now, 30),
            ))

    monkeypatch.setattr('main.StatusShelfReader', Reader, raising=False)
    monkeypatch.setenv('ASCEND_CORE_BASE_URL', 'http://localhost:8000')
    monkeypatch.setenv('ASCEND_STATUS_READ_CREDENTIAL', 'reader-id.reader-secret')
    database_path = tmp_path / 'session.db'
    queue = ChatIpcQueue(tmp_path / 'chat_ipc.db')
    config = replace(
        Config(runtime=RuntimeConfig(preview=False)),
        storage=replace(Config().storage, database=database_path),
        feedback=replace(Config().feedback, enabled=False),
    )
    client = create_app(config, chat_queue=queue).test_client()
    submission = client.post('/api/chat/messages', json={'text': 'Is Codex CLI still working?'})

    class WaitingStream(Stream):
        def read(self, after_sequence, timeout):
            frame = super().read(after_sequence, timeout)
            if self.sequence == 1:
                return frame
            deadline = time.monotonic() + 2.0
            while not client.get('/api/chat/messages?after=0').json['messages'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not client.get('/api/chat/messages?after=0').json['messages']:
                raise AssertionError('Vision did not answer the status query')
            raise KeyboardInterrupt

    class Face:
        def detect(self, _frame, _timestamp):
            return []

        def close(self):
            pass

    run(config, detector=Detector(), capture=WaitingStream(), hand_tracker=Hands(),
        face_tracker=Face(), voice_listener=False)

    reply = client.get('/api/chat/messages?after=0').json['messages'][0]
    assert reply['messageId'] == submission.json['messageId']
    assert reply['text'] == 'Codex CLI is working.'
    assert opened == [('http://localhost:8000', 'reader-id.reader-secret', 3.0)]


def test_microphone_hub_status_reaches_assistant_without_replacing_session_status(tmp_path, monkeypatch):
    from voice_listener import VoiceCommandParser

    chats = []
    announcements = []

    class Feedback:
        enabled = False

        def __init__(self, *_args):
            pass

        def start(self):
            assert hasattr(self, 'assistant')

        def set_session(self, _session):
            pass

        def bind_assistant(self, assistant):
            self.assistant = assistant

        def submit_chat(self, text, _context, **_kwargs):
            chats.append(text)
            return True

        def speak_announcement(self, text):
            announcements.append(text)
            return True

        def is_muted(self):
            return False

        def is_speaking(self):
            return False

        def drain(self):
            return []

        def close(self):
            pass

    class Listener:
        def __init__(self, _config, *, callback, unmatched_callback, is_speaking):
            self.callback = callback
            self.unmatched_callback = unmatched_callback

        def start(self):
            parser = VoiceCommandParser()
            self.callback(parser.parse("What is Antigravity's status?"))
            self.unmatched_callback("Is Codex CLI still working?")
            self.callback(parser.parse("status report"))

        def close(self):
            pass

    class Face:
        def detect(self, _frame, _timestamp):
            return []

        def close(self):
            pass

    config = Config(runtime=RuntimeConfig(preview=False))
    config = replace(
        config,
        storage=replace(config.storage, database=tmp_path / 'session.db'),
        ascend=replace(config.ascend, enabled=False),
        feedback=replace(config.feedback, enabled=False),
        voice_commands=replace(config.voice_commands, enabled=True),
    )
    monkeypatch.setattr('main.FeedbackService', Feedback)
    monkeypatch.setattr('main.VoiceCommandListener', Listener)

    run(config, detector=Detector(), capture=Stream(), hand_tracker=Hands(), face_tracker=Face())

    assert chats == ["What is Antigravity's status?", "Is Codex CLI still working?"]
    assert len(announcements) == 1
    assert announcements[0].startswith('Session status:')


def test_memory_storage_failure_keeps_camera_and_dashboard_chat_available(tmp_path, monkeypatch):
    from dashboard import create_app
    from integrations.chat_ipc import ChatIpcQueue

    def unavailable(_path):
        raise OSError('private memory path')

    monkeypatch.setattr('main.MemoryStore', unavailable)
    queue = ChatIpcQueue(tmp_path / 'chat_ipc.db')
    database_path = tmp_path / 'session.db'
    config = Config(runtime=RuntimeConfig(preview=False))
    config = replace(
        config,
        storage=replace(config.storage, database=database_path),
        ascend=replace(config.ascend, enabled=False),
        feedback=replace(config.feedback, enabled=False),
    )
    app = create_app(config, chat_queue=queue)
    client = app.test_client()
    submission = client.post('/api/chat/messages', json={'text': 'hello'})

    class WaitingStream(Stream):
        def read(self, after_sequence, timeout):
            frame = super().read(after_sequence, timeout)
            if self.sequence == 1:
                return frame
            deadline = time.monotonic() + 2.0
            while not client.get('/api/chat/messages?after=0').json['messages'] and time.monotonic() < deadline:
                time.sleep(0.01)
            if not client.get('/api/chat/messages?after=0').json['messages']:
                raise AssertionError('Chat stopped when memory storage failed')
            raise KeyboardInterrupt

    processed = []

    class Face:
        def detect(self, _frame, _timestamp):
            processed.append(_frame)
            return []

        def close(self):
            pass

    stream = WaitingStream()
    run(config, detector=Detector(), capture=stream, hand_tracker=Hands(),
        face_tracker=Face(), voice_listener=False)

    replies = client.get('/api/chat/messages?after=0').json['messages']
    assert stream.closed and stream.captured_count >= 2
    assert len(processed) == 1
    assert replies[0]['messageId'] == submission.json['messageId']
    assert replies[0]['status'] == 'reply'
