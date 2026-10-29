"""Slump detector: a local daily record of work signals, compared to the owner's own baseline.

A signal fires when its last 3 complete days move far from the median of the 14 days
before them. Two fired signals make a slump, which the coach answers with a gentle ladder.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import sqlite3
from statistics import mean, median
import threading
import urllib.request

LOG = logging.getLogger(__name__)

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


def sample_minute(store: SlumpStore, sample: str, now: datetime) -> None:
    """One minute of desktop activity. Idle minutes still write 0, so a day Vision ran is real data."""
    active = 1 if sample == 'input_active' else 0
    store.add(now.date(), 'active_minutes', active)
    store.add(now.date(), 'late_minutes', active if now.hour < 5 else 0)


def _local_date(stamp: str) -> date | None:
    try:
        moment = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return None
    return (moment.astimezone() if moment.tzinfo else moment).date()


def _fill_quiet_days(counts: dict[date, float], today: date) -> dict[date, float]:
    """Zero for each day from the first recorded day to yesterday that has no record."""
    if not counts:
        return {}
    day, filled = min(counts), dict(counts)
    while day < today:
        filled.setdefault(day, 0)
        day += timedelta(days=1)
    return filled


def room_session_days(payload: dict, *, today: date) -> dict[date, int]:
    sessions = payload.get('sessions') if isinstance(payload, dict) else None
    counts: dict[date, int] = {}
    for session in sessions if isinstance(sessions, list) else []:
        day = _local_date(session.get('createdAt')) if isinstance(session, dict) else None
        if day is not None:
            counts[day] = counts.get(day, 0) + 1
    return _fill_quiet_days(counts, today)


def read_room_sessions(base_url: str) -> dict | None:
    """Hub's session list, or None when Hub or the room is unreachable."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{base_url.rstrip('/')}/api/room/sessions", timeout=3.0) as response:
            payload = json.loads(response.read().decode('utf-8'))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _read_only(path) -> sqlite3.Connection | None:
    path = Path(path)
    if not path.is_file():
        return None
    return sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=5)


def focus_days(path) -> dict[date, float]:
    """Minutes of finished focus sessions, keyed by the local date they started."""
    connection = _read_only(path)
    if connection is None:
        return {}
    with closing(connection):
        try:
            rows = connection.execute(
                "SELECT start_time, end_time FROM sessions WHERE mode = 'focus' AND end_time IS NOT NULL").fetchall()
        except sqlite3.Error:
            return {}
    minutes: dict[date, float] = {}
    for start, end in rows:
        try:
            began = datetime.fromisoformat(start)
            length = (datetime.fromisoformat(end) - began).total_seconds() / 60
        except (TypeError, ValueError):
            continue
        day = (began.astimezone() if began.tzinfo else began).date()
        minutes[day] = minutes.get(day, 0) + max(0.0, length)
    return minutes


def distraction_days(path, *, today: date) -> dict[date, float]:
    connection = _read_only(path)
    if connection is None:
        return {}
    with closing(connection):
        try:
            rows = connection.execute('SELECT day, SUM(seconds) FROM distraction_time GROUP BY day').fetchall()
        except sqlite3.Error:
            return {}
    minutes: dict[date, float] = {}
    for day, seconds in rows:
        try:
            minutes[date.fromisoformat(day)] = (seconds or 0) / 60
        except (TypeError, ValueError):
            continue
    return _fill_quiet_days(minutes, today)


def backfill(store: SlumpStore, *, today: date, calendar: dict[date, int] | None = None,
             room_sessions: dict[date, int] | None = None, focus_db=None, distraction_db=None) -> None:
    """Put each source's days. A source that is None (unreachable) or missing leaves stored days alone."""
    oldest = today - timedelta(days=KEEP_DAYS)
    sources = (('commits', calendar), ('room_sessions', room_sessions),
               ('focus_minutes', focus_days(focus_db) if focus_db else None),
               ('distraction_minutes', distraction_days(distraction_db, today=today) if distraction_db else None))
    for metric, days in sources:
        for day, value in (days or {}).items():
            if oldest <= day <= today:
                store.put(day, metric, value)
    store.prune(today)


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


NUDGE_TEXT = 'Ascend: a small plan is waiting for you on the PC.'
FOCUS_STEP = 'one twenty-five minute focus block'
CHANGES = {'commits': 'fewer commits', 'room_sessions': 'fewer Agent Room sessions',
           'active_minutes': 'shorter days at the PC', 'late_minutes': 'later nights',
           'focus_minutes': 'less focus time', 'distraction_minutes': 'more time on distracting sites'}
NAMES = {'commits': 'commits', 'room_sessions': 'Agent Room sessions', 'active_minutes': 'minutes at the PC',
         'late_minutes': 'minutes up after midnight', 'focus_minutes': 'focus minutes',
         'distraction_minutes': 'minutes on distracting sites'}
ACCEPTED = {'one small commit on anything': 'Good. One small commit, whenever you are ready.',
            FOCUS_STEP: "Good. I'll check in after twenty-five minutes.",
            'one small task for the Agent Room': 'Good. Queue one small task in the Agent Room when you are ready.'}
SPEAK_FROM, SPEAK_UNTIL, MIN_ACTIVE = 8, 21, 30
_YES = re.compile(r'(?:(?:hey|ok|okay) )?(?:vision )?(?:yes|yeah|yep|sure|okay|ok)(?: please| sure)?')
_NOT_NOW = re.compile(r'(?:(?:hey|ok|okay) )?(?:vision )?(?:not now|no|nope|no thanks|not today)')


def _words(text: str) -> str:
    return ' '.join(re.sub(r"[^a-z' ]", ' ', text.lower()).split())


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _number(value: float) -> str:
    return f'{value:.0f}' if value >= 10 or value == round(value) else f'{value:.1f}'


def choose_step(fired: tuple[str, ...]) -> str:
    if 'commits' in fired:
        return 'one small commit on anything'
    if 'focus_minutes' in fired or 'active_minutes' in fired:
        return FOCUS_STEP
    if 'room_sessions' in fired:
        return 'one small task for the Agent Room'
    return FOCUS_STEP


class SlumpCoach:
    """Samples activity, backfills signals hourly, and walks the check-in ladder at most once a day."""

    def __init__(self, store: SlumpStore, *, announce, sample=None, backfill_sources=None, notify=None,
                 clock=datetime.now, offer_window: float = 120.0):
        self.store = store
        self.announce = announce
        self.sample = sample
        self.backfill_sources = backfill_sources
        self.notify = notify
        self.clock = clock
        self.offer_window = offer_window
        self._last_sample = self._last_backfill = None
        self.backfill_thread: threading.Thread | None = None
        self._offer: tuple[str, datetime] | None = None  # ('check_in' | 'plan', when)
        self._step = FOCUS_STEP
        self._focus_due: datetime | None = None

    def tick(self, owner_available: bool) -> None:
        now = self.clock()
        if self.sample and (self._last_sample is None or (now - self._last_sample).total_seconds() >= 60):
            self._last_sample = now
            sample_minute(self.store, self.sample(), now)
        if self.backfill_sources and (self._last_backfill is None
                                      or (now - self._last_backfill).total_seconds() >= 3600):
            self._last_backfill = now
            # The sources read the network; the caller's frame loop must never wait on them.
            self.backfill_thread = threading.Thread(target=self._backfill, args=(now.date(),),
                                                    name='slump-backfill', daemon=True)
            self.backfill_thread.start()
        if self._focus_due and now >= self._focus_due:
            self._focus_due = None
            self.announce("That's twenty-five minutes. Nice work.")
        if owner_available:
            self._climb(now)

    def _backfill(self, today: date) -> None:
        try:
            backfill(self.store, today=today, **self.backfill_sources())
        except Exception as exc:
            LOG.debug('Slump backfill skipped (%s)', type(exc).__name__)

    def _assessment(self, today: date) -> Assessment:
        return assess(self.store.days(today - timedelta(days=RECENT_DAYS + BASELINE_DAYS)), today)

    def _climb(self, now: datetime) -> None:
        today = now.date()
        ladder = self.store.ladder()
        if ladder.last_day == today or not SPEAK_FROM <= now.hour < SPEAK_UNTIL:
            return
        if self.store.days(today).get('active_minutes', {}).get(today, 0) < MIN_ACTIVE:
            return
        result = self._assessment(today)
        ladder.last_day = today
        if not result.slump:
            ladder.calm_days += 1
            if ladder.calm_days >= 2:
                ladder = Ladder(last_day=today, paused_until=ladder.paused_until)
            self.store.save_ladder(ladder)
            return
        ladder.calm_days = 0
        if ladder.paused_until and today < ladder.paused_until:
            self.store.save_ladder(ladder)
            return
        ladder.slump_days += 1
        self._step = choose_step(result.fired)
        if ladder.step == 0:
            ladder.step = 1
            self._offer = ('check_in', now)
            self.announce(f'Lately I have noticed {_join([CHANGES[m] for m in result.fired])} than usual. '
                          'Want a small plan for today?')
        elif ladder.step == 1:
            ladder.step = 2
            self._offer = ('plan', now)
            self.announce(f"Here's one small step for today, if you'd like it: {self._step}. Want to try it?")
        elif ladder.step == 2 and ladder.slump_days >= 3 and not ladder.accepted:
            ladder.step = 3
            if self.notify:
                self.notify(NUDGE_TEXT)
        self.store.save_ladder(ladder)

    def intercept(self, text: str) -> str | None:
        words = _words(text)
        if re.search(r'\bhow am i doing\b', words):
            return self._summary()
        now = self.clock()
        offer = self._offer
        if not offer or (now - offer[1]).total_seconds() > self.offer_window:
            return None
        if _NOT_NOW.fullmatch(words):
            self._offer = None
            ladder = self.store.ladder()
            ladder.paused_until = now.date() + timedelta(days=3)
            self.store.save_ladder(ladder)
            return "Okay. I'll leave it for a few days."
        if not _YES.fullmatch(words):
            return None
        ladder = self.store.ladder()
        if offer[0] == 'check_in':
            ladder.step = max(ladder.step, 2)
            self.store.save_ladder(ladder)
            self._offer = ('plan', now)
            return f"Here's one small step for today: {self._step}. Sound good?"
        self._offer = None
        ladder.accepted = True
        self.store.save_ladder(ladder)
        if self._step == FOCUS_STEP:
            self._focus_due = now + timedelta(minutes=25)
        return ACCEPTED[self._step]

    def _summary(self) -> str:
        result = self._assessment(self.clock().date())
        if result.learning:
            return "I'm still learning your normal week."
        if not result.signals:
            return "I don't have enough recent days to compare yet."
        parts = [f'{NAMES[metric]} {_number(recent)} a day, usually {_number(baseline)}'
                 for metric, (recent, baseline) in result.signals.items()]
        verdict = (f"You've had {_join([CHANGES[m] for m in result.fired])} than usual."
                   if result.fired else "You're close to your usual.")
        return f"Over the last few days: {'; '.join(parts)}. {verdict}"
