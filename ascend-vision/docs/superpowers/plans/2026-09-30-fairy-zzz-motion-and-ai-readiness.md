# Fairy ZZZ-Inspired Motion and AI Readiness Implementation Plan

> **For agentic workers:** Execute one task at a time with `superpowers:executing-plans`. Preserve the existing worktree changes. Checkboxes below describe future implementation, not completed work.

**Date:** 2026-09-30
**Status:** Proposed plan. This turn performed research and local diagnostics; it did not modify application behavior, launch Vision, change credentials, or call a live model.
**Goal:** Make Vision answer ordinary conversation through a working, visibly identified AI connection, and make the existing circular Fairy eye feel closer to the ZZZ reference without rapid speaking animation.
**Architecture:** Keep the shared laptop conversation service and existing WebGL/SVG renderer. Carry actual reply provenance from the model router to the conversation UI. Keep animation state independent of provider availability, microphone permission, and Ascend Core connectivity.
**Tech stack:** Python, existing provider router and local IPC, React/TypeScript, OGL/WebGL, SVG, pytest and Playwright.
**Spec:** Owner's current request and circular-eye preference, plus [the existing motion design](../specs/2026-09-30-fairy-eye-visual-motion-design.md). The proposed refinements below supersede contradictory visual details in the earlier plan. Speaking remains still by default unless the owner chooses subtle speaking motion.

## 1. Findings that explain the current experience

### 1.1 The reported answer is an offline fallback

The exact sentence reported by the owner is item 3 in `feedback.py:FALLBACK_CHAT_REPLIES`. It is selected by `get_fallback_chat_reply`, not evidence that a model answered the identity question.

Local reproduction exercised `AssistantService.respond("who are u")` with an empty provider environment. Only the hash selector was fixed to make selection of the exact sentence deterministic. Result:

```text
source: offline
reply: I heard that, but your screen is getting lonely. Eyes back on the prize!
AssertionError: ordinary identity question gets canned focus reminder instead of an answer
```

Two problems must be addressed: why this launch selects offline mode, and why ordinary offline conversation responds with an unrelated reprimand.

### 1.2 Credential loading follows the selected config directory

`main.py` loads `.env` beside `--config`, which defaults to the script's own `config.yaml`.

| Local location | Observed result |
|---|---|
| Current worktree `ascend-vision/.env` | File absent. |
| Inherited environment in the diagnostic process | No Gemini, Groq, or Cerebras key configured. |
| `D:/ascend-vision/.env` | File exists, but none of those provider keys is present. |
| `D:/ascend-vision/ascend-vision/.env` | All three provider key names have values. Values were not printed. |

A controlled comparison using the real generator-selection code, with provider construction mocked, selected `offline` with the worktree env path and `model` with the original project env path. Zero provider requests were made. This strongly explains the symptom when launched with the previous worktree instructions. It does not certify those credentials or configured models against the providers, or inspect the environment of the owner's prior running process.

The earlier suggested interpreter `D:/ascend-vision/.venv/Scripts/python.exe` also lacks `yaml` and `dotenv`. The nested interpreter at `D:/ascend-vision/ascend-vision/.venv/Scripts/python.exe` passed availability checks for yaml, dotenv, cv2, flask, ultralytics, mediapipe, and pytest. Those import-availability checks are not a complete camera/voice startup test.

### 1.3 The UI cannot currently prove that the AI answered

- `AssistantReply` has `source = model | offline | tool`, but `LocalConversation.handle_message` discards it when publishing a reply. The event's existing `source` identifies the input channel (`voice`, `fairy`, or `dashboard`), not the answer's provenance.
- `LLMRouter.generate_response` returns plain text even after all providers fail. `AssistantService` can therefore label a router fallback as `model`. A second local reproduction forced all three provider methods to fail: the returned text matched the router offline bank, while the reported source was `model`.
- “Voice link active” reports microphone/runtime availability. It is independent of a working model connection.
- The service caches its selected generator. Supplying credentials after selection needs a controlled restart or explicit reinitialization; changing a file alone does not reconnect the current instance.

### 1.4 Current visual gaps

- The circular silhouette is already appropriate and must be retained.
- The WebGL version has seven fine internal bands and a bright rim. The reference is visually simpler, with broad blue/white bands and a prominent small white highlight.
- The SVG fallback includes the small highlight circle; the current WebGL shader does not render its matching dot.
- The latest animation model freezes most eye motion during speaking, but the companion still supplies a speech-specific dilation value. The next pass must audit the whole rendered pose, including transitions, instead of assuming the pure animation model controls every moving property.
- Microphone-enabled status currently drives a listening state even when the user is silent. The UI should distinguish an armed microphone from detected input.

## 2. References and evidence limits

| Reference | Use in the design |
|---|---|
| Owner's supplied Fairy screenshot | Primary appearance target: circular blue disc, broad white ring, dark center, offset highlight and dark geometric surround. |
| [Fairy icon/reference entry](https://wiki.bittopup.com/zzz/Enemies/841) | Public still-image reference for the circular emblem. |
| [Fairy character reference](https://zenless-zone-zero.fandom.com/wiki/Fairy) | Character/art reference discovered in search; the full page was not retrievable. |
| [Fairy takes over the HDD — gameplay capture](https://www.youtube.com/watch?v=ePmYbzIaaHI) | Candidate motion reference for a future side-by-side review. This is a gameplay upload, not an official design specification. |
| [ZZZ Steam Points Shop](https://store.steampowered.com/points/shop/app/4162040) and [official community announcement](https://steamcommunity.com/app/4162040/announcements/) | Search surfaced a Fairy animated avatar; useful additional motion reference. |

Search and still references establish the visual direction. The video and animated-avatar playback could not be inspected frame by frame in this research session. All durations, amplitudes, and curves below are proposed Ascend values, not measurements claimed from the game. Before final visual acceptance, compare the rendered loop to an accessible gameplay or avatar reference. Use original procedural geometry; reference media is not required as a bundled runtime asset.

## 3. Recommended design

Three approaches were considered:

1. **Refine the existing procedural orb — recommended.** Preserve its silhouette and themes, simplify the layered drawing, restore the fixed white highlight, and add restrained movements with explicit states. This fits the owner request and reuses the current fallback and runtime wiring.
2. **Use a prerecorded animation loop.** Easier to match one reference moment, but it cannot naturally follow gaze, runtime state, themes, or interrupted speech. Avoid for the main interface.
3. **Rebuild as a 3D or anatomical eye.** Adds a different visual identity and unnecessary renderer work. The owner has already chosen the circular Fairy appearance.

The screen should communicate three separate facts: Vision's local process is connected, the AI provider is configured or has recently answered, and Core has its own connection state. An animated eye is never evidence of a successful model request.

### 3.1 Appearance

- Retain one circular orb with its geometric outer surround. Preserve the existing desktop/chat arrangement.
- Offer a ZZZ-inspired deep blue appearance using an initial palette of indigo `#2937D7`, dark blue `#172660`, pale blue `#91B5F4`, and warm white `#F0F0F6`. These are starting design values, not sampled official color specifications.
- Reduce the fine interior bands to two or three broad tonal layers. Keep the dark center and recognizable bright ring, with less white clipping and bloom.
- Restore the white highlight in WebGL and SVG at a fixed lower-right offset relative to the inner optical group. It follows that group's small gaze translation and never revolves around the center.
- Keep the outer silhouette stationary. Express attention through small shifts of the inner group and modest aperture changes.
- Keep stronger playful expressions available through explicit Appearance choices. Phone detection and uncertain camera observations must not trigger angry or judgmental expressions.

### 3.2 Motion contract

The following bounds are starting values to review on the actual laptop. “Orb diameter” refers to its visible circular disc, not the entire component container.

| State | Proposed movement | Visible status |
|---|---|---|
| Idle | Brief inner-group gaze shift every 4–8 seconds, at most 2% of orb diameter, eased over 300–500 ms. Optional halo drift within 2% over a 6-second cycle. | Ready; separate microphone state. |
| Listening to actual input | Smooth bounded tracking, at most 3% of orb diameter. Small attention aperture change over 250–350 ms. | Listening; input level belongs to the adjacent mic indicator. |
| Processing a local command | Small attention pose, then hold. | Processing locally. |
| Waiting for a model reply | Inner group settles into a focused pose once; no endless spin. | Thinking, with elapsed status text if slow. |
| Speaking — default | Capture and hold the complete eye pose through TTS, including gaze, glint, aperture, scale, and glow. | Speaking, transcript, and separate voice activity indicator. |
| Speaking — optional if selected by owner | Hold orientation and glint; permit only a low-amplitude glow/pupil pulse. Prefer measured TTS output if available; otherwise label it a speaking indicator, not audio synchronization. | Speaking. |
| Successful completion | A single soft acknowledgement before returning to rest, only when no speech freeze is active. No task-success cue for failed or unknown results. | Result state backed by the actual reply/task result. |
| AI unavailable | Calm resting pose with an explicit AI-unavailable label; local controls remain usable. | Provider not configured, request failed, or last success time, as applicable. |
| Paused / reduced motion | Stable pose and text. | Paused or the current operational state. |

Implementation rules: frame-time-based easing; clamp large frame deltas; deterministic seeded idle timing; pause animation scheduling in hidden tabs; one renderer loop; no catch-up spin on tab return. Freeze/resume must not change phase by multiplying total elapsed time by an audio level. Operating-system reduced motion overrides every nonessential effect, including adjacent waveform decoration.

### 3.3 UI changes

- Add a compact AI status near the chat header, separate from the Core badge and microphone controls.
- Display `AI not configured`, `AI configured — not yet tested`, `AI responding`, `Last AI response: <time>`, or `AI request failed` based on actual evidence. Never show a successful connection merely because an env key exists.
- Keep per-reply provenance visible in a low-noise form: `AI`, `Local tool`, or `Offline response`. The input channel remains independently available.
- Ordinary questions must receive an answer first. Personality may add a short flourish afterward; a focus reminder cannot replace an answer.
- Show actionable local help on failure. Do not expose credentials or raw provider error payloads in chat, status, or logs.
- Preserve the shared laptop transcript, microphone-independent typing, camera ownership, Core status, gestures, Stop, and separate phone/Discord sessions.
- Reduce decorative HUD density around the orb and keep the conversation/composer legible at 390px and laptop sizes. Keep theme/expression controls in Appearance.

## 4. Implementation phases

### Phase 0 — Make the selected launch use the intended environment

**Files:** `main.py`, `dashboard.py`, `README.md`; a small shared `assistant/runtime_environment.py` if needed to avoid duplicated CLI loading; `tests/test_main.py`, `tests/test_dashboard.py`, and `tests/test_runtime_environment.py` if the helper is added.

**Interface:** An optional `--env-file PATH` explicitly selects an owner-managed env file. The existing config-adjacent `.env` remains the default. Existing inherited environment values retain precedence (`override=False`). An explicitly supplied missing file produces a clear startup error. No upward directory scanning or automatic secret copying.

- [x] Add an isolated test for the worktree with no `.env`; verify it reports provider configuration missing without calling a provider.
- [x] Add an isolated test for explicit env selection using fixture credentials; verify inherited values still win and values never appear in diagnostic output.
- [x] Add the shared env-resolution behavior and CLI argument to both entry points. Keep the chosen YAML configuration independent of the env-file location.
- [x] Document the working nested interpreter and explicit env-file launch for this worktree. Verify required imports before launching camera/voice code.
- [ ] Run focused startup tests; start the selected laptop camera/voice runtime and verify its state on-device. (Service-state unit tests and a live provider call passed; physical camera/mic startup was intentionally left for the owner.)

**Exit:** A worktree launch is reproducible, shows its configuration status, and cannot silently choose a different project's credentials.

### Phase 1 — Preserve real AI provenance and repair offline conversation

**Files:** `llm_router.py`, `feedback.py`, `assistant/service.py`, `assistant/local_conversation.py`, `integrations/chat_ipc.py`; tests in `tests/test_llm_router.py`, `tests/test_assistant_service.py`, `tests/test_chat_runtime.py`, and `tests/test_chat_ipc.py`.

**Proposed chat result contract:**

```python
@dataclass(frozen=True)
class ChatGenerationResult:
    text: str
    source: Literal["model", "offline"]
    provider: str | None = None
    model: str | None = None
    failure_reason: Literal[
        "not_configured", "authentication", "rate_limited", "timeout",
        "model_unavailable", "provider_error", "empty_response"
    ] | None = None
```

Production chat should consume a typed result rather than infer provenance from its text. Keep a text-returning compatibility wrapper for existing non-chat callers. Extend `AssistantReply` with optional provider/model/failure fields while preserving `tool` results. Add `reply_source` to the local event schema; do not repurpose the current `source`, which is the input channel. Validate and migrate the temporary SQLite event schema explicitly for existing databases.

- [x] Turn the two diagnostic reproductions into regression tests: no keys yields an honest offline answer; all providers failing yields `offline`, never `model`.
- [x] Add failure coverage for empty replies, timeout, authentication failure, and unsupported model. Assert safe reason codes and no secret-bearing payload in the result.
- [x] Refactor the chat router boundary to retain actual provider/model metadata through fallbacks. A successful second provider is attributed to that provider; a canned reply is always offline.
- [x] Replace the ordinary-chat scolding bank with truthful offline behavior. For an identity request, a local answer may say: `I'm Ascend Vision, your laptop assistant. My AI connection is unavailable right now.` Mark it as local/offline, and never use this identity answer alone as the proof that a model works.
- [x] Carry provenance through `LLMRoaster`, `AssistantService`, `LocalConversation`, and IPC. Preserve read-only tool responses and existing non-chat warning behavior.
- [x] Test a successful model turn followed by a provider failure and then recovery in the same session. A file edit after startup must not magically be reported as a reconnected model; use the documented restart for this iteration.

**Exit:** Every answer has truthful provenance and ordinary offline questions no longer receive unrelated productivity criticism.

Minimum regression examples for `tests/test_assistant_service.py`:

```python
def test_unconfigured_identity_reply_explains_offline_mode(monkeypatch):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    reply = AssistantService(FeedbackConfig(), LLMConfig()).respond("who are u")
    assert reply.source == "offline"
    assert "Ascend Vision" in reply.text
    assert "unavailable" in reply.text.lower()
    assert "eyes back on the prize" not in reply.text.lower()


def test_all_provider_failures_keep_offline_provenance(monkeypatch):
    from llm_router import LLMRouter

    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    router = LLMRouter(LLMConfig())
    monkeypatch.setenv("GROQ_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setattr("assistant.service.get_router", lambda *_: router)

    def fail(*args, **kwargs):
        raise RuntimeError("fixture provider failure")

    for method in ("_call_groq", "_call_cerebras", "_call_gemini"):
        monkeypatch.setattr(router, method, fail)
    reply = AssistantService(FeedbackConfig(), LLMConfig()).respond("who are u")
    assert reply.source == "offline"
    assert reply.failure_reason == "provider_error"
```

These examples target the existing provider-call seams. If the typed router refactor changes those seams, keep the production failure path under test and move the stubs to the new provider boundary; do not stub the final answer into existence.

### Phase 2 — Show useful AI status in Fairy and dashboard

**Files:** `focus_ui.py`, `main.py`, `static/dashboard.js`, `fairy-ui/src/hooks/use-local-chat.ts`, `fairy-ui/src/hooks/use-vision-runtime.ts`, `fairy-ui/src/components/fairy-companion.tsx`, `fairy-ui/src/lib/eye-state.ts`; related existing Python and Playwright tests.

**Interfaces:** Read-only runtime AI state exposes configuration availability, current request state, last real model-success timestamp, and a safe last-failure category. Individual events expose `reply_source`, with optional actual provider/model. Do not infer either value from the text or the microphone state.

- [x] Extend the local state/event validators and TypeScript interfaces together. Missing fields from an older runtime display `AI status unknown`.
- [x] Add the compact AI status and per-reply provenance without crowding Core/mic controls.
- [x] Distinguish `Mic on` from detected user speech. Treat queued, local processing, model request, and TTS as separate states.
- [x] Add a UI regression fixture that renders the owner's identity question and an offline answer. It must show offline status even while Core and microphone are connected.
- [x] Add fixtures for model success, provider failure after success, local-tool success, and runtime disconnection. Never retain a stale green AI-ready label after a failed request.

**Exit:** The owner can tell why Vision is unavailable and whether any given reply came from the AI.

### Phase 3 — Match the circular Fairy appearance

**Files:** `fairy-ui/src/components/ui/fairy-eye.tsx`, `fairy-ui/src/lib/eye-motion.ts` only if geometry requires it, `fairy-ui/src/components/fairy-companion.tsx`, `fairy-ui/src/styles.css`, `fairy-ui/tests/eye-renderer.spec.ts`.

- [ ] Capture a pre-change idle/listening/speaking baseline in both renderers. (Not recoverable in this worktree because prior Fairy changes were already uncommitted; the owner screenshot remained the comparison reference.)
- [x] Apply the proposed blue palette as an Appearance option and simplify fine bands while retaining the original silhouette.
- [x] Implement one matching fixed white highlight in both renderers. Use the same inner-group transform and normalized offset.
- [x] Reduce clipped bloom and decorative surrounding rings so the central eye stays readable at small sizes.
- [x] Inspect 390px and laptop captures next to the owner's screenshot. Review ring proportions, highlight position, contrast, and fallback parity before layering on motion.

**Exit:** The still image clearly retains the approved circular Fairy identity in both renderers.

### Phase 4 — Add bounded ZZZ-inspired movement

**Files:** `fairy-ui/src/lib/eye-animation.ts`, `fairy-ui/src/lib/eye-state.ts`, `fairy-ui/src/components/ui/fairy-eye.tsx`, `fairy-ui/src/components/fairy-companion.tsx`, `fairy-ui/src/components/ui/waveform.tsx` if needed for reduced-motion support; existing eye-motion, eye-state and renderer tests.

**Interfaces:** Keep `stepEyeAnimation(previous, input, dtMs)` as the pure state transition boundary. Add a complete pose representation for all motion-controlled properties used by both renderers, including the highlight and effective dilation. A motion preference defaults to the owner's current still-speaking choice. UI theme changes remain explicit user actions rather than speech-generated motion.

- [x] Add meaningful bounds tests for 30/60/120 Hz, long frame gaps, idle timing, and invalid/missing gaze. Assert the same displacement bounds across frame rates.
- [x] Implement the motion table using one shared pose and time-based easing. Feed camera/pointer gaze only through bounded input; move no outer shell in response to audio.
- [x] Capture the entire pose on TTS start. Remove the companion's direct speaking dilation override so it cannot bypass the freeze. Resume gently after TTS without replaying elapsed idle motion.
- [x] Keep microphone energy outside speech-motion input. If a subtle speaking option is selected, use the configured small pulse and document whether it is an activity indicator or actual TTS-derived envelope.
- [ ] Check a 30-second simulated spoken reply in WebGL and SVG, including start, end, background-tab return, and context-loss fallback. (SVG has the full 30-second simulation; WebGL pixel-stability and context-loss were checked, but not as one combined 30-second test.)
- [x] Verify reduced motion across the eye and adjacent visualizer and release all animation frames/observers on unmount.

**Exit:** Movement feels intentional and restrained, with no rapid spinning, orbiting highlight, audio-driven jitter, or surprise jump when speech ends.

### Phase 5 — Validate real conversation and review the visual result

**Files:** The README and a dated acceptance note under `docs/audits/`; application changes only for a specific failure discovered during these checks.

Run focused Python chat/startup/IPC tests and the Fairy UI suite/build after implementation. Use `D:/ascend-vision/ascend-vision/.venv/Scripts/python.exe` for the selected Python checks if the earlier import preflight still passes. Existing uncommitted unrelated work stays intact.

| Owner-visible test | Pass condition |
|---|---|
| Type `Who are you?` | Identity is answered directly; provenance is visible. A local identity reply is not counted as model acceptance. |
| Ask an ordinary question and a follow-up | Actual provider response plus correct same-session follow-up, with provider/model and success timestamp recorded without retaining the transcript as long-term memory. |
| Trigger a controlled provider failure | Useful offline notice and reason; no focus scolding; no false model badge. |
| Recover the connection with the documented restart | A subsequent real request succeeds and status reflects that result. |
| Disable or deny microphone input | Typed chat still works. |
| Take Core offline | Core badge changes independently of AI chat availability. |
| Play a long TTS answer | Circular identity remains; default speaking pose stays still; no orbit/spin; labels follow real playback. |
| Switch tabs and return, force SVG fallback, enable reduced motion | No time jump, duplicate render loops, or continuous forbidden animation. |
| Use laptop width, 390px, and 200% zoom | Eye, answer, composer, AI status and main controls remain usable. |

For live provider verification, use only a small owner-triggered test with the configured provider; record actual success/failure and model identity. Configured keys and mocked tests do not satisfy this gate. Never include credentials or private conversation content in the acceptance note.

## 5. Delivery order and scope

Implement Phases 0–2 first so the owner can use and diagnose ordinary chat. Then implement the appearance and motion passes, followed by live acceptance. The visual pass can be reviewed independently, but release claims require the corresponding functional and visual evidence.

This plan does not enable browser automation, proactive monitoring, remote context sharing, automatic screenshot capture, or a new memory policy. Their existing settings and acceptance gates stay in place. No GitHub upload is part of this work.

If the owner leaves the speaking preference unanswered, retain still-speaking mode. The optional pulse remains a design alternative rather than an enabled behavior.
