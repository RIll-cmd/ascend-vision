from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from assistant.activity_summary import (
    ActivityHistoryStore, ActivitySummaryRecorder, render_daily_summary,
)


def snapshot(*, generated_at, focus="focus", intent="none", category="development",
             activity="input_active", paused=False, focus_expires=10,
             intent_expires=900, category_expires=10, activity_expires=10):
    def field(value, source, expires, freshness="fresh"):
        return SimpleNamespace(
            value=value,
            source=source,
            observed_at=generated_at,
            expires_at=generated_at + timedelta(seconds=expires),
            freshness=freshness,
            source_available=freshness == "fresh",
        )

    return SimpleNamespace(
        generated_at=generated_at,
        paused=paused,
        fields={
            "focusSession": field(focus, "session_runtime", focus_expires),
            "declaredIntent": field(intent, "user_declaration", intent_expires),
            "foregroundCategory": field(category, "desktop_activity", category_expires),
            "desktopActivity": field(activity, "desktop_activity", activity_expires),
        },
    )


def test_activity_history_starts_disabled_and_disabled_samples_are_not_retained(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    first = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    recorder.record(snapshot(generated_at=first), wall_time=first, monotonic_time=10)
    recorder.record(snapshot(generated_at=first + timedelta(seconds=2)),
                    wall_time=first + timedelta(seconds=2), monotonic_time=12)

    assert store.enabled is False
    assert store.list_summaries(first.date(), first.date(), timezone_name="UTC") == []


def test_activity_history_setting_survives_restart_but_pause_preserves_saved_days(tmp_path):
    path = tmp_path / "activity.db"
    first_store = ActivityHistoryStore(path)
    first_store.set_enabled(True)
    first_store.add_interval(date(2026, 9, 27), "UTC", tracked_seconds=12)
    first_store.set_enabled(False)

    restarted = ActivityHistoryStore(path)
    assert restarted.enabled is False
    assert restarted.get_summary(date(2026, 9, 27), timezone_name="UTC").tracked_seconds == 12
    assert restarted.add_interval(date(2026, 9, 27), "UTC", tracked_seconds=10) is False
    assert restarted.get_summary(date(2026, 9, 27), timezone_name="UTC").tracked_seconds == 12


def test_activity_summary_splits_a_measured_interval_at_local_midnight(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    before_midnight = datetime(2026, 9, 27, 23, 59, 58, tzinfo=timezone.utc)
    after_midnight = before_midnight + timedelta(seconds=4)
    recorder.record(snapshot(generated_at=before_midnight),
                    wall_time=before_midnight, monotonic_time=100)
    recorder.record(snapshot(generated_at=after_midnight),
                    wall_time=after_midnight, monotonic_time=104)

    previous_day = store.get_summary(before_midnight.date(), timezone_name="UTC")
    current_day = store.get_summary(after_midnight.date(), timezone_name="UTC")
    assert previous_day.tracked_seconds == 2
    assert current_day.tracked_seconds == 2
    assert previous_day.focus_session_seconds == 2
    assert current_day.focus_session_seconds == 2


def test_unknown_sources_are_counted_as_uncovered_not_as_activity(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    unknown = snapshot(generated_at=start, focus="unavailable", category="unknown")
    recorder.record(unknown, wall_time=start, monotonic_time=1)
    recorder.record(unknown, wall_time=start + timedelta(seconds=3), monotonic_time=4)

    summary = store.get_summary(start.date(), timezone_name="UTC")
    assert summary.tracked_seconds == 3
    assert summary.focus_session_seconds == 0
    assert summary.focus_uncovered_seconds == 3
    assert summary.entertainment_category_seconds == 0
    assert summary.entertainment_uncovered_seconds == 3


def test_overlapping_signals_share_one_tracked_interval_and_keep_precise_labels(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    before = snapshot(generated_at=start, intent="break", category="entertainment")
    after = snapshot(generated_at=start + timedelta(seconds=4), intent="break", category="entertainment")
    recorder.record(before, wall_time=start, monotonic_time=10)
    recorder.record(after, wall_time=start + timedelta(seconds=4), monotonic_time=14)

    summary = store.get_summary(start.date(), timezone_name="UTC")
    assert summary.tracked_seconds == 4
    assert summary.focus_session_seconds == 4
    assert summary.declared_break_seconds == 4
    assert summary.entertainment_category_seconds == 4


def test_expired_evidence_stops_coverage_inside_an_interval(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    before = snapshot(generated_at=start, focus_expires=2)
    after = snapshot(generated_at=start + timedelta(seconds=4))
    recorder.record(before, wall_time=start, monotonic_time=1)
    recorder.record(after, wall_time=start + timedelta(seconds=4), monotonic_time=5)

    summary = store.get_summary(start.date(), timezone_name="UTC")
    assert summary.tracked_seconds == 4
    assert summary.focus_session_seconds == 2
    assert summary.focus_coverage_seconds == 2
    assert summary.focus_uncovered_seconds == 2


def test_paused_context_is_tracked_as_uncovered_not_focus(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    recorder.record(snapshot(generated_at=start), wall_time=start, monotonic_time=1)
    recorder.record(snapshot(generated_at=start + timedelta(seconds=3), paused=True),
                    wall_time=start + timedelta(seconds=3), monotonic_time=4)

    summary = store.get_summary(start.date(), timezone_name="UTC")
    assert summary.tracked_seconds == 3
    assert summary.focus_session_seconds == 0
    assert summary.focus_coverage_seconds == 0
    assert summary.focus_uncovered_seconds == 3


def test_clock_correction_and_long_gap_do_not_create_fictional_time(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="UTC")
    start = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    recorder.record(snapshot(generated_at=start), wall_time=start, monotonic_time=1)
    recorder.record(snapshot(generated_at=start + timedelta(minutes=30)),
                    wall_time=start + timedelta(minutes=30), monotonic_time=2)
    recorder.record(snapshot(generated_at=start + timedelta(minutes=31)),
                    wall_time=start + timedelta(minutes=31), monotonic_time=62)

    summary = store.get_summary(start.date(), timezone_name="UTC")
    assert summary is None


def test_history_retention_export_delete_and_corrections_are_auditable(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db", retention_days=30)
    store.set_enabled(True)
    day = datetime(2026, 9, 27, 9, tzinfo=timezone.utc)
    store.add_interval(day.date(), "UTC", tracked_seconds=120,
                       focus_session_seconds=120, focus_coverage_seconds=120)
    correction = store.correct(day.date(), "UTC", "focus_session_seconds", -60)

    summary = store.get_summary(day.date(), timezone_name="UTC")
    assert summary.focus_session_seconds == 60
    assert summary.focus_coverage_seconds == 60
    assert summary.focus_uncovered_seconds == 60
    assert summary.corrections == (correction,)
    assert store.export(day.date(), timezone_name="UTC")[0]["focus_session_seconds"] == 60
    try:
        store.correct(day.date(), "UTC", "focus_session_seconds", -61)
    except ValueError as exc:
        assert "more time than the recorded metric" in str(exc)
    else:
        raise AssertionError("correction removed more seconds than the measured total")

    store.purge_expired(now=day.date() + timedelta(days=31))
    assert store.list_summaries(day.date(), day.date(), timezone_name="UTC") == []
    assert store.delete_all() is True


def test_retention_keeps_exactly_the_latest_thirty_local_dates(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    store.add_interval(date(2026, 9, 29), "UTC", tracked_seconds=1)
    store.add_interval(date(2026, 9, 28), "UTC", tracked_seconds=1)

    assert store.purge_expired(now=date(2026, 10, 28)) == 1
    assert store.get_summary(date(2026, 9, 29), timezone_name="UTC") is not None
    assert store.get_summary(date(2026, 9, 28), timezone_name="UTC") is None


def test_time_is_split_using_configured_local_timezone(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="America/Los_Angeles")
    before_midnight = datetime(2026, 9, 28, 6, 59, 58, tzinfo=timezone.utc)
    after_midnight = before_midnight + timedelta(seconds=4)
    recorder.record(snapshot(generated_at=before_midnight),
                    wall_time=before_midnight, monotonic_time=10)
    recorder.record(snapshot(generated_at=after_midnight),
                    wall_time=after_midnight, monotonic_time=14)

    prior = store.get_summary(datetime(2026, 9, 27).date(), timezone_name="America/Los_Angeles")
    next_day = store.get_summary(datetime(2026, 9, 28).date(), timezone_name="America/Los_Angeles")
    assert prior.tracked_seconds == 2
    assert next_day.tracked_seconds == 2


def test_fall_back_clock_repetition_keeps_monotonic_elapsed_time(tmp_path):
    store = ActivityHistoryStore(tmp_path / "activity.db")
    store.set_enabled(True)
    recorder = ActivitySummaryRecorder(store, timezone_name="America/New_York")
    before_fallback = datetime(2026, 11, 1, 5, 59, 58, tzinfo=timezone.utc)
    after_fallback = before_fallback + timedelta(seconds=4)
    recorder.record(snapshot(generated_at=before_fallback),
                    wall_time=before_fallback, monotonic_time=10)
    recorder.record(snapshot(generated_at=after_fallback),
                    wall_time=after_fallback, monotonic_time=14)

    summary = store.get_summary(date(2026, 11, 1), timezone_name="America/New_York")
    assert summary.tracked_seconds == 4
    assert summary.focus_session_seconds == 4


def test_daily_reflection_is_deterministic_and_never_claims_unavailable_core_outcomes(tmp_path):
    now = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    store = ActivityHistoryStore(tmp_path / "activity.db", now=lambda: now)
    assert "history is off" in render_daily_summary(store, timezone_name="UTC", now=now)

    store.set_enabled(True)
    store.add_interval(date(2026, 9, 27), "UTC", tracked_seconds=600,
                       focus_session_seconds=300, focus_coverage_seconds=300,
                       declared_break_seconds=120, break_coverage_seconds=600)
    answer = render_daily_summary(store, timezone_name="UTC", now=now)
    assert "Tracked focus-session minutes: 5m 00s" in answer
    assert "Declared break minutes: 2m 00s" in answer
    assert "uncovered 5m 00s" in answer
    assert "Core mission completions are unavailable" not in answer
    assert "Core-confirmed mission completions" not in answer
    assert "Scoring is disabled" in answer

