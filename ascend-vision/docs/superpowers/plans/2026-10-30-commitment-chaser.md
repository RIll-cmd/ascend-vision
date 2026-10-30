# Commitment chaser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Vision catches spoken or typed promises and chases them gently until done, smaller or dropped.

**Architecture:** One module `assistant/promises.py` (parser, JSON store, coach) shaped like `assistant/slump.py`:
`intercept(text)` joins `local_intercept`, `tick(owner_available)` joins the report loop, questions go through the
`AnnouncementPacer` with `on_said` opening a 2-minute answer window.

**Tech Stack:** Python 3.12, stdlib only, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-commitment-chaser-design.md`

## Global Constraints

- Spoken copy has no digits in unprompted lines except times and counts (TTS reads them fine).
- `config.yaml` is not edited.
- Tests: `.venv/Scripts/python.exe -m pytest tests/test_promises.py -q` from `ascend-vision/` (main checkout venv).

## Review Focus

- A sentence with "I'll ... tomorrow" inside a longer chat message (must be caught once, AI still answers).
- Times already past today ("at 9am" said at 10:00) must move to tomorrow, "today" said at 18:00 to now + 1 h.
- Two promises due at once: only one question open; the second waits.
- Answers outside the window must reach the AI, not the coach.
- A corrupt or missing `promises.json` must not stop Vision.

---

### Task 1: Shared answer words

**Files:** Create `assistant/answers.py`; modify `assistant/slump.py` to import from it; test `tests/test_answers.py`.

- `words(text) -> str`, `is_yes(text) -> bool` (slump's `_YES` match and no `_NEGATION`), `is_not_now(text) -> bool`
  (slump's `_NOT_NOW` fullmatch). Slump keeps its behaviour; its tests stay green.

### Task 2: Parser

**Files:** `assistant/promises.py` `parse_promise(text, now) -> Caught | None` with
`Caught(what: str, due: datetime, timed: bool, explicit: bool, when: str)`; tests in `tests/test_promises.py`.

Tests: explicit and implicit shapes, every `<when>` form in the spec, passed-time rules, no-time → None,
length bounds, punctuation trimmed, "remind me tomorrow to X" order.

### Task 3: Store and coach

**Files:** `PromiseStore(path)` (load tolerant of missing or corrupt file, atomic save, 30-day prune of closed);
`PromiseCoach(store, *, announce, clock=datetime.now, offer_window=120.0)` with `intercept(text)` and
`tick(owner_available)`.

Tests: capture replies, implicit notice, dedupe, ladder copy for asks 1/2/3, stale after 5, every answer kind,
smaller flow, drop that, done with X, list, one question at a time, window expiry, window opens on_said,
speak hours for date-only vs timed, owner away.

### Task 4: Config, wiring, README

**Files:** `config.py` `PromisesConfig(enabled=True)` (bool check like `SlumpConfig`), `Config.promises`,
sections map; `main.py` builds `PromiseCoach(PromiseStore(data_dir / 'promises.json'), announce=announce_paced)`
in try/except, adds it to `local_intercept` before `slump_coach`, ticks it in the report loop;
README settings row "Stop promise check-ins | `promises.enabled: false`" and one paragraph. Test in
`tests/test_config.py`.
