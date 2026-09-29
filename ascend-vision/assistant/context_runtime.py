"""Bounded, in-memory current context for one Vision runtime."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from datetime import datetime, timedelta, timezone
import math
import time
import threading
from functools import wraps
from uuid import uuid4


FIELD_VALUES = {
    "deskPresence": frozenset({"present", "away", "unknown"}),
    "desktopActivity": frozenset({"input_active", "input_idle", "locked", "unavailable"}),
    "foregroundCategory": frozenset({
        "development", "communication", "browser_unspecified", "entertainment", "other", "unknown",
    }),
    "focusSession": frozenset({"focus", "background", "unavailable"}),
    "declaredIntent": frozenset({"focus", "break", "research", "meeting", "none"}),
}
FIELD_SOURCES = {
    "deskPresence": frozenset({"webcam"}),
    "desktopActivity": frozenset({"desktop_activity"}),
    "foregroundCategory": frozenset({"desktop_activity"}),
    "focusSession": frozenset({"session_runtime"}),
    "declaredIntent": frozenset({"user_declaration"}),
}
MAX_FRESHNESS_SECONDS = {
    "deskPresence": 15.0,
    "desktopActivity": 10.0,
    "foregroundCategory": 10.0,
    "focusSession": 10.0,
    "declaredIntent": 12 * 60 * 60.0,
}
DEFAULT_VALUES = {
    "deskPresence": "unknown",
    "desktopActivity": "unavailable",
    "foregroundCategory": "unknown",
    "focusSession": "unavailable",
    "declaredIntent": "none",
}
MAX_EVENT_BYTES = 4 * 1024
MAX_QUEUED_EVENTS = 256


@dataclass(frozen=True)
class DeskRegion:
    """User-selected normalized rectangle used only for local desk presence."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self):
        values = (self.left, self.top, self.right, self.bottom)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise ValueError("desk region coordinates must be finite values from 0 to 1")
        if self.right - self.left < 0.02 or self.bottom - self.top < 0.02:
            raise ValueError("desk region must be at least 2% of the frame in each dimension")

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def as_dict(self) -> dict[str, float]:
        return {"left": self.left, "top": self.top, "right": self.right, "bottom": self.bottom}

    @classmethod
    def from_drag(cls, start: tuple[int, int], end: tuple[int, int],
                  frame_width: int, frame_height: int) -> "DeskRegion":
        if (isinstance(frame_width, bool) or isinstance(frame_height, bool)
                or not isinstance(frame_width, int) or not isinstance(frame_height, int)
                or frame_width < 1 or frame_height < 1):
            raise ValueError("frame dimensions must be positive integers")
        x1, y1 = start
        x2, y2 = end
        return cls(
            left=min(x1, x2) / frame_width,
            top=min(y1, y2) / frame_height,
            right=max(x1, x2) / frame_width,
            bottom=max(y1, y2) / frame_height,
        )


def _serialized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ObservationEnvelope:
    schema_version: int
    event_id: str
    source: str
    kind: str
    value: str
    boot_id: str
    sequence: int
    observed_at: datetime
    expires_at: datetime
    evidence_kind: str = "observation"
    confidence: float | None = None
    source_available: bool = True

    def __post_init__(self):
        if self.schema_version != 1:
            raise ValueError("unsupported context schema version")
        if self.kind not in FIELD_VALUES or self.source not in FIELD_SOURCES[self.kind]:
            raise ValueError("unsupported source or context field")
        if not isinstance(self.value, str) or self.value not in FIELD_VALUES[self.kind]:
            raise ValueError("unsupported context field value")
        if not isinstance(self.event_id, str) or not 1 <= len(self.event_id) <= 128:
            raise ValueError("event_id must be between 1 and 128 characters")
        if not isinstance(self.boot_id, str) or not 1 <= len(self.boot_id) <= 128:
            raise ValueError("boot_id must be between 1 and 128 characters")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        observed = _aware_utc(self.observed_at, "observed_at")
        expires = _aware_utc(self.expires_at, "expires_at")
        lifetime = (expires - observed).total_seconds()
        if lifetime <= 0 or lifetime > MAX_FRESHNESS_SECONDS[self.kind]:
            raise ValueError("context field expiry exceeds its freshness policy")
        if self.evidence_kind not in {"observation", "user_report", "inference"}:
            raise ValueError("unsupported evidence kind")
        if type(self.source_available) is not bool:
            raise ValueError("source_available must be a boolean")
        if not self.source_available and self.value != DEFAULT_VALUES[self.kind]:
            raise ValueError("unavailable sources must publish the field's unknown value")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be a finite value between 0 and 1")
        # The event payload is intentionally a few scalar values, not arbitrary user text.
        payload_bytes = sum(len(str(part).encode("utf-8")) for part in (
            self.schema_version, self.event_id, self.source, self.kind, self.value,
            self.boot_id, self.sequence, observed.isoformat(), expires.isoformat(),
            self.evidence_kind, self.confidence, self.source_available,
        ))
        if payload_bytes > MAX_EVENT_BYTES:
            raise ValueError("context observation exceeds the 4 KiB limit")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)


@dataclass(frozen=True)
class ContextField:
    value: str
    source: str
    observed_at: datetime | None
    expires_at: datetime | None
    evidence_kind: str
    confidence: float | None
    source_available: bool
    freshness: str


@dataclass(frozen=True)
class ContextDeclaration:
    declaration_id: str
    field: str
    value: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ContextSnapshot:
    schema_version: int
    snapshot_id: str
    device_id: str
    boot_id: str
    boot_started_at: datetime
    revision: int
    source_sequences: dict[str, int]
    generated_at: datetime
    fields: dict[str, ContextField]
    paused: bool
    snooze_until: datetime | None


class ContextRuntime:
    """Reduce validated events into an expiring snapshot; never writes to disk."""

    def __init__(self, *, device_id: str, boot_id: str | None = None,
                 monotonic=time.monotonic, now_utc=lambda: datetime.now(timezone.utc),
                 desk_calibration_available: bool = False):
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            raise ValueError("device_id must be between 1 and 128 characters")
        self.device_id = device_id
        self.boot_id = boot_id or uuid4().hex
        if not isinstance(self.boot_id, str) or not 1 <= len(self.boot_id) <= 128:
            raise ValueError("boot_id must be between 1 and 128 characters")
        self._monotonic = monotonic
        self._now_utc = now_utc
        self._boot_started_at = _aware_utc(self._now_utc(), "now_utc")
        self._lock = threading.RLock()
        self._observations: dict[str, tuple[ObservationEnvelope, float]] = {}
        self._last_sequence: dict[str, int] = {}
        self._declaration: ContextDeclaration | None = None
        self._declaration_deadline: float | None = None
        self._snooze_until: datetime | None = None
        self._snooze_deadline: float | None = None
        self._paused = False
        self._revision = 0
        self._desk_region: DeskRegion | None = None
        self._desk_calibrating = False
        self._companion_decisions: list[dict] = []
        self._event_queue: deque[tuple[str, str]] = deque()
        self._queued_events: dict[tuple[str, str], ObservationEnvelope] = {}
        self._queued_sequences: dict[str, int] = {}
        self._coalesced_events = 0
        self._dropped_events = 0
        if type(desk_calibration_available) is not bool:
            raise ValueError("desk_calibration_available must be a boolean")
        self._desk_calibration_available = desk_calibration_available

    @_serialized
    def record_companion_decision(self, decision: dict) -> None:
        """Keep a bounded, transient explanation view; never store chat or prompts."""
        if not isinstance(decision, dict) or set(decision) != {"evaluated_at", "mode", "rules"}:
            raise ValueError("invalid companion decision record")
        if decision["mode"] not in {"shadow", "active"} or not isinstance(decision["rules"], list):
            raise ValueError("invalid companion decision record")
        self._companion_decisions.append(decision)
        del self._companion_decisions[:-100]

    def read_companion_decisions(self) -> list[dict]:
        with self._lock:
            return [dict(item, rules=[dict(rule) for rule in item["rules"]])
                    for item in self._companion_decisions]

    @_serialized
    def set_desk_region(self, region: DeskRegion | None) -> None:
        if region is not None and not isinstance(region, DeskRegion):
            raise TypeError("region must be a DeskRegion or None")
        self._desk_region = region
        self._desk_calibrating = False
        self._revision += 1

    @_serialized
    def begin_desk_calibration(self) -> bool:
        if self._paused or not self._desk_calibration_available:
            return False
        self._desk_calibrating = True
        self._revision += 1
        return True

    @_serialized
    def cancel_desk_calibration(self) -> None:
        self._desk_calibrating = False
        self._revision += 1

    def desk_region_status(self) -> dict:
        with self._lock:
            return {
                "available": self._desk_calibration_available,
                "calibrated": self._desk_region is not None,
                "calibrating": self._desk_calibrating,
                "region": self._desk_region.as_dict() if self._desk_region else None,
            }

    @property
    def desk_region(self) -> DeskRegion | None:
        with self._lock:
            return self._desk_region

    @property
    def desk_calibrating(self) -> bool:
        with self._lock:
            return self._desk_calibrating

    @_serialized
    def accept(self, event: ObservationEnvelope) -> str:
        self._drain_event_queue()
        return self._apply_event(event)

    @_serialized
    def enqueue_observation(self, event: ObservationEnvelope) -> str:
        """Queue sensor evidence for bounded reduction; control APIs remain synchronous."""
        if not isinstance(event, ObservationEnvelope):
            raise TypeError("event must be an ObservationEnvelope")
        if self._paused:
            return "paused"
        if event.boot_id != self.boot_id:
            return "wrong_boot"
        last_sequence = max(self._last_sequence.get(event.source, -1),
                            self._queued_sequences.get(event.source, -1))
        if event.sequence == last_sequence:
            return "duplicate"
        if event.sequence < last_sequence:
            return "stale"
        now = _aware_utc(self._now_utc(), "now_utc")
        if event.observed_at > now + timedelta(seconds=5):
            return "future_timestamp"
        key = (event.source, event.kind)
        existing = self._queued_events.get(key)
        if existing is None and len(self._event_queue) >= MAX_QUEUED_EVENTS:
            self._dropped_events += 1
            return "queue_full"
        if existing is not None:
            self._event_queue.remove(key)
            self._coalesced_events += 1
        self._event_queue.append(key)
        self._queued_events[key] = event
        self._queued_sequences[event.source] = event.sequence
        return "coalesced" if existing is not None else "queued"

    def event_queue_status(self) -> dict[str, int]:
        with self._lock:
            return {"capacity": MAX_QUEUED_EVENTS, "pending": len(self._event_queue),
                    "coalesced": self._coalesced_events, "dropped": self._dropped_events}

    def _apply_event(self, event: ObservationEnvelope) -> str:
        if not isinstance(event, ObservationEnvelope):
            raise TypeError("event must be an ObservationEnvelope")
        if self._paused:
            return "paused"
        if event.boot_id != self.boot_id:
            return "wrong_boot"
        last_sequence = self._last_sequence.get(event.source, -1)
        if event.sequence == last_sequence:
            return "duplicate"
        if event.sequence < last_sequence:
            return "stale"
        now = _aware_utc(self._now_utc(), "now_utc")
        if event.observed_at > now + timedelta(seconds=5):
            return "future_timestamp"
        remaining = min(
            MAX_FRESHNESS_SECONDS[event.kind],
            (event.expires_at - now).total_seconds(),
        )
        deadline = self._monotonic() + max(0.0, remaining)
        self._observations[event.kind] = (event, deadline)
        self._last_sequence[event.source] = event.sequence
        self._revision += 1
        return "accepted"

    def _drain_event_queue(self) -> None:
        while self._event_queue:
            key = self._event_queue.popleft()
            event = self._queued_events.pop(key, None)
            if event is not None:
                self._apply_event(event)
        self._queued_sequences.clear()

    def _discard_event_queue(self) -> None:
        for event in self._queued_events.values():
            self._last_sequence[event.source] = max(self._last_sequence.get(event.source, -1), event.sequence)
        self._dropped_events += len(self._queued_events)
        self._event_queue.clear()
        self._queued_events.clear()
        self._queued_sequences.clear()

    @_serialized
    def declare_intent(self, value: str, *, duration_seconds: float = 900.0) -> ContextDeclaration:
        if value not in FIELD_VALUES["declaredIntent"] - {"none"}:
            raise ValueError("intent must be focus, break, research, or meeting")
        if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float))
                or not math.isfinite(duration_seconds) or not 1 <= duration_seconds <= MAX_FRESHNESS_SECONDS["declaredIntent"]):
            raise ValueError("intent duration must be between 1 second and 12 hours")
        now = _aware_utc(self._now_utc(), "now_utc")
        declaration = ContextDeclaration(
            declaration_id=uuid4().hex,
            field="declaredIntent",
            value=value,
            created_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
        )
        self._declaration = declaration
        self._declaration_deadline = self._monotonic() + float(duration_seconds)
        self._revision += 1
        return declaration

    @_serialized
    def clear(self) -> None:
        """Clear live values while retaining source sequences to reject replay."""
        self._discard_event_queue()
        self._observations.clear()
        self._declaration = None
        self._declaration_deadline = None
        self._snooze_until = None
        self._snooze_deadline = None
        self._desk_calibrating = False
        self._revision += 1

    @_serialized
    def clear_intent(self) -> None:
        self._declaration = None
        self._declaration_deadline = None
        self._revision += 1

    @_serialized
    def set_snooze(self, *, duration_seconds: float = 1800.0) -> datetime:
        if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float))
                or not math.isfinite(duration_seconds) or not 1 <= duration_seconds <= 12 * 60 * 60):
            raise ValueError("snooze duration must be between 1 second and 12 hours")
        now = _aware_utc(self._now_utc(), "now_utc")
        self._snooze_until = now + timedelta(seconds=duration_seconds)
        self._snooze_deadline = self._monotonic() + float(duration_seconds)
        self._revision += 1
        return self._snooze_until

    @_serialized
    def clear_snooze(self) -> None:
        self._snooze_until = None
        self._snooze_deadline = None
        self._revision += 1

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @_serialized
    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("paused must be a boolean")
        if paused != self._paused:
            self._discard_event_queue()
            self._paused = paused
            self._desk_calibrating = False
            self._observations.clear()
            self._declaration = None
            self._declaration_deadline = None
            self._snooze_until = None
            self._snooze_deadline = None
            self._revision += 1

    @_serialized
    def read_snapshot(self) -> ContextSnapshot:
        self._drain_event_queue()
        now_utc = _aware_utc(self._now_utc(), "now_utc")
        now_mono = self._monotonic()
        fields = {}
        for name, value in DEFAULT_VALUES.items():
            current = self._observations.get(name)
            if current is None:
                fields[name] = ContextField(
                    value=value,
                    source="none",
                    observed_at=None,
                    expires_at=None,
                    evidence_kind="observation",
                    confidence=None,
                    source_available=False,
                    freshness="paused" if self._paused else "unavailable",
                )
            else:
                event, deadline = current
                if deadline <= now_mono:
                    stale_value = "unknown" if name != "desktopActivity" else "unavailable"
                    fields[name] = ContextField(
                        value=stale_value,
                        source=event.source,
                        observed_at=event.observed_at,
                        expires_at=event.expires_at,
                        evidence_kind=event.evidence_kind,
                        confidence=event.confidence,
                        source_available=False,
                        freshness="stale",
                    )
                else:
                    fields[name] = ContextField(
                        value=event.value,
                        source=event.source,
                        observed_at=event.observed_at,
                        expires_at=event.expires_at,
                        evidence_kind=event.evidence_kind,
                        confidence=event.confidence,
                        source_available=event.source_available,
                        freshness="fresh" if event.source_available else "unavailable",
                    )
        declaration = self._declaration
        if (declaration is not None and declaration.expires_at > now_utc
                and self._declaration_deadline is not None and self._declaration_deadline > now_mono):
            fields["declaredIntent"] = ContextField(
                value=declaration.value,
                source="user_declaration",
                observed_at=declaration.created_at,
                expires_at=declaration.expires_at,
                evidence_kind="user_report",
                confidence=None,
                source_available=True,
                freshness="fresh",
            )
        else:
            self._declaration = None
            self._declaration_deadline = None
            fields["declaredIntent"] = ContextField(
                value="none",
                source="none",
                observed_at=None,
                expires_at=None,
                evidence_kind="user_report",
                confidence=None,
                source_available=False,
                freshness="paused" if self._paused else "unavailable",
            )
        snooze_until = self._snooze_until
        if (snooze_until is None or snooze_until <= now_utc
                or self._snooze_deadline is None or self._snooze_deadline <= now_mono):
            self._snooze_until = None
            self._snooze_deadline = None
            snooze_until = None
        return ContextSnapshot(
            schema_version=1,
            snapshot_id=f"{self.boot_id}:{self._revision}",
            device_id=self.device_id,
            boot_id=self.boot_id,
            boot_started_at=self._boot_started_at,
            revision=self._revision,
            source_sequences=dict(self._last_sequence),
            generated_at=now_utc,
            fields=fields,
            paused=self._paused,
            snooze_until=snooze_until,
        )
