# Fairy Eye Visual and Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the existing circular Fairy orb, stop its visual animation while Vision speaks, and make the main companion UI comfortable for voice and typed conversation.

**Architecture:** Keep the existing React/WebGL orb, SVG fallback, Python telemetry contract, and shared local chat. Keep the speech-mode renderer visually frozen, then reorganize the current companion layout around eye plus conversation.

**Tech Stack:** React 19, TypeScript, OGL/WebGL fragment shader, SVG fallback, CSS, Playwright/Vite.

**Spec:** `docs/superpowers/specs/2026-09-30-fairy-eye-visual-motion-design.md`

## Global constraints

- This is a frontend redesign. Keep the current Fairy API endpoints and camera/microphone ownership unless a concrete runtime gap is demonstrated.
- Do not change the laptop chat session, Core authority, gesture actions, browser Stop, or approved-memory behavior.
- Preserve the existing circular orb's WebGL and SVG fallback appearance, keyboard access, 390px layout, and both reduced-motion controls.
- During TTS, use the `speaking` flag to freeze the orb's visual output. Microphone `audioLevel` represents input and is not output PCM.
- Preserve the Fairy orb's current circular silhouette and established color themes. Do not implement the proposed sclera/eyelid anatomy redesign; the owner explicitly said the previous eye is good.
- Changes in this checkout are currently uncommitted. Preserve them, and do not push or merge as part of this plan.

## File map

| File | Responsibility |
|---|---|
| `fairy-ui/src/lib/eye-animation.ts` (new) | Pure bounded motion model and mode transition rules. |
| `fairy-ui/src/lib/eye-motion.ts` | Existing gaze constraints and expression geometry; adjust only values needed by the new eye. |
| `fairy-ui/src/components/ui/fairy-eye.tsx` | WebGL anatomy, matching SVG fallback, and render loop consuming the motion model. |
| `fairy-ui/src/components/ui/voice-powered-orb.tsx` | Keep the compatibility wrapper's props and description aligned with the non-spinning eye. |
| `fairy-ui/src/components/fairy-companion.tsx` | Derive companion mode, simplify expression controls, arrange eye/chat/status/actions. |
| `fairy-ui/src/styles.css` | Desktop/mobile layout, legibility, reduced motion, and focus styling. |
| `fairy-ui/tests/eye-motion.spec.ts`, `fairy-ui/tests/fairy.spec.ts` | Motion bounds, state transitions, responsive layout, and fallback acceptance. |
| `fairy-ui/README.md` if present; otherwise `README.md` | Describe the appearance and motion preferences after the UI exists. |

## Phase 1 — Remove the frantic speech motion

### Task 1: Define and test one bounded eye motion model

**Files:** Create `fairy-ui/src/lib/eye-animation.ts`; test in `fairy-ui/tests/eye-motion.spec.ts`.

**Interface:** Export `EyeMode = 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'offline'`, `EyeAnimationInput`, `EyeAnimationState`, and `stepEyeAnimation(previous, input, dt): EyeAnimationState`. State owns eased iris scale, glow, blink, and gaze. Do not produce any rotating angle. Clamp `dt` to 0–50 ms and input levels to 0–1.

- [ ] Add a failing test that advances idle, speech, and reduced-motion input for 30 seconds at 60 fps. Assert finite values and bounded idle behavior; during speech assert all visible eye properties stay exactly constant and mic-level changes have no visual effect.
- [ ] Add a failing transition test for `speaking → idle` and abrupt microphone level changes. Assert no instantaneous iris-scale change above `0.01` per 60 fps step; changing microphone energy while `speaking` must not change the speech animation target.
- [ ] Implement a speaking-mode visual freeze: retain the last eye pose and keep scale, glow, blink, and gaze fixed until speaking ends. Do not add a synthetic speech pulse. Reduced motion returns stable scale, glow, and gaze.
- [ ] Run `npm test -- --reporter=line` from `fairy-ui`; keep the motion test focused on observable bounds rather than shader source text.

### Task 2: Stop rotation, orbit, and jitter in the renderer

**Files:** Modify `fairy-ui/src/components/ui/fairy-eye.tsx` and `fairy-ui/src/components/ui/voice-powered-orb.tsx`.

**Interface:** `FairyEye` still accepts gaze, expression, theme colors, `speaking`, microphone `audioLevel`, and reduced-motion inputs. Remove `maxRotationSpeed` from `FairyEye` and its `VoicePoweredOrb` wrapper; repository search found no consumer beyond that wrapper. The renderer consumes Task 1's motion state.

- [ ] Remove speech-dependent `voiceSpin`, `rotation +=`, `speechOrbit = time * (level * 2.0)`, and the orbiting pearl. Remove high-frequency `voiceAcoustic` and `jitter` terms that move the pupil or rings at tens of cycles per second.
- [ ] Update the `VoicePoweredOrb` wrapper's props and outdated orbit/spin description so it no longer advertises or forwards a rotation setting.
- [ ] Separate user-input energy from Vision's speaking state. Freeze the entire eye while speaking; keep microphone feedback in the adjacent waveform.
- [ ] Keep one animation frame loop and proper cleanup on unmount/WebGL context loss. Check that paused/reduced-motion frames render a stable eye rather than a frozen mid-blink.
- [ ] Run `npm run build` and the eye-focused Playwright tests. Record a short local capture of idle and speaking states for visual review before proceeding to new anatomy.

**Phase gate:** A 30-second simulated TTS response produces no revolving feature, jump, or rapid flutter. The eye remains readable if WebGL is unavailable.

## Phase 2 — Make the visual read as an eye

### Task 3: Preserve the original orb and verify speech stillness

**Files:** Modify `fairy-ui/src/components/ui/fairy-eye.tsx`; adjust `fairy-ui/src/lib/eye-motion.ts` only if gaze/expression geometry needs it; test in `fairy-ui/tests/fairy.spec.ts`.

- [ ] Restore the prior circular Fairy orb rendering and keep the established rings/notches/details. Do not change it into an anatomical sclera/eyelid eye.
- [ ] Keep WebGL and SVG fallback visually consistent, including expression/gaze behavior outside the speaking freeze. During speech, hold the last visual state.
- [ ] Add Playwright checks/captures for idle, listening, speaking, sleepy, and fallback at desktop and 390px width. Confirm speaking pixels remain unchanged over time while the speaking status indicator remains active.

**Phase gate:** The owner-recognized circular orb appearance is preserved, and a 30-second TTS simulation has no visible eye movement.

## Phase 3 — Calm, usable companion layout

### Task 4: Make state and expression behavior honest

**Files:** Modify `fairy-ui/src/components/fairy-companion.tsx`; test in `fairy-ui/tests/fairy.spec.ts`.

- [ ] Map runtime state to one eye mode with explicit precedence: Vision runtime disconnected/paused, speaking, thinking, listening, idle. Keep the visible text in agreement with that mode. Core being offline changes its badge only. Use `speaking` for Vision speech and input `audioLevel` only for user listening.
- [ ] Remove the rule that phone detection or slouched posture makes the eye `angry`. Return automatic expression to neutral when a qualifying observation clears. Reserve strong expressions for explicit user selection or sufficiently grounded events.
- [ ] Move the twelve manual expression choices into Appearance settings, grouped or searchable if needed. Provide an obvious “Automatic” choice. The main view shows current expression and only essential actions.
- [ ] Test each state with mocked `/api/fairy/state`; assert that transcript text, microphone state, speech state, and Core status remain accurate and independent.

### Task 5: Give eye and chat stable space

**Files:** Modify `fairy-ui/src/components/fairy-companion.tsx` and `fairy-ui/src/styles.css`; test in `fairy-ui/tests/fairy.spec.ts`.

- [ ] Replace the fixed conversation overlay with a desktop two-region layout: eye/presence on one side, scrollable conversation/composer on the other. Keep the camera preview optional and avoid covering either region.
- [ ] At 390px width, shrink the eye, put conversation below it, and keep the composer and essential controls reachable. Appearance choices stay in a single settings surface. Do not let a long transcript resize the eye.
- [ ] Raise the size and contrast of Core, mic, speech, and session labels; use sentence case for user-facing status. Reserve tiny mono text for diagnostic timestamps only. Keep visible keyboard focus and 44px touch targets for primary controls.
- [ ] Verify 390px, common laptop viewport, and 200% browser zoom without horizontal scrolling or clipped primary controls. Preserve typed chat when microphone access fails, plus browser Stop and existing gesture panel navigation.

**Phase gate:** Eye and conversation are visible together on desktop; on mobile, the eye remains identifiable and sending a message requires no overlay management.

## Phase 4 — Acceptance and rollout

### Task 6: Verify the visual and runtime behavior

**Files:** Update focused Playwright tests and `README.md` only for shipped behavior.

- [ ] Run `npm run build` and `npm test -- --reporter=line` from `fairy-ui`; run focused Python Fairy/runtime tests only if the frontend work touches their contract.
- [ ] Capture a short real laptop session covering idle, the user's speech, Vision TTS, a long TTS answer, pause, disconnected Core, and reduced motion. Inspect for sudden jumps, hidden text, visual fatigue, and eye recognizability. Record the tested device and viewport; do not treat mocked audio as proof of real TTS behavior.
- [ ] Repeat with WebGL disabled and at 390px width. Verify keyboard chat, microphone-denied chat, camera preview, Core freshness badge, Stop, and 1–4 gesture navigation still work.
- [ ] Document the revised controls and motion preference. Keep an easy rollback: the existing Fairy launch flag can be disabled while the ordinary assistant and dashboard continue to function.

**Release gate:** The owner reviews the real speaking capture and agrees that Fairy looks eye-like and calm. Automated tests and the fallback/mobile checks pass; no new permission or remote-control behavior is introduced.
