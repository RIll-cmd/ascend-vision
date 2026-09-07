"""Real-time facial expression tracking and debouncing using MediaPipe blendshapes."""
from dataclasses import dataclass
import logging
from typing import Optional

LOG = logging.getLogger(__name__)


@dataclass
class _ExpressionState:
    active_since: Optional[float] = None
    duration: float = 0.0
    triggered_in_streak: bool = False

    def reset(self):
        self.active_since = None
        self.duration = 0.0
        self.triggered_in_streak = False


class ExpressionTracker:
    """Aggregates blendshape scores and detects sustained facial expressions with debouncing and cooldowns."""

    def __init__(
        self,
        cooldown_seconds: float = 45.0,
        yawn_threshold: float = 0.60,
        yawn_duration: float = 1.0,
        smile_threshold: float = 0.45,
        smile_duration: float = 1.2,
        frown_threshold: float = 0.40,
        frown_duration: float = 2.0,
        phone_threshold_duration: float = 2.0,
        phone_cooldown_seconds: float = 60.0,
    ):
        self.cooldown_seconds = float(cooldown_seconds)
        self.yawn_threshold = float(yawn_threshold)
        self.yawn_duration = float(yawn_duration)
        self.smile_threshold = float(smile_threshold)
        self.smile_duration = float(smile_duration)
        self.frown_threshold = float(frown_threshold)
        self.frown_duration = float(frown_duration)
        self.phone_threshold_duration = float(phone_threshold_duration)
        self.phone_cooldown_seconds = float(phone_cooldown_seconds)

        self._states = {
            'fatigue': _ExpressionState(),
            'stressed': _ExpressionState(),
            'smiling': _ExpressionState(),
            'phone_detected': _ExpressionState(),
            'PHONE_CALL': _ExpressionState(),
            'PHONE_SCROLLING': _ExpressionState(),
            'PHONE_GAMING': _ExpressionState(),
            'PHONE_USE': _ExpressionState(),
        }
        self._last_trigger_time: Optional[float] = None
        self._last_phone_trigger_time: Optional[float] = None
        self._last_posture_trigger_times: dict[str, float] = {}
        self._current_emotion: str = 'neutral'
        self._last_blendshapes: dict[str, float] = {}

    @property
    def current_emotion(self) -> str:
        """The currently active instantaneous or sustained emotion: neutral, fatigue, stressed, smiling."""
        return self._current_emotion

    @property
    def last_trigger_time(self) -> Optional[float]:
        return self._last_trigger_time

    @property
    def last_blendshapes(self) -> dict[str, float]:
        return self._last_blendshapes

    def get_duration(self, emotion: str) -> float:
        state = self._states.get(emotion) or self._states.get(emotion.upper()) or self._states.get(emotion.lower())
        return state.duration if state else 0.0

    def reset(self):
        for state in self._states.values():
            state.reset()
        self._current_emotion = 'neutral'
        self._last_blendshapes = {}

    def update_phone(self, phone_detected: bool, timestamp: float) -> Optional[str]:
        """Tracks phone presence sustained for >= phone_threshold_duration (2.0s).

        Enforces phone_cooldown_seconds (60.0s) between voice alerts.
        Returns 'phone_detected' if trigger fires, else None.
        """
        self._update_state('phone_detected', phone_detected, timestamp)
        state = self._states['phone_detected']
        if phone_detected and state.duration >= self.phone_threshold_duration and not state.triggered_in_streak:
            state.triggered_in_streak = True
            cooldown_elapsed = (
                self._last_phone_trigger_time is None
                or (timestamp - self._last_phone_trigger_time >= self.phone_cooldown_seconds)
            )
            if cooldown_elapsed:
                self._last_phone_trigger_time = timestamp
                LOG.info(
                    'Phone detection trigger fired: phone_detected (duration=%.2fs, timestamp=%.2f)',
                    state.duration, timestamp
                )
                return 'phone_detected'
            else:
                LOG.debug(
                    'Phone trigger suppressed by cooldown (remaining=%.1fs)',
                    self.phone_cooldown_seconds - (timestamp - self._last_phone_trigger_time)
                )
        return None

    def update_phone_posture(self, phone_posture: Optional[str], timestamp: float) -> Optional[str]:
        """Tracks specific phone posture (PHONE_CALL, PHONE_SCROLLING, PHONE_GAMING, PHONE_USE).

        Debounces posture for >= phone_threshold_duration (2.0s) continuous hold.
        Maintains per-event cooldown (60.0s).
        Returns the posture string if trigger fires, else None.
        """
        postures = ('PHONE_CALL', 'PHONE_SCROLLING', 'PHONE_GAMING', 'PHONE_USE')
        active_norm = phone_posture.strip().upper() if phone_posture and phone_posture.strip().upper() in postures else None

        for p in postures:
            self._update_state(p, (p == active_norm), timestamp)

        if active_norm is None:
            return None

        state = self._states[active_norm]
        if state.duration >= self.phone_threshold_duration and not state.triggered_in_streak:
            state.triggered_in_streak = True
            last_time = self._last_posture_trigger_times.get(active_norm)
            cooldown_elapsed = (
                last_time is None
                or (timestamp - last_time >= self.phone_cooldown_seconds)
            )
            if cooldown_elapsed:
                self._last_posture_trigger_times[active_norm] = timestamp
                self._last_phone_trigger_time = timestamp
                LOG.info(
                    'Phone posture trigger fired: %s (duration=%.2fs, timestamp=%.2f)',
                    active_norm, state.duration, timestamp
                )
                return active_norm
            else:
                LOG.debug(
                    'Phone posture trigger %s suppressed by cooldown (remaining=%.1fs)',
                    active_norm, self.phone_cooldown_seconds - (timestamp - last_time)
                )
        return None

    def update(
        self,
        blendshapes: Optional[dict[str, float]],
        timestamp: float,
        phone_detected: bool = False,
        phone_posture: Optional[str] = None
    ) -> Optional[str]:
        """Processes current frame's blendshapes and phone detection.

        Returns a trigger string if a condition fires, else None:
            - 'PHONE_SCROLLING', 'PHONE_GAMING', 'PHONE_CALL', 'PHONE_USE'
            - 'phone_detected'
            - 'fatigue'
            - 'stressed'
            - 'smiling'
        """
        if phone_posture is not None:
            posture_trigger = self.update_phone_posture(phone_posture, timestamp)
            is_present = (phone_posture != 'NONE' and bool(phone_posture)) or phone_detected
            self._update_state('phone_detected', is_present, timestamp)
            if posture_trigger is not None:
                return posture_trigger
        else:
            phone_trigger = self.update_phone(phone_detected, timestamp)
            if phone_trigger is not None:
                return phone_trigger

        if not blendshapes:
            for k in ('fatigue', 'stressed', 'smiling'):
                self._states[k].reset()
            self._current_emotion = 'neutral'
            self._last_blendshapes = {}
            return None

        self._last_blendshapes = blendshapes

        jaw_open = blendshapes.get('jawOpen', 0.0)
        smile_left = blendshapes.get('mouthSmileLeft', 0.0)
        smile_right = blendshapes.get('mouthSmileRight', 0.0)
        brow_down_left = blendshapes.get('browDownLeft', 0.0)
        brow_down_right = blendshapes.get('browDownRight', 0.0)

        is_yawning = jaw_open > self.yawn_threshold
        is_smiling = (smile_left > self.smile_threshold) and (smile_right > self.smile_threshold)
        is_frowning = (brow_down_left > self.frown_threshold) or (brow_down_right > self.frown_threshold)

        # Update rolling duration timers
        self._update_state('fatigue', is_yawning, timestamp)
        self._update_state('stressed', is_frowning, timestamp)
        self._update_state('smiling', is_smiling, timestamp)

        # Instantaneous emotion categorization
        if is_yawning:
            self._current_emotion = 'fatigue'
        elif is_frowning:
            self._current_emotion = 'stressed'
        elif is_smiling:
            self._current_emotion = 'smiling'
        else:
            self._current_emotion = 'neutral'

        # Check triggers in priority order: fatigue -> stressed -> smiling
        checks = [
            ('fatigue', is_yawning, self.yawn_duration),
            ('stressed', is_frowning, self.frown_duration),
            ('smiling', is_smiling, self.smile_duration),
        ]

        for emotion, active, required_dur in checks:
            state = self._states[emotion]
            if active and state.duration >= required_dur and not state.triggered_in_streak:
                state.triggered_in_streak = True
                cooldown_elapsed = (
                    self._last_trigger_time is None
                    or (timestamp - self._last_trigger_time >= self.cooldown_seconds)
                )
                if cooldown_elapsed:
                    self._last_trigger_time = timestamp
                    LOG.info(
                        'Expression trigger fired: %s (duration=%.2fs, timestamp=%.2f)',
                        emotion, state.duration, timestamp
                    )
                    return emotion
                else:
                    LOG.debug(
                        'Expression trigger suppressed by cooldown: %s (remaining=%.1fs)',
                        emotion, self.cooldown_seconds - (timestamp - self._last_trigger_time)
                    )

        return None

    def _update_state(self, emotion: str, active: bool, timestamp: float):
        state = self._states[emotion]
        if active:
            if state.active_since is None:
                state.active_since = timestamp
                state.duration = 0.0
            else:
                state.duration = max(0.0, timestamp - state.active_since)
        else:
            state.reset()
