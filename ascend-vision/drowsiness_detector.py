"""Eye Aspect Ratio (EAR) computation and eye closure/drowsiness state machine.

Implements the Eye Aspect Ratio metric (Soukupova and Cech, 2016) using
canonical MediaPipe Face Mesh landmark indices, differentiating brief natural
blinks from sustained eye closure (microsleep/drowsiness).
"""
from dataclasses import dataclass, replace
from datetime import datetime
import math

from config import DrowsinessConfig

# Canonical MediaPipe Face Mesh landmark indices for eyes
# Left eye (p1: outer, p2: top-outer, p3: top-inner, p4: inner, p5: bottom-inner, p6: bottom-outer)
LEFT_EYE_LANDMARKS = (33, 160, 158, 133, 153, 144)

# Right eye (p1: outer, p2: top-outer, p3: top-inner, p4: inner, p5: bottom-inner, p6: bottom-outer)
RIGHT_EYE_LANDMARKS = (362, 385, 387, 263, 373, 380)


def eye_aspect_ratio(eye_points: list[tuple[float, float, float] | tuple[float, float]]) -> float:
    """Compute Eye Aspect Ratio (EAR) for 6 ordered eye landmark points.

    Formula:
        EAR = (||p2 - p6|| + ||p3 - p5||) / (2 * ||p1 - p4||)
    """
    if len(eye_points) != 6:
        raise ValueError(f'Expected 6 eye points, got {len(eye_points)}')
    for pt in eye_points:
        if not math.isfinite(pt[0]) or not math.isfinite(pt[1]):
            return 0.0

    p1, p2, p3, p4, p5, p6 = eye_points[:6]

    # Vertical eyelid distances
    v1 = math.hypot(p2[0] - p6[0], p2[1] - p6[1])
    v2 = math.hypot(p3[0] - p5[0], p3[1] - p5[1])

    # Horizontal corner-to-corner distance
    h = math.hypot(p1[0] - p4[0], p1[1] - p4[1])

    if h < 1e-6:
        return 0.0

    return (v1 + v2) / (2.0 * h)


def calculate_face_ear(face_landmarks: list[tuple[float, float, float] | tuple[float, float]]) -> tuple[float, float, float]:
    """Extract left, right, and average EAR from 468+ face landmarks.

    Returns:
        (left_ear, right_ear, avg_ear). If face_landmarks is empty or invalid, returns (0., 0., 0.).
    """
    if not face_landmarks or len(face_landmarks) < 388:
        return 0.0, 0.0, 0.0

    left_points = [face_landmarks[i] for i in LEFT_EYE_LANDMARKS]
    right_points = [face_landmarks[i] for i in RIGHT_EYE_LANDMARKS]

    left_ear = eye_aspect_ratio(left_points)
    right_ear = eye_aspect_ratio(right_points)
    avg_ear = (left_ear + right_ear) / 2.0

    return left_ear, right_ear, avg_ear


@dataclass(frozen=True)
class DrowsinessEvent:
    id: int
    started_at: datetime
    ear: float
    alert_allowed: bool
    duration_seconds: float = 0.0
    ended_at: datetime | None = None
    end_reason: str | None = None

    @property
    def reason(self) -> str | None:
        return self.end_reason


@dataclass(frozen=True)
class DrowsinessUpdate:
    state: str  # 'awake', 'closing', 'drowsy'
    consecutive_frames: int
    ear: float
    blink_count: int
    active: DrowsinessEvent | None
    started: DrowsinessEvent | None
    ended: DrowsinessEvent | None
    cooldown_remaining: float
    calibrated: bool = True
    calibration_progress: float = 1.0
    baseline_ear: float = 0.0
    effective_threshold: float = 0.22


class DrowsinessMachine:
    """Deterministic eye closure and drowsiness state machine with adaptive baseline calibration.

    Pure transition logic: no I/O, no wall-clock sleep, fully replayable.
    """

    def __init__(self, config: DrowsinessConfig):
        self.config = config
        self.consecutive_frames = 0
        self.blink_count = 0
        self.active: DrowsinessEvent | None = None
        self._start_time: float | None = None
        self._last_time: float | None = None
        self._last_wall: datetime | None = None
        self._last_alert: float | None = None
        self._next_id = 1
        self._ear_samples: list[float] = []
        self.baseline_ear: float = 0.0
        self.calibrated = (getattr(config, 'calibration_frames', 30) <= 0)
        self.effective_threshold: float = config.ear_threshold

    def _end(self, now: float | None, wall: datetime | None, reason: str) -> DrowsinessEvent | None:
        ended = None
        if self.active is not None:
            duration = max(0.0, (now or 0.0) - (self._start_time or 0.0))
            ended = replace(self.active, duration_seconds=duration, ended_at=wall, end_reason=reason)
        self.active = None
        self._start_time = None
        return ended

    def finish(self, reason: str = 'shutdown') -> DrowsinessEvent | None:
        """Close at last observed timestamp."""
        ended = self._end(self._last_time, self._last_wall, reason)
        self.consecutive_frames = 0
        return ended

    def update(self, face_landmarks, now: float, captured_at: datetime) -> DrowsinessUpdate:
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
        calib_progress = 1.0 if calib_target <= 0 else min(1.0, len(self._ear_samples) / calib_target)

        if not face_landmarks:
            # Face not visible / lost: reset closure counter and close any active episode
            if self.consecutive_frames > 0 and self.consecutive_frames < self.config.closed_consec_frames:
                # Interrupted closure before drowsiness threshold
                self.consecutive_frames = 0
            ended = self._end(now, captured_at, 'face_lost')
            self.consecutive_frames = 0
            return DrowsinessUpdate(
                state='awake',
                consecutive_frames=0,
                ear=0.0,
                blink_count=self.blink_count,
                active=self.active,
                started=None,
                ended=ended,
                cooldown_remaining=cooldown_remaining,
                calibrated=self.calibrated,
                calibration_progress=calib_progress,
                baseline_ear=self.baseline_ear,
                effective_threshold=self.effective_threshold
            )

        _, _, avg_ear = calculate_face_ear(face_landmarks)

        # Handle calibration phase
        if not self.calibrated and calib_target > 0:
            if math.isfinite(avg_ear) and avg_ear > 0.05:
                self._ear_samples.append(avg_ear)
            calib_progress = min(1.0, len(self._ear_samples) / calib_target)
            if len(self._ear_samples) >= calib_target:
                self.baseline_ear = float(sum(self._ear_samples) / len(self._ear_samples))
                ratio = getattr(self.config, 'adaptive_threshold_ratio', 0.70)
                # Bound between 0.15 and 0.35 to guard against outliers
                self.effective_threshold = float(max(0.15, min(0.35, self.baseline_ear * ratio)))
                self.calibrated = True
            else:
                return DrowsinessUpdate(
                    state='calibrating',
                    consecutive_frames=0,
                    ear=avg_ear,
                    blink_count=self.blink_count,
                    active=None,
                    started=None,
                    ended=None,
                    cooldown_remaining=cooldown_remaining,
                    calibrated=self.calibrated,
                    calibration_progress=calib_progress,
                    baseline_ear=self.baseline_ear,
                    effective_threshold=self.effective_threshold
                )

        eyes_closed = avg_ear < self.effective_threshold

        if not eyes_closed:
            # Eyes are open
            if 0 < self.consecutive_frames < self.config.closed_consec_frames:
                # Natural blink completed!
                self.blink_count += 1

            if self.active is not None:
                ended = self._end(now, captured_at, 'reopened')

            self.consecutive_frames = 0
            state = 'awake'
        else:
            # Eyes are closed
            self.consecutive_frames += 1
            if self.consecutive_frames >= self.config.closed_consec_frames:
                state = 'drowsy'
                if self.active is None:
                    # Drowsiness episode begins
                    alert_allowed = cooldown_remaining <= 0.0
                    if alert_allowed:
                        self._last_alert = now
                        cooldown_remaining = self.config.cooldown_seconds
                    self._start_time = now
                    self.active = DrowsinessEvent(
                        id=self._next_id,
                        started_at=captured_at,
                        ear=avg_ear,
                        alert_allowed=alert_allowed
                    )
                    started = self.active
                    self._next_id += 1
                else:
                    # Update ongoing active episode
                    duration = max(0.0, now - (self._start_time or now))
                    self.active = replace(self.active, duration_seconds=duration)
            else:
                state = 'closing'

        return DrowsinessUpdate(
            state=state,
            consecutive_frames=self.consecutive_frames,
            ear=avg_ear,
            blink_count=self.blink_count,
            active=self.active,
            started=started,
            ended=ended,
            cooldown_remaining=cooldown_remaining,
            calibrated=self.calibrated,
            calibration_progress=calib_progress,
            baseline_ear=self.baseline_ear,
            effective_threshold=self.effective_threshold
        )
