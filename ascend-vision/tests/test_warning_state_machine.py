"""Direct Vision discipline events never create a negative habit."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from integrations.warning_state_machine import SensoryTriggerType, WarningFirstStateMachine

@pytest.mark.parametrize("trigger,behavior", [
    (SensoryTriggerType.PHONE, "PHONE_USE"),
    (SensoryTriggerType.SLOUCH, "SLOUCHING"),
    (SensoryTriggerType.FATIGUE, "DROWSINESS"),
])
def test_warning_then_penalty_without_habit_creation(trigger, behavior):
    client = MagicMock()
    client.record_discipline_event = AsyncMock(return_value={"success": True, "canonicalNarration": "-8 HP"})
    feedback = MagicMock()
    machine = WarningFirstStateMachine(client, feedback, debounce_seconds=5)
    first = machine.handle_trigger(trigger, now=100)
    second = machine.handle_trigger(trigger, now=160)
    assert (first["stage"], second["stage"]) == ("WARNING", "PENALTY")
    assert first["behavior"] == second["behavior"] == behavior
    assert first["event_id"] != second["event_id"]
    assert client.record_discipline_event.await_count == 2
    warning, penalty = [call.kwargs for call in client.record_discipline_event.await_args_list]
    assert warning["stage"] == "WARNING" and penalty["stage"] == "PENALTY"
    assert all(isinstance(call["observed_at"], datetime) and call["observed_at"].tzinfo for call in (warning, penalty))
    client.create_habit_routine.assert_not_called()
    client.record_bad_habit_offense.assert_not_called()
    feedback.speak_announcement.assert_any_call("-8 HP")

def test_debounce_and_expiry():
    client = MagicMock(record_discipline_event=AsyncMock(return_value={"success": True}))
    machine = WarningFirstStateMachine(client, MagicMock(), debounce_seconds=15)
    assert machine.handle_trigger(SensoryTriggerType.PHONE, now=100)["stage"] == "WARNING"
    assert machine.handle_trigger(SensoryTriggerType.PHONE, now=102) is None
    assert machine.handle_trigger(SensoryTriggerType.PHONE, now=405)["stage"] == "WARNING"
    assert client.record_discipline_event.await_count == 2

def test_behavior_windows_are_independent():
    client = MagicMock(record_discipline_event=AsyncMock(return_value={"success": True}))
    machine = WarningFirstStateMachine(client, MagicMock())
    machine.handle_trigger(SensoryTriggerType.PHONE, now=100)
    assert machine.handle_trigger(SensoryTriggerType.SLOUCH, now=101)["stage"] == "WARNING"
    assert machine.handle_trigger(SensoryTriggerType.PHONE, now=130)["stage"] == "PENALTY"
    assert machine.handle_trigger(SensoryTriggerType.SLOUCH, now=131)["stage"] == "PENALTY"

def test_failed_core_call_does_not_claim_penalty():
    client = MagicMock(record_discipline_event=AsyncMock(side_effect=RuntimeError("offline")))
    feedback = MagicMock()
    machine = WarningFirstStateMachine(client, feedback)
    machine.handle_trigger(SensoryTriggerType.PHONE, now=100)
    machine.handle_trigger(SensoryTriggerType.PHONE, now=130)
    assert feedback.speak_announcement.call_count == 1
