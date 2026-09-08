"""Transient, Core-validated automation proposal orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Protocol

from integrations.ascend_client import AscendConnectionState


SUPPORTED_CAPABILITY_VERSIONS = frozenset({"2026-09-07"})


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
