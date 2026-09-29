# Ascend Vision assistant delivery plan

**Status:** Approved for execution by the user (“do it”). Gates A and B are merged. Gate C code is implemented in isolated Core and Vision worktrees but is not yet merged or deployed. Mocked verification: Core status/AIRA passed 98 tests in the prior focused run; Vision focused status/shelf tests pass 97 and the latest full suite passes 602 (one existing Google GenAI deprecation warning). A broad Core suite remains incomplete because of an unavailable DB-dependent test and a stalled rerun. Staging Core URL/read credential are unavailable, and no live PostgreSQL validation or schema push was performed.

## Goal

Land the shared assistant and verified Hub-status work safely, then make “finished” answers evidence-based, then let the owner chat with the same assistant from a paired phone. Keep memory opt-in and approved, Core authoritative, and remote access authenticated.

## Why this order

The [future-features audit](ai_assistant_future_features_audit.md) separates memory, current status, completion history, and phone chat because they cross different trust boundaries. PR #1 (shared chat and approved memory) merged as `94d48c5` after resolving its two review findings; the full Vision suite passed (496 tests). PR #2 (read-only Hub status) merged as `eccd773`; after rebasing its ancestry via a merge commit, the full Vision suite passed (573 tests). Its mocked shelf reads, failure behavior, and deterministic named-agent routing are covered. The staging shelf was not contacted. Core's current shelf reports `idle`, `working`, `stuck`, or heartbeat-derived `offline`, but has no completion record. Its active-operation rows are deleted at finish, and the existing AIRA wrapper also calls finish in a `finally` block, so `idle` cannot be translated into “finished successfully.”

| Gate | Deliverable | Must be true before advancing |
| --- | --- | --- |
| A | Merge PR #1 | Review findings fixed; focused and full Vision tests pass; privacy checks pass |
| B | Merge PR #2 | Diff contains only status work; mocked read/failure cases pass. Live credential check deferred until staging access exists |
| C | Core completion history + Vision reader | Only explicit, authenticated finish events can produce a completion claim |
| D | Paired phone text chat | Same AssistantService, correct user/device isolation, durable delivery, no inbound Vision port |

## Gate A — finish and land memory PR #1

1. Work in the existing `codex/assistant-memory-v1` worktree; preserve unrelated edits in the main checkout. Re-read current PR comments before changing code.
2. Add regression tests for “What do you remember about me?” with five maximum-length approved facts and a small `max_words`. Bound the deterministic spoken reply to the request's word budget while keeping the dashboard's approved-memory list complete. Do not truncate or delete stored facts merely to shorten a reply.
3. Add a timing regression test where a dashboard message is processed immediately after its bridge starts. Give chat context one immutable session-start timestamp, so early and later replies use the same baseline; keep throughput timing separate.
4. Run focused tests, the full Vision pytest suite, dashboard JavaScript syntax check, and diff/secret checks. Review memory approval, deletion, disabled-memory, and transcript-retention behavior again. Update PR #1 with the fixes and evidence.
5. Review was resolved and PR #1 was merged with a merge commit (`94d48c5`).

**Acceptance:** Voice and dashboard use one assistant; only approved facts survive restarts; deterministic memory replies respect the channel word limit; an immediately handled dashboard message has the correct session duration; the PR has no unresolved actionable findings.

## Gate B — land verified current status PR #2

1. PR #2 was retargeted to `main`. A merge commit (`adb2f46`) reconciled the status branch with merged Gate A changes; its PR diff contained only the status reader, read-only tool, routing, docs, and tests.
2. The exact final status candidate passed the full suite (573 tests), `node --check static/dashboard.js`, and `git diff --check`. Coverage includes voice/dashboard paths, multiple and unknown named agents, personal-query exclusions, stale data, missing credential, unauthorized response, and the rule that `idle` never becomes `finished`.
3. Staging credentials and URL are currently unavailable. Use the existing mocked response tests for the code merge, and defer credential provisioning and live smoke testing until the staging endpoint and operator access are available. At that time, provision a dedicated Core shelf-read credential through Core's operator CLI and place it in Vision's secret environment. Do not reuse producer or Vision bearer credentials; do not paste the secret into a PR, log, screenshot, or chat. Confirm Core URL uses HTTPS remotely (loopback HTTP is acceptable locally).
4. When staging is provisioned, run a live smoke test with a registered producer: ask by dashboard and microphone while the producer is working, idle, and absent/stale. Confirm the answer is sourced from Core, and loss of Core produces “cannot verify,” not a model guess. Revoke or rotate the test credential under the operator's normal procedure.
5. The actionable code review finding was fixed and resolved. PR #2 merged as `eccd773` after mocked verification. Do not describe live integration as verified until step 4 passes.

**Code acceptance:** Mocked status questions return shelf-backed states; stale data cannot be reported as current; no status question invokes an LLM to invent an answer; no write capability is exposed by the tool. **Deferred deployment acceptance:** A live question such as “Is Codex CLI still working?” returns the staging Core state after the endpoint and credential are provisioned.

## Gate C — truthful completion history (separate Core and Vision PRs)

The Core design/spec and implementation plan are in the isolated `codex/status-completion-history` worktree; Core owns the record and Vision only reads it. Core lifecycle persistence, AIRA outcome classification, authenticated producer-event ingestion, and the Vision history reader/renderer are implemented in local candidate branches. The database schema has been validated and the Python client generated, but the schema has not been applied to any database. Proposed contract:

- Persist an immutable, bounded completion record keyed by service ID, instance ID, and operation ID, with outcome (`succeeded`, `failed`, or `cancelled`), finish time, and an optional short safe label. Keep the latest 20 per instance as the idempotency horizon; after eviction, reject an unmatched terminal retry. Producers must use a fresh, never-reused operation ID for each operation. Never store prompts, code, private output, or secrets.
- Record completion only when an identified operation explicitly ends. An idle transition, expired heartbeat, process exit, or disappearance produces no successful-completion record. Make a duplicate finish return the already-recorded outcome without changing it; reject a first-time finish without a matching active operation.
- Fix Core's AIRA operation wrapper so exceptions record `failed`, cancellation records `cancelled`, and normal exit records `succeeded`; `finally` alone must not imply success. Preserve accurate `working` state while other operations remain active.
- Core now accepts authenticated, instance-bound lifecycle events and records explicit outcomes. No Antigravity or Codex CLI producer adapter was found or installed; until one is wired and tested, Vision must **not** claim that either external agent finished. This remains a release gate for the user's Antigravity example.
- Expose only the latest safe completion evidence through a backward-compatible, versioned Core read contract. Extend Vision's shelf parser and deterministic answer renderer to say, for example, “Antigravity is idle; its last recorded task succeeded at 14:32.” Keep current state separate from the last outcome, especially when the agent is now offline.

Use tests for success/failure/cancellation, duplicate and out-of-order events, restart persistence, concurrent operations, unauthorized producer, schema compatibility, stale shelf, and no false “finished” claim. Migrate Core's Prisma/Postgres schema with rollback and existing-row compatibility checks. Ship Core first, then Vision, with a live cross-repository smoke test before either completion wording is enabled in production.

**Acceptance:** “Finished” is uttered only for an authenticated completion record matching the requested agent/instance; failed or cancelled work is described accurately; no completion evidence means Vision reports current status plus “I can't confirm the last task finished.”

## Gate D — paired phone text chat (separate design/spec and plan)

Build an authenticated Ascend Hub PWA first, not a native app or a public Vision endpoint. The phone sends text to a Core-owned durable per-user/per-device queue; Vision retrieves it over an outbound connection, calls the existing AssistantService, and posts the reply back. Use SSE for updates with polling fallback.

The first release includes explicit device pairing/revocation, user and device authorization on every message, message IDs and idempotent processing, bounded size/retention, queued/processing/completed/failed/expired states, reconnection after refresh, and rate limits. An explicit phone-channel policy is enforced before AssistantService handles commands: Phone V1 allows ordinary chat, approved-memory recall, and read-only status only; it blocks memory deletion, Core writes, and other consequential commands with a clear explanation. A later action phase must add a separate single-use, short-lived, user- and device-bound confirmation card; conversational “yes” is not authorization. Provider keys, Vision tokens, and status credentials never reach the phone.

Test wrong-user and unpaired access, replay, duplicate delivery, device disconnect/reconnect, Vision offline behavior, blocked memory/Core mutations, CSRF where session cookies are used, secret redaction, and end-to-end use of the same assistant/memory policy as local chat. Defer phone voice, push notifications, attachments, camera streaming, and native apps until text delivery is reliable.

**Acceptance:** A paired phone can send text and receive a durable reply from the same Vision assistant; another user/device cannot see or submit it; a temporary Vision outage cannot silently lose or duplicate the request.

## Hold points and exclusions

- Review and approve this sequence before implementation. Gates C and D each get their own detailed spec, test-first implementation plan, and PR review before work begins; approval of this document is not blanket permission to deploy remote infrastructure or issue production secrets.
- If PR review, tests, migration checks, or live smoke tests fail, stop at that gate and repair it before advancing. Current PRs have no reported automated checks, so local green tests must be recorded; adding CI is a separate improvement, not a substitute for the live integration test.
- No automatic merge into the dirty local `main` checkout. No public inbound Vision server, unapproved persistent transcripts, MCP server, arbitrary executable skills, or completion inferred from `idle`.
