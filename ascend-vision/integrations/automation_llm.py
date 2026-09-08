"""Constrained structured-output adapter for automation proposals."""
from __future__ import annotations

import json
from typing import Any


class StructuredAutomationProposalGenerator:
    """Gives the model only the request plus Core-owned proposal vocabulary."""

    def __init__(self, router: Any):
        self._router = router

    def generate(self, request: str, *, capabilities: dict[str, Any],
                 eligible_habits: list[dict[str, str]]) -> dict[str, Any]:
        if not isinstance(request, str) or not request.strip():
            raise ValueError("Automation request must be nonempty")
        context = {
            "request": request.strip(),
            "capabilities": capabilities,
            "eligibleHabits": eligible_habits,
        }
        prompt = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        system_prompt = (
            "You generate a non-executable automation proposal for Ascend Vision. "
            "Return one JSON object only, using structured output. The object must be either "
            "{\"kind\":\"proposal\",\"draft\":{...}} with a prospective Core automation body that omits characterId, "
            "{\"kind\":\"clarification\",\"message\":\"...\"}, or "
            "{\"kind\":\"unsupported\",\"message\":\"...\"}. "
            "Use only the supplied Core capabilities and eligibleHabits. Never invent a trigger, field, operator, "
            "condition type, action, or habit ID. Do not include secrets, identity, tokens, observations, or explanations."
        )
        result = self._router.generate_structured_response(
            prompt=prompt, system_prompt=system_prompt, task="reasoning", max_tokens=500,
        )
        if not isinstance(result, dict):
            raise ValueError("Provider returned malformed structured automation output")
        return result
