# Fairy ZZZ Motion and AI Readiness — Acceptance Record

**Date:** 2026-09-30
**Worktree:** `codex/fairy-eye-visual-motion`
**Scope:** truthful laptop-chat AI state, reply provenance, the approved circular Fairy eye, and restrained speech/idle motion.

## Verified in this worktree

- The typed-chat router reports the provider and actual model used after failover. Offline answers have an explicit safe failure category and no longer substitute a focus scold for an answer.
- AI state is distinct from Core and microphone state. It distinguishes not configured, configured-but-untested, request in progress, a real successful provider response, a failed request, and a disconnected local runtime.
- Fairy and dashboard chat events preserve the input channel separately from reply provenance (`AI`, `local tool`, or `offline`). Existing SQLite event tables receive additive provenance columns; a session restart clears stale AI-success state.
- The circular orb remains the primary artwork. The Celestial blue theme is updated, fine optical bands are reduced, and the fixed lower-right white glint is aligned between WebGL and SVG. The orb shell no longer follows pointer movement; bounded attention stays inside the eye.
- The full optical pose is captured at TTS start, including gaze, scale, dilation, expression, and palette. Speaking remains still by default. Reduced-motion also freezes the adjacent microphone waveform.
- README includes the explicit worktree launch command using the existing external owner env file; no credential values are included here.

## Test evidence

- Python full suite: **1,116 passed, 20 skipped** (one existing `audioop` deprecation warning).
- Fairy Playwright suite: **36 tests**; see the final implementation handoff for its result.
- Fairy production TypeScript/Vite build completed successfully.
- One small live, non-persisted router prompt used the selected external env file. Groq and Cerebras failed over; Gemini returned a real model response from `gemini-3.6-flash`. Only source/provider/model/failure metadata was printed. No credentials or response text were written into this record.

## Still requires the owner at the laptop

This agent did not take camera/microphone ownership or claim a full physical-device GUI acceptance run. Start Vision with the documented command, type `Who are you?`, and verify the Fairy chat shows an AI/provider attribution and changes to `AI responding`. Then speak a short prompt, confirm the eye stays still through TTS, and check reduced-motion on the actual display. If the live reply is offline, use the visible reason and restart Vision after fixing the owner env/provider setup.

No branches were merged, commits created, or GitHub uploads performed.
