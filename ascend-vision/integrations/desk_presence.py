"""Debounce face-presence observations into desk presence without identity claims."""
from __future__ import annotations

import math
import json
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np

from assistant.context_runtime import DeskRegion


def face_center_in_region(face_landmarks, region: DeskRegion, frame_width: int,
                          frame_height: int) -> bool:
    if not face_landmarks or frame_width < 1 or frame_height < 1:
        return False
    points = [(float(point[0]), float(point[1])) for point in face_landmarks
              if len(point) >= 2 and math.isfinite(float(point[0])) and math.isfinite(float(point[1]))]
    if not points:
        return False
    x = (min(point[0] for point in points) + max(point[0] for point in points)) / (2 * frame_width)
    y = (min(point[1] for point in points) + max(point[1] for point in points)) / (2 * frame_height)
    return region.contains(x, y)


def is_camera_frame_usable(frame) -> bool:
    """Reject very dark, blown-out, or nearly uniform images as weak evidence."""
    if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim not in (2, 3):
        return False
    if frame.ndim == 3 and frame.shape[2] not in (1, 3, 4):
        return False
    try:
        small = cv2.resize(frame, (64, 48), interpolation=cv2.INTER_AREA)
        if small.ndim == 3:
            if small.shape[2] == 1:
                small = small[:, :, 0]
            elif small.shape[2] == 4:
                small = cv2.cvtColor(small, cv2.COLOR_BGRA2GRAY)
            else:
                small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        mean, deviation = cv2.meanStdDev(small)
        brightness = float(mean[0, 0])
        contrast = float(deviation[0, 0])
        return 8.0 <= brightness <= 247.0 and contrast >= 5.0
    except (cv2.error, TypeError, ValueError):
        return False


class DeskRegionStore:
    """Atomically persist only the owner's normalized calibration rectangle."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> DeskRegion | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"left", "top", "right", "bottom"}:
                return None
            return DeskRegion(**payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def save(self, region: DeskRegion) -> None:
        if not isinstance(region, DeskRegion):
            raise TypeError("region must be a DeskRegion")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(region.as_dict(), temporary, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class PresenceDebouncer:
    def __init__(self, *, absence_seconds: float = 10.0, return_seconds: float = 3.0,
                 max_frame_gap_seconds: float = 2.0):
        for name, value in (("absence_seconds", absence_seconds),
                            ("return_seconds", return_seconds),
                            ("max_frame_gap_seconds", max_frame_gap_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        self.absence_seconds = float(absence_seconds)
        self.return_seconds = float(return_seconds)
        self.max_frame_gap_seconds = float(max_frame_gap_seconds)
        self._state = "unknown"
        self._last_time: float | None = None
        self._candidate_since: float | None = None
        self._candidate: str | None = None

    @property
    def state(self) -> str:
        return self._state

    def _reset(self, state: str = "unknown") -> str:
        self._state = state
        self._candidate = None
        self._candidate_since = None
        return self._state

    def update(self, face_present: bool, monotonic_time: float, *, source_available: bool) -> str:
        if type(face_present) is not bool or type(source_available) is not bool:
            raise ValueError("presence and source availability must be booleans")
        if isinstance(monotonic_time, bool) or not isinstance(monotonic_time, (int, float)) or not math.isfinite(monotonic_time):
            raise ValueError("monotonic_time must be finite")
        at = float(monotonic_time)
        if not source_available:
            self._last_time = None
            return self._reset()
        if self._last_time is not None:
            gap = at - self._last_time
            if gap <= 0 or gap > self.max_frame_gap_seconds:
                self._state = "unknown"
                self._candidate = None
                self._candidate_since = None
        self._last_time = at

        candidate = "present" if face_present else "away"
        dwell = self.return_seconds if face_present else self.absence_seconds
        if candidate != self._candidate:
            self._candidate = candidate
            self._candidate_since = at
        elif self._candidate_since is not None and at - self._candidate_since >= dwell:
            self._state = candidate
        return self._state
