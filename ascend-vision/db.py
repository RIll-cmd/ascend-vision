"""SQLite persistence, with one application owner and crash-safe session recovery."""
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 start_time TEXT NOT NULL,
 end_time TEXT,
 mode TEXT NOT NULL DEFAULT 'background' CHECK (mode IN ('background','focus'))
);
CREATE TABLE IF NOT EXISTS phone_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id INTEGER REFERENCES sessions(id),
 detected_at TEXT NOT NULL,
 duration_seconds REAL NOT NULL,
 confidence REAL,
 mode TEXT NOT NULL CHECK (mode IN ('background','focus')),
 roast_text TEXT,
 event_type TEXT NOT NULL DEFAULT 'phone_held',
 posture TEXT
);
CREATE INDEX IF NOT EXISTS idx_phone_events_detected_at ON phone_events(detected_at);
CREATE INDEX IF NOT EXISTS idx_phone_events_session_type ON phone_events(session_id, event_type);
CREATE TABLE IF NOT EXISTS app_state (
 id INTEGER PRIMARY KEY CHECK (id=1),
 session_id INTEGER REFERENCES sessions(id),
 last_seen TEXT NOT NULL
);
"""


def iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Database timestamps must be timezone-aware')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


class Database:
    def __init__(self, path: Path, timeout=5.):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = None
        self._lock = None
        self._locked = False
        try:
            self._lock = open(str(self.path) + '.lock', 'a+b')
            self._lock.seek(0, 2)
            if self._lock.tell() == 0:
                self._lock.write(b'0')
                self._lock.flush()
            self._lock.seek(0)
            try:
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(f'Database already in use by another Phone Watch process: {self.path}') from exc
            self._locked = True
            self.connection = sqlite3.connect(self.path, timeout=timeout, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute('PRAGMA foreign_keys=ON')
            version = self.connection.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError(f'Unsupported database schema version {version}')
            self.connection.execute('PRAGMA journal_mode=WAL')
            self.connection.execute('PRAGMA synchronous=FULL')
            self.connection.executescript('BEGIN;\n' + SCHEMA + '\nPRAGMA user_version=1;\nCOMMIT;')
            # Backward-compatible column migration for existing databases
            cols = {row['name'] for row in self.connection.execute('PRAGMA table_info(phone_events)')}
            if 'event_type' not in cols:
                self.connection.execute("ALTER TABLE phone_events ADD COLUMN event_type TEXT NOT NULL DEFAULT 'phone_held'")
            if 'posture' not in cols:
                self.connection.execute("ALTER TABLE phone_events ADD COLUMN posture TEXT")
        except BaseException:
            self.close()
            raise

    def recover(self):
        """Recover orphan sessions conservatively; never invent time since last run."""
        with self.connection:
            sessions = self.connection.execute('SELECT id,start_time FROM sessions WHERE end_time IS NULL').fetchall()
            for session in sessions:
                last_seen = datetime.fromisoformat(session['start_time'])
                heartbeat = self.connection.execute('SELECT last_seen FROM app_state WHERE session_id=?',
                                                    (session['id'],)).fetchone()
                if heartbeat is not None:
                    last_seen = max(last_seen, datetime.fromisoformat(heartbeat[0]))
                # A crash can occur between committing duration and its heartbeat.
                for event in self.connection.execute('SELECT detected_at,duration_seconds FROM phone_events WHERE session_id=?',
                                                      (session['id'],)):
                    last_seen = max(last_seen, datetime.fromisoformat(event[0]) + timedelta(seconds=event[1]))
                self.connection.execute('UPDATE sessions SET end_time=? WHERE id=?', (iso(last_seen), session['id']))
            self.connection.execute('DELETE FROM app_state')
        return len(sessions)

    def _insert_session(self, mode, at):
        cursor = self.connection.execute('INSERT INTO sessions(start_time,mode) VALUES(?,?)', (at, mode))
        sid = cursor.lastrowid
        self.connection.execute('INSERT OR REPLACE INTO app_state VALUES(1,?,?)', (sid, at))
        return sid

    def start_session(self, mode, at):
        timestamp = iso(at)
        with self.connection:
            if self.connection.execute('SELECT 1 FROM sessions WHERE end_time IS NULL').fetchone():
                raise RuntimeError('Recover or close the active session before starting another')
            return self._insert_session(mode, timestamp)

    def _end_session(self, sid, at):
        self.connection.execute("""UPDATE sessions SET end_time=MAX(start_time,?,COALESCE(
            (SELECT last_seen FROM app_state WHERE session_id=?),start_time))
            WHERE id=? AND end_time IS NULL""", (at, sid, sid))
        self.connection.execute('DELETE FROM app_state WHERE session_id=?', (sid,))

    def end_session(self, sid, at):
        with self.connection:
            self._end_session(sid, iso(at))

    def switch_session(self, sid, mode, at):
        with self.connection:
            self._end_session(sid, iso(at))
            boundary = self.connection.execute('SELECT end_time FROM sessions WHERE id=?', (sid,)).fetchone()
            if boundary is None:
                raise ValueError('Unknown session')
            return self._insert_session(mode, boundary[0])

    def heartbeat(self, sid, at):
        with self.connection:
            self.connection.execute('UPDATE app_state SET last_seen=MAX(last_seen,?) WHERE session_id=?',
                                    (iso(at), sid))

    def session_at(self, at):
        timestamp = iso(at)
        row = self.connection.execute("""SELECT id,mode FROM sessions
            WHERE start_time<=? AND (end_time IS NULL OR end_time>?)
            ORDER BY start_time DESC,id DESC LIMIT 1""", (timestamp, timestamp)).fetchone()
        if row is None:
            raise ValueError('No session covers the confirmation timestamp')
        return row['id'], row['mode']

    def start_event(self, sid, detected_at, confidence, event_type='phone_held', posture=None):
        if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError('Confidence must be finite and between 0 and 1')
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError('Event type must be a nonempty string')
        with self.connection:
            session = self.connection.execute('SELECT mode FROM sessions WHERE id=?', (sid,)).fetchone()
            if session is None:
                raise ValueError('Unknown event session')
            cursor = self.connection.execute("""INSERT INTO phone_events
                (session_id,detected_at,duration_seconds,confidence,mode,event_type,posture)
                VALUES(?,?,0,?,?,?,?)""", (sid, iso(detected_at), confidence, session['mode'], event_type, posture))
            return cursor.lastrowid

    def update_event(self, event_id, duration):
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Event duration must be finite and nonnegative')
        with self.connection:
            cursor = self.connection.execute('UPDATE phone_events SET duration_seconds=MAX(duration_seconds,?) WHERE id=?',
                                             (duration, event_id))
            if cursor.rowcount != 1:
                raise ValueError('Unknown event')

    def feedback_context(self, event_id):
        row = self.connection.execute('''SELECT e.session_id,e.mode,e.detected_at,s.start_time,e.event_type,e.posture
            FROM phone_events e JOIN sessions s ON s.id=e.session_id WHERE e.id=?''', (event_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown feedback event')
        detected = datetime.fromisoformat(row['detected_at'])
        local = detected.astimezone()
        # Resolve each local midnight separately so DST days need not be 24 hours.
        midnight = datetime.combine(local.date(), datetime.min.time()).astimezone(timezone.utc)
        tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time()).astimezone(timezone.utc)
        today = self.connection.execute('''SELECT COUNT(*) FROM phone_events
            WHERE detected_at>=? AND detected_at<? AND id<=?''', (iso(midnight), iso(tomorrow), event_id)).fetchone()[0]
        session_count = self.connection.execute('SELECT COUNT(*) FROM phone_events WHERE session_id=? AND id<=?',
                                                (row['session_id'], event_id)).fetchone()[0]
        metadata = {'pickups_today': today, 'pickups_this_session': session_count,
                    'session_duration_minutes': round(max(0., (detected - datetime.fromisoformat(row['start_time'])).total_seconds()) / 60, 2),
                    'time_of_day': local.strftime('%H:%M')}
        if row['event_type'] and row['event_type'] != 'phone_held':
            metadata['event_type'] = row['event_type']
        if row['posture'] and row['posture'] != 'none':
            metadata['posture'] = row['posture']
        return row['session_id'], row['mode'], metadata

    def save_roast(self, event_id, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError('Roast must be nonempty text of at most 500 characters')
        with self.connection:
            row = self.connection.execute('SELECT mode,roast_text FROM phone_events WHERE id=?', (event_id,)).fetchone()
            if row is None or row['mode'] != 'focus':
                raise ValueError('Roasts can only be attached to focus events')
            if row['roast_text'] is not None and row['roast_text'] != text:
                raise ValueError('Event already has a different roast')
            self.connection.execute('UPDATE phone_events SET roast_text=? WHERE id=?', (text, event_id))

    def session_event_counts(self, sid: int) -> dict[str, int]:
        with self.connection:
            rows = self.connection.execute(
                'SELECT event_type, COUNT(*) as count FROM phone_events WHERE session_id=? GROUP BY event_type',
                (sid,)).fetchall()
            return {row['event_type']: row['count'] for row in rows}

    def session_events_summary(self, sid: int) -> dict[str, dict]:
        with self.connection:
            rows = self.connection.execute('''
                SELECT event_type, COUNT(*) as count, SUM(duration_seconds) as total_duration
                FROM phone_events WHERE session_id=? GROUP BY event_type''', (sid,)).fetchall()
            return {
                row['event_type']: {
                    'count': row['count'],
                    'total_duration_seconds': round(row['total_duration'] or 0.0, 3)
                }
                for row in rows
            }

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
        finally:
            if self._lock is not None:
                try:
                    if self._locked:
                        self._lock.seek(0)
                        if sys.platform == 'win32':
                            import msvcrt
                            msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                finally:
                    self._lock.close()
                    self._lock = None
                    self._locked = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
