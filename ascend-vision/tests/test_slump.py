from datetime import date, datetime, timedelta, timezone

from assistant.slump import (Ladder, SlumpStore, assess, backfill, focus_days, room_session_days,
                             sample_minute)

TODAY = date(2026, 10, 29)


def series(baseline, recent, *, baseline_days=14, recent_days=3):
    """Values for the baseline window, then the 3 days before TODAY."""
    days = {}
    for offset in range(1, recent_days + 1):
        days[TODAY - timedelta(days=offset)] = recent
    for offset in range(4, 4 + baseline_days):
        days[TODAY - timedelta(days=offset)] = baseline
    return days


def test_a_drop_needs_the_recent_average_below_half_the_baseline():
    assert assess({'commits': series(4, 1)}, TODAY).fired == ('commits',)
    assert assess({'commits': series(4, 3)}, TODAY).fired == ()
    assert assess({'commits': series(4, 1)}, TODAY).signals['commits'] == (1, 4)


def test_two_signals_make_a_slump():
    both = assess({'commits': series(4, 1), 'active_minutes': series(300, 60)}, TODAY)
    assert both.slump and set(both.fired) == {'commits', 'active_minutes'}
    one = assess({'commits': series(4, 1), 'active_minutes': series(300, 280)}, TODAY)
    assert not one.slump


def test_late_nights_fire_over_thirty_and_baseline_plus_thirty():
    assert assess({'late_minutes': series(10, 45)}, TODAY).fired == ('late_minutes',)
    assert assess({'late_minutes': series(10, 35)}, TODAY).fired == ()
    assert assess({'late_minutes': series(0, 25)}, TODAY).fired == ()


def test_distraction_fires_over_thirty_and_twice_the_baseline():
    assert assess({'distraction_minutes': series(20, 50)}, TODAY).fired == ('distraction_minutes',)
    assert assess({'distraction_minutes': series(20, 35)}, TODAY).fired == ()


def test_metrics_without_seven_baseline_days_are_ignored():
    result = assess({'commits': series(4, 1, baseline_days=6)}, TODAY)
    assert 'commits' not in result.signals
    assert result.learning and not result.slump
    assert not assess({'commits': series(4, 1, baseline_days=7)}, TODAY).learning


def test_missing_recent_days_are_not_zero():
    result = assess({'focus_minutes': series(60, 0, recent_days=0)}, TODAY)
    assert result.fired == ()
    assert 'focus_minutes' not in result.signals
    assert not result.learning


def test_today_is_not_part_of_the_recent_window():
    days = series(4, 4)
    days[TODAY] = 0
    assert assess({'commits': days}, TODAY).fired == ()


def test_store_adds_and_survives_reopen(tmp_path):
    path = tmp_path / 'slump.db'
    SlumpStore(path).add(TODAY, 'active_minutes', 1)
    store = SlumpStore(path)
    store.add(TODAY, 'active_minutes', 1)
    store.put(TODAY, 'commits', 3)
    store.put(TODAY, 'commits', 5)
    assert store.days(TODAY) == {'active_minutes': {TODAY: 2}, 'commits': {TODAY: 5}}


def test_store_prunes_after_ninety_days(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    store.put(TODAY - timedelta(days=91), 'commits', 1)
    store.put(TODAY - timedelta(days=90), 'commits', 2)
    store.prune(TODAY)
    assert store.days(TODAY - timedelta(days=200)) == {'commits': {TODAY - timedelta(days=90): 2}}


def test_store_keeps_the_ladder(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    assert store.ladder() == Ladder()
    ladder = Ladder(step=2, last_day=TODAY, slump_days=2, paused_until=TODAY + timedelta(days=3), accepted=True)
    store.save_ladder(ladder)
    assert SlumpStore(tmp_path / 'slump.db').ladder() == ladder


def _focus_db(path, rows):
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, start_time TEXT NOT NULL, end_time TEXT, mode TEXT NOT NULL)")
        db.executemany('INSERT INTO sessions (start_time, end_time, mode) VALUES (?, ?, ?)', rows)
    db.close()


def _local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute).astimezone()


def test_minutes_count_active_time_and_late_time(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    sample_minute(store, 'input_active', datetime(2026, 10, 29, 2, 10))
    sample_minute(store, 'input_active', datetime(2026, 10, 29, 14, 0))
    sample_minute(store, 'input_idle', datetime(2026, 10, 29, 3, 0))
    sample_minute(store, 'input_idle', datetime(2026, 10, 30, 10, 0))
    assert store.days(TODAY) == {
        'active_minutes': {TODAY: 2, TODAY + timedelta(days=1): 0},
        'late_minutes': {TODAY: 1, TODAY + timedelta(days=1): 0},
    }


def test_backfill_puts_github_and_room_days(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    yesterday = TODAY - timedelta(days=1)
    calendar = {yesterday: 3, TODAY - timedelta(days=2): 0}
    room = {yesterday: 2}
    backfill(store, today=TODAY, calendar=calendar, room_sessions=room)
    days = store.days(TODAY - timedelta(days=10))
    assert days['commits'] == calendar
    assert days['room_sessions'] == room


def test_an_unreachable_source_leaves_stored_days_alone(tmp_path):
    store = SlumpStore(tmp_path / 'slump.db')
    yesterday = TODAY - timedelta(days=1)
    store.put(yesterday, 'commits', 5)
    store.put(yesterday, 'room_sessions', 2)
    backfill(store, today=TODAY, calendar=None, room_sessions=None,
             focus_db=tmp_path / 'missing.db', distraction_db=tmp_path / 'missing2.db')
    assert store.days(yesterday) == {'commits': {yesterday: 5}, 'room_sessions': {yesterday: 2}}


def test_focus_minutes_sum_focus_sessions_per_local_day(tmp_path):
    path = tmp_path / 'phone_watch.db'
    yesterday = TODAY - timedelta(days=1)
    start = _local(yesterday, 9)
    _focus_db(path, [
        (start.isoformat(), (start + timedelta(minutes=25)).isoformat(), 'focus'),
        ((start + timedelta(hours=2)).isoformat(), (start + timedelta(hours=2, minutes=50)).isoformat(), 'focus'),
        ((start + timedelta(hours=4)).isoformat(), (start + timedelta(hours=6)).isoformat(), 'background'),
        ((start + timedelta(hours=7)).isoformat(), None, 'focus'),
    ])
    assert focus_days(path) == {yesterday: 75}
    assert focus_days(tmp_path / 'missing.db') == {}


def test_distraction_days_fill_quiet_days_with_zero(tmp_path):
    import sqlite3
    path = tmp_path / 'distractions.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE distraction_time (day TEXT NOT NULL, site TEXT NOT NULL, seconds REAL NOT NULL)')
        db.executemany('INSERT INTO distraction_time VALUES (?, ?, ?)', [
            ((TODAY - timedelta(days=3)).isoformat(), 'youtube.com', 600),
            ((TODAY - timedelta(days=3)).isoformat(), 'reddit.com', 300),
        ])
    db.close()
    store = SlumpStore(tmp_path / 'slump.db')
    backfill(store, today=TODAY, distraction_db=path)
    assert store.days(TODAY - timedelta(days=5))['distraction_minutes'] == {
        TODAY - timedelta(days=3): 15, TODAY - timedelta(days=2): 0, TODAY - timedelta(days=1): 0}


def test_room_session_days_use_local_dates_and_fill_quiet_days():
    yesterday = TODAY - timedelta(days=1)
    late = _local(yesterday, 23, 30)
    payload = {'sessions': [
        {'createdAt': late.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')},
        {'createdAt': _local(TODAY - timedelta(days=3), 10).astimezone(timezone.utc).isoformat()},
        {'createdAt': 'garbage'},
        {},
    ]}
    assert room_session_days(payload, today=TODAY) == {
        TODAY - timedelta(days=3): 1, TODAY - timedelta(days=2): 0, yesterday: 1}
    assert room_session_days({'sessions': []}, today=TODAY) == {}
    assert room_session_days({'nope': 1}, today=TODAY) == {}
