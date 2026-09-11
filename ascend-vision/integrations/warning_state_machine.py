"""Warning-First State Machine for CV detector observations and repeat bad habit offenses."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Any, Callable, Optional

LOG = logging.getLogger(__name__)


class SensoryTriggerType(str, Enum):
    PHONE = "phone_usage"
    SLOUCH = "slouching"
    FATIGUE = "fatigue"


@dataclass(frozen=True)
class BadHabitConfig:
    warning_text: str
    habit_name: str
    penalty_stat: str = "HP"
    penalty_amount: int = 10
    category: str = "DISCIPLINE"


TRIGGER_CONFIGS: dict[SensoryTriggerType, BadHabitConfig] = {
    SensoryTriggerType.PHONE: BadHabitConfig(
        warning_text="Warning: Phone distraction detected. Focus on the task.",
        habit_name="Doomscrolling",
        penalty_stat="HP",
        penalty_amount=10,
        category="DISCIPLINE",
    ),
    SensoryTriggerType.SLOUCH: BadHabitConfig(
        warning_text="Warning: Slouching detected. Please correct your posture.",
        habit_name="Bad Posture",
        penalty_stat="HP",
        penalty_amount=10,
        category="HEALTH",
    ),
    SensoryTriggerType.FATIGUE: BadHabitConfig(
        warning_text="Warning: Fatigue detected. Take a breather or stay alert.",
        habit_name="Staying Up Late",
        penalty_stat="HP",
        penalty_amount=10,
        category="HEALTH",
    ),
}


class WarningFirstStateMachine:
    """Implements a two-strike Warning-First State Machine for computer vision detectors:

    - Offense 1: Issue localized warning chime/voice alert and start 5-minute timer.
    - Offense 2 (Within 5 minutes): Call Core composite bad habit recorder and speak canonical narration.
    """

    REPEAT_WINDOW_SECONDS = 300.0  # 5 minutes
    DEBOUNCE_SECONDS = 15.0        # Minimum gap between processing the same trigger type

    def __init__(
        self,
        core_client=None,
        feedback_service=None,
        async_runner=None,
        *,
        repeat_window_seconds: float = REPEAT_WINDOW_SECONDS,
        debounce_seconds: float = DEBOUNCE_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self.core_client = core_client
        self.feedback_service = feedback_service
        self.async_runner = async_runner
        self.repeat_window_seconds = repeat_window_seconds
        self.debounce_seconds = debounce_seconds
        self._time_fn = time_fn

        # Per-trigger tracking: {trigger: (last_warning_time, last_trigger_time)}
        self._warning_times: dict[SensoryTriggerType, float | None] = {
            t: None for t in SensoryTriggerType
        }
        self._last_trigger_times: dict[SensoryTriggerType, float] = {
            t: float("-inf") for t in SensoryTriggerType
        }

    def reset(self) -> None:
        """Reset all warning states."""
        for t in SensoryTriggerType:
            self._warning_times[t] = None
            self._last_trigger_times[t] = float("-inf")

    def handle_trigger(
        self, trigger: SensoryTriggerType, now: Optional[float] = None
    ) -> dict[str, Any] | None:
        """Process a sensory detector event through the warning-first state machine.

        Returns:
            dict with offense details, or None if debounced.
        """
        current_time = self._time_fn() if now is None else now
        cfg = TRIGGER_CONFIGS.get(trigger)
        if cfg is None:
            LOG.warning("Unknown sensory trigger type: %s", trigger)
            return None

        # Debounce check to prevent multiple rapid triggers on a continuous gesture
        last_triggered = self._last_trigger_times[trigger]
        if current_time - last_triggered < self.debounce_seconds:
            LOG.debug("Debouncing trigger %s (%.1fs < %.1fs)", trigger, current_time - last_triggered, self.debounce_seconds)
            return None

        self._last_trigger_times[trigger] = current_time
        last_warning = self._warning_times[trigger]

        # Case 1: Offense 1 (First Warning, or previous warning expired past 5 minutes)
        if last_warning is None or (current_time - last_warning) > self.repeat_window_seconds:
            self._warning_times[trigger] = current_time
            LOG.info("OFFENSE 1: Trigger %s -> issuing warning alert", trigger.value)
            speak_announcement = getattr(self.feedback_service, "speak_announcement", None)
            if speak_announcement is not None:
                speak_announcement(cfg.warning_text)
            return {
                "offense": 1,
                "trigger": trigger.value,
                "warning": cfg.warning_text,
                "timestamp": current_time,
            }

        # Case 2: Offense 2 (Repeat Offense within 5 minutes)
        time_since_warning = current_time - last_warning
        LOG.info(
            "OFFENSE 2: Trigger %s repeated within %.1fs (window=%.1fs) -> penalizing bad habit '%s'",
            trigger.value,
            time_since_warning,
            self.repeat_window_seconds,
            cfg.habit_name,
        )
        # Reset warning state so the next detection after cooldown starts a fresh cycle
        self._warning_times[trigger] = None

        self._dispatch_bad_habit_offense(cfg)

        return {
            "offense": 2,
            "trigger": trigger.value,
            "habit_name": cfg.habit_name,
            "penalty_stat": cfg.penalty_stat,
            "penalty_amount": cfg.penalty_amount,
            "category": cfg.category,
            "time_since_warning": time_since_warning,
            "timestamp": current_time,
        }

    def _dispatch_bad_habit_offense(self, cfg: BadHabitConfig) -> None:
        """Schedule record_bad_habit_offense on the async runner or loop."""
        speak_announcement = getattr(self.feedback_service, "speak_announcement", None)
        if self.core_client is None:
            LOG.warning("Ascend Core client is not configured; cannot record bad habit offense.")
            if speak_announcement is not None:
                speak_announcement(
                    f"Repeat offense detected: {cfg.habit_name}. Maintain discipline."
                )
            return

        async def _record_and_speak():
            try:
                result = await self.core_client.record_bad_habit_offense(
                    name=cfg.habit_name,
                    penalty_stat=cfg.penalty_stat,
                    penalty_amount=cfg.penalty_amount,
                    category=cfg.category,
                )
                canonical_narration = result.get("canonicalNarration")
                if canonical_narration and speak_announcement is not None:
                    LOG.info("Offense recorded. Speaking canonical narration: %s", canonical_narration)
                    speak_announcement(canonical_narration)
                return result
            except Exception as exc:
                LOG.error("Failed to record bad habit offense in Core: %s", exc)
                if speak_announcement is not None:
                    speak_announcement(
                        f"Protocol breached for {cfg.habit_name}. Maintain vigilance."
                    )
                return None

        if self.async_runner is not None:
            self.async_runner.submit(_record_and_speak())
        else:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_record_and_speak())
            except RuntimeError:
                asyncio.run(_record_and_speak())
