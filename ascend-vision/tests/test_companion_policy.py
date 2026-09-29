from dataclasses import replace
from datetime import datetime, timedelta, timezone

from assistant.companion_policy import CompanionPolicy, CompanionPreferences, NeedsInputEvidence
from assistant.context_runtime import ContextRuntime, ObservationEnvelope


def snapshot_at(now, *, presence="present", activity="input_active", intent=None,
                activity_available=True):
    monotonic = [100.0]
    runtime = ContextRuntime(
        device_id="test-device", boot_id="boot-test",
        monotonic=lambda: monotonic[0], now_utc=lambda: now,
    )
    events = [
        ("webcam", "deskPresence", presence, 1),
        ("desktop_activity", "desktopActivity", activity, 1),
        ("desktop_activity", "foregroundCategory", "development", 2),
        ("session_runtime", "focusSession", "focus", 1),
    ]
    for source, kind, value, sequence in events:
        if not activity_available and source == "desktop_activity":
            value = "unavailable" if kind == "desktopActivity" else "unknown"
        runtime.accept(ObservationEnvelope(
            schema_version=1, event_id=f"{source}-{sequence}", source=source,
            kind=kind, value=value, boot_id="boot-test", sequence=sequence,
            observed_at=now - timedelta(seconds=2), expires_at=now + timedelta(seconds=5),
            source_available=(activity_available if source == "desktop_activity" else True),
        ))
    if intent:
        runtime.declare_intent(intent, duration_seconds=900)
    return runtime


def preferences(*, session_started_at, **overrides):
    base = CompanionPreferences(
        enabled=True, mode="focus_coach", break_suggestion_enabled=True,
        desk_checkin_enabled=True, focus_session_id="session-17",
        focus_session_started_at=session_started_at, local_speech_enabled=True,
    )
    return replace(base, **overrides)


def test_break_suggestion_is_proposed_once_per_focus_interval():
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    now = start + timedelta(minutes=50, seconds=10)
    runtime = snapshot_at(now)
    policy = CompanionPolicy()

    first = policy.evaluate(runtime.read_snapshot(), preferences(session_started_at=start), now)
    second = policy.evaluate(runtime.read_snapshot(), preferences(session_started_at=start), now)

    first_intent = next(item for item in first.intents if item.rule_id == "break_suggestion")
    second_intent = next(item for item in second.intents if item.rule_id == "break_suggestion")
    assert first_intent.intent_id == second_intent.intent_id
    assert first_intent.deduplication_key == "break_suggestion:session-17:interval-1"
    assert first_intent.expires_at == start + timedelta(minutes=51)


def test_break_suggestion_waits_for_fresh_active_focus_and_respects_break_intent():
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    now = start + timedelta(minutes=55)
    runtime = snapshot_at(now, intent="break")

    result = CompanionPolicy().evaluate(
        runtime.read_snapshot(), preferences(session_started_at=start), now,
    )

    assert not any(item.rule_id == "break_suggestion" for item in result.intents)
    assert any(item.rule_id == "break_suggestion" and item.reason_code == "declared_break"
               for item in result.decisions)


def test_break_suggestion_copy_matches_configured_interval():
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    now = start + timedelta(minutes=30, seconds=10)
    runtime = snapshot_at(now)

    result = CompanionPolicy(break_interval_seconds=30 * 60).evaluate(
        runtime.read_snapshot(), preferences(session_started_at=start), now,
    )

    proposal = next(item for item in result.intents if item.rule_id == "break_suggestion")
    assert "30 minutes" in proposal.message


def test_break_suggestion_requires_fresh_desktop_activity():
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    now = start + timedelta(minutes=50, seconds=5)
    runtime = snapshot_at(now, activity_available=False)

    result = CompanionPolicy().evaluate(
        runtime.read_snapshot(), preferences(session_started_at=start), now,
    )

    assert not any(item.rule_id == "break_suggestion" for item in result.intents)
    assert any(item.rule_id == "break_suggestion" and item.reason_code == "desktop_activity_not_fresh"
               for item in result.decisions)


def test_desk_checkin_requires_sustained_confirmed_absence_during_focus():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    runtime = snapshot_at(now, presence="away")
    policy = CompanionPolicy(desk_absence_seconds=300)
    prefs = preferences(session_started_at=now - timedelta(minutes=40))

    early = policy.evaluate(runtime.read_snapshot(), prefs, now)
    after_dwell = policy.evaluate(runtime.read_snapshot(), prefs, now + timedelta(seconds=300))

    assert not any(item.rule_id == "desk_check_in" for item in early.intents)
    check_in = next(item for item in after_dwell.intents if item.rule_id == "desk_check_in")
    assert check_in.deduplication_key.startswith("desk_check_in:session-17:")


def test_global_suppression_resets_desk_absence_dwell():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    policy = CompanionPolicy(desk_absence_seconds=300)
    prefs = preferences(session_started_at=now - timedelta(minutes=40))

    policy.evaluate(snapshot_at(now, presence="away").read_snapshot(), prefs, now)
    paused_at = now + timedelta(minutes=4)
    paused_snapshot = replace(snapshot_at(paused_at, presence="away").read_snapshot(), paused=True)
    policy.evaluate(paused_snapshot, prefs, paused_at)

    resumed_at = now + timedelta(minutes=5)
    resumed = policy.evaluate(
        snapshot_at(resumed_at, presence="away").read_snapshot(), prefs, resumed_at,
    )
    assert resumed.decisions[1].reason_code == "absence_dwell_pending"
    assert not any(item.rule_id == "desk_check_in" for item in resumed.intents)


def test_unknown_or_locked_presence_never_creates_desk_checkin():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    start = now - timedelta(minutes=40)
    unknown = snapshot_at(now, presence="unknown")
    locked = snapshot_at(now, presence="away", activity="locked")
    policy = CompanionPolicy(desk_absence_seconds=1)
    prefs = preferences(session_started_at=start)

    unknown_result = policy.evaluate(unknown.read_snapshot(), prefs, now)
    locked_result = policy.evaluate(locked.read_snapshot(), prefs, now)

    assert not any(item.rule_id == "desk_check_in" for item in unknown_result.intents)
    assert not any(item.rule_id == "desk_check_in" for item in locked_result.intents)


def test_idle_agent_state_is_not_a_needs_input_event():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    runtime = snapshot_at(now)
    policy = CompanionPolicy()

    result = policy.evaluate(
        runtime.read_snapshot(), preferences(session_started_at=now, agent_needs_input_enabled=True), now,
    )

    assert not any(item.rule_id == "agent_needs_input" for item in result.intents)
    assert any(item.rule_id == "agent_needs_input" and item.reason_code == "source_unavailable"
               for item in result.decisions)


def test_agent_input_alert_requires_fresh_explicit_operation_evidence_and_deduplicates():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    runtime = snapshot_at(now)
    prefs = preferences(session_started_at=now - timedelta(minutes=10),
                        agent_needs_input_enabled=True)
    evidence = (NeedsInputEvidence("codex-cli", "0123456789abcdef", "Deploy preview", now),)
    policy = CompanionPolicy()

    first = policy.evaluate(runtime.read_snapshot(), prefs, now, agent_needs_input=evidence)
    replay = policy.evaluate(runtime.read_snapshot(), prefs, now + timedelta(seconds=2),
                             agent_needs_input=evidence)
    intent = next(item for item in first.intents if item.rule_id == "agent_needs_input")
    replayed = next(item for item in replay.intents if item.rule_id == "agent_needs_input")
    assert intent.intent_id == replayed.intent_id
    assert "Deploy preview" in intent.message
    assert "short break" not in intent.message


def test_agent_input_rule_is_disabled_by_default_and_stale_evidence_is_rejected():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    snapshot = snapshot_at(now).read_snapshot()
    stale = (NeedsInputEvidence("codex-cli", "0123456789abcdef", None,
                               now - timedelta(seconds=31)),)

    disabled = CompanionPolicy().evaluate(snapshot, preferences(session_started_at=now), now,
                                          agent_needs_input=stale)
    enabled = CompanionPolicy().evaluate(
        snapshot, preferences(session_started_at=now, agent_needs_input_enabled=True), now,
        agent_needs_input=stale,
    )

    assert any(item.rule_id == "agent_needs_input" and item.reason_code == "rule_disabled"
               for item in disabled.decisions)
    assert not any(item.rule_id == "agent_needs_input" for item in enabled.intents)
    assert any(item.rule_id == "agent_needs_input" and item.reason_code == "evidence_stale"
               for item in enabled.decisions)


def test_legacy_speech_in_queue_suppresses_companion_delivery():
    now = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)
    start = now - timedelta(minutes=51)
    prefs = preferences(session_started_at=start, speech_queue_busy=True)
    result = CompanionPolicy().evaluate(snapshot_at(now).read_snapshot(), prefs, now)

    assert not result.intents
    assert all(decision.reason_code == "speech_queue_busy" for decision in result.decisions)
