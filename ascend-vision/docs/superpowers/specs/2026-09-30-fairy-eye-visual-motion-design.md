# Fairy Eye Motion and Companion Layout

**Date:** 2026-09-30
**Status:** Owner direction clarified on 2026-09-30: preserve the existing circular Fairy orb; stop its animation while Vision speaks. Companion layout and interaction improvements remain in scope.

## Purpose

Keep the existing circular Fairy orb that the owner likes, and make its desktop companion interface easier to use while Vision talks, listens, or waits. Preserve local chat, camera ownership, Core connection display, gesture navigation, and explicit microphone and speech controls. The eye itself must not animate while Vision is speaking.

## What the current implementation shows

- `fairy-ui/src/components/ui/fairy-eye.tsx` spins the outer iris faster as audio energy rises (`voiceSpin`), moves a bright pearl around the pupil (`speechOrbit = time * level * 2.0`), and adds high-frequency ripple and jitter. Multiplying elapsed time by a changing audio level can make the pearl's position jump as that level changes. A synthetic speaking envelope also pulses at roughly four peaks per second. These combine into the rapid movement reported by the owner.
- The owner confirmed that the existing circular orb appearance is good. Do not replace it with an almond-shaped/sclera anatomy redesign. The concern is specifically the animation during speech.
- `fairy-ui/src/components/fairy-companion.tsx` presents twelve expression buttons in the main view. On a narrow viewport this strip takes more space than the conversation and pushes the eye out of view. The current phone and posture reactions select `angry`; there is no explicit return to `open` when those observations clear.
- The runtime chat is a fixed overlay. Its content works, but the layout makes the eye and conversation compete for the same space.

These are code and screenshot observations. The precise severity of the motion should be confirmed with a short recording from the owner's actual laptop during TTS playback.

## Direction and alternatives

| Approach | Result | Trade-off |
|---|---|---|
| **Preserve the existing orb and freeze during speech** | Retains the approved Fairy appearance and removes distracting TTS movement | Speech activity is communicated through labels and the separate voice indicator, not eye motion. |
| Redesign into an anatomical eye | A different, more literal eye shape | Rejected by the owner; do not implement. |
| 3D character eye/avatar | Potentially richer expressions | Out of scope: more assets, GPU use, and tuning than this single-owner companion needs. |

Keep the current friendly, otherworldly circular orb, luminous rings, and established Fairy color language. Do not change its fundamental silhouette or visual identity as part of this fix.

## Visual system

- **Color:** Preserve the existing deep night Ascend setting, luminous blue orb, and established themes.
- **Type:** use a readable Windows/system sans for headings, controls, and conversation; reserve a monospaced face for times and diagnostic details. Sentence case and useful state words replace decorative all-caps microtext.
- **Eye shape:** retain the existing circular orb, concentric rings, and established details. Do not add sclera, eyelids, or a new anatomical silhouette. Remove or freeze speech-triggered rotation, orbit, scale/glow pulsing, and jitter while speaking.
- **Expression:** lid shape, gaze, pupil size, and iris color convey expression. Color alone never carries a status. Neutral is the default; uncertain camera observations do not produce anger or blame. Manual expression selection remains available in Appearance settings, with a clear return to automatic expression.

## Motion contract

The UI has one primary motion at a time. All movement uses frame-time-based easing and bounded values so a dropped frame or changing audio level cannot jump the eye.

| Mode | Eye behavior | Text or adjacent UI |
|---|---|---|
| Idle | Preserve existing non-speaking appearance and behavior, subject to reduced-motion preference. | Ready state. |
| Listening to user | Iris turns gently toward the face or pointer; subtle attention lift. Microphone energy animates the nearby waveform, not the eye's orientation. | Listening label and transcript when available. |
| Thinking | Eye settles; a slow, small highlight change may show activity. | Explicit “Vision is thinking…” state. |
| Speaking through TTS | Freeze the orb's visual state for the duration of speech. No speaking-driven rotation, orbit, scale/glow pulse, blink, gaze movement, or jitter. Resume its prior animation state after speech ends. | Speaking label, reply text, and separate voice indicator communicate activity. |
| Paused, Vision runtime disconnected, or reduced motion | Still eye; retain state through words and controls. | Accurate paused/offline text. |

The runtime already supplies `speaking`, microphone `audioLevel`, and connection state. Treat microphone level as user input; never reuse it as the amplitude of Vision's spoken reply. The standalone demo may synthesize a slow preview pattern, but it must not claim real voice synchronization. `prefers-reduced-motion` and the in-app motion preference both disable nonessential animation.

An offline Ascend Core connection is a separate badge state. It does not imply that the local Vision assistant or eye is disconnected.

## Interface layout

Desktop runtime uses an eye region and an adjacent conversation region. The eye remains visible when chat is open; Core state, session mode, and microphone/speech state are readable near the top. Chat gets a stable scroll area and composer, while camera preview remains optional.

```text
Fairy / Vision ready                         Core: connected · checked 10:42
┌──────────────────────────┬────────────────────────────────────────┐
│                          │ Conversation                           │
│     expressive eye       │ Vision / You turns                     │
│                          │                                        │
│   Listening / Speaking   │ [Message Vision…]              [Send]  │
└──────────────────────────┴────────────────────────────────────────┘
Mic  ·  Speech  ·  Stop  ·  Camera  ·  Appearance
```

On narrow screens, show a smaller eye above the conversation, keep the composer reachable, and tuck appearance choices behind a single control. Phone-width layout here is responsive display only; it does not add remote camera or voice access.

## Boundaries

- This redesign is frontend-only unless a verified runtime state gap appears during implementation. Keep the existing `/api/fairy/state`, chat, command, and frame endpoints.
- Keep camera capture in its current owner. The eye animation reads telemetry and does not open a second camera or microphone in runtime mode.
- Preserve typed chat without microphone permission, the separate mic/speech toggles, Core freshness states, browser Stop semantics, panel navigation, and SVG fallback when WebGL fails.
- Do not add emotion inference or automatic action based on eye animation.

## Acceptance

1. During a 30-second TTS playback, the circular orb render remains visually still; no speaking-driven rotation, orbit, scale/glow change, blink, gaze movement, or jitter occurs. Speaking remains apparent through adjacent UI.
2. The existing circular Fairy orb appearance is preserved at desktop and 390px mobile width, including SVG fallback and reduced motion.
3. Idle, listening, thinking, speaking, paused, and disconnected states are visually and textually distinct. A phone observation or slouched posture does not turn Fairy angry.
4. At 390px width and 200% browser zoom, chat text and the composer remain usable without horizontal scrolling or controls hidden behind the eye.
5. Existing camera, chat, Core, gesture, and Stop behavior continues to pass its focused checks. A short real laptop video is reviewed before the speech-freeze behavior is called visually accepted.
