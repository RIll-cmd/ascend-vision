# Show me once Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** "Vision, watch me" records narration and screenshots and writes a how-to guide.

**Architecture:** `assistant/show_me.py` `HowToRecorder` with `intercept(text)` (first after away mode in
`local_intercept`) and `tick()` (report loop). Writing runs on a daemon thread; tests inject `run`.

**Tech Stack:** Python 3.12, PIL, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-show-me-once-design.md`

## Global Constraints

- Screenshots never leave the computer; only transcript text goes to the AI.
- `SILENT_ACK = '📝'` replies are shown, never spoken (`main.py` `on_local_chat_reply`).
- `config.yaml` is not edited.

## Review Focus

- Narration must not reach other helpers or the AI while recording, and normal chat must be untouched otherwise.
- A screenshot failure (locked screen, no display) must not stop recording or lose the transcript.
- An AI reply with bad JSON, wrong step indexes or too many steps falls back cleanly.
- Two guides with the same title on the same day must not overwrite each other.
- Markdown injection: narration with `#`, `![`, or newlines must not break the guide's structure.

---

### Task 1: Recorder

**Files:** `assistant/show_me.py`; `tests/test_show_me.py`.

- `HowToRecorder(folder, *, announce, capture, write_steps=None, clock=datetime.now, run=None, max_seconds=1800,
  max_shots=60)`; `intercept(text) -> str | None`; `tick() -> None`; `recording: bool`.
- `fallback_steps(lines) -> dict`; `parse_steps(answer, line_count) -> dict | None`; `make_writer(router)`.
- `render_guide(title, summary, steps, checklist, shots) -> str`.

### Task 2: Config, wiring, README

**Files:** `config.py` `ShowMeConfig(enabled=True, folder='', ai_cleanup=True)`; `main.py` (recorder after
away mode, tick in report loop, SILENT_ACK not spoken); README row and paragraph; `tests/test_config.py`.
