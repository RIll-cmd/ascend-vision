from datetime import datetime, timedelta, timezone

import pytest

from assistant.context_runtime import ContextRuntime, ObservationEnvelope


NOW = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.monotonic = 0.0
        self.utc = NOW

    def advance(self, seconds):
        self.monotonic += seconds
        self.utc += timedelta(seconds=seconds)


def event(*, field, value, source="desktop_activity", sequence=1, observed_at=NOW,
          expires_at=None, boot_id="boot-a", source_available=True):
    expiry_seconds = {"deskPresence": 15, "desktopActivity": 10,
                      "foregroundCategory": 10, "declaredIntent": 900}[field]
    return ObservationEnvelope(
        schema_version=1,
        event_id=f"{source}-{sequence}",
        source=source,
        kind=field,
        value=value,
        boot_id=boot_id,
        sequence=sequence,
        observed_at=observed_at,
        expires_at=expires_at or observed_at + timedelta(seconds=expiry_seconds),
        source_available=source_available,
    )


def test_camera_presence_expires_to_unknown_without_a_fresh_frame():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    runtime.accept(event(field="deskPresence", value="present", source="webcam"))

    clock.advance(16)

    assert runtime.read_snapshot().fields["deskPresence"].value == "unknown"


def test_duplicate_and_out_of_order_source_events_cannot_restore_old_context():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    fresh = event(field="desktopActivity", value="input_idle", sequence=2)
    assert runtime.accept(fresh) == "accepted"
    assert runtime.accept(fresh) == "duplicate"

    clock.advance(1)
    old = event(field="desktopActivity", value="input_active", sequence=1,
                observed_at=NOW - timedelta(seconds=1))
    assert runtime.accept(old) == "stale"
    assert runtime.read_snapshot().fields["desktopActivity"].value == "input_idle"


def test_observations_from_an_old_runtime_boot_are_rejected():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-current",
                             now_utc=lambda: NOW)

    assert runtime.accept(event(field="desktopActivity", value="input_active",
                                boot_id="boot-old")) == "wrong_boot"
    assert runtime.read_snapshot().fields["desktopActivity"].value == "unavailable"


def test_unknown_desk_presence_distinguishes_healthy_absence_from_camera_failure():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)
    runtime.accept(event(field="deskPresence", value="unknown", source="webcam",
                         source_available=False))

    field = runtime.read_snapshot().fields["deskPresence"]

    assert field.value == "unknown"
    assert field.freshness == "unavailable"
    assert field.source_available is False


@pytest.mark.parametrize("observed_at", [NOW + timedelta(seconds=6)])
def test_observation_more_than_five_seconds_in_future_is_rejected(observed_at):
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)

    result = runtime.accept(event(field="desktopActivity", value="input_active",
                                  observed_at=observed_at))

    assert result == "future_timestamp"
    assert runtime.read_snapshot().fields["desktopActivity"].value == "unavailable"


def test_user_break_declaration_changes_intent_without_changing_sensor_facts():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    runtime.accept(event(field="deskPresence", value="present", source="webcam"))
    runtime.accept(event(field="desktopActivity", value="input_active"))

    declaration = runtime.declare_intent("break", duration_seconds=900)
    snapshot = runtime.read_snapshot()

    assert snapshot.fields["declaredIntent"].value == "break"
    assert snapshot.fields["declaredIntent"].source == "user_declaration"
    assert snapshot.fields["deskPresence"].value == "present"
    assert snapshot.fields["desktopActivity"].value == "input_active"
    assert declaration.expires_at == NOW + timedelta(minutes=15)


def test_expired_user_intent_returns_to_none_and_can_be_cleared():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    runtime.declare_intent("research", duration_seconds=60)
    clock.advance(61)

    assert runtime.read_snapshot().fields["declaredIntent"].value == "none"

    runtime.declare_intent("break", duration_seconds=300)
    runtime.clear()
    assert runtime.read_snapshot().fields["declaredIntent"].value == "none"
    assert runtime.read_snapshot().fields["deskPresence"].value == "unknown"


def test_clock_rollback_cannot_extend_a_temporary_user_declaration():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    runtime.declare_intent("break", duration_seconds=60)
    clock.advance(61)
    clock.utc = NOW

    assert runtime.read_snapshot().fields["declaredIntent"].value == "none"


def test_undo_intent_and_snooze_change_only_their_temporary_context_fields():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    runtime.accept(event(field="deskPresence", value="present", source="webcam"))
    runtime.declare_intent("research", duration_seconds=600)
    runtime.set_snooze(duration_seconds=1800)

    runtime.clear_intent()
    snapshot = runtime.read_snapshot()

    assert snapshot.fields["declaredIntent"].value == "none"
    assert snapshot.fields["deskPresence"].value == "present"
    assert snapshot.snooze_until == NOW + timedelta(minutes=30)

    runtime.clear_snooze()
    assert runtime.read_snapshot().snooze_until is None


def test_snooze_expires_using_monotonic_time_even_if_wall_clock_moves_back():
    clock = FakeClock()
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a",
                             monotonic=lambda: clock.monotonic, now_utc=lambda: clock.utc)
    runtime.set_snooze(duration_seconds=60)
    clock.advance(61)
    clock.utc = NOW

    assert runtime.read_snapshot().snooze_until is None


def test_observation_queue_coalesces_latest_field_and_preserves_source_sequence_order():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)
    assert runtime.enqueue_observation(event(field="desktopActivity", value="input_idle", sequence=1)) == "queued"
    assert runtime.enqueue_observation(event(field="foregroundCategory", value="communication", sequence=2)) == "queued"
    assert runtime.enqueue_observation(event(field="desktopActivity", value="input_active", sequence=3)) == "coalesced"
    assert runtime.event_queue_status() == {"capacity": 256, "pending": 2, "coalesced": 1, "dropped": 0}

    snapshot = runtime.read_snapshot()
    assert snapshot.fields["desktopActivity"].value == "input_active"
    assert snapshot.fields["foregroundCategory"].value == "communication"
    assert snapshot.source_sequences["desktop_activity"] == 3


def test_priority_pause_discards_sensor_queue_and_suppresses_replay():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)
    pending = event(field="desktopActivity", value="input_active", sequence=1)
    assert runtime.enqueue_observation(pending) == "queued"

    runtime.set_paused(True)
    assert runtime.read_snapshot().fields["desktopActivity"].value == "unavailable"
    assert runtime.event_queue_status()["pending"] == 0
    runtime.set_paused(False)
    assert runtime.enqueue_observation(pending) == "duplicate"


def test_sensor_flood_is_coalesced_to_bounded_latest_state_without_blocking_controls():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)
    for sequence in range(1, 1_001):
        assert runtime.enqueue_observation(event(
            field="desktopActivity", value="input_idle" if sequence < 1_000 else "input_active",
            sequence=sequence,
        )) in {"queued", "coalesced"}

    assert runtime.event_queue_status() == {"capacity": 256, "pending": 1, "coalesced": 999, "dropped": 0}
    runtime.set_paused(True)
    assert runtime.event_queue_status()["pending"] == 0
    runtime.set_paused(False)
    assert runtime.read_snapshot().fields["desktopActivity"].value == "unavailable"


def test_pausing_context_clears_live_values_and_rejects_new_observations_until_resumed():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)
    runtime.accept(event(field="desktopActivity", value="input_active"))

    runtime.set_paused(True)
    assert runtime.read_snapshot().paused is True
    assert runtime.read_snapshot().fields["desktopActivity"].value == "unavailable"
    assert runtime.accept(event(field="desktopActivity", value="input_idle", sequence=2)) == "paused"

    runtime.set_paused(False)
    assert runtime.read_snapshot().paused is False
    assert runtime.accept(event(field="desktopActivity", value="input_idle", sequence=3)) == "accepted"
    assert runtime.read_snapshot().fields["desktopActivity"].value == "input_idle"


@pytest.mark.parametrize("field,value", [
    ("deskPresence", "emotionally_engaged"),
    ("desktopActivity", "productive"),
    ("foregroundCategory", "raw_window_title"),
    ("declaredIntent", "permanent_personal_memory"),
])
def test_context_rejects_unbounded_or_unsupported_field_values(field, value):
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)

    with pytest.raises(ValueError):
        runtime.accept(event(field=field, value=value))


def test_field_expiry_cannot_be_extended_by_an_observation():
    runtime = ContextRuntime(device_id="laptop-1", boot_id="boot-a", now_utc=lambda: NOW)
    with pytest.raises(ValueError, match="expiry"):
        runtime.accept(event(field="deskPresence", value="present", source="webcam",
                             expires_at=NOW + timedelta(hours=1)))
