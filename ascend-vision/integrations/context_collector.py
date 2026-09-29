"""Bridge the existing laptop sensors into a small, non-persistent snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assistant.context_runtime import MAX_FRESHNESS_SECONDS, ContextRuntime, ObservationEnvelope
from integrations.desk_presence import PresenceDebouncer
from integrations.desktop_activity import WindowsActivityAdapter


class CompanionContextCollector:
    def __init__(self, runtime: ContextRuntime, desktop_adapter: WindowsActivityAdapter,
                 *, desktop_poll_seconds: float = 2.0, absence_dwell_seconds: float = 10.0,
                 return_dwell_seconds: float = 3.0,
                 now_utc=lambda: datetime.now(timezone.utc)):
        if desktop_poll_seconds <= 0:
            raise ValueError("desktop_poll_seconds must be positive")
        self.runtime = runtime
        self.desktop_adapter = desktop_adapter
        self.desktop_poll_seconds = float(desktop_poll_seconds)
        self._now_utc = now_utc
        self._presence = PresenceDebouncer(
            absence_seconds=absence_dwell_seconds,
            return_seconds=return_dwell_seconds,
        )
        self._last_desktop_sample: float | None = None
        self._sequence = {"webcam": 0, "desktop_activity": 0, "session_runtime": 0}
        self._last_session_sample: float | None = None

    def _publish(self, source: str, kind: str, value: str, *, source_available: bool = True) -> str:
        observed_at = self._now_utc().astimezone(timezone.utc)
        self._sequence[source] += 1
        sequence = self._sequence[source]
        event = ObservationEnvelope(
            schema_version=1,
            event_id=f"{self.runtime.boot_id}:{source}:{sequence}",
            source=source,
            kind=kind,
            value=value,
            boot_id=self.runtime.boot_id,
            sequence=sequence,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(seconds=MAX_FRESHNESS_SECONDS[kind]),
            source_available=source_available,
        )
        return self.runtime.enqueue_observation(event)

    def observe_face(self, face_present: bool, monotonic_time: float, *,
                     source_available: bool = True) -> str:
        if self.runtime.paused:
            return "unknown"
        if self.runtime.desk_region is None or self.runtime.desk_calibrating:
            source_available = False
        state = self._presence.update(
            face_present, monotonic_time, source_available=source_available,
        )
        self._publish("webcam", "deskPresence", state, source_available=source_available)
        return state

    def sample_desktop(self, monotonic_time: float) -> bool:
        if self.runtime.paused:
            return False
        if (self._last_desktop_sample is not None
                and monotonic_time - self._last_desktop_sample < self.desktop_poll_seconds):
            return False
        self._last_desktop_sample = monotonic_time
        sample = self.desktop_adapter.sample()
        self._publish(
            "desktop_activity", "desktopActivity", sample.desktop_activity,
            source_available=sample.available,
        )
        self._publish(
            "desktop_activity", "foregroundCategory", sample.foreground_category,
            source_available=sample.available,
        )
        return True

    def sample_session_mode(self, mode: str, monotonic_time: float) -> bool:
        if self.runtime.paused:
            return False
        if (self._last_session_sample is not None
                and monotonic_time - self._last_session_sample < self.desktop_poll_seconds):
            return False
        if mode not in {"focus", "background"}:
            mode = "unavailable"
        self._last_session_sample = monotonic_time
        self._publish("session_runtime", "focusSession", mode)
        return True
