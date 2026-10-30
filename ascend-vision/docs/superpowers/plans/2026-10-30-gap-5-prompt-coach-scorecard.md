# Gap 5: Prompt coach on the scorecard's data

**Goal:** grade prompts by the same signals the Agent Room scorecard uses.

## Research

- Hub `scripts/agent-room/scorecard.mjs` reads the Room's session files (`readSessions`) and sums them **per
  agent**: first try, checks green, stalls (`keptGoing`/`extraRounds`), reassignments, task failures, plan
  revisions, rejected and stopped endings. `GET /api/room/scorecard` returns only those per-agent totals, so it
  cannot say which prompt led to rework.
- `assistant/prompt_coach.py` already reads the same files with the scorecard's `counted()` rule and counts plan
  revisions, extra review rounds, retries, a final red check, rejection and stop — but not stalls, reassignments
  or failures that never became a retry.

## Design

`grade()` also counts, per task: a stall (`keptGoing` or `extraRounds` above 0) as `stalls`; `reassigned`
truthy as `reassigned tasks`; `failures` beyond `attempts` as `failures`. `REWORK_WORDS` gain
`('a stall', 'stalls')`, `('a reassigned task', 'reassigned tasks')`, `('a failure', 'failures')`.

## Rulings

- The coach keeps reading the session files rather than the scorecard API, because the API has no per-session
  view — cost: the two can drift if the scorecard's rules change (the counting is written to match today's).
- Failures that already led to a retry are not counted twice — cost: none expected.

## Tasks (TDD)

1. Test: a task with `keptGoing: 1`, `reassigned: true`, `failures: [a, b]`, `attempts: 1` grades as stall 1,
   reassigned 1, failures 1; the rework words read naturally.
