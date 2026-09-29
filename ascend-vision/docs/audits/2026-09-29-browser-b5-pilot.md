# Browser Automation B5 — Pilot Readiness and Evidence Log

Date: 2026-09-29. This is a readiness record, not a report of live pilot results.
No provider request, staging rollout, Discord command sync, phone action, commit,
or push was performed for this B5 slice.

## Readiness

| Gate | State | Evidence / remaining requirement |
|---|---|---|
| B1 local controller | Passed locally | [B1/B2 acceptance](2026-09-28-vision-browser-b1-b2-acceptance.md), dated 2026-09-28. |
| B2 deterministic fixture acceptance | Passed locally | Same audit: 10 repeated scripted fixture tasks and prompt-injection failures; fixtures make no model call. |
| B2 live Gemini acceptance | Open | Same audit: five real model tasks and provider usage evidence have not been collected. No request is counted for B5. |
| B2 spoken completion confirmation | Open | Owner confirmation on the target laptop remains outstanding in the B1/B2 audit. |
| B3 signed-in workflows | Open | B5 pilot is public read-only; no signed-in workflow is selected for this cohort. |
| B4 PWA / Discord | Local implementation present; live acceptance open | [B4 audit](2026-09-29-browser-b4-implementation-status.md): staging, Discord command sync/DM, and physical phone acceptance remain open. |
| Browser flags | Off | The existing YAML browser automation flags remain false. |
| Metrics preference | Off | `browser_automation.metrics_enabled` defaults false; collection must be enabled explicitly in the local dashboard or configuration. |
| Supported pilot sites | Not selected | Select exact public documentation origins only after live B2 acceptance and owner review. |
| Price/token reservation | Not ready | The broker has a fail-closed reservation boundary and a persistent owner-local spend ledger, but no current, model-specific reviewed price table or accepted provider-specific token bound is configured. Paid B5 pilot calls must remain paused until both are available. |

## Proposed workload and limits

The proposed local public-read workload is 15 documentation lookups, 10 two-page
comparisons, and 10 visible-fact extraction tasks, spread across at least seven
distinct calendar days and at least five eligible attempts per day. It requires
owner verdicts for at least 90% of eligible attempts and reports each workload
family separately. Start date: **not started**. Completed days: **0**. Eligible
live attempts: **0**. Fixture/replay tasks are excluded.

The proposed targets are 50,000 input tokens and 8,000 output tokens per task,
USD 1/day and USD 5 total for the pilot. The owner may lower these before launch.
These estimates are not provider billing caps. A current provider price table,
an accepted conservative token reservation, live B2 model acceptance and spoken
completion confirmation are prerequisites; none is inferred from this
implementation work.

When collection is enabled, enter one row per eligible real attempt using the
fixed fields below. Do not put prompts, full URLs, page contents, findings, task
IDs, owner IDs, exception messages, or credentials into this table.

```text
day | channel | workload_family | eligible_attempts | owner_reviewed | worked |
corrections | handoffs | unknown_effects | known_cost | unknown_cost_calls | revision
```

## Current implementation identity

| Repository | Worktree | Branch | HEAD at B5 start |
|---|---|---|---|
| Vision | `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision` | `codex/laptop-companion-context` | `3ee6985e9e0447f5d0333c32bbe800453d44ca9b` |
| Core | `D:/ascend-core/.worktrees/phone-chat-core` | `codex/phone-chat-core` | `d38846002a0f36fe1659c09fd3b2abeb53cb9648` |

Both worktrees contain pre-existing uncommitted B4 and phone work. The current
B5 changes are layered on those dirty trees; no clean-commit-only claim is made.

## Implementation and phase exit status

Local acceptance is not the same as the seven-day live pilot or channel
acceptance. The following table is the current B5 go/no-go record.

| Phase | Status | Evidence / remaining exit condition |
|---|---|---|
| B5.0 — Freeze pilot contract | Recorded; exit not passed | Workload, proposed limits, readiness matrix and row schema are recorded. The selected local provider is Gemini, but the live model/provider acceptance, explicit metrics opt-in, reviewed price table/token bound and exact site selection are not complete. |
| B5.1 — Measure accurately | Local implementation and focused fixture acceptance passed | Metrics/budget/router/dashboard fixtures pass, including simulated storage failure, Windows clear, retention/cap, unknown usage/cost, and privacy sentinel checks. This does not certify provider billing or a paid pilot. |
| B5.2 — Seven-day baseline | Not started | 0 eligible live attempts and 0 elapsed pilot days. Requires B2 live acceptance and voice confirmation, owner opt-in/site/budget choices, then 35 eligible tasks over 7 distinct days with at least 90% owner review coverage. |
| B5.3 — Reusable routines | Local implementation and broker/verifier fixture acceptance passed; promotion not eligible | Exact routine candidates remain disabled; no Chromium routine run or live promotion evidence. Promotion requires B5.2 and each exact version's acceptance. |
| B5.4 — Targeted expansion | Deferred | No adapter is justified without baseline failure evidence. Named routines on PWA/Discord require the corresponding B4 live staging and phone gates, which remain open. |
| B5.5 — Release decision | **NO-GO for routine release** | Keep all routines disabled and browser pilot flags off. No measured per-routine results exist to satisfy release thresholds. Revisit after B5.2–B5.4 evidence. |

| Task | Local implementation | Acceptance |
|---|---|---|
| B5.0 readiness | This audit records B2/B3/B4 gates, proposed workload, limits, and zero live attempts. | Recorded; pilot has not started. |
| B5.1 accounting | Owner-local metrics schema/store, feedback, retention/cap, paged export, provider-attempt callbacks, two-attempt cap, actual-model attribution, and a persistent pre-call spend ledger are implemented in Vision. | Local acceptance passed: `tests/test_browser_metrics.py`, `tests/test_browser_budgets.py`, `tests/test_llm_router.py`, dashboard/IPC/config/service/planner coverage. No live billing or paid-pilot claim. |
| B5.2 baseline | No baseline tasks were run. | Not started: live B2 model acceptance, voice confirmation, explicit pilot setup and elapsed user activity remain outstanding. |
| B5.3 routine tooling | Candidate manifests, strict registry/invocation, narrower policy, verifiers, broker/UI path, persistent local disable state and bounded prior-page evidence context are implemented. | Local acceptance passed for validation, exact-digest opt-in, restrictions, verifier behavior and broker completion. No routine is enabled; Chromium-specific routine and live promotion acceptance remain open. |
| B5.4 expansion | No site adapter implemented. | Correctly deferred: there are no baseline failures to justify an adapter. Remote named-routine acceptance is gated by B4. |
| B5.5 release | Release decision recorded as NO-GO. | Keep flags off and routines disabled until measured, per-version evidence satisfies the plan. |

Focused acceptance run on 2026-09-29: **123 passed, 1 dependency deprecation
warning** across metrics, budget, routine, router, planner, service, dashboard,
IPC, config and deterministic Chromium browser acceptance tests. The warning is
from the installed `google.genai` package's Python 3.14 compatibility shim. No
live provider call or remote deployment was part of this run. Routine-specific
tests exercise strict contracts, the policy-aware broker, two-page verification,
and queued disable using a scripted executor; they are not a live Chromium
routine acceptance. Static syntax and `git diff --check` also passed and remain
separate from behavioral acceptance.

## Evidence log

| Date | Channel | Cohort | Attempts | Reviewed | Worked | Corrections | Handoffs | Unknown effects | Cost coverage | Revision / notes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| — | — | — | 0 | 0 | 0 | 0 | 0 | 0 | none | Pilot has not started. |

## Gate to begin

1. Complete and review the five-task live Gemini B2 acceptance, with the selected
   provider's reported usage and latency. Confirm voice completion separately.
2. Review and enable local operational metrics. Choose exact supported public
   documentation origins and lower/equal token and spend targets.
3. Verify a current official price table for each actual/fallback model and a
   conservative token bound at the provider-call boundary. If a request cannot be
   reserved, do not make that request.
4. Keep channel groups separate. Remote PWA/Discord collection waits for the
   B4 staging, cleanup scheduler, command/DM, and physical-phone gates in the B4
   audit.

No B5 pilot day passes on the strength of unit or browser fixtures.
