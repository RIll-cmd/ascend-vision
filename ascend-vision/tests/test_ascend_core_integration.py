"""End-to-end integration tests for Ascend Core in Ascend Vision."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from integrations.habit_voice_handler import HabitVoiceHandler
from integrations.warning_state_machine import SensoryTriggerType, WarningFirstStateMachine


class MockTime:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float):
        self.t += s


@pytest.fixture
def mock_core_client():
    client = MagicMock()
    client.create_habit_routine = AsyncMock(return_value={
        "success": True,
        "habitId": "habit-test-1",
        "name": "Study Python for 30 minutes",
        "canonicalNarration": "Protocol locked: 'Study Python for 30 minutes' registered.",
        "idempotentReplay": False,
    })
    client.record_bad_habit_offense = AsyncMock(return_value={
        "success": True,
        "action": "RELAPSE_LOGGED",
        "canonicalNarration": "Protocol breached: 'Doomscrolling' relapse logged. -10 HP deducted.",
    })
    return client


@pytest.fixture
def mock_feedback():
    fb = MagicMock()
    fb.speak_announcement = MagicMock()
    return fb


def test_voice_command_habit_creation_flow(mock_core_client, mock_feedback):
    handler = HabitVoiceHandler(core_client=mock_core_client, feedback_service=mock_feedback)
    res = asyncio.run(handler.create_habit("Create a daily habit to study Python for 30 minutes"))

    assert res["success"] is True
    mock_core_client.create_habit_routine.assert_called_once_with(
        name="Study Python for 30 minutes",
        category="KNOWLEDGE",
        difficulty="MEDIUM",
    )
    mock_feedback.speak_announcement.assert_called_once_with(
        "Protocol locked: 'Study Python for 30 minutes' registered."
    )


def test_cv_phone_warning_and_repeat_offense_flow(mock_core_client, mock_feedback):
    time_source = MockTime(500.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_source,
        debounce_seconds=5.0,
    )

    # First offense: Localized warning chime/voice alert
    offense1 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert offense1["offense"] == 1
    mock_feedback.speak_announcement.assert_called_once_with(
        "Warning: Phone distraction detected. Focus on the task."
    )
    mock_core_client.record_bad_habit_offense.assert_not_called()

    # User repeats behavior 45 seconds later (within 5 minutes)
    time_source.advance(45.0)
    offense2 = sm.handle_trigger(SensoryTriggerType.PHONE)
    assert offense2["offense"] == 2
    assert offense2["habit_name"] == "Doomscrolling"
    assert offense2["penalty_stat"] == "HP"
    assert offense2["penalty_amount"] == 10
    assert offense2["category"] == "DISCIPLINE"

    mock_core_client.record_bad_habit_offense.assert_called_once_with(
        name="Doomscrolling",
        penalty_stat="HP",
        penalty_amount=10,
        category="DISCIPLINE",
    )


def test_cv_slouch_warning_and_repeat_offense_flow(mock_core_client, mock_feedback):
    time_source = MockTime(500.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_source,
        debounce_seconds=5.0,
    )

    offense1 = sm.handle_trigger(SensoryTriggerType.SLOUCH)
    assert offense1["offense"] == 1
    mock_feedback.speak_announcement.assert_called_with(
        "Warning: Slouching detected. Please correct your posture."
    )

    time_source.advance(120.0)
    offense2 = sm.handle_trigger(SensoryTriggerType.SLOUCH)
    assert offense2["offense"] == 2
    assert offense2["habit_name"] == "Bad Posture"
    assert offense2["category"] == "HEALTH"


def test_cv_fatigue_warning_and_repeat_offense_flow(mock_core_client, mock_feedback):
    time_source = MockTime(500.0)
    sm = WarningFirstStateMachine(
        core_client=mock_core_client,
        feedback_service=mock_feedback,
        time_fn=time_source,
        debounce_seconds=5.0,
    )

    offense1 = sm.handle_trigger(SensoryTriggerType.FATIGUE)
    assert offense1["offense"] == 1
    mock_feedback.speak_announcement.assert_called_with(
        "Warning: Fatigue detected. Take a breather or stay alert."
    )

    time_source.advance(60.0)
    offense2 = sm.handle_trigger(SensoryTriggerType.FATIGUE)
    assert offense2["offense"] == 2
    assert offense2["habit_name"] == "Staying Up Late"
    assert offense2["category"] == "HEALTH"
