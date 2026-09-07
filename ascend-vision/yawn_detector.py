"""Mouth Aspect Ratio (MAR) computation and yawn detection state machine.

Implements the Mouth Aspect Ratio metric using canonical MediaPipe Face Mesh
outer/inner lip landmarks to detect sustained yawning while filtering out
transient mouth movements during normal speech or smiling.
"""
from dataclasses import dataclass, replace
from datetime import datetime
import math

from config import YawnConfig

# Canonical MediaPipe Face Mesh landmark indices for mouth/lips:
# m1 (left corner): 61
# m2 (top left): 37
# m3 (top center): 0
# m4 (top right): 267
# m5 (right corner): 291
# m6 (bottom right): 314
# m7 (bottom center): 17
# m8 (bottom left): 84
MOUTH_LANDMARKS = (61, 37, 0, 267, 291, 314, 17, 84)


def mouth_aspect_ratio(mouth_points: list[tuple[float, float, float] | tuple[float, float]]) -> float:
    """Compute Mouth Aspect Ratio (MAR) for 8 ordered mouth landmark points.

    Formula:
        MAR = (||m2 - m8|| + ||m3 - m7|| + ||m4 - m6||) / (2 * ||m1 - m5||)
    """
    if len(mouth_points) != 8:
        raise ValueError(f'Expected 8 mouth points, got {len(mouth_points)}')
    for pt in mouth_points:
        if not math.isfinite(pt[0]) or not math.isfinite(pt[1]):
            return 0.0

    m1, m2, m3, m4, m5, m6, m7, m8 = mouth_points[:8]

    # Vertical lip opening distances
    v1 = math.hypot(m2[0] - m8[0], m2[1] - m8[1])
    v2 = math.hypot(m3[0] - m7[0], m3[1] - m7[1])
    v3 = math.hypot(m4[0] - m6[0], m4[1] - m6[1])

    # Horizontal mouth width (corner-to-corner)
    h = math.hypot(m1[0] - m5[0], m1[1] - m5[1])

    if h < 1e-6:
        return 0.0

    return (v1 + v2 + v3) / (2.0 * h)


def calculate_face_mar(face_landmarks: list[tuple[float, float, float] | tuple[float, float]]) -> float:
    """Extract Mouth Aspect Ratio (MAR) from 468+ face landmarks.

    Returns:
        MAR float. Returns 0.0 if face_landmarks is empty or invalid.
    """
    if not face_landmarks or len(face_landmarks) < 315:
        return 0.0

    mouth_points = [face_landmarks[i] for i in MOUTH_LANDMARKS]
    return mouth_aspect_ratio(mouth_points)


@dataclass(frozen=True)
class YawnEvent:
    id: int
    started_at: datetime
    mar: float
    alert_allowed: bool
    duration_seconds: float = 0.0
    ended_at: datetime | None = None
    end_reason: str | None = None

    @property
    def reason(self) -> str | None:
        return self.end_reason


@dataclass(frozen=True)
class YawnUpdate:
    state: str  # 'closed', 'opening', 'yawning'
    consecutive_frames: int
    mar: float
    yawn_count: int
    active: YawnEvent | None
    started: YawnEvent | None
    ended: YawnEvent | None
    cooldown_remaining: float
    calibrated: bool = True
    calibration_progress: float = 1.0
    baseline_mar: float = 0.0
    effective_threshold: float = 0.65


class YawnMachine:
    """Deterministic yawn detection state machine with adaptive baseline calibration.

    Differentiates short speech-related mouth movements from prolonged yawning.
    Pure transition logic: no I/O, no wall-clock sleep, fully replayable.
    """

    def __init__(self, config: YawnConfig):
        self.config = config
        self.consecutive_frames = 0
        self.yawn_count = 0
        self.active: YawnEvent | None = None
        self._start_time: float | None = None
        self._last_time: float | None = None
        self._last_wall: datetime | None = None
        self._last_alert: float | None = None
        self._next_id = 1
        self._mar_samples: list[float] = []
        self.baseline_mar: float = 0.0
        self.calibrated = (getattr(config, 'calibration_frames', 30) <= 0)
        self.effective_threshold: float = config.mar_threshold

    def _end(self, now: float | None, wall: datetime | None, reason: str) -> YawnEvent | None:
        ended = None
        if self.active is not None:
            duration = max(0.0, (now or 0.0) - (self._start_time or 0.0))
            ended = replace(self.active, duration_seconds=duration, ended_at=wall, end_reason=reason)
        self.active = None
        self._start_time = None
        return ended

    def finish(self, reason: str = 'shutdown') -> YawnEvent | None:
        """Close at last observed timestamp."""
        ended = self._end(self._last_time, self._last_wall, reason)
        self.consecutive_frames = 0
        return ended

    def update(self, face_landmarks, now: float, captured_at: datetime) -> YawnUpdate:
        if not math.isfinite(now) or (self._last_time is not None and now <= self._last_time):
            raise ValueError('Observation timestamps must be finite and strictly increasing')
        if captured_at.tzinfo is None or captured_at.utcoffset() is None:
            raise ValueError('Capture timestamps must be timezone-aware')

        self._last_time = now
        self._last_wall = captured_at

        started = None
        ended = None

        cooldown_remaining = 0.0
        if self._last_alert is not None:
            cooldown_remaining = max(0.0, self.config.cooldown_seconds - (now - self._last_alert))

        calib_target = getattr(self.config, 'calibration_frames', 30)
        calib_progress = 1.0 if calib_target <= 0 else min(1.0, len(self._mar_samples) / calib_target)

        if not face_landmarks:
            # Face lost: reset opening frames and close active yawn
            ended = self._end(now, captured_at, 'face_lost')
            self.consecutive_frames = 0
            return YawnUpdate(
                state='closed',
                consecutive_frames=0,
                mar=0.0,
                yawn_count=self.yawn_count,
                active=self.active,
                started=None,
                ended=ended,
                cooldown_remaining=cooldown_remaining,
                calibrated=self.calibrated,
                calibration_progress=calib_progress,
                baseline_mar=self.baseline_mar,
                effective_threshold=self.effective_threshold
            )

        mar = calculate_face_mar(face_landmarks)

        # Handle calibration phase
        if not self.calibrated and calib_target > 0:
            if math.isfinite(mar) and mar > 0.05:
                self._mar_samples.append(mar)
            calib_progress = min(1.0, len(self._mar_samples) / calib_target)
            if len(self._mar_samples) >= calib_target:
                self.baseline_mar = float(sum(self._mar_samples) / len(self._mar_samples))
                ratio = getattr(self.config, 'adaptive_threshold_ratio', 1.75)
                # Adaptive MAR threshold: must be significantly above baseline resting mouth opening
                # At least baseline + 0.18, and at least config.mar_threshold
                calculated = max(self.baseline_mar + 0.18, self.baseline_mar * ratio)
                self.effective_threshold = float(max(self.config.mar_threshold, min(1.30, calculated)))
                self.calibrated = True
            else:
                return YawnUpdate(
                    state='calibrating',
                    consecutive_frames=0,
                    mar=mar,
                    yawn_count=self.yawn_count,
                    active=None,
                    started=None,
                    ended=None,
                    cooldown_remaining=cooldown_remaining,
                    calibrated=self.calibrated,
                    calibration_progress=calib_progress,
                    baseline_mar=self.baseline_mar,
                    effective_threshold=self.effective_threshold
                )

        mouth_wide_open = mar >= self.effective_threshold

        if not mouth_wide_open:
            # Mouth closed or normal speech
            if self.active is not None:
                # Active yawn ended because mouth closed
                ended = self._end(now, captured_at, 'closed')
                self.yawn_count += 1

            self.consecutive_frames = 0
            state = 'closed'
        else:
            # Mouth is open beyond MAR threshold
            self.consecutive_frames += 1
            if self.consecutive_frames >= self.config.yawn_consec_frames:
                state = 'yawning'
                if self.active is None:
                    # Yawn confirmed after passing debounce frames
                    alert_allowed = cooldown_remaining <= 0.0
                    if alert_allowed:
                        self._last_alert = now
                        cooldown_remaining = self.config.cooldown_seconds
                    self._start_time = now
                    self.active = YawnEvent(
                        id=self._next_id,
                        started_at=captured_at,
                        mar=mar,
                        alert_allowed=alert_allowed
                    )
                    started = self.active
                    self._next_id += 1
                else:
                    # Ongoing yawn duration update
                    duration = max(0.0, now - (self._start_time or now))
                    self.active = replace(self.active, duration_seconds=duration)
            else:
                state = 'opening'

        return YawnUpdate(
            state=state,
            consecutive_frames=self.consecutive_frames,
            mar=mar,
            yawn_count=self.yawn_count,
            active=self.active,
            started=started,
            ended=ended,
            cooldown_remaining=cooldown_remaining,
            calibrated=self.calibrated,
            calibration_progress=calib_progress,
            baseline_mar=self.baseline_mar,
            effective_threshold=self.effective_threshold
        )
