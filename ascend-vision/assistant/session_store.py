"""RAM-only, bounded conversation turns for channel-scoped phone sessions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import json
import threading
import time
from typing import TypeAlias


SessionKey: TypeAlias = tuple[str, str, str]
Turn: TypeAlias = tuple[str, str]
SESSION_IDLE_TTL_SECONDS = 60 * 60
MAX_SESSION_TURNS = 12
MAX_SESSION_CONTEXT_BYTES = 3_000


@dataclass
class _Session:
    last_activity: float
    turns: deque[Turn] = field(default_factory=lambda: deque(maxlen=MAX_SESSION_TURNS))


class SessionContextStore:
    """Keep only recent phone turns in process memory, scoped to one session."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._sessions: dict[SessionKey, _Session] = {}
        self._lock = threading.RLock()

    def get(self, key: SessionKey, now: float | None = None) -> tuple[tuple[Turn, ...], bool]:
        timestamp = self._clock() if now is None else now
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return (), False
            if timestamp - session.last_activity >= SESSION_IDLE_TTL_SECONDS:
                del self._sessions[key]
                return (), True
            return tuple(session.turns), False

    def record(self, key: SessionKey, user_text: str, assistant_text: str,
               now: float | None = None) -> None:
        timestamp = self._clock() if now is None else now
        with self._lock:
            session = self._sessions.get(key)
            if session is None or timestamp - session.last_activity >= SESSION_IDLE_TTL_SECONDS:
                session = _Session(last_activity=timestamp)
                self._sessions[key] = session
            session.turns.append((user_text, assistant_text))
            session.last_activity = timestamp
            while session.turns and len(self._encoded_turns(session.turns)) > MAX_SESSION_CONTEXT_BYTES:
                session.turns.popleft()
            if not session.turns:
                self._sessions.pop(key, None)

    def clear(self, key: SessionKey) -> None:
        with self._lock:
            self._sessions.pop(key, None)

    def expire(self, now: float | None = None) -> int:
        timestamp = self._clock() if now is None else now
        with self._lock:
            expired = [
                key for key, session in self._sessions.items()
                if timestamp - session.last_activity >= SESSION_IDLE_TTL_SECONDS
            ]
            for key in expired:
                del self._sessions[key]
            return len(expired)

    @staticmethod
    def _encoded_turns(turns) -> bytes:
        return json.dumps(
            [{"user": user, "assistant": answer} for user, answer in turns],
            ensure_ascii=False,
        ).encode("utf-8")
