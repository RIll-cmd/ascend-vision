# B4 — Phone and Discord Browser Control Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This document plans work; it does not authorize deployments or mark the work implemented.

**Goal:** Start a scoped Vision browser task from the PWA or linked Discord account, inspect progress, stop it, and review eligible actions on the phone while execution stays on the selected laptop.

**Architecture:** Core owns durable remote tasks, source identity, claims, review decisions, and cancellation. An outbound Vision worker connects those tasks to the existing local browser broker. The broker remains the only browser executor and checks Core authorization immediately before remote dispatch; a phone never connects to Playwright directly.

**Tech Stack:** Existing FastAPI, PostgreSQL/Prisma, Next.js/React, Python/httpx, Discord.py, Playwright Chromium, Windows named pipes, and the local SQLite action journal. No new orchestration platform or browser engine.

**Spec:** [Browser automation roadmap, B4](./2026-09-28-vision-browser-automation.md#b4--phone-and-discord-initiation). Existing behavior: [browser operations guide](../../browser-automation.md).

**Status (2026-09-29):** Local B4-A/B4-B/B4-C source paths are implemented; release acceptance remains open. The original checkboxes remain planning criteria, not an implied release sign-off. See the [current acceptance audit](../../audits/2026-09-29-browser-b4-implementation-status.md) and [remote operations guide](../../browser-remote-operations.md) for evidence and unresolved gates. This implementation does not close B2/B3 live-provider, voice or signed-in-site acceptance.

## 1. Scope and expected experience

Deliver three usable slices in sequence:

1. **B4-A — PWA research:** A dedicated Browser panel starts public research, shows progress and sources, and offers Pause/Resume/Stop. No signed-in profile is exposed by this slice.
2. **B4-B — PWA reviewed actions:** A locally enabled site scope permits eligible B3 actions. A phone review card shows the exact origin, target, values, expected effect, expiry, and target laptop. Approval applies once to that proposal.
3. **B4-C — Discord:** `/browser start`, `/browser status`, and `/browser stop` use the same Core task service. Detailed review uses an explicitly opened, authenticated PWA task view. Existing `/status` continues reporting Hub agent status.

Examples:

- PWA: “Find the official documentation for this Python library and summarize the installation steps.” The visible Vision browser runs on the laptop; the phone receives progress and linked findings.
- PWA: Choose an enabled site scope and request a form edit. Vision shows a proposal before changing the field, including the autosave warning. The owner can approve, reject, or stop.
- Discord: `/browser start goal: Compare these two documentation pages` returns a durable task ID and current status. `/browser status task_id:…` reports that specific task in its originating Discord browser session.
- Laptop asleep: PWA shows “Laptop offline — queued until it reconnects,” with an expiry. A locally hosted Discord bot is also offline when that laptop sleeps; its availability is not improved by this queue.

The initial remote action set is `fill`, `select`, and reviewed `click` on a laptop-configured site scope. Submit-button clicks count as consequential actions. Only individually accepted site workflows may enable these capabilities. Remote file upload, file delivery to the phone, saved-profile administration, CAPTCHA solving, password/MFA entry, and changing laptop scope configuration are outside B4. Existing local B3 files remain local capabilities.

Ordinary `/ask`, phone audio, and plain phone chat do not silently become browser commands. They may point to the dedicated Browser panel. Natural-language remote routing can be considered after the explicit entry points pass acceptance.

## 2. Global constraints

- Single owner and one enrolled execution laptop for V1; identifiers still bind every request explicitly.
- Separate PWA, Discord, local-dashboard, and voice sessions. A task is not visible in another session merely because both sessions have the same owner.
- “Keep execution outbound from laptop to Core. Never publish a Playwright/CDP/MCP port for the phone to connect to.”
- “Clear prompt/result payloads on terminal acknowledgment or expiry; retained tombstones contain no conversation content.”
- “A sleeping laptop cannot carry out fresh browser work.”
- Keep browser feature flags disabled by default. Remote write support has its own flag and requires B3 to be enabled and the selected workflow accepted.
- Page content and model output cannot change scope, identity, provider, the destination laptop, or authorization.
- A transport retry must not repeat a website action. No claim of exactly-once execution on arbitrary websites.
- Preserve existing worktree changes. Do not merge, deploy, rotate credentials, or publish branches as an incidental planning step.
- Follow Core `client/AGENTS.md`: read the installed Next.js guides under `client/node_modules/next/dist/docs/` before implementing the client. This plan assumes no new framework API.

## 3. Repository findings and integration boundaries

| Inspected component | Finding | B4 consequence |
|---|---|---|
| Vision worktree | `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision`, HEAD `3ee6985`, with uncommitted B3 changes | Build on the reviewed B3 changes; HEAD alone does not identify the actual feature baseline. |
| Core working baseline | `D:/ascend-core/.worktrees/phone-chat-core`, HEAD `d388460`, with substantial uncommitted companion/phone changes | Record the selected baseline and preserve these changes before execution. |
| Other Core worktree | `phone-chat-discord`, HEAD `ee62290` | Reconcile relevant pairing fixes with the chosen baseline; do not assume all worktrees contain identical features. |
| Core route layout | Existing routes are in `server/routers/phone_chat.py` | New routes belong in `server/routers/browser_tasks.py`, not `server/routes/`. |
| Phone queue | `phone_chat_queue.py` / `phone_chat_repository.py` implement leases and payload erasure | Reuse identity/error conventions; create a browser-specific state machine and transactional claims. Do not turn browser actions into retried chat jobs. |
| Discord identity | `PhoneDiscordLink` is an owner slot reused on relink; bot chat currently uses the Discord user ID as session ID | Add a link generation and server-issued browser sessions. Relinking must not revive old tasks or grants. |
| Local browser protocol | `browser/contracts.py` and `browser/ipc.py` accept only `dashboard` and `voice` sessions | Introduce a separately validated remote envelope and IPC operation. Do not simply allow arbitrary remote session strings in local submission. |
| Local profiles | Dashboard tasks use owner key `local`; Core has a real owner ID | Map an explicitly configured remote scope to a local profile internally. Do not accept a profile ID/path from the phone or rename existing profile ownership. |
| Runtime entry | Broker starts in `main.py` via `browser/main.py` | Start the remote worker only after broker readiness, using the same resource cleanup lifecycle. |
| Existing journal | Contains minimal action metadata and restart-to-unknown behavior | Extend its records with remote task/fence identity; do not persist page text or invent a remote replay service. |

No new code, credentials, live migrations, or deployments are part of this planning change.

## 4. Ownership and flow

```text
PWA owner + enrolled device ─────────┐
                                    ├─> Core BrowserTask service + PostgreSQL
Discord bridge + active owner link ─┘               ↑
                                                    │ outbound HTTPS polling
                                       Vision browser-task worker
                                                    │ owner-restricted local IPC
                                       Existing browser broker ──> Chromium
                                                    │
                                      Core check immediately before dispatch

Progress/proposals/results: broker -> worker -> Core -> originating task view
Approval: PWA -> Core -> broker check -> one reviewed action
```

Core is authoritative for remote authorization and lifecycle. The broker is authoritative for current DOM observations and whether it attempted an action. A Core approval does not establish that a website changed. A local observation does not establish that a remote task remains authorized.

The worker is a delivery adapter, not a second planner. Run the existing planner inside the broker, preserving its provider selection and budgets. Worker claims, local tasks, and journal entries share one durable task ID.

## 5. Concrete settings and limits

| Setting | Initial value / rule |
|---|---|
| Vision `browser_automation.remote_enabled` | `false`; requires `enabled` |
| Vision `browser_automation.remote_writes_enabled` | `false`; requires `remote_enabled` and `b3_enabled` |
| Core `ASCEND_BROWSER_REMOTE_ENABLED` | `false`; enables enrollment/session creation and starts |
| Core `ASCEND_BROWSER_REMOTE_WRITES_ENABLED` | `false`; gates selected-origin tasks and approvals |
| Owner | Reuse configured `ASCEND_PHONE_OWNER_ID`; compare on Core and laptop |
| Laptop identity | Dedicated `ASCEND_BROWSER_LAPTOP_ID`; credential is bound to this configured ID |
| Worker credential | New `ASCEND_BROWSER_WORKER_TOKEN`, distinct from phone/Discord/notification/publisher credentials |
| Core origin | Reuse configured `ASCEND_PHONE_CORE_URL`; HTTPS except loopback development |
| Poll / heartbeat / freshness | Poll every 2 seconds; broker-readiness heartbeat every 15 seconds; online requires receipt within 45 seconds |
| Lease | 60 seconds; renew every 20 seconds, with active broker boot and fence |
| Queue | At most 4 waiting tasks and 1 claimed/running task per laptop; server transaction enforces the cap |
| Queue expiry | 24 hours maximum from creation, also bounded by session expiry |
| Remote browser session | Server UUID; absolute 24-hour maximum, no automatic extension from polling |
| Active execution | Existing 180-second maximum, including review/takeover time; show countdown |
| Action review | At most 120 seconds, never past active task or session expiry |
| Dispatch permission | Single action/attempt; at most 2 seconds from the start of the authorization round trip to dispatch |
| Events | 200 retained per task; pages of 50; each event at most 8 KiB; reset cursor returns authoritative snapshot |
| Goal / result | Goal 4,000 characters; result JSON at most 16 KiB; same bounded source findings as local tasks |
| Retention | Erase transient payload at acknowledgment, session end, revocation, or expiry; unacknowledged results expire after 1 hour, capped by session expiry |
| Tombstones | Minimal IDs, state, reason code, timestamps and execution uncertainty; 24 hours after terminalization; no goals, values, excerpts, URLs or proposal bodies |
| Cleanup | Authorized scheduled sweep at least once per minute, plus bounded cleanup on reads/writes; reject logically expired records immediately |

Heartbeat must reflect a responsive broker with the configured provider available, not just a live HTTP worker. Return separate `online`, `busy`, `provider_unavailable`, `disabled`, and `offline` availability reasons. A recent heartbeat is not proof of task completion.

Provider readiness means the selected provider is configured and its client can be constructed; heartbeat does not perform paid model probes or promise upstream availability. Report real provider failures on the task. Registering a new broker boot fences all previous-boot claims, invalidates pending proposals, and classifies started work as interrupted/unknown before accepting another task.

Boot registration is a separate startup operation with a compare-and-swap against Core's previous boot ID. Ordinary heartbeats can refresh only the already registered boot; they cannot replace it. Concurrent registration has one winner, and a rejected old worker stops instead of registering itself again. Restarting only the delivery worker reuses the live broker's boot identity.

Local configuration may define `remote_scopes`, each with `scope_id`, exact origin, allowed actions, optional local profile ID, and a version. Only its public descriptor (ID, origin, actions, version) goes to Core. A remote request selects an offered scope ID/version; it cannot supply an origin, provider, profile ID, local file token, or path. The broker resolves the descriptor to local configuration and checks it again before dispatch. An empty scope list permits public research only. Provider consent for remote work is captured at session creation and must match the broker's selected provider; a provider change requires new consent.

## 6. Core records and state machine

Add a versioned, additive Prisma migration. Retain existing phone tables and APIs. Use database constraints as well as application validation.

| Record | Required data and constraints |
|---|---|
| `BrowserLaptop` | ID, owner, current boot ID, heartbeat timestamp, enabled scopes/provider descriptor, revocation timestamp; no stored plaintext token |
| `BrowserRemoteSession` | Server UUID, owner, channel, exactly one source device or Discord link, link generation if Discord, parent phone-chat session reference if PWA, provider consent, expiry/end timestamps |
| `BrowserTask` | UUID, session, owner, target laptop, scope ID/version, goal, state, created/queue/active expiry, claim token hash, lease expiry, pre-start attempt count, monotonically increasing fence, broker boot, start timestamp, cancel flag, current proposal ID, event cursor, transient result, terminal reason |
| `BrowserTaskEvent` | Unique `(taskId, sequence)`, bounded transient summary/proposal reference, timestamp; ordered append with monotonic task cursor |
| `BrowserActionReview` | Unique `(taskId, actionId)`, immutable canonical proposal and digest, target laptop/boot/fence, decision/decider binding, expiry, permit-consumed timestamp, observed/unknown outcome |
| `BrowserReviewAccess` | Hashed one-use handoff token before consumption; afterward binds one task to one authenticated reviewer device/session; expiry and revocation fields |

Keep a terminal `BrowserTask` row as its tombstone after nulling all content fields and removing event/review bodies. Retain no goal hash in the tombstone. Duplicate task UUIDs after erasure return an already-used/erased response and cannot execute again. Old sessions expire before their tombstones are purged, so an old request cannot reopen an expired session.

Add `generation Int @default(1)` to `PhoneDiscordLink`. Increment in the same transaction as consume/relink/revoke changes. End browser sessions bound to the previous generation. The existing chat link verification response can remain backward compatible; browser endpoints resolve the generation internally.

```text
queued -> claimed -> running <-> paused
                       |  ^
                       v  |
                 waiting_for_user  (review or laptop takeover; subtype required)

queued/claimed-before-start -> cancelled | expired
running/paused/waiting_for_user -> stopping -> cancelled | partial | unknown
running -> completed | partial | failed | unknown
```

Rules:

- Claim transaction locks the laptop row, then picks one eligible task with `FOR UPDATE SKIP LOCKED`; increments fence and issues a random lease. All worker updates compare task ID, laptop, boot ID, lease and fence.
- Initial browser start is itself authorized and recorded before the first page observation/model call. A lost start acknowledgment can leave an uncertain start; query state, do not create another task.
- Only a claimed task proven not started may return to `queued` after lease loss; at most three pre-start claims. A started task is never automatically rerun after restart, including research tasks.
- When a started task loses its lease: stop further browser/model work; if any consequential dispatch may have occurred, terminalize `unknown`; otherwise `failed` with reason `execution_interrupted`.
- Stop of queued work completes immediately. Stop of running work reports `stopping` until broker acknowledgment; a phone click alone does not prove cancellation.
- Approval, cancellation, device/link revocation, and session end serialize on the same owner/session/task authority rows in a fixed order. A grant cannot commit after an already-committed revocation.
- Terminal state is immutable for normal completion updates. Identical completion retries may return the existing acknowledgment. A separate content-free reconciliation record can add evidence of an attempted action, but cannot reopen a task or restore erased payloads.
- Feature disable blocks starts, approvals and dispatch permission. Authenticated Stop, status, acknowledgment and cleanup remain usable.

## 7. API and protocol contracts

Base path: `/api/browser-tasks`. All payload schemas forbid unknown fields. Owner identity is derived from authentication, never from JSON. Cookie-authenticated writes require the same-origin protection used by phone chat. Responses containing task data use `Cache-Control: no-store`.

PWA calls carry `X-Phone-Device` and, after session creation, `X-Browser-Session`. These are lookup identifiers, not bearer authorization: validate them against the authenticated owner and active database records. If a body also contains either identifier it must match the header. Availability/session creation requires the enrolled device; task reads, controls, reviews and acknowledgment require the browser session as well. Reviewer access is resolved from its stored task/device/session binding, never from a client assertion that it is a reviewer.

| Route | Caller and contract |
|---|---|
| `GET /availability` | Authenticated owner plus active enrolled device; returns laptop state, provider and enabled public scope descriptors |
| `POST /sessions` | Owner/device; `{deviceId, parentSessionId, providerConsent}`; server issues bound browser session UUID |
| `POST /sessions/{id}/end` | Owning source; ends session, invalidates review/dispatch rights, requests active stop, erases payload |
| `POST /` | PWA browser session; `{taskId, browserSessionId, laptopId, goal, scopeId, scopeVersion}`; returns `202` receipt; `scopeId="public_research"`, version `1`, selects ephemeral research |
| `GET /{id}?after=…` | Exact owning source session/device or task-specific PWA reviewer grant; returns snapshot, event page and cursor |
| `POST /{id}/control` | `{command: "pause" | "resume" | "stop", browserSessionId}` with owning-source or reviewer binding; resume after takeover requires fresh observation |
| `POST /{id}/reviews/{actionId}` | PWA only; `{browserSessionId, proposalDigest, approved}`; derive reviewer/source identity server-side |
| `POST /{id}/ack` | Owning source or explicit reviewer access; erases terminal task payload; polling alone never acknowledges |
| `POST /review-access/consume` | Owner + active PWA device/session, one-use token from Discord handoff; returns task-scoped reviewer access |
| `POST /bridge/discord/{start,status,stop,end-session,review-link,ack}` | Existing dedicated Discord bridge credential plus interaction user ID; Core verifies current link/generation and server session every time |
| `GET /worker/identity`, `POST /worker/register-boot` | Dedicated worker only; read current boot, then atomically register `{expectedBootId, newBootId}` once at startup; repeat of the same successful registration is idempotent |
| `POST /worker/heartbeat` | Browser-worker credential; server derives laptop/owner, validates boot and bounded availability descriptor |
| `POST /worker/claim` | Browser-worker credential and current boot; `204` when no eligible task |
| `POST /worker/{id}/{start,renew,events,proposal,authorize-dispatch,complete,reconcile}` | Dedicated worker credential, `X-Browser-Lease`, `X-Browser-Fence`, `X-Browser-Boot`; exact schema per operation |
| `POST /api/cron/browser-tasks-sweep` | Explicit configured cron authentication even outside production; bounded expiry/erasure work |

Never import route-private phone helpers into the new router. Extract only genuinely shared owner/origin/credential dependencies into `server/services/phone_access.py`, retaining backward-compatible wrappers where current tests import existing helpers. Keep browser authorization in its own service.

Recommended errors: `401` unauthenticated; `404` absent or inaccessible task; `409` stale lease/proposal, closed session, or conflicting idempotency payload; `410` erased result for an otherwise authorized source; `413` body limit; `429` queue/rate cap; `503` capability/provider unavailable. No response confirms another session's task existence.

New Vision envelope, separate from local `BrowserTaskRequest` v1:

```python
@dataclass(frozen=True)
class RemoteBrowserBinding:
    task_id: str
    owner_id: str
    channel: str                 # phone_pwa or discord_dm
    browser_session_id: str      # Core-issued UUID
    laptop_id: str
    broker_boot_id: str
    lease_id: str
    fence: int
    scope_id: str
    scope_version: int
```

Create `RemoteBrowserBinding.from_payload(payload: dict) -> RemoteBrowserBinding` and `RemoteBrowserTaskRequest.from_payload(payload: dict) -> RemoteBrowserTaskRequest` with exact-field, UUID, bounds, expiry and channel validation. A remote request carries this binding plus goal, provider consent and deadlines. IPC `submit_remote` accepts it only when the remote feature and broker-side guard are configured. Keep legacy `submit` local-only. Remote `events/control/decide` operations validate the complete binding, not a caller-supplied local session key. A local emergency stop may stop all tasks but must not grant remote actions.

Implement `CoreBrowserClient` async methods `identity`, `register_boot`, `heartbeat`, `claim`, `start`, `renew`, `publish_events`, `publish_proposal`, `complete`, `reconcile`. Each accepts the validated binding where task-scoped. `renew` returns current control state and latest decision identity. Add synchronous `RemoteDispatchGuard.authorize(binding, attempt_id, action, proposal_digest: str | None) -> DispatchPermit` inside the broker's execution thread; it calls `authorize-dispatch` immediately before dispatch. `DispatchPermit` binds the attempt/action and full execution identity, plus its expiry; a consequential action requires a non-null digest. Read checks and the `plan` operation use a null digest and no owner-review grant, while still checking the current scope and authority. No model or UI gets a worker credential.

### Exact dispatch sequence

1. Check local stop/pause/expiry, task binding and configured scope. Revalidate the observed target and action arguments using B3 checks.
2. For a consequential action, require the exact single-use local grant, matching Core review and unchanged proposal digest. Write durable intent with task, action ID, laptop, boot, fence and digest; omit content.
3. Call Core `authorize-dispatch`. Core atomically checks active device/link generation/session, enabled capability, scope/provider version, task state, target laptop/boot, unexpired lease/fence and exact approved proposal, then consumes the permit for that attempt.
4. If the response is lost, rejected or takes more than the 2-second window, do not dispatch or retry that action. Record known non-dispatch locally when provable. Otherwise recheck local stop/deadline/target and dispatch once.
5. Record post-action observation as `observed`, or uncertainty as `unknown`; publish progress using the same attempt identity. `observed` is not proof of website persistence.

All remote planner turns and browser actions also require a fresh authority check, including navigation and observation. Read-only checks do not require an owner action-review card. This bounds additional data processing after session end; an already-started provider request cannot be recalled.

Core can revoke immediately after granting a permit. This unavoidable interval is recorded honestly: stop subsequent dispatches, reconcile any in-flight action, and never promise revocation undoes a website change. Two-second permits limit the interval but do not eliminate it.

## 8. Discord-to-PWA review without merging sessions

`/browser start` initially supports public research. The PWA owns selected-origin starts. Discord status may issue a review/open link for an existing task; this is also usable to inspect a longer result.

On the first Discord browser request in a session, show the configured provider and an explicit “Start this browser session with [provider]” interaction before creating the session/enqueuing work. Bind the button to that Discord user and pending task intent, expire it after 60 seconds, and recheck the link generation when clicked. Keep the pending goal only in bot memory during that minute. Later requests reuse that session's consent; a provider change ends it and requires new consent. Duplicate clicks use the same task UUID. This confirmation is provider/session consent, not authorization for any future site write.

- Core issues a random 32-byte, one-use handoff token valid for 5 minutes, stores its hash, and binds it to one task, source Discord session, owner and current link generation.
- Link format is the configured PWA origin plus `/browser-tasks/review#handoff=<token>`. Keep tokens out of query parameters, logs and analytics; remove the fragment from browser history after capture.
- The page requires the same authenticated Core owner and an active PWA device. Show “Open this Discord browser task on this phone?”; only an explicit POST consumes the token.
- Resulting reviewer access is limited to this task and bound to the current PWA device/browser session, at most 10 minutes and no later than task/session expiry. It never imports either chat transcript or changes the task's originating channel.
- Reads and decisions recheck both the source Discord link/session and the reviewer device/session. Revoking either immediately disables access. A copied URL, account login alone, or knowing a task UUID is insufficient.
- The same authorization service permits reviewer decisions only against the current action ID/digest. Expired links can be replaced through `/browser status` in the active source session.
- Discord command delivery is best-effort. Defer the interaction promptly using existing bot patterns, then return the Core receipt. Use the Discord interaction ID as request idempotency input, normalized to the task UUID contract. Redelivery returns the original task; it never starts another.

## 9. Implementation tasks

Paths below are relative to their named repository root. New test fixtures and signatures described here are part of the task deliverable, not existing APIs.

### Task 1 — Freeze contracts and baseline

**Files:** Vision `browser/remote_contracts.py`, `config.py`, `config.yaml`, `tests/test_browser_remote_contracts.py`, `tests/test_config.py`; Core `server/schemas/browser_tasks.py`, `server/tests/test_browser_task_schemas.py`; both use the contract examples in this document.

**Produces:** `RemoteBrowserBinding`, `RemoteBrowserTaskRequest`, strict Core request/response models, capability settings and local `remote_scopes` validation.

- [ ] Record the two selected worktrees, actual reviewed diffs and commit IDs in a B4 implementation evidence document. Confirm B3 files exist on the chosen Vision base. Resolve source selection before cross-repository coding; preserve unrelated edits.
- [ ] Write schema tests rejecting owner override, arbitrary provider/profile/path/origin, invalid fence, unknown channel, expired session, and remote mode with local browser disabled.
- [ ] Define exact schemas and the limits from sections 5–7. Include contract round-trip samples for claim, proposal, decision and terminal reply used by both test suites.
- [ ] Verify local v1 requests still reject remote sessions; remote envelopes are rejected until a guard is installed.

```python
def test_remote_binding_rejects_missing_execution_fence():
    payload = valid_remote_payload()  # fixture contains every field in section 7
    payload.pop("fence")
    with pytest.raises(ValueError):
        RemoteBrowserBinding.from_payload(payload)
```

**Check:** Vision `python -m pytest tests/test_browser_remote_contracts.py tests/test_config.py -q`; Core/server `python -m pytest tests/test_browser_task_schemas.py -q`.

### Task 2 — Durable sessions, claims and erasure

**Files:** Core `server/prisma/schema.prisma`, `server/prisma/migrations/20260928_browser_tasks_b4/migration.sql`, new `server/services/browser_task_repository.py`, `server/services/browser_tasks.py`, `server/tests/test_browser_task_repository_postgres.py`, `server/tests/test_browser_tasks.py`.

**Consumes:** Task 1 schemas. **Produces:** `BrowserTaskService.create_session`, `end_session`, `enqueue`, `claim`, `start`, `renew`, `control`, `complete`, `ack`, `sweep`; `PostgresBrowserTaskRepository` transaction methods. Each service method accepts a server-derived principal or worker binding and an injectable UTC clock.

- [ ] Add records/constraints from section 6; migration adds objects and the link-generation column without dropping existing phone data.
- [ ] Implement claims with laptop-row serialization and task row locking. Use database time for lease comparisons and transactional quota checks, not a Python scan of all jobs.
- [ ] Make enqueue idempotent within an active source session. A changed payload under the same task UUID is a conflict; terminal erasure never turns into a new task.
- [ ] Implement transitions, bounded cleanup and nulling of all content-bearing fields/events/reviews. Cancel/session-end wins over late completion and content cannot be restored.
- [ ] Test concurrent claims/enqueues, lost start response, pre-start lease recovery, started-task non-replay, expired/revoked sessions, completion retry, and cleanup after acknowledgment/TTL.

```python
async def test_two_workers_cannot_claim_the_same_task(service, worker_a, worker_b):
    await enqueue_fixture_task(service)
    claims = await asyncio.gather(service.claim(worker_a), service.claim(worker_b))
    assert sum(claim is not None for claim in claims) == 1

async def test_expired_started_task_is_not_requeued(service, running_task, clock):
    clock.advance(seconds=61)
    await service.sweep()
    assert await service.claim(running_task.worker) is None
```

**Check:** `python -m pytest tests/test_browser_tasks.py tests/test_browser_task_repository_postgres.py -q`. Supply `BROWSER_TEST_DATABASE_URL` for an explicitly isolated PostgreSQL database/schema. These tests must refuse an unmarked production URL and must not silently substitute SQLite. Restore/reset only their uniquely named test schema. Verify migration against a copy of the preceding schema with existing phone rows.

### Task 3 — Authentication, revocation and source lifecycle

**Files:** Core `server/services/phone_access.py`, `server/routers/browser_tasks.py`, `server/main.py`, `server/services/phone_chat_pairing.py`, `server/services/phone_chat_repository.py`, `server/routers/phone_chat.py`, `server/routers/cron.py`, `server/tests/test_browser_task_routes.py`, `server/tests/test_browser_task_worker_auth.py`; existing pairing and phone tests.

**Consumes:** Task 2 service. **Produces:** PWA routes, isolated worker credential dependency, Discord source resolution, authenticated sweep route.

- [ ] Reuse Core owner auth and active phone-device enrollment. Limit shared helper extraction to identity/origin checks and keep existing phone behavior covered.
- [ ] Bind the worker token to the configured owner/laptop, and check it differs from existing transport credentials. Never accept laptop/owner overrides from a claim body.
- [ ] Increment Discord link generation on link changes; invalidate old browser sessions in the same transaction. Hook device revoke into browser session end/stop/erasure.
- [ ] Add PWA browser-session end support; wire an explicit request into new-chat/logout flows in Task 7. Do not depend on browser unload events for revocation.
- [ ] Add strict cron authentication and bounded sweeping. The operational runbook must provision a recurring caller; a route alone does not satisfy retention.
- [ ] Test that phone worker, Discord bridge and browser worker credentials cannot call each other's privileged routes; test cross-owner/device/session failures, same-origin checks and no-store responses.

```python
def test_phone_worker_token_cannot_claim_browser_work(client, phone_worker_token):
    response = client.post("/api/browser-tasks/worker/claim",
                           headers={"Authorization": f"Bearer {phone_worker_token}"})
    assert response.status_code == 403
```

**Check:** Core/server `python -m pytest tests/test_browser_task_routes.py tests/test_browser_task_worker_auth.py tests/test_phone_chat.py tests/test_phone_chat_pairing.py tests/test_phone_chat_worker_auth.py -q`.

### Task 4 — Outbound worker and honest laptop availability

**Files:** Vision `integrations/browser_task_worker.py`, `browser/ipc.py`, `browser/main.py`, `main.py`, `tests/test_browser_task_worker.py`, `tests/test_browser_ipc.py`, `tests/test_main.py`.

**Consumes:** Tasks 1–3 contracts. **Produces:** `CoreBrowserClient` and `BrowserTaskWorker.run_once/start/stop`; IPC `capabilities`, `submit_remote`, `remote_events`, `remote_control`. Broker identity includes a fresh boot UUID and immutable configured scope version.

- [ ] Add strict Core response parsing and outbound HTTPS enforcement. Reject unexpected owner, laptop, boot, provider or scope before broker submission.
- [ ] Start worker only after broker readiness; do not claim while the broker is busy with a local task. Handle the local-task race by declining/releasing an unstarted claim safely.
- [ ] Register the broker boot once with compare-and-swap; test that delayed old-worker heartbeats and rejected registration attempts cannot replace the new boot or alternate ownership.
- [ ] Renew leases independently of model calls; publish availability only after broker ping/capability checks. Apply returned stop/pause/review controls using the current binding.
- [ ] Forward bounded broker events with unique sequence identity. Keep a Core cursor, handle broker ring-buffer reset with an authoritative snapshot, and never use an old event to regress state.
- [ ] Retry status/completion delivery only with the original identity and valid claim; never retry a browser action. Redelivered task submission queries the existing broker task first.
- [ ] On renewal failure or network uncertainty, block broker work and request local stop; do not wait for a model call to finish before setting the stop condition. Recover status separately.

```python
async def test_lease_loss_stops_existing_task_without_resubmission(worker, core, broker):
    core.renew_result = "lease_lost"
    await worker.run_once()
    assert broker.remote_stop_count == 1
    assert broker.remote_submit_count == 1
```

**Check:** Vision `python -m pytest tests/test_browser_task_worker.py tests/test_browser_ipc.py tests/test_main.py -q`. Use mocked HTTP for this task; no staging credential is required.

### Task 5 — Broker dispatch guard and recovery

**Files:** Vision `browser/remote_guard.py`, `browser/service.py`, `browser/authorization.py`, `browser/journal.py`, `browser/main.py`, `browser/ipc.py`, `tests/test_browser_remote_guard.py`, `tests/test_browser_recovery.py`, `tests/test_browser_service.py`; Core `server/services/browser_tasks.py`, `server/services/browser_task_repository.py`, `server/tests/test_browser_task_authorization.py`.

**Consumes:** Full binding and validated local scope. **Produces:** `RemoteDispatchGuard.authorize`, `DispatchPermit`, Core `authorize_dispatch`, remote journal reconciliation metadata.

- [ ] Install the guard in the broker itself. A worker-side check must not be the sole authorization check. Refuse remote tasks if the guard is absent even if IPC input validates.
- [ ] Implement the exact sequence in section 7, reusing B3 target revalidation and one-time grants. Add authority checks before remote model requests/read actions and before consequential actions.
- [ ] Tie Core proposal digest to the current task/laptop/boot/fence, and consume approval atomically with dispatch permission. Do not infer approval from a task's general `running` state.
- [ ] Extend the local journal using a compatible SQLite migration with nullable remote metadata for existing local entries. Record remote task acceptance/start metadata without storing the goal so broker restart cannot silently rerun an uncertain task.
- [ ] Let a newly started broker report old uncertain attempts using a dedicated reconciliation operation. Reject stale normal completion/control writes; reconciliation may add only non-content evidence and cannot authorize action.
- [ ] Exercise cancellation before permit, revocation during review, stale target after approval, Core timeout, expired permission window, lost permission response, and crash after website effect before acknowledgment.

```python
def test_remote_action_does_not_dispatch_when_core_is_unavailable(remote_service, core, executor):
    core.authorize_error = TimeoutError()
    remote_service.run_reviewed_fixture_action()
    assert executor.consequential_dispatches == 0

def test_late_permit_is_discarded(guard, core, monotonic_clock):
    core.on_authorize = lambda: monotonic_clock.advance(seconds=3)
    with pytest.raises(PermissionError):
        guard.authorize(fixture_binding(), "attempt-1", "click", "a" * 64)
```

**Check:** New guard/authorization tests plus existing B3 service/recovery tests. Include a controlled local test site and real Chromium for before-dispatch, after-dispatch, and before-acknowledgment process termination. Count actual fixture-site side effects across restart; the required count is at most one. Journal-only subprocess tests are insufficient for this gate.

### Task 6 — Review cards and task-specific Discord handoff

**Files:** Core `server/services/browser_task_review.py`, `server/routers/browser_tasks.py`, `server/schemas/browser_tasks.py`, `server/tests/test_browser_task_review.py`; Vision worker/guard from Tasks 4–5.

**Produces:** Core `publish_proposal`, `decide_review`, `issue_review_access`, `consume_review_access`; task-specific reviewer authorization consumed by Task 7.

- [ ] Persist one current immutable proposal per active task; reject replaced/expired proposal digests. Exact repeated approval is idempotent; approval/rejection conflicts are rejected.
- [ ] Implement the hashed one-use handoff and reviewer-device/session binding from section 8. Compare source link generation and target laptop/fence on every decision.
- [ ] Set task `waiting_for_user` subtype to `action_review` or `local_takeover`; do not display phone approval controls for manual laptop login/MFA.
- [ ] Invalidate pending proposals on resume/re-observation, scope changes, session end, stop, lease loss or broker restart. Worker applies only the current Core decision; broker still performs the final dispatch check.
- [ ] Test copied token under another owner, consumed/expired token, source unlink/relink, reviewer logout, changed content under the same action ID, stale fence and simultaneous approve/stop.

```python
async def test_unlink_then_relink_does_not_revive_old_review(service, linked_task):
    access = await service.issue_review_access(linked_task)
    await revoke_and_relink_fixture_owner()
    with pytest.raises(PermissionError):
        await service.consume_review_access(access.token, fixture_pwa_principal())
```

**Check:** Core/server `python -m pytest tests/test_browser_task_review.py tests/test_browser_task_authorization.py tests/test_phone_chat_pairing.py -q` against both service fixtures and isolated PostgreSQL race tests.

### Task 7 — PWA Browser panel

**Files:** Core/client new `src/features/browser-tasks/types.ts`, `api.ts`, `useBrowserTasks.ts`, `BrowserTaskPanel.tsx`, `BrowserTaskCard.tsx`, `BrowserActionReview.tsx`, corresponding tests; `src/app/(dashboard)/browser-tasks/review/page.tsx`; modify `src/app/(dashboard)/phone-chat/page.tsx`, `src/features/phone-chat/usePhoneChat.ts`, `phoneChatSession.ts` and logout/new-chat tests.

**Consumes:** Tasks 3 and 6 API. **Produces:** A phone-sized task form, status/result cards, stop controls, exact review UI and explicit Discord task handoff.

- [ ] Read installed Next.js routing/server-client guidance required by `client/AGENTS.md`. Reuse existing authenticated API client, layout and UI components.
- [ ] Create a Browser tab beside Chat. Offer target laptop availability, approved provider, public research or enabled site scope, and goal input. Disable unavailable actions with a concrete reason.
- [ ] Use one task UUID per submission intent and retain it on transport failure. Poll every 2 seconds while visible; stop background polling when hidden and refetch on focus. Server authorization always remains decisive.
- [ ] Render progress and sources as text/validated HTTP(S) links. Show `stopping` until acknowledged; label expired/offline/unknown separately. Never render raw page HTML or cache task content in the service worker.
- [ ] Display full bounded values and target/origin before approval; reject truncated/invalid proposal data. Show an explicit autosave warning for fill/select. Clear a review card immediately when its action ID/digest or task authority changes.
- [ ] Wire new-chat/logout to end browser sessions before local state reset when online. For an offline failure, erase local content and retain only the pending revocation/session-end IDs for retry; show that remote stop is not yet confirmed. TTL remains the fallback, not an invented acknowledgment.
- [ ] Keep result text in session memory/storage only; acknowledge after explicit dismiss/clear so a refresh does not erase the result before the user sees it. No cross-channel task listing.
- [ ] Implement the Discord handoff confirmation screen and task-only reviewer view; no credentials in URLs or task data in analytics.

```tsx
it("keeps stop pending until Core confirms the broker stopped", async () => {
  render(<BrowserTaskCard task={runningTask} onControl={requestStop} />);
  await user.click(screen.getByRole("button", { name: "Stop" }));
  expect(await screen.findByText("Stopping…")).toBeVisible();
  expect(screen.queryByText("Stopped")).not.toBeInTheDocument();
});
```

**Check:** Client `npm test -- src/features/browser-tasks src/features/phone-chat`; `npx tsc --noEmit`; repository client build. Use mocked responses for component tests, then staging/physical-device tests in Task 9. Verify keyboard review controls and narrow Android/iOS layouts.

### Task 8 — Discord browser commands

**Files:** Vision `integrations/discord_phone_bot.py`, new `integrations/discord_browser_commands.py`, `tests/test_discord_browser_commands.py`, existing `tests/test_discord_phone_bot.py`; Core bridge endpoints and route tests from Task 3.

**Consumes:** Durable Core tasks, active link generation and server browser sessions. **Produces:** DM-only `/browser start`, `/browser status`, `/browser stop`, `/browser clear` (terminal acknowledgment); `/newchat` also ends the Discord browser session.

- [ ] Add the command group using existing Discord.py command registration/interaction patterns. Keep ordinary `/ask`, `/context` and Hub `/status` behavior unchanged.
- [ ] Derive the Discord actor from the interaction. Core derives owner, link generation, current browser session and target laptop. Reject identity fields supplied in the goal.
- [ ] Submit public-research requests through Core, never directly to the local broker. Use stable UUID mapping of Discord interaction IDs so redelivery cannot create a duplicate task.
- [ ] Return short task receipts/status, suppress mentions, and use a task-specific PWA handoff for detailed output/review. Do not approve actions from an emoji, free-text “yes,” or a guild message.
- [ ] End browser sessions on `/newchat`; the source Discord link can stay paired. New tasks get a new server browser session; old status/approvals are no longer accessible.
- [ ] Test forbidden guild context, unlinked/relinked identity, duplicate interaction, stale-session status, busy/offline laptop and a Core network failure. Bot hosting remains best-effort on the configured host.

```python
async def test_discord_start_uses_core_queue_not_local_executor(bot, core, interaction):
    await bot.handle_browser_start(interaction, "Read the official docs")
    await bot.handle_browser_start(interaction, "Read the official docs")
    assert core.distinct_task_ids == 1
    assert bot.local_browser_submit_count == 0
```

**Check:** Vision `python -m pytest tests/test_discord_browser_commands.py tests/test_discord_phone_bot.py tests/test_phone_handler.py -q`; Core bridge authorization tests. Sync slash commands only during the authorized staging rollout, not at module import or ordinary test startup.

### Task 9 — Staging proof, operations and release gates

**Files:** Vision `docs/browser-automation.md`, new `docs/browser-remote-operations.md`, `docs/audits/2026-09-28-browser-b4-acceptance.md`; Core isolated integration tests and environment examples without secret values.

- [ ] Run isolated PostgreSQL races for claim/renew/cancel/approve/revoke/relink. Assert actual database rows and dispatch counts, not only HTTP status codes.
- [ ] Run controlled Chromium broker crashes at dispatch boundaries with a recording fixture server. Confirm no automatic repeat after Core or broker restart; render uncertain effects as `unknown`.
- [ ] Deploy only to explicitly selected isolated staging after local gates. Record the actual Core URL, PWA URL, database branch/schema, worker laptop ID and exact tested revisions in the audit. Previous pasted smoke-test claims are not fresh B4 evidence.
- [ ] Demonstrate PWA start/status/pause/resume/stop/result; selected-origin approval/rejection/expiry; logout/unlink during review; laptop sleep/wake; Core outage; stale heartbeat; hidden-tab return; repeated send tap; erased-result refresh.
- [ ] Demonstrate Discord start/status/stop and task-specific PWA handoff while keeping PWA and Discord sessions separate. Confirm `/status` still answers the Hub question.
- [ ] Test on an actual Android or iOS phone first; record the tested device/browser. Do not claim the untested platform passed. Check installed-PWA resume behavior separately from desktop responsive emulation.
- [ ] Document credential provisioning via the existing secret store/environment, dedicated worker identity, heartbeat meaning, all limits, scheduled cleanup operation, recovery procedure and disable procedure. Document that the configured laptop bot cannot receive Discord commands while its host sleeps.
- [ ] Record each criterion as pass/fail/unavailable with commands/evidence IDs. Leave unmet live-provider, site-account or device gates open; no unqualified “B4 complete” from mocked tests.

**Release order:** PWA public research -> PWA individually accepted reviewed workflows -> Discord. B4-A can be built while B3 account acceptance remains open. B4-B cannot be enabled against real accounts until those B3 workflow gates and broker crash tests pass. B4-C public research does not require enabling B4-B writes.

## 10. Coverage and exit criteria

| Roadmap B4 requirement | Implementation / proof |
|---|---|
| Explicit remote capability and source binding | Tasks 1, 3, 5; local-v1 bypass and cross-session rejection tests |
| Durable IDs, fencing, queue TTL, session cleanup | Tasks 2, 3, 5; real PostgreSQL races, erased-payload assertions |
| Outbound laptop execution | Tasks 4, 8; no inbound browser endpoint or remote CDP configuration |
| Exact remote decisions and just-before-dispatch recheck | Tasks 5, 6; broker-side revocation/timeout/stale-target tests |
| Claim/network loss and uncertain-write reconciliation | Tasks 4, 5, 9; real broker/fixture-site crash tests |
| PWA first, Discord second, separate sessions | Tasks 7, 8; task-specific cross-channel handoff tests |
| Honest laptop offline status | Tasks 4, 7, 9; sleep/wake and stale heartbeat checks |

B4 is accepted only when Core database races, actual broker execution, staging identity boundaries, and at least one physical-phone flow pass. Report local implementation completion separately from rollout readiness. Site-specific write acceptance is scoped to the tested sites/actions, never all websites.

## 11. Rollback and next implementation slice

Disable remote writes first; this prevents new approval/dispatch while keeping status/stop available. Disable remote starts next, stop/resolve active tasks, and run payload cleanup. Preserve content-free uncertain-action evidence for its bounded retention. The additive database objects can remain dormant; do not drop live tables to roll back a feature flag. Existing local browser and ordinary phone chat remain available under their own settings.

The first implementation slice is **Tasks 1–4 plus Task 5's read-only guard, then Task 7's research UI**. Finish its PostgreSQL and staging proof before enabling the reviewed-action slice. This keeps the initial deliverable useful: send a research task from the phone and follow the visible laptop browser, with reliable stop and honest offline behavior.
