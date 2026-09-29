from datetime import datetime, timezone

from assistant.context_runtime import ContextRuntime, DeskRegion
from integrations.context_collector import CompanionContextCollector
from integrations.desktop_activity import DesktopActivitySample


class FakeDesktopAdapter:
    def __init__(self):
        self.result = DesktopActivitySample("input_active", "development", True)
        self.calls = 0

    def sample(self):
        self.calls += 1
        return self.result


def test_collector_publishes_debounced_camera_and_rate_limited_desktop_signals():
    desktop = FakeDesktopAdapter()
    runtime = ContextRuntime(device_id="local-laptop", boot_id="boot-a",
                             now_utc=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc))
    runtime.set_desk_region(DeskRegion(0.1, 0.1, 0.9, 0.9))
    collector = CompanionContextCollector(
        runtime, desktop, desktop_poll_seconds=2, absence_dwell_seconds=10,
        return_dwell_seconds=3,
        now_utc=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc),
    )

    assert collector.sample_desktop(0.0) is True
    assert collector.sample_desktop(1.0) is False
    assert desktop.calls == 1
    assert collector.sample_desktop(2.0) is True
    assert desktop.calls == 2

    for tick in range(61):
        collector.observe_face(True, tick * 0.05)

    snapshot = runtime.read_snapshot()
    assert snapshot.fields["deskPresence"].value == "present"
    assert snapshot.fields["foregroundCategory"].value == "development"
    assert snapshot.fields["desktopActivity"].value == "input_active"


def test_collector_stops_sampling_context_while_the_owner_has_paused_it():
    desktop = FakeDesktopAdapter()
    runtime = ContextRuntime(device_id="local-laptop", boot_id="boot-a")
    collector = CompanionContextCollector(runtime, desktop)
    runtime.set_paused(True)

    assert collector.sample_desktop(0.0) is False
    assert collector.observe_face(True, 0.0) == "unknown"
    assert desktop.calls == 0


def test_unavailable_face_tracker_is_never_interpreted_as_camera_absence():
    runtime = ContextRuntime(device_id="local-laptop", boot_id="boot-a")
    collector = CompanionContextCollector(runtime, FakeDesktopAdapter())

    for tick in range(241):
        collector.observe_face(False, tick * 0.05, source_available=False)

    field = runtime.read_snapshot().fields["deskPresence"]
    assert field.value == "unknown"
    assert field.freshness == "unavailable"
    assert field.source_available is False


def test_existing_focus_session_mode_is_separate_from_user_declared_intent():
    runtime = ContextRuntime(device_id="local-laptop", boot_id="boot-a",
                             now_utc=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc))
    collector = CompanionContextCollector(
        runtime, FakeDesktopAdapter(),
        now_utc=lambda: datetime(2026, 9, 27, tzinfo=timezone.utc),
    )

    collector.sample_session_mode("focus", 1.0)

    snapshot = runtime.read_snapshot()
    assert snapshot.fields["focusSession"].value == "focus"
    assert snapshot.fields["declaredIntent"].value == "none"
