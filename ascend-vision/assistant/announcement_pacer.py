"""Spaces out notices Vision says on its own, so several never play back to back.

At startup the weather briefing, GitHub nudge, food reminder, agent room and AI key
notices can all become due at once. They are kept, in order, and spoken one at a time
with a gap. Answers to the owner and conversations (away mode, calibration) are not paced.

During a focus block the owner asks for ("do not disturb", ``hold``) ordinary notices
wait at the door; urgent ones (agents waiting, a reminder set for a clock time) still
come in. When the block ends Vision says how many waited, then plays them.
"""
from __future__ import annotations

from collections import deque
import logging
import re
import threading
import time
from typing import Callable

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
            same = next((i for i, (queued, _, _) in enumerate(self._queue) if queued == text), None)
            if same is None:
                self._queue.append((text, on_said, urgent))
            elif urgent and not self._queue[same][2]:  # an urgent copy lets the waiting one through
                self._queue[same] = (text, on_said or self._queue[same][1], True)
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

    def pump(self) -> None:
        """Speak the next notice that may come in if the gap has passed; call this regularly."""
        holding = self.holding()
        with self._lock:
            held = sum(1 for _, _, urgent in self._queue if not urgent)
            if self._was_holding and not holding and held:
                self._summary_due = True
            self._was_holding = holding
            if holding:
                self._summary_due = False  # a new block began before the summary was said
            else:
                self._released = 0
            if self._clock() - self._last < self._gap:
                return
            if self._summary_due:
                self._summary_due = False
                summary = f"While you focused, {_count(held)} notice{'' if held == 1 else 's'} waited."
            else:
                summary = None
                index = next((i for i, (_, _, urgent) in enumerate(self._queue)
                              if urgent or not holding or self._released), None)
                if index is None:
                    return
                text, on_said, urgent = self._queue[index]
                del self._queue[index]
                if not urgent and holding:
                    self._released -= 1
                self._last = self._clock()
        if summary:  # the first waiting notice follows the summary without a gap
            self._say_safely(summary)
            return
        self._say_safely(text)
        if on_said:
            # Coaches compare this count to learn whether a later question took over the owner's answer.
            self.question_seq += 1
            try:
                on_said()  # even after a failed say, so a coach waiting for its question is never stuck
            except Exception:
                LOG.exception('Paced notice callback failed')

    def _say_safely(self, text: str) -> None:
        try:
            self._say(text)
        except Exception:  # One failed notice must not hold back the rest.
            LOG.exception('Paced notice failed')


_LEAD = r"(?:(?:hey|ok|okay) )?(?:vision )?"
_AMOUNT = {'a': 1, 'an': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'ten': 10, 'fifteen': 15,
           'twenty': 20, 'twenty five': 25, 'thirty': 30, 'forty': 40, 'forty five': 45, 'fifty': 50, 'sixty': 60,
           'ninety': 90}
_NUMBER = '|'.join(sorted(map(re.escape, _AMOUNT), key=len, reverse=True))
_START = re.compile(rf"{_LEAD}(?:do not disturb|don't disturb me|focus block|start a focus block|hold my notices)"
                    rf"(?: for (?:(?P<half>half an hour)|(?P<n>\d+|{_NUMBER}) (?P<unit>minutes?|hours?)))?")
_END = re.compile(rf"{_LEAD}(?:(?:end|stop|finish) (?:the )?(?:focus block|do not disturb)|i'm done focusing"
                  r"|stop holding(?: my notices)?)")


class FocusDoorman:
    """A focus block the owner asks for: ordinary notices wait until it ends and the owner is at the PC."""

    def __init__(self, pacer: AnnouncementPacer, *, clock=None, present=lambda: True, default_minutes: int = 50,
                 max_minutes: int = 240):
        from datetime import datetime
        self.pacer, self.clock, self.present = pacer, clock or datetime.now, present
        self.default_minutes, self.max_minutes = default_minutes, max_minutes
        self._until = None
        self._leftover = False  # the block ended with notices waiting
        self._lock = threading.Lock()  # the main loop and the chat thread both ask

    def _here(self) -> bool:
        try:
            return bool(self.present())
        except Exception:
            return True

    def holding(self) -> bool:
        waiting, here = self.pacer.waiting(), self._here()
        with self._lock:
            if self._until is not None and self.clock() >= self._until:
                self._until, self._leftover = None, True
            if self._until is not None:
                return True
            if self._leftover and waiting and not here:
                return True  # never play what waited to an empty room
            self._leftover = False
            return False

    def intercept(self, text: str) -> str | None:
        from datetime import timedelta
        said = ' '.join(re.sub(r"[^a-z0-9' ]", ' ', text.lower()).split())  # keeps "25 minutes"
        started = _START.fullmatch(said)
        if started:
            if started['half']:
                minutes = 30
            elif started['n']:
                amount = _AMOUNT.get(started['n']) or int(started['n'])
                minutes = amount * (60 if started['unit'].startswith('h') else 1)
            else:
                minutes = self.default_minutes
            minutes = max(5, min(self.max_minutes, minutes))
            with self._lock:
                self._until = self.clock() + timedelta(minutes=minutes)
            hours = minutes // 60
            span = (f"{hours} hour{'' if hours == 1 else 's'}" if minutes % 60 == 0 else f'{minutes} minutes')
            return f"Okay. I'll hold my notices for {span}. Agents and reminders you timed still come in."
        if _END.fullmatch(said):
            with self._lock:
                if self._until is None:
                    return None
                self._until, self._leftover = None, True
            return 'Okay, the focus block is over.'
        asked = re.search(r"\b(?:what's|what is|anything) waiting\b", said)
        release = re.search(r'\blet them (?:through|in)\b', said)
        if not (asked or release) or not self.holding():
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


