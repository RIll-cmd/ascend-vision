"""Runtime consumer for typed dashboard messages."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from integrations.chat_ipc import ChatIpcQueue


LOG = logging.getLogger(__name__)
DEFAULT_REPLY = "Vision received your message."
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
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ChatRuntimeBridge is single-use")
        self._thread = threading.Thread(target=self._run, name="vision-chat-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        while not self._stop.is_set():
            message = self._queue.receive_inbound()
            if message is None:
                self._stop.wait(self._poll_seconds)
                continue
            try:
                result = self._handler(message["text"])
                text = result.strip() if isinstance(result, str) and result.strip() else DEFAULT_REPLY
                self._queue.reply(message["message_id"], text, "reply")
            except Exception:
                LOG.exception("Dashboard chat message handler failed")
                try:
                    self._queue.reply(message["message_id"], ERROR_REPLY, "error")
                except Exception:
                    LOG.exception("Dashboard chat error reply failed")
