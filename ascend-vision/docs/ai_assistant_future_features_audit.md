# Ascend Vision AI Assistant: Future Features Audit and Implementation Roadmap

**Date:** 2026-09-23  
**Projects audited:** Ascend Vision and Ascend Core / Ascend Hub  
**Decision status:** Approved architectural direction; implementation must be split into independently reviewed plans  
**Assumption:** Phone chat should work remotely through authenticated Ascend Hub, not by exposing Vision's local dashboard to the internet.

---

## 1. Executive Summary

Ascend Vision already has many of the seams needed for a capable personal assistant: provider-routed LLM calls, voice chat, a dashboard chat UI and queue, SQLite persistence, local tools, authenticated Ascend Core integration, automation proposals, and Vision/Core heartbeats. The product does not yet have a unified assistant service, durable conversational memory, a typed permission-aware tool runtime, a skills system, or a secure remote phone-chat transport.

The recommended direction is:

1. Build a single assistant service used by voice, desktop dashboard, Fairy UI, and future phone chat.
2. Add bounded short-term memory and user-approved long-term memory using SQLite and FTS5.
3. Introduce a typed, permission-aware native tool runtime. Make Ascend Hub status its first read-only tool.
4. Add authenticated phone chat through Ascend Hub using an outbound Vision connection and a durable Core-owned relay.
5. Add declarative skills only after memory and tool boundaries are stable.
6. Add an optional MCP client adapter later. Do not make Vision an MCP server in the near term.
7. Add document RAG, embeddings, and bounded multi-step workflows only when actual use cases justify them.

The system should remain local-first. Ascend Core stays authoritative for habits, missions, automations, agent status, and consequential operations. Vision may remember user-approved conversational facts, but memory must never silently modify Core records.

---

## 2. Audit Method and Status Labels

This audit distinguishes verified repository behavior from proposed work.

| Label | Meaning |
|---|---|
| **Implemented** | Present in the current repositories with code or tests supporting the behavior. |
| **Partial** | A reusable foundation exists, but the requested end-to-end feature is incomplete. |
| **Missing** | No complete implementation was found. |
| **Deferred** | Valuable later, but premature for the current architecture. |
| **Rejected for now** | Risk or complexity exceeds present value. |

Primary evidence inspected:

- `main.py`, `feedback.py`, `llm_router.py`, `db.py`, and `config.py` in Ascend Vision.
- `integrations/chat_ipc.py`, `integrations/chat_runtime.py`, `integrations/ascend_client.py`, and the Vision heartbeat modules.
- Existing browser and Spotify tools under `tools/`.
- Ascend Core status schemas, repository, service, router, credential tests, and Vision integration tests.
- Existing dashboard-chat and Vision/Core contract specs and plans.

The working tree already contained unrelated edits when this audit was written. This document does not evaluate or alter those edits.

---

## 3. Current-State Architecture

### 3.1 What already works

| Capability | State | Audit conclusion |
|---|---|---|
| Provider-routed text generation | **Implemented** | `llm_router.py` supports provider fallback and provider-enforced structured JSON. Automatic provider function calling is intentionally disabled. |
| Voice conversation entry point | **Implemented** | Voice commands route through `main.py` and `FeedbackService`. |
| Local dashboard text chat | **Partial** | The dashboard UI and WAL-mode SQLite inbox/outbox exist, but the Vision runtime does not start `ChatRuntimeBridge`, so queued messages do not receive generated answers. |
| Conversation persistence | **Missing** | Dashboard transport messages exist, but they are not a complete user-owned conversation and memory model. |
| SQLite application storage | **Implemented** | Sessions, observations, events, feedback, and heartbeat-related state are already stored locally. |
| Ascend Core authentication | **Implemented / evolving** | Vision has authenticated Core integration, character context, heartbeat, and proposal/confirmation flows. |
| Consequential-action confirmation | **Partial but strong** | Core already supplies preview/confirm/execute patterns for sensitive operations. This must remain authoritative. |
| Browser and Spotify actions | **Implemented but hard-coded** | These are direct Python integrations, not registered typed tools with common permission and audit policy. |
| Native skills | **Missing** | There is no versioned declarative skill loader or activation policy. |
| MCP | **Missing** | No MCP client gateway or Vision MCP server is present. |
| Hub service-status shelf | **Implemented in Core** | Core derives `idle`, `working`, and heartbeat-based `offline` status and exposes a dedicated authenticated read endpoint. |
| Phone chat | **Missing** | The local dashboard is responsive, but there is no secure remote phone transport, pairing, or user-owned relay. |

### 3.2 Current routing problem

Conversation logic is distributed across `main.py`, `FeedbackService`, dashboard IPC, gesture routing, automation routing, and direct tool branches. Adding memory or phone chat directly to these branches would duplicate behavior and produce channel-specific assistants.

The required architectural seam is a central `AssistantService`:

```text
Voice ───────────┐
Dashboard ───────┼──> AssistantService ──> context + memory + tools ──> LLM router
Fairy UI ────────┤             │
Phone via Hub ───┘             └──> Core preview/confirm/execute for consequential work
```

Every chat channel must use the same assistant identity, context limits, memory policy, tool policy, and audit trail.

---

## 4. Feature Audit and Decisions

| Feature | Current state | Decision | Required gate |
|---|---|---|---|
| Unified assistant service | Missing | **Build first** | Voice and dashboard produce equivalent routing behavior. |
| Short-term conversational memory | Missing | **Build** | Token-bounded context; restart-safe sessions; graceful stateless fallback. |
| Long-term user memory | Missing | **Build with approval controls** | Proposed memories cannot become active without policy-compliant approval. |
| Memory dashboard | Missing | **Required for release** | Users can inspect, approve, edit, export, disable, and delete memory. |
| Hub status answers | Partial | **Build as first native tool** | Replies are fully derived from Core status data, never guessed. |
| Completion reporting | Partial | **Extend Core contract** | “Finished” is only spoken after an explicit completion event. |
| Phone text chat | Missing | **Build through Hub** | Authenticated remote use, device revocation, durable delivery, no inbound Vision port. |
| Phone voice chat | Missing | **Future** | Text transport, cancellation, and interruption semantics must be stable first. |
| Native skill packages | Missing | **Build after memory/tools** | Declarative-only V1; no arbitrary executable skill code. |
| Typed tool runtime | Missing | **Build** | Schema validation, risk classes, confirmations, timeouts, audit records, call limits. |
| MCP client gateway | Missing | **Deferred** | Native tool policy must apply unchanged to MCP-imported tools. |
| Vision MCP server | Missing | **Rejected for now** | Privacy and authorization model is not mature enough to expose Vision state externally. |
| SQLite FTS5 retrieval | Missing for memory | **Build** | Meets measured relevance target on the initial memory evaluation set. |
| Embedding/vector retrieval | Missing | **Deferred** | Add only after FTS5 fails a real document or semantic-recall requirement. |
| Document RAG | Missing | **Deferred** | Requires an explicit document-ingestion product use case and provenance UI. |
| Autonomous ReAct loops | Missing | **Rejected for now** | Replace with bounded workflows and explicit action budgets. |
| Evaluation and tracing | Partial | **Build throughout** | Every milestone ships with deterministic tests, redaction, timing, and outcome metrics. |
| Cross-device memory sync | Missing | **Future, opt-in** | Encryption, tenancy, deletion propagation, and conflict policy are prerequisites. |
| Remote camera viewing | Missing | **Rejected by default** | High privacy exposure; a future implementation would require explicit foreground consent. |

---

## 5. Milestone 1: Unified Assistant and Memory Foundation V1

### 5.1 Goal

Give Ascend Vision one bounded, inspectable conversational memory shared by voice and local dashboard chat. This is the first major implementation milestone.

### 5.2 Components

Create a focused `assistant/` package with clear boundaries:

| Component | Responsibility |
|---|---|
| `AssistantService` | Accept a normalized user message, assemble context, call the LLM router, and return a typed assistant result. |
| `ConversationStore` | Persist conversations, messages, summaries, and lifecycle state. |
| `MemoryStore` | Persist proposed, active, rejected, superseded, and deleted memories. |
| `ContextBuilder` | Apply token budgets and assemble recent turns, summary, approved memories, and safe runtime context. |
| `MemoryPolicy` | Classify memory candidates, reject sensitive content, enforce approval, and resolve corrections. |
| `MemoryRetriever` | Retrieve a small relevant set using structured filters and SQLite FTS5. |
| `MemoryAuditLog` | Record proposals, approvals, retrievals, edits, suppression, and deletion without storing secrets in logs. |

### 5.3 Data model

The initial SQLite model should include:

- `conversations`: owner, channel, mode, lifecycle timestamps, memory-disabled flag.
- `conversation_messages`: role, bounded text, creation time, provider-independent metadata.
- `conversation_summaries`: covered message range, summary version, creation time.
- `memories`: owner, type, normalized text, status, sensitivity, confidence, source, created/updated/last-used timestamps.
- `memory_events`: append-only audit events for proposal, approval, rejection, retrieval, edit, supersession, and deletion.

Memory types should initially be limited to preference, personal fact, standing instruction, relationship, and routine. New categories require explicit schema and policy review.

### 5.4 Context policy

- Keep approximately 10–20 recent turns, but enforce a configured token budget rather than a fixed count alone.
- Summarize older turns using a versioned prompt and retain the source range.
- Retrieve only a small configurable number of active memories.
- Treat retrieved memory as untrusted factual context, never as executable instructions.
- Include memory IDs internally so incorrect answers can be traced to their source.
- If storage or retrieval fails, continue with stateless chat and disclose no internal exception details.

### 5.5 User controls

Supported commands and UI actions must include:

- “Remember that …”
- “What do you remember about me?”
- “Forget …”
- “Correct that memory …”
- “Do not remember this conversation.”
- Search, approve, reject, edit, export, and delete from the dashboard.
- Disable all long-term memory without disabling chat.

Explicit remember commands may activate a safe memory immediately with confirmation. Inferred memories remain proposed until approved.

### 5.6 Data that must not be retained automatically

- Passwords, access tokens, API keys, cookies, or recovery codes.
- Payment-card and financial-account details.
- Raw camera frames, screenshots, or microphone recordings.
- Medical diagnoses or sensitive health inferences.
- Temporary emotional states inferred from facial expression.
- Precise location history.
- Model-generated guesses about the user.
- Tool output marked secret or non-persistable.

### 5.7 Release gates

- Voice and dashboard use the same assistant service.
- A confirmed preference survives a restart and is recalled when relevant.
- Irrelevant memories are excluded from a controlled retrieval suite.
- Deleted memories do not appear in retrieval or model context.
- Proposed memories require approval.
- Sensitive-memory tests reject or redact prohibited values.
- Context remains within the configured token budget.
- Existing provider fallback still works.
- Core mutations still require Core confirmation.
- Disabling memory produces a genuinely stateless conversation.

---

## 6. Milestone 2: Typed Tool Runtime and Ascend Hub Status

### 6.1 Why Hub status is the first tool

It is read-only, immediately useful, and backed by an existing authoritative Core API. It exercises tool registration, schema validation, credentials, timeouts, audit logs, and natural-language rendering without introducing mutation risk.

### 6.2 Native tool contract

Every tool must define:

- Stable name and version.
- Human-readable purpose.
- JSON-schema-compatible validated input and output types.
- Risk level: read-only, reversible, or consequential.
- Required credentials and capabilities.
- Timeout and cancellation behavior.
- Confirmation requirement.
- Persistence/redaction policy for inputs and outputs.
- Per-request call limit.
- Deterministic audit outcome.

Skills may recommend tools, but only the tool runtime can authorize and execute them.

### 6.3 Hub status behavior

Vision should recognize questions such as:

- “What is Ascend Hub doing?”
- “Is Codex CLI still working?”
- “What is Antigravity’s status?”
- “Which AIs are online?”

Vision must fetch the authenticated Core status shelf and normalize each instance into:

- `working`: at least one active operation or an authoritative working event.
- `idle`: healthy heartbeat with no active operation.
- `offline`: heartbeat older than the Core-defined stale threshold.
- `blocked` or `error`: an authoritative safe issue is present.
- `unknown`: incomplete, malformed, or contradictory data.

The LLM must not decide these states. It may only turn already-normalized structured facts into natural language.

Safe examples:

> Antigravity is idle. Codex CLI is still working on “run integration tests.”

> I cannot reach Ascend Hub right now, so I cannot verify the agents’ current status.

### 6.4 Completion accuracy gap

Core currently knows whether an agent has active work, but an idle state does not prove that the agent recently finished. To support “Antigravity finished its work and is idle now,” Core should expose a sanitized `lastCompletedOperation` containing:

- Operation ID.
- Safe display label.
- Completion timestamp.
- Outcome: succeeded, failed, or cancelled.
- Optional sanitized summary.

Rules:

- Only an explicit finish event creates completion history.
- A stale or missing heartbeat becomes offline, not finished.
- Process disappearance becomes interrupted or unknown unless a finish event was recorded.
- Raw prompts, file contents, secrets, stack traces, and private agent output never enter the public shelf.
- Multiple instances are reported separately unless the user asks for an aggregate.

### 6.5 Authentication

Use Core’s dedicated shelf-read credential. Do not reuse producer credentials, place credentials in prompts, return them to the UI, or include them in logs. Credential provisioning, rotation, revocation, and unauthorized-response tests are release requirements.

### 6.6 Release gates

- Working, idle, offline, blocked/error, unknown, and multiple-instance cases are tested.
- Stale data is never described as current.
- “Finished” is never generated without an explicit completion record.
- Unauthorized and malformed Core responses fail closed.
- Tool output includes source time and heartbeat freshness for auditing.
- The tool cannot mutate Core.

---

## 7. Milestone 3: Secure Phone Chat Through Ascend Hub

### 7.1 Decision

Build an installable responsive web app / PWA within Ascend Hub before considering native iOS or Android clients. Phone chat should work remotely through authenticated Hub infrastructure.

Do not expose the local Vision Flask server, dashboard chat endpoints, SQLite files, or a home-network port directly to the internet.

### 7.2 Data flow

```text
Authenticated Phone PWA
        │ HTTPS
        ▼
Ascend Hub chat API and durable user-owned queue
        │
        │ Vision-maintained outbound poll/WebSocket
        ▼
Ascend Vision device
        │
        ▼
AssistantService → memory/tools/Core confirmation
        │
        ▼
Hub reply queue → SSE status/reply stream → Phone PWA
```

Vision maintains the outbound connection so remote use does not require inbound NAT traversal or opening a local service.

### 7.3 MVP scope

- Existing Ascend account authentication.
- Explicit pairing with a particular Vision device.
- Text messages and text replies.
- Shared conversation identity with desktop and voice channels.
- Delivery states: queued, delivered, processing, confirmation required, completed, failed, and expired.
- Online/offline device presence.
- Durable message delivery across phone refresh and temporary Vision disconnection.
- SSE for reply and status updates; ordinary polling as a degraded fallback.
- Cancellation before a consequential action is executed.
- Per-user and per-device rate limits.
- Bounded message size and retention.
- Device/session revocation.

### 7.4 Confirmation UX

Read-only answers may complete immediately. Consequential actions must return a structured confirmation card containing:

- Proposed action.
- Target system.
- Material effects.
- Expiration time.
- Confirm and cancel controls.

Confirmation tokens must be single-use, user-bound, device-aware, short-lived, and invalidated after execution or cancellation. The phone UI must never simulate confirmation using plain conversational text.

### 7.5 Security requirements

- TLS for all remote traffic.
- Existing Hub session authentication plus CSRF protection where applicable.
- Strict tenancy checks on every conversation and message.
- Pairing approval from an already trusted session or the local Vision device.
- No model-provider keys, Vision tokens, shelf credentials, or Core producer credentials on the phone.
- Log redaction and bounded retention.
- Replay protection for message and confirmation IDs.
- Safe behavior when Vision is offline: queue within policy or clearly reject; never claim delivery.

### 7.6 Deferred phone features

- Voice input and spoken replies.
- Push notifications for completion or confirmation.
- Image/file attachments.
- Background audio sessions.
- Native mobile apps.
- Remote camera feed.

Text chat, cancellation, reconnection, and confirmation semantics must be reliable before these are considered.

### 7.7 Release gates

- A paired phone can converse with the same assistant used locally.
- An unpaired or wrong-user phone cannot read or submit messages.
- Temporary Vision disconnection does not duplicate message execution.
- Refreshing the phone does not lose durable replies.
- Confirmation is required and single-use for consequential actions.
- Revoking a device immediately blocks new reads and writes.
- No local Vision port is internet-exposed.
- Accessibility, narrow-screen layout, and slow-network behavior are tested.

---

## 8. Milestone 4: Native Skills

### 8.1 Definition

A skill changes how the assistant handles a class of requests. A tool performs an external action. Skills must not bypass tool permissions.

The first skill format should be declarative and versioned:

```text
skills/<skill-id>/
├── SKILL.md
├── manifest.json
├── prompts/
└── references/
```

The manifest should declare the skill ID, version, description, activation hints, compatible assistant/runtime versions, required tool capabilities, and whether it may propose memories. V1 skills must not contain arbitrary executable Python, shell, or JavaScript.

### 8.2 Recommended first skills

- Focus coach.
- Habit reflection.
- Daily review.
- Posture coaching.
- Automation proposal assistant.
- Screen-use summary.
- Ascend Hub operations narrator.

### 8.3 Skill safety

- User-enabled allowlist.
- Version pinning and integrity hash.
- Prompt/reference size limits.
- Explicit required-tool declarations.
- Conflict resolution when multiple skills activate.
- No skill can change confirmation policy, memory sensitivity rules, or credential access.
- Skill output is treated as untrusted instructions and cannot authorize execution.

---

## 9. Milestone 5: Optional MCP Client Gateway

### 9.1 Decision

Do not implement MCP before the native tool registry and permission model are proven. When added, Vision should initially be an MCP client consuming explicitly configured servers. Vision should not expose its memories, observations, or camera state as a general MCP server.

### 9.2 Required gateway behavior

- Explicit server allowlist and user-visible enable/disable controls.
- Supported transport allowlist; no arbitrary command execution to launch unknown servers.
- Tool-schema import into the native registry.
- Local schema validation even when the server advertises a schema.
- Per-server capabilities and risk classification.
- Native confirmation, timeout, cancellation, call-budget, and audit policies.
- Secret isolation from prompts and tool-visible arguments.
- Response-size limits and content sanitization.
- Clear attribution of which MCP server produced each result.
- Server health and revocation controls.

### 9.3 MCP rejection criteria

Reject or disable a server that:

- Requires unrestricted shell or filesystem access.
- Produces unstable or unbounded schemas.
- Attempts to return or request secrets through model-visible text.
- Cannot respect user/tenant separation.
- Bypasses Ascend Core for consequential operations.
- Cannot provide adequate timeouts, cancellation, or audit attribution.

---

## 10. Milestone 6: Document RAG and Hybrid Retrieval

### 10.1 Decision

Do not add a vector database for conversational memory V1. Use relational fields and SQLite FTS5 first.

Add document RAG only after defining a real ingestion use case such as personal manuals, project notes, or approved Ascend documentation.

### 10.2 Future architecture

If evaluation shows FTS5 is insufficient:

1. Chunk documents with stable source and version identifiers.
2. Generate local embeddings when practical.
3. Combine FTS5 keyword results and dense-vector results.
4. Fuse rankings with reciprocal-rank fusion.
5. Optionally rerank only the small fused candidate set.
6. Return citations and source timestamps with every grounded answer.
7. Delete derived chunks and embeddings when the source is removed.

A managed vector database, Redis semantic cache, or external reranker is not justified for the current single-user local-first product.

---

## 11. Milestone 7: Bounded Agentic Workflows

### 11.1 Decision

Do not implement open-ended ReAct or autonomous plan-and-solve loops. Use explicit workflow state machines with limited steps, typed state, tool budgets, and human checkpoints.

### 11.2 Safe workflow contract

- Declared goal and allowed tools.
- Maximum step and elapsed-time budgets.
- Idempotency key for every external action.
- Cancellation between steps.
- Retry only for classified transient failures.
- No silent privilege escalation.
- Human confirmation before consequential transitions.
- Final structured outcome: completed, failed, cancelled, expired, or needs user input.

Potential later workflows include a daily review, focus-session setup, and automation proposal refinement. Sending messages, writing files, or changing Core data remains confirmation-gated.

---

## 12. Evaluation, Guardrails, and Observability

These are cross-cutting requirements, not a final phase.

### 12.1 Memory evaluations

- Relevant-memory recall.
- Irrelevant-memory exclusion.
- Correction and supersession behavior.
- Deletion completeness.
- Sensitive-data rejection.
- Token-budget compliance.
- Cross-channel consistency.

### 12.2 Tool evaluations

- Correct tool selection.
- Argument-schema validity.
- Unauthorized-call rejection.
- Confirmation enforcement.
- Timeout and retry behavior.
- Hallucinated-status rate; target is zero for authoritative Hub states.

### 12.3 Phone evaluations

- Authentication and tenant isolation.
- Pairing and revocation.
- Queue idempotency and reconnection.
- Slow and intermittent networks.
- Confirmation replay resistance.
- Mobile accessibility and responsive layout.

### 12.4 Operational telemetry

Record locally or in Core, according to ownership:

- Request and conversation IDs.
- Selected provider and latency.
- Token estimates and context composition counts.
- Retrieved memory IDs, not secret memory text in logs.
- Tool name, duration, outcome, and confirmation status.
- Phone delivery state transitions.
- Redacted error categories.

Do not log API keys, authentication headers, raw camera/audio, private tool payloads, or complete prompts by default.

---

## 13. Recommended Delivery Sequence

Each row is a separate spec, implementation plan, and review gate.

| Order | Deliverable | Depends on | User-visible result |
|---:|---|---|---|
| 1 | Unified Assistant Service | Existing chat and LLM router | Voice and dashboard use one conversational path. |
| 2 | Memory Foundation V1 | Unified assistant | Vision remembers approved preferences and conversation context. |
| 3 | Typed Tool Runtime + Hub Status | Unified assistant; Core status shelf | Vision truthfully reports Codex CLI, Antigravity, Core, and Vision status. |
| 4 | Core Completion History | Existing Core operations/status | Vision can truthfully say an AI finished, failed, or was cancelled. |
| 5 | Secure Phone Text Chat | Unified assistant; Core auth; durable relay | The user can chat with Vision from a paired phone anywhere. |
| 6 | Native Skills V1 | Memory and tool policy | Versioned focus, habit, review, and status skills. |
| 7 | Optional MCP Client | Proven native tool security | Approved external tools can be connected without bypassing policy. |
| 8 | Document RAG | Proven knowledge use case | Grounded answers over user-approved document collections. |
| 9 | Bounded Workflows | Mature tools, confirmations, telemetry | Safe multi-step assistance with cancellation and checkpoints. |
| 10 | Cross-device memory and richer phone UX | Stable tenancy and encryption | Optional synced memory, voice, and notifications. |

Milestones 1 and 2 are the first major subsystem. Milestone 3 is the first tool vertical slice. Phone chat intentionally follows the unified assistant so it does not create a second memory or routing implementation.

---

## 14. Features Rejected or Deferred

### Rejected for now

- Unbounded autonomous agent loops.
- Automatic storage of every user utterance.
- Memory derived from raw camera frames or screenshots.
- Arbitrary executable downloadable skills.
- Unrestricted shell, filesystem, email, or browser-control tools.
- Direct internet exposure of Vision’s local dashboard or SQLite IPC.
- Vision MCP server access to private observations or memories.
- Remote camera viewing by default.
- Silent execution of Core mutations.

### Deferred until demonstrated need

- LangChain, LangGraph, or another orchestration framework.
- Pinecone, Qdrant, Chroma, `pgvector`, or a managed vector service.
- Redis or semantic response caching.
- External reranking services.
- Native mobile applications.
- Voice calls, background audio, attachments, and push notifications.
- Multi-agent autonomous planning inside Vision.

Plain Python services, typed models, SQLite, FTS5, existing Core APIs, and explicit state machines are sufficient for the recommended milestones.

---

## 15. Final Recommendation

Proceed with the following program:

1. Extract and verify the unified assistant service.
2. Implement Memory Foundation V1 with dashboard controls and strict approval policy.
3. Use the Hub status reader as the first typed read-only tool.
4. Extend Core with safe completion history so “finished” statements are truthful.
5. Deliver authenticated phone text chat through Ascend Hub.
6. Add declarative skills, then evaluate whether MCP provides enough benefit to justify its attack surface.

The next engineering artifact should be a dedicated design specification and test-driven implementation plan for **Unified Assistant Service + Memory Foundation V1**. Hub status, Core completion history, and phone chat should each receive their own subsequent specs and plans because they cross different repositories and trust boundaries.
