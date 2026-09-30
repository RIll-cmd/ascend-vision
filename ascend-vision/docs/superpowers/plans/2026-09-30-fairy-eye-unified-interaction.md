# Fairy Eye Unified Interaction Implementation Plan

> **For agentic workers:** Implement each scoped phase in order. Use the project's existing review process. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Fairy Eye the primary laptop interface, unify laptop typed and spoken conversation, and make gesture navigation safe and useful.

**Architecture:** One local conversation service owns laptop turn ordering and one in-memory assistant session; a bounded temporary local IPC store fans out events to Fairy Eye and dashboard subscribers. Voice and typed turns call the same assistant session; phone and Discord remain isolated. Gestures navigate visible panels only, while explicit controls own mute and consequential actions remain confirmation-gated.

**Tech Stack:** Existing Python runtime and Flask loopback bridge, `AssistantService`, feedback worker, SQLite local IPC where required, React/Vite Fairy UI, existing pytest and Playwright suites.

**Spec:** `docs/superpowers/specs/2026-09-30-fairy-eye-unified-interaction-design.md`

## Global Constraints

- Keep camera capture owned by the current Vision capture loop.
- Keep laptop, phone PWA, and Discord transcript sessions separate.
- Keep ordinary transcripts session-scoped; only approved facts use long-term memory.
- Keep Core credentials out of the Fairy UI and responses.
- Require explicit confirmation for consequential automation or Core mutations.
- Keep keyboard and pointer controls usable when camera or microphone input fails.

---

## Phase 1: Remove accidental fist mute

### Task 1: Make zero fingers inert

**Files:**
- Modify: `ascend-vision/gesture_controls.py`
- Modify: `ascend-vision/main.py`
- Existing behavior reference: `ascend-vision/tests/test_gesture_controls.py`

**Interfaces:** `GestureController.handle(0)` becomes a no-op. Fist, open-palm, and out-of-range poses cannot change microphone, speech, or cancel state; explicit UI controls own those actions.

- [x] Change the zero-finger controller branch to return without changing mute or mode state.
- [x] Remove the runtime branch that applies the long gesture mute; retain explicit voice toggle and feedback mute paths.
- [x] Update gesture behavior checks: fist/open palm are inert, while dedicated stop/mute controls remain available.
- [x] Remove obsolete gesture mute and voice-routing surfaces; no pose submits text or authorizes an action.

**Acceptance:** Holding a fist or an occluded phone grip cannot set `feedback.is_muted()` through the gesture path. Explicit voice and speech controls remain unchanged.

## Phase 2: One laptop conversation

### Task 2: Define a shared local conversation session

**Files:**
- Create: `ascend-vision/assistant/local_conversation.py`
- Modify: `ascend-vision/main.py`
- Reuse: `ascend-vision/assistant/service.py` and its existing session-key interface

**Interfaces:** `LocalConversation` accepts a source-tagged user turn, calls the existing `AssistantService` with one laptop session key, and publishes ordered `user`, `assistant`, and `status` events. The existing AssistantService session-key interface is reused; bounded temporary event records live in the local IPC store and are erased at runtime shutdown.

- [x] Define ordered event fields: `cursor`, `turn_id`, `source`, `kind`, `text`, `status`, and `created_at`.
- [x] Use one local laptop session key for voice and typed dashboard/Fairy turns; preserve phone and Discord keys.
- [x] Add independent subscriber cursors so dashboard acknowledgements cannot consume Fairy Eye events.
- [x] Add runtime-session initialization and shutdown clearing for temporary events and IPC envelopes.
- [x] Route voice transcripts, typed turns, and generated replies through this service without changing Core/tool confirmation rules.

**Acceptance:** Voice and typed laptop turns share history and appear once to each local UI; remote sessions remain isolated; shutdown/startup clears the local transcript.

### Task 3: Adapt local dashboard transport

**Files:**
- Modify: `ascend-vision/integrations/chat_ipc.py`
- Modify: `ascend-vision/integrations/chat_runtime.py`
- Modify: `ascend-vision/dashboard.py`
- Modify: `ascend-vision/static/dashboard.js`

**Interfaces:** Dashboard enqueue/poll routes address the active runtime session and a dashboard-specific subscriber cursor. Queue transport records are temporary delivery envelopes, not the conversation store.

- [x] Include runtime session identity on queued messages and reject envelopes from older sessions.
- [x] Fan out events with per-subscriber cursors rather than a global destructive acknowledgement.
- [x] Bound event count and body size; clear transport envelopes at shutdown and startup.
- [x] Preserve the existing dashboard's origin, content-length, no-store, and validation behavior.

**Acceptance:** Dashboard still works when Vision is running, shares live local turns, and cannot erase Fairy's unread messages.

## Phase 3: Fairy Eye main chat and Core status

### Task 4: Add chat and runtime status endpoints

**Files:**
- Modify: `ascend-vision/focus_ui.py`
- Modify: `ascend-vision/main.py`
- Modify: `ascend-vision/tests/test_focus_ui.py`

**Interfaces:** Add bounded typed-message enqueue and cursor-based event retrieval to the loopback bridge. Publish safe Core state and last-success timestamp from existing health monitors.

- [x] Validate typed-message JSON/length and event cursors; bind enqueue to the active runtime session.
- [x] Return no-store JSON and preserve trusted-host, origin, and cross-site request protections.
- [x] Publish configured, connecting, connected, re-auth-required, offline, or stale Core state without tokens.
- [x] Keep browser request handlers away from camera access and model work; enqueue work for the runtime owner.

**Acceptance:** Typed chat reaches the shared service; status accurately reflects configured and observed Core health.

### Task 5: Add Fairy Eye conversation surface

**Files:**
- Modify: `ascend-vision/fairy-ui/src/components/fairy-companion.tsx`
- Modify: `ascend-vision/fairy-ui/src/hooks/use-vision-runtime.ts`
- Create: `ascend-vision/fairy-ui/src/hooks/use-local-chat.ts`
- Modify: `ascend-vision/fairy-ui/src` chat-related styles and Playwright fixtures.

**Interfaces:** `useLocalChat` owns draft text, submit state, independent event cursor, errors, and the rendered local event list. A speech preference controls output only; the text composer never depends on microphone permission.

- [x] Add accessible chat open/close control, message list, composer, pending/error state, and keyboard submission.
- [x] Display typed and recognized voice turns and replies in sequence.
- [x] Add distinct mic input and speech output indicators and controls.
- [x] Add a Core status badge/panel with last-check time and clear stale/offline states.
- [x] Keep camera preview optional and avoid duplicate camera ownership.
- [x] Make the documented daily launch open Fairy Eye while retaining the advanced dashboard entry point.

**Acceptance:** Chat works with microphone permission denied; Core and media states are distinguishable; layout remains usable at laptop and phone-width viewports.

## Phase 4: Gesture navigation

### Task 6: Replace voice-only mode selection with visible panel navigation

**Files:**
- Modify: `ascend-vision/gesture_controls.py`
- Modify: `ascend-vision/main.py`
- Modify: `ascend-vision/focus_ui.py`
- Modify: `ascend-vision/fairy-ui/src/components/fairy-companion.tsx`
- Modify: `ascend-vision/tests/test_gesture_controls.py`

**Interfaces:** A validated gesture event selects a named UI panel. Mapping: 1 chat, 2 automation, 3 missions, 4 habits. It does not submit text or execute actions. Five-finger cancel/unmute behavior is represented by separate explicit UI controls until validated.

- [x] Require a complete valid 21-point hand observation before counting; represent missing/invalid landmarks as unknown, not zero.
- [x] Add phone overlap and hand-confidence suppression, stable dwell, release-to-rearm, and cooldown.
- [x] Publish selected panel and gesture recognition status to Fairy Eye.
- [x] Keep pointer and touch panel navigation equivalent to gestures.
- [x] Gestures only navigate; existing automation confirmation requirements remain unchanged.

**Acceptance:** Deliberate poses open the correct panel; partial/occluded hands and phone overlap do nothing; no gesture mutes or authorizes an action.

## Phase 5: Acceptance and rollout

### Task 7: Verify cross-channel behavior and fail-safe paths

**Files:**
- Update: `ascend-vision/tests/test_chat_runtime.py`
- Update: `ascend-vision/tests/test_chat_ipc.py`
- Update: `ascend-vision/tests/test_focus_ui.py`
- Update: `ascend-vision/fairy-ui/tests/fairy.spec.ts`
- Update: `ascend-vision/docs` operating instructions

- [x] Verify ordered local typed/voice turns and independent event cursors with automated tests.
- [x] Verify runtime shutdown clears temporary local transcript/events without deleting approved memories.
- [x] Verify that dashboard and Fairy reject chat while Vision is stopped or draining; browser Stop targets only task IDs from the current laptop session.
- [x] Verify repeated gestures can reopen a panel after manual close.
- [ ] Confirm in a live multi-channel run that phone/Discord sessions do not receive laptop turns or camera facts.
- [x] Verify typed chat with microphone unavailable and offline, stale, and recovered Core states.
- [ ] Perform physical webcam trials with a closed fist, phone held in either hand, partial hand occlusion, and poses 1–4.
- [x] Keep the advanced dashboard available while Fairy becomes the documented daily launch surface.

**Acceptance:** All automated checks pass, physical gesture trials produce no false mute/navigation action in the scripted cases, and rollback can disable gestures or Fairy launch without affecting ordinary text chat.
