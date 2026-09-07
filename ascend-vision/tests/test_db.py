from datetime import datetime, timedelta, timezone
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from db import Database

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)


def test_schema_and_event_are_durable_with_foreign_keys(tmp_path):
    path = tmp_path / 'data' / 'test.db'
    with Database(path) as db:
        sid = db.start_session('background', NOW)
        event = db.start_event(sid, NOW, .8)
        db.update_event(event, 2.5)
        assert db.connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute("INSERT INTO sessions(start_time,mode) VALUES('x','invalid')")
        db.connection.rollback()
        db.end_session(sid, NOW + timedelta(seconds=3))
    with sqlite3.connect(path) as connection:
        row = connection.execute('SELECT session_id,duration_seconds,confidence,mode,roast_text FROM phone_events').fetchone()
        assert row == (sid, 2.5, .8, 'background', None)


def test_recovery_ends_at_heartbeat_not_restart(tmp_path):
    path = tmp_path / 'test.db'
    with Database(path) as db:
        sid = db.start_session('focus', NOW)
        db.heartbeat(sid, NOW + timedelta(seconds=4))
        # Closing the connection without end_session simulates an interrupted app.
    with Database(path) as db:
        db.recover()
        end = db.connection.execute('SELECT end_time FROM sessions WHERE id=?', (sid,)).fetchone()[0]
        assert datetime.fromisoformat(end) == NOW + timedelta(seconds=4)


def test_atomic_switch_and_timestamp_lookup(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        first = db.start_session('background', NOW)
        second = db.switch_session(first, 'focus', NOW + timedelta(seconds=2))
        assert db.session_at(NOW + timedelta(seconds=1)) == (first, 'background')
        assert db.session_at(NOW + timedelta(seconds=2)) == (second, 'focus')
        assert db.connection.execute('SELECT COUNT(*) FROM sessions WHERE end_time IS NULL').fetchone()[0] == 1


def test_second_owner_is_rejected_and_lock_released(tmp_path):
    path = tmp_path / 'test.db'
    with Database(path):
        with pytest.raises(RuntimeError, match='already in use'):
            Database(path)
    with Database(path):
        pass


@pytest.mark.parametrize('duration', [-1., float('nan'), float('inf')])
def test_invalid_duration_rejected(tmp_path, duration):
    with Database(tmp_path / 'test.db') as db:
        sid = db.start_session('background', NOW)
        event = db.start_event(sid, NOW, .8)
        with pytest.raises(ValueError):
            db.update_event(event, duration)


def test_failed_switch_rolls_back_old_session(tmp_path):
    with Database(tmp_path / 'test.db') as db:
        sid = db.start_session('background', NOW)
        with pytest.raises(sqlite3.IntegrityError):
            db.switch_session(sid, 'invalid', NOW + timedelta(seconds=1))
        assert db.connection.execute('SELECT end_time FROM sessions WHERE id=?', (sid,)).fetchone()[0] is None
        assert db.connection.execute('SELECT session_id FROM app_state').fetchone()[0] == sid


def test_recovery_keeps_latest_persisted_event_progress(tmp_path):
    path = tmp_path / 'test.db'
    with Database(path) as db:
        sid = db.start_session('focus', NOW)
        event = db.start_event(sid, NOW + timedelta(seconds=1), .8)
        db.update_event(event, 3.)
    with Database(path) as db:
        db.recover()
        end = db.connection.execute('SELECT end_time FROM sessions').fetchone()[0]
        assert datetime.fromisoformat(end) == NOW + timedelta(seconds=4)


def test_abrupt_process_exit_releases_lock_and_recovers(tmp_path):
    path = tmp_path / 'crash.db'
    script = ("from db import Database; from datetime import datetime,timezone; import os; "
              f"db=Database({str(path)!r}); "
              "db.start_session('focus',datetime(2026,9,6,tzinfo=timezone.utc)); os._exit(17)")
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 17, result.stderr
    with Database(path) as db:
        assert db.recover() == 1
        assert db.connection.execute('SELECT end_time FROM sessions').fetchone()[0] is not None
