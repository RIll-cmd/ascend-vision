"""Slump detector: a local daily record of work signals, compared to the owner's own baseline.

A signal fires when its last 3 complete days move far from the median of the 14 days
before them. Two fired signals make a slump, which the coach answers with a gentle ladder.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from statistics import mean, median

METRICS = ('commits', 'room_sessions', 'active_minutes', 'late_minutes', 'focus_minutes',
           'distraction_minutes')
DROPS = ('commits', 'room_sessions', 'active_minutes', 'focus_minutes')
KEEP_DAYS = 90
RECENT_DAYS = 3
BASELINE_DAYS = 14
MIN_BASELINE_DAYS = 7


@dataclass(frozen=True)
class Assessment:
    fired: tuple[str, ...]
    slump: bool
    learning: bool
    signals: dict[str, tuple[float, float]] = field(default_factory=dict)


def _fires(metric: str, recent: float, baseline: float) -> bool:
    if metric in DROPS:
        return baseline > 0 and recent < 0.5 * baseline
    if metric == 'late_minutes':
        return recent > 30 and recent > baseline + 30
    if metric == 'distraction_minutes':
        return recent > 30 and recent > 2 * baseline
    return False


def assess(days: dict[str, dict[date, float]], today: date) -> Assessment:
    recent_window = [today - timedelta(days=offset) for offset in range(1, RECENT_DAYS + 1)]
    baseline_window = [today - timedelta(days=offset)
                       for offset in range(RECENT_DAYS + 1, RECENT_DAYS + BASELINE_DAYS + 1)]
    signals: dict[str, tuple[float, float]] = {}
    has_baseline = False
    for metric in METRICS:
        values = days.get(metric, {})
        baseline_values = [values[day] for day in baseline_window if day in values]
        if len(baseline_values) < MIN_BASELINE_DAYS:
            continue
        has_baseline = True
        recent_values = [values[day] for day in recent_window if day in values]
        if recent_values:  # a missing day is "no data" (camera off), never zero
            signals[metric] = (mean(recent_values), median(baseline_values))
    fired = tuple(metric for metric, (recent, baseline) in signals.items()
                  if _fires(metric, recent, baseline))
    return Assessment(fired=fired, slump=len(fired) >= 2, learning=not has_baseline, signals=signals)


@dataclass
class Ladder:
    step: int = 0
    last_day: date | None = None
    slump_days: int = 0
    calm_days: int = 0
    paused_until: date | None = None
    accepted: bool = False


def _ladder_json(ladder: Ladder) -> str:
    data = asdict(ladder)
    for key in ('last_day', 'paused_until'):
        data[key] = data[key].isoformat() if data[key] else None
    return json.dumps(data)


def _ladder_from(text: str) -> Ladder:
    try:
        data = json.loads(text)
        for key in ('last_day', 'paused_until'):
            data[key] = date.fromisoformat(data[key]) if data.get(key) else None
        return Ladder(**data)
    except (TypeError, ValueError):
        return Ladder()


class SlumpStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                'CREATE TABLE IF NOT EXISTS day_metric (day TEXT NOT NULL, metric TEXT NOT NULL, '
                '  value REAL NOT NULL, PRIMARY KEY (day, metric));'
                'CREATE TABLE IF NOT EXISTS ladder (id INTEGER PRIMARY KEY CHECK (id = 1), json TEXT NOT NULL);')

    def _connect(self):
        return _Connection(sqlite3.connect(self.path, timeout=5))

    def add(self, day: date, metric: str, amount: float) -> None:
        with self._connect() as db:
            db.execute('INSERT INTO day_metric (day, metric, value) VALUES (?, ?, ?) '
                       'ON CONFLICT (day, metric) DO UPDATE SET value = value + excluded.value',
                       (day.isoformat(), metric, amount))

    def put(self, day: date, metric: str, value: float) -> None:
        with self._connect() as db:
            db.execute('INSERT INTO day_metric (day, metric, value) VALUES (?, ?, ?) '
                       'ON CONFLICT (day, metric) DO UPDATE SET value = excluded.value',
                       (day.isoformat(), metric, value))

    def days(self, since: date) -> dict[str, dict[date, float]]:
        with self._connect() as db:
            rows = db.execute('SELECT day, metric, value FROM day_metric WHERE day >= ?',
                              (since.isoformat(),)).fetchall()
        result: dict[str, dict[date, float]] = {}
        for day, metric, value in rows:
            result.setdefault(metric, {})[date.fromisoformat(day)] = value
        return result

    def prune(self, today: date) -> None:
        with self._connect() as db:
            db.execute('DELETE FROM day_metric WHERE day < ?',
                       ((today - timedelta(days=KEEP_DAYS)).isoformat(),))

    def ladder(self) -> Ladder:
        with self._connect() as db:
            row = db.execute('SELECT json FROM ladder WHERE id = 1').fetchone()
        return _ladder_from(row[0]) if row else Ladder()

    def save_ladder(self, ladder: Ladder) -> None:
        with self._connect() as db:
            db.execute('INSERT INTO ladder (id, json) VALUES (1, ?) '
                       'ON CONFLICT (id) DO UPDATE SET json = excluded.json', (_ladder_json(ladder),))


class _Connection:
    """Commits on success and always closes, so Windows can delete the file afterwards."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, kind, error, trace) -> None:
        with closing(self.connection):
            if kind is None:
                self.connection.commit()
            else:
                self.connection.rollback()
