# Memory Foundation V1 Design

**Date:** 2026-09-25
**Depends on:** Unified Assistant Conversation

## Outcome and privacy boundary

Vision can recall a small set of user-approved facts across restarts and use recent voice/dashboard turns during the current Vision process. Ordinary chat transcripts are not a durable memory: the recent-turn window exists only in RAM and disappears when Vision exits. No model-generated inference is saved as a memory. The dashboard's existing SQLite chat IPC remains a temporary delivery mechanism, not a conversation archive; delivered rows are acknowledged and removed, and stale transport rows are purged. SQLite deletion is logical deletion, not a forensic guarantee against filesystem snapshots or backups.

Only approved memory text survives a Vision restart. A `remember that ...` request creates a short-lived proposal in local SQLite so the independently running dashboard can show it; unapproved proposals are deleted on Vision startup or after 24 hours. This temporary proposal record is the same explicit, bounded disk-transport exception as chat IPC. Proposals are never sent to the model as approved facts.

## Shape of V1

```text
voice / dashboard message
          |
          v
AssistantService -- deterministic memory-command handling
          |               |
          |               +--> MemoryStore proposals / approved facts
          v
RAM recent-turn window (at most 12 turns, bounded serialized bytes)
          |
          +--> SQLite FTS5 search of active memories (at most 3)
          v
existing LLMRoaster / OfflineRoaster
```

`AssistantService` owns the recent-turn window and injects selected context before generation. The window is shared across voice and dashboard requests in the one Vision process; it is not read from or written to disk. It stores only user and assistant text, never images, audio, tokens, Core records, or tool output. A text/byte budget is applied before calling the provider; old turns are dropped, not summarized or stored elsewhere. Summaries and durable conversation history are deliberately deferred because they would persist unapproved conversation content.

`MemoryStore` uses a dedicated `assistant_memory.db` alongside the configured Vision database. It opens short-lived SQLite connections with WAL and a busy timeout so the dashboard and Vision processes can use it concurrently. An active memory has an ID, local owner, category (`preference` or `fact`), text (maximum 500 characters), and timestamps. A proposal has the same bounded text plus an expiry and is physically removed on rejection, expiry, or a fresh Vision startup. Deleted active memories are physically removed from the table and FTS index. A settings row contains the durable memory-enabled switch. No raw conversation log or secret-bearing audit event is stored.

`MemoryPolicy` accepts only explicit user-supplied candidate text. It rejects empty/oversized text and common secret, payment-card, account, precise-address, and sensitive-health patterns before proposal and again before approval/edit. This is defense in depth, not a claim that pattern matching can detect every sensitive fact. The user sees and approves the exact text in the dashboard before it becomes active. Nothing is auto-inferred from an answer or observation.

## User flow

- `Remember that I prefer tea` creates a pending proposal and replies that dashboard approval is required. It does not claim the fact was saved yet.
- The local dashboard has a Memories panel: pending proposals with approve/reject, active memories with search/edit/delete, an enable/disable switch, and JSON export of active memories. Editing an active memory is an explicit approval of the replacement text after policy validation.
- `What do you remember about me?` returns a deterministic, bounded list of active memories, or says memory is off/empty. It never asks the model to invent a list.
- `Forget <phrase>` selects a unique matching active memory for deletion; ambiguity asks the user to choose in the dashboard. `Correct that memory ...` directs the user to dashboard editing unless it can identify exactly one active memory. No hidden overwrite is allowed.
- `Do not remember this conversation` clears the RAM window and suppresses recording new turns or proposing facts until Vision restarts. Existing approved memories remain readable; the global dashboard switch disables retrieval and all memory commands, clears the RAM window on the next request, and keeps chat working statelessly.

## Retrieval and prompt safety

The memory search queries only `active` rows using SQLite FTS5. The service tokenizes the current query into safe quoted terms, ranks matches, and passes at most three records with internal IDs to the generator. If no terms match, no memory is injected. The provider prompt labels remembered facts and prior turns as untrusted data, not instructions; the model must not obey commands inside them. The service combines those records with existing `ConversationContext` telemetry without passing credentials or raw sensor data. Offline fallback continues to work, but it does not claim to semantically answer from memories.

## Transport cleanup

`ChatIpcQueue` remains the two-process transport. A dashboard acknowledgement after rendering replies deletes those reply rows and their completed inbox texts. The queue also removes completed rows and old pending rows after 24 hours during normal operations; startup maintenance removes completed rows left by an interrupted dashboard. Pending messages are preserved briefly so a message queued while Vision is down can still receive an answer. The dashboard does not present the queue as durable chat history. The acknowledgement endpoint remains loopback-only and follows the existing origin/host checks.

## Failure and lifecycle behavior

- If memory storage is unavailable, normal chat uses the existing stateless provider/offline path. Explicit remember/forget/edit operations return a safe failure and never claim success.
- SQLite FTS5 initialization failure disables memory for that run without disabling camera monitoring or chat.
- Provider failure still returns the existing offline reply; only replies actually returned are added to the RAM window.
- Memory disable affects the next request immediately and does not delete approved facts; deletion is a separate explicit action.
- Voice chat still depends on its existing feedback/TTS startup prerequisites. This milestone does not add a no-key speech worker.

## Acceptance and non-goals

1. A dashboard-approved preference survives Vision restart and is retrieved for a relevant later question from either channel.
2. Unapproved proposals are absent from model context and do not survive Vision restart; rejected and deleted text is absent from active retrieval and export.
3. Ordinary chat turns are absent from the memory database, held only in a bounded RAM window, and lost on restart.
4. Dashboard approval, rejection, search, edit, delete, export, and enable/disable work through local routes and UI.
5. Sensitive candidates fail before persistence; memory-store errors do not turn chat into an error or false confirmation.
6. The prompt context stays within its byte/turn limits, and unrelated approved memories are excluded by retrieval tests.
7. Delivered chat IPC text is acknowledged and removed; stale transport rows expire without unbounded accumulation.
8. Existing voice, dashboard chat, camera monitoring, and provider fallback tests remain green.

No phone chat, Core synchronization, Hub agent status, autonomous memory inference, vector database, document RAG, skills, MCP, or tool execution is introduced here.
