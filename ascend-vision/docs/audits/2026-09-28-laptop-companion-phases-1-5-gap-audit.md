# Laptop and Phone Companion — Phases 1–5 Gap Audit

**Audit date:** 2026-09-28, Asia/Shanghai  
**Scope:** Compare the supplied Phase 1–5 checklist and architecture requirements with the current Vision/Core working copies.  
**Overall verdict:** Substantial software exists, but the evidence does **not** support “Phases 1–5 are complete.”  
**Implementation status:** The initial audit was read-only. Follow-up software changes listed in §12 were subsequently authorized and made in the named dirty working copies; no credentials, deployment, production database, or external service were changed.

## 1. Evidence and status rules

Requirements came from the two attachments supplied for the preceding audit:

- Phase checklist: attachment `2e71f5f7-d5ab-4c65-b130-22b66ed946e6`.
- Product/architecture specification: attachment `c7ada90c-b4d4-4259-ac1f-4044a7b5d25c`.
- Repository reference: [original roadmap](D:/ascend-vision/ascend-vision/docs/superpowers/plans/2026-09-27-laptop-companion-roadmap.md).

The audit inspected these existing, dirty worktrees without discarding their changes:

| Project | Working copy | Branch | HEAD at audit |
|---|---|---|---|
| Vision | `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision` | `codex/laptop-companion-context` | `956470032222d9179fcdac465e3aa264a09c6fa4` |
| Core | `D:/ascend-core/.worktrees/phone-chat-core` | `codex/phone-chat-core` | `d38846002a0f36fe1659c09fd3b2abeb53cb9648` |

HEAD alone does not describe the inspected implementation: many companion files are modified or untracked. This audit is evidence about those working copies, not a certification of a merged branch, staging deployment, or production release.

Status meanings:

- **Implemented:** Relevant software was found; this does not imply every integration or device gate passed.
- **Missing:** The requested behavior is absent or explicitly deferred in the inspected implementation.
- **Scope deviation:** A different, smaller behavior was built; it needs explicit acceptance or the original requirement must be implemented.
- **Unverified:** Code exists or the requirement concerns operation, but sufficient acceptance evidence was not obtained.
- **Failed:** A fresh automated check failed; its actual cause is stated separately.

Tests below cover selected modules, not the whole application. Hardware, PostgreSQL, provider, and pilot gates cannot be replaced by fake-adapter tests.

## 2. Phase-level verdict

| Phase | Present foundation | Outstanding completion gates | Verdict |
|---|---|---|---|
| 1 — Current Context V1 | Typed expiring local state; calibrated desk region; frame-quality guard; IPC; dashboard controls | Physical lifecycle checks; performance baseline; limits of occlusion detection | Partial |
| 2 — Grounded conversation | Bounded context injection; mission reader; deterministic agent answers; local-only screen inspection | Actual shelf integration; token-cost measurement; factual tone-invariance examples | Mostly implemented; verification incomplete |
| 3 — Proactive companion | Break/desk rules; explicit operation-scoped agent-input rule; bounded shadow/explanation view; durable duplicate claims; shared speech arbitration | Three shadow sessions; producer/deployment integration; real-device replay; personal pilot | Software substantially implemented; acceptance partial |
| 4 — Phone context/voice/notifications | Context publication/read APIs; PWA card; linked Discord reads; opt-in Gemini audio contract; independently opted-in PWA push and Discord DM software pipeline | Real PostgreSQL gate; provider/staging and physical-device evidence | Partial |
| 5 — Daily reflection | Opt-in local aggregates; deterministic totals; coverage; corrections; export/delete/retention; owner-authorized Core mission history contract/client | Real Core integration and normal-user storage workflow checks | Software substantially implemented; acceptance partial |

## 3. Phase 1 gaps — reliable current context

### P1-01 — Desk-region calibration exists; physical setup accuracy is unverified

**Status:** Implemented in the inspected working copy; physical calibration accuracy remains unverified. **Priority:** High.

The roadmap requires an explicit preview/setup step and a calibrated desk region. The main loop now computes whether each detected face's landmark center falls within the calibrated normalized region, then passes only that result to the presence collector. If the region is missing or calibration is in progress, the collector marks webcam evidence unavailable. A usable-frame quality check prevents very dark, overexposed, or nearly uniform images from accumulating absence dwell. This establishes the software filter; it does not establish physical calibration accuracy or reliable detection of all occlusion conditions.

Evidence: [camera-to-context wiring](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/main.py:891), [calibration preview handler](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/main.py:676), [calibration geometry and persistence](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/integrations/desk_presence.py:16), [dashboard setup control](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/templates/dashboard.html:118).

**Impact:** A face detected elsewhere in the frame is not proof that the owner is seated at the calibrated desk. Face absence is not automatically proof that the owner left.

The owner can start setup from the local dashboard, drag a rectangle on the existing live camera preview, and persist only the normalized rectangle beside the configured local database. Uncalibrated context reports webcam presence as unavailable. The preview draws the saved rectangle. The feature describes detected faces within that region and makes no identity claim.

**Remaining acceptance:** Complete physical calibration scenarios, including camera/window scaling, and confirm the selected rectangle after restarting Vision.

### P1-02 — Occlusion and physical lifecycle acceptance remain open

**Status:** Partly addressed in software; sustained occlusion and physical lifecycle remain unverified. **Priority:** High.

Very dark, blown-out, and nearly uniform sampled frames now mark camera evidence unavailable; no-face results only accrue absence dwell after a valid calibrated frame. This simple image-quality gate cannot reliably identify every obstruction, blur, or unusual lighting condition. The implementation therefore still needs the physical checks below.

**Close when:** Record outcomes for chair movement, lighting changes, empty desk, blocked lens, another person entering, sustained occlusion, camera unplug/reconnect, laptop lock, sleep/resume, and process restart. Camera loss must not become confirmed absence; another person must not become an identity assertion. Desktop observations should continue where available.

### P1-03 — Operational and overhead proof is not complete

**Status:** Unverified. **Priority:** Medium.

Reducer/IPC/dashboard tests exist and pass in the selected run. This audit did not establish the Phase 0 performance baseline, collector overhead, real Windows owner isolation, shutdown under sensor load, or end-to-end offline UI controls.

**Close when:** Measure added CPU/memory and frame-loop latency against a recorded baseline; verify responsive pause/shutdown, no orphaned loops, same snapshot revision across local consumers, and network-disconnected correction/snooze behavior.

## 4. Phase 2 gaps — grounded answers

### P2-01 — Remote-screen regression wording is aligned and verified

**Status:** Resolved in the working copy. **Priority:** Medium; regression coverage retained.

`test_screen_intent_does_not_capture_for_remote_channel` expects “only available in local vision chat.” The assistant now returns “Screen inspection can only be started locally in Vision; screen contents are never shared to phone or Discord.”

Evidence: [assertion](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/tests/test_context_screen_policy.py:107), [response constant](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/assistant/service.py:32).

The original failure was a wording mismatch. The assertion now matches the user-facing response and continues to use a capture stub that raises if invoked. This is not evidence that remote screen capture was enabled.

**Resolved:** Updated the assertion to the actual local-only / never-shared wording and reran it with the broader companion test gate.

### P2-02 — Shelf, tone, and context-cost acceptance evidence is incomplete

**Status:** Unverified. **Priority:** Medium.

Existing tests exercise bounded packets, mission-reader behavior, prompt grounding, and idle-versus-finished distinctions. They do not by themselves establish:

- Compatibility with the selected live Core shelf schema and authorized runtime credentials, including unavailable/stale/completed cases.
- Direct calm, playful, and strict answers with invariant factual content. A prompt containing those tone names is not an output evaluation.
- Measured token overhead for representative context packets and its effect on latency/cost.

Evidence: [assistant context tests](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/tests/test_assistant_context.py), [status tests](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/tests/test_hub_status.py).

**Close when:** Save sanitized contract-test results against the selected Core build, evaluate the listed answer scenarios in all three tones, and record context-only token/latency measurements. Idle must never substitute for explicit successful completion.

## 5. Phase 3 gaps — proactive behavior

### P3-01 — Agent-needs-input rule is implemented locally; producer integration remains open

**Status:** Software implemented in the Vision branch and current phone-Core branch; cross-build integration remains open. **Priority:** High for Phase 3 completion.

The third rule consumes explicit Core `needs_input` evidence. Core requires a matching active operation ID and matching operation start timestamp, rejects idle or unrelated-operation requests, exposes only a stable hashed operation reference, and clears the request when that operation finishes. The legacy Vision heartbeat endpoint rejects `needs_input`; producers must use `/api/status/events`. Vision accepts only fresh (30-second) `working` evidence, requires the rule's separate opt-in, and persists a local intent claim for 30 days so restart/replay cannot speak twice.

Evidence: [policy](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/assistant/companion_policy.py), [Core contract](D:/ascend-core/.worktrees/status-completion-history/server/schemas/service_status.py), [durable intent claims](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/assistant/intervention_delivery.py).

**Producer integration refresh (2026-09-28):** The Hub working copy has supported Codex CLI and Antigravity CLI lifecycle adapters, but inspection of `scripts/status-adapters/codex-lifecycle.mjs`, `codex-launch.mjs`, and the Antigravity lifecycle mapping found no producer event for `needs_input`. The Codex adapter currently emits lifecycle/progress events only. Official Codex hooks include `PermissionRequest`, a potential explicit source for a privacy-minimal Codex needs-input signal. However, the selected Core `/api/status/events` route only validates `needs_input` against an already-active Core operation; the Hub adapters currently publish aggregate lifecycle status and do not create those Core operation rows. Therefore, wiring the hook alone would still fail the authoritative contract. Current official Antigravity CLI hooks document only `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, and `Stop`; they do not document an approval-request event. Although hook payloads expose a transcript path, transcript scraping is not an acceptable substitute for explicit producer evidence and remains out of scope.

**Fresh producer regression verification:** `node --test tests/phase3/*.test.mjs` in `D:/ascend_hub` passed **67 tests**. This confirms existing lifecycle, privacy allowlist, durability, and retry behavior; it does not cover `PermissionRequest` or Core operation creation, so it does not close the producer gap.

**Software status:** Vision's rule, Core's operation-scoped contract, durable local claims, and separate opt-in are implemented and tested. **Still open:** Extend the producer contract so an authorized lifecycle producer can create and finish its own Core operation records; add a sanitized Codex `PermissionRequest` path tied to that operation; test clearing on progress/completion/interruption/rejection and rejection of stale or unrelated operations. Keep Antigravity `needs_input` unsupported until the producer exposes an explicit supported approval signal. Then integrate against the selected deployed Core build with dedicated credentials. An explicit authorization request for the additional cross-repository Hub producer/Core API change is pending; no such start/finish endpoint or Codex hook has been added. No staging request or producer credential was used here.

Evidence: [Hub Codex lifecycle adapter](D:/ascend_hub/scripts/status-adapters/codex-lifecycle.mjs), [Hub Codex hook configuration](D:/ascend_hub/scripts/status-adapters/codex-launch.mjs), [official Codex hooks](https://developers.openai.com/codex/hooks), [official Antigravity CLI hooks](https://antigravity.google/docs/hooks?tab=cli).

### P3-02 — Shadow mode and explanation view are implemented; human review remains open

**Status:** Implemented in the inspected working copy; the three-session human review remains open. **Priority:** High before enabling proactive rules broadly.

`CompanionRuntime.tick()` results are now retained in a bounded, transient in-memory ring and exposed through the existing owner-restricted IPC to a dashboard explanation panel. Shadow mode evaluates without dispatching speech. The panel includes rule, trigger/reason, evidence source and age, selected channel, mode, and outcome; it excludes messages, prompts, and chat.

Evidence: [tick result](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/assistant/companion_runtime.py:13), [main-loop call](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/main.py:695).

**Software status:** Implemented and unit-tested. **Operational gate:** inspect three representative shadow sessions on the laptop before enabling any spoken rule.

### P3-03 — Software arbitration is covered; device replay and seven-day pilot remain open

**Status:** Local cross-component and main-loop arbitration tests pass. Human/device acceptance is still unverified. **Priority:** High before active rollout.

The delivery controller uses a local 30-day SQLite claim ledger for duplicate suppression across restart. Legacy PHONE/FATIGUE/SLOUCH warning triggers remain distinct policies but use the shared `FeedbackService` queue. An ordinary accepted announcement, feedback reaction, or user response removes queued companion speech and cancels an active companion utterance; companion speech also declines to enqueue when ordinary speech is already pending. This addresses both queue orderings while preserving the ordinary warning/reply. It does not combine trigger policies or prove target-device timing.

Evidence: [main-loop companion-before-frame ordering](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/main.py:825), [shared queue/preemption implementation](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/feedback.py:559), [composed runtime/warning test](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/tests/test_feedback.py:40), [main-loop replay test](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/tests/test_main.py:96).

**Fresh software verification:** `tests/test_feedback.py`, `tests/test_main.py`, `tests/test_companion_runtime.py`, `tests/test_warning_state_machine.py`, `tests/test_ascend_core_integration.py`, and `tests/test_intervention_delivery.py` passed **65 tests**. The main-loop test uses the real run loop, feedback queue, warning state machine, detector event, and a cancellable speaker; it verifies a frame-triggered warning preempts proactive speech. Separate tests prove pending warnings block new companion speech, screen-audit/user/event feedback supersede queued companion speech, and stop/snooze cancels guarded delivery while ordinary speech can resume.

**Still open:** Replay camera/screen-audit/sleep/resume and asynchronous Core narration on the actual laptop, review three representative shadow sessions, then complete the seven-day personal pilot. This local test does not establish physical TTS timing, passive-context CPU cost, human interruption acceptability, producer/deployment integration, or a no-penalty live run.

**Wording follow-up:** Updated to “I haven't detected a face in the calibrated desk area”; it does not claim identity or confirmed absence.

## 6. Phase 4 gaps — phone delivery

### P4-01 — Real PostgreSQL migration/reliability gate has not passed

**Status:** Additive migration artifact added; database execution and rollback remain unverified. **Priority:** High; blocks release enablement.

A PostgreSQL repository and Prisma model exist. Versioned SQL artifacts now create `CompanionContextRecord`, add nullable voice-audio fields to `PhoneChatJob`, and create notification preferences, encrypted push subscriptions/details, durable intents/deliveries, and the shared daily budget. Selected service tests still use in-memory repositories. The migrations have not been applied to isolated PostgreSQL; transaction behavior, concurrency, restart, cleanup failure, and rollback remain unproven.

Evidence: [PostgreSQL adapter](D:/ascend-core/.worktrees/phone-chat-core/server/services/companion_context.py:128), [test repository](D:/ascend-core/.worktrees/phone-chat-core/server/tests/test_companion_context.py:54), [schema](D:/ascend-core/.worktrees/phone-chat-core/server/prisma/schema.prisma:47).

**Close when:** Use an isolated database and document the chosen additive migration method. Verify owner/device uniqueness, concurrent publishers, out-of-order writes, transaction failure, restart, cleanup failure, and old-reader rollback compatibility. Demonstrate logical expiry independently of cleanup. Do not run this against production by default.

### P4-02 — Foreground Gemini audio upload is implemented; acceptance remains open

**Status:** Original upload/transcription path implemented locally; staging/device acceptance remains open. **Priority:** High for Phase 4 completion.

The PWA now records foreground audio with `MediaRecorder`, caps the UI recording at 30 seconds and uploads at 5 MiB, previews the recording before a separate Send voice action, supports discard/cancellation, and keeps a typed fallback. The authenticated Core audio endpoint requires explicit `google-gemini` consent, enforces a 5 MiB streaming body cap and MIME allowlist, stores audio temporarily in the queue for at most the existing 24-hour job TTL, and clears job audio fields on completion, cancellation, expiry, exhausted retries, or device revocation. The outbound Vision worker validates the payload, transcribes inline with Gemini, then sends only the transient transcript to the existing phone session handler. Transcripts are not added to the phone chat record. Voice upload is disabled by default in Vision and Core; enabling it requires both local feature switches and credentials. Database backup/WAL retention is distinct from clearing the active job row.

The selected implementation is the bounded audio-upload/transcription path, not the earlier browser-draft alternative. Browser support, server validation, user consent, queue expiry/cleanup, and transient transcription flow are implemented locally. Cancellable provider requests and backup/WAL erasure are not guaranteed; already-started synchronous Gemini work may finish before the worker observes cancellation, and database backups are outside the active-row cleanup contract.

Evidence: [voice implementation](D:/ascend-core/.worktrees/phone-chat-core/client/src/features/phone-chat/VoiceCaptureButton.tsx:79), [disclosure](D:/ascend-core/.worktrees/phone-chat-core/client/src/features/phone-chat/PhoneChatPanel.tsx:110).

**Software status:** The user selected Gemini transcription. Fresh Core phone queue/worker-auth/status/notification/history contract tests passed **88** using the Core Python 3.11 environment; Vision's fresh full suite passed **801**; and the dependency-complete Core client suite passed **130 tests across 31 files**. Five new `VoiceCaptureButton` component tests cover recording/review, explicit-send gating, discard, denied microphone permission, background-stop, and the 5 MiB cap. They exposed and fixed a race where the recorder's later `onstop` event overwrote the background-stop notice. Core client `npx tsc --noEmit` and targeted ESLint passed. `prisma validate` passed in the earlier focused check. **Still open:** PostgreSQL migration/lifecycle verification, provider-backed transcription, staged configuration, and physical Android/iOS tests. Cancellation revokes the queue lease and prevents delivering the result; an already-started synchronous Gemini request may continue until the provider call returns.

### P4-03 — Opt-in notifications need provider and rollout verification

**Status:** Core/PWA/Vision notification software is implemented in the named working copies; real database/provider/device acceptance remains open. **Priority:** High for Phase 4 release.

Core now has separate off-by-default PWA push and Discord-DM preferences, owner/device-bound encrypted destinations, idempotent expiring intents, atomic claims, one shared owner/day budget, bounded retry state, revocation checks, and authenticated device-bound detail retrieval. Worker claims deliberately omit decrypted notification detail; only the authenticated device-bound PWA detail endpoint can reveal it. The PWA settings use a user click for browser permission and subscription; its service worker displays only “You have a private update.” and carries only an opaque intent ID into the signed-in app. Vision's opt-in outbound worker supports Web Push and a linked-account Discord DM, both with generic content only. Push destinations are HTTPS/provider-domain allowlisted, and provider/private-detail secrets are encrypted at rest by the Core adapter. Vision's separate, opt-in publisher emits only a generic notification for a fresh explicit `agent_needs_input` rule; it excludes task labels and is disabled in shadow mode. It does not publish break/desk prompts or enable remote actions.

Acknowledgment means accepted by the push/Discord API, not delivered to, displayed by, or read on a device. Explicit provider 429 responses are retryable within the bounded attempt policy. Ambiguous provider transport failures are recorded `unknown`; code does not immediately retry the provider send. Core outcome writes retry transient transport/429/5xx failures idempotently (three attempts) without re-sending to the provider. Residual delivery is at-least-once: if Core remains unavailable through those outcome retries and the claim lease expires, the job may be reclaimed, so an occasional duplicate generic notification remains possible. The Discord DM points the user to the signed-in app; full detail remains behind the authenticated PWA API.

Evidence: [Core durable delivery service](D:/ascend-core/.worktrees/phone-chat-core/server/services/companion_notifications.py), [PWA notification settings/detail](D:/ascend-core/.worktrees/phone-chat-core/client/src/features/phone-chat/CompanionNotificationSettings.tsx), [generic service worker](D:/ascend-core/.worktrees/phone-chat-core/client/public/sw.js), [Vision publisher](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/integrations/notification_publisher.py), [Vision delivery worker](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/integrations/notification_worker.py), [context-sharing limitations](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/docs/laptop-context-sharing.md).

**Fresh software verification:** Core notification + phone API/auth tests passed **27**; PWA API/panel/context/notification settings tests previously passed **4 files, 8 tests**; Vision notification worker + publisher tests passed **12**. The worker test verifies a transient Core outcome timeout is retried while the provider send occurs once. These use fakes/in-memory storage; no notification was sent to an external provider.

**Dependency gap resolved during follow-up (2026-09-28):** `requirements.txt` now declares `discord.py>=2.7,<3`, matching the Discord 2.x API used by the optional DM adapter. Installing it into an isolated temporary target exposed 12 existing failures: eight test doubles had an accidentally misplaced `close()` method, and four router fallback assertions were contaminated because the missing-settings CLI path changed the process-wide logger level before returning. The fake Core now implements the adapter's async-close contract; `main()` configures private provider logging only after settings validate, so an invalid invocation has no unrelated global logging side effect. Focused Discord/router tests passed **47**; the complete Vision suite passed **799** with the dependency available. One upstream Google SDK deprecation warning remains non-failing. No credential or provider request was used.

**Provisioning contract (names only; never commit values):** Core requires `ASCEND_COMPANION_NOTIFICATIONS_ENABLED`, `ASCEND_PHONE_OWNER_ID`, `ASCEND_NOTIFICATION_WORKER_TOKEN`, `ASCEND_NOTIFICATION_PUBLISHER_TOKEN`, and `ASCEND_NOTIFICATION_ENCRYPTION_KEY`; PWA push additionally needs `ASCEND_VAPID_PUBLIC_KEY`. Vision publishing requires `ASCEND_NOTIFICATION_PUBLISHER_ENABLED=true`, `ASCEND_PHONE_CORE_URL`, and the dedicated publisher token. Delivery requires `ASCEND_NOTIFICATION_DELIVERY_ENABLED=true`, `ASCEND_PHONE_OWNER_ID`, the separate channel switches `ASCEND_PWA_PUSH_DELIVERY_ENABLED` / `ASCEND_DISCORD_DM_DELIVERY_ENABLED`, the dedicated worker token, and either VAPID private key/subject or the existing Discord bot token. Do not reuse phone-worker, context-publisher, bridge, cron, or browser credentials.

**Still open:** Apply/test migrations on isolated PostgreSQL; provision dedicated Core publisher/worker credentials, encryption and VAPID keys; verify provider rate limits, uncertain receipts, revoked subscriptions/devices/Discord links, restart/sleep, and physical Android/iOS delivery. Confirm lock-screen generic content on each device. All notification flags remain disabled unless separately configured. Staging Core URL/credentials were previously reported unavailable in this environment, and the local Docker executable has no running daemon, so no database migration or staging/provider test was attempted.

### P4-04 — Current mobile/staging validation is incomplete

**Status:** Unverified. **Priority:** High before rollout.

The previously reported text-chat staging smoke test predates this companion context/voice/notification slice. It is not proof of these additions. The four selected client tests do not constitute physical-device coverage.

**Close when:** Validate the selected build against staging with separate publisher credentials and non-production flags. Test wrong-owner/device/link, revoked device/link, stale records, delayed packets, publisher/service restart, laptop sleep, last-seen labels, and separate channel histories. On physical Android and an installed iOS Home Screen PWA, test microphone denial/revocation, calls, backgrounding, network loss, duplicate submit, cancel, logout, and supported fallback. After notifications exist, verify their delivery on both devices too.

## 7. Phase 5 gaps — daily reflection

### P5-01 — Core-confirmed daily mission history is implemented; authenticated integration remains open

**Status:** Historical contract and Vision consumer are implemented in the inspected working copies; authenticated Core integration remains unverified. **Priority:** High for Phase 5 completeness.

Local tracked-focus, declared-break, and entertainment-category aggregates exist. Core now exposes a bounded `mission_completion_history` read through Vision's authenticated, owner-checked query contract. It filters currently completed missions by `completedAt` in a timezone-aware half-open range, returns stable mission IDs and timestamps, excludes reopened rows by requiring `COMPLETED`, and uses a generic label if the habit was deleted. Vision validates IDs, timestamps, deduplicates, and composes Core-confirmed local-day outcomes into daily reflection.

Evidence: [documented limitation](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/docs/daily-activity-history.md:15), [summary disclosure](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/assistant/activity_summary.py:499).

**Software status:** Contract, server query, timezone boundaries, reopened/deleted treatment, response validation, and daily-summary composition implemented. Core's focused contract tests pass 20 cases and Vision's query/summary tests pass in the selected local gate. **Still open:** test against the selected authenticated Core build and staging dataset; this must remain separate from agent-operation history.

### P5-02 — User-store operational proof remains limited

**Status:** Temporary-SQLite and default per-profile dashboard/API lifecycle are now covered in a disposable application profile; behavior against the user's existing profile and backup behavior remain unverified. **Priority:** Medium.

The local tests exercise opt-in, interval accounting, clock/gap handling, corrections, export/delete, retention, default path selection, and app recreation/restart. They do not prove behavior across a user's existing application data or independent backups.

**Close remaining acceptance when:** Repeat on the user's ordinary app profile and verify backup/snapshot behavior. Explain that local deletion does not erase independent backups. Keep history off by default.

**Fresh software verification:** `tests/test_activity_summary.py` plus `tests/test_dashboard_activity_history.py` passed **18 tests** on 2026-09-28. The new `test_default_profile_history_survives_dashboard_recreation_and_user_lifecycle` exercises the normal `create_app(config)` database path with a disposable profile, enables collection, writes a measured interval, recreates the dashboard, corrects/exports/deletes the data, and verifies deletion persists across another app recreation. It does not verify the user's existing profile or erase filesystem backups.

**Not a mandatory gap:** Scoring remains disabled with no enable control in this slice. The roadmap makes scoring optional. Do not add it merely to mark Phase 5 complete; enabling it later requires an explicit formula, coverage policy, consent, and deterministic tests.

## 8. Contract deviations to resolve, not automatic new infrastructure

One implementation detail now matches the architecture; the remaining scope decision is resolved conservatively for V1:

- **Context ingestion — software gap resolved:** `ContextRuntime` now has a capacity-256 in-process observation queue, coalesces by `(source, field)`, preserves source sequence order when a queued value is replaced, and keeps pause/clear/snooze/declaration controls synchronous and outside the sensor queue. Pause/clear discard pending sensor values and advance sequence watermarks so discarded packets cannot replay. A 1,000-sample test proves this sensor stream remains one pending latest value. The collector uses this queue; snapshots drain the bounded queue.
- **Focus-session snapshot — V1 scope resolved:** Keep the remote `focusSession` field coarse (`focus/background/unavailable`). Do not export a session ID, mission ID, start/end timestamps, or a focus timeline in V1. Local proactive timing keeps its own preferences; mission identity remains Core-owned. This preserves the existing privacy-minimal behavior and avoids turning phone/Discord context into a session-history feed. Revisit only if the owner explicitly asks to share richer focus-session data.

Evidence: [context fields](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/assistant/context_runtime.py:13), [collector](D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision/integrations/context_collector.py). This is a V1 scope resolution, not an omitted implementation task.

The missing `context_models.py` filename alone is not a missing feature: typed classes already live in `context_runtime.py`. No Kafka, Redis, vector database, MCP server, or new agent framework is needed to close these findings.

## 9. Fresh verification — 2026-09-28

### Vision selected companion tests

Working directory: `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision`.

```powershell
python -m pytest tests/test_context_runtime.py tests/test_desktop_activity.py tests/test_desk_presence.py tests/test_context_collector.py tests/test_context_ipc.py tests/test_companion_context_dashboard.py tests/test_context_packet.py tests/test_assistant_context.py tests/test_context_screen_policy.py tests/test_hub_status.py tests/test_companion_policy.py tests/test_companion_runtime.py tests/test_intervention_delivery.py tests/test_context_publisher.py tests/test_activity_summary.py tests/test_dashboard_activity_history.py -q
```

**Result at the first audit pass:** 161 passed, 1 failed; the failure was the P2-01 wording assertion. After the implementation batch below, the expanded selected gate passed 194 tests.

### Core selected API/service tests

Working directory: `D:/ascend-core/.worktrees/phone-chat-core/server`.

```powershell
python -m pytest tests/test_companion_context.py tests/test_phone_chat.py -q
```

**Result:** 23 passed; exit code 0. Context repository tests use an in-memory repository; PostgreSQL gates remain open.

### PWA selected tests

Working directory: `D:/ascend-core/.worktrees/phone-chat-core/client`.

```powershell
npm test -- --run src/features/phone-chat/CompanionContextCard.test.tsx src/features/phone-chat/PhoneChatPanel.test.tsx src/features/phone-chat/api.test.ts
```

**Result at the first audit pass:** 3 test files passed; 4 tests passed; exit code 0. No physical Android/iOS or push-provider certification.

### Follow-up implementation verification — 2026-09-28

- Core status lifecycle + phone queue + mission contract selected tests: **45 passed**; Vision phone worker/transcription + assistant context + config selected tests: **49 passed**. Gemini fake-client test verifies inline audio is sent with its MIME type and only transcript text is returned to the handler. One installed Google SDK deprecation warning is non-failing.
- PWA phone chat/context selected tests: **3 files, 5 tests passed**. ESLint passed on the changed phone-chat UI/API files with no warnings.
- `prisma validate --schema prisma\schema.prisma`: **valid** against the modified phone-Core schema.
- **Initial check:** full client `tsc --noEmit` failed because Next.js generated page validators rejected exported constants in profile/customize and profile/skills. A later follow-up removed only the unused module exports; the current full type check now passes (see §12).
- No real Gemini request, PostgreSQL engine, staging service, push provider, webcam, or phone device was used.

## 10. Recommended closure order and release checklist

The following is a proposed backlog, not implementation/deployment authorization:

1. **Validate observations on hardware:** P1-01 is implemented; complete its physical calibration checks and P1-02 camera lifecycle/occlusion matrix. Preserve unknown instead of guessing.
2. **Complete Phase 2 acceptance:** P2-01 is resolved; record the remaining Phase 2 contract/tone/cost evidence.
3. **Make proactive behavior inspectable:** P3-02, then P3-01 when authoritative producer evidence is available; prove cross-path arbitration before the personal pilot.
4. **Validate remote storage before enablement:** P4-01, then staging identity/expiry checks from P4-04.
5. **Voice software path selected and implemented:** P4-02; next run isolated PostgreSQL migration checks and physical Android/iOS tests.
6. **Validate the notification slice:** P4-03 software is implemented locally; finish isolated PostgreSQL, credential/key provisioning, provider failure/revocation tests, and physical PWA/Discord acceptance.
7. **Complete daily outcomes:** P5-01 after the Core historical contract; finish P5-02 operational checks. Keep scoring optional.

Do not mark the phases complete until the corresponding evidence is attached:

- [ ] Desk calibration and camera/laptop lifecycle matrix.
- [ ] Performance, offline controls, teardown, and IPC operational checks.
- [ ] Green focused software tests and Phase 2 integration/tone/cost evidence.
- [ ] Three independently inspectable rules, shadow-session evidence, and laptop arbitration replay.
- [x] Local shared-queue arbitration and main-loop warning/companion replay tests.
- [ ] Seven-day personal pilot with acceptable interruption rate.
- [ ] Isolated PostgreSQL migration/concurrency/failure/rollback evidence.
- [x] Gemini audio-upload/transcription scope selected; local consent, queue, and transient-transcript path implemented and tested.
- [ ] Staging/provider validation and physical Android/iOS voice behavior.
- [x] Independently consented push/DM software pipeline, generic-only payloads, owner-bound destinations, authenticated detail endpoint, bounded queue/budget/retry contract.
- [ ] Isolated PostgreSQL migration/concurrency checks and provider, revocation, staging, Android/iOS delivery evidence for push/DM.
- [x] Core mission-history contract/client and temporary-SQLite local lifecycle tests.
- [ ] Authenticated Core/staging history integration and real-profile/backup workflow evidence.
- [ ] Accepted contract deviations recorded, or the full contracts implemented. The coarse remote focus-session projection is accepted for V1; the producer-owned Core operation contract remains open pending explicit authorization and integration.
- [ ] Reviewed integration/build and selected deployment verified separately; working-copy presence alone is insufficient.

## 11. Audit boundaries

The initial audit pass used the `verify-and-stop` workflow and made no product edits. Subsequent work was explicitly authorized by the user to close software gaps. No staging/production system was contacted, no secrets were read or printed, no credentials were provisioned, and no production database was migrated. Features remain disabled by default; local code/tests do not certify provider, database, staging, camera, or device behavior. Claims refer to the named worktrees, not every possible branch.

## 12. Implementation follow-up — 2026-09-28

The user's approved first batch was implemented in the Vision working copy:

- Added normalized desk-region validation, face-center containment, and atomic local storage containing only the rectangle.
- Added dashboard controls to start/cancel calibration through the owner-restricted named pipe. Setup uses the existing camera preview; the preview shows the saved or in-progress region.
- Desk presence stays unknown until a region is calibrated. The live detector filters face evidence against that region.
- Very dark, blown-out, and nearly uniform frames invalidate webcam evidence immediately; this is a conservative quality heuristic, not general occlusion detection.
- Changed the proactive absence wording to say no face was detected in the calibrated desk area.
- Updated the remote screen policy assertion to match the local-only response while retaining the no-capture guard.
- Added a disabled-by-default shadow mode and a privacy-bounded decision explanation view over owner-restricted local IPC.
- Added explicit Core needs-input evidence bound to an active operation lifecycle; the shelf publishes only a stable hashed reference. Vision requires fresh producer evidence and an independent opt-in.
- Added local 30-day durable intent claims so an agent-input alert cannot be replayed after a Vision restart.
- Added the Core-owned `mission_completion_history` query scoped to the authenticated character and a timezone-aware daily window; Vision includes only currently completed rows with Core timestamps and stable IDs in daily reflection.
- Ported the needs-input evidence contract to the phone-Core working copy: a request must match a currently active operation ID and its start time; the shelf emits a stable hashed reference; completing the operation clears the request; the legacy Vision heartbeat rejects needs-input.
- Implemented the user's selected Gemini voice path across the PWA/Core/Vision working copies: MediaRecorder review/discard/send; 30-second client and 5 MiB streaming server limits; explicit Google Gemini consent header; feature switches disabled by default; bounded supported MIME set; temporary PostgreSQL queue bytes cleared on terminal paths; outbound worker transcribes inline with Gemini and forwards transient text to the existing isolated phone session. Transcript and audio are not copied into session history.
- Declared the Discord adapter's missing `discord.py` runtime dependency. Fixed the Discord test fake's async cleanup protocol and prevented invalid CLI configuration from leaving the process-wide router logger at `CRITICAL`.
- Fixed the Core client-wide TypeScript failure by making page-local `TitleItem`/`DEFAULT_TITLES` and `Specialization`/`DEFAULT_SPECIALIZATIONS` declarations non-exported. Repository search showed no external consumers. Also removed the `any` casts/unsupported local `class` property that caused targeted ESLint errors, typed specialization stat-key reads, and removed unused imports/bindings. Targeted ESLint on both pages now exits 0 with no warnings; `npx tsc --noEmit` failed on the generated Next page-export checks before these changes and passes afterward. The Core API and persisted profile schema were not changed.
- Removed stale “Core mission timestamps are not provided” wording left in Vision after adding the authenticated mission-history contract. The local aggregate renderer no longer asserts Core outcomes are unavailable; the assistant appends either Core-confirmed outcomes or a genuine auth/read failure. The local dashboard endpoint and footnote now explicitly distinguish its aggregate-only data from the authenticated assistant reflection.
- Added an additive Prisma migration artifact for the companion context table and audio fields. `prisma validate` succeeds, but no local PostgreSQL engine is available, so SQL application, transaction/concurrency behavior, rollback, and backup/WAL retention remain open.

**Fresh focused verification:** the selected Vision gate, including main-loop, context, dashboard, policy, screen, and daily-history tests, passed **197 tests**. node --check static/dashboard.js passed. The check does not establish live camera mouse-coordinate accuracy, all occlusion behavior, physical hardware performance, or staging/production deployment. No camera setup was exercised on the user's laptop during this implementation.

**Follow-up focused verification:** Vision policy/runtime/shelf/dashboard/config/delivery tests passed **58 tests**; Core status contract/service/serialization/completion tests passed **84 tests**. These are local working-copy tests; the Core producer contract has not been deployed or tested with authenticated staging credentials.

**Mission-history follow-up:** Vision context/history tests passed **94 tests**. Core `tests/test_vision_contract.py` passed **20 tests** after removing eager Prisma/router imports from the read-service module. This validates mocked service behavior and the local contract, not staging authentication or real database rows.

**Voice/status follow-up:** Core phone queue + status lifecycle + mission contract selected tests passed **45**; Vision phone worker/transcriber + assistant context + config tests passed **49**; PWA phone panel/API/context selected tests passed **5**. `prisma validate` passed. The Google GenAI fake-client test verifies inline MIME-tagged audio and text-only transcript return. The initial full client TypeScript check failed on page-export errors; that was resolved in the latest follow-up and `npx tsc --noEmit` now passes. A subsequent UI regression test caught and fixed the background-stop notice race; fresh suites are recorded in P4-02. Gemini live request, Postgres, staging, and Android/iOS acceptance remain untested.

**Discord/full-suite follow-up:** Installed `discord.py` 2.7.1 into an isolated temporary Python target (without modifying global packages), then ran `tests/test_discord_phone_bot.py tests/test_llm_router.py`: **47 passed**. The complete Vision test suite passed **799 tests, 1 upstream deprecation warning**. This verifies local behavior and dependency importability only; Discord credentials, live Discord APIs, staging, and phone-device acceptance remain untested.

**Daily-history follow-up:** Regression tests first reproduced the stale Core-unavailable message in the local renderer/dashboard. After correcting the renderer, assistant fallback text, API note, and dashboard footnote, the Phase 5 gate (`test_activity_summary.py`, `test_dashboard_activity_history.py`, `test_main.py`, `test_assistant_context.py`, and `test_assistant_service.py`) passed **94 tests**. It includes default-profile persistence/lifecycle and authenticated Core completion composition tests. A fresh complete Vision run then passed **801 tests, 1 upstream Google SDK deprecation warning**.


**Architecture queue follow-up:** Vision context runtime/collector/packet/publisher selected tests passed **29**, including sequence-safe coalescing and pause-control replay protection. This verifies local bounded behavior, not UI responsiveness under webcam/speech load on the target laptop.

**Audit accuracy correction (2026-09-28):** P1-01's initial wording incorrectly said that current presence detection ignored the calibrated desk region. Fresh source inspection confirmed `main.py` filters detected face landmarks through `face_center_in_region` before calling the collector. The finding now records that software behavior and keeps physical calibration/lifecycle acceptance open.

**Current verification refresh:** Vision companion arbitration/runtime, Core integrations, history, and notification gate passed **94 tests**; after declaring the Discord dependency and fixing the test/CLI issues above, the full Vision suite passed **799 tests**. After correcting stale Core mission-history claims and adding the default-profile lifecycle test, the focused Phase 5 gate passed **94 tests** and a fresh full Vision suite passed **801 tests, 1 upstream Google SDK deprecation warning**. The earlier Core PWA context/chat/API/notification test gate passed **8 tests**; after adding the voice lifecycle regression tests, the full Core client suite passed in an isolated dependency-complete copy: **31 files / 130 tests**. This avoids mutating linked `node_modules`, shared with the main Core checkout, which lacks `happy-dom`. The fresh Core phone/status/notification/context/history integration gate passed **88 tests** using `D:/ascend-core/server/.venv` (Python 3.11); two upstream Starlette/httpx deprecation warnings remain. A separate Python 3.14 run previously passed its 64 service/contract tests; generated-Prisma import was avoided by using Python 3.11 for the broader API gate. Core client `npx tsc --noEmit` and targeted ESLint both pass with the new component test. No Core API or persisted profile schema was changed by the voice UI fix.
