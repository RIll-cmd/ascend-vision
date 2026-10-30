# Prompt coach — design

Date: 2026-10-30. From the "sales call QA and coaching" idea; the owner opted out of questions, so the calls marked
*(my call)* are Claude's.

## Why

The Hub scorecard grades the agents. Nobody grades the owner's side: the task prompts. Some prompts sail through
the Agent Room; others come back with plan revisions, review rounds, retries and failed checks. Vision compares the
two groups and once a week gives three spoken lines of coaching: how the month went, what the smooth prompts had
that the rough ones lacked, and one tip.

## Research findings that shaped it

- The Room keeps every session on disk at `<hub>/.sites-runtime/agent-room/sessions/<id>/` with `state.json`
  (`task`, `stage`, `check`, `createdAt`, `plan.tasks[]` with `attempts`, `rounds`, `failures`, `check`) and
  `transcript.jsonl`, where an owner revision is a line from `room` starting "Owner asked for changes to the".
  This PC has 37 sessions. No Room API returns this per session, so Vision reads the files (read-only).
- The scorecard's `counted()` rule skips sessions the owner stopped before any work started; the same rule applies.

## What it does

- **Reading:** sessions created in the last 30 days, at most 200 newest. The folder is `prompt_coach.room_data`, or
  by default `ascend_hub/.sites-runtime/agent-room` next to Vision's repo. Missing folder: the coach stays quiet.
- **Grading a session:** rework = owner plan revisions + extra review rounds + retries + failed task checks, plus 3
  if the result was rejected and 2 if it was stopped after work began. Smooth = finished (`done`) with rework 0.
  *(my call: weights)*
- **Prompt traits**, read from the task text without AI: names files (`@file`, paths, file extensions); says what
  done looks like ("should", "must", "so that", "tests pass", "done when", "expected"); has a check command (the
  session's `check`); lists steps (bullet or numbered lines); gives an example ("for example", "e.g.", "such as").
  *(my call: deterministic, offline and explainable)*
- **Coaching** (needs at least 6 counted sessions with at least one smooth and one rough; otherwise a short "not
  enough yet" answer on request and silence on the weekly turn):
  1. "Your Agent Room prompts, last 30 days: 12 sessions; 7 went smoothly and 5 needed rework, mostly plan
     revisions."
  2. The trait with the biggest gap (smooth rate minus rough rate, at least 30 points): "The smooth ones more often
     named the files: 6 of 7, against 1 of 5." If none stands out: "The roughest was 'Add dark mode', with 4 rounds
     of rework."
  3. A tip for that trait (or for the first trait the roughest prompt lacked), e.g. "Next time, name the files to
     change."
  If every session went smoothly: "All 12 went smoothly. Keep writing them like that."
- **When:** once every 7 days, 10:00 to 20:00, owner at the PC, through the pacer (held by a focus block). Anytime:
  "how are my prompts?", "coach my prompts", "prompt tips".
- **Storage:** `data/prompt_coach.json` (when it last spoke).
- **Config:** `prompt_coach.enabled` (true), `prompt_coach.room_data` (''). Not added to `config.yaml`.

## Non-goals

- Sending prompts to an AI, or rewriting prompts for the owner.
- Grading the agents (the Hub scorecard does that).
- Writing anything into the Room's files.
