"""Real-time poor posture & forward head posture (FHP) detector.

Inspired by hasanpeal/PostureCoach.
Derives neck flexion and vertical head drop from MediaPipe Face Mesh landmarks,
supports automatic baseline calibration, and runs a pure deterministic state machine.
"""
from dataclasses import dataclass, replace
from datetime import datetime
import math

from config import PostureConfig

# Landmark indices from MediaPipe Face Mesh
FOREHEAD_IDX = 10
NOSE_TIP_IDX = 1
CHIN_IDX = 152
LEFT_EAR_IDX = 234
RIGHT_EAR_IDX = 454


def calculate_face_pitch(face_landmarks: list[tuple[float, float, float] | tuple[float, float]]) -> float:
    """Calculate 3D face pitch angle in degrees from forehead to chin.

    Positive pitch indicates forward/downward head tilt (neck flexion).
    """
    if not face_landmarks or len(face_landmarks) <= max(FOREHEAD_IDX, CHIN_IDX, NOSE_TIP_IDX):
        return 0.0

    p_forehead = face_landmarks[FOREHEAD_IDX]
    p_chin = face_landmarks[CHIN_IDX]

    dy = p_chin[1] - p_forehead[1]
    if len(p_forehead) > 2 and len(p_chin) > 2:
        dz = p_chin[2] - p_forehead[2]
    else:
        dz = 0.0

    # If 3D z coordinates are present and meaningful:
    if abs(dz) > 1e-5 or abs(dy) > 1e-5:
        return math.degrees(math.atan2(dz, max(1e-6, abs(dy))))

    # Fallback to 2D projected nose-to-chin vertical compression ratio
    p_nose = face_landmarks[NOSE_TIP_IDX]
    total_h = max(1e-6, p_chin[1] - p_forehead[1])
    nose_offset = (p_nose[1] - p_forehead[1]) / total_h
    # Normal upright nose ratio is ~0.60; looking down shifts it to ~0.75+
    return (nose_offset - 0.60) * 100.0


def calculate_normalized_head_drop(face_landmarks: list[tuple[float, float, float] | tuple[float, float]]) -> float:
    """Calculate vertical head position normalized by face height.

    Invariant to user distance from camera. Sinking into chair increases this value.
    """
    if not face_landmarks or len(face_landmarks) <= max(FOREHEAD_IDX, CHIN_IDX, NOSE_TIP_IDX):
        return 0.0

    p_forehead = face_landmarks[FOREHEAD_IDX]
    p_chin = face_landmarks[CHIN_IDX]
    p_nose = face_landmarks[NOSE_TIP_IDX]

    face_h = math.hypot(p_chin[0] - p_forehead[0], p_chin[1] - p_forehead[1])
    if face_h < 1e-6:
        return 0.0

    # Center-of-head Y coordinate normalized by face height
    return p_nose[1] / face_h


class PostureCalibrator:
    """Collects initial upright frames to establish individualized posture baselines."""

    def __init__(self, target_frames: int = 30):
        self.target_frames = max(5, target_frames)
        self._pitch_samples: list[float] = []
        self._drop_samples: list[float] = []
        self.calibrated = False
        self.baseline_pitch = 0.0
        self.baseline_drop = 0.0

    @property
    def progress(self) -> float:
        return min(1.0, len(self._pitch_samples) / self.target_frames)

    def update(self, face_landmarks: list) -> bool:
        if self.calibrated:
            return True

        if not face_landmarks or len(face_landmarks) < 153:
            return False

        pitch = calculate_face_pitch(face_landmarks)
        drop = calculate_normalized_head_drop(face_landmarks)

        if math.isfinite(pitch) and math.isfinite(drop) and drop > 0.0:
            self._pitch_samples.append(pitch)
            self._drop_samples.append(drop)

        if len(self._pitch_samples) >= self.target_frames:
            self.baseline_pitch = sum(self._pitch_samples) / len(self._pitch_samples)
            self.baseline_drop = sum(self._drop_samples) / len(self._drop_samples)
            self.calibrated = True

        return self.calibrated

    def set_baseline(self, pitch: float, drop: float):
        """Explicitly inject baseline (useful for testing or stored user profiles)."""
        self.baseline_pitch = pitch
        self.baseline_drop = drop
        self.calibrated = True

    def recalibrate(self):
        self._pitch_samples.clear()
        self._drop_samples.clear()
        self.calibrated = False
        self.baseline_pitch = 0.0
        self.baseline_drop = 0.0


@dataclass(frozen=True)
class PostureEvent:
    id: int
    started_at: datetime
    slouch_score: float
    alert_allowed: bool
    duration_seconds: float = 0.0
    ended_at: datetime | None = None
    end_reason: str | None = None

    @property
    def reason(self) -> str | None:
        return self.end_reason


@dataclass(frozen=True)
class PostureUpdate:
    state: str  # 'calibrating', 'upright', 'slouching', 'slouched'
    consecutive_frames: int
    slouch_score: float
    is_slouched: bool
    calibrated: bool
    calibration_progress: float
    active: PostureEvent | None
    started: PostureEvent | None
    ended: PostureEvent | None


class PostureMachine:
    """State machine for debounced poor posture & slouch detection."""

    def __init__(self, config: PostureConfig):
        self.config = config
        self.calibrator = PostureCalibrator(config.calibration_frames)
        self.consecutive_frames = 0
        self.active: PostureEvent | None = None
        self._start_time: float | None = None
        self._last_alert: float | None = None
        self._next_id = 1
        self._last_face_time: float | None = None

    def update(self, face_landmarks: list, now: float, captured_at: datetime) -> PostureUpdate:
        if not self.config.enabled:
            return PostureUpdate(
                state='disabled',
                consecutive_frames=0,
                slouch_score=0.0,
                is_slouched=False,
                calibrated=True,
                calibration_progress=1.0,
                active=None,
                started=None,
                ended=None
            )

        # 1. Check/update calibration
        if not self.calibrator.calibrated:
            self.calibrator.update(face_landmarks)
            return PostureUpdate(
                state='calibrating',
                consecutive_frames=0,
                slouch_score=0.0,
                is_slouched=False,
                calibrated=self.calibrator.calibrated,
                calibration_progress=self.calibrator.progress,
                active=None,
                started=None,
                ended=None
            )

        started = None
        ended = None

        # 2. Handle face lost
        if not face_landmarks or len(face_landmarks) < 153:
            if self.active is not None:
                ended = replace(
                    self.active,
                    duration_seconds=max(0.0, now - (self._start_time or now)),
                    ended_at=captured_at,
                    end_reason='face_lost'
                )
                self.active = None
                self._start_time = None
            self.consecutive_frames = 0
            return PostureUpdate(
                state='upright',
                consecutive_frames=0,
                slouch_score=0.0,
                is_slouched=False,
                calibrated=True,
                calibration_progress=1.0,
                active=None,
                started=None,
                ended=ended
            )

        self._last_face_time = now

        # 3. Compute posture metrics
        pitch = calculate_face_pitch(face_landmarks)
        head_drop = calculate_normalized_head_drop(face_landmarks)

        delta_pitch = pitch - self.calibrator.baseline_pitch
        delta_drop = head_drop - self.calibrator.baseline_drop

        # Poor posture condition: head drops downward OR neck bends forward
        is_slouched = (delta_drop > self.config.head_drop_threshold) or (delta_pitch > self.config.pitch_threshold_deg)

        score_drop = max(0.0, delta_drop / max(1e-6, self.config.head_drop_threshold))
        score_pitch = max(0.0, delta_pitch / max(1e-6, self.config.pitch_threshold_deg))
        slouch_score = round(max(score_drop, score_pitch), 2)

        # 4. State transitions & debounce
        if is_slouched:
            self.consecutive_frames += 1
            if self.consecutive_frames < self.config.consec_frames:
                state = 'slouching'
            else:
                state = 'slouched'
                if self.active is None:
                    alert_allowed = (self._last_alert is None or (now - self._last_alert >= self.config.cooldown_seconds))
                    if alert_allowed:
                        self._last_alert = now
                    self.active = PostureEvent(
                        id=self._next_id,
                        started_at=captured_at,
                        slouch_score=slouch_score,
                        alert_allowed=alert_allowed
                    )
                    self._start_time = now
                    self._next_id += 1
                    started = self.active
                else:
                    # Update active duration
                    self.active = replace(
                        self.active,
                        duration_seconds=max(0.0, now - (self._start_time or now)),
                        slouch_score=max(self.active.slouch_score, slouch_score)
                    )
        else:
            state = 'upright'
            self.consecutive_frames = 0
            if self.active is not None:
                ended = replace(
                    self.active,
                    duration_seconds=max(0.0, now - (self._start_time or now)),
                    ended_at=captured_at,
                    end_reason='posture_restored'
                )
                self.active = None
                self._start_time = None

        return PostureUpdate(
            state=state,
            consecutive_frames=self.consecutive_frames,
            slouch_score=slouch_score,
            is_slouched=is_slouched,
            calibrated=True,
            calibration_progress=1.0,
            active=self.active,
            started=started,
            ended=ended
        )

    def finish(self, reason: str = 'shutdown') -> PostureEvent | None:
        ended = None
        if self.active is not None:
            ended = replace(
                self.active,
                ended_at=self.active.started_at,
                end_reason=reason
            )
            self.active = None
            self._start_time = None
        return ended
