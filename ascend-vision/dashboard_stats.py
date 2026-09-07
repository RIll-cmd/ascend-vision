"""Read-only, snapshot-consistent habit statistics over recorded monitoring time."""
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


class DashboardError(RuntimeError):
    """A safe, user-facing database read failure."""


def get_zone(name):
    if name == 'local':
        return None  # astimezone(None) resolves the OS offset at each timestamp.
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError('Use local or a valid IANA timezone, such as Asia/Shanghai') from exc


def midnight(day, tz):
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC).timestamp()


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Database timestamp has no timezone')
    return parsed.timestamp()


def union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end < start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def longest_free(monitored, holds):
    """Zero-duration confirmed pickups still split a phone-free streak."""
    blocks = union(holds)
    longest = 0.
    index = 0
    for start, end in union(monitored):
        cursor = start
        while index < len(blocks) and blocks[index][1] < start:
            index += 1
        j = index
        while j < len(blocks) and blocks[j][0] <= end:
            left, right = blocks[j]
            longest = max(longest, min(left, end) - cursor)
            cursor = max(cursor, right)
            j += 1
        longest = max(longest, end - cursor)
    return longest


def _snapshot(path, low, high, timeout):
    if not path.exists():
        return [], [], {}, False
    try:
        # mode=ro does not create a database, run migrations or acquire the writer lock.
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=timeout)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise DashboardError('This database schema is newer than this dashboard supports.')
            hi = datetime.fromtimestamp(high, UTC).isoformat(timespec='microseconds')
            lo = datetime.fromtimestamp(low, UTC).isoformat(timespec='microseconds')
            sessions = db.execute('''SELECT id,start_time,end_time,mode FROM sessions
                WHERE start_time < ? AND (end_time IS NULL OR end_time >= ?)''', (hi, lo)).fetchall()
            events = db.execute('''SELECT id,session_id,detected_at,duration_seconds,mode,roast_text
                FROM phone_events WHERE detected_at < ? AND
                (detected_at >= ? OR julianday(detected_at)+duration_seconds/86400.0 >= julianday(?))
                ORDER BY detected_at,id''', (hi, lo, lo)).fetchall()
            heartbeats = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_state'").fetchone():
                heartbeats = {r[0]: timestamp(r[1]) for r in db.execute('SELECT session_id,last_seen FROM app_state')}
            # Include durable progress before the displayed range for orphan open sessions.
            progress = db.execute('''SELECT e.session_id, MAX(julianday(e.detected_at)+e.duration_seconds/86400.0)
                FROM phone_events e JOIN sessions s ON s.id=e.session_id
                WHERE s.end_time IS NULL GROUP BY e.session_id''').fetchall()
            for sid, julian in progress:
                if julian is not None:
                    # Millisecond rounding removes SQLite Julian-date floating point noise.
                    value = round((julian - 2440587.5) * 86400, 3)
                    heartbeats[sid] = max(heartbeats.get(sid, value), value)
            return sessions, events, heartbeats, True
    except sqlite3.Error as exc:
        raise DashboardError('Cannot read the Phone Watch database. Check its schema, permissions or availability.') from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise DashboardError('The database contains invalid session progress.') from exc


def read_stats(path, start, end, *, mode='all', zone='local', now=None, timeout=5.):
    if mode not in ('all', 'focus', 'background'):
        raise ValueError('Mode must be all, focus or background')
    if type(start) is not date or type(end) is not date or end < start or (end-start).days >= 366:
        raise ValueError('Choose an ordered date range of at most 366 days')
    if start.year < 1970 or end.year > 9998:
        raise ValueError('Dates must be between 1970 and 9998')
    tz = get_zone(zone)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError('Current time must be timezone-aware')
    low, calendar_high = midnight(start, tz), midnight(end + timedelta(days=1), tz)
    high = min(calendar_high, now.timestamp())
    days = []
    day = start
    while day <= end:
        days.append({'date': day.isoformat(), 'pickups': 0, 'focus': 0, 'background': 0})
        day += timedelta(days=1)
    by_day = {row['date']: row for row in days}
    summary = dict(pickups=0, phone_seconds=0., focus_seconds=0., monitored_seconds=0., longest_streak_seconds=0.)
    sessions, events, progress, exists = _snapshot(Path(path).resolve(), low, max(high, low), timeout)
    selected_holds, all_holds, monitored, focus, recent = [], [], [], [], []
    try:
        for row in sessions:
            left = timestamp(row['start_time'])
            right = timestamp(row['end_time']) if row['end_time'] else max(left, progress.get(row['id'], left))
            left, right = max(left, low), min(right, high)
            if right <= left or (mode != 'all' and row['mode'] != mode):
                continue
            monitored.append((left, right))
            if row['mode'] == 'focus':
                focus.append((left, right))
        for row in events:
            at = timestamp(row['detected_at'])
            duration = float(row['duration_seconds'])
            if not math.isfinite(duration) or duration < 0 or row['mode'] not in ('focus', 'background'):
                raise ValueError('Invalid event data')
            left, right = max(low, at), min(high, at + duration)
            if right >= left:
                all_holds.append((left, right))
            if mode != 'all' and row['mode'] != mode:
                continue
            if right >= left:
                selected_holds.append((left, right))
            if low <= at < high:
                local = datetime.fromtimestamp(at, UTC).astimezone(tz)
                bucket = by_day[local.date().isoformat()]
                bucket['pickups'] += 1
                bucket[row['mode']] += 1
                recent.append({'id': row['id'], 'detected_at': local.isoformat(), 'mode': row['mode'],
                    'duration_seconds': max(0., min(duration, high-at)), 'roast_text': row['roast_text']})
    except (ValueError, TypeError, OverflowError, KeyError) as exc:
        raise DashboardError('The database contains invalid event or session data.') from exc
    summary.update(pickups=sum(row['pickups'] for row in days),
        phone_seconds=sum(b-a for a, b in union(selected_holds)),
        focus_seconds=sum(b-a for a, b in union(focus)),
        monitored_seconds=sum(b-a for a, b in union(monitored)),
        longest_streak_seconds=longest_free(monitored, all_holds))
    weeks = defaultdict(int)
    for row in days:
        day = date.fromisoformat(row['date'])
        monday = day - timedelta(days=day.weekday())
        weeks[monday.isoformat()] += row['pickups']
    return {'database_exists': exists, 'start': start.isoformat(), 'end': end.isoformat(),
        'mode': mode, 'timezone': zone, 'updated_at': now.isoformat(), 'summary': summary, 'days': days,
        'weeks': [{'start': day, 'pickups': count,
            'partial': date.fromisoformat(day) < start or date.fromisoformat(day)+timedelta(days=6) > end}
            for day, count in sorted(weeks.items())],
        'recent': list(reversed(recent[-20:]))}
