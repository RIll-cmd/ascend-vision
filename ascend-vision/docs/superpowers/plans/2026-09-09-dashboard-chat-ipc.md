# Dashboard Chat IPC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed dashboard chat surface that uses the existing Vision routing and Core-confirmation flow.

**Architecture:** A local SQLite inbox/outbox bridges the independent dashboard and Vision runtime processes. Dashboard routes only enqueue text and return safe replies; `main.py` claims messages and invokes its existing text handler. Core remains authoritative for validation, persistence, and execution.

**Tech Stack:** Python, Flask, SQLite, vanilla HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-dashboard-chat-ipc-design.md`

## Global Constraints

- Dashboard remains loopback-only.
- No endpoint accepts or returns Core credentials, JWTs, integration keys, or provider payloads.
- Typed and voice input use the same Vision routing and confirmation state.
- Automation mutations require explicit confirmation and Core validation.
- Existing intentional working-tree changes must not be reset, stashed, discarded, or overwritten.

### Task 1: Queue boundary

**Files:** Create `integrations/chat_ipc.py`; test `tests/test_chat_ipc.py`.

- [ ] Write failing tests for bounded enqueue, transactional claim, reply ordering, and malformed input.
- [ ] Run the focused tests and observe the expected failures.
- [ ] Implement the SQLite queue with WAL, parameterized SQL, atomic claim, and bounded retention.
- [ ] Run the focused tests to green.

### Task 2: Dashboard HTTP surface

**Files:** Modify `dashboard.py`, `templates/dashboard.html`, `static/dashboard.js`, `static/dashboard.css`; test `tests/test_dashboard.py`.

- [ ] Add failing route/UI tests for enqueue, cursor polling, validation errors, and accessible chat controls.
- [ ] Implement routes using the queue boundary without embedding routing or Core calls.
- [ ] Add the chat panel and restrained existing-dashboard styling.
- [ ] Run dashboard tests and the focused suite.

### Task 3: Runtime consumer and feedback

**Files:** Modify `main.py`, `integrations/chat_ipc.py`; test `tests/test_chat_runtime.py`.

- [ ] Add failing tests proving claimed typed messages invoke the same unmatched-text handler and persist safe reply statuses.
- [ ] Implement a daemon consumer with stop handling and bounded polling.
- [ ] Ensure confirmation-required and Core/auth failures become safe user-facing replies; do not expose secrets.
- [ ] Run runtime and integration tests.

### Task 4: Regression verification and status

- [ ] Run the complete Vision suite with the repository Python 3.12 environment.
- [ ] Run compilation and `git diff --check`.
- [ ] Update `D:\ascend-coordination\VISION_STATUS.md` with changed UI/IPC behavior, APIs consumed, authentication, tests, blockers, and Core next steps.
