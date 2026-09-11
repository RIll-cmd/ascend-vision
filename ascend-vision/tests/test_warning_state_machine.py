"""Unit tests for WarningFirstStateMachine."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from integrations.warning_state_machine import (
    SensoryTriggerType,
    WarningFirstStateMachine,
    TRIGGER_CONFIGS,
)


class MockTime:
    def __init__(self, start: float = 1000.0):
        self.current = start

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float):
        self.current += seconds


@pytest.fixture
def mock_feedback():
    fb = MagicMock()
    fb.speak_announcement = MagicMock()
    return fb


@pytest.fixture
def mock_core_client():
    client = MagicMock()
    client.record_bad_habit_offense = AsyncMock(return_value={
        "success": True,
        "action": "RELAPSE_LOGGED",
        "canonicalNarration": "Protocol breached: Doomscrolling relapse logged. -10 HP deducted.",
    })
    return client


def test_first_offense_phone_warning(mock_feedback, mock_core_client):
    time_fn = MockTime(100.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_fn,
        debounce_seconds=5.0,
    )

    res = sm.handle_trigger(SensoryTriggerType.PHONE)

    assert res is not None
    assert res["offense"] == 1
    assert res["trigger"] == "phone_usage"
    assert "Phone distraction detected" in res["warning"]
    mock_feedback.speak_announcement.assert_called_once_with(
        TRIGGER_CONFIGS[SensoryTriggerType.PHONE].warning_text
    )
    mock_core_client.record_bad_habit_offense.assert_not_called()


def test_first_offense_slouch_warning(mock_feedback, mock_core_client):
    time_fn = MockTime(100.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_fn,
        debounce_seconds=5.0,
    )

    res = sm.handle_trigger(SensoryTriggerType.SLOUCH)

    assert res is not None
    assert res["offense"] == 1
    assert "Slouching detected" in res["warning"]
    mock_feedback.speak_announcement.assert_called_once_with(
        TRIGGER_CONFIGS[SensoryTriggerType.SLOUCH].warning_text
    )


def test_first_offense_fatigue_warning(mock_feedback, mock_core_client):
    time_fn = MockTime(100.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_fn,
        debounce_seconds=5.0,
    )

    res = sm.handle_trigger(SensoryTriggerType.FATIGUE)

    assert res is not None
    assert res["offense"] == 1
    assert "Fatigue detected" in res["warning"]
    mock_feedback.speak_announcement.assert_called_once_with(
        TRIGGER_CONFIGS[SensoryTriggerType.FATIGUE].warning_text
    )


def test_repeat_offense_within_5_minutes(mock_feedback, mock_core_client):
    time_fn = MockTime(100.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_fn,
        debounce_seconds=5.0,
    )

    # Offense 1 at t = 100s
    res1 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert res1["offense"] == 1

    # Advance time by 60 seconds (well within 5m / 300s window)
    time_fn.advance(60.0)

    # Offense 2 at t = 160s
    res2 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert res2 is not None
    assert res2["offense"] == 2
    assert res2["habit_name"] == "Doomscrolling"
    assert res2["penalty_stat"] == "HP"
    assert res2["penalty_amount"] == 10
    assert res2["category"] == "DISCIPLINE"

    mock_core_client.record_bad_habit_offense.assert_called_once_with(
        name="Doomscrolling",
        penalty_stat="HP",
        penalty_amount=10,
        category="DISCIPLINE",
    )


def test_expired_warning_after_5_minutes_becomes_warning_again(mock_feedback, mock_core_client):
    time_fn = MockTime(100.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_fn,
        debounce_seconds=5.0,
        repeat_window_seconds=300.0,
    )

    # Offense 1 at t = 100s
    res1 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert res1["offense"] == 1

    # Advance time by 305 seconds (> 5 minutes)
    time_fn.advance(305.0)

    # New event at t = 405s should be Offense 1 again, not Offense 2
    res2 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert res2 is not None
    assert res2["offense"] == 1
    assert "Phone distraction detected" in res2["warning"]
    mock_core_client.record_bad_habit_offense.assert_not_called()


def test_debounce_ignores_rapid_calls(mock_feedback, mock_core_client):
    time_fn = MockTime(100.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_fn,
        debounce_seconds=15.0,
    )

    res1 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert res1 is not None

    # Immediate call 2 seconds later should be debounced
    time_fn.advance(2.0)
    res2 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert res2 is None
    assert mock_feedback.speak_announcement.call_count == 1
