"""Guided eye calibration: measure the owner's open and closed eyes, save it, reuse it.

"Calibrate my eyes" -> Vision asks for 5 seconds of normally open eyes, then 3 seconds
of gently closed eyes, and puts the closed-eye line between the two. Two saved profiles
(glasses / no glasses) are switched with "glasses on" / "glasses off". Fixed wording, no AI.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
import threading
import time
from typing import Callable

START = re.compile(r'\b(calibrate|configure|set ?up|recalibrate|reset)\b.*\beyes?\b|\beye calibration\b', re.I)
GLASSES_ON = re.compile(r"\b(glasses on|wearing (my )?glasses|put (my )?glasses on|with glasses)\b", re.I)
GLASSES_OFF = re.compile(r"\b(glasses off|took (my )?glasses off|no glasses|without glasses|not wearing (my )?glasses)\b", re.I)

OPEN_SECONDS, CLOSED_SECONDS = 5.0, 3.0
SETTLE_SECONDS = 3.0        # time to hear the instruction before measuring
MIN_SAMPLES = 15
LINE_POSITION = 0.40        # closed + 40 % of the gap: well below normal open eyes, fewer false alarms
PROFILES = {'glasses': 'with glasses', 'no_glasses': 'without glasses'}


def threshold_from(open_samples: list[float], closed_samples: list[float]) -> tuple[float, float, float | None]:
    """(threshold, open level, closed level). Medians ignore blinks and stray frames."""
    open_level = statistics.median(open_samples)
    closed_level = statistics.median(closed_samples) if closed_samples else None
    if closed_level is None or open_level - closed_level < 0.04:
        line = open_level * 0.65             # closing was not clearly measured
    else:
        line = closed_level + LINE_POSITION * (open_level - closed_level)
    return max(0.10, min(0.35, line)), open_level, closed_level


class EyeCalibrationStore:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1), encoding='utf-8')

    @property
    def active(self) -> str:
        value = self._read().get('active')
        return value if value in PROFILES else 'no_glasses'

    def set_active(self, profile: str) -> None:
        data = self._read()
        data['active'] = profile
        self._write(data)

    def profile(self, name: str | None = None) -> dict | None:
        entry = self._read().get('profiles', {}).get(name or self.active)
        if not isinstance(entry, dict) or not isinstance(entry.get('threshold'), (int, float)):
            return None
        return entry

    def save(self, name: str, threshold: float, open_level: float, closed_level: float | None) -> None:
        data = self._read()
        data.setdefault('profiles', {})[name] = {
            'threshold': round(threshold, 4), 'open': round(open_level, 4),
            'closed': None if closed_level is None else round(closed_level, 4),
            'calibrated_at': datetime.now().isoformat(timespec='seconds')}
        data['active'] = name
        self._write(data)


class EyeCalibrator:
    """Driven by camera frames (observe) and chat/voice (intercept)."""

    def __init__(self, store: EyeCalibrationStore, machine, *, announce: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic):
        self._store, self._machine, self._announce, self._clock = store, machine, announce, clock
        self._lock = threading.Lock()
        self.phase: str | None = None        # None | 'open' | 'closed'
        self._phase_started = 0.0
        self._open: list[float] = []
        self._closed: list[float] = []
        self._profile = store.active

    @property
    def active(self) -> bool:
        return self.phase is not None

    def apply_saved(self) -> bool:
        """Use the saved calibration for the current glasses setting; False if there is none."""
        saved = self._store.profile()
        if saved is None:
            return False
        self._machine.apply_calibration(saved['threshold'], saved['open'])
        return True

    def start(self) -> str:
        with self._lock:
            self._profile = self._store.active
            self.phase, self._phase_started = 'open', self._clock()
            self._open, self._closed = [], []
        self._machine.suspended = True       # closing your eyes now must not count as drowsiness
        where = PROFILES[self._profile]
        return (f"Let's calibrate your eyes ({where}). Look at the screen with your eyes open normally, "
                f"as you usually work, for about 5 seconds.")

    def observe(self, ear: float | None) -> None:
        """One camera frame: the average eye openness, or None when no face is visible."""
        notices = []
        with self._lock:
            if self.phase is None:
                return
            elapsed = self._clock() - self._phase_started
            usable = ear is not None and math.isfinite(ear) and ear > 0.03
            if self.phase == 'open':
                if SETTLE_SECONDS <= elapsed <= SETTLE_SECONDS + OPEN_SECONDS and usable:
                    self._open.append(ear)
                if elapsed > SETTLE_SECONDS + OPEN_SECONDS:
                    if len(self._open) < MIN_SAMPLES:
                        notices.append(self._fail_locked())
                    else:
                        self.phase, self._phase_started = 'closed', self._clock()
                        notices.append("Good. Now gently close your eyes until I say open. Relax, don't squeeze.")
            elif self.phase == 'closed':
                if SETTLE_SECONDS <= elapsed <= SETTLE_SECONDS + CLOSED_SECONDS and usable:
                    self._closed.append(ear)
                if elapsed > SETTLE_SECONDS + CLOSED_SECONDS:
                    notices.append(self._finish_locked())
        for text in notices:
            self._announce(text)

    def _fail_locked(self) -> str:
        self.phase = None
        self._machine.suspended = False
        return ("I couldn't see your face clearly enough. Check the light, face the camera, and say "
                "'calibrate my eyes' again.")

    def _finish_locked(self) -> str:
        self.phase = None
        closed = self._closed if len(self._closed) >= MIN_SAMPLES // 2 else []
        threshold, open_level, closed_level = threshold_from(self._open, closed)
        self._store.save(self._profile, threshold, open_level, closed_level)
        self._machine.apply_calibration(threshold, open_level)
        self._machine.suspended = False
        where = PROFILES[self._profile]
        extra = '' if closed else " I couldn't measure your closed eyes, so I used a safe default."
        return f'Open your eyes. Done: your eyes are calibrated {where} and saved.{extra}'

    def intercept(self, text: str) -> str | None:
        words = text.strip()
        if START.search(words):
            if self.active:
                return 'Calibration is already running. Follow my instructions.'
            return self.start()
        for pattern, profile in ((GLASSES_ON, 'glasses'), (GLASSES_OFF, 'no_glasses')):
            if pattern.search(words):
                self._store.set_active(profile)
                if self.apply_saved():
                    return f'Okay, using your eye calibration {PROFILES[profile]}.'
                self._machine.reset_calibration()
                return (f"Okay. I don't have a calibration {PROFILES[profile]} yet, so I'll estimate for now. "
                        f"Say 'calibrate my eyes' for a better fit.")
        return None
