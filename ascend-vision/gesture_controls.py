"""Deterministic finger gestures and their Vision-only control state."""
from __future__ import annotations

from enum import Enum
import math
from typing import Sequence


class GestureMode(str, Enum):
    IDLE = "idle"
    CHAT = "chat"
    AUTOMATION = "automation"
    MISSIONS = "missions"
    HABITS = "habits"


def count_fingers(landmarks: Sequence[Sequence[float]], *, handedness: str | None = None) -> int | None:
    """Return the count of extended fingers from one upright MediaPipe hand."""
    try:
        if len(landmarks) != 21:
            return None
        points = [(float(point[0]), float(point[1])) for point in landmarks]
    except (TypeError, ValueError, IndexError, KeyError, OverflowError):
        return None
    if not all(math.isfinite(value) for point in points for value in point):
        return None

    def x(index: int) -> float:
        return points[index][0]

    def y(index: int) -> float:
        return points[index][1]

    normalized_hand = (handedness or "Right").strip().lower()
    thumb_extended = x(4) < x(3) if normalized_hand == "right" else x(4) > x(3)
    extended = int(thumb_extended)
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        extended += int(y(tip) < y(pip))
    return extended


def hand_overlaps_phone(landmarks: Sequence[Sequence[float]], phone_box,
                        *, threshold: float = 0.2) -> bool:
    """Suppress gestures when the tracked hand substantially overlaps the phone."""
    if phone_box is None:
        return False
    try:
        if len(landmarks) != 21:
            return True
        xs = [float(point[0]) for point in landmarks]
        ys = [float(point[1]) for point in landmarks]
        x1, y1, x2, y2 = (float(value) for value in phone_box.xyxy)
    except (AttributeError, TypeError, ValueError, IndexError):
        return True
    if not all(math.isfinite(value) for value in (*xs, *ys, x1, y1, x2, y2)):
        return True
    hand_x1, hand_x2 = min(xs), max(xs)
    hand_y1, hand_y2 = min(ys), max(ys)
    intersection = max(0.0, min(hand_x2, x2) - max(hand_x1, x1)) * max(
        0.0, min(hand_y2, y2) - max(hand_y1, y1))
    hand_area = max(1e-9, (hand_x2 - hand_x1) * (hand_y2 - hand_y1))
    phone_area = max(1e-9, (x2 - x1) * (y2 - y1))
    return intersection / min(hand_area, phone_area) >= threshold


class GestureRecognizer:
    """Time-based debounce that triggers a hand pose once until it is released."""
    def __init__(self, *, stable_seconds: float = 0.9, cooldown_seconds: float = 1.5):
        self.stable_seconds = stable_seconds
        self.cooldown_seconds = cooldown_seconds
        self._candidate: int | None = None
        self._candidate_started_at: float | None = None
        self._last_triggered: int | None = None
        self._last_triggered_at = float("-inf")
        self._armed = True

    def update(self, finger_count: int | None, now: float) -> int | None:
        if finger_count is None:
            self._candidate = None
            self._candidate_started_at = None
            self._armed = True
            return None
        if finger_count != self._candidate:
            self._candidate = finger_count
            self._candidate_started_at = now
            if finger_count != self._last_triggered:
                self._armed = True
            return None
        if not self._armed or self._candidate_started_at is None:
            return None
        if now - self._candidate_started_at + 1e-9 < self.stable_seconds:
            return None
        if now - self._last_triggered_at < self.cooldown_seconds:
            return None
        self._armed = False
        self._last_triggered = finger_count
        self._last_triggered_at = now
        return finger_count


class GestureController:
    """Owns one-shot UI panel selection; explicit controls own mute and cancel."""
    _MODES = {1: GestureMode.CHAT, 2: GestureMode.AUTOMATION,
              3: GestureMode.MISSIONS, 4: GestureMode.HABITS}

    def __init__(self):
        self.mode = GestureMode.IDLE

    def handle(self, finger_count: int) -> None:
        # Hand poses navigate panels only. Explicit controls own mute and cancel.
        if finger_count in (0, 5) or finger_count not in range(1, 5):
            return None
        mode = self._MODES.get(finger_count)
        if mode is not None:
            self.mode = mode

def core_status_indicator(*, configured: bool, state: str | None) -> tuple[str, str]:
    """Translate existing Ascend health states into a compact camera badge."""
    if not configured:
        return 'CORE: DISABLED', 'muted'
    if state is None:
        return 'CORE: CONNECTING...', 'warning'
    if state == 'ASCEND_CONNECTED':
        return 'CORE: CONNECTED', 'good'
    return 'CORE: UNAVAILABLE', 'bad'


def effective_core_connection_state(health_state: str | None, vision_state: str | None) -> str | None:
    """Treat a successful authenticated heartbeat as proof that Core is reachable."""
    if health_state == 'ASCEND_CONNECTED' or vision_state == 'ASCEND_CONNECTED':
        return 'ASCEND_CONNECTED'
    return health_state or vision_state


def vision_presence_indicator(*, configured: bool, state: str | None) -> tuple[str, str]:
    """Translate heartbeat outcomes without conflating them with Core API health."""
    if not configured:
        return 'VISION: DISABLED', 'muted'
    if state is None:
        return 'VISION: CONNECTING...', 'warning'
    if state == 'CONNECTED':
        return 'VISION: CONNECTED', 'good'
    if state == 'ASCEND_AUTH_ERROR':
        return 'VISION: RE-AUTH REQUIRED', 'bad'
    return 'VISION: OFFLINE', 'bad'


def gesture_overlay_lines(*, finger_count: int | None, handedness: str | None,
                          recognizer: GestureRecognizer, controller: GestureController,
                          feedback_muted: bool, now: float, last_action: str | None,
                          core_status: str = 'CORE: DISABLED',
                          vision_status: str = 'VISION: OFFLINE') -> list[str]:
    """Return compact, human-readable status text for the live camera preview."""
    active_for = 0.0
    if recognizer._candidate_started_at is not None:
        active_for = min(recognizer.stable_seconds, max(0.0, now - recognizer._candidate_started_at))
    voice = 'MUTED' if feedback_muted else 'ACTIVE'
    fingers = '--' if finger_count is None else str(finger_count)
    side = handedness or 'Unknown'
    filled = round(10 * active_for / recognizer.stable_seconds) if recognizer.stable_seconds else 0
    hold_bar = '#' * filled + '-' * (10 - filled)
    return [
        'GESTURE CONTROLS',
        f'VOICE: {voice} | {core_status}',
        vision_status,
        f'HAND: {side} | FINGERS: {fingers}',
        f'HOLD: {hold_bar} {active_for:.1f} / {recognizer.stable_seconds:.1f}s',
        f'MODE: {controller.mode.value.upper()}',
        f'LAST: {last_action or "Waiting for a stable hand pose"}',
    ]
