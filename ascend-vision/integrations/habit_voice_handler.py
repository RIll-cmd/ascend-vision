"""Voice tool handler for voice-commanded habit and routine creation in Ascend Core."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

LOG = logging.getLogger(__name__)

# Category mapping keywords
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "KNOWLEDGE": [
        "study", "studying", "python", "code", "coding", "read", "reading",
        "book", "books", "learn", "learning", "research", "homework", "review",
        "write", "writing", "japanese", "language", "math", "algorithms"
    ],
    "FITNESS": [
        "jog", "jogging", "run", "running", "gym", "workout", "working out",
        "walk", "walking", "stretch", "stretching", "pushup", "pushups",
        "pullup", "pullups", "cardio", "exercise", "exercising", "lift",
        "lifting", "swim", "swimming", "bike", "cycling"
    ],
    "HEALTH": [
        "water", "drink water", "hydrate", "sleep", "posture", "eat",
        "eating", "diet", "vegetable", "vegetables", "fruit", "fruits",
        "floss", "flossing", "brush teeth", "vitamins", "medicine", "sunlight"
    ],
    "DISCIPLINE": [
        "meditate", "meditation", "focus", "journal", "journaling", "wake up",
        "organize", "clean", "cleaning", "plan", "planning", "budget", "budgeting",
        "cold shower", "no phone", "deep work"
    ],
}

# Regex to detect habit creation intent
HABIT_INTENT_PATTERN = re.compile(
    r"\b(?:create|add|start|make|track|set\s+up|setup|new)\b.*\b(?:habit|routine)\b|"
    r"^(?:add|create|start)\s+(?:daily\s+|morning\s+|evening\s+)?(?:jog|run|workout|meditation|study|reading|stretch|gym)\b",
    re.IGNORECASE,
)

# Prefix stripper to extract the habit name
PREFIX_PATTERNS = [
    r"^(?:please\s+)?(?:can\s+you\s+)?(?:create|add|start|make|track|set\s+up|setup)\s+(?:a\s+)?(?:new\s+)?(?:daily\s+)?(?:habit|routine)(?:\s+to|\s+called|\s*:\s*|\s+for|\s+of)?\s+",
    r"^(?:please\s+)?(?:new\s+)(?:daily\s+)?(?:habit|routine)(?:\s*:\s*|\s+to|\s+called)?\s+",
    r"^(?:please\s+)?(?:add|create|start)\s+",
]


def is_habit_creation_intent(text: str) -> bool:
    """Return True if the spoken text expresses an intent to create a habit or routine."""
    if not text or not text.strip():
        return False
    normalized = text.lower().strip()
    return bool(HABIT_INTENT_PATTERN.search(normalized))


def infer_category(text: str) -> str:
    """Infer the RPG habit category based on keywords in the text."""
    lower_text = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", lower_text):
                return category
    return "GENERAL"


def extract_habit_details(text: str) -> tuple[str, str]:
    """Extract the clean habit name and category from a natural language utterance.

    Returns:
        tuple[str, str]: (name, category)
    """
    raw = text.strip()
    cleaned = raw

    for pattern in PREFIX_PATTERNS:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            cleaned = cleaned[match.end():].strip()
            break

    # Strip any trailing polite phrases or punctuation
    cleaned = re.sub(r"[,.!?]+$", "", cleaned).strip()
    cleaned = re.sub(r"\s+please$", "", cleaned, flags=re.IGNORECASE).strip()

    # If the remaining text is empty or just generic words, fallback to raw
    if not cleaned or len(cleaned) < 2:
        cleaned = raw

    # Capitalize the first letter for clean presentation
    habit_name = cleaned[0].upper() + cleaned[1:] if cleaned else "New Habit"
    category = infer_category(raw)

    return habit_name, category


class HabitVoiceHandler:
    """Coordinates voice habit creation through AscendCoreVisionClient and TTS narration."""

    def __init__(self, core_client, feedback_service=None, async_runner=None):
        self.core_client = core_client
        self.feedback_service = feedback_service
        self.async_runner = async_runner

    async def create_habit(self, text: str) -> dict[str, Any]:
        """Async worker to parse and call core_client.create_habit_routine."""
        name, category = extract_habit_details(text)
        LOG.info("Creating habit via Core: name='%s', category='%s'", name, category)

        try:
            result = await self.core_client.create_habit_routine(
                name=name,
                category=category,
                difficulty="MEDIUM",
            )
        except Exception as exc:
            LOG.error("Failed to execute create_habit_routine: %s", exc)
            err_msg = f"Unable to reach Ascend Core to create habit '{name}'."
            if self.feedback_service:
                self.feedback_service.speak_announcement(err_msg)
            return {"success": False, "reason": "EXCEPTION", "message": err_msg, "error": str(exc)}

        if result.get("success"):
            narration = result.get("canonicalNarration")
            LOG.info("Habit created successfully. Canonical narration: %s", narration)
            if narration and self.feedback_service:
                self.feedback_service.speak_announcement(narration)
        else:
            msg = result.get("message", "Habit creation failed.")
            LOG.warning("Habit creation not successful: %s", msg)
            if self.feedback_service:
                self.feedback_service.speak_announcement(msg)

        return result

    def handle_voice_utterance(self, text: str) -> bool:
        """Synchronous dispatcher called from voice thread. Returns True if handled."""
        if not is_habit_creation_intent(text):
            return False

        if self.core_client is None:
            LOG.warning("Habit creation requested but core_client is not configured.")
            if self.feedback_service:
                self.feedback_service.speak_announcement(
                    "Ascend Core is not configured for habit creation."
                )
            return True

        if self.async_runner is not None:
            self.async_runner.submit(self.create_habit(text))
        else:
            import asyncio
            asyncio.run(self.create_habit(text))

        return True
