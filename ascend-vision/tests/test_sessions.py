from datetime import datetime, timedelta, timezone
from dataclasses import replace
import threading
import sqlite3

from db import Database
from session_manager import SessionManager
from state_machine import HoldEvent

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def test_modes_events_and_mid_hold_switch(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        manager = SessionManager(db, now=lambda: NOW)
        manager.start()
        first = HoldEvent(1, NOW, .8, True)
        manager.record_start(first)
        manager.request('focus')
        manager.process_commands(NOW + timedelta(seconds=1))
        assert manager.mode == 'focus'
        manager.record_update(replace(first, duration_seconds=2.), ended=True)
        second = HoldEvent(2, NOW + timedelta(seconds=2), .9, False)
        manager.record_start(second)
        manager.record_update(replace(second, duration_seconds=.5), ended=True)
        manager.close(NOW + timedelta(seconds=3))
        rows = db.connection.execute('SELECT mode,duration_seconds FROM phone_events ORDER BY id').fetchall()
        assert [tuple(r) for r in rows] == [('background', 2.), ('focus', .5)]
        assert db.connection.execute('SELECT COUNT(*) FROM sessions WHERE end_time IS NULL').fetchone()[0] == 0


def test_thread_callbacks_only_queue_and_duplicate_mode_is_noop(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        manager = SessionManager(db, now=lambda: NOW)
        manager.start()
        worker = threading.Thread(target=lambda: manager.request('focus'))
        worker.start(); worker.join()
        assert manager.mode == 'background'
        manager.process_commands(NOW + timedelta(seconds=1))
        manager.request('focus')
        manager.process_commands(NOW + timedelta(seconds=2))
        assert db.connection.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 2
        manager.request('quit')
        manager.process_commands(NOW + timedelta(seconds=3))
        assert manager.quit_requested
        manager.close()


def test_buffered_frame_remains_in_previous_session(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        manager = SessionManager(db, now=lambda: NOW)
        manager.start()
        manager.request('focus')
        manager.process_commands(NOW + timedelta(seconds=2))
        manager.record_start(HoldEvent(1, NOW + timedelta(seconds=1), .8, True))
        assert db.connection.execute('SELECT mode FROM phone_events').fetchone()[0] == 'background'
        manager.close()


def test_open_event_progress_is_visible_to_other_readers(tmp_path, monkeypatch):
    ticks = [0.]
    monkeypatch.setattr('session_manager.time.monotonic', lambda: ticks[0])
    path = tmp_path / 'test.db'
    with Database(path) as db:
        manager = SessionManager(db, now=lambda: NOW, update_seconds=1.)
        manager.start()
        event = HoldEvent(1, NOW, .9, True)
        manager.record_start(event)
        ticks[0] = 2.
        manager.record_update(replace(event, duration_seconds=2.))
        with sqlite3.connect(path) as observer:
            assert observer.execute('SELECT duration_seconds FROM phone_events').fetchone()[0] == 2.
        assert manager.active_event_id == 1
        manager.close()


def test_concurrent_multi_event_persistence(tmp_path):
    path = tmp_path / 'test.db'
    with Database(path) as db:
        manager = SessionManager(db, now=lambda: NOW, update_seconds=0.0)
        manager.start('focus')

        phone_event = HoldEvent(1, NOW, 0.9, True, posture='texting')
        yawn_event = HoldEvent(1, NOW + timedelta(seconds=1), 0.8, True)

        # Start phone hold and yawn concurrently
        phone_id = manager.record_start(phone_event, event_type='phone_held', posture='texting')
        yawn_id = manager.record_start(yawn_event, event_type='yawn')

        assert 'phone_held' in manager.active_event_types
        assert 'yawn' in manager.active_event_types

        # Update durations concurrently
        manager.record_update(replace(phone_event, duration_seconds=5.0), event_type='phone_held')
        manager.record_update(replace(yawn_event, duration_seconds=2.5), ended=True, event_type='yawn')

        # Yawn ended, phone still active
        assert 'yawn' not in manager.active_event_types
        assert 'phone_held' in manager.active_event_types

        manager.record_update(replace(phone_event, duration_seconds=6.0), ended=True, event_type='phone_held')
        assert len(manager.active_event_types) == 0

        # Verify DB records and queries
        counts = db.session_event_counts(manager.session_id)
        assert counts == {'phone_held': 1, 'yawn': 1}

        summary = db.session_events_summary(manager.session_id)
        assert summary['phone_held']['total_duration_seconds'] == 6.0
        assert summary['yawn']['total_duration_seconds'] == 2.5
        manager.close()


def test_unpersisted_or_duplicate_event_update_does_not_crash(tmp_path):
    path = tmp_path / 'test.db'
    with Database(path) as db:
        manager = SessionManager(db, now=lambda: NOW, update_seconds=0.0)
        manager.start('focus')

        unpersisted = HoldEvent(99, NOW, 0.8, True)
        # record_update on unpersisted event should gracefully return without raising ValueError
        manager.record_update(unpersisted, event_type='yawn')
        manager.record_update(unpersisted, ended=True, event_type='yawn')

        # Overlapping start closes previous unended event safely
        first = HoldEvent(1, NOW, 0.9, True)
        manager.record_start(first, event_type='yawn')
        second = HoldEvent(2, NOW + timedelta(seconds=1), 0.9, True)
        manager.record_start(second, event_type='yawn')
        assert 'yawn' in manager.active_event_types
        manager.record_update(replace(second, duration_seconds=1.0), ended=True, event_type='yawn')
        assert 'yawn' not in manager.active_event_types

        manager.close()


