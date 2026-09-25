"""Local, user-approved assistant memories; no conversation transcript storage."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3


PROPOSAL_LIFETIME = timedelta(hours=24)
MAX_MEMORY_LENGTH = 500
SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:password|passphrase|api\s*key|access\s*token|secret|recovery\s*code|cookie)\b", re.I),
    re.compile(r"\b(?:credit\s*card|debit\s*card|payment\s*card|bank\s*account|account\s*number|routing\s*number|iban)\b", re.I),
    re.compile(r"\b(?:diagnos(?:is|ed)|diabetes|cancer|hiv|medication|prescription)\b", re.I),
    re.compile(r"\b(?:home\s*address|live\s+at\s+\d+\b)\b", re.I),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class MemoryPolicy:
    """Validate explicit memory text before any write."""

    @staticmethod
    def validate(text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Memory text must not be empty")
        text = text.strip()
        if len(text) > MAX_MEMORY_LENGTH:
            raise ValueError("Memory text must be at most 500 characters")
        if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
            raise ValueError("Sensitive details cannot be saved as memory")
        return text


class MemoryStore:
    """Short-lived proposals and approved facts shared by Vision and dashboard."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner TEXT NOT NULL DEFAULT 'local',
                    category TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    enabled INTEGER NOT NULL CHECK (enabled IN (0,1))
                );
                INSERT OR IGNORE INTO settings(id, enabled) VALUES (1, 1);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                    USING fts5(text, memory_id UNINDEXED, tokenize='porter unicode61');
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def propose(self, text: str) -> int:
        text = MemoryPolicy.validate(text)
        if not self.enabled():
            raise ValueError("Memory is disabled")
        now = datetime.now(timezone.utc)
        expires = (now + PROPOSAL_LIFETIME).isoformat(timespec="milliseconds")
        with self._connection() as db:
            cursor = db.execute(
                "INSERT INTO proposals(text, created_at, expires_at) VALUES (?, ?, ?)",
                (text, now.isoformat(timespec="milliseconds"), expires),
            )
            return int(cursor.lastrowid)

    def pending(self) -> list[dict]:
        with self._connection() as db:
            self._expire(db)
            rows = db.execute(
                "SELECT id, text, created_at FROM proposals ORDER BY id DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def approve(self, proposal_id: int) -> dict:
        self._validated_id(proposal_id)
        if not self.enabled():
            raise ValueError("Memory is disabled")
        with self._connection() as db:
            self._expire(db)
            row = db.execute("SELECT text FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                raise ValueError("Memory proposal was not found")
            text = MemoryPolicy.validate(row["text"])
            now = _now()
            category = "preference" if re.search(r"\b(prefer|like|favorite|favourite)\b", text, re.I) else "fact"
            cursor = db.execute(
                "INSERT INTO memories(category, text, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (category, text, now, now),
            )
            memory_id = int(cursor.lastrowid)
            db.execute("INSERT INTO memory_fts(text, memory_id) VALUES (?, ?)", (text, memory_id))
            db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,))
            return {"id": memory_id, "category": category, "text": text, "created_at": now, "updated_at": now}

    def reject(self, proposal_id: int) -> bool:
        self._validated_id(proposal_id)
        with self._connection() as db:
            return db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,)).rowcount > 0

    def discard_proposals(self) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM proposals")

    def active(self, query: str = "") -> list[dict]:
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("Invalid memory query")
        with self._connection() as db:
            if query.strip():
                rows = db.execute(
                    "SELECT id, category, text, created_at, updated_at FROM memories "
                    "WHERE status='active' AND text LIKE ? ORDER BY id DESC LIMIT 100",
                    ("%" + query.strip() + "%",),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT id, category, text, created_at, updated_at FROM memories "
                    "WHERE status='active' ORDER BY id DESC LIMIT 100"
                ).fetchall()
            return [dict(row) for row in rows]

    def edit(self, memory_id: int, text: str) -> dict:
        self._validated_id(memory_id)
        text = MemoryPolicy.validate(text)
        with self._connection() as db:
            row = db.execute("SELECT category, created_at FROM memories WHERE id=? AND status='active'", (memory_id,)).fetchone()
            if row is None:
                raise ValueError("Memory was not found")
            now = _now()
            db.execute("UPDATE memories SET text=?, updated_at=? WHERE id=?", (text, now, memory_id))
            db.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))
            db.execute("INSERT INTO memory_fts(text, memory_id) VALUES (?, ?)", (text, memory_id))
            return {"id": memory_id, "category": row["category"], "text": text,
                    "created_at": row["created_at"], "updated_at": now}

    def delete(self, memory_id: int) -> bool:
        self._validated_id(memory_id)
        with self._connection() as db:
            db.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))
            return db.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def enabled(self) -> bool:
        with self._connection() as db:
            return bool(db.execute("SELECT enabled FROM settings WHERE id=1").fetchone()[0])

    def set_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self._connection() as db:
            db.execute("UPDATE settings SET enabled=? WHERE id=1", (int(enabled),))

    def search(self, query: str, limit: int = 3) -> list[dict]:
        if not isinstance(query, str) or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Invalid memory search")
        words = [word for word in re.findall(r"\w+", query.lower()) if len(word) > 2][:8]
        if not words:
            return []
        if not self.enabled():
            return []
        match = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
        with self._connection() as db:
            rows = db.execute(
                "SELECT m.id, m.category, m.text, m.created_at, m.updated_at "
                "FROM memory_fts JOIN memories m ON m.id=memory_fts.memory_id "
                "WHERE memory_fts MATCH ? AND m.status='active' "
                "ORDER BY bm25(memory_fts) LIMIT ?",
                (match, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _expire(db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM proposals WHERE expires_at <= ?", (_now(),))

    @staticmethod
    def _validated_id(value: int) -> None:
        if type(value) is not int or value <= 0:
            raise ValueError("Memory ID must be a positive integer")
