# Fairy Eye Unified Interaction Design

**Status:** Approved direction; implementation in progress.

## Goal

Make Fairy Eye the primary daily interface for Ascend Vision, let laptop typed chat and laptop voice share one live conversation, and make camera gestures deliberate and useful without allowing an accidental phone grip to mute Vision.

Phone PWA and Discord sessions remain separate from the laptop conversation. Approved memories continue through the existing memory approval path. Ordinary conversation turns are session-scoped and must not become approved memories automatically.

## Current behavior observed

- `main.py` sends voice chat through `FeedbackService.submit_chat()` with a voice session key, while the dashboard bridge calls `AssistantService.respond()` directly with a dashboard session key. They therefore do not share conversation history.
- The dashboard transfers text through `integrations/chat_ipc.py`; its SQLite transport has transient retention and is not a shared live conversation view.
- `focus_ui.py` serves Fairy state, camera frames, and focus/voice toggle commands. It does not currently expose chat messages or Core connection status.
- `gesture_controls.py` maps a zero-finger pose to mute. `main.py` applies a 315,360,000-second feedback mute. The detector uses the first tracked hand, so an occluded hand while holding a phone may be classified as a fist; this is a plausible cause that still needs physical reproduction.
- One through four fingers already select the next spoken-command mode: chat, automation, missions, or habits. Fairy Eye does not make that selection useful or visible yet.
- Core health and Vision presence are calculated for the camera overlay but are not presented as a Fairy Eye status panel.

## Product behavior

### Laptop conversation

- Typed and spoken laptop turns use one local conversation session and appear in Fairy Eye and the dashboard while the session is active.
- Replies appear as text by default. A separate user-controlled setting may speak typed replies aloud; the microphone is not required for typed chat.
- Voice transcripts and assistant replies appear in the same feed, with distinct states for listening, thinking, speaking, queued, and failed.
- Starting a fresh Vision runtime clears the previous local transcript. Only user-approved facts enter long-term memory.
- Phone and Discord keep their own conversation sessions and histories.
- Local context and camera-derived facts may inform laptop chat through existing policy. Remote channels do not inherit laptop transcripts or sensor access.

### Gestures

- A closed fist or zero detected fingers never mutes, unmutes, cancels, or authorizes anything.
- Explicit UI controls remain available for microphone/listening and speech-output mute. A keyboard-accessible fallback is available.
- One through four fingers open the corresponding Fairy panel: chat, automation, missions, and habits. The selected panel and any spoken-command mode are visibly labeled.
- Gesture recognition requires a fully observed hand, a stable deliberate pose, a release before retrigger, and a cooldown. Uncertain landmarks and phone overlap suppress actions.
- Finger gestures only navigate or select a panel. Consequential actions continue through existing confirmations.
- Five-finger behavior is reviewed separately: stop/cancel and unmute must not be conflated. Until validated, the UI offers explicit Stop and Unmute controls.

### Fairy Eye primary shell

- Fairy Eye is the default daily surface and includes chat, camera preview control, separate microphone and speech-output states, and Core connection health with last-check time.
- Core state distinguishes unconfigured, connecting, connected, re-authentication required, offline, and stale. It never exposes credentials.
- Local text chat continues when Core is disconnected; Core-dependent answers state that the data is unavailable.
- The existing dashboard remains available for advanced controls until Fairy Eye contains equivalent required workflows.

## Architecture

Use one in-process laptop conversation service as the authority for turn ordering, session identity, and UI events. Fairy Eye calls it through its existing loopback bridge. Dashboard messages enter through the existing local IPC boundary, adapted to publish and read the same conversation events. The dashboard and Fairy Eye are subscribers; neither owns a separate assistant session.

Conversation turns use the existing bounded local SQLite IPC/event store during the active runtime session so separate dashboard and Fairy processes can share them. Vision clears the rows at session end and before a new runtime session; the store is not a long-term transcript archive. Approved memories remain in the separate memory store. A UI subscriber must not delete another subscriber's unread events; each UI reads with its own cursor.

Typed and spoken turns call the same AssistantService session key. Voice output is surfaced as an event from the feedback worker so the UI sees the same answer that is spoken. Speech output remains optional and interruptible. Camera capture remains owned by the current capture loop; the Fairy bridge never opens a second camera.

The existing loopback-only security model remains. New commands are bounded and enumerated; chat and status routes keep no-store responses, origin checks, input limits, and do not expose secrets.

## Delivery phases

1. **False-mute safety fix:** remove zero-finger mute while keeping explicit controls; update gesture descriptions and the tracked release criteria.
2. **Shared laptop conversation:** establish one session key and in-memory event feed for voice, Fairy chat, and dashboard chat; show turns from every input path.
3. **Fairy Eye primary interface:** add the composer, conversation feed, mic/output states, Core health panel, and reliable default launch path.
4. **Gesture navigation:** map stable one-to-four-finger poses to visible panels, guard against occlusion/phone overlap, and make the mapping discoverable.
5. **Acceptance and rollout:** validate multi-subscriber delivery, process restart clearing, keyboard/mic fallback, physical gesture scenarios, stale Core status, and dashboard compatibility.

## Acceptance criteria

- With microphone permission disabled, the user can type into Fairy Eye and get a visible answer.
- A voice question and typed follow-up share local laptop context; the same turns appear in both local UIs without duplication or one UI consuming the other's events.
- A runtime restart starts a new laptop transcript; approved memories remain available through the existing memory system.
- Phone and Discord histories remain isolated from laptop chat.
- A closed fist and a phone pickup never change mute state. Explicit mute controls work and expose input mute and speech-output mute distinctly.
- One through four deliberate poses open the documented panels. Partial hands, hand tracking loss, and phone overlap do not produce an action.
- Core status is timestamped and honest when stale, offline, unconfigured, or re-authentication is required.
- No camera frame is persisted or sent to Core as part of chat integration.

## Risks and decisions

- The current IPC queue stores message bodies temporarily. The shared conversation work must shorten and clear transport retention so it matches session-only transcript behavior.
- Dashboard and Fairy polling need independent cursors to avoid lost or duplicated events.
- Camera gesture reliability varies with grip, hand angle, lighting, and occlusion. A button and keyboard path remain first-class.
- Fairy Eye should become the default daily UI before the advanced dashboard is retired; parity work is a separate release decision.
