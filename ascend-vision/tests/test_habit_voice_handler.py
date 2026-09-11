"""Unit tests for HabitVoiceHandler."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from integrations.habit_voice_handler import (
    HabitVoiceHandler,
    extract_habit_details,
    infer_category,
    is_habit_creation_intent,
)


def test_habit_intent_detection():
    assert is_habit_creation_intent("Create a daily habit to study Python for 30 minutes") is True
    assert is_habit_creation_intent("Add morning jog") is True
    assert is_habit_creation_intent("Add habit drink water") is True
    assert is_habit_creation_intent("Create routine to stretch every day") is True
    assert is_habit_creation_intent("Start a new habit to meditate") is True

    assert is_habit_creation_intent("What is the time?") is False
    assert is_habit_creation_intent("Mute audio") is False
    assert is_habit_creation_intent("Status report") is False


def test_category_inference():
    assert infer_category("study python for 30 minutes") == "KNOWLEDGE"
    assert infer_category("read 20 pages of book") == "KNOWLEDGE"
    assert infer_category("morning jog") == "FITNESS"
    assert infer_category("do 50 pushups") == "FITNESS"
    assert infer_category("drink 2 liters of water") == "HEALTH"
    assert infer_category("fix posture sitting tall") == "HEALTH"
    assert infer_category("meditate for 10 minutes") == "DISCIPLINE"
    assert infer_category("journal today's progress") == "DISCIPLINE"
    assert infer_category("check email") == "GENERAL"


def test_extract_habit_details():
    name, category = extract_habit_details("Create a daily habit to study Python for 30 minutes")
    assert name == "Study Python for 30 minutes"
    assert category == "KNOWLEDGE"

    name, category = extract_habit_details("Add morning jog")
    assert name == "Morning jog"
    assert category == "FITNESS"

    name, category = extract_habit_details("Add habit: drink 2 liters of water")
    assert name == "Drink 2 liters of water"
    assert category == "HEALTH"


def test_habit_creation_speaks_canonical_narration_verbatim():
    import asyncio
    mock_feedback = MagicMock()
    mock_feedback.speak_announcement = MagicMock()

    mock_client = MagicMock()
    expected_narration = "Protocol locked: 'Study Python for 30 minutes' successfully registered to daily routines."
    mock_client.create_habit_routine = AsyncMock(return_value={
        "success": True,
        "habitId": "habit-12345",
        "name": "Study Python for 30 minutes",
        "canonicalNarration": expected_narration,
        "idempotentReplay": False,
    })

    handler = HabitVoiceHandler(core_client=mock_client, feedback_service=mock_feedback)
    result = asyncio.run(handler.create_habit("Create a daily habit to study Python for 30 minutes"))

    assert result["success"] is True
    mock_client.create_habit_routine.assert_called_once_with(
        name="Study Python for 30 minutes",
        category="KNOWLEDGE",
        difficulty="MEDIUM",
    )
    # Ensure canonical narration was spoken verbatim through TTS
    mock_feedback.speak_announcement.assert_called_once_with(expected_narration)

