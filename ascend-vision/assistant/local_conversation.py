"""Single laptop chat session and event fan-out for local Vision interfaces."""
from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from uuid import uuid4


GENERIC_CHAT_ERROR = "Vision could not process that message right now."


class LocalConversation:
    """Route local text through one AssistantService session and publish events."""

    def __init__(self, queue, assistant_service, context_builder: Callable[[str], object],
                 *, session_key: tuple[str, str, str] | Callable[[], tuple[str, str, str]],
                 max_words: int = 25):
        if not callable(getattr(queue, "publish_event", None)):
            raise TypeError("queue must publish conversation events")
        if not callable(getattr(assistant_service, "respond", None)):
            raise TypeError("assistant_service must provide respond")
        if not callable(context_builder):
            raise TypeError("context_builder must be callable")
        if not callable(session_key) and (not isinstance(session_key, tuple) or len(session_key) != 3):
            raise ValueError("session_key must be a three-part key or provider")
        if type(max_words) is not int or max_words <= 0:
            raise ValueError("max_words must be a positive integer")
        self._queue = queue
        self._assistant_service = assistant_service
        self._context_builder = context_builder
        self._session_key = session_key
        self._max_words = max_words
        self._runtime_session_id = uuid4().hex
        self._state_lock = RLock()
        self._turn_lock = RLock()
        self._started = False
        self._accepting = False

    @property
    def runtime_session_id(self) -> str:
        return self._runtime_session_id

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                raise RuntimeError("local conversation is already started")
            self._queue.begin_session(self._runtime_session_id)
            self._started = True
            self._accepting = True

    def stop_accepting(self) -> None:
        """Close the producer gate before the runtime worker is stopped."""
        # This short lifecycle operation must not wait for an LLM response. The
        # SQLite gate makes concurrent HTTP producers fail atomically.
        with self._state_lock:
            if self._started and self._accepting:
                self._queue.stop_accepting_session(self._runtime_session_id)
            self._accepting = False

    def close(self) -> None:
        with self._turn_lock, self._state_lock:
            if not self._started:
                return
            if self._accepting:
                self._queue.stop_accepting_session(self._runtime_session_id)
            self._accepting = False
            self._queue.end_session(self._runtime_session_id)
            self._started = False

    def enqueue(self, text: str, *, source: str) -> str:
        if source not in {"voice", "fairy", "dashboard"}:
            raise ValueError("source must be a local conversation channel")
        with self._state_lock:
            if not self._started or not self._accepting:
                raise RuntimeError("local conversation is not accepting messages")
            return self._queue.enqueue(
                text, source=source, session_id=self._runtime_session_id,
            )

    def handle_message(self, message: dict) -> str:
        """Process one already-claimed local turn; the bridge serializes callers."""
        if not isinstance(message, dict):
            raise ValueError("message must be a mapping")
        text = message.get("text")
        source = message.get("source")
        turn_id = message.get("message_id")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("message text must not be empty")
        if source not in {"voice", "fairy", "dashboard"}:
            source = "dashboard"
        if not isinstance(turn_id, str) or not turn_id:
            turn_id = uuid4().hex

        with self._turn_lock:
            with self._state_lock:
                if not self._started or not self._accepting:
                    raise RuntimeError("local conversation is not active")
            self._queue.publish_event(
                self._runtime_session_id, turn_id=turn_id, source=source,
                kind="user", text=text.strip(), status="received",
            )
            self._queue.publish_event(
                self._runtime_session_id, turn_id=turn_id, source=source,
                kind="status", text="", status="thinking",
            )
            try:
                reply = self._assistant_service.respond(
                    text.strip(), self._context_builder(text.strip()),
                    max_words=self._max_words,
                    session_key=(self._session_key() if callable(self._session_key)
                                 else self._session_key),
                )
                answer = reply.text.strip()
                if not answer:
                    raise ValueError("assistant returned an empty reply")
            except Exception:
                self._queue.publish_event(
                    self._runtime_session_id, turn_id=turn_id, source=source,
                    kind="status", text=GENERIC_CHAT_ERROR, status="error",
                )
                raise
            self._queue.publish_event(
                self._runtime_session_id, turn_id=turn_id, source=source,
                kind="assistant", text=answer, status="reply",
                reply_source=getattr(reply, "source", None),
                provider=getattr(reply, "provider", None),
                model=getattr(reply, "model", None),
                failure_reason=getattr(reply, "failure_reason", None),
            )
            status_reader = getattr(self._assistant_service, "ai_status", None)
            status_publisher = getattr(self._queue, "publish_ai_status", None)
            if callable(status_reader) and callable(status_publisher):
                try:
                    status_publisher(status_reader())
                except Exception:
                    pass
            return answer

    def events_after(self, cursor: int = 0, limit: int = 100) -> list[dict]:
        with self._state_lock:
            if not self._started:
                return []
        return self._queue.events_after(self._runtime_session_id, cursor, limit)
