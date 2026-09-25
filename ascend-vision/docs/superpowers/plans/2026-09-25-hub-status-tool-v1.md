# Ascend Hub Status Tool V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer Vision voice and dashboard questions about Ascend Hub AI status from Core's authenticated read-only status shelf.

**Architecture:** A dedicated HTTP reader validates Core's shelf into typed snapshot objects. One registered read-only tool fetches the snapshot, and `AssistantService` routes explicit status questions to deterministic rendering before model generation. Startup wires the tool with an environment-provided shelf-read credential.

**Tech Stack:** Python standard library HTTP/JSON/dataclasses, existing Vision assistant runtime, Core status shelf schema v1, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-hub-status-tool-v1-design.md`

## Global Constraints

- Only `GET /api/status/shelf` with `X-Status-Read-Credential`; no producer or Vision token reuse.
- Missing credential, unauthorized response, timeout, malformed data, or stale snapshot must produce a deterministic cannot-verify answer.
- Do not claim completion from `idle`; Core has no completion record in schema v1.
- No raw shelf payload, credential, or user question in logs or persisted memory.
- One read-only tool call per assistant request; no model-selected or write-capable tools in V1.

---

### Task 1: Authenticated shelf reader

**Files:**
- Create: `integrations/status_shelf.py`
- Test: `tests/test_status_shelf.py`

**Interfaces:**
- `StatusShelfReader(base_url: str, credential: str | None, *, timeout_seconds: float = 3.0, opener=urlopen)`
- `read(now: datetime | None = None) -> ShelfSnapshot`; raises `StatusShelfError` on configuration, transport, authorization, schema, or freshness failure.
- `ShelfSnapshot(generated_at: datetime, services: tuple[ShelfService, ...])`; each `ShelfService` carries validated identity, type, normalized state, heartbeat, stale threshold, and optional safe activity label.

- [ ] Write tests proving GET path/header, no credential reuse, bounded response, valid state parsing, offline override for stale heartbeat, malformed/old/unauthorized/network failure.
- [ ] Run `python -m pytest tests/test_status_shelf.py -q`; confirm the new tests fail before the reader exists.
- [ ] Implement with strict schema v1 validation, a 256 KiB response cap, aware ISO timestamps, timeout, `Cache-Control: no-store`, and no sensitive logging.
- [ ] Run the focused tests until green.
- [ ] Commit the reader and tests.

### Task 2: Read-only tool runtime and deterministic status answers

**Files:**
- Create: `assistant/tool_runtime.py`
- Create: `assistant/hub_status.py`
- Test: `tests/test_hub_status.py`

**Interfaces:**
- `ToolRuntime.register(ToolSpec(name="hub_status", version=1, risk="read-only", arguments=frozenset()), callable)` and `ToolRuntime.start_request().call("hub_status", {}) -> ShelfSnapshot`.
- `parse_status_intent(text: str) -> StatusIntent | None` and `render_status_answer(intent: StatusIntent, snapshot: ShelfSnapshot) -> str`.

- [ ] Write tests for unknown/write-capable tool rejection, unexpected arguments, one-call budget, query recognition, target matching, all-agent answers, multiple instances, blocked/offline/unknown states, and no completion claim from idle.
- [ ] Run `python -m pytest tests/test_hub_status.py -q`; confirm failure before implementation.
- [ ] Implement a single registered read-only tool and concise deterministic renderer. Normalize known display names; include instance IDs only when needed for ambiguity. Do not pass raw shelf data to an LLM.
- [ ] Run focused tests until green.
- [ ] Commit runtime, renderer, and tests.

### Task 3: Shared assistant integration

**Files:**
- Modify: `assistant/service.py`
- Test: `tests/test_assistant_service.py`
- Test: `tests/test_assistant_session_memory.py`

**Interfaces:**
- `AssistantService(..., tool_runtime: ToolRuntime | None = None)`; status intent is evaluated before model generation, with `ToolRequest.call("hub_status", {})` and deterministic response/failure text.

- [ ] Add tests proving status requests use the tool without model calls, ordinary chat still uses the model, tool failure never causes a guessed status, and status responses do not persist as approved memory.
- [ ] Run focused tests to see the new failures.
- [ ] Route only explicit status intents through the registered tool. Use a fixed cannot-verify response for any tool failure; log outcome class only.
- [ ] Run focused tests until green.
- [ ] Commit assistant wiring and tests.

### Task 4: Runtime configuration, documentation, and final verification

**Files:**
- Modify: `main.py`
- Modify: `README.md`
- Test: `tests/test_main.py`

**Interfaces:**
- Startup reads `ASCEND_STATUS_READ_CREDENTIAL` and `ASCEND_CORE_BASE_URL` (falling back to `ASCEND_BASE_URL` / local default) and registers the reader as `hub_status` for the shared assistant. It never emits the credential in logs.

- [ ] Add startup tests for configured and missing credentials, including one voice/dashboard shared assistant path and safe operation when the reader cannot connect.
- [ ] Run focused tests to see the new failures.
- [ ] Wire the reader/runtime before both voice and dashboard bind to `AssistantService`; document Core credential provisioning and the exact current-status/completion limitation.
- [ ] Run focused tests, full `python -m pytest -q`, `node --check static/dashboard.js`, and `git diff --check`.
- [ ] Review the branch diff and commit the runtime/docs/tests.
