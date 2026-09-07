"""Deterministic hold confirmation; no I/O, wall-clock duration arithmetic or alerts."""
from dataclasses import dataclass, replace
from datetime import datetime
import math

from config import HoldConfig
from detector import PhoneBox

from posture import TrajectorySmoother, classify_phone_posture

HOLD_LANDMARKS = (0, 4, 8, 12, 16, 20)  # wrist and five fingertips


def proximity_distance(box: PhoneBox | None, hands, metric='center') -> float:
    """Minimum pixel distance of a wrist/tip; infinity means no usable evidence."""
    if metric not in ('center', 'box'):
        raise ValueError('Unknown proximity metric')
    if box is None:
        return math.inf
    x1, y1, x2, y2 = box.xyxy
    if not all(math.isfinite(v) for v in box.xyxy) or x2 <= x1 or y2 <= y1:
        return math.inf
    distances = []
    for hand in hands:
        if len(hand) != 21:
            continue
        for index in HOLD_LANDMARKS:
            x, y = hand[index][:2]
            if not math.isfinite(x) or not math.isfinite(y):
                continue
            if metric == 'center':
                distances.append(math.hypot(x - (x1 + x2) / 2, y - (y1 + y2) / 2))
            else:
                distances.append(math.hypot(max(x1 - x, 0, x - x2), max(y1 - y, 0, y - y2)))
    return min(distances, default=math.inf)


@dataclass(frozen=True)
class HoldEvent:
    id: int  # process-local; persistence IDs belong to Phase 3
    started_at: datetime
    confidence: float
    alert_allowed: bool
    duration_seconds: float = 0.
    ended_at: datetime | None = None
    end_reason: str | None = None
    posture: str = 'active_hold'

    @property
    def reason(self) -> str | None:
        return self.end_reason


@dataclass(frozen=True)
class HoldUpdate:
    state: str
    consecutive_frames: int
    distance_px: float
    active: HoldEvent | None
    started: HoldEvent | None
    ended: HoldEvent | None
    cooldown_remaining: float
    posture: str = 'none'


class HoldMachine:
    def __init__(self, config: HoldConfig):
        self.config = config
        self.consecutive_frames = 0
        self.active = None
        self._start_time = None
        self._last_time = None
        self._last_wall = None
        self._last_alert = None
        self._next_id = 1
        self._trajectory = TrajectorySmoother(config.trajectory_smoothing_frames)

    def _end(self, now, wall, reason):
        ended = None
        if self.active is not None:
            ended = replace(self.active, duration_seconds=max(0., now - self._start_time),
                            ended_at=wall, end_reason=reason)
        self.active = None
        self._start_time = None
        self.consecutive_frames = 0
        return ended

    def finish(self, reason='shutdown') -> HoldEvent | None:
        """Close at last observed evidence, excluding unobserved shutdown/stall time."""
        return self._end(self._last_time, self._last_wall, reason)

    def update(self, box, hands, now: float, captured_at: datetime, face_landmarks=None) -> HoldUpdate:
        if not math.isfinite(now) or (self._last_time is not None and now <= self._last_time):
            raise ValueError('Observation timestamps must be finite and strictly increasing')
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError('Capture timestamps must be timezone-aware')
        ended = started = None
        if (self._last_time is not None
                and now - self._last_time > self.config.max_observation_gap_seconds):
            ended = self.finish('observation_gap')
        self._last_time, self._last_wall = now, captured_at
        distance = proximity_distance(box, hands, self.config.distance_metric)
        held = distance < self.config.proximity_px

        stability = self._trajectory.update(now, box, hands)
        if stability < 0.2 and held and self.active is None:
            # Reaching/rapid-sweep gesture veto: suppress initial trigger
            held = False

        posture = 'none'
        if held or self.active is not None:
            if self.config.posture_enabled:
                posture = classify_phone_posture(box, hands, face_landmarks, self.config.call_proximity_px)
            else:
                posture = 'active_hold'

        if not held:
            released = self._end(now, captured_at, 'released')
            ended = released if released is not None else ended
        else:
            self.consecutive_frames = min(self.consecutive_frames + 1, self.config.threshold_frames)
            if self.active is None and self.consecutive_frames == self.config.threshold_frames:
                allowed = self._last_alert is None or now - self._last_alert >= self.config.cooldown_seconds
                if allowed:
                    self._last_alert = now
                self.active = HoldEvent(self._next_id, captured_at, box.confidence, allowed, posture=posture)
                self._next_id += 1
                self._start_time = now
                started = self.active
            elif self.active is not None:
                self.active = replace(self.active, duration_seconds=now - self._start_time, posture=posture)
        cooldown = (max(0., self.config.cooldown_seconds - (now - self._last_alert))
                    if self._last_alert is not None else 0.)
        state = 'holding' if self.active is not None else ('candidate' if held else 'idle')
        return HoldUpdate(state, self.consecutive_frames, distance, self.active, started, ended, cooldown, posture=posture)

