from integrations.desktop_activity import WindowsActivityAdapter


class FakeWindowsProbe:
    def __init__(self, *, locked=False, idle_ms=0, executable="Code.exe", available=True):
        self.locked = locked
        self.idle_ms = idle_ms
        self.executable = executable
        self.available = available

    def is_locked(self):
        return self.locked

    def idle_milliseconds(self):
        return self.idle_ms

    def foreground_executable(self):
        return self.executable


def test_desktop_adapter_reports_coarse_category_and_input_activity_only():
    adapter = WindowsActivityAdapter(probe=FakeWindowsProbe(
        executable="Code.exe", idle_ms=1_000,
    ))

    state = adapter.sample()

    assert state.desktop_activity == "input_active"
    assert state.foreground_category == "development"
    assert not hasattr(state, "window_title")
    assert not hasattr(state, "executable_path")


def test_locked_desktop_overrides_recent_input_activity():
    adapter = WindowsActivityAdapter(probe=FakeWindowsProbe(locked=True, idle_ms=0))

    assert adapter.sample().desktop_activity == "locked"


def test_idle_threshold_is_distinct_from_desk_presence():
    adapter = WindowsActivityAdapter(probe=FakeWindowsProbe(idle_ms=60_000))

    assert adapter.sample().desktop_activity == "input_idle"


def test_unknown_foreground_app_keeps_activity_but_hides_app_identity():
    adapter = WindowsActivityAdapter(probe=FakeWindowsProbe(
        executable="personal-project-secret.exe", idle_ms=500,
    ))

    state = adapter.sample()

    assert state.desktop_activity == "input_active"
    assert state.foreground_category == "other"
    assert "personal-project-secret" not in repr(state)


def test_probe_failure_reports_unavailable_without_stale_values():
    class FailingProbe:
        def is_locked(self):
            raise OSError("desktop unavailable")

    adapter = WindowsActivityAdapter(probe=FailingProbe())

    state = adapter.sample()

    assert state.desktop_activity == "unavailable"
    assert state.foreground_category == "unknown"
    assert state.available is False


def test_unreadable_lock_state_fails_closed_instead_of_claiming_active():
    adapter = WindowsActivityAdapter(probe=FakeWindowsProbe(locked=None, idle_ms=0))

    state = adapter.sample()

    assert state.desktop_activity == "unavailable"
    assert state.foreground_category == "unknown"
