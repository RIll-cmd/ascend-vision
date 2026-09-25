"""Deterministic intent and wording for Ascend Hub's authoritative status shelf."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from integrations.status_shelf import ShelfService, ShelfSnapshot


_STATUS_WORDS = re.compile(
    r"\b(?:status|doing|working|idle|online|offline|available|finish|finished|done|blocked|stuck|progress)\b",
    re.I,
)
_SUBJECT_WORDS = re.compile(
    r"\b(?:ascend\s+hub|ascend\s+core|ascend\s+vision|antigravity|codex(?:\s+cli)?|ais?|agents?)\b",
    re.I,
)
_COMPLETION_WORDS = re.compile(r"\b(?:finish|finished|done|completed|complete)\b", re.I)
_TARGETS = (
    (re.compile(r"\bantigravity\b", re.I), "antigravity"),
    (re.compile(r"\bcodex\s+cli\b", re.I), "codex cli"),
    (re.compile(r"\bcodex\b", re.I), "codex"),
    (re.compile(r"\bascend\s+core\b", re.I), "ascend core"),
    (re.compile(r"\bascend\s+vision\b", re.I), "ascend vision"),
)
_KNOWN_NAMES = {
    "antigravity": "Antigravity",
    "codex cli": "Codex CLI",
    "codex": "Codex",
    "ascend core": "Ascend Core",
    "ascend vision": "Ascend Vision",
}
_STATE_TEXT = {
    "idle": "idle",
    "working": "working",
    "stuck": "blocked",
    "offline": "offline",
}


@dataclass(frozen=True)
class StatusIntent:
    target: str | None
    asks_completion: bool = False


def parse_status_intent(text: str) -> StatusIntent | None:
    if not isinstance(text, str) or not _STATUS_WORDS.search(text) or not _SUBJECT_WORDS.search(text):
        return None
    target = next((name for pattern, name in _TARGETS if pattern.search(text)), None)
    return StatusIntent(target, bool(_COMPLETION_WORDS.search(text)))


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _name(service: ShelfService) -> str:
    normalized = _normalized(service.service_id)
    for key, label in _KNOWN_NAMES.items():
        if normalized == key or normalized.startswith(key + " "):
            return label
    return " ".join(word.upper() if word == "ai" else word.capitalize()
                    for word in normalized.split())


def _matches(service: ShelfService, target: str) -> bool:
    haystack = " " + _normalized(service.service_id + " " + service.instance_id) + " "
    return " " + target + " " in haystack


def render_status_answer(intent: StatusIntent, snapshot: ShelfSnapshot) -> str:
    if intent.target:
        services = [row for row in snapshot.services if _matches(row, intent.target)]
        if not services:
            name = _KNOWN_NAMES.get(intent.target, intent.target.title())
            return f"I couldn't find {name} in Ascend Hub's status shelf."
    else:
        services = [row for row in snapshot.services
                    if row.service_type in {"agent", "assistant", "bot", "vision"}]
        if not services:
            return "Ascend Hub has no AI agent status to report."

    services.sort(key=lambda row: (_name(row), row.instance_id))
    duplicate_names = Counter(_name(row) for row in services)
    shown = services[:6]
    parts = []
    for row in shown:
        label = _name(row)
        if duplicate_names[label] > 1:
            label += f" ({row.instance_id})"
        state = _STATE_TEXT.get(row.state)
        parts.append(f"{label} is {state}" if state else f"{label}'s status cannot be verified")
    answer = "; ".join(parts) + "."
    if len(services) > len(shown):
        answer += f" {len(services) - len(shown)} more instances are on the shelf."
    if intent.asks_completion and any(row.state != "working" for row in shown):
        answer += " The current status does not confirm whether its last task finished."
    return answer
