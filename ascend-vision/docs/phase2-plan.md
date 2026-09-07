# Phase 2 implementation plan

Authorized scope: add MediaPipe hands and hold-confirm logic to the existing
Phase 1 application. Sessions, persistence and feedback remain Phase 3/4.

- [x] Write and run failing tests for proximity, debounce, duration, cooldown,
  dropped observations, model preprocessing, timestamp handling and cleanup.
- [x] Add validated hand model settings and explicit atomic model setup.
- [x] Implement `hands.py`: MediaPipe VIDEO mode, BGR-to-RGB conversion,
  pixel coordinates for 21 landmarks, monotonically increasing timestamps.
- [x] Implement `state_machine.py`: wrist/tip geometry, consecutive processed
  observations, one event per hold, immutable duration snapshots and cooldown.
- [x] Wire both models to the same frame, preview landmarks/status, log starts
  and ends, finalize on errors/shutdown and close native hand resources.
- [x] Run full regression suite, real models and bounded live camera checks.
- [x] Update README, verification report, dependency lock and Phase 2 archive.

Decisions: default center distance follows section 3.3 pseudocode; optional
box-edge distance follows section 3.2 prose. Only wrist/tips (0,4,8,12,16,20)
qualify. Confirmation is measured in processed observations, not captured or
skipped frames. A configurable observation gap breaks continuity. Release is
immediate on a negative observation as specified. Every confirmation is retained;
cooldown gates alert eligibility, preserving section 3.4's every-pickup logging.
Durations begin at confirmation (section 3.3), not the first candidate frame.

Limit: this geometry cannot prove a grip if a resting hand is sufficiently close
to a desk phone. Verify no-hand, distant-hand, brief proximity and stable hold
cases automatically; do not claim universal desk-phone rejection.
