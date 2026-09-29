from dataclasses import replace
from datetime import datetime, timedelta, timezone

from assistant.companion_policy import CompanionPolicy, CompanionPreferences
from assistant.companion_runtime import CompanionRuntime
from assistant.context_runtime import ContextRuntime, ObservationEnvelope
from assistant.intervention_delivery import InterventionDelivery


class SpeechQueue:
    def __init__(self):
        self.jobs = []

    def speak_guarded_announcement(self, text, guard):
        self.jobs.append((text, guard))
        return True


def make_runtime(clock, *, presence="present"):
    monotonic = [10.0]
    context = ContextRuntime(
        device_id="device-1", boot_id="boot-1",
        monotonic=lambda: monotonic[0], now_utc=lambda: clock[0],
    )
    for source, kind, value, sequence in (
        ("webcam", "deskPresence", presence, 1),
        ("desktop_activity", "desktopActivity", "input_active", 1),
        ("desktop_activity", "foregroundCategory", "development", 2),
        ("session_runtime", "focusSession", "focus", 1),
    ):
        context.accept(ObservationEnvelope(
            schema_version=1, event_id=f"{source}-{sequence}", source=source,
            kind=kind, value=value, boot_id="boot-1", sequence=sequence,
            observed_at=clock[0] - timedelta(seconds=3), expires_at=clock[0] + timedelta(seconds=5),
        ))
    return context


def test_companion_runtime_evaluates_and_queues_one_guarded_local_intent():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    session_start = now[0] - timedelta(minutes=50, seconds=5)
    context = make_runtime(now)
    speech = SpeechQueue()
    preferences = CompanionPreferences(
        enabled=True, mode="focus_coach", break_suggestion_enabled=True,
        focus_session_id="session-5", focus_session_started_at=session_start,
        local_speech_enabled=True,
    )
    companion = CompanionRuntime(
        context, CompanionPolicy(), InterventionDelivery(now=lambda: now[0]),
        lambda: preferences, speech, now=lambda: now[0],
    )

    first = companion.tick()
    second = companion.tick()

    assert [receipt.status for receipt in first.receipts] == ["attempted"]
    assert second.receipts == ()
    assert len(speech.jobs) == 1
    assert "short break" in speech.jobs[0][0]


def test_shadow_mode_records_bounded_decision_without_enqueuing_speech():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    context = make_runtime(now)
    speech = SpeechQueue()
    preferences = CompanionPreferences(
        enabled=True, mode="focus_coach", break_suggestion_enabled=True,
        focus_session_id="session-shadow",
        focus_session_started_at=now[0] - timedelta(minutes=50, seconds=5),
        local_speech_enabled=True,
    )
    companion = CompanionRuntime(
        context, CompanionPolicy(), InterventionDelivery(now=lambda: now[0]),
        lambda: preferences, speech, now=lambda: now[0], shadow_mode=True,
    )

    tick = companion.tick()

    assert tick.receipts == ()
    assert speech.jobs == []
    decisions = context.read_companion_decisions()
    assert decisions[0]["mode"] == "shadow"
    assert decisions[0]["rules"][0]["rule_id"] == "break_suggestion"
    assert decisions[0]["rules"][0]["reason_code"] == "eligible"
    assert "message" not in str(decisions)


def test_companion_speech_guard_rechecks_pause_before_playback():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    context = make_runtime(now, presence="away")
    preferences = CompanionPreferences(
        enabled=True, mode="companion", desk_checkin_enabled=True,
        focus_session_id="session-6", focus_session_started_at=now[0] - timedelta(minutes=20),
        local_speech_enabled=True,
    )
    speech = SpeechQueue()
    companion = CompanionRuntime(
        context, CompanionPolicy(desk_absence_seconds=1),
        InterventionDelivery(now=lambda: now[0]), lambda: preferences,
        speech, now=lambda: now[0],
    )

    companion.tick()
    now[0] += timedelta(seconds=1)
    assert companion.tick().receipts[0].status == "attempted"
    context.set_paused(True)

    assert len(speech.jobs) == 1
    assert speech.jobs[0][1]() is False


def test_companion_speech_guard_rechecks_snooze_before_playback():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    context = make_runtime(now, presence="away")
    preferences = CompanionPreferences(
        enabled=True, mode="companion", desk_checkin_enabled=True,
        focus_session_id="session-8", focus_session_started_at=now[0] - timedelta(minutes=20),
        local_speech_enabled=True,
    )
    speech = SpeechQueue()
    companion = CompanionRuntime(
        context, CompanionPolicy(desk_absence_seconds=1),
        InterventionDelivery(now=lambda: now[0]), lambda: preferences,
        speech, now=lambda: now[0],
    )

    companion.tick()
    now[0] += timedelta(seconds=1)
    assert companion.tick().receipts[0].status == "attempted"
    context.set_snooze(duration_seconds=1800)

    assert speech.jobs[0][1]() is False


def test_companion_guard_does_not_cancel_its_own_active_speech():
    now = [datetime(2026, 9, 27, 10, tzinfo=timezone.utc)]
    context = make_runtime(now)
    preferences = [CompanionPreferences(
        enabled=True, mode="focus_coach", break_suggestion_enabled=True,
        focus_session_id="session-7",
        focus_session_started_at=now[0] - timedelta(minutes=50, seconds=5),
        local_speech_enabled=True,
    )]
    speech = SpeechQueue()
    companion = CompanionRuntime(
        context, CompanionPolicy(), InterventionDelivery(now=lambda: now[0]),
        lambda: preferences[0], speech, now=lambda: now[0],
    )

    companion.tick()
    preferences[0] = replace(preferences[0], vision_speaking=True)

    assert speech.jobs[0][1]() is True
