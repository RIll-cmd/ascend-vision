"""Integration boundary: Vision warning/penalty events are direct Core writes."""
from unittest.mock import AsyncMock, MagicMock
from integrations.warning_state_machine import SensoryTriggerType, WarningFirstStateMachine

def test_warning_then_penalty_uses_direct_event_api():
    client = MagicMock()
    client.record_discipline_event = AsyncMock(return_value={"success": True, "canonicalNarration": "-7 HP"})
    feedback = MagicMock()
    machine = WarningFirstStateMachine(client, feedback)
    machine.handle_trigger(SensoryTriggerType.PHONE, now=100)
    machine.handle_trigger(SensoryTriggerType.PHONE, now=130)
    assert client.record_discipline_event.await_count == 2
    client.create_habit_routine.assert_not_called()
    client.record_bad_habit_offense.assert_not_called()
    feedback.speak_announcement.assert_any_call("-7 HP")
