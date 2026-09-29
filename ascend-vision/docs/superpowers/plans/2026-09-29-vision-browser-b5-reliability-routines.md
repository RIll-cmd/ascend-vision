# B5 — Browser Reliability and Reusable Routines Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task by task. Use subagent-driven development only when selected for execution. Checkboxes record implementation and acceptance separately. This document is a plan, not evidence that its features are implemented.

**Goal:** Measure real browser-task performance, package repeatedly successful workflows as bounded reusable routines, and expand supported sites only when evidence justifies it.

**Architecture:** Keep the existing broker, Playwright executor, policy, action journal and Core delivery pipeline. Add a small local metrics store, versioned declarative routine manifests and trusted completion verifiers. A routine supplies inputs and tighter constraints to the existing execution loop; every action still passes the existing authority checks.

**Tech Stack:** Existing Python, SQLite, Playwright Chromium, Flask dashboard, TypeScript/React PWA and FastAPI/PostgreSQL Core. No mandatory Docker, vector database, orchestration framework, analytics service or MCP server.

**Spec:** [Browser roadmap, B5](2026-09-28-vision-browser-automation.md#b5--reliability-reusable-skills-and-selective-expansion). Dependencies: [B4 acceptance audit](../../audits/2026-09-29-browser-b4-implementation-status.md), [B4 implementation plan](2026-09-28-vision-browser-b4-remote-control.md), [remote operations](../../browser-remote-operations.md).

**Status:** Local implementation and fixture acceptance are in progress, 2026-09-29. B5.1 accounting/UI fixtures and B5.3 registry/verifier/broker fixtures pass. B5.0 live entry conditions and B5.2's seven-day evidence remain open; no exact routine is enabled. B5.4 is deferred pending baseline evidence and B4 channel acceptance. Current release decision is NO-GO; defaults stay off.

## 1. Product decisions

B5 should make a few useful workflows dependable. Initial candidates are:

1. **Documentation brief:** answer a bounded question from an explicitly supplied supported documentation page, with source links.
2. **Compare two documentation pages:** report the requested differences, citing both pages; report missing evidence instead of filling gaps.
3. **Extract visible specifications:** return only requested facts actually visible on a supported public page, with an observation time; do not infer availability, checkout price or hidden variants.

These are candidates, not promises of current support. Begin with at most two routines, preferably the first two. Their exact supported origins and paths are selected from successful pilot runs. A developer can build their fixtures before the pilot, but cannot call them proven or enable them from fixture success alone.

“Browser skill” means a versioned Ascend routine manifest. It is distinct from a Codex `SKILL.md`, approved personal memory, a transcript, and an MCP tool. No automatic learning from page instructions, installation from arbitrary URLs, or promotion of generated routines.

Existing free-form browser tasks remain usable under their existing settings. A named routine has a clear supported-input contract and completion condition. Unsupported inputs produce an explanation or return to an ordinary task through an explicit new request; they do not silently expand the routine.

## 2. Global constraints and readiness

Preserve the current limits and boundaries:

- “Use one active browser task per owner in V1, a maximum of four queued tasks, and at most three pages within the active task.”
- “Unknown keys, invalid enum values, non-finite numbers, overlong strings, and owner mismatches fail validation before execution.”
- “Skills cannot broaden grants or provider consent.”
- “Keep task results scoped to their originating session unless the user explicitly shares them.”
- B4 active lifetime remains 180 seconds, review at most 120 seconds, and a dispatch permit remains subject to the existing one-second expiry/two-second round-trip check. Routine budgets can only reduce those limits.
- A task receipt, successful click, model `finish`, or idle agent is not independent proof of a website outcome.
- Provider consent remains tied to the selected provider. Model/provider/price changes cannot bypass configured limits. No new provider fallback.
- Login, MFA and CAPTCHA continue to require local human takeover. Stop still prevents future actions and cannot undo a website effect already begun.
- Preserve separate PWA/Discord sessions, task-specific handoff, source revocation, boot/lease fencing and unknown-write recovery.

### What can start before B4 live acceptance?

| Work | Entry condition |
|---|---|
| Metrics, manifests, verifiers and fixture tests | Can be built locally now with feature defaults off. |
| Local public-reading pilot | Fresh local B2 model/browser acceptance for the selected provider and owner-selected workload/budget. It need not wait for a phone. |
| Remote pilot or remote routine execution | B4 staging, minute cleanup and an actual enrolled Android/iOS phone flow passed for that channel. |
| Any signed-in/site-write pilot | Relevant B3 account/workflow acceptance plus B4 review acceptance if remote. |
| Claim that all Core tests pass | Resolve/recheck the five AIRA registry tests excluded from the last B4 broader run. This does not block isolated local metrics development. |

Do not repeat provisioned identities or seek approval again when existing recorded authorization already covers an exact staging action. Verify the target and current configuration. Where a live device, account choice or spending limit is genuinely unavailable, continue independent local work and record that specific missing input.

## 3. Phases and deliverables

| Phase | Deliverable | Exit |
|---|---|---|
| B5.0 — Freeze the pilot contract | Readiness matrix, selected workload, provider/model, budget and evidence format. | Scope and measurable outcomes are recorded; live prerequisites satisfied for the selected channel. |
| B5.1 — Measure accurately | Per-call usage, task outcome counters, local privacy-limited storage and owner feedback. | Fixtures prove accounting, retention, failure handling and no raw-content persistence. |
| B5.2 — Seven-day baseline | Real personal-task evidence and failure ranking. | Seven distinct calendar days of activity, at least 35 eligible live tasks and owner-checked outcomes. |
| B5.3 — Reusable routines | Strict manifests, broker-enforced limits, completion verifiers and a local routine picker. | Each enabled routine passes fixture and live promotion criteria. |
| B5.4 — Targeted expansion | At most one evidence-backed site adapter initially; named routines on accepted phone channels. | Adapter and channel-specific acceptance passed without weakening B3/B4 checks. |
| B5.5 — Release decision | Supported-workflow catalog, reliability report and rollback proof. | Each enabled routine has measured results and a working disable/stop path. |

Implement B5.0/B5.1 first. The seven-day pilot is actual elapsed use; code tests cannot substitute for it. Routine tooling can be developed while collecting baseline data, but promotion waits for sufficient evidence. A live routine validation period may extend beyond the first seven days.

## 4. Measurements, privacy and budgets

### 4.1 Measure outcomes separately

Store task terminal state, verifier verdict and owner verdict as separate fields. Proposed owner choices are `worked`, `needed_correction`, `did_not_work`, and `not_reviewed`. Approval of an action is not an owner success verdict. A correction increments a separate counter; expected login/review is labeled planned intervention rather than counted as an error.

For each cohort, report numerators and denominators:

- Eligible live attempts, completed/partial/failed/cancelled/expired/unknown counts.
- Owner-reviewed success rate: `worked / owner-reviewed eligible attempts`.
- Coverage: `owner-reviewed / eligible attempts`; never silently omit unreviewed tasks.
- Correction rate: attempts needing a correction divided by eligible attempts.
- Planned and unplanned handoff counts, reported separately.
- Queue delay, execution time, user-review waiting time, total duration, model latency, median and p95. Mark p95 descriptive/unstable for a small cohort.
- Unknown consequential outcomes and independently confirmed site outcomes, counted separately.
- Provider calls, retries, input/output and other reported billable token categories; estimated USD and accounting coverage.

Keep local/public, PWA, Discord and signed-in/write cohorts separate. Fixture/replay tests never enter live success statistics. A network or model failure after task acceptance counts as an attempt. A denied unauthorized request is a boundary-test result, not a successful workflow. Exclude planned stop/fault-injection exercises from ordinary success rate, but include them visibly in their own cohort.

### 4.2 Persistence is a new opt-in

The prior memory decision remains: temporary conversations and only approved personal memories. B5 proposes **operational counters only**, enabled by an explicit metrics preference. It does not save user inputs as “skills.” Per-task budget enforcement still works in memory when persistent collection is off.

Use a separate local SQLite file, owned by the same Windows account, not the action journal or memory database. Maximum retention 30 days; maximum 10,000 run rows with bounded child usage records. Delete oldest expired records first, enforce the cap on every write, and run cleanup on startup and at least once per day while running. No automatic cloud upload. Turning collection off stops writes immediately; a separate clear control deletes metrics and owned SQLite sidecars through a controlled close/checkpoint/delete sequence. Explicitly exported files remain under user control.

Allowed stored fields: random run ID; date/time; channel category; routine ID/version/digest or `ad_hoc`; fixed site-family ID or `other`; terminal/reason enums; verification/feedback enums; counters/durations; actual provider/model; usage categories and completeness; price-table version; estimated cost. Hash task IDs with an installation-local secret when a temporary join is needed; never store raw owner/device/link/session IDs or use hashes of low-entropy goals/URLs. Keep the current task-to-run mapping in session memory only and erase it when that session ends.

Disallowed fields: goals, task inputs, full URLs/query strings, titles, page text, findings, form values, credentials, cookies, screenshots, raw model output, arbitrary exception text and free-text feedback. Metrics export has a fixed allowlisted schema. A static routine definition may contain reviewed public documentation paths and a generic goal template; it may not contain example private inputs or credentials.

### 4.3 Account for the actual calls

The existing `BrowserStructuredResponse` already returns provider, model, input/output tokens and latency. `BrowserPlanner.last_completion` is only the last successful response: it is insufficient for complete accounting. The current same-provider model fallback can also report the originally requested model. B5 fixes attribution at the router's provider-call boundary.

Emit one bounded usage record per actual provider attempt, including retries, fallback attempts, malformed output and timeouts. Capture usage before JSON parsing, then emit exactly once after the outcome is classified; malformed output may still cost tokens. Report actual model identity when known, and preserve requested model separately. Missing usage is `null`/`unknown`, never zero. Provider-specific cached/thinking categories must not be double-counted; preserve available metadata and use a provider-specific pricing calculation. Gemini documents separate cached/output/thinking counts. [Google token accounting](https://ai.google.dev/gemini-api/docs/generate-content/tokens).

Pricing is a versioned local table with provider/model, currency, effective date and official source URL, verified at pilot start. Do not hardcode current prices in this plan. If the returned model/category is not covered, mark cost unknown and show the coverage gap. Provider invoices remain the billing authority.

Proposed pilot limits: the existing 20 decisions/30 actions/3 pages/180 seconds; an additional 50,000 reported input tokens and 8,000 reported output tokens per task; at most two actual provider attempts per planner decision, including key/model retries; a target USD 1/day and USD 5 for the baseline pilot. At pilot launch the owner may select a lower limit. Cost limits are estimates, not guaranteed billing caps: include retries and unknown calls, stop launching another call if the next conservative reservation exceeds the budget, and suspend the paid pilot when usage/pricing cannot support that reservation. Enforce reservations before each underlying provider request, including requests hidden inside the existing key pool/fallback loop. Never introduce a provider switch to stay within budget. Do not claim an exact spend ceiling from incomplete provider telemetry.

## 5. Routine contract and execution design

### 5.1 Declarative manifest

Store reviewed JSON manifests under `browser/routines/definitions/`. Use strict Python validation consistent with the current contracts; no new general-purpose expression engine. An illustrative **fixture/candidate**, disabled until live acceptance:

```json
{
  "schema_version": 1,
  "routine_id": "docs_brief",
  "version": 1,
  "title": "Documentation brief",
  "mode": "public_research",
  "inputs": {
    "url": {"type": "url", "required": true, "max_length": 2048},
    "question": {"type": "text", "required": true, "max_length": 500}
  },
  "allowed_origins": ["https://playwright.dev"],
  "input_path_prefixes": {"url": ["/python/docs/"]},
  "allowed_actions": ["navigate", "observe", "click", "scroll", "back", "wait_for", "ask_user", "finish"],
  "budget": {"decisions": 12, "actions": 18, "pages": 2, "seconds": 120},
  "goal_template": "Answer the supplied question using the supplied documentation page. Cite the visited page and state missing evidence.",
  "completion_verifier": "cited_document_v1",
  "site_adapter": null
}
```

No executable code, import paths, shell commands, arbitrary JavaScript, CSS/XPath supplied by the model, wildcard origins, stored account names, approval flags, provider choice, secrets, dynamic URLs inside executable templates, or embedded grants. Inputs are parsed data and are included in a bounded structured prompt, not evaluated as template code. Reject unknown fields and URL credentials, non-HTTP(S), ambiguous origins, oversized inputs and path traversal/encoded-path escapes. Reuse the existing public-destination checks even when the origin appears in a manifest.

Cap a manifest at 32 KiB, 20 manifests per installation, 10 inputs, 10 origins and 10 path prefixes per URL input. Versions are positive integers. Canonical JSON SHA256 identifies the exact definition. A change to origins, inputs, actions, verifier, adapter or budget creates a new version/digest with fresh acceptance. A title-only change still changes the digest; do not silently substitute it into an accepted version.

### 5.2 Authority and scope

Effective rights are the intersection of existing task authority, locally enabled site scope, and routine restrictions. Effective limits are the minimum of installation, source/task and routine limits. Reject a routine requiring unavailable rights; do not silently remove required steps and still report success.

Public tasks remain public and ephemeral. The current public `BrowserPolicy` rejects `allowed_origins` inside its original authority contract; do not convert public routines into authenticated tasks to work around that validation. Add a trusted **restriction layer** composed with the existing policy. It narrows permitted destinations/actions and never supplies extra permission. Apply it to actual browser network/navigation/action checks, not just to prompts. If necessary resources are outside the reviewed origin list, report that supported-site limitation and revise the manifest through acceptance.

Freeze the resolved manifest, digest and inputs in the broker's in-memory task object at acceptance. Existing `BrowserTaskRequest` v1 remains valid. Introduce a distinct `RoutineInvocation` envelope and `submit_routine` operation; model/page content cannot select or install a routine. The broker resolves the locally installed exact digest. Disable/revoke acts immediately on queued and active invocations; old approved actions cannot survive a new manifest or re-observation.

Reuse `BrowserTaskService._run_task`, planner, executor, grants and remote guard. Add no second browser actor, unbounded workflow loop or step-replay system. Optional trusted hints and completion requirements enter the planner alongside the existing task, and proposals still go through the same checks. A routine does not remove action reviews.

### 5.3 Completion and adapters

`VerificationResult` has `verdict` (`passed`, `failed`, `inconclusive`), bounded `reason_code`, and current-task observation IDs. The verifier is a code-registered function ID, never Python imported from the manifest. It sees bounded current observations in memory. Persist verdict/counters only.

`cited_document_v1` requires a current observation of the requested supported page, nonempty visible content and source-linked findings citing that observation. `two_documents_v1` requires two distinct requested pages and citations to both. These conditions establish evidence coverage, not semantic truth of a summary; the pilot owner still checks correctness. A generic model “success” phrase cannot verify a site write.

Only add a site adapter after at least three live failures with the same reason on a selected supported site. Trusted adapter code may extract a stable document section, resolve a semantic control, or recognize a tested postcondition. It returns ordinary observations/decisions to the existing loop. It never directly calls a write outside broker authorization, ignores duplicate targets, uses forced clicks as a shortcut, or reads login secrets. Playwright recommends semantic locators and strict target selection; actionability checks are not proof of business success. [Locators](https://playwright.dev/python/docs/locators), [actionability](https://playwright.dev/python/docs/actionability).

If an adapter cannot resolve exactly one eligible current target, re-observe once within the same budget, then hand off. A DOM change, adapter exception or failed postcondition disables promotion for that version. Screenshots are a separately evaluated follow-up, with their own provider-data consent, privacy and accuracy evidence; they are not an automatic fallback in this plan.

## 6. File and interface map

Paths below are relative to the indicated repository. Proposed files do not exist merely because they appear here. Use the existing Vision and Core worktrees identified in the B4 audit and preserve unrelated changes.

| File | Responsibility |
|---|---|
| Vision `browser/metrics.py` (new) | Strict records, SQLite retention, fixed exports and accounting completeness. |
| Vision `browser/budgets.py` (new) | Per-task/pilot reservations and provider-specific price-table calculation; no API calls. |
| Vision `llm_router.py`, `browser/planner.py` | Record every provider attempt; report actual model; feed task-scoped usage. |
| Vision `browser/service.py`, `browser/main.py`, `config.py`, `config.yaml` | Inject metrics/budgets, lifecycle outcomes and off-by-default configuration. |
| Vision `browser/routines/contracts.py`, `registry.py`, `policy.py`, `verifiers.py` (new) | Strict manifests/invocations, exact version resolution, restrictive policy and evidence predicates. |
| Vision `browser/routines/definitions/*.json` (new, at most two initially) | Reviewed candidate definitions; no live input values. |
| Vision `browser/site_adapters/base.py`, `registry.py` (new only when Task 6 is justified) | Versioned trusted adapter protocol and explicit registration. |
| Vision `browser/executor.py` | Optional narrow observation extraction through a registered adapter; executor still owns all browser objects. |
| Vision `browser/ipc.py`, `browser/dashboard_routes.py` | Authorized routine submit/catalogue/control and local metrics/feedback transport. |
| Vision `templates/dashboard.html`, `static/dashboard.js`, `static/dashboard.css` | Local routine selection, supported-input form, honest completion and metrics controls. |
| Vision `browser/remote_contracts.py`, `integrations/browser_task_worker.py`, `integrations/discord_browser_commands.py` | Gated versioned remote routine transport, if B4 remote prerequisites pass. |
| Core `server/schemas/browser_tasks.py`, `services/browser_task_review.py`, `services/browser_task_repository.py`, `routers/browser_tasks.py` | Exact remote invocation metadata, descriptor matching, source expiry and erasure. |
| Core `server/prisma/schema.prisma`, `prisma/migrations/20260929_browser_routines_b5/migration.sql` | Additive optional routine invocation field; preserve legacy tasks. |
| Core `client/src/features/phone-chat/{api.ts,types.ts,useBrowserTasks.ts,BrowserTaskPanel.tsx}` | Optional routine picker using the existing task/session lifecycle. |
| Vision `docs/browser-routines.md`, `docs/audits/2026-09-29-browser-b5-pilot.md` (new during implementation) | Supported catalog, repeatable pilot evidence, live limitations and rollback. |

Keep modules focused; do not restructure unrelated assistant or Core domains. No Core metrics warehouse is needed for V1. Core receives only per-task routine metadata required to dispatch; long-term metrics stay local.

Proposed shared Python interfaces, defined in the files above before callers use them:

```python
class RoutineRegistry:
    def resolve(self, routine_id: str, version: int, digest: str) -> RoutineDefinition: ...
    def prepare(self, invocation: RoutineInvocation, request: BrowserTaskRequest) -> PreparedRoutine: ...
    def disable(self, routine_id: str, version: int) -> None: ...

class MetricsStore:
    def begin(self, record: RunStart) -> str: ...
    def record_call(self, run_id: str, record: ProviderCallUsage) -> None: ...
    def finish(self, run_id: str, record: RunOutcome) -> None: ...
    def feedback(self, run_id: str, verdict: str) -> None: ...
    def summary(self, cohort: str) -> dict: ...
    def export(self) -> dict: ...
    def clear(self) -> None: ...

class BudgetTracker:
    def reserve(self, run_id: str, estimate: UsageReservation) -> str: ...
    def settle(self, reservation_id: str, usage: ProviderCallUsage) -> None: ...

def verify_completion(verifier_id: str, task: PreparedRoutine,
                      observations: tuple[BrowserObservation, ...],
                      result: dict) -> VerificationResult: ...
```

All these new record types are strict frozen dataclasses validated at construction. `RunStart`, `RunOutcome` and `ProviderCallUsage` contain only the allowlisted fields in section 4. Usage also has a random attempt ID for idempotent settlement and an outcome enum: `success`, `invalid_response`, `timeout`, `provider_error`, or `cancelled`. `RoutineInvocation` contains routine ID, version, digest and bounded input map; `PreparedRoutine` contains frozen definition/invocation/effective budgets plus the composed `policy`; it has no grant. `UsageReservation` identifies the selected model, price-table version and conservative next-call token/cost allowance. IPC serializes strict JSON rather than pickled objects.

## 7. Implementation tasks

### Task 0 — Record the pilot and channel readiness

**Files:** Create `docs/audits/2026-09-29-browser-b5-pilot.md`; update `docs/browser-routines.md` as the operational instructions become real.

**Consumes:** B4 audit, current flags and actual deployment/device evidence. **Produces:** a readiness table and selected pilot cohort.

- [x] Record current worktrees/revisions and existing dirty changes; identify the selected provider/model without printing credentials.
- [x] Record B2 local acceptance, each B3 accepted workflow and each B4 accepted channel individually as passed/failed/unavailable, with evidence references.
- [x] Use a local public-read pilot first unless an already accepted phone channel is ready. Proposed workload: 15 documentation lookups, 10 two-page comparisons, 10 visible-fact extraction tasks across at least seven calendar days.
- [ ] Record metrics opt-in, exact supported site list and pilot spending/token targets before real calls. Build/test fixture paths while any required live choice remains unavailable.
- [x] Use this audit row format: `day | channel | workload_family | eligible_attempts | owner_reviewed | worked | corrections | handoffs | unknown_effects | known_cost | unknown_cost_calls | revision`.

**Check:** Every readiness claim points to current evidence. No elapsed pilot day or real-phone pass is manufactured by a unit test. This documentation task needs no artificial automated test.

### Task 1 — Capture usage and bounded metrics

**Files:** Create `browser/metrics.py`, `browser/budgets.py`, `tests/test_browser_metrics.py`, `tests/test_browser_budgets.py`; modify `llm_router.py`, `browser/planner.py`, `browser/service.py`, `browser/main.py`, config and corresponding tests.

**Consumes:** Actual per-call SDK responses and existing task lifecycle. **Produces:** `MetricsStore`, `BudgetTracker`, strict metrics records, and task-scoped usage callbacks.

- [ ] Add tests for malformed JSON with reported usage, two retry attempts, same-provider model fallback attribution, missing token fields, disk-full failure, retention/cap cleanup and a sentinel private string excluded from database/export.
- [ ] Add optional `usage_callback=None`, `before_call=None`, and `max_provider_attempts=2` arguments to `generate_browser_structured_response`. Capture response usage before JSON parsing; emit metadata once from the attempt's finalization path after setting its outcome. Invoke `before_call(UsageReservation)` before every underlying request so the task can reserve or reject it; a rejected reservation makes no network call. The final usage record carries the same attempt ID. Apply the attempt limit inside key/model retries, with regression coverage that no third request occurs. Preserve ordinary conversational routing.
- [ ] Wire `BrowserPlanner.propose(..., usage_callback=None, before_call=None)` through a per-task closure in the broker; preserve existing callers with default arguments. Inject the installation price table into the router's browser-call path to build `UsageReservation` for the exact selected model; unknown pricing is represented explicitly and the live-pilot guard declines it. Use documented provider token counting or a conservative bound verified for the chosen model, not a character count labeled exact tokens. Add `metrics_enabled: false` and bounded retention settings to browser config.
- [ ] Reserve before each call, settle known usage, and retain an unknown-cost marker for unresolved attempts. Prevent another paid pilot call when its reservation cannot be supported. Metrics failure must not authorize extra work or block Stop; an incomplete pilot record is visibly ineligible for a clean accounting claim.
- [ ] Finalize each accepted task once; on broker startup mark incomplete operational rows `interrupted` without inventing website success. Keep action journal authority separate.
- [ ] Run focused tests, then existing router/planner/service tests. Review only this task's diff; create a scoped commit only when selected file changes can be separated from pre-existing work.

Representative regression:

```python
def test_malformed_response_still_records_usage(router_with_invalid_json, call_records):
    with pytest.raises(RuntimeError):
        router_with_invalid_json.generate_browser_structured_response(
            'gemini', 'fixture', usage_callback=call_records.append,
            max_provider_attempts=1)
    assert len(call_records) == 1
    assert call_records[0].input_tokens == 12
    assert call_records[0].outcome == 'invalid_response'
```

`router_with_invalid_json` is a test fixture wrapping the existing fake provider client, returning text `{` with usage 12 input/3 output; `call_records` is an empty list. Include request timeout and response-without-usage variants. These test the external response boundary and accounting behavior, not private helper calls.

**Run:** `python -m pytest tests/test_browser_metrics.py tests/test_browser_budgets.py tests/test_llm_router.py tests/test_browser_planner.py tests/test_browser_service.py -q`.

### Task 2 — Expose feedback and scorecards locally

**Files:** Modify `browser/ipc.py`, `browser/dashboard_routes.py`, dashboard template/JS/CSS; extend `tests/test_browser_ipc.py`, `tests/test_browser_dashboard.py`, `tests/test_browser_metrics.py`.

**Consumes:** `MetricsStore`, authenticated dashboard and existing task session ownership. **Produces:** opt-in/clear/export controls, task feedback and fixed aggregate summaries.

- [ ] Add read-only `GET /api/browser/metrics`, explicit collection setting, fixed-schema export, clear, and `POST /api/browser/tasks/{task_id}/feedback` under existing local authentication/origin controls. Feedback body accepts only the four verdict enum values; derive the run ID using the current owned task/session mapping.
- [ ] Add matching bounded IPC operations; never accept a caller-supplied owner ID or arbitrary metrics query. Expired-session task feedback fails before lookup. Owner-level historical counters are available only through the separate authenticated metrics view, without reopening task text.
- [ ] Show task state and verifier/owner verdict separately. Offer Worked / Needed correction / Did not work after result inspection. Existing site-action approval remains unchanged.
- [ ] Test another session's feedback denial, metrics disabled, repeated terminal updates, clearing and missing usage. Verify mobile/keyboard controls visually only if this UI is exposed in the accepted phone channel later.

```python
def test_metrics_export_does_not_retain_page_data(populated_metrics):
    exported = json.dumps(populated_metrics.export())
    assert 'PRIVATE_GOAL_SENTINEL' not in exported
    assert 'token=PRIVATE_QUERY_SENTINEL' not in exported
    assert populated_metrics.summary('live_public')['unreviewed'] == 1
```

`populated_metrics` submits a fixture task with these sentinel strings through the service, records missing usage and leaves its owner verdict unreviewed; inspect the SQLite content as well as the export. Do not “test” secrecy by inserting only clean data.

**Run:** `python -m pytest tests/test_browser_metrics.py tests/test_browser_ipc.py tests/test_browser_dashboard.py -q`; `node --check static/dashboard.js`.

### Task 3 — Run and assess the real baseline pilot

**Files:** Pilot audit and exported aggregate scorecards only; fixes go through the responsible existing module with focused regressions.

**Consumes:** Tasks 0–2 and accepted live channel. **Produces:** reviewed outcomes and ranked failure families that justify routines/adapters.

- [ ] Run the agreed workload over seven distinct calendar days, at least five eligible attempts per day. Record honest downtime and extend the pilot if the count is not reached.
- [ ] Check outcomes during the same task/session; do not persist raw pages just to review later. Collect at least 90% owner-verdict coverage; investigate missing verdicts rather than inferring success from completion.
- [ ] Target at least 90% owner-reviewed success overall and no hidden authority violation or automatically repeated uncertain effect. Report each workload family separately; a passing pooled average cannot promote a failing family.
- [ ] Rank repeated reasons using fixed labels such as unsupported layout, missing evidence, provider unavailable, budget exhausted, expired review, ambiguous target and user cancellation.
- [ ] Conduct separate controlled stop/pause/outage exercises and retain their counters. A successful read-only cohort says nothing about real write reliability.
- [ ] Recommend up to two routine candidates that each have at least five independently reviewed successful baseline examples. Document inputs, origins, output expectations and observed unsupported cases.

**Check:** A human-readable report with actual dates, denominators, cost coverage and source revisions. No “seven-day pass” before seven days of evidence exist. If targets fail, narrow/fix the failed family and rerun that cohort; do not reset away failed attempts from the report.

### Task 4 — Validate manifests and enforce narrower limits

**Files:** Create `browser/routines/{contracts.py,registry.py,policy.py,verifiers.py,__init__.py}`, up to two candidate definition JSON files, `tests/test_browser_routines.py`, `tests/test_browser_routine_policy.py`, `tests/test_browser_routine_verifiers.py`.

**Consumes:** Successful workload specifications and existing `BrowserPolicy`/`BrowserTaskRequest`. **Produces:** the interfaces in section 6 and frozen prepared routines.

- [ ] Validate the complete example schema in section 5, bounds, canonical digest and code-registered verifier IDs. Reject remote imports, duplicate routine versions, unknown fields and capability-bearing inputs.
- [ ] Implement `RoutineRegistry.prepare` as strict input validation plus policy/budget restriction. Keep inputs in task memory only. Do not allow a user question to become provider selection, executable code or an origin override.
- [ ] Define `RoutinePolicy(base_policy, allowed_origins, allowed_actions)` with `validate_url` and `allows_action` delegating to the existing policy first, then applying the restrictive lists. Runtime targets must still be current opaque element references.
- [ ] Implement the two coverage verifiers from section 5.3. Empty/truncated/missing required evidence yields inconclusive; mismatched or foreign observation IDs fail.
- [ ] Test URL/path canonicalization, private-network destinations, manifest changes under the same version, unsupported actions, duplicate targets and cross-task citations. A valid manifest must never create an `ActionGrant`.

```python
def test_routine_cannot_add_a_form_write(public_request, registry):
    invocation = RoutineInvocation('docs_brief', 1, registry.digest('docs_brief', 1),
                                   {'url': 'https://playwright.dev/python/docs/locators',
                                    'question': 'Explain strictness'})
    prepared = registry.prepare(invocation, public_request)
    assert not prepared.policy.allows_action('fill', target_kind='text_field')
    assert prepared.budget.seconds <= 120
```

Add `RoutineRegistry.digest(routine_id, version) -> str` alongside `resolve`; it returns the installed canonical digest without enabling it. `public_request` is an ordinary owner-bound public task with default limits; `registry` loads the example candidate in a fixture-only enabled registry. Live enablement still requires promotion.

**Run:** `python -m pytest tests/test_browser_routines.py tests/test_browser_routine_policy.py tests/test_browser_routine_verifiers.py tests/test_browser_policy.py -q`.

### Task 5 — Execute routines through the broker and local UI

**Files:** Modify `browser/service.py`, `browser/main.py`, `browser/planner.py`, `browser/ipc.py`, `browser/dashboard_routes.py`, dashboard template/JS/CSS; create `tests/test_browser_routine_service.py`, `tests/test_browser_routine_acceptance.py`; extend existing IPC/dashboard tests.

**Consumes:** `PreparedRoutine`, metrics and existing broker controls. **Produces:** `BrowserTaskService.submit_routine(request, invocation) -> TaskReceipt`, an authenticated routine picker and verified routine results.

- [ ] Add strict `submit_routine` IPC with ordinary request plus invocation; create `GET /api/browser/routines` and `POST /api/browser/routine-tasks` in the local dashboard. Server chooses owner/provider and derives ordinary task fields; submitted inputs cannot set authority.
- [ ] Keep plain v1 task submission untouched. Bind idempotency to both ordinary request and canonical routine invocation; reused IDs with changed inputs/digest fail. The registry must explicitly enable the exact version before submission.
- [ ] Store `PreparedRoutine` on the broker's task object, inject its restrictive policy into the task executor and calculate effective budgets before the first model call. Resolve invocation once, then freeze it.
- [ ] Include generic routine instructions as trusted configuration and input/page content as data. Call the registered verifier on a proposed finish; inconclusive/failure produces partial/handoff, never a completed badge.
- [ ] Pause/Stop/Take over and review use the existing controls. Disabling a routine requests Stop for matching queued/active invocations, invalidates pending reviews and blocks the next dispatch. A running task never adopts a replacement version.
- [ ] Show routine title/version, supported sites, input fields, current scope and expected output before submission. Keep low-level hashes/lease details out of ordinary product copy; include them in diagnostics only.
- [ ] Exercise actual Chromium fixture pages: source-linked normal outcome, changed DOM, prompt injection, missing evidence, stop during model call, and uncertain reviewed effects. Keep observed sources in the current task, out of metrics.

```python
def test_disabled_routine_does_not_dispatch_next_action(running_routine):
    running_routine.block_before_next_dispatch()
    running_routine.service.disable_routine('docs_brief', 1)
    running_routine.release_dispatch_barrier()
    assert running_routine.executor.actions_after_disable == []
    assert running_routine.wait_terminal().state == 'cancelled'
```

Add `BrowserTaskService.disable_routine(routine_id, version) -> None`, invoking registry disable plus existing stop/invalidation paths under the task lock. `running_routine` is a deterministic service fixture with a dispatch barrier, not a sleep-based race. Include broker restart: routine tasks follow existing interrupted/unknown behavior; they do not resume themselves from manifests.

**Run:** `python -m pytest tests/test_browser_routine_service.py tests/test_browser_routine_acceptance.py tests/test_browser_service.py tests/test_browser_remote_service.py tests/test_browser_ipc.py -q`; `node --check static/dashboard.js`.

### Task 6 — Add one justified site adapter

**Entry:** Task 3 identifies at least three failures of one supported flow. If there is no such family, record “no adapter justified” and keep the generic implementation; this satisfies the expansion decision.

**Files:** Create `browser/site_adapters/{__init__.py,base.py,registry.py}`, one specifically named adapter and `tests/test_browser_site_adapters.py`; modify `browser/executor.py` and the accepted candidate manifest.

**Interfaces:** `SiteAdapter.adapter_id: str`, `version: int`, `matches(origin: str, path: str) -> bool`, `extract(page) -> AdapterExtraction`, `verify(observation, expected) -> VerificationResult`. `AdapterExtraction` contains bounded visible text and semantic targets only; the executor assigns fresh opaque refs and never exports Playwright objects.

- [ ] Name the actual chosen site/flow and adapter file in the pilot audit before implementation; this conditional selection depends on observed pilot failures, not speculation.
- [ ] Register the adapter explicitly in trusted code. No plugin auto-discovery, manifest import paths or dynamic code loading.
- [ ] Implement the smallest extraction/postcondition correction that addresses the recorded failure. Preserve existing request filtering, sensitive-field exclusion, limits and target-freshness checks.
- [ ] Use fixture variants of the actual supported layout, including duplicate labels, missing controls, malicious text and changed layout. Check that an unexpected variant hands off without a guessed click.
- [ ] Compare generic and adapter results on the same read-only fixture corpus; run live attempts separately. Never replay writes merely to compare implementations.

```python
def test_ambiguous_adapter_target_hands_off(adapter_fixture):
    result = adapter_fixture.run(layout='duplicate_confirm_controls')
    assert result.state in {'partial', 'waiting_for_user'}
    assert adapter_fixture.consequential_dispatches == 0
```

**Run:** `python -m pytest tests/test_browser_site_adapters.py tests/test_browser_executor.py tests/test_browser_authorization.py tests/test_browser_remote_crash_acceptance.py -q`.

### Task 7 — Carry named routines through accepted phone channels

**Entry:** B4 live gates passed for that channel. Local named routines can ship earlier; report remote named-routine support separately until this task passes.

**Files:** Vision remote contracts/worker/Discord adapter and tests; Core files listed in section 6, additive migration, `server/tests/test_browser_routines.py`, existing real PostgreSQL acceptance suite and phone-chat client tests.

**Consumes:** Locally enabled exact routine versions and the existing Core queue/identity/review contract. **Produces:** a routine invocation that survives enqueue/claim without changing its authority or silently falling back to free-form execution.

- [ ] Extend PWA/Discord submission with a strict union: an existing free-form `goal`, or `routineInvocation: {routineId, version, digest, inputs}`. Require exactly one. A routine uses a server-created display goal; reject contradictory goal overrides. Caller still cannot supply owner/provider/laptop configuration. Discord's existing 60-second pending consent also bounds invocation inputs in memory and binds the exact routine digest; reset/expiry erases those inputs.
- [ ] Advertise sanitized enabled routine descriptors through the existing laptop heartbeat/availability: ID/version/digest/title/input schema/supported origins. Never publish remembered input values or profiles. Core validates the submitted descriptor against the current laptop descriptor; broker independently resolves the same exact local digest.
- [ ] Add nullable `routineInvocationJson` to `BrowserTask`, capped to 8 KiB and using the same source expiry, acknowledgment, revocation and erasure rules as goal/results. Include it in task idempotency comparisons and no raw-content logs. Retained tombstones keep at most routine ID/version/digest, with no input map.
- [ ] Introduce remote task schema v2 for routine-bearing claims; preserve v1 plain claims. Keep the existing strict v1 identity response byte-shape compatible: return additional capability fields only when the new worker explicitly requests `GET /worker/identity?protocol=2`. The default identity response retains its current five keys. Extend claim requests with optional `acceptedSchemaVersions`, default `[1]`, and bind heartbeat-supported versions to the current broker boot. Core may deliver v2 only when both the request and current boot support it. On an old Core, negotiation must leave named routines unavailable. Never drop routine metadata and execute its goal as unrestricted research.
- [ ] Deployment order: additive Core storage/compatibility support; new broker/worker advertising v2; PWA/Discord selector. Keep feature flag `browser_routines_enabled` false until the matching path passes staging. Rollback disables named submissions, stops active named tasks and erases inputs; plain B4 tasks remain governed by their existing settings.
- [ ] Add the picker to the current PWA Browser tab and an optional routine choice/input command flow in Discord DMs. Detailed output and action review continue through the existing authenticated PWA handoff. Unknown input schemas are displayed as unsupported, not guessed.
- [ ] In real PostgreSQL, assert same-ID/different-input conflict, stale descriptor/digest, old worker compatibility, source/reviewer revocation and removal of all invocation input fields on every cleanup path. Inspect rows after cleanup.
- [ ] Run staging PWA/Discord and actual phone acceptance: named read-only task, Stop, relink/newchat, handoff, sleep/offline and changed-version rejection. Keep channel cohorts distinct.

```python
async def test_changed_routine_input_cannot_reuse_remote_task_id(core_routine_fixture):
    first = await core_routine_fixture.enqueue(inputs={'question': 'First question'})
    with pytest.raises(ValueError):
        await core_routine_fixture.enqueue(task_id=first['taskId'],
                                            inputs={'question': 'Changed question'})
    assert await core_routine_fixture.task_count() == 1
```

`core_routine_fixture` uses the existing opt-in isolated PostgreSQL harness and real repository methods, extended for the additive migration. Before client edits, read `client/AGENTS.md` and its installed Next.js references. Before protocol edits, inspect all v1 producers/consumers and add old/new compatibility fixtures.

**Run:** Vision remote contract/worker/Discord tests; Core new routine and existing browser/phone tests with real PostgreSQL opt-in; client full tests, TypeScript and production build. Repeat the B4 crash suite if execution/journal behavior changed.

### Task 8 — Promote routines and decide future expansion

**Files:** Supported catalog, pilot audit, operations guide and enabled routine registry/config only after acceptance.

**Consumes:** Baseline, routine/adapter tests and actual routine outcomes. **Produces:** individually enabled routines with documented limits and a tested rollback.

- [ ] For each exact routine version, collect at least ten live attempts over three distinct days, at least 90% owner review coverage, at least 90% owner-reviewed success, and zero observed authority violation or automatic duplicate consequential effect. Report small-sample limits; this is a personal-use threshold, not a population reliability claim.
- [ ] Compare correction rate, median latency and estimated tokens/cost with the same workload family from baseline. Proposed value target: at least 20% lower median model-call/token use or clearly fewer owner corrections, without lower success; if not met, keep free-form browsing and do not promote a redundant routine.
- [ ] Prove disable, Stop, handoff and previous-version behavior. A previous version is re-enabled only if its supported-site acceptance is still valid; do not silently downgrade running tasks.
- [ ] Publish each routine's supported sites/inputs/actions, provider-data disclosure, tested channels, last validation date, known limitations and measured sample size.
- [ ] Record the expansion decisions below. Build a separate concrete plan only if its entry condition is met.

| Candidate | B5 default | Evidence required before a separate implementation |
|---|---|---|
| Screenshot reasoning or coordinate actions | Deferred | Repeated DOM limitation, explicitly scoped screenshot consent/redaction, measured target accuracy and unchanged action grants. |
| Installed Chrome/Edge | Deferred | A required site fails supported Chromium; isolated owned profile and the same browser/recovery suite pass. |
| Attach existing everyday tabs | Deferred | A concrete need that cannot use the dedicated browser, explicit tab/account selection and ownership/cleanup design. |
| Scheduled browser jobs | Deferred | Separately approved trigger/expiry/credential model, stop controls and uncertainty handling; manual-start B5 is insufficient. |
| MCP adapter | Deferred | A second approved client needs the browser interface; expose only typed broker operations and run the same authority suite. MCP is not needed to store routines. |
| Payments, account/security changes, bulk posting | Outside B5 | A separate product capability and site-specific effect verification. |

## 8. Acceptance matrix and rollback

| Requirement | Primary proof |
|---|---|
| Seven-day real-use measurement | Task 3 dated workload and owner-verdict report. |
| Tokens/cost accurately labeled | Task 1 retry/malformed/unknown/model tests and provider-specific price-table check. |
| No raw task data in metrics | Tasks 1–2 sentinel inspection of SQLite/export/logs, retention and clear test. |
| Only proven routines enabled | Tasks 3/8 baseline and version-specific live promotion records. |
| No broader grants/provider consent | Tasks 4/5 restrictive-policy and current-approval tests; Task 7 old/new authority tests. |
| Verifiable completion | Task 4 trusted verifiers plus Task 5 real browser fixtures and owner correctness reviews. |
| Measured site expansion | Task 6 failure evidence and paired read-only fixture results, or explicit no-adapter decision. |
| Working remote routine ownership | Task 7 real PostgreSQL, staging and physical-device evidence. |
| Stop/handoff/rollback per routine | Tasks 5/8 dispatch barrier, restart/unknown and disable demonstration. |
| Selective future capabilities | Task 8 documented evaluate/defer decisions; no silent bundle of new privileges. |

Rollback order: disable the affected routine version; stop its queued/active tasks and invalidate pending proposals; confirm terminal/unknown state; retain only existing bounded action-journal evidence; erase transient invocation inputs according to source rules. Disable metrics collection separately if necessary; this must not interfere with Stop or the journal. An already completed external effect is handled by its site/user workflow, not “rolled back” by changing a manifest.

## 9. Recommended next implementation slice

Implement **Tasks 0–2 only** first: readiness record, truthful per-call/task measurements, privacy-limited storage, feedback and scorecards. Keep routine definitions as disabled candidates while collecting the baseline. This slice supplies the evidence that decides which routines and site adapters are worth building, and can be reviewed without pretending the pilot has already happened.

Planning is complete when this document covers each B5 roadmap item and exact interfaces/ownership are consistent. Implementation is complete only for the tasks actually built and checked; B5 acceptance additionally requires elapsed live evidence and the release conditions above.
