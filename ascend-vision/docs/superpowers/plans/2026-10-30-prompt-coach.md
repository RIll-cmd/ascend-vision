# Prompt coach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Weekly spoken coaching on the owner's Agent Room task prompts, from the Room's own session files.

**Architecture:** `assistant/prompt_coach.py`: `read_sessions(data_dir, now)`, `traits(task, check)`,
`grade(session)`, `coaching(grades)`, `PromptCoach(read, *, announce, store_path, clock)` with `tick` / `intercept`.

**Tech Stack:** Python 3.12, json, pytest.

**Spec:** `docs/superpowers/specs/2026-10-30-prompt-coach-design.md`

## Global Constraints

- Read-only on the Room's folder; a broken session file is skipped.
- No AI calls.
- `config.yaml` is not edited.

## Review Focus

- Sessions without a plan, tasks without counters, transcripts with bad JSON lines or huge size.
- Debate / question-mode sessions (`mode` not `build`, `question` instead of `task`).
- Clock skew: `createdAt` in the future or unparsable.
- The weekly line must not repeat after a restart (stored time).
- Tips must read well aloud.

---

### Task 1: Reading and grading

`read_sessions`, `traits`, `grade` → `Grade(title, smooth, rework, reasons: Counter, traits: dict[str, bool])`.

### Task 2: Coaching and coach

`coaching(grades) -> list[str]`, `PromptCoach`.

### Task 3: Config, wiring, README

`PromptCoachConfig(enabled=True, room_data='')`; `main.py` builds it after the revival coach; README row and
paragraph.
