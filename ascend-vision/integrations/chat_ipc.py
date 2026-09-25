"""Process-safe local inbox/outbox transport for dashboard chat."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

    def enqueue(self, text, source: str = "dashboard") -> str:
        text = self._validated_text(text)
        source = self._validated_identifier(source, "source")
        message_id = uuid4().hex
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO chat_inbox (message_id, source, text, created_at) VALUES (?, ?, ?, ?)",
                (message_id, source, text, _utc_now()),
            )
            self._cleanup(connection)
        return message_id

    def receive_inbound(self) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._cleanup(connection)
            row = connection.execute(
                """
                SELECT sequence, message_id, source, text, created_at
                FROM chat_inbox
                WHERE claimed_at IS NULL
                ORDER BY sequence
                LIMIT 1
                """
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
        }

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
                                completed_at TEXT
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
                            """
                        )
                        inbox_columns = {
                            row["name"] for row in connection.execute("PRAGMA table_info(chat_inbox)")
                        }
                        if "completed_at" not in inbox_columns:
                            connection.execute("ALTER TABLE chat_inbox ADD COLUMN completed_at TEXT")
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
