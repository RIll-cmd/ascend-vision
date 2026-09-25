# Memory Foundation V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Vision bounded in-process conversation context and durable, explicitly approved local memories without keeping durable chat transcripts.

**Architecture:** A new SQLite `MemoryStore` owns short-lived proposals and active memories; `AssistantService` owns the 12-turn RAM window and selects up to three relevant active memories. Dashboard routes and UI manage approval and deletion. Existing SQLite chat IPC remains transient and is acknowledged/expired.

**Tech Stack:** Python 3.12, standard-library `sqlite3` with FTS5, Flask, plain dashboard JavaScript/CSS, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-memory-foundation-v1-design.md`

## Global constraints

- Work in the isolated `codex/assistant-memory-v1` branch; preserve the user's dirty primary checkout.
- The ordinary conversation window is RAM-only, at most 12 user/assistant turns and a bounded UTF-8 byte payload.
- Only active user-approved memory text survives Vision restart. Proposals expire after 24 hours and are deleted at Vision startup.
- Never auto-infer a memory, persist an ordinary transcript, or put secret text in logs.
- A memory failure must leave ordinary chat and camera monitoring usable and must never report a false save.
- Do not add Core writes, phone chat, skills, MCP, RAG, vector databases, or new network listeners.

## File map

| Path | Responsibility |
|---|---|
| `assistant/memory.py` | SQLite store, FTS5 retrieval, validation/policy, bounded proposal lifecycle. |
| `assistant/service.py` | RAM recent-turn window, deterministic memory commands, selected context. |
| `feedback.py` | Add optional recent turns and approved facts to the allowlisted `ConversationContext.payload()`. |
| `main.py` | Initialize memory store and pass it into the shared service without blocking camera startup on failure. |
| `dashboard.py` | Local-only memory routes and strict input validation. |
| `templates/dashboard.html`, `static/dashboard.js`, `static/dashboard.css` | Small Memories panel with proposal and active-memory controls. |
| `integrations/chat_ipc.py` | Acknowledgement and age-based cleanup of transient text. |
| `tests/test_assistant_memory.py`, `tests/test_assistant_service.py`, `tests/test_dashboard.py`, `tests/test_chat_ipc.py`, `tests/test_main.py` | Contract, privacy, API, transport, and runtime proof. |

---

### Task 1: Store only approved memories durably

**Files:** Create `assistant/memory.py`, `tests/test_assistant_memory.py`.

**Interfaces:** `MemoryStore(path)` produces `propose(text) -> int`, `pending() -> list[dict]`, `approve(id) -> dict`, `reject(id) -> bool`, `active(query='') -> list[dict]`, `search(query, limit=3) -> list[dict]`, `edit(id, text) -> dict`, `delete(id) -> bool`, `enabled() -> bool`, `set_enabled(bool)`, and `discard_proposals()`. `MemoryPolicy.validate(text) -> str` is called before any text write.

- [ ] **Step 1: Write failing store tests.** Exercise a proposal that is not returned by `search`, approval that survives a new `MemoryStore` instance, matching vs unrelated FTS5 queries, reject/delete/edit, expiry/startup proposal cleanup, enable switch, and rejected sensitive candidates. A representative test:

```python
def test_only_approved_memory_is_retrieved_across_restart(tmp_path):
    path = tmp_path / 'assistant_memory.db'
    store = MemoryStore(path)
    proposal_id = store.propose('I prefer green tea')
    assert store.search('green tea') == []
    store.approve(proposal_id)
    assert MemoryStore(path).search('green tea')[0]['text'] == 'I prefer green tea'
```

- [ ] **Step 2: Verify red.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_assistant_memory.py -q` from `ascend-vision/`; expect import failure for `assistant.memory`.
- [ ] **Step 3: Implement minimal storage and policy.** Use separate short-lived SQLite connections, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`, `secure_delete=ON`. Define `memories`, `proposals`, `settings`, and `memory_fts` tables; index only active rows. Guard every mutation in one transaction. Reject empty or >500-character candidates and secret/payment/account/address/health patterns before writes. Use fixed local owner and parameterized SQL. Search with safely quoted tokens, `MATCH`, `bm25`, and active-status filtering:

```python
terms = [word for word in re.findall(r"[\w]+", query.lower()) if len(word) > 2][:8]
match = ' OR '.join('"' + word.replace('"', '""') + '"' for word in terms)
rows = db.execute('SELECT m.id,m.text,m.category FROM memory_fts f '
                  'JOIN memories m ON m.id=f.memory_id '
                  'WHERE memory_fts MATCH ? AND m.status=? '
                  'ORDER BY bm25(memory_fts) LIMIT ?', (match, 'active', limit))
```

- [ ] **Step 4: Verify green and commit.** Run the focused test, then `git add ascend-vision/assistant/memory.py ascend-vision/tests/test_assistant_memory.py` and `git commit -m "feat(vision): add approved local memory store"`.

### Task 2: Keep recent chat in RAM and ground answers in approved facts

**Files:** Modify `assistant/service.py`, `feedback.py`, `tests/test_assistant_service.py`; add `tests/test_assistant_session_memory.py` if separation keeps tests focused.

**Interfaces:** `AssistantService(..., memory_store=None)` remains callable through `respond(text, context, max_words=25)`. `ConversationContext` gains optional `recent_turns` and `approved_memories` tuples; `payload()` includes only bounded, allowlisted text when present.

- [ ] **Step 1: Write failing assistant tests.** With a fake generator that records `context.payload()`, assert a second turn sees the first user/assistant pair; a newly constructed service does not. An approved relevant fact is present, an irrelevant one is absent, and a proposal is absent. Turn 13 evicts turn 1. Disabling memory clears the window and sends no history/facts. Force a store exception and verify a normal reply still returns. Test `remember that`, `what do you remember`, `forget`, `correct that memory`, and `do not remember this conversation` without calling the model for deterministic commands.

```python
def test_recent_turns_are_process_local(tmp_path):
    generator = RecordingGenerator()
    first = AssistantService(FeedbackConfig(), generator=generator)
    first.respond('hello')
    first.respond('and now?')
    assert 'hello' in str(generator.contexts[-1].payload()['recent_turns'])
    fresh = AssistantService(FeedbackConfig(), generator=generator)
    fresh.respond('and now?')
    assert 'recent_turns' not in generator.contexts[-1].payload()
```

- [ ] **Step 2: Verify red.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_assistant_service.py tests/test_assistant_session_memory.py -q`; expect constructor/context/command assertions to fail.
- [ ] **Step 3: Implement the bounded flow.** Hold the existing service lock across context assembly, generation, and RAM-window update. Use a `deque(maxlen=12)` of paired turns and trim its serialized UTF-8 text to a fixed 3000-byte context allowance. Before generation, read at most three `active` FTS5 matches; wrap as IDs and quoted data, never system instructions. Handle explicit memory commands before model generation; catch store errors for ordinary chat but return safe failure for memory mutations. Clear and suppress the window on the session privacy command. Amend both LLM chat prompts to label `recent_turns` and `approved_memories` as untrusted data, not instructions.
- [ ] **Step 4: Verify green and commit.** Run focused assistant and voice tests, then `git add ascend-vision/assistant/service.py ascend-vision/feedback.py ascend-vision/tests/test_assistant_service.py ascend-vision/tests/test_assistant_session_memory.py` and `git commit -m "feat(vision): recall approved facts in bounded chat context"`.

### Task 3: Expose local dashboard memory controls

**Files:** Modify `dashboard.py`, `templates/dashboard.html`, `static/dashboard.js`, `static/dashboard.css`, `tests/test_dashboard.py`.

**Interfaces:** `create_app(config, chat_queue=None, memory_store=None)` uses the configured `assistant_memory.db`. Routes: `GET /api/memory`, `POST /api/memory/proposals/<int:id>/approve`, `POST /api/memory/proposals/<int:id>/reject`, `PATCH /api/memory/<int:id>`, `DELETE /api/memory/<int:id>`, `PUT /api/memory/settings`, `GET /api/memory/export`.

- [ ] **Step 1: Write failing route/UI tests.** Assert proposal text is visible only in the local memory endpoint; approve then export returns active text, reject/delete remove it, edit validates text, and disable does not delete. Verify `Host`, `Origin`, and `Sec-Fetch-Site` restrictions on mutation routes; malformed JSON and overlong text return 400. Verify the page has labeled controls and JS uses `textContent`, not `innerHTML` for memory text.

```python
def test_dashboard_approval_makes_memory_active(tmp_path):
    store = MemoryStore(tmp_path / 'assistant_memory.db')
    proposal_id = store.propose('I prefer green tea')
    client = create_app(Config(storage=StorageConfig(database=tmp_path/'watch.db')),
                        memory_store=store).test_client()
    assert client.post(f'/api/memory/proposals/{proposal_id}/approve').status_code == 200
    assert client.get('/api/memory').json['active'][0]['text'] == 'I prefer green tea'
```

- [ ] **Step 2: Verify red.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_dashboard.py -q`; expect absent memory routes/controls.
- [ ] **Step 3: Implement routes and panel.** Share `MemoryStore` validation and transactions with Vision; do not duplicate policy in JavaScript. Use strict input object shapes and safe generic 503s. Render pending/active lists with DOM `textContent` and explicit approve/reject/edit/delete buttons. Add an accessible enable switch, search input, and JSON export link. Keep the existing local-only and CSP protections.
- [ ] **Step 4: Verify green and commit.** Run dashboard tests, then `git add ascend-vision/dashboard.py ascend-vision/templates/dashboard.html ascend-vision/static/dashboard.js ascend-vision/static/dashboard.css ascend-vision/tests/test_dashboard.py` and `git commit -m "feat(vision): add dashboard memory approvals and controls"`.

### Task 4: Make the SQLite chat queue transient

**Files:** Modify `integrations/chat_ipc.py`, `dashboard.py`, `static/dashboard.js`, `tests/test_chat_ipc.py`, `tests/test_dashboard.py`.

**Interfaces:** `ChatIpcQueue.acknowledge_through(cursor: int) -> None` deletes rendered outbox records and completed inbox text. `POST /api/chat/ack` accepts exactly `{'cursor': nonnegative int}`. Stale completed and pending rows older than 24 hours are removed during queue operations; newly queued offline messages remain available until that expiry.

- [ ] **Step 1: Write failing queue/API tests.** Enqueue and reply; assert `replies_after()` still returns the message before acknowledgement, then `acknowledge_through(cursor)` removes both inbox and outbox text. A malicious/negative/repeated ack cursor returns 400. An old row expires while a fresh offline message remains queued. Check that the browser script sends acknowledgement only after rendering the returned messages.

```python
def test_acknowledgement_removes_delivered_text(tmp_path):
    queue = ChatIpcQueue(tmp_path / 'chat.db')
    message_id = queue.enqueue('temporary transcript')
    cursor = queue.reply(message_id, 'temporary answer', 'reply')
    queue.acknowledge_through(cursor)
    assert queue.replies_after() == []
    assert queue.receive_inbound() is None
```

- [ ] **Step 2: Verify red.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_chat_ipc.py tests/test_dashboard.py -q`; expect `acknowledge_through` or route assertions to fail.
- [ ] **Step 3: Implement acknowledgement and expiry.** Validate cursor as an integer excluding bool. In one transaction, delete outbox rows through the cursor and completed inbox rows whose terminal reply has been acknowledged; run the 24-hour time-based cleanup using UTC timestamps. Add a strict local route, and POST the new cursor in dashboard JS only after `appendChatMessage` has rendered all messages. Preserve the queue's monotonic sequence cursor and existing retention limit.
- [ ] **Step 4: Verify green and commit.** Run IPC/dashboard tests, then `git add ascend-vision/integrations/chat_ipc.py ascend-vision/dashboard.py ascend-vision/static/dashboard.js ascend-vision/tests/test_chat_ipc.py ascend-vision/tests/test_dashboard.py` and `git commit -m "fix(vision): purge delivered and stale dashboard chat text"`.

### Task 5: Wire Vision startup, verify, and document the feature

**Files:** Modify `main.py`, `tests/test_main.py`, `README.md`; review spec and plan.

**Interfaces:** Vision startup constructs `MemoryStore(Path(config.storage.database).parent / 'assistant_memory.db')`, calls `discard_proposals()`, and passes it into `AssistantService`. If initialization fails, it logs only the exception class and constructs the existing stateless service.

- [ ] **Step 1: Write failing startup tests.** Synthetic runtime with a temporary database and a real store: a proposal from a previous instance is gone after startup but an approved fact remains. Patch `MemoryStore` to raise `sqlite3.OperationalError` and assert camera monitoring still processes frames and dashboard chat answers through the stateless assistant. Include a provider-failure fallback test with active memory.
- [ ] **Step 2: Verify red.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_main.py -q`; expect startup-memory assertions to fail.
- [ ] **Step 3: Wire and document.** Initialize memory before binding feedback, keeping the optional injected `assistant_service` for tests. Document that approval happens in the local Memories panel, how to disable/delete/export memory, that ordinary transcript history is RAM-only, and that temporary SQLite delivery is logically purged but not forensic erasure.
- [ ] **Step 4: Verify focused and full suites.** Run `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest tests/test_assistant_memory.py tests/test_assistant_service.py tests/test_assistant_session_memory.py tests/test_dashboard.py tests/test_chat_ipc.py tests/test_main.py tests/test_voice_commands.py -q`, then `D:\ascend-vision\ascend-vision\.venv\Scripts\python.exe -m pytest -q`; expect zero failures.
- [ ] **Step 5: Final review and commit.** Run `git diff --check` and check the spec's eight acceptance conditions. Stage `main.py`, its tests, README, and any directly related doc corrections; commit with `feat(vision): enable privacy-first assistant memory`.
