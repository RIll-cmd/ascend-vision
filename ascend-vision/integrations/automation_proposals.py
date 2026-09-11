"""Transient, Core-validated automation proposal orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Protocol

from integrations.ascend_client import AscendConnectionState


SUPPORTED_CAPABILITY_VERSIONS = frozenset({"2026-09-09"})


class ProposalStatus(str, Enum):
    PREVIEW = "preview"
    CREATED = "created"
    CANCELLED = "cancelled"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"
    REAUTH_REQUIRED = "reauth_required"
    ERROR = "error"


@dataclass(frozen=True)
class ProposalResult:
    status: ProposalStatus
    message: str
    preview: dict[str, Any] | None = None
    normalized_proposal: dict[str, Any] | None = None
    requires_confirmation: bool = False


@dataclass(frozen=True)
class HabitAutomationResult:
    created: bool = False
    existing: bool = False
    message: str = ""


class ProposalGenerator(Protocol):
    def generate(self, request: str, *, capabilities: dict[str, Any],
                 eligible_habits: list[dict[str, str]]) -> dict[str, Any]: ...


def is_automation_intent(text: str) -> bool:
    """Route only obvious rule-creation requests into the proposal flow."""
    normalized = text.lower().strip()
    return bool(
        re.search(r"\b(if|when|whenever)\b", normalized)
        and re.search(r"\b(log|record|count)\b", normalized)
        and re.search(r"\b(habit|distraction|automation)\b", normalized)
    ) or bool(re.search(r"\b(turn off|automatically|create automation)\b", normalized))


def explicit_habit_trigger(text: str) -> str | None:
    """Recognize direct requests for the three detector-backed habit rules."""
    normalized = text.lower().strip() if isinstance(text, str) else ""
    if not re.search(r"\b(create|make|set up|setup)\b", normalized):
        return None
    if re.search(r"\b(phone|cell|mobile)\b", normalized):
        return "phone_usage_observed"
    if re.search(r"\b(drowsy|drowsiness|sleep|fatigue)\b", normalized):
        return "drowsiness_observed"
    if re.search(r"\b(posture|slouch|slouching)\b", normalized):
        return "posture_observed"
    return None


class AutomationProposalService:
    """Ensures an AI draft can never bypass Core validation or confirmation."""

    def __init__(self, core_client: Any, character_id: str, generator: ProposalGenerator):
        if not isinstance(character_id, str) or not character_id.strip():
            raise ValueError("character_id must come from authenticated runtime configuration")
        self._core = core_client
        self._character_id = character_id.strip()
        self._generator = generator
        self._pending: ProposalResult | None = None

    @property
    def pending(self) -> ProposalResult | None:
        return self._pending

    def propose(self, request: str) -> ProposalResult:
        preflight = self._preflight(request)
        if preflight is not None:
            return preflight
        capabilities = self._fetch_capabilities()
        if isinstance(capabilities, ProposalResult):
            return capabilities
        habits = self._fetch_eligible_habits()
        if isinstance(habits, ProposalResult):
            return habits
        ambiguity = self._ambiguity(request, habits)
        if ambiguity is not None:
            return ambiguity
        try:
            draft = self._generator.generate(request, capabilities=capabilities, eligible_habits=habits)
        except Exception:
            return ProposalResult(ProposalStatus.ERROR, "I could not safely form that automation proposal.")
        if not isinstance(draft, dict):
            return ProposalResult(ProposalStatus.ERROR, "I could not safely read that automation proposal.")
        if draft.get("kind") == "clarification":
            return ProposalResult(ProposalStatus.CLARIFICATION, str(draft.get("message") or "What details should the automation use?"))
        if draft.get("kind") == "unsupported":
            return ProposalResult(ProposalStatus.UNSUPPORTED, str(draft.get("message") or "That automation action is not currently supported."))
        if draft.get("kind") == "proposal":
            draft = draft.get("draft")
        if not isinstance(draft, dict):
            return ProposalResult(ProposalStatus.ERROR, "I could not safely read that automation proposal.")
        return self._validate({"characterId": self._character_id, **draft})

    def edit(self, instruction: str) -> ProposalResult:
        if self._pending is None or self._pending.normalized_proposal is None:
            return ProposalResult(ProposalStatus.ERROR, "There is no pending automation to edit.")
        count = re.search(r"\b(\d+)\s+times?\b", instruction.lower())
        if count is None:
            return ProposalResult(ProposalStatus.CLARIFICATION, "Tell me the new number of detections, for example 5 times.")
        draft = _copy_json_object(self._pending.normalized_proposal)
        for condition in draft.get("conditions", []):
            if condition.get("type") == "occurrence_count":
                condition["count"] = int(count.group(1))
                return self._validate(draft)
        return ProposalResult(ProposalStatus.CLARIFICATION, "That proposal does not have an editable detection count.")

    def confirm(self, explicit_command: str) -> ProposalResult:
        if self._pending is None or self._pending.normalized_proposal is None:
            return ProposalResult(ProposalStatus.ERROR, "There is no pending automation to create.")
        if explicit_command.lower().strip() != "create automation":
            return ProposalResult(ProposalStatus.CONFIRMATION_REQUIRED,
                                  "Say or select Create Automation to create this rule, or Cancel to discard it.",
                                  preview=self._pending.preview,
                                  normalized_proposal=self._pending.normalized_proposal,
                                  requires_confirmation=True)
        result = self._core.create_automation(self._pending.normalized_proposal)
        if result.state is AscendConnectionState.AUTH_ERROR:
            self._pending = None
            return ProposalResult(ProposalStatus.REAUTH_REQUIRED, "Vision authentication expired. Please sign in to Core again.")
        if result.state is not AscendConnectionState.CONNECTED:
            return ProposalResult(ProposalStatus.ERROR, "Core could not create the automation. Nothing was created.")
        created = ProposalResult(ProposalStatus.CREATED, "Automation created.", normalized_proposal=self._pending.normalized_proposal)
        self._pending = None
        return created

    def cancel(self) -> ProposalResult:
        self._pending = None
        return ProposalResult(ProposalStatus.CANCELLED, "Automation proposal cancelled.")

    def handle_utterance(self, text: str) -> ProposalResult | None:
        """Handle only a clear proposal request or an explicit pending-proposal command."""
        normalized = text.lower().strip() if isinstance(text, str) else ""
        if self._pending is not None:
            if normalized == "cancel":
                return self.cancel()
            if normalized == "create automation":
                return self.confirm(normalized)
            if normalized == "edit":
                return ProposalResult(ProposalStatus.CLARIFICATION,
                                      "Tell me what to change, for example make it 5 times.")
            if re.search(r"\b(make|change|set)\b.*\b\d+\s+times?\b", normalized):
                return self.edit(text)
            return self.confirm(text)
        if is_automation_intent(text):
            return self.propose(text)
        return None

    def _preflight(self, request: str) -> ProposalResult | None:
        if not isinstance(request, str) or not request.strip():
            return ProposalResult(ProposalStatus.ERROR, "Please describe the automation you want.")
        normalized = request.lower()
        if "too much" in normalized:
            return ProposalResult(ProposalStatus.CLARIFICATION,
                                  "How should I define too much—for example, 3 detections within 30 minutes?")
        if re.search(r"\b(turn off|lock|block|disable)\b", normalized):
            return ProposalResult(ProposalStatus.UNSUPPORTED,
                                  "That automation action is not currently supported.")
        return None

    def _fetch_capabilities(self) -> dict[str, Any] | ProposalResult:
        result = self._core.get_automation_capabilities()
        if result.state is AscendConnectionState.AUTH_ERROR:
            return ProposalResult(ProposalStatus.REAUTH_REQUIRED, "Vision authentication is required before creating automations.")
        if result.state is not AscendConnectionState.CONNECTED or not isinstance(result.payload, dict):
            return ProposalResult(ProposalStatus.ERROR, "Core automation capabilities are unavailable right now.")
        if result.payload.get("version") not in SUPPORTED_CAPABILITY_VERSIONS:
            return ProposalResult(ProposalStatus.ERROR, "Automation creation is temporarily unavailable while Vision updates for Core's capability version.")
        return result.payload

    def _fetch_eligible_habits(self) -> list[dict[str, str]] | ProposalResult:
        result = self._core.get_eligible_habits(self._character_id)
        if result.state is AscendConnectionState.AUTH_ERROR:
            return ProposalResult(ProposalStatus.REAUTH_REQUIRED, "Vision authentication is required before creating automations.")
        if result.state is not AscendConnectionState.CONNECTED or not isinstance(result.payload, dict):
            return ProposalResult(ProposalStatus.ERROR, "Eligible habits are unavailable right now.")
        habits = result.payload.get("habits")
        if not isinstance(habits, list):
            return ProposalResult(ProposalStatus.ERROR, "Core returned an invalid eligible-habits response.")
        cleaned = [{"id": item["id"], "name": item["name"]} for item in habits
                   if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("name"), str)]
        return cleaned

    def _ambiguity(self, request: str, habits: list[dict[str, str]]) -> ProposalResult | None:
        normalized = request.lower()
        if len(habits) > 1 and not any(habit["name"].lower() in normalized for habit in habits):
            return ProposalResult(ProposalStatus.CLARIFICATION, "Which eligible bad habit should this automation log?")
        if not habits:
            return ProposalResult(ProposalStatus.ERROR, "You do not have an eligible negative habit for this automation.")
        return None

    def _validate(self, draft: dict[str, Any]) -> ProposalResult:
        result = self._core.validate_automation_proposal(draft)
        if result.state is AscendConnectionState.AUTH_ERROR:
            return ProposalResult(ProposalStatus.REAUTH_REQUIRED, "Vision authentication expired. Please sign in to Core again.")
        payload = result.payload if isinstance(result.payload, dict) else {}
        if result.state is not AscendConnectionState.CONNECTED or not payload.get("valid"):
            return ProposalResult(ProposalStatus.ERROR, _validation_message(payload))
        normalized = payload.get("normalizedProposal")
        preview = payload.get("preview")
        if payload.get("requiresConfirmation") is not True or not isinstance(normalized, dict) or not isinstance(preview, dict):
            return ProposalResult(ProposalStatus.ERROR, "Core returned an invalid automation preview.")
        self._pending = ProposalResult(ProposalStatus.PREVIEW, _preview_message(preview),
                                       preview=preview, normalized_proposal=normalized, requires_confirmation=True)
        return self._pending


class HabitAutomationService:
    """Creates one validated default rule per supported Vision detection."""

    _HABIT_KEYWORDS = {
        "phone_usage_observed": ("phone", "distraction"),
        "drowsiness_observed": ("drows", "sleep", "fatigue"),
        "posture_observed": ("posture", "slouch"),
    }

    def __init__(self, core_client: Any, character_id: str, *, repeat_threshold: int = 2):
        if not isinstance(character_id, str) or not character_id.strip():
            raise ValueError("character_id must come from authenticated runtime configuration")
        if type(repeat_threshold) is not int or repeat_threshold < 1:
            raise ValueError("repeat_threshold must be a positive integer")
        self._core = core_client
        self._character_id = character_id.strip()
        self._repeat_threshold = repeat_threshold
        self._counts: dict[str, int] = {}
        self._created: set[str] = set()

    def handle_detection(self, trigger_type: str) -> HabitAutomationResult:
        if trigger_type not in self._HABIT_KEYWORDS:
            return HabitAutomationResult(message="That detection does not have an automatic habit rule.")
        if trigger_type in self._created:
            return HabitAutomationResult(message="The habit automation is already active.")
        existing = self._get_existing(trigger_type)
        if existing is True:
            self._created.add(trigger_type)
            return HabitAutomationResult(existing=True, message="The habit automation is already active.")
        if existing is None:
            return HabitAutomationResult(message="I could not check Core for the habit automation.")
        self._counts[trigger_type] = self._counts.get(trigger_type, 0) + 1
        if self._counts[trigger_type] < self._repeat_threshold:
            return HabitAutomationResult(message="I detected that habit. Put it right and I will set up the tracker if it continues.")
        return self._create(trigger_type)

    def create_for_trigger(self, trigger_type: str) -> HabitAutomationResult:
        """Create a supported habit rule immediately for an explicit voice request."""
        if trigger_type not in self._HABIT_KEYWORDS:
            return HabitAutomationResult(message="That habit automation is not supported yet.")
        self._counts[trigger_type] = self._repeat_threshold
        return self.handle_detection(trigger_type)

    def _create(self, trigger_type: str) -> HabitAutomationResult:
        draft = self._default_draft(trigger_type)
        habits = self._eligible_habits()
        if habits is None:
            return HabitAutomationResult(message="I detected the habit, but eligible bad habits are unavailable in Core.")
        habit = self._choose_habit(habits, trigger_type)
        if habit is None:
            return HabitAutomationResult(message="I detected the habit, but could not find a matching bad habit in Core.")
        draft["actions"] = [{"type": "log_bad_habit", "habitId": habit["id"]}]
        validation = self._core.validate_automation_proposal({"characterId": self._character_id, **draft})
        if validation.state is not AscendConnectionState.CONNECTED or not isinstance(validation.payload, dict) or not validation.payload.get("valid"):
            return HabitAutomationResult(message="Core could not validate the automatic habit automation.")
        normalized = validation.payload.get("normalizedProposal")
        if not isinstance(normalized, dict):
            return HabitAutomationResult(message="Core returned an invalid automatic habit automation.")
        result = self._core.create_automation(normalized)
        if result.state is not AscendConnectionState.CONNECTED:
            return HabitAutomationResult(message="I detected the habit, but Core could not create its automation.")
        self._created.add(trigger_type)
        return HabitAutomationResult(created=True, message=f"I created the {trigger_type.replace('_observed', '').replace('_', ' ')} habit automation. You can adjust its fields in Core.")

    def _get_existing(self, trigger_type: str) -> bool | None:
        result = self._core.get_automations(self._character_id)
        if result.state is not AscendConnectionState.CONNECTED or not isinstance(result.payload, dict):
            return None
        automations = result.payload.get("automations")
        if not isinstance(automations, list):
            return None
        return any(isinstance(item, dict) and item.get("enabled", True) is not False
                   and item.get("triggerType") == trigger_type for item in automations)

    def _eligible_habits(self) -> list[dict[str, str]] | None:
        result = self._core.get_eligible_habits(self._character_id)
        if result.state is not AscendConnectionState.CONNECTED or not isinstance(result.payload, dict):
            return None
        habits = result.payload.get("habits")
        if not isinstance(habits, list):
            return None
        return [item for item in habits if isinstance(item, dict) and isinstance(item.get("id"), str)
                and isinstance(item.get("name"), str)]

    def _choose_habit(self, habits: list[dict[str, str]], trigger_type: str) -> dict[str, str] | None:
        keywords = self._HABIT_KEYWORDS[trigger_type]
        matching = [habit for habit in habits if any(word in habit["name"].lower() for word in keywords)]
        return matching[0] if matching else (habits[0] if len(habits) == 1 else None)

    @staticmethod
    def _default_draft(trigger_type: str) -> dict[str, Any]:
        return {
            "name": f"Vision {trigger_type.replace('_observed', '').replace('_', ' ').title()} Habit",
            "enabled": True,
            "triggerType": trigger_type,
            "matchMode": "all",
            "conditions": [],
            "actions": [],
            "cooldownSeconds": 1800,
        }


def _validation_message(payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        if errors[0].get("code") == "target_not_eligible":
            return "That habit is not eligible for this automation. Please choose one of your eligible bad habits."
        message = errors[0].get("message")
        if isinstance(message, str) and message:
            return message
    return "Core could not validate that automation proposal. Nothing was created."


def _preview_message(preview: dict[str, Any]) -> str:
    target = preview.get("targetHabit") if isinstance(preview.get("targetHabit"), dict) else {}
    execution = preview.get("execution") if isinstance(preview.get("execution"), dict) else {}
    habit = target.get("name") if isinstance(target.get("name"), str) else "the selected bad habit"
    trigger = execution.get("triggerType") if isinstance(execution.get("triggerType"), str) else "the configured trigger"
    cooldown = execution.get("cooldownSeconds")
    cooldown_text = f" with a {cooldown}-second cooldown" if isinstance(cooldown, int) else ""
    return (f"Automation preview: when {trigger} matches, log {habit}{cooldown_text}. "
            "Say Create Automation to confirm, Edit to change it, or Cancel.")


def _copy_json_object(value: dict[str, Any]) -> dict[str, Any]:
    import json
    return json.loads(json.dumps(value))
