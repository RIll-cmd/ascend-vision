"""Runtime consumer for typed dashboard messages."""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable

from integrations.chat_ipc import ChatIpcQueue


LOG = logging.getLogger(__name__)
ERROR_REPLY = "Vision could not process that message right now."


class ChatRuntimeBridge:
    """Dispatches each queued dashboard message through Vision's text handler."""

    def __init__(self, queue: ChatIpcQueue, handler: Callable[[str], str | None], *, poll_seconds: float = 0.25):
        if not callable(handler):
            raise TypeError("handler must be callable")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._queue = queue
        self._handler = handler
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ChatRuntimeBridge is single-use")
        self._thread = threading.Thread(target=self._run, name="vision-chat-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _publish(self, message_id: str, text: str, status: str) -> None:
        with self._lifecycle_lock:
            if not self._stop.is_set():
                self._queue.reply(message_id, text, status)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._queue.receive_inbound()
            except (sqlite3.Error, OSError):
                LOG.exception("Dashboard chat queue read failed; retrying")
                self._stop.wait(self._poll_seconds)
                continue
            if message is None:
                self._stop.wait(self._poll_seconds)
                continue
            try:
                result = self._handler(message["text"])
                if self._stop.is_set():
                    break
                if not isinstance(result, str) or not result.strip():
                    raise ValueError("Chat handler returned no answer")
                text = result.strip()
                self._publish(message["message_id"], text, "reply")
            except Exception:
                if self._stop.is_set():
                    break
                LOG.exception("Dashboard chat message handler failed")
                try:
                    self._publish(message["message_id"], ERROR_REPLY, "error")
                except Exception:
                    LOG.exception("Dashboard chat error reply failed")
