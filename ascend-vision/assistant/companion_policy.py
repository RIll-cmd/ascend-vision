"""Deterministic, evidence-gated rules for local companion interventions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import threading
from typing import Literal

from assistant.context_runtime import ContextSnapshot


ALLOWED_CHANNELS = ("desktop_speech",)
DEFAULT_BREAK_INTERVAL_SECONDS = 50 * 60
DEFAULT_DESK_ABSENCE_SECONDS = 5 * 60
INTENT_TTL_SECONDS = 60
RULE_IDS = ("break_suggestion", "desk_check_in", "agent_needs_input")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CompanionPreferences:
    enabled: bool = False
    mode: Literal["quiet", "companion", "focus_coach"] = "quiet"
    break_suggestion_enabled: bool = False
    desk_checkin_enabled: bool = False
    agent_needs_input_enabled: bool = False
    focus_session_id: str | None = None
    focus_session_started_at: datetime | None = None
    local_speech_enabled: bool = False
    user_speaking: bool = False
    vision_speaking: bool = False
    speech_queue_busy: bool = False

    def __post_init__(self):
        for name in ("enabled", "break_suggestion_enabled", "desk_checkin_enabled",
                     "agent_needs_input_enabled", "local_speech_enabled", "user_speaking", "vision_speaking",
                     "speech_queue_busy"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.mode not in {"quiet", "companion", "focus_coach"}:
            raise ValueError("mode must be quiet, companion, or focus_coach")
        if self.focus_session_id is not None and (
            not isinstance(self.focus_session_id, str) or not self.focus_session_id.strip()
        ):
            raise ValueError("focus_session_id must be nonempty when provided")
        if self.focus_session_started_at is not None:
            object.__setattr__(
                self, "focus_session_started_at",
                _aware(self.focus_session_started_at, "focus_session_started_at"),
            )


@dataclass(frozen=True)
class InterventionIntent:
    intent_id: str
    deduplication_key: str
    rule_id: str
    trigger_id: str
    evidence_timestamps: tuple[tuple[str, datetime], ...]
    reason_code: str
    expires_at: datetime
    priority: int
    allowed_channels: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class RuleDecision:
    rule_id: str
    reason_code: str
    intent_id: str | None = None


@dataclass(frozen=True)
class PolicyEvaluation:
    intents: tuple[InterventionIntent, ...]
    decisions: tuple[RuleDecision, ...]


@dataclass(frozen=True)
class NeedsInputEvidence:
    service_id: str
    operation_ref: str
    label: str | None
    observed_at: datetime


class CompanionPolicy:
    """Evaluate local rules and explicitly producer-reported agent input requests."""

    def __init__(self, *, break_interval_seconds: int = DEFAULT_BREAK_INTERVAL_SECONDS,
                 desk_absence_seconds: int = DEFAULT_DESK_ABSENCE_SECONDS):
        for name, value in (("break_interval_seconds", break_interval_seconds),
                            ("desk_absence_seconds", desk_absence_seconds)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self.break_interval_seconds = break_interval_seconds
        self.desk_absence_seconds = desk_absence_seconds
        self._lock = threading.RLock()
        self._away_since: datetime | None = None
        self._away_session_id: str | None = None

    def evaluate(self, snapshot: ContextSnapshot, preferences: CompanionPreferences,
                 now: datetime, *, agent_needs_input: tuple[NeedsInputEvidence, ...] = ()) -> PolicyEvaluation:
        if not isinstance(snapshot, ContextSnapshot):
            raise TypeError("snapshot must be a ContextSnapshot")
        if not isinstance(preferences, CompanionPreferences):
            raise TypeError("preferences must be CompanionPreferences")
        now = _aware(now, "now")
        intents: list[InterventionIntent] = []
        decisions: list[RuleDecision] = []

        if not preferences.enabled:
            global_reason = "disabled"
        elif snapshot.paused:
            global_reason = "paused"
        elif snapshot.snooze_until is not None and snapshot.snooze_until > now:
            global_reason = "snoozed"
        elif preferences.mode == "quiet":
            global_reason = "quiet_mode"
        elif not preferences.local_speech_enabled:
            global_reason = "local_speech_unavailable"
        elif preferences.user_speaking:
            global_reason = "user_speaking"
        elif preferences.vision_speaking:
            global_reason = "vision_speaking"
        elif preferences.speech_queue_busy:
            global_reason = "speech_queue_busy"
        else:
            global_reason = None

        if global_reason is not None:
            with self._lock:
                self._reset_away()

        for rule_id in RULE_IDS:
            if global_reason is not None:
                decisions.append(RuleDecision(rule_id, global_reason))
                continue
            if rule_id == "break_suggestion":
                intent, decision = self._break_suggestion(snapshot, preferences, now)
            elif rule_id == "desk_check_in":
                intent, decision = self._desk_check_in(snapshot, preferences, now)
            else:
                intent, decision = self._agent_needs_input(preferences, now, agent_needs_input)
            decisions.append(decision)
            if intent is not None:
                intents.append(intent)
        return PolicyEvaluation(tuple(intents), tuple(decisions))

    @staticmethod
    def _agent_needs_input(preferences, now, evidence):
        rule_id = "agent_needs_input"
        if not preferences.agent_needs_input_enabled:
            return None, RuleDecision(rule_id, "rule_disabled")
        if not evidence:
            return None, RuleDecision(rule_id, "source_unavailable")
        current = []
        for item in evidence:
            if (not isinstance(item, NeedsInputEvidence)
                    or not item.service_id or not item.operation_ref
                    or not isinstance(item.observed_at, datetime)
                    or item.observed_at.tzinfo is None or item.observed_at.utcoffset() is None):
                continue
            observed_at = item.observed_at.astimezone(timezone.utc)
            age = (now - observed_at).total_seconds()
            if -5 <= age <= 30:
                current.append((item, observed_at))
        if not current:
            return None, RuleDecision(rule_id, "evidence_stale")
        item, observed_at = sorted(current, key=lambda pair: (pair[0].service_id, pair[0].operation_ref))[0]
        label = item.label.strip() if isinstance(item.label, str) else ""
        suffix = f" ({label})" if label else ""
        dedupe = f"{rule_id}:{item.service_id}:{item.operation_ref}"
        expires_at = observed_at + timedelta(seconds=60)
        intent = CompanionPolicy._intent(
            dedupe, rule_id, item.service_id, ("agentStatus", observed_at),
            "producer_needs_input", expires_at, 50,
            f"{item.service_id} needs your input{suffix}.",
        )
        return intent, RuleDecision(rule_id, "eligible", intent.intent_id)

    def _break_suggestion(self, snapshot, preferences, now):
        rule_id = "break_suggestion"
        if not preferences.break_suggestion_enabled:
            return None, RuleDecision(rule_id, "rule_disabled")
        if preferences.mode != "focus_coach":
            return None, RuleDecision(rule_id, "focus_coach_required")
        focus = snapshot.fields["focusSession"]
        if focus.freshness != "fresh":
            return None, RuleDecision(rule_id, "focus_session_not_fresh")
        if focus.value != "focus":
            return None, RuleDecision(rule_id, "focus_session_inactive")
        intent_field = snapshot.fields["declaredIntent"]
        if intent_field.freshness == "fresh" and intent_field.value in {"break", "meeting"}:
            return None, RuleDecision(rule_id, f"declared_{intent_field.value}")
        activity = snapshot.fields["desktopActivity"]
        if activity.freshness != "fresh":
            return None, RuleDecision(rule_id, "desktop_activity_not_fresh")
        if activity.value == "locked":
            return None, RuleDecision(rule_id, "device_locked")
        started_at = preferences.focus_session_started_at
        session_id = preferences.focus_session_id
        if started_at is None or session_id is None:
            return None, RuleDecision(rule_id, "focus_session_timing_unavailable")
        elapsed = (now - started_at).total_seconds()
        if elapsed < self.break_interval_seconds:
            return None, RuleDecision(rule_id, "interval_not_reached")
        interval_index = int(elapsed // self.break_interval_seconds)
        trigger_at = started_at + timedelta(seconds=interval_index * self.break_interval_seconds)
        expires_at = trigger_at + timedelta(seconds=INTENT_TTL_SECONDS)
        if now > expires_at:
            return None, RuleDecision(rule_id, "intent_window_expired")
        dedupe = f"{rule_id}:{session_id}:interval-{interval_index}"
        interval_minutes = max(1, self.break_interval_seconds // 60)
        intent = self._intent(
            dedupe, rule_id, rule_id, ("focusSession", focus.observed_at or started_at),
            "focus_interval_reached", expires_at, 40,
            f"You've been in this focus session for {interval_minutes} minutes. Want to take a short break?",
        )
        return intent, RuleDecision(rule_id, "eligible", intent.intent_id)

    def _desk_check_in(self, snapshot, preferences, now):
        with self._lock:
            return self._desk_check_in_locked(snapshot, preferences, now)

    def _desk_check_in_locked(self, snapshot, preferences, now):
        rule_id = "desk_check_in"
        presence = snapshot.fields["deskPresence"]
        focus = snapshot.fields["focusSession"]
        if not preferences.desk_checkin_enabled:
            self._reset_away()
            return None, RuleDecision(rule_id, "rule_disabled")
        if presence.freshness != "fresh":
            self._reset_away()
            return None, RuleDecision(rule_id, "presence_not_fresh")
        if presence.value != "away":
            self._reset_away()
            return None, RuleDecision(rule_id, "presence_not_away")
        if focus.freshness != "fresh":
            self._reset_away()
            return None, RuleDecision(rule_id, "focus_session_not_fresh")
        if focus.value != "focus":
            self._reset_away()
            return None, RuleDecision(rule_id, "focus_session_inactive")
        activity = snapshot.fields["desktopActivity"]
        if activity.freshness != "fresh" or activity.value == "locked":
            self._reset_away()
            reason = "device_locked" if activity.value == "locked" else "desktop_activity_not_fresh"
            return None, RuleDecision(rule_id, reason)
        intent_field = snapshot.fields["declaredIntent"]
        if intent_field.freshness == "fresh" and intent_field.value in {"break", "meeting"}:
            self._reset_away()
            return None, RuleDecision(rule_id, f"declared_{intent_field.value}")
        if preferences.focus_session_id is None or preferences.focus_session_started_at is None:
            self._reset_away()
            return None, RuleDecision(rule_id, "focus_session_timing_unavailable")

        if self._away_since is None or self._away_session_id != preferences.focus_session_id:
            self._away_since = now
            self._away_session_id = preferences.focus_session_id
            return None, RuleDecision(rule_id, "absence_dwell_pending")
        trigger_at = self._away_since + timedelta(seconds=self.desk_absence_seconds)
        if now < trigger_at:
            return None, RuleDecision(rule_id, "absence_dwell_pending")
        expires_at = trigger_at + timedelta(seconds=INTENT_TTL_SECONDS)
        if now > expires_at:
            return None, RuleDecision(rule_id, "intent_window_expired")
        dedupe = f"{rule_id}:{preferences.focus_session_id}:{self._away_since.isoformat()}"
        evidence_at = presence.observed_at or trigger_at
        intent = self._intent(
            dedupe, rule_id, rule_id, ("deskPresence", evidence_at), "confirmed_desk_absence",
            expires_at, 30,
            "I haven't detected a face in the calibrated desk area during this focus session. Want to pause it?",
        )
        return intent, RuleDecision(rule_id, "eligible", intent.intent_id)

    def _reset_away(self):
        self._away_since = None
        self._away_session_id = None

    @staticmethod
    def _intent(deduplication_key, rule_id, trigger_id, evidence, reason_code, expires_at, priority, message):
        digest = hashlib.sha256(deduplication_key.encode("utf-8")).hexdigest()[:24]
        field, timestamp = evidence
        if (not isinstance(timestamp, datetime) or timestamp.tzinfo is None
                or timestamp.utcoffset() is None):
            timestamp = None
        else:
            timestamp = timestamp.astimezone(timezone.utc)
        evidence_timestamps = ((field, timestamp),) if timestamp is not None else ()
        return InterventionIntent(
            intent_id=digest,
            deduplication_key=deduplication_key,
            rule_id=rule_id,
            trigger_id=trigger_id,
            evidence_timestamps=evidence_timestamps,
            reason_code=reason_code,
            expires_at=expires_at,
            priority=priority,
            allowed_channels=ALLOWED_CHANNELS,
            message=message,
        )
