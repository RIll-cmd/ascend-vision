"""Spaces out notices Vision says on its own, so several never play back to back.

At startup the weather briefing, GitHub nudge, food reminder, agent room and AI key
notices can all become due at once. They are kept, in order, and spoken one at a time
with a gap. Answers to the owner and conversations (away mode, calibration) are not paced.
"""
from __future__ import annotations

from collections import deque
import logging
import threading
import time
from typing import Callable

LOG = logging.getLogger(__name__)


class AnnouncementPacer:
    def __init__(self, say: Callable[[str], None], gap_seconds: float = 45.0, clock: Callable[[], float] = time.monotonic):
        self._say, self._gap, self._clock = say, gap_seconds, clock
        self._queue: deque[tuple[str, Callable[[], None] | None]] = deque()
        self._lock = threading.Lock()
        self._last = float('-inf')

    def __call__(self, text: str, on_said: Callable[[], None] | None = None) -> None:
        """Queue a notice; on_said runs once it has been spoken (an offer's answer window starts then)."""
        with self._lock:
            if all(queued != text for queued, _ in self._queue):
                self._queue.append((text, on_said))
        self.pump()

    def pump(self) -> None:
        """Speak the next waiting notice if the gap has passed; call this regularly."""
        with self._lock:
            if not self._queue or self._clock() - self._last < self._gap:
                return
            text, on_said = self._queue.popleft()
            self._last = self._clock()
        try:
            self._say(text)
            if on_said:
                on_said()
        except Exception:  # One failed notice must not hold back the rest.
            LOG.exception('Paced notice failed')
