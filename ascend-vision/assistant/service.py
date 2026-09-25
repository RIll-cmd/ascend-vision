"""Synchronous chat generation shared by voice and dashboard channels."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import threading
from typing import Literal

from feedback import LLMRoaster, OfflineRoaster, get_fallback_chat_reply
from llm_router import get_router


LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssistantReply:
    text: str
    source: Literal["model", "offline"]


class AssistantService:
    """Returns one answer per request while protecting a shared generator."""

    def __init__(self, feedback_config, llm_config=None, *, generator=None):
        self._feedback_config = feedback_config
        self._llm_config = llm_config
        self._generator = generator
        self._generator_source: Literal["model", "offline"] = "model"
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
            try:
                generator = self._get_generator()
                answer = generator.generate_chat(text, context, max_words=max_words)
                if isinstance(answer, str) and answer.strip():
                    return AssistantReply(answer.strip(), self._generator_source)
            except Exception as exc:
                LOG.warning("Assistant generation failed (%s); using offline reply", type(exc).__name__)

            fallback = get_fallback_chat_reply(text, context)
            if not isinstance(fallback, str) or not fallback.strip():
                raise RuntimeError("Assistant could not produce a reply")
            return AssistantReply(fallback.strip(), "offline")

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
