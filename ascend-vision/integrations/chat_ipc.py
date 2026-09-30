"""Process-safe local inbox/outbox transport for dashboard chat."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4


DEFAULT_PATH = Path("data/chat_ipc.db")
ALLOWED_REPLY_STATUSES = frozenset({
    "queued",
    "reply",
    "error",
    "confirmation_required",
})
TERMINAL_REPLY_STATUSES = ALLOWED_REPLY_STATUSES - {"queued"}
_INITIALIZE_LOCK = threading.Lock()
_INITIALIZE_RETRY_SECONDS = 5.0
_TRANSIENT_LIFETIME = timedelta(hours=24)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ChatIpcQueue:
    """SQLite-backed single-claim inbox and ordered reply outbox."""

    def __init__(
        self,
        path: str | Path = DEFAULT_PATH,
        *,
        max_text_length: int = 4_000,
        retention_limit: int = 1_000,
    ):
        if isinstance(max_text_length, bool) or not isinstance(max_text_length, int) or max_text_length <= 0:
            raise ValueError("max_text_length must be a positive integer")
        if isinstance(retention_limit, bool) or not isinstance(retention_limit, int) or retention_limit <= 0:
            raise ValueError("retention_limit must be a positive integer")
        self.path = Path(path)
        self.max_text_length = max_text_length
        self.retention_limit = retention_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(self, text, source: str = "dashboard", *, session_id: str | None = None) -> str:
        text = self._validated_text(text)
        source = self._validated_identifier(source, "source")
        if session_id is not None:
            session_id = self._validated_identifier(session_id, "session_id")
        message_id = uuid4().hex
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            runtime = connection.execute(
                "SELECT session_id, accepting FROM chat_runtime WHERE id = 1"
            ).fetchone()
            active_session_id = runtime["session_id"] if runtime else None
            if session_id is not None and (
                    active_session_id != session_id or not runtime["accepting"]):
                raise RuntimeError("chat runtime session is not accepting messages")
            session_id = active_session_id
            connection.execute(
                """INSERT INTO chat_inbox
                   (message_id, source, text, created_at, session_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (message_id, source, text, _utc_now(), session_id),
            )
            self._cleanup(connection)
        return message_id

    def receive_inbound(self) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._cleanup(connection)
            runtime = connection.execute(
                "SELECT session_id FROM chat_runtime WHERE id = 1"
            ).fetchone()
            session_id = runtime["session_id"] if runtime else None
            if session_id is None:
                row = connection.execute(
                    """SELECT sequence, message_id, source, text, created_at
                       FROM chat_inbox WHERE claimed_at IS NULL AND session_id IS NULL
                       ORDER BY sequence LIMIT 1"""
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT sequence, message_id, source, text, created_at
                       FROM chat_inbox WHERE claimed_at IS NULL AND session_id = ?
                       ORDER BY sequence LIMIT 1""", (session_id,)
                ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE chat_inbox SET claimed_at = ? WHERE sequence = ? AND claimed_at IS NULL",
                (_utc_now(), row["sequence"]),
            )
        return {
            "message_id": row["message_id"],
            "text": row["text"],
            "source": row["source"],
            "created_at": row["created_at"],
            "session_id": session_id,
        }

    def begin_session(self, session_id: str) -> None:
        """Start a fresh laptop transcript and discard leftovers from a prior run."""
        session_id = self._validated_identifier(session_id, "session_id")
        with self._connection() as connection:
            connection.execute("DELETE FROM chat_inbox")
            connection.execute("DELETE FROM chat_outbox")
            connection.execute("DELETE FROM chat_events")
            connection.execute(
                "UPDATE chat_delivery SET max_sequence = 0 WHERE id = 1"
            )
            connection.execute("UPDATE chat_ai_status SET state='unknown', provider=NULL, model=NULL, last_success_at=NULL, last_failure=NULL WHERE id=1")
            connection.execute(
                "INSERT INTO chat_runtime(id, session_id, accepting) VALUES (1, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET session_id = excluded.session_id, accepting = 1",
                (session_id,),
            )
        with self._connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def end_session(self, session_id: str) -> None:
        """Erase the laptop conversation and IPC bodies when its runtime ends."""
        session_id = self._validated_identifier(session_id, "session_id")
        with self._connection() as connection:
            connection.execute("DELETE FROM chat_events WHERE session_id = ?", (session_id,))
            connection.execute(
                "DELETE FROM chat_outbox WHERE message_id IN "
                "(SELECT message_id FROM chat_inbox WHERE session_id = ?)", (session_id,),
            )
            connection.execute(
                "DELETE FROM chat_inbox WHERE session_id = ?", (session_id,)
            )
            connection.execute(
                "UPDATE chat_runtime SET session_id = NULL, accepting = 0 "
                "WHERE id = 1 AND session_id = ?",
                (session_id,),
            )
            connection.execute("UPDATE chat_ai_status SET state='unknown', provider=NULL, model=NULL, last_success_at=NULL, last_failure=NULL WHERE id=1")
        with self._connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def active_session_id(self) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id FROM chat_runtime WHERE id = 1"
            ).fetchone()
        return row["session_id"] if row else None

    def publish_ai_status(self, status: dict) -> None:
        allowed = {"unknown", "not-configured", "configured-untested", "requesting", "available", "request-failed"}
        if not isinstance(status, dict) or status.get("state") not in allowed or type(status.get("configured")) is not bool:
            raise ValueError("invalid AI status")
        values = [status.get(key) for key in ("provider", "model", "lastSuccessAt", "lastFailure")]
        for value in values:
            if value is not None and (not isinstance(value, str) or len(value) > 80):
                raise ValueError("invalid AI status metadata")
        with self._connection() as connection:
            connection.execute(
                "UPDATE chat_ai_status SET configured=?, state=?, provider=?, model=?, last_success_at=?, last_failure=? WHERE id=1",
                (int(status["configured"]), status["state"], *values),
            )

    def ai_status(self) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM chat_ai_status WHERE id=1").fetchone()
        return {"configured": bool(row["configured"]), "state": row["state"],
                "provider": row["provider"], "model": row["model"],
                "lastSuccessAt": row["last_success_at"], "lastFailure": row["last_failure"]}

    def stop_accepting_session(self, session_id: str) -> None:
        """Atomically close producer admission before stopping the consumer."""
        session_id = self._validated_identifier(session_id, "session_id")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE chat_runtime SET accepting = 0 "
                "WHERE id = 1 AND session_id = ? AND accepting = 1",
                (session_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("chat runtime session is no longer accepting messages")

    def publish_event(self, session_id: str, *, turn_id: str, source: str,
                      kind: str, text: str = "", status: str = "",
                      reply_source: str | None = None, provider: str | None = None,
                      model: str | None = None, failure_reason: str | None = None) -> int:
        session_id = self._validated_identifier(session_id, "session_id")
        turn_id = self._validated_identifier(turn_id, "turn_id")
        source = self._validated_identifier(source, "source")
        if kind not in {"user", "assistant", "status"}:
            raise ValueError("invalid chat event kind")
        if not isinstance(text, str) or len(text) > self.max_text_length:
            raise ValueError("event text is invalid or too long")
        if not isinstance(status, str) or len(status) > 64:
            raise ValueError("event status is invalid")
        if reply_source not in {None, "model", "offline", "tool"}:
            raise ValueError("invalid reply source")
        for field, value in (("provider", provider), ("model", model), ("failure_reason", failure_reason)):
            if value is None:
                continue
            if not isinstance(value, str) or len(value) > 64:
                raise ValueError(f"invalid {field}")
            pattern = r"[A-Za-z0-9][A-Za-z0-9._/-]{0,63}" if field == "model" else r"[A-Za-z0-9_-]+"
            if re.fullmatch(pattern, value) is None:
                raise ValueError(f"invalid {field}")
        if kind != "assistant" and any(value is not None for value in (reply_source, provider, model, failure_reason)):
            raise ValueError("reply provenance is only valid on assistant events")
        with self._connection() as connection:
            active = connection.execute(
                "SELECT session_id FROM chat_runtime WHERE id = 1"
            ).fetchone()
            if active is None or active["session_id"] != session_id:
                raise ValueError("chat session is no longer active")
            cursor = connection.execute(
                """INSERT INTO chat_events
                   (session_id, turn_id, source, kind, text, status, created_at,
                    reply_source, provider, model, failure_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, turn_id, source, kind, text, status, _utc_now(),
                 reply_source, provider, model, failure_reason),
            )
            connection.execute(
                """DELETE FROM chat_events WHERE session_id = ? AND sequence NOT IN
                   (SELECT sequence FROM chat_events WHERE session_id = ?
                    ORDER BY sequence DESC LIMIT ?)""",
                (session_id, session_id, self.retention_limit * 4),
            )
            return int(cursor.lastrowid)

    def events_after(self, session_id: str, cursor: int = 0,
                     limit: int = 100) -> list[dict]:
        session_id = self._validated_identifier(session_id, "session_id")
        if type(cursor) is not int or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT sequence, turn_id, source, kind, text, status, created_at,
                          reply_source, provider, model, failure_reason
                   FROM chat_events WHERE session_id = ? AND sequence > ?
                   ORDER BY sequence LIMIT ?""",
                (session_id, cursor, limit),
            ).fetchall()
        return [{
            "cursor": int(row["sequence"]),
            "turn_id": row["turn_id"],
            "source": row["source"],
            "kind": row["kind"],
            "text": row["text"],
            "status": row["status"],
            "created_at": row["created_at"],
            "reply_source": row["reply_source"],
            "provider": row["provider"],
            "model": row["model"],
            "failure_reason": row["failure_reason"],
        } for row in rows]

    def reply(self, message_id, text, status) -> int:
        message_id = self._validated_identifier(message_id, "message_id")
        text = self._validated_text(text)
        if not isinstance(status, str) or status not in ALLOWED_REPLY_STATUSES:
            raise ValueError("invalid reply status")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO chat_outbox (message_id, text, status, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (message_id, text, status, _utc_now()),
            )
            if status in TERMINAL_REPLY_STATUSES:
                connection.execute(
                    "UPDATE chat_inbox SET completed_at = ? WHERE message_id = ?",
                    (_utc_now(), message_id),
                )
            self._cleanup(connection)
            return int(cursor.lastrowid)

    def replies_after(self, cursor: int = 0, limit: int = 50) -> list[dict]:
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connection() as connection:
            self._cleanup(connection)
            rows = connection.execute(
                """
                SELECT sequence, message_id, text, status, created_at
                FROM chat_outbox
                WHERE sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (cursor, limit),
            ).fetchall()
            if rows:
                connection.execute(
                    "UPDATE chat_delivery SET max_sequence=MAX(max_sequence, ?) WHERE id=1",
                    (int(rows[-1]["sequence"]),),
                )
        return [
            {
                "cursor": row["sequence"],
                "message_id": row["message_id"],
                "text": row["text"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def acknowledge_through(self, cursor: int) -> None:
        """Remove rendered reply text and the matching completed inbox text."""
        if type(cursor) is not int or cursor < 0:
            raise ValueError("cursor must be a non-negative integer")
        with self._connection() as connection:
            delivered = connection.execute(
                "SELECT max_sequence FROM chat_delivery WHERE id=1"
            ).fetchone()[0]
            if cursor > delivered:
                raise ValueError("cursor exceeds the highest delivered reply")
            connection.execute("DELETE FROM chat_outbox WHERE sequence <= ?", (cursor,))
            connection.execute(
                """DELETE FROM chat_inbox
                   WHERE completed_at IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM chat_outbox
                         WHERE chat_outbox.message_id = chat_inbox.message_id
                     )"""
            )
            self._cleanup(connection)

    def _initialize(self) -> None:
        deadline = time.monotonic() + _INITIALIZE_RETRY_SECONDS
        while True:
            try:
                with _INITIALIZE_LOCK:
                    with self._connection() as connection:
                        connection.execute("PRAGMA journal_mode=WAL")
                        connection.executescript(
                            """
                            CREATE TABLE IF NOT EXISTS chat_inbox (
                                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                                message_id TEXT NOT NULL UNIQUE,
                                source TEXT NOT NULL,
                                text TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                claimed_at TEXT,
                                completed_at TEXT,
                                session_id TEXT
                            );
                            CREATE INDEX IF NOT EXISTS chat_inbox_pending
                                ON chat_inbox (claimed_at, sequence);
                            CREATE TABLE IF NOT EXISTS chat_outbox (
                                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                                message_id TEXT NOT NULL,
                                text TEXT NOT NULL,
                                status TEXT NOT NULL,
                                created_at TEXT NOT NULL
                            );
                            CREATE TABLE IF NOT EXISTS chat_delivery (
                                id INTEGER PRIMARY KEY CHECK (id=1),
                                max_sequence INTEGER NOT NULL DEFAULT 0
                            );
                            INSERT OR IGNORE INTO chat_delivery(id, max_sequence) VALUES (1, 0);
                            CREATE TABLE IF NOT EXISTS chat_runtime (
                                id INTEGER PRIMARY KEY CHECK (id = 1),
                                session_id TEXT,
                                accepting INTEGER NOT NULL DEFAULT 0 CHECK (accepting IN (0, 1))
                            );
                            CREATE TABLE IF NOT EXISTS chat_ai_status (
                                id INTEGER PRIMARY KEY CHECK (id=1),
                                configured INTEGER NOT NULL DEFAULT 0,
                                state TEXT NOT NULL DEFAULT 'unknown',
                                provider TEXT, model TEXT, last_success_at TEXT, last_failure TEXT
                            );
                            INSERT OR IGNORE INTO chat_ai_status(id) VALUES (1);
                            CREATE TABLE IF NOT EXISTS chat_events (
                                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                                session_id TEXT NOT NULL,
                                turn_id TEXT NOT NULL,
                                source TEXT NOT NULL,
                                kind TEXT NOT NULL,
                                text TEXT NOT NULL,
                                status TEXT NOT NULL,
                                created_at TEXT NOT NULL,
                                reply_source TEXT,
                                provider TEXT,
                                model TEXT,
                                failure_reason TEXT
                            );
                            CREATE INDEX IF NOT EXISTS chat_events_session_sequence
                                ON chat_events (session_id, sequence);
                            """
                        )
                        inbox_columns = {
                            row["name"] for row in connection.execute("PRAGMA table_info(chat_inbox)")
                        }
                        if "completed_at" not in inbox_columns:
                            connection.execute("ALTER TABLE chat_inbox ADD COLUMN completed_at TEXT")
                        if "session_id" not in inbox_columns:
                            connection.execute("ALTER TABLE chat_inbox ADD COLUMN session_id TEXT")
                        runtime_columns = {
                            row["name"] for row in connection.execute("PRAGMA table_info(chat_runtime)")
                        }
                        if "accepting" not in runtime_columns:
                            connection.execute(
                                "ALTER TABLE chat_runtime ADD COLUMN accepting INTEGER NOT NULL DEFAULT 0"
                            )
                        event_columns = {
                            row["name"] for row in connection.execute("PRAGMA table_info(chat_events)")
                        }
                        for column in ("reply_source", "provider", "model", "failure_reason"):
                            if column not in event_columns:
                                connection.execute(f"ALTER TABLE chat_events ADD COLUMN {column} TEXT")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA secure_delete=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _validated_text(self, text) -> str:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        text = text.strip()
        if not text:
            raise ValueError("text must not be empty")
        if len(text) > self.max_text_length:
            raise ValueError(f"text must not exceed {self.max_text_length} characters")
        return text

    @staticmethod
    def _validated_identifier(value, name: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError(f"{name} must be a non-empty string of at most 128 characters")
        return value.strip()

    def _cleanup(self, connection: sqlite3.Connection) -> None:
        cutoff = (datetime.now(timezone.utc) - _TRANSIENT_LIFETIME).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        connection.execute("DELETE FROM chat_inbox WHERE created_at < ?", (cutoff,))
        connection.execute("DELETE FROM chat_outbox WHERE created_at < ?", (cutoff,))
        connection.execute(
            """
            DELETE FROM chat_inbox
            WHERE sequence IN (
                SELECT sequence FROM chat_inbox
                WHERE completed_at IS NOT NULL
                ORDER BY sequence DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.retention_limit,),
        )
        connection.execute(
            """
            DELETE FROM chat_outbox
            WHERE sequence IN (
                SELECT sequence FROM chat_outbox
                ORDER BY sequence DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.retention_limit,),
        )
