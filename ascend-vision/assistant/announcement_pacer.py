"""Spaces out notices Vision says on its own, so several never play back to back.

At startup the weather briefing, GitHub nudge, food reminder, agent room and AI key
notices can all become due at once. They are kept, in order, and spoken one at a time
with a gap. Answers to the owner and conversations (away mode, calibration) are not paced.

During focus mode (``hold``) ordinary notices wait at the door; urgent ones (agents
waiting, a reminder set for a clock time) still come in. When focus ends Vision says
how many waited, then plays them.
"""
from __future__ import annotations

from collections import deque
import logging
import re
import threading
import time
from typing import Callable

from assistant.answers import words

LOG = logging.getLogger(__name__)
NUMBERS = ('no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten')


def _count(n: int) -> str:
    return NUMBERS[n] if n < len(NUMBERS) else str(n)


class AnnouncementPacer:
    def __init__(self, say: Callable[[str], None], gap_seconds: float = 45.0, clock: Callable[[], float] = time.monotonic,
                 hold: Callable[[], bool] | None = None):
        self._say, self._gap, self._clock, self._hold = say, gap_seconds, clock, hold
        self._queue: deque[tuple[str, Callable[[], None] | None, bool]] = deque()
        self._lock = threading.Lock()
        self._last = float('-inf')
        self._was_holding = self._summary_due = False
        self._released = 0  # waiting notices the owner let through early
        self.question_seq = 0  # notices with an answer window spoken so far

    def __call__(self, text: str, on_said: Callable[[], None] | None = None, urgent: bool = False) -> None:
        """Queue a notice; on_said runs once it has been spoken (an offer's answer window starts then)."""
        with self._lock:
            if all(queued != text for queued, _, _ in self._queue):
                self._queue.append((text, on_said, urgent))
        self.pump()

    def holding(self) -> bool:
        if self._hold is None:
            return False
        try:
            return bool(self._hold())
        except Exception as exc:  # A broken focus check must never silence Vision.
            LOG.debug('Focus hold check failed (%s)', type(exc).__name__)
            return False

    def waiting(self) -> int:
        """Ordinary notices queued (the ones focus holds)."""
        with self._lock:
            return sum(1 for _, _, urgent in self._queue if not urgent)

    def release_held(self) -> None:
        """Let what is waiting now come in, still one at a time; later notices wait again."""
        with self._lock:
            self._released = sum(1 for _, _, urgent in self._queue if not urgent)
        self.pump()

    def pump(self) -> None:
        """Speak the next notice that may come in if the gap has passed; call this regularly."""
        holding = self.holding()
        with self._lock:
            held = sum(1 for _, _, urgent in self._queue if not urgent)
            if self._was_holding and not holding and held:
                self._summary_due = True
            self._was_holding = holding
            if not holding:
                self._released = 0
            if self._clock() - self._last < self._gap:
                return
            if self._summary_due:
                self._summary_due = False
                text, on_said = f"While you focused, {_count(held)} notice{'' if held == 1 else 's'} waited.", None
            else:
                index = next((i for i, (_, _, urgent) in enumerate(self._queue)
                              if urgent or not holding or self._released), None)
                if index is None:
                    return
                text, on_said, urgent = self._queue[index]
                del self._queue[index]
                if not urgent and holding:
                    self._released -= 1
            self._last = self._clock()
        try:
            self._say(text)
        except Exception:  # One failed notice must not hold back the rest.
            LOG.exception('Paced notice failed')
        if on_said:
            # Coaches compare this count to learn whether a later question took over the owner's answer.
            self.question_seq += 1
            try:
                on_said()  # even after a failed say, so a coach waiting for its question is never stuck
            except Exception:
                LOG.exception('Paced notice callback failed')


class FocusDoorman:
    """Answers "what's waiting?" and "let them through" while focus holds notices."""

    def __init__(self, pacer: AnnouncementPacer):
        self.pacer = pacer

    def intercept(self, text: str) -> str | None:
        said = words(text)
        asked = re.search(r"\b(?:what's|what is|anything) waiting\b", said)
        release = re.search(r'\blet them (?:through|in)\b', said)
        if not (asked or release) or not self.pacer.holding():
            return None
        waiting = self.pacer.waiting()
        if not waiting:
            return 'Nothing is waiting.'
        if release:
            self.pacer.release_held()
            return 'Okay.'
        if waiting == 1:
            return "One notice is waiting for the end of your focus block. Say 'let them through' to hear it now."
        return (f"{_count(waiting).title()} notices are waiting for the end of your focus block. "
                "Say 'let them through' to hear them now.")
