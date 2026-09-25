"""Policy boundary shared by phone transports before they reach the assistant."""
from __future__ import annotations

import re
from typing import Literal

from assistant.service import AssistantReply, AssistantService
from assistant.session_store import SessionKey


PhoneChannel = Literal["phone_pwa", "discord_dm"]
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MEMORY_ADMIN = re.compile(
    r"\b(?:forget|delete|remove)\s+(?:that\s+|this\s+|the\s+|my\s+)?memory\b"
    r"|\bforget\s+(?!that\s+conversation\b)[^\n]{1,500}"
    r"|\b(?:correct|edit|change|update)\s+(?:that\s+|this\s+|the\s+|my\s+)?memory\b"
    r"|\b(?:approve|reject)\s+(?:that\s+|this\s+|the\s+|pending\s+)?memory\b"
    r"|\bdo\s+not\s+remember\s+this\s+conversation\b",
    re.IGNORECASE,
)
_MEMORY_ADMIN_REPLY = "Memory changes and approvals are only available in the dashboard."


class PhoneMessageHandler:
    """Validate transport identity and policy before invoking shared chat."""

    def __init__(self, assistant_service: AssistantService, *, owner_id: str,
                 max_words: int = 25, allowed_channels: frozenset[str] = frozenset({"phone_pwa"})):
        if not callable(getattr(assistant_service, "respond", None)):
            raise TypeError("assistant_service must provide respond")
        if not callable(getattr(assistant_service, "clear_session", None)):
            raise TypeError("assistant_service must provide clear_session")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be nonempty")
        if type(max_words) is not int or max_words <= 0:
            raise ValueError("max_words must be a positive integer")
        if not allowed_channels or not allowed_channels <= {"phone_pwa", "discord_dm"}:
            raise ValueError("allowed_channels contains an unsupported channel")
        self._assistant_service = assistant_service
        self._owner_id = owner_id
        self._max_words = max_words
        self._allowed_channels = allowed_channels

    def handle(self, owner_id: str, channel: PhoneChannel, session_id: str,
               text: str) -> AssistantReply:
        key = self._validated_session_key(owner_id, channel, session_id)
        if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
            raise ValueError("text must contain between 1 and 4000 characters")
        normalized = text.strip()
        if _MEMORY_ADMIN.search(normalized):
            return AssistantReply(_MEMORY_ADMIN_REPLY, "offline")
        reply = self._assistant_service.respond(
            normalized,
            context=None,
            max_words=self._max_words,
            session_key=key,
        )
        if not isinstance(reply, AssistantReply) or not reply.text.strip():
            raise RuntimeError("Assistant returned an invalid reply")
        return reply

    def clear_session(self, owner_id: str, channel: PhoneChannel, session_id: str) -> None:
        self._assistant_service.clear_session(
            self._validated_session_key(owner_id, channel, session_id)
        )

    def _validated_session_key(self, owner_id, channel, session_id) -> SessionKey:
        if owner_id != self._owner_id:
            raise ValueError("owner is not authorized for this Vision installation")
        if channel not in self._allowed_channels:
            raise ValueError("phone channel is not enabled")
        if not isinstance(session_id, str) or _SESSION_ID.fullmatch(session_id) is None:
            raise ValueError("session_id is invalid")
        return (owner_id, channel, session_id)
