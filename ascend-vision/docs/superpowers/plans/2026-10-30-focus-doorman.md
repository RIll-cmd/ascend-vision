# Focus doorman Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Vision's own non-urgent notices wait during focus mode and come in when it ends.

**Architecture:** `AnnouncementPacer` gains `hold` (a callable) and an `urgent` flag per notice; a small
`FocusDoorman(pacer)` in the same module answers "what's waiting" and "let them through". `main.py` passes
`hold=lambda: manager.mode == 'focus'`, marks Room lines urgent, and puts the doorman in `local_intercept`.
`PromiseCoach` marks clock-time reminders urgent.

**Tech Stack:** Python 3.12, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-focus-doorman-design.md`

## Global Constraints

- Existing pacer behaviour (gap, dedupe, on_said, one failure does not block the rest) is unchanged when `hold` is
  None or false.
- `config.yaml` is not edited.

## Review Focus

- A `hold` callable that raises must not stop notices (treat as not holding).
- Urgent and held notices interleaved: urgent ones keep their order and gap; held ones keep theirs.
- The summary is said once per focus block and only if something waited.
- "let them through" releases what is waiting now, not every future notice of the block.
- Dedupe still works across urgent and held notices.

---

### Task 1: Pacer hold, urgent, summary, release

**Files:** `assistant/announcement_pacer.py`; tests in `tests/test_announcement_pacer.py`.

- `AnnouncementPacer(say, gap_seconds=45.0, clock=time.monotonic, hold=None)`;
  `__call__(text, on_said=None, urgent=False)`; `waiting() -> int` (held, non-urgent notices queued);
  `release_held() -> None`.
- `FocusDoorman(pacer)` with `intercept(text) -> str | None`.
- Copy: "While you focused, {n words} notice(s) waited."; "{N} notice(s) are/is waiting for the end of your focus
  block. Say 'let them through' to hear them now."; "Nothing is waiting."; "Okay."

### Task 2: Config, wiring, promises, README

**Files:** `config.py` `FocusDoormanConfig(enabled=True, let_room_through=True)`; `main.py`; `assistant/promises.py`
(`urgent=True` for timed asks); README row and sentence; tests in `tests/test_config.py`, `tests/test_promises.py`.
