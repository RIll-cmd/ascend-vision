"""Synchronous chat generation shared by voice and dashboard channels."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from collections import deque
import json
import logging
import os
import re
import threading
from typing import Literal

from feedback import ConversationContext, LLMRoaster, OfflineRoaster, get_fallback_chat_reply
from llm_router import get_router


LOG = logging.getLogger(__name__)
CONTEXT_TEXT_BUDGET = 3_000


@dataclass(frozen=True)
class AssistantReply:
    text: str
    source: Literal["model", "offline"]


class AssistantService:
    """Returns one answer per request while protecting a shared generator."""

    def __init__(self, feedback_config, llm_config=None, *, generator=None, memory_store=None):
        self._feedback_config = feedback_config
        self._llm_config = llm_config
        self._generator = generator
        self._generator_source: Literal["model", "offline"] = "model"
        self._memory_store = memory_store
        self._turns: deque[tuple[str, str]] = deque(maxlen=12)
        self._suppress_session = False
        self._lock = threading.RLock()

    def respond(self, user_text, context=None, *, max_words=25) -> AssistantReply:
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("user_text must be nonempty")
        if len(user_text) > 4_000:
            raise ValueError("user_text must be at most 4000 characters")
        if type(max_words) is not int or max_words <= 0:
            raise ValueError("max_words must be a positive integer")

        text = user_text.strip()
        with self._lock:
            memory_enabled = self._memory_enabled()
            if not memory_enabled:
                self._turns.clear()
            command_reply = self._memory_command(text, memory_enabled)
            if command_reply is not None:
                return command_reply
            prompt_context, memory_ready = self._with_memory_context(text, context, memory_enabled)
            try:
                generator = self._get_generator()
                answer = generator.generate_chat(text, prompt_context, max_words=max_words)
                if isinstance(answer, str) and answer.strip():
                    reply = AssistantReply(answer.strip(), self._generator_source)
                    self._record_turn(text, reply.text, memory_enabled and memory_ready)
                    return reply
            except Exception as exc:
                LOG.warning("Assistant generation failed (%s); using offline reply", type(exc).__name__)

            fallback = get_fallback_chat_reply(text, prompt_context)
            if not isinstance(fallback, str) or not fallback.strip():
                raise RuntimeError("Assistant could not produce a reply")
            reply = AssistantReply(fallback.strip(), "offline")
            self._record_turn(text, reply.text, memory_enabled and memory_ready)
            return reply

    def _memory_enabled(self) -> bool:
        if self._memory_store is None:
            return True
        try:
            return self._memory_store.enabled()
        except Exception as exc:
            LOG.warning("Memory unavailable (%s); using stateless chat", type(exc).__name__)
            return False

    def _memory_command(self, text: str, enabled: bool) -> AssistantReply | None:
        normalized = text.lower().rstrip(".!? ")
        if normalized == "do not remember this conversation":
            self._turns.clear()
            self._suppress_session = True
            if self._memory_store is not None:
                try:
                    self._memory_store.discard_proposals()
                except Exception as exc:
                    LOG.warning("Could not clear memory proposals (%s)", type(exc).__name__)
                    return AssistantReply("I stopped keeping this chat in memory, but could not clear pending proposals. Check the dashboard.", "offline")
            return AssistantReply("I won't keep new turns or memory proposals from this conversation.", "offline")

        match = re.fullmatch(r"remember that\s+(.+)", text, re.I | re.S)
        if match:
            if self._suppress_session:
                return AssistantReply("I cannot save a memory from this conversation now.", "offline")
            if not enabled or self._memory_store is None:
                return AssistantReply("Memory is unavailable or disabled, so I did not save that.", "offline")
            try:
                self._memory_store.propose(match.group(1))
            except ValueError:
                return AssistantReply("I cannot save that detail as memory. No memory was created.", "offline")
            except Exception as exc:
                LOG.warning("Memory proposal failed (%s)", type(exc).__name__)
                return AssistantReply("Memory is unavailable, so I did not save that.", "offline")
            return AssistantReply("I prepared that memory. Please approve it in the dashboard before I use it.", "offline")

        if normalized == "what do you remember about me":
            if not enabled or self._memory_store is None:
                return AssistantReply("Memory is unavailable or disabled right now.", "offline")
            try:
                memories = self._memory_store.active()[:5]
            except Exception as exc:
                LOG.warning("Memory list failed (%s)", type(exc).__name__)
                return AssistantReply("I cannot check memories right now.", "offline")
            if not memories:
                return AssistantReply("I have no approved memories about you yet.", "offline")
            return AssistantReply("Approved memories: " + "; ".join(row["text"] for row in memories), "offline")

        match = re.fullmatch(r"forget\s+(.+)", text, re.I | re.S)
        if match:
            if not enabled or self._memory_store is None:
                return AssistantReply("Memory is unavailable or disabled right now.", "offline")
            try:
                matches = self._memory_store.active(match.group(1).strip().rstrip(".!?"))
                if len(matches) == 1 and self._memory_store.delete(matches[0]["id"]):
                    return AssistantReply("I forgot that approved memory.", "offline")
            except Exception as exc:
                LOG.warning("Memory forget failed (%s)", type(exc).__name__)
                return AssistantReply("I could not forget that memory right now.", "offline")
            if len(matches) > 1:
                return AssistantReply("Several memories match. Please choose one to delete in the dashboard.", "offline")
            return AssistantReply("I could not find an approved memory matching that phrase.", "offline")

        if normalized.startswith("correct that memory"):
            return AssistantReply("Please edit the exact approved memory in the dashboard; I won't overwrite it silently.", "offline")
        return None

    def _with_memory_context(self, text, context, enabled):
        if not enabled:
            return context, False
        memories: list[dict] = []
        if self._memory_store is not None:
            try:
                memories = self._memory_store.search(text, limit=3)
            except Exception as exc:
                LOG.warning("Memory retrieval failed (%s); using stateless chat", type(exc).__name__)
                self._turns.clear()
                return context, False
        turns = list(self._turns) if not self._suppress_session else []
        facts = [(row["id"], row["text"]) for row in memories]
        while turns or facts:
            payload = {
                "recent_turns": [{"user": user, "assistant": reply} for user, reply in turns],
                "approved_memories": [{"id": memory_id, "text": value} for memory_id, value in facts],
            }
            if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= CONTEXT_TEXT_BUDGET:
                break
            if turns:
                turns.pop(0)
            else:
                facts.pop()
        if not turns and not facts:
            return context, True
        base = context if isinstance(context, ConversationContext) else ConversationContext(user_query=text)
        return replace(base, recent_turns=tuple(turns), approved_memories=tuple(facts)), True

    def _record_turn(self, text, answer, enabled):
        if enabled and not self._suppress_session:
            self._turns.append((text, answer))

    def _get_generator(self):
        if self._generator is not None:
            return self._generator

        has_keys = any((
            os.environ.get(self._feedback_config.api_key_env, "").strip(),
            os.environ.get("GROQ_API_KEY", "").strip(),
            os.environ.get("CEREBRAS_API_KEY", "").strip(),
        ))
        if has_keys:
            router = get_router(self._llm_config) if self._llm_config is not None else get_router()
            self._generator = LLMRoaster(self._feedback_config, router=router)
            self._generator_source = "model"
        else:
            self._generator = OfflineRoaster(self._feedback_config)
            self._generator_source = "offline"
        return self._generator
