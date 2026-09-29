# Phone Chat PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first safe phone-chat channel: an authenticated Ascend Hub PWA backed by Core's durable queue and Vision's outbound worker, with isolated temporary sessions and approved-memory-only persistence.

**Architecture:** Core remains the public HTTPS boundary and owns the configured single-owner binding, browser-device registry, durable message state, and PWA UI. Vision polls Core outbound, validates each job, and routes it through one `PhoneMessageHandler` into the existing `AssistantService`; phone turns are held in RAM by `(owner_id, channel, session_id)`. This plan excludes Discord; the Discord plan depends on this one.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, Prisma Python client/PostgreSQL, Next.js 16/React 19, TypeScript, pytest, Vitest. Add no dependency unless an implementation task demonstrates a concrete need.

**Spec:** `docs/superpowers/specs/2026-09-26-phone-chat-dual-channel-design.md`

## Global Constraints

- V1 is single-owner; fail closed unless the authenticated PWA account matches the explicitly provisioned Core phone-owner binding.
- Use separate Core credentials for browser authentication and Vision worker queue access; the worker credential may only claim, renew, and complete phone jobs.
- Vision makes outbound HTTPS requests only; do not expose an inbound Vision port.
- Keep PWA/phone transcripts in browser `sessionStorage` and Vision RAM only; Core request/reply bodies exist only for delivery and are removed on acknowledgement or expiry.
- Temporary Vision context is keyed by `(owner_id, channel, session_id)`, bounded by the existing budgets, and expires after 60 minutes idle. Restart means a fresh context.
- Pending queue work expires 24 hours after enqueue; attempts are capped at 3; results are idempotent and a job cannot create duplicate visible replies.
- Limit user text to 4,000 characters. Do not permit Core mutations, device control, shell/code, arbitrary skills, MCP, voice, attachments, or push notifications.
- Phone may read approved memories and propose a memory for dashboard approval, but may not approve, edit, or delete memories. Block existing assistant commands that perform those actions.
- Never log prompts, replies, pairing codes, or credentials. Core currently has no migration history and documents `prisma db push`; validate/generate locally, but do not run `db push` against any environment until a provisioned non-production database is explicitly configured.
- Preserve all pre-existing dirty checkouts. Execute Core and Vision work in dedicated worktrees and reconcile the reviewed assistant/status candidate before editing.
- Before editing the Core Next.js client, read the version-matched Next.js agent docs under `client/node_modules/next/dist/docs/` as required by `client/AGENTS.md`.

---

## File Map

| Repository | File | Responsibility |
|---|---|---|
| Vision | `ascend-vision/assistant/service.py` | Accept an optional isolated session key and maintain bounded/expiring phone turns without mixing them with legacy local chat. |
| Vision | `ascend-vision/assistant/phone_handler.py` | Validate channel/owner/session/text; block unsupported memory commands; adapt to `AssistantService`. |
| Vision | `ascend-vision/assistant/session_store.py` | RAM-only keyed session turns, idle expiration, clear-one-session and context-budget enforcement. |
| Vision | `ascend-vision/integrations/phone_worker.py` | Outbound Core polling, lease renewal, expiry checks, retry cap, idempotent result submission. |
| Vision | `ascend-vision/config.py` | Optional phone worker URL/token/poll settings, disabled unless configured. |
| Vision | `ascend-vision/tests/test_assistant_phone_sessions.py`, `test_phone_handler.py`, `test_phone_worker.py` | Session isolation, policy, and worker contract tests. |
| Core | `server/prisma/schema.prisma` | Phone device, job, and minimal tombstone models with owner/device/session scope and indexed expiry/status. |
| Core | `server/schemas/phone_chat.py` | Strict request and response DTOs for PWA and worker routes. |
| Core | `server/services/phone_chat_queue.py` | Owner/device checks, bounded queue insertion, atomic claim/lease, idempotent completion, expiry/cleanup. |
| Core | `server/services/phone_chat_repository.py` | Repository contract, deterministic in-memory test repository, and Prisma/PostgreSQL production adapter. |
| Core | `server/routers/phone_chat.py` | Authenticated PWA routes and dedicated worker routes; no direct Vision ingress. |
| Core | `server/main.py` | Register the phone router. |
| Core | `server/tests/test_phone_chat.py`, `test_phone_chat_worker_auth.py` | Owner/device isolation, queue lifecycle, CSRF/rate limits, worker auth. |
| Core client | `client/src/features/phone-chat/**` | Typed API client, session-only transcript, device registration, queue status/reconnect UI. |
| Core client | `client/src/app/(dashboard)/phone-chat/page.tsx` | Authenticated PWA chat route. |
| Core client | `client/src/app/manifest.ts`, `client/public/sw.js` (only if required by the installed Next version) | Install metadata and minimal service-worker registration; do not cache chat data. |
| Core client | `client/src/app/(dashboard)/layout.tsx` or existing navigation component | Add authenticated navigation entry without changing existing auth behavior. |
| Core client | `client/src/features/phone-chat/**/*.test.tsx` | Session persistence, status transitions, logout/new-chat clearing, and API error UX. |

## Cross-repository API Contract

Use these v1 routes; tests must lock the contract before implementation:

- `POST /api/phone-chat/devices` — browser-authenticated; registers the current browser device for the bound owner and returns `{deviceId}`.
- `DELETE /api/phone-chat/devices/{device_id}` — browser-authenticated; revokes only a device owned by the caller, cancels its queued/unclaimed jobs, and prevents further result polling.
- `POST /api/phone-chat/messages` — browser-authenticated with `deviceId`, `messageId` (UUID idempotency key), `sessionId`, and `text`; returns `{messageId,status,expiresAt}`.
- `GET /api/phone-chat/messages/{message_id}?deviceId=...` — browser-authenticated; returns only the caller's active device message state and completed reply.
- `POST /api/phone-chat/messages/{message_id}/ack` — browser-authenticated; acknowledges delivery, removes the stored prompt/reply body, and leaves a body-free 24-hour tombstone.
- `GET /api/phone-chat/events?deviceId=...` — authenticated SSE for that device's state changes, with polling fallback; never streams message text except the completed reply to its owner/device.
- `POST /api/phone-chat/worker/claim` — worker bearer credential; claims one eligible item or returns 204. Core derives owner from configured binding, not request input.
- `POST /api/phone-chat/worker/jobs/{message_id}/start` — worker bearer credential; moves a valid, leased job from `claimed` to `processing`.
- `POST /api/phone-chat/worker/jobs/{message_id}/renew` and `/complete` — same worker credential; renews lease and submits terminal result idempotently.

The job payload is `{messageId, ownerId, deviceId, sessionId, text, expiresAt, attempt, leaseId, leaseExpiresAt}`. Worker results are `{status: "completed"|"failed", reply?: string, errorCode?: string}`. Reject unknown fields, oversized text, invalid IDs, expired jobs, stale leases, owner mismatches, and revoked devices.

## Tasks

### Task 1: Lock the Vision phone-session and policy contracts

**Files:**
- Create: `ascend-vision/tests/test_assistant_phone_sessions.py`
- Create: `ascend-vision/tests/test_phone_handler.py`
- Modify later: `ascend-vision/assistant/service.py`

**Interfaces:**
- Produces `PhoneChannel = Literal["phone_pwa", "discord_dm"]`, `SessionKey = tuple[str, PhoneChannel, str]` with `(owner_id, channel, session_id)`, and handler signature `PhoneMessageHandler.handle(owner_id: str, channel: PhoneChannel, session_id: str, text: str) -> AssistantReply`. The PWA release enables only `phone_pwa`; Discord remains unavailable until its follow-on plan ships.
- `AssistantService.respond(..., *, session_key: SessionKey | None = None)` uses the existing non-phone context only when `session_key is None`; phone session data never enters that default context.

- [ ] **Step 1: Add red session-isolation tests.** Use one `RecordingGenerator` and one `AssistantService`. Send `hello` and `follow up` through PWA session A, then send `new topic` through session B and a local call with no session key. Assert only A's follow-up generator payload contains A's prior turn. Assert a clear operation for A does not clear B.
- [ ] **Step 2: Add red handler-policy tests.** Assert the handler rejects unknown channels, empty text, text over 4,000 characters, malformed IDs, and `forget ...`, memory correction/edit, or approval commands before calling `AssistantService`. Assert `remember that ...`, approved-memory recall, ordinary chat, and Hub status reach the service and return its exact typed reply.
- [ ] **Step 3: Run focused tests and observe expected failures.** From `ascend-vision/`, run `.venv/Scripts/python.exe -m pytest tests/test_assistant_phone_sessions.py tests/test_phone_handler.py -q`; expected failures identify the missing session-key API and handler.
- [ ] **Step 4: Commit the contract tests.** Stage only those two tests and commit `test(vision): define phone session and policy contracts`.

### Task 2: Implement isolated RAM session contexts and the handler

**Files:**
- Create: `ascend-vision/assistant/session_store.py`
- Create: `ascend-vision/assistant/phone_handler.py`
- Modify: `ascend-vision/assistant/service.py`, `ascend-vision/assistant/__init__.py`
- Test: `ascend-vision/tests/test_assistant_phone_sessions.py`, `test_phone_handler.py`

**Interfaces:**
- `SessionContextStore.get(key, now=None) -> tuple[tuple[tuple[str,str],...], bool]`, `record(key, user_text, assistant_text, now=None) -> None`, `clear(key) -> None`, `expire(now=None) -> int`.
- `PhoneMessageHandler.handle(owner_id, channel, session_id, text) -> AssistantReply`; `clear_session(owner_id, channel, session_id) -> None`.

- [ ] **Step 1: Implement the failing session store tests.** Cover key isolation, 60-minute idle expiry, 12-turn maximum, 3,000-byte context budget, clear-one-key behavior, and no disk writes. Inject a clock value rather than sleeping.
- [ ] **Step 2: Run the session-store tests to verify red.** Run `.venv/Scripts/python.exe -m pytest tests/test_assistant_phone_sessions.py -q` from `ascend-vision/`; expected failure is the missing `SessionContextStore`.
- [ ] **Step 3: Implement `SessionContextStore`.** Use an in-memory mapping of session key to last-activity timestamp and bounded deque; expire entries at `now - last_activity >= 3600`; prune oldest turns until their UTF-8 JSON encoding is at most 3,000 bytes. Do not serialize to SQLite or files.
- [ ] **Step 4: Run session tests to green.** Run the same focused pytest command and expect every isolation, expiry, and budget assertion to pass.
- [ ] **Step 5: Add optional session context to `AssistantService`.** In `respond`, read and append history only for the supplied session key; maintain the existing legacy default behavior for `None`; add `clear_session(session_key)` and ensure status/memory deterministic replies do not accidentally merge different phone sessions. Inject the store to keep tests deterministic.
- [ ] **Step 6: Implement the phone boundary.** Enforce single-owner ID match against configured owner, channel allowlist, UUID-like bounded session ID, 4,000-character text limit, and memory-command policy. Keep explicit pending proposal and approved-memory recall supported; block every phone request that would approve, edit, delete, or globally clear memory/proposals.
- [ ] **Step 7: Run assistant regression tests.** Run `.venv/Scripts/python.exe -m pytest tests/test_assistant_phone_sessions.py tests/test_phone_handler.py tests/test_assistant_service.py tests/test_assistant_session_memory.py tests/test_assistant_memory.py tests/test_hub_status.py -q`; expect all existing and new tests to pass.
- [ ] **Step 8: Commit the Vision handler and session store.** Stage only the listed assistant modules and tests; commit `feat(vision): isolate phone assistant sessions`.

### Task 3: Add the Core owner/device and durable queue schema

**Files:**
- Modify: `D:/ascend-core/server/prisma/schema.prisma`
- Create: `D:/ascend-core/server/schemas/phone_chat.py`
- Create: `D:/ascend-core/server/tests/test_phone_chat.py`

**Interfaces:**
- `PhoneChatDevice`: `id`, `ownerId`, `createdAt`, `revokedAt`.
- `PhoneChatJob`: `messageId` (unique), `ownerId`, `deviceId`, `sessionId`, `text`, `status`, `attempt`, `leaseId`, `leaseExpiresAt`, `expiresAt`, `reply`, `errorCode`, `createdAt`, `completedAt`, `acknowledgedAt`.
- `PhoneChatTombstone`: message ID, owner/device scope, terminal status, expiry; no prompt/reply content.
- Tests use `InMemoryPhoneChatRepository`, matching Core's existing `InMemoryStatusRepository` / `PostgresStatusRepository` split; no shared test database fixture exists in this repository.

- [ ] **Step 1: Add red model/DTO contract tests.** Test strict input validation for message IDs, owner/device/session IDs, body lengths, queue-state enum, worker result shape, and rejection of extra fields. Use `importlib.util.find_spec` inside the test to fail with an assertion while the schema module is absent, then import the Pydantic models after the module is added.
- [ ] **Step 2: Run the tests to verify red.** From `D:/ascend-core/server`, run `python -m pytest tests/test_phone_chat.py -q`; expected assertion failures report that the phone-chat schemas/models have not been implemented, not a collection/import error.
- [ ] **Step 3: Add Prisma models and indexes.** Add cascading device/job relations and indexes for `(ownerId, deviceId, status, createdAt)`, `(status, leaseExpiresAt, expiresAt)`, and `expiresAt`; store only the fields in the interface. This repository has no migration history and documents `prisma db push`; do not invent a migration directory or run `db push` in this task.
- [ ] **Step 4: Define strict Pydantic DTOs.** Reject extra fields and enforce IDs, enum states, string length, and result-shape constraints; worker DTOs must not accept owner ID for claim or completion authorization.
- [ ] **Step 5: Run schema validation and tests.** From the Core worktree's `server/`, set both `DATABASE_URL` and `DATABASE_URL_UNPOOLED` to dummy localhost PostgreSQL URLs for validation only, then run the existing Core venv's `python -m prisma validate`; generate the Python client using a worktree-local `.venv` so the main checkout's generated client is not overwritten; run `python -m pytest tests/test_phone_chat.py -q`. Expect schema and DTO tests to pass; never run `prisma db push` here.
- [ ] **Step 6: Commit schema and DTO contract.** Stage only the schema, DTO, and tests; commit `feat(core): define phone chat queue contract`.

### Task 4: Implement Core queue operations and protected API routes

**Files:**
- Create: `D:/ascend-core/server/services/phone_chat_queue.py`
- Create: `D:/ascend-core/server/services/phone_chat_repository.py`
- Create: `D:/ascend-core/server/routers/phone_chat.py`
- Modify: `D:/ascend-core/server/main.py`, `D:/ascend-core/server/auth_utils.py` only if a dedicated token-purpose helper is required
- Test: `D:/ascend-core/server/tests/test_phone_chat.py`, `test_phone_chat_worker_auth.py`

**Interfaces:**
- `PhoneChatRepository` defines async device/job reads and writes; `InMemoryPhoneChatRepository` implements it for deterministic unit tests; `PostgresPhoneChatRepository` implements it using the generated Prisma client.
- `register_device(owner_id) -> PhoneChatDevice`; `revoke_device(owner_id, device_id) -> bool`.
- `enqueue(owner_id, device_id, message_id, session_id, text, now) -> PhoneChatJob`; same ID/same payload returns existing state; same ID/different payload returns 409.
- `claim(worker_credential, now) -> WorkerJob | None`; `start(worker_credential, message_id, lease_id, now) -> bool`; `renew(worker_credential, message_id, lease_id, now) -> bool`; `complete(worker_credential, message_id, lease_id, result, now) -> PhoneChatJob`.
- `get_for_device(owner_id, device_id, message_id) -> PhoneChatJob | PhoneChatTombstone`; never returns another device's body.
- `acknowledge(owner_id, device_id, message_id, now) -> PhoneChatTombstone`; deletes message/reply text and creates a body-free status tombstone.

- [ ] **Step 1: Add red queue/repository and API-security tests.** Exercise queue behavior against `InMemoryPhoneChatRepository`: one-owner mismatch, revoked device, 4,000-character boundary, idempotent replay, duplicate ID with changed payload, expiry, active lease exclusion, renew/start, three-attempt cap, acknowledgement deletion, tombstone retention, and device-scoped reads. In a FastAPI `TestClient`, also verify unauthenticated calls return 401; non-owner account returns 403; worker endpoints reject browser auth; PWA endpoints reject worker auth; invalid origin/CSRF and rate-limit failures reject writes; invalid worker secret rejects claim/complete; and routes never accept caller-selected owner ID.
- [ ] **Step 2: Implement repository operations.** Add matching methods to `InMemoryPhoneChatRepository` and `PostgresPhoneChatRepository`. The Postgres adapter uses conditional `update_many` predicates and transactions for atomic claims/completion; the in-memory adapter mirrors the same observable contract for tests.
- [ ] **Step 3: Implement queue service operations.** Claim oldest unexpired eligible job; increment attempt on claim; lease expires after 60 seconds and can be renewed while active. When a lease expires, requeue the job only if fewer than three attempts have been made; otherwise mark it failed. `start` changes `claimed` to `processing` only for the current lease. Completion compares message ID and current lease, is idempotent for an already-terminal identical result, and rejects stale conflicting completion.
- [ ] **Step 4: Add expiry and retention cleanup.** Expire queued/claimed/processing jobs after 24 hours; delete request/reply content on explicit acknowledgement or 24 hours after completion; retain only a body-free tombstone for 24 hours; cancel queued/unclaimed device jobs on logout/device revocation and discard in-flight results where possible.
- [ ] **Step 5: Implement the PWA API routes.** Use `get_current_user`; compare the account to explicit `ASCEND_PHONE_OWNER_ID`/Core owner binding and fail closed if unconfigured; validate active device for each call; apply existing CSRF/origin and rate-limiting conventions. Implement SSE state events with an authenticated device filter and no cached response bodies. `ack` deletes the body but keeps the minimal tombstone.
- [ ] **Step 6: Implement worker-only routes.** Compare a dedicated `ASCEND_PHONE_WORKER_TOKEN` using constant-time comparison; never use a browser JWT for worker access. Derive owner and device from the stored job, and return only the contract payload.
- [ ] **Step 7: Run Core focused checks.** From the Core worktree's `server/`, run `python -m pytest tests/test_phone_chat.py tests/test_phone_chat_worker_auth.py -q`, validate schema using dummy localhost database URLs, and regenerate the Python Prisma client with the worktree-local `.venv`; expect queue lifecycle, ownership, CSRF/rate, and credential-isolation tests to pass. Do not run `prisma db push` until a non-production database is provisioned.
- [ ] **Step 8: Commit Core queue API.** Stage only repository/service, router, auth helper if changed, main router registration, DTO/model, and tests; commit `feat(core): add authenticated phone chat queue`.

### Task 5: Implement Vision's outbound queue worker

**Files:**
- Create: `ascend-vision/integrations/phone_worker.py`
- Modify: `ascend-vision/config.py`, `ascend-vision/main.py`, `ascend-vision/requirements.txt` only if the existing HTTP client cannot meet the contract
- Create: `ascend-vision/tests/test_phone_worker.py`

**Interfaces:**
- `CorePhoneClient.claim() -> WorkerJob | None`, `renew(job) -> bool`, `complete(job, result) -> None`.
- `WorkerJob` has `message_id`, `owner_id`, `device_id`, `session_id`, `text`, `expires_at`, `attempt`, `lease_id`, and `lease_expires_at`.
- `PhoneQueueWorker.run_once() -> bool` (true if a job was handled); `start()`, `stop(timeout)`.

- [ ] **Step 1: Write red mocked HTTP tests.** Verify claim sends only worker bearer auth; no-work returns without invoking the assistant; expiry is checked before generation; lease is renewed; a stale/revoked job is discarded; `PhoneMessageHandler` receives the `phone_pwa` key; failed replies return only a generic error code; retries stop at attempt 3; malformed Core payloads fail closed; and logs contain no chat text or credentials.
- [ ] **Step 2: Run tests to verify red.** Run `.venv/Scripts/python.exe -m pytest tests/test_phone_worker.py -q` from Vision; expected failure is the missing worker.
- [ ] **Step 3: Implement the worker with the existing HTTP dependency.** Poll outbound HTTPS only; set connect/read timeouts; renew lease before it expires; mark a lease `processing` before invoking the assistant; check 24-hour expiry and attempt count; send idempotent completion; never log request/reply bodies. If no HTTP dependency already exists, propose and pin one only after checking the lockfile and project conventions.
- [ ] **Step 4: Wire optional configuration.** Keep the worker disabled when URL/token are absent; reject partial configuration; start/stop it through existing resource lifecycle. Do not create an inbound listener.
- [ ] **Step 5: Run Vision focused tests.** Run `.venv/Scripts/python.exe -m pytest tests/test_phone_worker.py tests/test_phone_handler.py tests/test_assistant_phone_sessions.py -q`; expect all mocked worker and session tests to pass.
- [ ] **Step 6: Commit Vision worker.** Stage only worker/config/runtime/tests; commit `feat(vision): poll Core for phone chat jobs`.

### Task 6: Build the authenticated PWA chat surface

**Files:**
- Create: `D:/ascend-core/client/src/features/phone-chat/api.ts`, `types.ts`, `usePhoneChat.ts`, `PhoneChatPanel.tsx`, tests.
- Create: `D:/ascend-core/client/src/app/(dashboard)/phone-chat/page.tsx` and `client/src/app/manifest.ts`.
- Modify: authenticated dashboard navigation and root layout/service-worker registration only after reading the installed Next.js docs.

**Interfaces:**
- `PhoneChatApi.registerDevice()`, `enqueue({deviceId,messageId,sessionId,text})`, `getMessage(deviceId,messageId)`, `subscribe(deviceId,onState)`, `revokeDevice(deviceId)`.
- `usePhoneChat` owns one per-tab `sessionStorage` transcript and current `sessionId`; it never writes chat content to localStorage, IndexedDB, service-worker cache, or analytics.

- [ ] **Step 1: Read local Next.js docs and add red component tests.** Read `client/node_modules/next/dist/docs/` docs relevant to app routes, metadata/manifest, and service workers. Test that initial history loads only from sessionStorage; submit inserts a local queued item; server state transitions replace status; refresh restores only this tab's transcript; New Chat creates a new session ID and clears visible transcript; logout clears transcript and cancels pending work; oversized/blank text is rejected; and failures never invent assistant text.
- [ ] **Step 2: Run the frontend tests to verify red.** From `D:/ascend-core/client`, run `npm test -- src/features/phone-chat`; expected failure is missing feature files.
- [ ] **Step 3: Implement API/types/hook.** Use the exact Core route DTOs; send same-origin credentials and idempotent message IDs; use EventSource only where cookie auth works, otherwise authenticated polling with exponential backoff capped at 10 seconds; close streams/timers on unmount/logout.
- [ ] **Step 4: Implement accessible chat UI and route.** Use existing design tokens/components; expose queued, processing, completed, failed, and expired statuses as text; keep New Chat and device revoke explicit; do not add memory management controls or arbitrary tool UI.
- [ ] **Step 5: Add PWA metadata and offline boundary.** Register only the minimal service worker required for installability; do not cache authenticated pages, API results, or chat bodies. Show offline submit failure clearly; queued messages remain in Core, not an offline browser outbox.
- [ ] **Step 6: Run frontend tests, lint, and build.** Run `npm test -- src/features/phone-chat`, `npm run lint`, and `npm run build`; expect tests/lint/build to pass without changes to unrelated features.
- [ ] **Step 7: Commit the PWA slice.** Stage only the phone-chat feature, route, manifest, navigation/layout changes, and tests; commit `feat(core): add installable phone chat PWA`.

### Task 7: End-to-end mocked contract and rollout readiness

**Files:**
- Create or modify: Vision/Core contract tests and `docs/phone-chat-operations.md` only if operator steps are needed.

**Interfaces:**
- Contract test sends a browser-authenticated message through Core's queue, claims it using mocked Vision worker HTTP, invokes a fake `PhoneMessageHandler`, completes it, and reads the reply through the original device.

- [ ] **Step 1: Add one end-to-end mocked contract test.** Cover queued → claimed → processing → completed → acknowledged, with duplicate claim/completion/acknowledgement and an expired job; assert one visible answer, no duplicate response, no cross-device visibility, and no prompt in logs/tombstones.
- [ ] **Step 2: Run all relevant tests.** Run Vision tests `.venv/Scripts/python.exe -m pytest tests/test_phone_worker.py tests/test_phone_handler.py tests/test_assistant_phone_sessions.py tests/test_assistant_memory.py tests/test_hub_status.py -q`; run Core `python -m pytest tests/test_phone_chat.py tests/test_phone_chat_worker_auth.py -q`; run PWA `npm test -- --run src/features/phone-chat` and `npm run build`.
- [ ] **Step 3: Review security and retention.** Confirm owner binding is provisioned out of band, worker secret is scoped and absent from browser bundles, auth/CSRF/rate limits are on, logs are redacted, sessionStorage is per-tab, and expiry cleanup deletes body content as specified.
- [ ] **Step 4: Keep live rollout gated.** Do not apply migrations or deploy until a staging Core URL and scoped credentials are provisioned; then run staging install/login/device revoke, Vision-offline queue/reconnect, 24-hour expiry, and restart/session-reset checks. Production deployment remains a separate operator approval.

## Spec Coverage Review

- Session isolation, 60-minute expiry, restart reset, memory recall/proposal restrictions, status no-guess behavior: Tasks 1–2 and 5.
- Core owner binding, device revocation, durable queue, leases, retries, idempotency, body retention, tombstones, CSRF/rate limits: Tasks 3–4.
- PWA authentication, session-only transcript, SSE/polling fallback, reconnect, installability, failure UX: Task 6.
- Outbound-only Vision network behavior, no phone actions/MCP/skills: Tasks 2 and 5.
- Mocked contract and staging gate: Task 7.
