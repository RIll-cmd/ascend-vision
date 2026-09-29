"""Minimal durable action-attempt journal; never stores page or form contents."""

from pathlib import Path
from contextlib import contextmanager
import sqlite3
import threading
import time
import hashlib


class DuplicateAction(ValueError):
    """An action ID has already been journaled and must not be replayed."""


class JournalCapacityExceeded(RuntimeError):
    """The bounded action journal requires operator cleanup before more writes."""


MAX_JOURNAL_ROWS = 10_000
JOURNAL_RETENTION_SECONDS = 30 * 24 * 60 * 60


class BrowserActionJournal:
    def __init__(self, path: str | Path, *, clock=time.time, max_rows=MAX_JOURNAL_ROWS,
                 retention_seconds=JOURNAL_RETENTION_SECONDS):
        if type(max_rows) is not int or max_rows < 1 or type(retention_seconds) is not int or retention_seconds < 1:
            raise ValueError('journal retention limits must be positive integers')
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._max_rows = max_rows
        self._retention_seconds = retention_seconds
        self._lock = threading.RLock()
        with self._connection() as connection:
            connection.execute('''
                CREATE TABLE IF NOT EXISTS browser_actions (
                    action_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('dispatching','observed','failed','unknown')),
                    started_at REAL NOT NULL,
                    finished_at REAL
                )
            ''')
            connection.execute('''
                CREATE TABLE IF NOT EXISTS browser_remote_tasks (
                    task_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    laptop_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    browser_session_id TEXT NOT NULL,
                    broker_boot_id TEXT NOT NULL,
                    lease_hash TEXT NOT NULL,
                    fence INTEGER NOT NULL CHECK(fence > 0),
                    scope_id TEXT NOT NULL,
                    scope_version INTEGER NOT NULL CHECK(scope_version > 0),
                    state TEXT NOT NULL CHECK(state IN ('accepted','started','unknown','terminal','reconciled')),
                    accepted_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            ''')
            # Any action left in-flight at broker startup may have reached the site.
            # It is never replayed automatically.
            connection.execute(
                "UPDATE browser_actions SET state='unknown', finished_at=? WHERE state='dispatching'",
                (self._clock(),),
            )
            connection.execute(
                "UPDATE browser_remote_tasks SET state='unknown', updated_at=? WHERE state='started'",
                (self._clock(),),
            )
            connection.execute(
                'DELETE FROM browser_actions WHERE finished_at IS NOT NULL AND finished_at < ?',
                (self._clock() - self._retention_seconds,),
            )
            connection.execute(
                "DELETE FROM browser_remote_tasks WHERE state IN ('terminal','reconciled') AND updated_at < ?",
                (self._clock() - self._retention_seconds,),
            )

    def _connect(self):
        connection = sqlite3.connect(self._path, timeout=5, isolation_level='IMMEDIATE')
        connection.row_factory = sqlite3.Row
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

    def begin(self, task_id: str, owner_id: str, action_id: str, action: str,
              origin: str, proposal_digest: str) -> None:
        values = (task_id, owner_id, action_id, action, origin, proposal_digest)
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in values):
            raise ValueError('journal metadata is invalid')
        with self._lock, self._connection() as connection:
            count = connection.execute('SELECT COUNT(*) FROM browser_actions').fetchone()[0]
            if count >= self._max_rows:
                raise JournalCapacityExceeded('The browser action journal has reached its safe retention limit.')
            try:
                connection.execute('''
                    INSERT INTO browser_actions
                        (task_id, owner_id, action_id, action, origin, proposal_digest, state, started_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'dispatching', ?)
                ''', (*values, self._clock()))
            except sqlite3.IntegrityError as exc:
                raise DuplicateAction('This browser action was already journaled.') from exc

    def finish(self, action_id: str, state: str) -> None:
        if state not in {'observed', 'failed', 'unknown'}:
            raise ValueError('unsupported journal outcome')
        with self._lock, self._connection() as connection:
            cursor = connection.execute('''
                UPDATE browser_actions SET state=?, finished_at=?
                WHERE action_id=? AND state='dispatching'
            ''', (state, self._clock(), action_id))
            if cursor.rowcount != 1:
                raise ValueError('journal action is missing or already finalized')

    def get(self, action_id: str) -> dict | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                'SELECT action_id, task_id, owner_id, action, origin, proposal_digest, state, started_at, finished_at '
                'FROM browser_actions WHERE action_id=?', (action_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def record_remote_task(self, binding, *, state: str = 'accepted') -> dict:
        """Persist only remote task identity/fencing metadata, never its prompt or result."""
        if state not in {'accepted', 'started', 'terminal'}:
            raise ValueError('remote task journal state is invalid')
        values = (
            binding.task_id, binding.owner_id, binding.laptop_id, binding.channel,
            binding.browser_session_id, binding.broker_boot_id,
            hashlib.sha256(binding.lease_id.encode('utf-8')).hexdigest(), binding.fence,
            binding.scope_id, binding.scope_version,
        )
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in values[:7] + values[8:9]):
            raise ValueError('remote task journal identity is invalid')
        if type(binding.fence) is not int or binding.fence < 1 or type(binding.scope_version) is not int or binding.scope_version < 1:
            raise ValueError('remote task journal fence is invalid')
        with self._lock, self._connection() as connection:
            prior = connection.execute(
                'SELECT * FROM browser_remote_tasks WHERE task_id=?', (binding.task_id,),
            ).fetchone()
            if prior:
                comparable = ('task_id', 'owner_id', 'laptop_id', 'channel', 'browser_session_id',
                              'broker_boot_id', 'lease_hash', 'fence', 'scope_id', 'scope_version')
                if any(prior[key] != value for key, value in zip(comparable, values)):
                    raise DuplicateAction('Remote task identity was already journaled with a different fence.')
                if prior['state'] in {'unknown', 'terminal', 'reconciled'} and state != prior['state']:
                    return dict(prior)
                connection.execute('UPDATE browser_remote_tasks SET state=?, updated_at=? WHERE task_id=?',
                                   (state, self._clock(), binding.task_id))
            else:
                connection.execute(
                    "DELETE FROM browser_remote_tasks WHERE state IN ('terminal','reconciled') AND updated_at < ?",
                    (self._clock() - self._retention_seconds,),
                )
                count = connection.execute('SELECT COUNT(*) FROM browser_remote_tasks').fetchone()[0]
                if count >= self._max_rows:
                    raise JournalCapacityExceeded(
                        'The remote browser task journal has reached its safe retention limit.'
                    )
                now = self._clock()
                connection.execute('''
                    INSERT INTO browser_remote_tasks
                    (task_id,owner_id,laptop_id,channel,browser_session_id,broker_boot_id,lease_hash,
                     fence,scope_id,scope_version,state,accepted_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (*values, state, now, now))
            return dict(connection.execute(
                'SELECT * FROM browser_remote_tasks WHERE task_id=?', (binding.task_id,),
            ).fetchone())

    def remote_reconciliations(self) -> list[dict]:
        """Return bounded, content-free records the next worker boot must reconcile."""
        with self._lock, self._connection() as connection:
            rows = connection.execute('''
                SELECT task_id,owner_id,laptop_id,channel,browser_session_id,broker_boot_id,
                       lease_hash,fence,scope_id,scope_version,state,accepted_at,updated_at
                FROM browser_remote_tasks WHERE state IN ('accepted','unknown')
                ORDER BY accepted_at LIMIT 256
            ''').fetchall()
            result = []
            for row in rows:
                item = dict(row)
                attempts = connection.execute('''
                    SELECT action_id, action, proposal_digest, state FROM browser_actions
                    WHERE task_id=? ORDER BY started_at LIMIT 64
                ''', (row['task_id'],)).fetchall()
                item['attempts'] = [
                    {
                        'action_id': attempt['action_id'],
                        'action': attempt['action'],
                        'proposal_digest': attempt['proposal_digest'],
                        'outcome': ({'dispatching': 'unknown', 'unknown': 'unknown',
                                     'observed': 'attempted', 'failed': 'not_attempted'}
                                    [attempt['state']]),
                    }
                    for attempt in attempts
                ]
                result.append(item)
        return result

    def mark_remote_reconciled(self, task_id: str) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute('''
                UPDATE browser_remote_tasks SET state='reconciled', updated_at=?
                WHERE task_id=? AND state IN ('accepted','unknown')
            ''', (self._clock(), task_id))
            return cursor.rowcount == 1

    def close(self):
        """Connections are per-operation; retained for a simple service lifecycle API."""
