"""Ask "Are you still there?" after a long absence during focus; sleep until the owner returns.

Sleeping pauses the focus session (no roasts, no distraction nudges) and answers chat or
voice with fixed lines instead of AI calls. Camera presence and the microphone keep
running so Vision can notice the owner is back or hear "wake up". Every reply here is
deterministic: none of it uses the AI.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

ASK = 'Are you still there?'
ACK_HERE = "Great, let's keep going."
ACK_REST = "Okay, I'll rest too. Wake me up when you're ready to work again."
SLEEP_SILENT = "I'll rest to save energy. Sit back down or say 'wake up' when you're ready."
ASLEEP_REPLY = "I'm resting to save energy. Say 'wake up' when you're ready."
WAKE_BY_VOICE = "I'm awake. Back to focus."
WAKE_BY_FACE = 'Welcome back! Back to focus.'

_WAKE = re.compile(r"\b(wake up|i'?m back|i am back|let'?s (?:work|go|start)|ready to work|i'?m ready)\b", re.I)
_REST = re.compile(r"\b(rest|resting|break|relax|relaxing|nap|sleep|lunch|dinner|eating|later|brb|be right back)\b", re.I)
_HERE = re.compile(r"\b(yes|yeah|yep|yup|here|still here|i'?m here|i am here|present)\b", re.I)
_SLEEP_NOW = re.compile(r"\b(go to sleep|sleep mode|you can rest|rest now)\b", re.I)


class AwayMode:
    def __init__(self, *, is_focus: Callable[[], bool], pause: Callable[[], None], resume: Callable[[], None],
                 announce: Callable[[str], None], away_after: float = 300.0, answer_window: float = 120.0,
                 wake_after: float = 3.0, clock: Callable[[], float] = time.monotonic):
        self._is_focus, self._pause, self._resume, self._announce = is_focus, pause, resume, announce
        self._away_after, self._answer_window, self._wake_after = away_after, answer_window, wake_after
        self._clock = clock
        self._lock = threading.Lock()
        self.state = 'active'
        now = clock()
        self._last_face = now
        self._asked_at = 0.0
        self._face_since: float | None = None
        self._paused_by_sleep = False
        self._slept_at = 0.0

    # --- camera side (main loop) -------------------------------------------------
    def observe(self, face_present: bool) -> None:
        now = self._clock()
        notices = []
        with self._lock:
            if face_present:
                self._last_face = now
                self._face_since = self._face_since if self._face_since is not None else now
            else:
                self._face_since = None
            if self.state == 'active':
                if self._is_focus() and now - self._last_face >= self._away_after:
                    self.state, self._asked_at = 'asking', now
                    notices.append(ASK)
            elif self.state == 'asking':
                if face_present:
                    self.state = 'active'
                elif now - self._asked_at >= self._answer_window:
                    notices.append(self._sleep_locked(SLEEP_SILENT))
            elif self.state == 'sleeping':
                if self._is_focus() and now - self._slept_at > 5.0:
                    # Focus is on again after our pause took effect: the owner resumed by hand.
                    self.state, self._paused_by_sleep, self._last_face = 'active', False, now
                elif face_present and now - self._face_since >= self._wake_after:
                    notices.append(self._wake_locked(WAKE_BY_FACE))
        for text in notices:
            self._announce(text)

    # --- chat and voice side ----------------------------------------------------
    def intercept(self, text: str) -> str | None:
        """Reply without AI when the message belongs to the away flow; otherwise None."""
        words = text.strip()
        with self._lock:
            if self.state == 'sleeping':
                if _WAKE.search(words):
                    return self._wake_locked(WAKE_BY_VOICE)
                return ASLEEP_REPLY
            if self.state == 'asking':
                if _REST.search(words):
                    return self._sleep_locked(ACK_REST)
                self.state, self._last_face = 'active', self._clock()
                if _HERE.search(words):
                    return ACK_HERE
                return None                          # a real question: let the AI answer it
            if _SLEEP_NOW.search(words):
                return self._sleep_locked(ACK_REST)
        return None

    @property
    def sleeping(self) -> bool:
        return self.state == 'sleeping'

    # --- transitions (lock held) ------------------------------------------------
    def _sleep_locked(self, message: str) -> str:
        self._paused_by_sleep = bool(self._is_focus())
        if self._paused_by_sleep:
            self._pause()
        self.state, self._face_since, self._slept_at = 'sleeping', None, self._clock()
        return message

    def _wake_locked(self, message: str) -> str:
        if self._paused_by_sleep:
            self._resume()
        self._paused_by_sleep = False
        self.state, self._last_face = 'active', self._clock()
        return message
