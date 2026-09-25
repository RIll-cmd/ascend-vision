# Unified Assistant Conversation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make voice and local dashboard conversation use one assistant service, and return Vision's actual answer to dashboard chat.

**Architecture:** `AssistantService` owns synchronized, synchronous chat generation and returns a typed reply. `FeedbackService` calls it from the existing speech worker, while `main.py` starts the existing SQLite dashboard bridge with a handler that returns the same service's text. A small context builder reads session event counts with its own SQLite connection.

**Tech Stack:** Python 3.11+, SQLite, Flask dashboard IPC, pytest; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-25-unified-assistant-conversation-design.md`

## Global Constraints

- Preserve the existing dashboard's loopback-only HTTP restriction and IPC schema.
- Do not store secrets, frames, audio, or provider payloads in chat IPC.
- Do not change Ascend Core mutation or confirmation behavior.
- Preserve voice mute, cooldown, cancellation, and TTS behavior.
- Keep the existing dirty primary checkout untouched; this plan runs in `codex/assistant-conversation-v1`.
- Do not add memory, Hub status, MCP, skills, or phone transport in this milestone.

---

## File map

| File | Change |
|---|---|
| `assistant/__init__.py` | Export the public assistant service and reply type. |
| `assistant/service.py` | Validate chat input, select existing online/offline generator, serialize calls, return `AssistantReply`. |
| `assistant/context.py` | Build `ConversationContext` using a separate SQLite count reader. |
| `feedback.py` | Bind the assistant service and use it in `_ChatJob` speech handling. |
| `integrations/chat_runtime.py` | Convert a missing handler result into a safe error, retry transient queue reads, and discard late answers after shutdown. |
| `main.py` | Instantiate and bind the service, share context creation, start and stop the dashboard bridge. |
| `tests/test_assistant_service.py` | Unit tests for real service behavior with controlled generator input. |
| `tests/test_assistant_context.py` | Context count and database failure tests. |
| `tests/test_voice_commands.py` | Bound assistant to spoken reply test. |
| `tests/test_chat_runtime.py` | Missing-answer status, queue-read retry, and shutdown race tests. |
| `tests/test_main.py` | Synthetic queue-to-reply runtime test. |

### Task 1: Synchronous assistant reply service

**Files:**
- Create: `assistant/__init__.py`
- Create: `assistant/service.py`
- Create: `tests/test_assistant_service.py`

**Interfaces:**
- Consumes: `feedback.LLMRoaster`, `feedback.OfflineRoaster`, `feedback.get_fallback_chat_reply`, and `llm_router.get_router`.
- Produces: `AssistantReply(text: str, source: Literal['model', 'offline'])` and `AssistantService(feedback_config, llm_config=None, *, generator=None).respond(user_text, context=None, *, max_words=25) -> AssistantReply`.

- [ ] **Step 1: Write failing service tests.** Use a fake generator whose `generate_chat(text, context, max_words)` returns the literal `"The task is moving."`; assert `respond("  Status?  ").text` equals that answer and `source == 'model'`. Add a generator that raises `RuntimeError("secret")` and assert the reply is nonempty, `source == 'offline'`, and contains no `secret`. Add empty-input and two-thread serialization tests.
- [ ] **Step 2: Run the focused test.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_assistant_service.py -q` from `ascend-vision/`; expect import failure because `assistant.service` does not exist.
- [ ] **Step 3: Implement the service.** Use this public shape and generation logic:

```python
@dataclass(frozen=True)
class AssistantReply:
    text: str
    source: Literal['model', 'offline']

class AssistantService:
    def __init__(self, feedback_config, llm_config=None, *, generator=None):
        self._lock = threading.RLock()
        self._generator = generator
        self._generator_source = 'model'
        self._feedback_config = feedback_config
        self._llm_config = llm_config

    def respond(self, user_text, context=None, *, max_words=25) -> AssistantReply:
        if not isinstance(user_text, str) or not user_text.strip():
            raise ValueError('user_text must be nonempty')
        if len(user_text) > 4_000:
            raise ValueError('user_text must be at most 4000 characters')
        if type(max_words) is not int or max_words <= 0:
            raise ValueError('max_words must be a positive integer')
        text = user_text.strip()
        with self._lock:
            try:
                answer = self._get_generator().generate_chat(text, context, max_words=max_words)
                if isinstance(answer, str) and answer.strip():
                    return AssistantReply(answer.strip(), self._generator_source)
            except Exception as exc:
                LOG.warning('Assistant generation failed (%s); using offline reply', type(exc).__name__)
            fallback = get_fallback_chat_reply(text, context)
            if not isinstance(fallback, str) or not fallback.strip():
                raise RuntimeError('Assistant could not produce a reply')
            return AssistantReply(fallback.strip(), 'offline')

    def _get_generator(self):
        if self._generator is None:
            keys = (self._feedback_config.api_key_env, 'GROQ_API_KEY', 'CEREBRAS_API_KEY')
            if any(os.environ.get(key, '').strip() for key in keys):
                router = get_router(self._llm_config) if self._llm_config is not None else get_router()
                self._generator = LLMRoaster(self._feedback_config, router=router)
            else:
                self._generator = OfflineRoaster(self._feedback_config)
                self._generator_source = 'offline'
        return self._generator
```

Import `dataclass`, `logging`, `os`, `threading`, and `Literal`, plus the named feedback classes/functions and `get_router`. Set `LOG = logging.getLogger(__name__)`. Export both public types from `assistant/__init__.py`.
- [ ] **Step 4: Run the focused tests to green.** Run the same pytest command; expect all service tests to pass.
- [ ] **Step 5: Commit the self-contained service.** Run `git add ascend-vision/assistant ascend-vision/tests/test_assistant_service.py` and `git commit -m "feat(vision): add shared assistant reply service"` from the worktree root.

### Task 2: Thread-safe session context

**Files:**
- Create: `assistant/context.py`
- Create: `tests/test_assistant_context.py`

**Interfaces:**
- Consumes: `feedback.ConversationContext` and the existing `phone_events(session_id, event_type)` data.
- Produces: `build_conversation_context(user_text: str, *, session_id: int | None, mode: str, database_path: str | Path, started_at: float | None) -> ConversationContext`.

- [ ] **Step 1: Write failing context tests.** Create a temporary SQLite `phone_events` table with `session_id` and `event_type`; insert two `phone_held` rows and one `slouch` row for session 7, plus one unrelated session. Assert the returned context reports `phone_pickups == 2`, `slouch_events == 1`, the chosen mode, and the original query. Add a missing-database case that returns zero counts without creating a new database file.
- [ ] **Step 2: Run the focused test.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_assistant_context.py -q`; expect import failure for `assistant.context`.
- [ ] **Step 3: Implement the builder.** Open a short-lived SQLite connection using read-only URI mode against an existing file; group counts by `event_type` for the requested session ID. Catch `sqlite3.Error` and `OSError`, log only the exception type, and use zero counts. Compute nonnegative elapsed minutes using `time.perf_counter() - started_at`:

```python
counts = {}
if session_id is not None:
    path = Path(database_path).resolve()
    try:
        with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=0.5)) as connection:
            rows = connection.execute(
                'SELECT event_type, COUNT(*) FROM phone_events WHERE session_id=? GROUP BY event_type',
                (session_id,),
            ).fetchall()
            counts = {event_type: count for event_type, count in rows}
    except (OSError, sqlite3.Error) as exc:
        LOG.debug('Chat session counts unavailable (%s)', type(exc).__name__)
minutes = max(0.0, time.perf_counter() - started_at) / 60.0 if started_at is not None else 0.0
return ConversationContext(user_query=user_text, mode=mode,
    phone_pickups=counts.get('phone_held', 0),
    microsleep_events=counts.get('drowsiness_microsleep', 0),
    yawns=counts.get('yawn', 0), slouch_events=counts.get('slouch', 0),
    session_duration_minutes=minutes)
```
- [ ] **Step 4: Run context and service tests.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_assistant_context.py tests/test_assistant_service.py -q`; expect all tests to pass.
- [ ] **Step 5: Commit the context builder.** Run `git add ascend-vision/assistant/context.py ascend-vision/tests/test_assistant_context.py` and `git commit -m "feat(vision): build chat context with isolated database reads"`.

### Task 3: Route spoken conversation through the service

**Files:**
- Modify: `feedback.py:440-466,699-733`
- Modify: `tests/test_voice_commands.py`

**Interfaces:**
- Consumes: `AssistantService.respond(...).text` from Task 1.
- Produces: `FeedbackService.bind_assistant(assistant_service) -> None`; existing `submit_chat` and TTS signatures remain unchanged.

- [ ] **Step 1: Write a failing speech test.** Create a real `FeedbackService` with a controlled speaker and generator. Bind a stub assistant whose `respond` returns `AssistantReply("Shared answer.", "model")`; submit a chat utterance and wait for the speaker. Assert the spoken text is exactly `"Shared answer."`. Also assert the existing chat cooldown still rejects an immediate second utterance.
- [ ] **Step 2: Run the new test.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_voice_commands.py -q`; expect failure because `bind_assistant` does not exist.
- [ ] **Step 3: Add binding and delegate chat generation.** Initialize `self._assistant_service = None`, add `bind_assistant`, and inside `_ChatJob` choose the service when bound. Keep the existing legacy generator path for callers that do not bind an assistant:

```python
def bind_assistant(self, assistant_service) -> None:
    if not callable(getattr(assistant_service, 'respond', None)):
        raise TypeError('assistant_service must provide respond')
    with self._lock:
        self._assistant_service = assistant_service

# In _ChatJob, before speech:
if self._assistant_service is not None:
    reply_text = self._assistant_service.respond(
        job.user_text, job.context, max_words=job.max_words,
    ).text
elif hasattr(self._generator, 'generate_chat'):
    reply_text = self._generator.generate_chat(
        job.user_text, job.context, max_words=job.max_words,
    )
```

Only initialize the legacy generator when no assistant is bound. Preserve the existing cancellation and speech checks.
- [ ] **Step 4: Run speech and feedback tests.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_voice_commands.py tests/test_feedback.py -q`; expect all tests to pass.
- [ ] **Step 5: Commit voice integration.** Run `git add ascend-vision/feedback.py ascend-vision/tests/test_voice_commands.py` and `git commit -m "feat(vision): use shared assistant for spoken chat"`.

### Task 4: Return actual dashboard answers and start the runtime bridge

**Files:**
- Modify: `integrations/chat_runtime.py:12-56`
- Modify: `main.py:44-45,139-151,328-347,406-443`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `AssistantService`, `build_conversation_context`, `ChatIpcQueue`, and `ChatRuntimeBridge`.
- Produces: `run(..., assistant_service=None)` for controlled integration testing; dashboard IPC retains its existing `messageId`, `text`, and `status` contract.

- [ ] **Step 1: Write failing bridge behavior tests.** Queue a message, run `ChatRuntimeBridge` with a handler returning `None`, and assert the outbox contains `status == 'error'` and its text is the safe `ERROR_REPLY`. This catches the current false success acknowledgement. Also inject one transient `sqlite3.OperationalError` from `receive_inbound()` and verify the bridge recovers, and block an in-flight handler until after `stop()` to verify its late reply is not published.
- [ ] **Step 2: Write failing runtime integration tests.** POST `"How is focus going?"` through `dashboard.create_app(config, chat_queue=ChatIpcQueue(tmp_path / 'chat_ipc.db')).test_client()`. Use the existing synthetic camera fakes, a temporary storage database, disabled Core integration, and a fake assistant returning `AssistantReply("Your focus is steady.", "model")`. Let the synthetic capture wait briefly for the dashboard GET response, then stop. Assert the polled `messageId`, `text`, and `status` match the submitted message and answer. In a second test, replace `integrations.chat_ipc.ChatIpcQueue` with a callable that raises `OSError('private storage path')`; assert `run()` still processes synthetic camera frames and closes capture.
- [ ] **Step 3: Run both tests to verify red.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_chat_runtime.py tests/test_main.py -q`; expect the missing-answer test to fail on `reply`, and the runtime test to fail because `run` has no `assistant_service` parameter or does not start a bridge.
- [ ] **Step 4: Implement the runtime connection.** Make `ChatRuntimeBridge` reject empty/None handler results as safe errors, retry transient queue reads, and synchronize stop with publishing so late in-flight answers are discarded. In `main.py`, build one service after feedback setup, bind it, and use this handler and startup sequence before the camera loop:

```python
def chat_context(text):
    return build_conversation_context(text, session_id=manager.session_id,
        mode=manager.mode, database_path=config.storage.database, started_at=start)

def handle_dashboard_chat(text):
    return assistant_service.respond(
        text, chat_context(text), max_words=config.voice_commands.max_reply_words,
    ).text

chat_queue = ChatIpcQueue(Path(config.storage.database).parent / 'chat_ipc.db')
chat_bridge = ChatRuntimeBridge(chat_queue, handle_dashboard_chat)
resources.callback(chat_bridge.stop)
chat_bridge.start()
```

Use `chat_context(text)` in the voice `route_chat` as well. Extend `run()` with `assistant_service=None` for the synthetic integration test; construct `AssistantService(config.feedback, config.llm)` when it is `None`. Register bridge cleanup last so it stops before feedback and database cleanup. Wrap queue creation and bridge startup in `except (OSError, sqlite3.Error, RuntimeError) as exc:`; log only `type(exc).__name__` and continue the camera loop.
- [ ] **Step 5: Run focused and full verification.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_chat_runtime.py tests/test_main.py tests/test_voice_commands.py tests/test_dashboard.py -q`, then `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest -q`. Expect zero failures, including the new queue-to-reply test.
- [ ] **Step 6: Commit runtime integration.** Run `git add ascend-vision/integrations/chat_runtime.py ascend-vision/main.py ascend-vision/tests/test_chat_runtime.py ascend-vision/tests/test_main.py` and `git commit -m "feat(vision): deliver real dashboard assistant replies"`.

### Task 5: Documentation and final review

**Files:**
- Modify: `README.md` or `docs/phase5-readme.md` only if needed to document the existing two-process startup commands.
- Review: `docs/superpowers/specs/2026-09-25-unified-assistant-conversation-design.md`
- Review: `docs/superpowers/plans/2026-09-25-unified-assistant-conversation.md`

**Interfaces:**
- Consumes: completed Tasks 1–4.
- Produces: a documented way to run the Vision runtime and local dashboard together.

- [ ] **Step 1: Verify the existing startup documentation.** Confirm it instructs users to run `main.py` and `dashboard.py` as separate processes, and that both use the same configured storage directory. If this is missing, add the exact two-process commands and the statement that dashboard chat waits while Vision is offline.
- [ ] **Step 2: Review this plan against the spec.** Check each acceptance criterion, scan for unfinished placeholders, and verify all public signatures match the implementation.
- [ ] **Step 3: Run final checks.** Run `git diff --check` and the full `pytest -q` suite from `ascend-vision/`; report the exact passed count.
- [ ] **Step 4: Commit documentation if changed.** Stage only the two new spec/plan documents and any directly related startup documentation; commit with `docs(vision): document unified assistant conversation`.

## Final scope check

- The dashboard's HTTP route and UI already exist; their request and response schema remains unchanged.
- This plan makes their queued messages receive actual answers.
- Memory, Hub status, Core completion reporting, phone transport, skills, MCP, and RAG each retain their own later design and plan.
