"""Privacy-limited local accounting for browser automation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager


CHANNELS = {'local_dashboard', 'local_voice', 'pwa', 'discord'}
TERMINAL_STATES = {'completed', 'partial', 'failed', 'cancelled', 'expired', 'unknown', 'interrupted'}
OWNER_VERDICTS = {'worked', 'needed_correction', 'did_not_work', 'not_reviewed'}
CALL_OUTCOMES = {'success', 'invalid_response', 'timeout', 'provider_error', 'cancelled'}
MAX_RUNS = 10_000
MAX_RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_CHILD_CALLS = 100
MAX_TEXT = 128


def _enum(value: str, choices: set[str], name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f'{name} is invalid')
    return value


def _optional_count(value, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= 10_000_000:
        raise ValueError(f'{name} is invalid')
    return value


@dataclass(frozen=True)
class RunStart:
    channel: str
    cohort: str
    routine_id: str = 'ad_hoc'
    routine_version: int | None = None
    routine_digest: str | None = None
    site_family: str = 'other'
    provider: str = 'unknown'
    model: str = 'unknown'
    started_at: float = 0.0

    def __post_init__(self):
        _enum(self.channel, CHANNELS, 'channel')
        _enum(self.cohort, {'live_public', 'pwa', 'discord', 'signed_in', 'fixture'}, 'cohort')
        for name in ('routine_id', 'site_family', 'provider', 'model'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
                raise ValueError(f'{name} is invalid')
        if self.routine_version is not None and (type(self.routine_version) is not int or self.routine_version < 1):
            raise ValueError('routine_version is invalid')
        if self.routine_digest is not None and (len(self.routine_digest) != 64
                or any(c not in '0123456789abcdef' for c in self.routine_digest)):
            raise ValueError('routine_digest is invalid')
        if self.started_at and (type(self.started_at) not in (int, float)
                                or not math.isfinite(self.started_at) or self.started_at <= 0):
            raise ValueError('started_at is invalid')


@dataclass(frozen=True)
class RunOutcome:
    state: str
    reason_code: str
    verifier_verdict: str = 'unknown'
    duration_ms: int | None = None
    queue_ms: int | None = None
    review_ms: int | None = None
    handoff: str = 'none'
    unknown_effect: bool = False

    def __post_init__(self):
        _enum(self.state, TERMINAL_STATES, 'state')
        _enum(self.verifier_verdict, {'passed', 'failed', 'inconclusive', 'unknown'}, 'verifier_verdict')
        _enum(self.handoff, {'none', 'planned', 'unplanned'}, 'handoff')
        if not isinstance(self.reason_code, str) or not self.reason_code or len(self.reason_code) > MAX_TEXT:
            raise ValueError('reason_code is invalid')
        for name in ('duration_ms', 'queue_ms', 'review_ms'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 0 <= value <= 86_400_000):
                raise ValueError(f'{name} is invalid')
        if type(self.unknown_effect) is not bool:
            raise ValueError('unknown_effect must be boolean')


@dataclass(frozen=True)
class ProviderCallUsage:
    attempt_id: str
    provider: str
    model_requested: str
    model_actual: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: int | None = None
    outcome: str = 'provider_error'
    price_table_version: str | None = None
    estimated_cost_usd_micros: int | None = None
    reserved_cost_usd_micros: int | None = None

    def __post_init__(self):
        if not isinstance(self.attempt_id, str) or not 16 <= len(self.attempt_id) <= 128:
            raise ValueError('attempt_id is invalid')
        for name in ('provider', 'model_requested'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
                raise ValueError(f'{name} is invalid')
        if self.model_actual is not None and (not isinstance(self.model_actual, str) or not self.model_actual
                                              or len(self.model_actual) > MAX_TEXT):
            raise ValueError('model_actual is invalid')
        for name in ('input_tokens', 'output_tokens', 'cached_tokens', 'thinking_tokens'):
            _optional_count(getattr(self, name), name)
        if self.latency_ms is not None and (type(self.latency_ms) is not int or not 0 <= self.latency_ms <= 3_600_000):
            raise ValueError('latency_ms is invalid')
        _enum(self.outcome, CALL_OUTCOMES, 'outcome')
        for name in ('estimated_cost_usd_micros', 'reserved_cost_usd_micros'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f'{name} is invalid')
        if self.price_table_version is not None and len(self.price_table_version) > MAX_TEXT:
            raise ValueError('price table version is invalid')


class MetricsStore:
    """SQLite store containing only bounded operational counters and enums."""

    def __init__(self, path: str | Path, *, retention_days: int = 30, max_runs: int = MAX_RUNS,
                 enabled: bool = False, clock=time.time):
        if type(retention_days) is not int or not 1 <= retention_days <= 30:
            raise ValueError('retention_days must be between 1 and 30')
        if type(max_runs) is not int or not 1 <= max_runs <= MAX_RUNS:
            raise ValueError('max_runs must be between 1 and 10000')
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        self.path = Path(path).resolve()
        self.retention_seconds = retention_days * 86400
        self.max_runs = max_runs
        self._clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize(enabled)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=3, isolation_level='IMMEDIATE')
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=3000')
        connection.execute('PRAGMA journal_mode=DELETE')
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self, enabled):
        with self._lock, self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, started_at REAL NOT NULL, channel TEXT NOT NULL,
                    cohort TEXT NOT NULL, routine_id TEXT NOT NULL, routine_version INTEGER,
                    routine_digest TEXT, site_family TEXT NOT NULL, provider TEXT NOT NULL,
                    model TEXT NOT NULL, state TEXT, reason_code TEXT, verifier_verdict TEXT,
                    owner_verdict TEXT NOT NULL DEFAULT 'not_reviewed', duration_ms INTEGER,
                    queue_ms INTEGER, review_ms INTEGER, handoff TEXT, unknown_effect INTEGER,
                    owner_reviewed_at REAL
                );
                CREATE TABLE IF NOT EXISTS provider_calls (
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    attempt_id TEXT NOT NULL, provider TEXT NOT NULL, model_requested TEXT NOT NULL,
                    model_actual TEXT, input_tokens INTEGER, output_tokens INTEGER,
                    cached_tokens INTEGER, thinking_tokens INTEGER, latency_ms INTEGER,
                    outcome TEXT NOT NULL, price_table_version TEXT,
                    estimated_cost_usd_micros INTEGER, reserved_cost_usd_micros INTEGER,
                    PRIMARY KEY(run_id, attempt_id)
                );
                CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs(started_at);
                CREATE INDEX IF NOT EXISTS runs_cohort_idx ON runs(cohort);
            ''')
            db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('enabled', ?)", ('true' if enabled else 'false',))
            db.execute("UPDATE runs SET state='interrupted',reason_code='broker_restart' WHERE state IS NULL")
            db.commit()

    @property
    def enabled(self) -> bool:
        with self._lock, self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
            return bool(row and row['value'] == 'true')

    def set_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        with self._lock, self._connection() as db:
            db.execute("INSERT INTO settings(key,value) VALUES('enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       ('true' if enabled else 'false',))
            db.commit()

    def begin(self, record: RunStart) -> str | None:
        if not isinstance(record, RunStart):
            raise TypeError('record must be RunStart')
        if not self.enabled:
            return None
        run_id = secrets.token_urlsafe(24)
        started_at = record.started_at or self._clock()
        try:
            with self._lock, self._connection() as db:
                self._cleanup(db)
                db.execute('''INSERT INTO runs(id,started_at,channel,cohort,routine_id,routine_version,
                    routine_digest,site_family,provider,model) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (run_id, started_at, record.channel, record.cohort, record.routine_id,
                     record.routine_version, record.routine_digest, record.site_family,
                     record.provider, record.model))
                self._cap(db)
                db.commit()
            return run_id
        except (OSError, sqlite3.Error):
            return None

    def record_call(self, run_id: str | None, record: ProviderCallUsage) -> bool:
        if run_id is None or not self.enabled:
            return False
        if not isinstance(record, ProviderCallUsage):
            raise TypeError('record must be ProviderCallUsage')
        try:
            with self._lock, self._connection() as db:
                row = db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone()
                if row is None:
                    return False
                count = db.execute('SELECT count(*) FROM provider_calls WHERE run_id=?', (run_id,)).fetchone()[0]
                if count >= MAX_CHILD_CALLS:
                    return False
                db.execute('''INSERT OR IGNORE INTO provider_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (run_id, record.attempt_id, record.provider, record.model_requested,
                     record.model_actual, record.input_tokens, record.output_tokens,
                     record.cached_tokens, record.thinking_tokens, record.latency_ms,
                     record.outcome, record.price_table_version, record.estimated_cost_usd_micros,
                     record.reserved_cost_usd_micros))
                db.commit()
            return True
        except (OSError, sqlite3.Error):
            return False

    def finish(self, run_id: str | None, record: RunOutcome) -> bool:
        if run_id is None or not self.enabled:
            return False
        if not isinstance(record, RunOutcome):
            raise TypeError('record must be RunOutcome')
        try:
            with self._lock, self._connection() as db:
                cursor = db.execute('''UPDATE runs SET state=?,reason_code=?,verifier_verdict=?,duration_ms=?,
                    queue_ms=?,review_ms=?,handoff=?,unknown_effect=? WHERE id=? AND state IS NULL''',
                    (record.state, record.reason_code, record.verifier_verdict, record.duration_ms,
                     record.queue_ms, record.review_ms, record.handoff, int(record.unknown_effect), run_id))
                db.commit()
                return cursor.rowcount == 1
        except (OSError, sqlite3.Error):
            return False

    def feedback(self, run_id: str | None, verdict: str) -> bool:
        _enum(verdict, OWNER_VERDICTS - {'not_reviewed'}, 'verdict')
        if run_id is None or not self.enabled:
            return False
        try:
            with self._lock, self._connection() as db:
                cursor = db.execute('UPDATE runs SET owner_verdict=?,owner_reviewed_at=? WHERE id=? AND state IS NOT NULL',
                                    (verdict, self._clock(), run_id))
                db.commit()
                return cursor.rowcount == 1
        except (OSError, sqlite3.Error):
            return False

    def summary(self, cohort: str) -> dict:
        _enum(cohort, {'all', 'live_public', 'pwa', 'discord', 'signed_in', 'fixture'}, 'cohort')
        with self._lock, self._connection() as db:
            where, args = ('', ()) if cohort == 'all' else ('WHERE cohort=?', (cohort,))
            row = db.execute(f'''SELECT count(*) eligible,
                sum(CASE WHEN owner_verdict != 'not_reviewed' THEN 1 ELSE 0 END) reviewed,
                sum(CASE WHEN owner_verdict='worked' THEN 1 ELSE 0 END) worked,
                sum(CASE WHEN owner_verdict='needed_correction' THEN 1 ELSE 0 END) corrections,
                sum(CASE WHEN handoff='planned' THEN 1 ELSE 0 END) planned_handoffs,
                sum(CASE WHEN handoff='unplanned' THEN 1 ELSE 0 END) unplanned_handoffs,
                sum(CASE WHEN unknown_effect=1 THEN 1 ELSE 0 END) unknown_effects,
                sum(CASE WHEN state='completed' THEN 1 ELSE 0 END) completed,
                sum(CASE WHEN state='partial' THEN 1 ELSE 0 END) partial,
                sum(CASE WHEN state='failed' THEN 1 ELSE 0 END) failed,
                sum(CASE WHEN state='cancelled' THEN 1 ELSE 0 END) cancelled,
                sum(CASE WHEN state='expired' THEN 1 ELSE 0 END) expired,
                sum(CASE WHEN state='unknown' THEN 1 ELSE 0 END) unknown,
                sum(CASE WHEN state='interrupted' THEN 1 ELSE 0 END) interrupted,
                avg(duration_ms) mean_execution_ms,avg(queue_ms) mean_queue_ms,
                avg(review_ms) mean_review_ms,avg(duration_ms + queue_ms) mean_total_duration_ms
                FROM runs {where}''', args).fetchone()
            count_fields = {'eligible', 'reviewed', 'worked', 'corrections', 'planned_handoffs',
                            'unplanned_handoffs', 'unknown_effects', 'completed', 'partial', 'failed',
                            'cancelled', 'expired', 'unknown', 'interrupted'}
            result = {key: (row[key] or 0) if key in count_fields else row[key] for key in row.keys()}
            reviewed = result['reviewed']
            result['owner_reviewed_success_rate'] = (result['worked'] / reviewed) if reviewed else None
            result['owner_review_coverage'] = (reviewed / result['eligible']) if result['eligible'] else None
            result['p95_duration_ms'] = self._percentile(db, where, args, 'duration_ms', .95)
            result['accounting'] = db.execute(f'''SELECT count(*) calls,
                sum(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN 1 ELSE 0 END) unknown_token_calls,
                sum(CASE WHEN estimated_cost_usd_micros IS NULL THEN 1 ELSE 0 END) unknown_cost_calls,
                CASE WHEN count(*)=0 OR sum(CASE WHEN input_tokens IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(input_tokens) END input_tokens,
                CASE WHEN count(*)=0 OR sum(CASE WHEN output_tokens IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(output_tokens) END output_tokens,
                CASE WHEN count(*)=0 OR sum(CASE WHEN estimated_cost_usd_micros IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(estimated_cost_usd_micros) END estimated_cost_usd_micros,
                CASE WHEN count(*)=0 OR sum(CASE WHEN reserved_cost_usd_micros IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(reserved_cost_usd_micros) END reserved_cost_usd_micros,
                avg(latency_ms) mean_model_latency_ms
                FROM provider_calls c JOIN runs r ON r.id=c.run_id {where.replace('cohort', 'r.cohort')}''', args).fetchone()
            accounting = result['accounting']
            accounting_counts = {'calls', 'unknown_token_calls', 'unknown_cost_calls'}
            result['accounting'] = {key: (accounting[key] or 0) if key in accounting_counts else accounting[key]
                                    for key in accounting.keys()}
            result['accounting']['p95_model_latency_ms'] = self._percentile_calls(db, cohort)
            return result

    @staticmethod
    def _percentile(db, where, args, field, percentile):
        rows = db.execute(f'SELECT {field} FROM runs {where} AND {field} IS NOT NULL ORDER BY {field}'
                          if where else f'SELECT {field} FROM runs WHERE {field} IS NOT NULL ORDER BY {field}', args).fetchall()
        if not rows:
            return None
        return rows[max(0, int((len(rows) * percentile + .999999)) - 1)][field]

    @staticmethod
    def _percentile_calls(db, cohort):
        where, args = ('', ()) if cohort == 'all' else ('WHERE r.cohort=?', (cohort,))
        rows = db.execute(f'''SELECT c.latency_ms FROM provider_calls c
            JOIN runs r ON r.id=c.run_id {where} AND c.latency_ms IS NOT NULL
            ORDER BY c.latency_ms''' if where else '''SELECT c.latency_ms FROM provider_calls c
            JOIN runs r ON r.id=c.run_id WHERE c.latency_ms IS NOT NULL ORDER BY c.latency_ms''', args).fetchall()
        if not rows:
            return None
        return rows[max(0, int((len(rows) * .95 + .999999)) - 1)]['latency_ms']

    def export(self, *, cursor: int = 0, limit: int = 50) -> dict:
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('metrics export cursor or page size is invalid')
        with self._lock, self._connection() as db:
            self._cleanup(db)
            total = db.execute('SELECT count(*) FROM runs').fetchone()[0]
            runs = db.execute('''SELECT r.started_at,r.channel,r.cohort,r.routine_id,r.routine_version,r.routine_digest,
                r.site_family,r.provider,r.model,r.state,r.reason_code,r.verifier_verdict,r.owner_verdict,
                r.duration_ms,r.queue_ms,r.review_ms,r.handoff,r.unknown_effect,
                count(c.attempt_id) provider_calls,
                sum(CASE WHEN c.input_tokens IS NULL OR c.output_tokens IS NULL THEN 1 ELSE 0 END) unknown_token_calls,
                sum(CASE WHEN c.estimated_cost_usd_micros IS NULL THEN 1 ELSE 0 END) unknown_cost_calls,
                CASE WHEN count(c.attempt_id)=0 OR sum(CASE WHEN c.input_tokens IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(c.input_tokens) END input_tokens,
                CASE WHEN count(c.attempt_id)=0 OR sum(CASE WHEN c.output_tokens IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(c.output_tokens) END output_tokens,
                CASE WHEN count(c.attempt_id)=0 OR sum(CASE WHEN c.estimated_cost_usd_micros IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(c.estimated_cost_usd_micros) END estimated_cost_usd_micros,
                CASE WHEN count(c.attempt_id)=0 OR sum(CASE WHEN c.reserved_cost_usd_micros IS NULL THEN 1 ELSE 0 END)>0
                     THEN NULL ELSE sum(c.reserved_cost_usd_micros) END reserved_cost_usd_micros
                FROM runs r LEFT JOIN provider_calls c ON c.run_id=r.id
                GROUP BY r.id ORDER BY r.started_at,r.id LIMIT ? OFFSET ?''', (limit, cursor)).fetchall()
            db.commit()
        next_cursor = cursor + len(runs)
        return {'schema_version': 1, 'cursor': cursor, 'next_cursor': next_cursor if next_cursor < total else None,
                'total': total, 'runs': [dict(item) for item in runs]}

    def clear(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._initialize(False)
                return
            db = self._connect()
            try:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                db.execute('DELETE FROM provider_calls')
                db.execute('DELETE FROM runs')
                db.commit()
            finally:
                db.close()
            # SQLite is closed before removing the owned database and any sidecars.
            for target in (self.path, Path(str(self.path) + '-wal'), Path(str(self.path) + '-shm'),
                           Path(str(self.path) + '-journal')):
                if target.exists():
                    target.unlink()
            self._initialize(False)

    def _cleanup(self, db):
        cutoff = self._clock() - self.retention_seconds
        db.execute('DELETE FROM runs WHERE started_at < ?', (cutoff,))

    def maintenance(self) -> None:
        try:
            with self._lock, self._connection() as db:
                self._cleanup(db)
                self._cap(db)
                db.commit()
        except (OSError, sqlite3.Error):
            pass

    def _cap(self, db):
        db.execute('DELETE FROM runs WHERE id IN (SELECT id FROM runs ORDER BY started_at DESC LIMIT -1 OFFSET ?)',
                   (self.max_runs,))


def default_metrics_path() -> Path | None:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    return Path(local) / 'AscendVision' / 'browser' / 'metrics.sqlite3'
