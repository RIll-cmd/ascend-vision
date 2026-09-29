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
import time
import uuid
from typing import Literal

from assistant.context_packet import (
    build_context_packet, render_activity_answer, render_break_answer,
    validate_context_read_response,
)
from assistant.context_screen_policy import is_screen_inspection_request
from assistant.browser_intent import parse_browser_intent
from assistant.hub_status import parse_status_intent, render_status_answer
from assistant.session_store import SessionContextStore, SessionKey
from assistant.tool_runtime import ToolRuntime
from integrations.vision_query_reader import MissionSnapshot, render_missions_answer
from feedback import ConversationContext, LLMRoaster, OfflineRoaster, get_fallback_chat_reply
from llm_router import get_router
from browser.contracts import BrowserTaskRequest
from browser.service import TaskQueueFull


LOG = logging.getLogger(__name__)
CONTEXT_TEXT_BUDGET = 3_000
STATUS_UNAVAILABLE_TEXT = "I cannot verify Ascend Hub AI status right now."
MISSIONS_UNAVAILABLE_TEXT = "I cannot verify today's Ascend Core missions right now."
LAPTOP_CONTEXT_LOCAL_ONLY_TEXT = "Laptop context is not shared with this phone or Discord session. Enable context sharing on the laptop to allow it."
SCREEN_INSPECTION_LOCAL_ONLY_TEXT = "Screen inspection can only be started locally in Vision; screen contents are never shared to phone or Discord."
LAPTOP_CONTEXT_UNAVAILABLE_TEXT = "I cannot read current laptop context right now."
DAILY_REVIEW_LOCAL_ONLY_TEXT = "Daily activity history is local to the laptop and is not shared with this phone or Discord session."
DAILY_REVIEW_UNAVAILABLE_TEXT = "I cannot read the local daily activity summary right now."
_DAILY_REVIEW_QUESTION = re.compile(
    r"\b(?:review|summari[sz]e|recap)\s+(?:my\s+)?(?:day|today)\b|"
    r"\bhow\s+was\s+my\s+day\b|\bdaily\s+activity\s+summary\b", re.I,
)
_ACTIVITY_QUESTION = re.compile(
    r"\b(?:what\s+am\s+i\s+doing|what(?:'s|\s+is)\s+my\s+(?:current\s+)?activity|"
    r"what(?:'s|\s+is)\s+happening\s+at\s+my\s+desk)\b", re.I,
)
_BREAK_QUESTION = re.compile(r"\b(?:am\s+i\s+(?:on|taking)\s+a\s+break|did\s+i\s+declare\s+a\s+break)\b", re.I)
_MISSIONS_QUESTION = re.compile(
    r"\b(?:what|which|list|show|tell\s+me)\b.{0,100}\bmissions?\b|"
    r"\bmissions?\b.{0,50}\b(?:today|current|active|have)\b", re.I,
)
_MISSION_MUTATION = re.compile(r"\b(?:complete|finish|claim|accept|skip|delete|cancel)\b", re.I)
_LOCAL_CHANNELS = frozenset({"voice", "dashboard"})


def _is_local_session(session_key: SessionKey | None) -> bool:
    """Allow laptop sensors only for explicitly local channels (and legacy local calls)."""
    return session_key is None or session_key[1] in _LOCAL_CHANNELS


@dataclass(frozen=True)
class AssistantReply:
    text: str
    source: Literal["model", "offline", "tool"]


class AssistantService:
    """Returns one answer per request while protecting a shared generator."""

    def __init__(self, feedback_config, llm_config=None, *, generator=None, memory_store=None,
                 tool_runtime: ToolRuntime | None = None,
                 session_store: SessionContextStore | None = None,
                 context_provider=None, screen_inspector=None,
                 phone_context_sharing_enabled: bool = False,
                 daily_summary_provider=None, browser_task_client=None,
                 browser_automation_config=None, browser_task_submitted=None):
        self._feedback_config = feedback_config
        self._llm_config = llm_config
        self._generator = generator
        self._generator_source: Literal["model", "offline"] = "model"
        self._memory_store = memory_store
        self._tool_runtime = tool_runtime
        self._context_provider = context_provider
        self._screen_inspector = screen_inspector
        self._daily_summary_provider = daily_summary_provider
        self._browser_task_client = browser_task_client
        self._browser_automation_config = browser_automation_config
        self._browser_task_submitted = browser_task_submitted
        if type(phone_context_sharing_enabled) is not bool:
            raise ValueError("phone_context_sharing_enabled must be a boolean")
        self._phone_context_sharing_enabled = phone_context_sharing_enabled
        self._session_store = session_store or SessionContextStore()
        self._turns: deque[tuple[str, str]] = deque(maxlen=12)
        self._suppress_session = False
        self._lock = threading.RLock()

    def respond(self, user_text, context=None, *, max_words=25,
                session_key: SessionKey | None = None,
                trusted_laptop_context: dict | None = None) -> AssistantReply:
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError("user_text must be nonempty")
        if len(user_text) > 4_000:
            raise ValueError("user_text must be at most 4000 characters")
        if type(max_words) is not int or max_words <= 0:
            raise ValueError("max_words must be a positive integer")

        text = user_text.strip()
        local_session = _is_local_session(session_key)
        browser_goal = parse_browser_intent(text)
        if browser_goal is not None:
            return self._submit_browser_task(browser_goal, session_key, local_session)
        shared_phone_session = bool(
            session_key is not None and session_key[1] == "phone_pwa"
            and self._phone_context_sharing_enabled
        )
        if trusted_laptop_context is not None:
            if session_key is None or session_key[1] != "discord_dm":
                raise ValueError("remote laptop context is only valid for a linked Discord session")
            trusted_laptop_context = validate_context_read_response(trusted_laptop_context)
        with self._lock:
            memory_enabled = self._memory_enabled()
            if not memory_enabled:
                self._turns.clear()
                if session_key is not None:
                    self._session_store.clear(session_key)
            command_reply = self._memory_command(
                text, memory_enabled, max_words, session_key=session_key,
            )
            if command_reply is not None:
                return command_reply
            if _DAILY_REVIEW_QUESTION.search(text):
                if not local_session:
                    return AssistantReply(DAILY_REVIEW_LOCAL_ONLY_TEXT, "offline")
                if self._daily_summary_provider is None:
                    return AssistantReply(DAILY_REVIEW_UNAVAILABLE_TEXT, "offline")
                try:
                    answer = self._daily_summary_provider()
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError("daily summary provider returned no text")
                    return AssistantReply(answer, "tool")
                except Exception as exc:
                    LOG.warning("Daily summary unavailable (%s)", type(exc).__name__)
                    return AssistantReply(DAILY_REVIEW_UNAVAILABLE_TEXT, "offline")
            status_intent = parse_status_intent(text)
            if status_intent is not None:
                if self._tool_runtime is None:
                    return AssistantReply(STATUS_UNAVAILABLE_TEXT, "offline")
                try:
                    snapshot = self._tool_runtime.start_request().call("hub_status", {})
                    return AssistantReply(render_status_answer(status_intent, snapshot), "tool")
                except Exception as exc:
                    LOG.warning("Hub status unavailable (%s)", type(exc).__name__)
                    return AssistantReply(STATUS_UNAVAILABLE_TEXT, "offline")
            if is_screen_inspection_request(text):
                if not local_session:
                    return AssistantReply(SCREEN_INSPECTION_LOCAL_ONLY_TEXT, "offline")
                if self._screen_inspector is None:
                    return AssistantReply("User-requested screen inspection is unavailable right now.", "offline")
                try:
                    observation = self._screen_inspector.inspect_once()
                    category = observation.category.lower().replace("_", " ")
                    observed = observation.observed_at.astimezone().strftime("%H:%M:%S")
                    line = " ".join(observation.observation.split())[:240]
                    return AssistantReply(
                        f"One-time screen capture at {observed} was immediately discarded: {category}. "
                        f"Untrusted visual observation: {line} This estimate does not prove task progress.",
                        "tool",
                    )
                except Exception as exc:
                    LOG.warning("User-requested screen inspection unavailable (%s)", type(exc).__name__)
                    return AssistantReply("I could not complete the requested screen check.", "offline")
            if _MISSIONS_QUESTION.search(text) and not _MISSION_MUTATION.search(text):
                if self._tool_runtime is None:
                    return AssistantReply(MISSIONS_UNAVAILABLE_TEXT, "offline")
                try:
                    snapshot = self._tool_runtime.start_request().call("missions_summary", {})
                    if not isinstance(snapshot, MissionSnapshot):
                        raise TypeError("invalid missions result")
                    return AssistantReply(render_missions_answer(snapshot), "tool")
                except Exception as exc:
                    LOG.warning("Core missions unavailable (%s)", type(exc).__name__)
                    return AssistantReply(MISSIONS_UNAVAILABLE_TEXT, "offline")
            if _ACTIVITY_QUESTION.search(text) or _BREAK_QUESTION.search(text):
                if not (local_session or shared_phone_session or trusted_laptop_context is not None):
                    return AssistantReply(LAPTOP_CONTEXT_LOCAL_ONLY_TEXT, "offline")
                packet = trusted_laptop_context or self._read_laptop_context()
                if packet is None:
                    return AssistantReply(LAPTOP_CONTEXT_UNAVAILABLE_TEXT, "offline")
                answer = render_break_answer(packet) if _BREAK_QUESTION.search(text) else render_activity_answer(packet)
                return AssistantReply(answer, "tool")
            prompt_context, memory_ready = self._with_memory_context(
                text, context, memory_enabled, session_key=session_key,
            )
            if local_session or shared_phone_session:
                packet = self._read_laptop_context()
                if packet is not None:
                    base = prompt_context if isinstance(prompt_context, ConversationContext) else ConversationContext(user_query=text)
                    prompt_context = replace(base, laptop_context=packet)
            elif trusted_laptop_context is not None:
                base = prompt_context if isinstance(prompt_context, ConversationContext) else ConversationContext(user_query=text)
                prompt_context = replace(base, laptop_context=trusted_laptop_context)
            elif isinstance(prompt_context, ConversationContext) and prompt_context.laptop_context is not None:
                prompt_context = replace(prompt_context, laptop_context=None)
            try:
                generator = self._get_generator()
                answer = generator.generate_chat(text, prompt_context, max_words=max_words)
                if isinstance(answer, str) and answer.strip():
                    reply = AssistantReply(answer.strip(), self._generator_source)
                    self._record_turn(
                        text, reply.text, memory_enabled and memory_ready, session_key=session_key,
                    )
                    return reply
            except Exception as exc:
                LOG.warning("Assistant generation failed (%s); using offline reply", type(exc).__name__)

            fallback = get_fallback_chat_reply(text, prompt_context)
            if not isinstance(fallback, str) or not fallback.strip():
                raise RuntimeError("Assistant could not produce a reply")
            reply = AssistantReply(fallback.strip(), "offline")
            self._record_turn(
                text, reply.text, memory_enabled and memory_ready, session_key=session_key,
            )
            return reply

    def _submit_browser_task(self, goal: str, session_key: SessionKey | None,
                             local_session: bool) -> AssistantReply:
        if not local_session or session_key is None or session_key[1] not in _LOCAL_CHANNELS:
            return AssistantReply('Browser research is only available from local laptop chat or voice.', 'offline')
        config = self._browser_automation_config
        if config is None or not getattr(config, 'enabled', False):
            return AssistantReply('Local browser research is not enabled on this laptop yet.', 'offline')
        if self._browser_task_client is None:
            return AssistantReply('The local browser service is unavailable right now.', 'offline')
        request = BrowserTaskRequest.from_payload({
            'schema_version': 1,
            'task_id': uuid.uuid4().hex,
            'session_key': list(session_key),
            'goal': goal,
            'provider': config.provider,
            'scope_mode': config.scope_mode,
            'expires_at': time.time() + config.task_timeout_seconds,
        })
        try:
            receipt = self._browser_task_client.submit(request)
        except TaskQueueFull:
            return AssistantReply('The browser task queue is full. Stop or wait for a task to finish first.', 'offline')
        except Exception as exc:
            LOG.info('Browser task submission unavailable (%s)', type(exc).__name__)
            return AssistantReply('I could not start the browser research task.', 'offline')
        if session_key[1] == 'voice' and callable(self._browser_task_submitted):
            try:
                self._browser_task_submitted(receipt.task_id, session_key)
            except Exception as exc:
                LOG.info('Browser voice completion watcher unavailable (%s)', type(exc).__name__)
        return AssistantReply(
            f'Queued browser task {receipt.task_id}. I’ll use a temporary visible browser for public research only.',
            'tool',
        )

    def _read_laptop_context(self):
        if self._context_provider is None:
            return None
        try:
            read_snapshot = getattr(self._context_provider, "read_snapshot", None)
            snapshot = read_snapshot() if callable(read_snapshot) else self._context_provider()
            return build_context_packet(snapshot)
        except Exception as exc:
            LOG.warning("Laptop context read failed (%s)", type(exc).__name__)
            return None

    def clear_session(self, session_key: SessionKey) -> None:
        """Forget the temporary turns for one owner/channel/session tuple."""
        self._session_store.clear(session_key)

    def expire_sessions(self) -> int:
        """Purge expired temporary contexts, including sessions not queried again."""
        return self._session_store.expire()

    def _memory_enabled(self) -> bool:
        if self._memory_store is None:
            return True
        try:
            return self._memory_store.enabled()
        except Exception as exc:
            LOG.warning("Memory unavailable (%s); using stateless chat", type(exc).__name__)
            return False

    def _memory_command(self, text: str, enabled: bool, max_words: int, *,
                        session_key: SessionKey | None = None) -> AssistantReply | None:
        def fixed(message: str, compact: str) -> AssistantReply:
            return AssistantReply(message if len(message.split()) <= max_words else compact, "offline")

        normalized = text.lower().rstrip(".!? ")
        if normalized == "do not remember this conversation":
            self._turns.clear()
            self._suppress_session = True
            if self._memory_store is not None:
                try:
                    self._memory_store.discard_proposals()
                except Exception as exc:
                    LOG.warning("Could not clear memory proposals (%s)", type(exc).__name__)
                    return fixed("I stopped keeping this chat in memory, but could not clear pending proposals. Check the dashboard.", "Proposals-uncleared")
            return fixed("I won't keep new turns or memory proposals from this conversation.", "Memory-stopped")

        match = re.fullmatch(r"remember that\s+(.+)", text, re.I | re.S)
        if match:
            if session_key is None and self._suppress_session:
                return fixed("I cannot save a memory from this conversation now.", "Not-saved")
            if not enabled or self._memory_store is None:
                return fixed("Memory is unavailable or disabled, so I did not save that.", "Not-saved")
            try:
                self._memory_store.propose(match.group(1))
            except ValueError:
                return fixed("I cannot save that detail as memory. No memory was created.", "Not-saved")
            except Exception as exc:
                LOG.warning("Memory proposal failed (%s)", type(exc).__name__)
                return fixed("Memory is unavailable, so I did not save that.", "Not-saved")
            return fixed("I prepared that memory. Please approve it in the dashboard before I use it.", "Pending-approval")

        if normalized == "what do you remember about me":
            if not enabled or self._memory_store is None:
                return fixed("Memory is unavailable or disabled right now.", "Memory-unavailable")
            try:
                memories = self._memory_store.active()[:5]
            except Exception as exc:
                LOG.warning("Memory list failed (%s)", type(exc).__name__)
                return fixed("I cannot check memories right now.", "Memory-unavailable")
            if not memories:
                return fixed("I have no approved memories about you yet.", "No-approved-memories")
            answer = "Approved memories: " + "; ".join(row["text"] for row in memories)
            words = answer.split()
            if len(words) > max_words:
                if max_words == 1:
                    answer = "Approved-memories:"
                elif max_words == 2:
                    answer = "Approved-memories: …"
                else:
                    answer = " ".join(words[:max_words - 1] + ["…"])
            return AssistantReply(answer, "offline")

        match = re.fullmatch(r"forget\s+(.+)", text, re.I | re.S)
        if match:
            if not enabled or self._memory_store is None:
                return fixed("Memory is unavailable or disabled right now.", "Memory-unavailable")
            try:
                matches = self._memory_store.active(match.group(1).strip().rstrip(".!?"), limit=None)
                if len(matches) == 1 and self._memory_store.delete(matches[0]["id"]):
                    return fixed("I forgot that approved memory.", "Forgotten")
            except Exception as exc:
                LOG.warning("Memory forget failed (%s)", type(exc).__name__)
                return fixed("I could not forget that memory right now.", "Not-forgotten")
            if len(matches) > 1:
                return fixed("Several memories match. Please choose one to delete in the dashboard.", "Choose-in-dashboard")
            return fixed("I could not find an approved memory matching that phrase.", "Not-found")

        if normalized.startswith("correct that memory"):
            return fixed("Please edit the exact approved memory in the dashboard; I won't overwrite it silently.", "Edit-in-dashboard")
        return None

    def _with_memory_context(self, text, context, enabled, *, session_key=None):
        if not enabled:
            return context, False
        memories: list[dict] = []
        if self._memory_store is not None:
            try:
                memories = self._memory_store.search(text, limit=3)
            except Exception as exc:
                LOG.warning("Memory retrieval failed (%s); using stateless chat", type(exc).__name__)
                if session_key is None:
                    self._turns.clear()
                else:
                    self._session_store.clear(session_key)
                return context, False
        if session_key is None:
            turns = list(self._turns) if not self._suppress_session else []
        else:
            turns, _context_expired = self._session_store.get(session_key)
            turns = list(turns)
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

    def _record_turn(self, text, answer, enabled, *, session_key=None):
        if enabled and (session_key is not None or not self._suppress_session):
            if session_key is not None:
                self._session_store.record(session_key, text, answer)
                return
            self._turns.append((text, answer))
            while self._turns:
                payload = {"recent_turns": [
                    {"user": user, "assistant": reply} for user, reply in self._turns
                ]}
                if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= CONTEXT_TEXT_BUDGET:
                    break
                self._turns.popleft()

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
