from datetime import datetime, timezone, date
import sqlite3

import pytest

from db import Database
from dashboard_stats import read_stats, DashboardError


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def session(db, start, end, mode='focus'):
    sid = db.start_session(mode, dt(start))
    if end:
        db.end_session(sid, dt(end))
    return sid


def event(db, sid, start, seconds):
    eid = db.start_event(sid, dt(start), .9)
    db.update_event(eid, seconds)
    return eid


def stats(path, **kwargs):
    return read_stats(path, date(2026, 9, 1), date(2026, 9, 7),
                      zone='UTC', now=dt('2026-09-08T00:00:00'), **kwargs)


def test_counts_durations_streaks_and_reading_while_writer_open(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        a = session(db, '2026-09-01T09:00:00', '2026-09-01T10:00:00')
        event(db, a, '2026-09-01T09:20:00', 600)
        b = session(db, '2026-09-01T11:00:00', '2026-09-01T12:00:00', 'background')
        event(db, b, '2026-09-01T11:10:00', 0)
        result = stats(path)
        assert result['summary'] == dict(pickups=2, phone_seconds=600., focus_seconds=3600.,
            monitored_seconds=7200., longest_streak_seconds=3000.)
        assert result['days'][0]['pickups'] == 2
        assert sum(d['pickups'] for d in result['days']) == 2
        assert len(result['days']) == 7 and len(result['weeks']) == 2
        assert stats(path, mode='focus')['summary']['longest_streak_seconds'] == 1800
        assert stats(path, mode='background')['summary']['focus_seconds'] == 0
        # Dashboard has neither ended nor recovered sessions or modified user_version.
        assert db.connection.execute('PRAGMA user_version').fetchone()[0] == 1


def test_midnight_and_range_clipping(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        sid = session(db, '2026-08-31T23:00:00', '2026-09-01T01:00:00')
        event(db, sid, '2026-08-31T23:59:00', 120)
    result = stats(path)
    assert result['summary']['pickups'] == 0
    assert result['summary']['phone_seconds'] == 60
    assert result['summary']['focus_seconds'] == 3600
    assert result['summary']['longest_streak_seconds'] == 3540


def test_open_session_never_counts_unobserved_downtime(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        sid = session(db, '2026-09-01T09:00:00', None)
        db.heartbeat(sid, dt('2026-09-01T09:10:00'))
        event(db, sid, '2026-09-01T09:11:00', 60)
        result = stats(path)
        assert result['summary']['focus_seconds'] == 720
        assert result['summary']['longest_streak_seconds'] == 660
        assert db.connection.execute('SELECT end_time FROM sessions').fetchone()[0] is None


def test_cross_mode_hold_blocks_focus_streak(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        sid = session(db, '2026-09-01T09:00:00', '2026-09-01T09:10:00', 'background')
        event(db, sid, '2026-09-01T09:09:00', 120)
        session(db, '2026-09-01T09:10:00', '2026-09-01T09:20:00')
    result = stats(path, mode='focus')
    assert result['summary']['pickups'] == 0
    assert result['summary']['longest_streak_seconds'] == 540


@pytest.mark.parametrize('day,start,end,seconds', [
    ('2026-03-08', '2026-03-08T05:00:00', '2026-03-09T04:00:00', 23*3600),
    ('2026-11-01', '2026-11-01T04:00:00', '2026-11-02T05:00:00', 25*3600)])
def test_dst_calendar_days(tmp_path, day, start, end, seconds):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        session(db, start, end)
    result = read_stats(path, date.fromisoformat(day), date.fromisoformat(day),
        zone='America/New_York', now=dt('2026-12-01T00:00:00'))
    assert result['summary']['focus_seconds'] == seconds


def test_missing_database_is_empty_without_creating_it(tmp_path):
    path = tmp_path / 'missing.db'
    result = stats(path)
    assert result['database_exists'] is False
    assert result['summary']['pickups'] == 0
    assert not path.exists()


def test_unsupported_schema_is_reported_without_mutation(tmp_path):
    path = tmp_path / 'future.db'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=9')
    with pytest.raises(DashboardError):
        stats(path)


def test_pickups_bucket_in_requested_timezone_and_zero_days_are_retained(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        sid = session(db, '2026-09-01T15:00:00', '2026-09-01T18:00:00')
        event(db, sid, '2026-09-01T15:59:59', 2)
        event(db, sid, '2026-09-01T16:00:00', 0)
    result = read_stats(path, date(2026, 9, 1), date(2026, 9, 3), zone='Asia/Shanghai',
                        now=dt('2026-09-08T00:00:00'))
    assert [day['pickups'] for day in result['days']] == [1, 1, 0]
    assert result['summary']['phone_seconds'] == 2  # overlap is not double-counted


def test_adjacent_sessions_join_but_zero_duration_pickup_splits_streak(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        a = session(db, '2026-09-01T09:00:00', '2026-09-01T09:30:00')
        event(db, a, '2026-09-01T09:15:00', 0)
        session(db, '2026-09-01T09:30:00', '2026-09-01T10:00:00', 'background')
    assert stats(path)['summary']['longest_streak_seconds'] == 45*60


def test_future_progress_is_clamped_to_now(tmp_path):
    path = tmp_path / 'watch.db'
    with Database(path) as db:
        session(db, '2026-09-01T09:00:00', '2026-09-01T10:00:00')
    result = read_stats(path, date(2026, 9, 1), date(2026, 9, 1), zone='UTC',
                        now=dt('2026-09-01T09:30:00'))
    assert result['summary']['monitored_seconds'] == 1800
