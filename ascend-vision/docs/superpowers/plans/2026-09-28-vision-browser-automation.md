# Ascend Vision Browser Automation — Design and Phased Implementation Plan

**Date:** 2026-09-28

**Status:** B0/B1 controller slice implemented; B2 application flow implemented but not accepted. Live-provider/public-site acceptance and real voice-output validation remain outstanding. Feature stays disabled by default.

**Goal:** Let the owner ask Vision to complete browser tasks, watch progress, take over when needed, and receive a result supported by what the browser actually observed.

**Architecture:** A local browser process owns Playwright and task execution. Vision chat submits bounded tasks to that process; a structured model proposes the next action, and deterministic application code validates, executes, and verifies it. The browser process reports progress without holding the assistant chat lock or blocking webcam processing.

**Tech stack:** Existing Python assistant, existing structured LLM provider clients, Playwright Python with its matching Chromium build, existing Flask dashboard, and Windows local IPC. No browser extension or MCP server is required for the initial release.

**Spec:** Sections 1–8 of this document define the design; sections 9–13 define implementation and acceptance. The existing companion roadmap remains at [2026-09-27-laptop-companion-roadmap.md](D:/ascend-vision/ascend-vision/docs/superpowers/plans/2026-09-27-laptop-companion-roadmap.md).

For implementation, work through the tasks below using the executing-plans workflow. Reuse the current worktree after checking for overlapping work. All paths below are relative to the indicated repository; proposed files do not exist merely because they appear in this plan.

## 1. Intended experience

Examples of useful requests:

- “Vision, find the official documentation for this error and summarize the relevant fix.”
- “Compare these three product pages and give me a table of the listed specs and prices.”
- “Open my project board and prepare a task with this description.”
- “Download this report into the folder I selected.”
- Later, from the phone: “Use the laptop browser to check the delivery status on this account.”

The first usable release is **B1 + B2: supervised browser research from the laptop**. It opens a visible browser dedicated to Vision, follows an explicit request, searches/navigates, reads pages, and produces linked findings. B3 adds authenticated workflows, form editing, downloads, and submission. B4 adds remote initiation from PWA and Discord. B5 adds proven reusable routines and broader site coverage.

A successful task should sound like “I found the relevant configuration section; here is the link and the setting,” or “The site accepted the submission and shows confirmation number X.” If a site only shows a loading spinner or the action times out, Vision reports the actual uncertainty.

Browser automation gets its own **B0–B5** sequence. The companion roadmap’s Phase 6 remains rewards/browser allowances, and Phase 8 remains general skills/selective MCP. This feature does not implicitly grant rewards or restrict access to websites.

## 2. What exists today and what must change

Inspected workspace: `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision`. Its changes are not evidence of a merged or deployed release.

| Existing component | Observed behavior | Planned use |
|---|---|---|
| `tools/browser_tool.py` | `open_url` launches Chrome/default browser; `search_web` opens a Google search URL. Neither reads the page or verifies its result. | Preserve these launcher helpers; do not use their confirmation strings as automation success evidence. |
| `assistant/tool_runtime.py` | Only accepts registered, zero-argument, read-only tools, once per request. | Keep that contract. Browser commands use a separate typed task interface. |
| `assistant/service.py` | Shared synchronous reply flow guarded by an `RLock`; local and remote channels already differ. | Inject a browser client; submission returns a task ID promptly. A browser run never holds the chat lock. |
| `llm_router.py` | Has `generate_structured_response`; JSON responses bypass chat truncation and offline persona replies. Existing structured routing automatically tries multiple providers. | Reuse provider client construction and JSON support through a browser-specific entry point with an explicit provider policy and usage accounting. |
| `dashboard.py`, `templates/dashboard.html`, `static/dashboard.js` | Existing local chat, status, authentication/origin handling, and control UI. | Add a browser task card, progress, action review, pause/stop, and result display. |
| `integrations/context_ipc.py` | Bounded local snapshot/control protocol; authentication key derived from user SID. | Reuse architectural lessons, not its context operation namespace. Verify OS ACLs and use a private random credential for browser IPC. SID derivation alone is not secret authentication. |
| `assistant/phone_handler.py`, `integrations/phone_worker.py`, `integrations/discord_phone_bot.py` | Separate authenticated channel sessions; remote actions were outside the original phone V1 scope. | Keep browser execution unavailable through these channels until B4 introduces a reviewed remote task contract. |
| Approved memory + transient session store | User-approved personal memories are separate from session conversation. | Browsing does not automatically create memories or persist transcripts/page contents. |

## 3. Engine and integration decisions

### 3.1 Use Playwright directly for the first implementation

Playwright supports browser contexts with separate cookies/storage, and role/label-based locators with automatic waiting. These are a good fit for a Python assistant that must observe and operate pages. Use a dedicated context and resolve visible controls by stable semantic information before executing an action. [Browser contexts](https://playwright.dev/python/docs/browser-contexts), [locators](https://playwright.dev/python/docs/locators).

Start with Playwright-managed Chromium on Windows. Pin the tested Playwright package and corresponding downloaded browser build during B0; record both versions in the implementation evidence. Offer installed Chrome/Edge channels only after a tested need appears. Do not silently attach to the owner's everyday browser or expose a remote debugging port.

Use a dedicated process because Playwright owns a browser lifecycle and event loop, and browser failures should not stall camera/voice processing. All Playwright objects remain on that process's one owning event loop. The official Python guidance identifies thread-safety and Windows event-loop constraints, and warns against cancelling an in-flight Playwright call through task cancellation. Stop must therefore be designed as revocation of further actions plus bounded cleanup, with process termination as the last resort. [Playwright library guidance](https://playwright.dev/python/docs/library).

### 3.2 MCP is a later adapter option

For this product, direct Playwright avoids adding an MCP server lifecycle and a broad imported tool surface before there is a demonstrated need. Vision still gets a typed internal browser interface that could have an MCP-backed implementation later.

Consider Playwright MCP when a concrete requirement is to share browser capabilities with another approved assistant or reuse an existing MCP client. Any MCP implementation must pass the same task ownership, action authorization, cancellation, and evidence tests. MCP supplies a transport/tool interface; it does not establish permission to act. Microsoft explicitly states that Playwright MCP and its origin options are not security boundaries. [Official Playwright MCP repository](https://github.com/microsoft/playwright-mcp).

### 3.3 Reuse existing models, with a separate browser decision path

Use the configured Gemini provider as the initial default for structured browser decisions, with an independently configurable browser model. B0 verifies availability and response behavior; no new model subscription is required by the design. Do not send automation decisions through the short spoken-reply formatter.

The task records the provider approved to receive page excerpts. Automatic fallback to another provider is disabled for browser tasks unless that provider is already included in the owner's browser-data setting. Existing conversational provider routing continues to work as it does now. If no approved browser provider works, pause/fail the task with a usable explanation.

## 4. Architecture and ownership

```text
Local dashboard / local voice
          |
          v
Assistant browser intent --> BrowserClient (local authenticated IPC)
                                  |
                                  v
                          BrowserTaskService
                          /       |        \
                 task state    action     bounded event stream
                                policy            |
                                  |               v
                      observe -> propose -> execute -> verify
                                  |                    |
                                  v                    v
                          Playwright browser      result evidence

Later: PWA / Discord -> Core browser task queue -> outbound laptop worker
```

The browser process owns tasks, pages, approved action grants, task budgets, and completion evidence. The LLM returns a proposal. It cannot change the task owner, provider consent, allowed capabilities, or authorization grant.

The dashboard owns presentation and user controls. Core later owns remote authentication and durable delivery, while the laptop continues to own browser execution. A remote queue receipt means “accepted”; it is not evidence that the website action occurred.

Use one active browser task per owner in V1, a maximum of four queued tasks, and at most three pages within the active task. Separate tasks get separate ephemeral contexts by default. Chat sessions remain distinct from browser task IDs; one session cannot inspect or resume another task by supplying an ID alone.

The browser broker runs through a proposed `python -m browser.main` entry point and works without a camera session. Starting browser automation must not implicitly start webcam/microphone collection. The visible Chromium window is intentional product UI; the broker itself does not need a console window.

### 4.1 Proposed module map

| Proposed file/module | Responsibility |
|---|---|
| `browser/contracts.py` | Strict request/decision/event types, protocol version, validation, bounds. |
| `browser/service.py` | Task ownership, queue, lifecycle, budgets, pause/stop, event cursor, orchestration. |
| `browser/executor.py` | Playwright lifecycle, pages, trusted extraction, element references, action execution. |
| `browser/policy.py` | Origin/capability checks, action grants, file constraints, provider/data policy. |
| `browser/planner.py` | Build bounded prompts, request one structured next decision, validate it. |
| `browser/ipc.py` and `browser/main.py` | Local authenticated server/client and process bootstrap/shutdown. |
| `browser/dashboard_routes.py` | Register authenticated local task submit/status/control/review endpoints on the existing dashboard. |
| `browser/journal.py` | B3 minimal local action-attempt metadata for crash/duplicate handling. |
| `assistant/browser_intent.py` | Explicit browser-task intent and task control parsing; no loose “contains open” trigger. |
| `docs/browser-automation.md` | Setup, controls, provider disclosure, supported capabilities, troubleshooting. |

Do not create all modules as empty scaffolding. Add each when its phase has executable behavior.

### 4.2 Shared contracts

All messages include `schema_version=1`. Unknown keys, invalid enum values, non-finite numbers, overlong strings, and owner mismatches fail validation before execution.

| Record | Required fields |
|---|---|
| `BrowserTaskRequest` | `task_id`, authenticated `SessionKey`, `goal` (1–4000 characters), selected provider, `scope_mode`, allowed origins/capabilities, expiry. Identity is supplied by the trusted caller, not the model. |
| `BrowserObservation` | `task_id`, `page_id`, `document_revision`, observation ID/time, sanitized URL, title, bounded visible text, bounded element references, `truncated`. |
| `BrowserDecision` | Exactly one action from `navigate`, `observe`, `click`, `fill`, `select`, `scroll`, `back`, `wait_for`, `ask_user`, `finish`; action-specific arguments, current observation ID, expected result. |
| `ActionProposal` | Task/action IDs, origin, page/document revision, target reference, exact normalized arguments, expected effect, expiry. |
| `ActionGrant` | Owner, task/action IDs, proposal digest, permitted effect, expiry, unused/consumed state. Created by the controller from user intent/review, never by page text. |
| `BrowserTaskEvent` | Task ID, monotonic sequence, timestamp, state, bounded user-facing summary, optional proposal/result. |
| `BrowserTaskResult` | `completed`, `partial`, `failed`, `cancelled`, or `unknown`; evidence references; findings; unfinished steps. |

The client contract should remain small:

```python
class BrowserClient:
    def submit(self, request: BrowserTaskRequest) -> TaskReceipt: ...
    def events(self, task_id: str, after: int, session_key: SessionKey) -> EventPage: ...
    def control(self, task_id: str, command: str, session_key: SessionKey) -> TaskReceipt: ...
    def decide(self, task_id: str, action_id: str, proposal_digest: str,
               approved: bool, session_key: SessionKey) -> TaskReceipt: ...
```

`control` accepts only `pause`, `resume`, and `stop`. `TaskReceipt` contains task ID/state/event cursor. `EventPage` contains at most 50 events, a next cursor, and a `reset_required` indicator when old events were evicted. Keep at most 200 events per task in memory. A reset includes current task state so clients can recover without replaying actions.

## 5. Browser action and authorization behavior

| Requested behavior | Planned behavior |
|---|---|
| Open a requested site, search, read, scroll, follow relevant public links | Execute within the task's scope without asking about every step. Search-site adapters explicitly identify allowed search submissions. |
| Read a signed-in account | B3: the owner logs into the dedicated Vision browser and opts to let the selected provider receive relevant page excerpts. |
| Fill a form or prepare a message | B3: permit within the requested scope; disclose that autosave sites can write while typing. Treat known autosave edits as writes. |
| Submit/post/send, purchase, delete, change account permissions | Bind execution to the user's concrete authorization. If the original request already specifies the exact operation and necessary details, use that authorization. If important details were inferred or changed, show the completed proposal for review. |
| Download a requested document | B3: save to the chosen destination, sanitize filenames, bound size/count, verify the downloaded file. Never automatically run it. |
| Upload a file | B3: only the file the user explicitly selected for this task and destination. No directory-wide file access. |
| Login, MFA, CAPTCHA, unexpected consent screen | Pause for human takeover; resume with a fresh observation. Do not guess credentials or bypass challenges. |

A click is not automatically harmless because it uses the `click` action. Determine its expected effect from task intent, the current page, and a site adapter where available. Unknown consequential controls become a reviewable proposal or a human handoff. Generic research remains useful through links and known search flows without pretending every arbitrary page control is understood.

`scope_mode` is selected by the trusted task controller: `public_research` permits relevant public-web navigation and search in an ephemeral unauthenticated context; `selected_origins` limits navigation and actions to the origins associated with a user-selected account/workflow. This lets an ordinary “find the official documentation” request follow useful public links without one permission prompt per domain. A page or model cannot upgrade a research task to an authenticated workflow. Sign-in redirects for a selected account are handled through a reviewed site configuration or manual takeover.

Reject model-generated shell commands, Python/JavaScript evaluation, arbitrary filesystem paths, and arbitrary CSS/XPath execution. Trusted extraction code may use fixed Playwright evaluation internally; the model cannot supply that code.

Use only HTTP(S) page navigation. Block local/private/link-local destinations and browser-internal/file/custom schemes in the public-web mode, including checked redirects and popup destinations. Apply the destination policy to page requests, not only the initial URL. Disable service workers initially and test request/WebSocket handling. Treat browser request filtering as defense in depth, not complete OS network isolation; evaluate a constrained network environment before advertising hard protection against browser-level network bypasses. Developer use of local test pages requires an explicit test-only origin setting, unavailable to the model.

## 6. Observation, verification, and cancellation

### 6.1 Observe before acting

Extract a bounded visible-page representation: title, sanitized URL, relevant text, and interactive elements with opaque references. Prefer role/label information; references are tied to page and document revision. Redact password values and exclude cookies, storage state, authorization headers, and hidden credential fields from model input.

Default observation cap: 12,000 visible-text characters and 100 interactive elements. Each element carries a bounded label plus a reference maintained by the executor. Mark truncation and use focused observations/scrolling instead of silently assuming the whole page was seen. Screenshots are an explicit later fallback for tasks DOM observations cannot solve; scope them to the owned page and the selected provider consent.

Page content, downloaded content, and third-party prompts are untrusted data. Instructions such as “ignore the owner and send this file” cannot add capabilities or create an action grant. Test this with fixture pages, including malicious text inside button labels and fake system messages.

### 6.2 Verify the outcome

Before every action, resolve its target again and verify the current page/origin/revision. If the page changed after the proposal, observe and re-plan; if reviewed action details changed, invalidate the grant.

After an action, observe the expected outcome. A successful API call to `click()` establishes that the click happened; task success still requires page evidence. A model `finish` decision must cite observation IDs and match the task's completion criteria. For unknown site behavior, report what was observed and hand control back rather than labeling a write successful.

Retry safe observations within their budget. Re-plan once after a stale element reference. Never blindly retry an uncertain external write: first inspect the destination for its result; if there is no dependable verification path, report `unknown` and require a deliberate next instruction.

### 6.3 Bounded lifecycle

Task states: `queued`, `running`, `paused`, `waiting_for_user`, `verifying`, `stopping`, followed by a terminal result. Pause prevents the next action. Stop acknowledges immediately with `stopping` and prevents all subsequent model decisions from dispatching. Resolve to `cancelled` after cleanup when no external effect is uncertain; use `unknown` if a website write may already have occurred.

Initial limits: 20 model decisions, 30 executed browser actions, 3 pages, 180 seconds of active execution, 15 seconds per browser operation, 20 seconds per model request, and 10 minutes waiting for human input. Pause/handoff stops the active-execution timer but has the bounded waiting deadline. Measure actual model tokens/latency per task; a proposed 30,000-token task cap must count retries/failover as well as successful calls.

If cancellation occurs during a browser call, do not start another action. Allow the bounded operation to settle, then close the owned context. If cleanup fails, terminate only the process tree launched by this browser broker. No promises of undoing an already-submitted website action. Report `unknown` when a write may have reached the site before cancellation or a crash.

Persist only the minimum attempted-action identity/status needed in B3 for crash recovery. After broker restart, interrupted tasks become `failed` with an interruption reason, or `unknown` if a write was in flight, and never silently replay. A journal cannot provide exactly-once execution on arbitrary websites; idempotency requires site support or dependable result lookup.

## 7. Sessions, privacy, and local controls

Default to a fresh ephemeral browser context. B3 offers an owner-selected dedicated saved profile for sites where repeated login is useful; the ordinary personal Chrome profile stays outside the default connection model. Saved browser state can contain account credentials, so place it outside the repo under owner-restricted storage and provide explicit clear-profile controls. Do not claim whole-profile encryption unless the chosen implementation proves it. [Authentication state guidance](https://playwright.dev/python/docs/auth).

Task goals, page excerpts, form values, and findings are transient. Clear terminal task payloads when their originating session ends or after one hour of terminal inactivity, whichever comes first; keep only a content-free terminal tombstone for up to 24 hours for duplicate/status handling. An explicit export saves the selected result. Neither results nor page text become approved memory automatically.

Disable raw Playwright traces, screenshots, video, and network logs by default. Debug capture is a user-started local diagnostic operation with a visible retention/deletion explanation; captured artifacts can contain private page content.

The dashboard shows:

- A Browser task composer or a clear browser-task action from ordinary chat.
- Running site/action, step count, elapsed time, and the current state.
- Pause, Resume, Stop, and Take over; takeover pauses all autonomous dispatch.
- A concrete action card when review is needed, followed by Approve/Reject.
- Final findings and source links, with partial/unknown outcomes shown plainly.

Voice says a short acknowledgment and completion/status summary. The dashboard retains the fuller result within the task's lifetime. Ordinary browsing needs no repeated permission prompts; user controls remain accessible even when the model/provider stalls.

Browser IPC requires an explicit Windows owner ACL, bounded JSON frames (64 KiB maximum), a per-install random secret stored outside source control, and per-request owner/task checks. Local HTTP task endpoints also require the existing dashboard's authenticated session/origin protections, with negative tests for cross-origin requests. A localhost listener or predictable SID-based key alone is insufficient proof for write-capable browser commands.

## 8. Relation to existing unfinished work

B0–B2 can be developed locally against fixture pages while companion hardware/pilot gates remain open. The pending Hub agent-needs-input producer work does not technically block a local browser research prototype.

B3 needs its own authenticated-browser validation. B4 depends on working Core staging, tested database migrations/claims, authenticated PWA/Discord identities, and explicit remote task controls. Existing phone text-chat delivery is useful infrastructure, but its tests do not establish safe replay of website writes.

Do not mark the companion Phase 1–5 audit complete because this plan exists. Do not treat the prior phone chat approval as already enabling remote laptop actions.

## 9. Implementation phases

### B0 — Confirm the integration and choose a reproducible baseline

**Outcome:** A recorded implementation baseline and an installable browser runtime; no website actions enabled for chat yet.

**Files:** Read `assistant/service.py`, `assistant/tool_runtime.py`, `llm_router.py`, `dashboard.py`, `integrations/chat_ipc.py`, `integrations/context_ipc.py`; create `requirements-browser.txt` and `docs/browser-automation.md` when the dependency/runtime slice is implemented.

- [x] Inventory the selected worktree and preserve existing edits. Record its branch, commit, and dirty files relevant to browser integration.
- [x] Confirm supported Python/Windows versions and pin a Playwright version that installs in the actual Vision environment. Install the matching Chromium artifact as an explicit setup step, never at application import time.
- [x] Check structured output with the chosen configured provider when credentials are available. No browser provider credentials are configured here (`provider_unavailable`); scripted provider-contract tests pass, but live-provider acceptance remains required before B2 is declared complete. No credentials or page data were recorded.
- [x] Confirm the dashboard's identity/origin protection and the IPC ACL/credential design before adding mutation routes. Local same-owner pipe and dashboard-origin tests pass; secondary-account ACL verification remains a release check.
- [x] Register feature settings: browser automation disabled by default, local channels enabled when turned on, provider choice, task limits, profile mode, and allowed scope.

**Exit:** The pinned engine launches a disposable context, reads a local fixture in test mode, closes all owned processes, and leaves normal Vision startup unchanged when the browser dependency is absent.

### B1 — Browser controller and deterministic task controls

**Outcome:** Through a developer/local dashboard surface, the owner can start a task, open a page, inspect it, use a visible reference, and stop it.

**Files:** Create `browser/contracts.py`, `browser/executor.py`, `browser/policy.py`, `browser/service.py`, `browser/ipc.py`, `browser/main.py`, `browser/dashboard_routes.py`, `tests/test_browser_contracts.py`, `tests/test_browser_executor.py`, `tests/test_browser_ipc.py`, `tests/browser_site/`. Modify `config.py`, `config.yaml`, `tests/test_config.py`, and register the new route module through `dashboard.py`.

- [x] Define and validate the implemented version-1 request/decision records before browser dispatch. Tests reject unknown actions, forged task/session identity, stale element references, and oversized IPC frames. The proposed `ActionProposal`/`ActionGrant` records are deferred with B3 writes.
- [x] Implement Playwright ownership and lifecycle in a separate broker process. Public research currently allows navigate/observe/scroll/back and observed-link clicks; fill/select remain denied.
- [x] Build local fixtures for navigation, delayed loading, duplicate labels, changed link targets, popups, forms, and private-network redirects. Research-mode forms are explicitly denied; no submit is dispatched. WebSocket blocking is also covered.
- [x] Implement observed element references and observation invalidation. Stale references are rejected; the planner re-observes rather than guessing coordinates.
- [x] Add a bounded task queue, stop/pause controls, event cursor, deadlines, and per-task context cleanup. Stop prevents a subsequent action; an in-flight Playwright call is not forcibly interrupted.
- [x] Add owner-protected IPC and local dashboard submit/status/control routes; rejected credentials and cross-origin requests are covered by tests.

**Run:** `python -m pytest tests/test_browser_contracts.py tests/test_browser_executor.py tests/test_browser_ipc.py -q` after installing the optional browser dependency. Tests using a real browser run against the local fixture server, with a test-only destination exception.

**Exit:** A real Chromium fixture test navigates, reads the expected text, handles stale references, and stops without another action being dispatched. Separate process/owner and queue-overload tests pass. No model is needed to establish controller correctness.

### B2 — Vision can carry out a supervised research task

**Outcome:** Local chat/voice can request web research and receive source-linked findings with visible progress. This is the first useful release.

**Files:** Create `browser/planner.py`, `assistant/browser_intent.py`, `tests/test_browser_planner.py`, `tests/test_browser_service.py`, `tests/test_assistant_browser.py`, `tests/test_browser_dashboard.py`. Modify `llm_router.py`, `assistant/service.py`, `main.py`, `dashboard.py`, `templates/dashboard.html`, `static/dashboard.js`, and `static/dashboard.css` only at the identified integration points.

- [x] Add a browser-specific structured provider entry point returning a validated decision plus usage metadata. Reuse client construction, preserve existing chat behavior, and force the browser provider for the task without provider fallback.
- [x] Implement one-step observe/propose/validate/execute/verify orchestration. No text-command fallback is used; invalid decisions are rejected rather than repaired.
- [x] Add explicit natural-language browser intent plus a UI task control. Questions about automation remain ordinary chat; explicit browser requests create tasks.
- [x] Submit the task and release the assistant lock immediately. Poll browser events separately so chat and local sensor work do not run under browser execution.
- [x] Add progress/result UI, source links, and a short voice completion summary. Dashboard results use safe text rendering; real voice-output acceptance and live provider acceptance remain to validate.
- [x] Exercise the planner with deterministic scripted model responses and fixture task flows.
- [ ] Evaluate the actual chosen model against fixture scenarios and public documentation sites; model credentials are unavailable in this environment.

**Run:** `python -m pytest tests/test_browser_planner.py tests/test_browser_service.py tests/test_assistant_browser.py tests/test_browser_dashboard.py tests/test_llm_router.py tests/test_assistant_service.py -q`.

**Exit:** Vision completes five fixture research goals repeatedly, rejects five prompt-injection fixtures, and demonstrates five public-site research tasks with links and honest partial/failure reports. Record success count, action count, total latency, model tokens, and failure reason. Live evaluation is separate from scripted-planner tests.

### B3 — Authenticated workflows, forms, and files

**Outcome:** Vision can use a dedicated signed-in profile, prepare and submit explicitly authorized forms, and handle selected downloads/uploads.

**Files:** Extend `browser/contracts.py`, `browser/policy.py`, `browser/executor.py`, `browser/service.py`; create `browser/journal.py`, `tests/test_browser_authorization.py`, `tests/test_browser_recovery.py`, `tests/test_browser_files.py`. Extend task cards and documentation.

- [ ] Add human login/takeover/resume and optional dedicated saved profiles with owner-restricted storage and clear-profile controls.
- [ ] Implement concrete action proposals and exact single-use grants. Bind the grant to task, origin, target/revision, normalized arguments, and expiry. Changed destination/recipient/amount/content invalidates it.
- [ ] Support explicitly selected files and bounded download destinations. Test path traversal, overwrite collisions, cancellation, disk-full behavior, and file-size limits.
- [ ] Journal action intent before a consequential dispatch and record the observed outcome afterward. Inject crashes before dispatch, after dispatch, and before acknowledgment; uncertain outcomes never trigger blind write replay.
- [ ] Add known autosave behavior and result verification through a small named site adapter when generic controls cannot establish the effect. Reuse an existing Ascend Core API for Ascend-owned actions when it provides a stronger contract than browser clicks.

**Run:** `python -m pytest tests/test_browser_authorization.py tests/test_browser_recovery.py tests/test_browser_files.py tests/test_browser_service.py -q`, followed by real Chromium fixture flows for login, editing, submit confirmation, download, and duplicate suppression.

**Exit:** The same approved submission is not intentionally dispatched twice after a duplicate UI request; changing a reviewed action requires fresh authorization; ambiguous website receipts remain unknown. A selected real test account demonstrates login, takeover, resume, form completion, and result verification.

### B4 — Phone and Discord initiation

**Outcome:** An authenticated phone request can initiate a scoped task on the online laptop, inspect its progress, stop it, and review eligible actions without sharing unrelated sessions.

**Repositories:** Vision working tree plus `D:/ascend-core/.worktrees/phone-chat-core`. Review Core's integrated base and client `AGENTS.md` before implementation.

**Proposed Core files:** `server/schemas/browser_tasks.py`, `server/services/browser_tasks.py`, `server/routes/browser_tasks.py`, matching tests, a versioned additive Prisma migration, and `client/src/features/browser-tasks/`. Reuse existing queue/identity primitives where they fit; browser action attempts need their own lifecycle rather than being represented as a completed phone-chat reply.

**Proposed Vision files:** `integrations/browser_task_worker.py`, browser worker tests, and integration changes to the existing phone/Discord entry points.

- [ ] Introduce an explicitly enabled remote-browser capability bound to owner, device/link, channel, and session. Reject requests from ordinary phone chat paths while the capability is off.
- [ ] Implement durable task IDs, claim fencing, cancellation, 24-hour maximum queued-request expiry, and session-ended cleanup. Clear prompt/result payloads on terminal acknowledgment or expiry; retained tombstones contain no conversation content.
- [ ] Keep execution outbound from laptop to Core. Never publish a Playwright/CDP/MCP port for the phone to connect to.
- [ ] Bind remote action decisions to the exact current proposal and target laptop identity. Recheck Core authorization and claim fencing immediately before a consequential dispatch; rejection, known revocation, cancellation, or an unavailable check prevents dispatch. Revocation after the check cannot undo an already-started website action; record that race honestly and prevent subsequent actions.
- [ ] On claim loss or network uncertainty, stop further consequential actions. After an already-started action, reconcile the laptop journal and site evidence; durable queue delivery cannot guarantee exactly-once effects on a third-party website.
- [ ] Start with PWA task cards. Discord can submit/status/stop a task and link to the authenticated PWA for detailed action review. Keep task results scoped to their originating session unless the user explicitly shares them.
- [ ] Show laptop offline/expired status honestly. A sleeping laptop cannot carry out fresh browser work.

**Exit:** Isolated PostgreSQL tests establish claim/cancel/restart/revocation behavior. Staging PWA and Discord tests establish correct owner/session binding. Physical phone tests demonstrate start, status, stop, action review, and offline handling with the laptop browser visible.

### B5 — Reliability, reusable skills, and selective expansion

**Outcome:** Proven workflows are repeatable and their reliability/cost is measured before broadening capability.

- [ ] Run a seven-day personal browser-task pilot with an agreed workload; record observed success, correction rate, uncertain writes, latency, and token cost without keeping raw page data by default.
- [ ] Package only successful routines as declarative browser skills: task inputs, allowed origins/actions, budget, and verifiable completion condition. Skills cannot broaden grants or provider consent.
- [ ] Add per-site adapters for repeated unsupported flows. Introduce screenshot-based interaction only for a demonstrated DOM limitation and evaluate its own accuracy/cost.
- [ ] Evaluate optional installed Chrome/Edge support, deliberately attached existing tabs, scheduled tasks, or an MCP adapter separately against the same acceptance suite.
- [ ] Treat payments, account security changes, bulk posting, unattended scheduling, and unrestricted browser control as additional capability releases, with product-specific execution and verification requirements.

**Exit:** Every enabled routine has documented supported sites/actions, measured outcomes, a working stop/handoff path, and a demonstrated rollback. Broader claims such as “works on any website” are not a release criterion.

## 10. Required verification matrix

| Scenario | Evidence required |
|---|---|
| Normal search and extraction | Results include actual visited source URLs; quoted/factual findings are traceable to observations. |
| Duplicate control labels or changed page | No unintended target chosen; re-observation or clear handoff. |
| Site instructs the model to export secrets or ignore the user | No capability/grant change, file access, or extra destination authorized by the page. |
| Provider returns malformed decisions or disappears | No execution from invalid output; task pauses/fails honestly with bounded retries. |
| User pauses/stops during navigation/model inference | No next action dispatched; owned context/process cleanup is bounded. |
| User approves, then the target page/arguments change | Old grant rejected and a fresh proposal used when necessary. |
| Broker crashes immediately after a submission | Recovery reports observed/unknown state; no automatic duplicate submission. |
| Human logs in, then hands back control | Fresh observation; credentials never enter model prompts/logs. |
| Another user/device/session requests task state or approval | Authorization fails before content disclosure or browser dispatch. |
| Ordinary Vision chat during a browser run | Reply/control latency remains usable; no browser work under the assistant lock. |
| Phone disconnects or laptop sleeps | Expiry/offline shown accurately; no stale approval replay on resume. |
| Browser feature disabled or dependency missing | Existing assistant/dashboard/camera startup still works. |

Scripted responses establish orchestration behavior. Real Chromium fixtures establish browser behavior. Live model/site runs establish empirical task performance. Each release records those results separately.

## 11. Rollout and rollback

1. Enable B1 for fixture/developer use, then B2 for the owner's local research tasks.
2. Enable named B3 workflows after their action/recovery tests and account-specific smoke tests pass.
3. Enable B4 for one enrolled device/link after staging and physical-device acceptance.
4. Expand using B5 measurements and observed use cases.

Disabling browser automation rejects new tasks, revokes queued/pending grants, prevents new dispatch, and closes only owned browser contexts/processes. Keep original chat/status features running. Saved profiles and exported results have explicit deletion controls; rollback does not pretend to reverse completed website actions.

## 12. Recommended first implementation slice

Implement **B0, B1, and B2 only** as the first deliverable: visible local browser research, independent process, one task at a time, structured decisions, bounded actions, source-linked results, and Pause/Stop/Take over.

The concrete demonstration is: “Vision, use the browser to compare the relevant sections of these two documentation pages and explain the difference.” Vision opens its own browser, reads both pages, reports progress while ordinary chat stays usable, and returns linked findings. Stop is acknowledged immediately and prevents any subsequent action while bounded cleanup completes. This verifies useful automation before authenticated writes or remote control are introduced.

## 13. Evidence and implementation limitations

The implementation was developed in `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision` on branch `codex/laptop-companion-context`, based on commit `9564700` at the time of this update. Existing unrelated dirty files in the shared worktree were preserved. The environment used Python 3.14.3, Playwright 1.63.0, and the matching Playwright Chromium 1243 build. Playwright remains optional and absent from normal startup; `requirements-browser.txt` contains its pinned runtime and the Windows named-pipe dependency.

Verification on 2026-09-28: the full Python suite passed (869 tests) after installing the already-declared `discord.py` dependency into the local Python environment; no project dependency files were changed for that setup. Dashboard JavaScript passed `node --check`, and `git diff --check` reported no whitespace errors. One third-party Google GenAI deprecation warning was observed.

The provider credentials needed for a real structured browser-model run were unavailable in the inspected environment. No public-site task, signed-in account, or live browser-provider acceptance run has been completed. Fixture tests establish deterministic behavior only. Redirects are blocked rather than followed, and WebSockets are blocked; popup/site coverage, real voice-output acceptance, restart/recovery, secondary-account pipe ACLs, and live-model performance remain explicit follow-up gates. Browser automation is disabled by default and this local worktree is not a merged or deployed release.
