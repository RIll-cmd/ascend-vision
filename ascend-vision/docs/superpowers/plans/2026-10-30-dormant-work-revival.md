# Dormant work revival Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Once a week Vision brings back one piece of left-behind work and asks: revive, later, or forget.

**Architecture:** `assistant/revival.py`: finders (git, Room payload, gh JSON) that take injected runners,
`RevivalStore` (JSON), `RevivalCoach(store, *, scan, announce, promise=None, clock, run)` with `tick` and
`intercept`, using the pacer's `question_seq` like the other coaches. `PromiseCoach.add(what, due)` is new.

**Tech Stack:** Python 3.12, subprocess (git, gh), pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-dormant-work-revival-design.md`

## Global Constraints

- Read-only on git, GitHub and the Room.
- Every subprocess has a timeout and no console window; a failing repo or a missing `gh` is skipped.
- `config.yaml` is not edited.

## Review Focus

- A repo with no `main`/`master`, a detached HEAD, or a branch name with spaces or unicode.
- `gh` missing, not logged in, or slow (timeout) must not stop the scan.
- Two scans at once (tick and "what did I leave?") must not run twice or race on the result.
- Answers outside the window, or after another question was spoken, reach the AI.
- Corrupt `revival.json`.

---

### Task 1: Finders

`left_branches(repo, run_git, now) -> list[Leftover]`, `left_room_sessions(payload, now)`,
`left_issues(repo, run_gh, now)`, `default_repos(vision_root) -> list[Path]`, `scan(repos, ...)`.
`Leftover(key, kind, name, where, last: datetime, detail)`.

### Task 2: Store, coach, promise hook

`RevivalStore(path)`; `RevivalCoach`; `PromiseCoach.add(what, due) -> bool`.

### Task 3: Config, wiring, README

`RevivalConfig(enabled=True, repos=())` (list of strings); `main.py` builds the scan (git and gh runners, Room
payload via `read_room_sessions`), coach after the promise coach in `local_intercept`, tick in the report loop.
