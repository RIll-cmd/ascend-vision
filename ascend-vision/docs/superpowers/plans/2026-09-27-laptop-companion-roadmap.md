# Ascend Vision Laptop and Phone Companion — Phased Implementation Plan

> **For agentic workers:** When implementation is explicitly requested, use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to deliver one phase in reviewable tasks. Checkboxes track future work, not work completed while writing this document. This document authorizes no implementation or deployment by itself.

**Date:** 2026-09-27  
**Status:** Detailed proposal for user review; documentation only.  
**Goal:** Make Ascend Vision a laptop-first companion that understands observable activity, converses naturally, initiates useful interventions, and remains accessible from Android and iOS.  
**Architecture:** One laptop runtime owns live observations and current context. Ascend Core remains authoritative for account data, missions, progression, and reported agent status; it relays a minimal, expiring context snapshot to authorized phone and Discord clients. A deterministic policy chooses eligible interventions, while the shared assistant supplies grounded explanations and personality.  
**Tech stack:** Reuse Vision's Python runtime, existing CV/audio components, `AssistantService`, SQLite, and typed tools; Core's FastAPI/Prisma/PostgreSQL backend; and the existing Next.js PWA. Add native Android/iOS components only in the optional mobile phases. No new event broker, vector database, or agent framework is required for the initial release.  
**Spec:** The product requirements, architecture, contracts, and data policy in sections 1–9 are the embedded design specification for phases 0–8.  
**Reference brief:** User-provided Luna/Shinji analysis, received 2026-09-27. Its text matches the reference received in the preceding planning turn.  
**Research companion:** [Primary-source research and limitations](../../research/2026-09-27-laptop-companion-references.md).

## Global constraints

- Initial sensor platform: the user's Windows laptop, with its webcam, microphone, speakers, and explicitly enabled desktop signals.
- Phone access: Android and iOS through the existing PWA/Discord foundations; native telemetry is optional and separately delivered.
- Scope excludes smart-home equipment, apartment-wide tracking, wearable punishment devices, and physical enforcement.
- Preserve separate desktop/voice, PWA, and Discord conversation sessions. Shared current context must not copy transcripts between channels.
- Preserve user-approved long-term memory. Newly observed activity must not silently become a personal memory.
- Any new activity-history retention requires a separate opt-in. Existing storage behavior must be audited before making product-wide retention claims.
- Raw webcam footage, microphone recordings, and screenshots are not a new permanent archive.
- Every factual context field carries provenance and freshness; unknown, disabled, stale, and unavailable are first-class outcomes.
- A laptop that is asleep, locked, disconnected, or denied sensor access must not be reported as currently observing the user.
- New companion behavior must coordinate with existing warnings and automation paths; the same observation must not cause duplicate narration or additional penalties.
- The language model cannot determine permissions, invent task completion, award XP directly, or bypass Core's action contracts.
- Keep implementation, deployment, and hardware-purchase decisions separate from approval of this roadmap.

## Contents

1. Product direction and intended experience
2. Reference interpretation and feature selection
3. Existing Ascend foundations and integration gaps
4. Architecture and ownership
5. Observation and current-context contracts
6. Memory, privacy, and retention
7. Intervention and personality design
8. Laptop and mobile capability boundaries
9. File ownership and proposed module map
10. Phases and dependencies
11. Detailed phase plans
12. Evaluation and release evidence
13. Rollout, recovery, and operation
14. Decisions, risks, and deferred scope
15. Source register and planning review

## 1. Product direction and intended experience

### 1.1 What we are building

Ascend Vision should feel like one consistently informed companion across laptop voice, desktop UI, phone chat, and Discord. Its usefulness should come from a reliable, appropriately qualified view of the present situation, timely responses, and explanations in the user's chosen tone. Shared context must not mean shared transcripts: each channel keeps its own conversation history.

The essential loop is:

**Observe → update current context → evaluate a rule → speak or notify → accept the user's response → update context.**

Continuous awareness means lightweight local updates while Vision is running and the relevant feature is enabled. It does not mean continuously sending video, desktop images, or ambient conversations to a large model. Each observation must retain its source, age, and uncertainty so stale or missing sensor data is never presented as live knowledge.

### 1.2 Example experiences

| Situation | Proposed experience | Evidence required |
|---|---|---|
| The user begins a focus session | Vision knows the selected mission, session end, interruption preference, and current sensor availability. | Explicit session selection; valid Core mission if linked. |
| The user leaves the webcam's view | Vision may report that desk presence is not detected. A check-in is eligible only after a configured absence and only during a relevant session. | Healthy camera, calibrated desk region, and sustained absence evidence. Camera failure or occlusion produces an unknown state, not an away claim. |
| The user is watching a tutorial | Vision accepts a temporary correction such as “This is research” and suppresses incompatible distraction prompts. | User declaration with scope and expiry; application category remains a separate fact. |
| An AI agent is working | Vision can answer from Core's fresh status shelf without treating the user's wait as a failure or inferring task completion from an idle state. | Authenticated, fresh, producer-reported agent state; explicit completion evidence for any “finished” claim. |
| An AI agent requires a decision | Vision sends one useful alert to the selected channel. | Explicit needs-input evidence; a generic idle state is insufficient. |
| The user asks about the day | Vision summarizes confirmed missions and, if enabled, tracked activity with coverage gaps. | Core records and consented aggregates; no guessed hours. |
| The laptop sleeps while the user is on a phone | The phone shows when laptop context was last observed and that live context is unavailable. | Snapshot expiry and laptop/runtime availability; a heartbeat must not make old observations fresh. |

### 1.3 First deliverable

The first implementation milestone is **Current Context V1**, preceded by a short integration baseline phase. Its product test is:

> Vision can explain what it currently observes, identify what it cannot know, and correctly apply a temporary user correction without modifying unrelated facts or long-term memory.

Proactive delivery should follow this milestone. Daily scoring, native mobile monitoring, and MCP are later additions.

## 2. Reference interpretation and feature selection

### 2.1 Evidence boundary

The supplied analysis describes a creator's AI called Luna and reconstructs a likely architecture from public-facing demonstrations. Embedded citation placeholders in that document are not independently usable source links. The TikTok reference is [@subject_shinji](https://www.tiktok.com/@subject_shinji).

For this planning pass, the researcher could not reliably retrieve the TikTok profile or independently establish the listed demonstrations from attributable creator sources. This plan therefore treats those descriptions as **user-provided inspiration**, not verified claims about the creator's software, model, implementation, reliability, or data policy. No claim is made that the videos were watched. The primary technical sources in section 15 support platform capabilities, not the creator's architecture.

### 2.2 What to adapt

| Reference concept | Ascend interpretation | Decision | Phase |
|---|---|---|---|
| An AI notices desk departures and returns | Calibrated laptop camera presence transitions, with unknown states when detection is unavailable. | Build. | 1 |
| An AI knows what the user is doing | Combine application category, input-idle state, session intent, and optional screen interpretation. | Build bounded observations; avoid certainty about motivation. | 1–2 |
| The AI speaks first | Rules produce a limited number of explainable, interruptible prompts. | Build after context accuracy. | 3 |
| A consistent character | One identity and configurable tone across channels; factual answers remain useful. | Build on the shared assistant. | 2–3 |
| Work tracking | Report tracked focus-session time and confirmed milestones, with coverage and exclusions. | Build; do not label all desk time productive. | 5 |
| Daily score | Transparent, opt-in goal progress computed from declared rules. | Optional after reliable summaries. | 5 |
| Entertainment rewards | An explicit Core reward ledger and optional browser allowance. | Optional; reversible and independently enabled. | 6 |
| Agent-aware coaching | Use verified AI activity and explicit completion/needs-input events. | Build behind producer-data gates. | 2–3 |
| Arrival greetings | A desk-return greeting when enabled and contextually useful. | Build locally; do not claim home arrival. | 3 |
| Wake/sleep routines | User-entered schedules and check-ins; laptop lock/idle observations remain limited facts. | Build declared routines; no inferred sleep measurement. | 3 / 5 |
| Health or hangover check-ins | Follow up on something the user explicitly said, with expiry and an opt-out. | Optional conversation feature; no diagnostic inference. | 3 / 8 |
| Whole-apartment awareness | Multiple rooms, cameras, home sensors, and smart-home actions. | Excluded from this scope. | — |
| Punishment devices | Physical consequences controlled by the assistant. | Excluded. | — |

### 2.3 Behavioral guardrails and gaps to close

These are product requirements, not a claim that every existing Vision path already meets them. In particular, legacy phone-held feedback can characterize a pickup as distraction, and the companion context model does not measure gaze. Phase 0 must map these existing behaviors; later implementation and release tests must prove that the guardrails apply across every enabled path.

- **Phone use:** A pickup is evidence that the phone was detected, not proof of doomscrolling, distraction, or a failed habit. Coaching requires additional, relevant evidence and an enabled rule.
- **Presence:** Looking away, being outside the camera frame, and losing camera availability are distinct states. The system may report only what its sensors support; camera failure or occlusion must not be described as confirmed absence.
- **Agent status:** An agent reported as idle is not necessarily finished. Use “finished” only when an authoritative completion record confirms it.
- **Waiting:** Waiting for a build or an agent can be a reasonable part of work. Elapsed time alone is not evidence of avoidance or failure and must not trigger criticism.
- **Answer quality:** Personality may shape delivery, but the assistant still answers the user's question using available evidence. If it cannot verify an answer, it says what is unavailable instead of deflecting with “check your dashboard.”
- **Personalization:** Adapt tone only from explicit user preference or correction. A correction to activity or intent changes that context within its stated scope; it does not authorize stronger claims, rewrite other observations, or silently become a lasting personality preference.

## 3. Existing Ascend foundations and integration gaps

### 3.1 Inspection baseline

The workspace has multiple branches and unfinished or unrelated edits. Repository existence is not proof of a production deployment. This roadmap uses these inspected working copies as evidence:

| Project / location | Inspection point | Relevant evidence |
|---|---|---|
| Vision main checkout: `D:/ascend-vision` | `ecf63ff`, with local changes | Camera, audio, session, feedback, and screen-audit foundations. |
| Vision Discord worktree: `D:/ascend-vision/.worktrees/phone-chat-discord` | `9564700` | Shared assistant, approved memories, separate chat sessions, typed status tool, phone worker, Discord adapter. |
| Core phone worktree: `D:/ascend-core/.worktrees/phone-chat-core` | `d388460` | PWA/Discord pairing and relay work, plus the reported Prisma upsert correction. |
| Core Discord worktree: `D:/ascend-core/.worktrees/phone-chat-discord` | `ee62290` | Reviewed pairing API and PWA linking work. |
| Core completion worktree: `D:/ascend-core/.worktrees/status-completion-history` | `2f4ae40` | Explicit operation completion records and shelf completion summaries. |

These are planning observations dated 2026-09-27. Phase 0 must resolve the actual integration base before any future edits. Existing test counts mentioned in earlier chats are historical evidence; this documentation task did not rerun the application suites or certify staging.

The older `ai_assistant_future_features_audit.md` is useful historical context. Its “missing” labels predate several assistant, memory, status, and phone changes and must not be treated as the current release inventory.

### 3.2 Reuse versus new work

| Capability | Foundation inspected | What remains for this roadmap |
|---|---|---|
| Camera observations | Existing capture, detectors, hold state machines, posture/drowsiness/yawn components. | Desk-region calibration, sensor-health reporting, explicit present/away/unknown state. |
| Sessions | `session_manager.py` serializes mode requests and records sessions/events. | Adapt existing modes into current context; distinguish companion mode from session mode. |
| Voice and speech | `voice_listener.py`, `feedback.py`, `speech.py`, and TTS integration. | A coordinated speaking/listening state, interruptible output, and consistent context injection. |
| Screen understanding | `screen_auditor.py` captures and classifies screenshots and can speak directly. | Metadata-first sampling, explicit capture controls, freshness labels, central delivery arbitration. |
| Chat and memory | `assistant/service.py`, `assistant/session_store.py`, `assistant/memory.py`. | A read-only current-context provider usable across independently running processes. |
| Agent status | Core `/api/status/shelf`, Vision `integrations/status_shelf.py`. | Verify runtime credential configuration, compatibility with completion work, and publisher coverage. |
| Credential administration | Core `server/cli/status_read_credentials.py` already provides create/list/revoke. | Use the existing operator workflow against the correct environment; do not invent another credential mechanism. |
| Phone and Discord | Outbound phone worker, PWA, dedicated Discord bot and account linking. | Shared context access, foreground phone voice, and explicitly enabled proactive notifications. |
| Warning/penalty path | `integrations/warning_state_machine.py` can dispatch repeat offenses to Core. | Prevent passive companion observations from unintentionally entering this action path; reconcile existing user-enabled behavior. |
| Missions/progression | Core mission, habit, and automation services. | Narrow context projection; later reward proposals through Core-owned validation and idempotency. |

### 3.3 Important integration findings

1. The desktop runtime and Discord process can construct separate `AssistantService` instances. Shared source code does not give them a shared in-memory view of the world. Context needs an explicit provider and transport.
2. The inspected screen-audit configuration uses a 1,800-second interval. A screen classification from that audit is historical evidence, not a continuous account of the current foreground activity.
3. Existing feedback paths can speak directly. Adding a new proactive loop without coordinating those paths would produce repeated or contradictory messages.
4. The dedicated status-shelf reader credential differs from phone-worker, Discord bridge, and producer credentials. The earlier `/status` screenshot showed unavailable status; this plan does not assume it has since been resolved.
5. Core completion support is present on a separate branch. Integration must preserve the distinction between current state and a completed operation, and verify that Vision reads the fields it intends to narrate.
6. Vision already persists some camera/session events. The new companion's session-only context policy cannot retroactively be described as the retention behavior of every existing table.

## 4. Architecture and ownership

### 4.1 Logical flow

```text
Webcam observations ──────┐
Windows activity ────────┤
User declarations ───────┼─> Context runtime ─> Read-only snapshot ─> Assistant
Optional screen result ──┤         │                                  │
Core mission/agent data ─┘         └─> Intervention policy ─> Delivery controller
                                                                         │
                                                   Desktop speech / UI / phone
                                                                         │
                                                   User correction / snooze
```

The context runtime belongs to the laptop process that owns the observations. The assistant can read it, but model output cannot rewrite facts or authorize actions.

### 4.2 Authorities

| Information | Authority | Other components may do |
|---|---|---|
| Latest webcam/desktop observations | Laptop context runtime | Read an expiring projection; never extend its freshness without new evidence. |
| User-declared focus/break/research intent | Authenticated user interaction | Preserve scope/expiry and show it as a declaration. |
| Current mission, goal, completed mission, XP | Ascend Core | Read or submit a separately authorized proposal. |
| Agent working/idle/blocked/completed | Core shelf backed by producer reports | Explain the state and its timestamp; report unknown when stale. |
| Approved personal facts/preferences | Existing memory store and approval UI | Retrieve relevant approved facts within existing limits. |
| Temporary conversation turns | Channel/session store | Use only in the corresponding channel and session. |
| Delivery receipt or failure | Channel adapter | Record attempted/acknowledged/unknown; do not invent receipt. |

### 4.3 Process boundaries

- **Same-process voice/chat:** call `ContextProvider.read_snapshot()` directly.
- **Separate local dashboard process:** use an owner-restricted Windows named pipe adapter for the same snapshot interface. It is local IPC, with bounded messages and no new internet-facing listener. If an existing IPC facility can provide equivalent request/reply and owner isolation without persisting the snapshot, reuse it after Phase 0 review.
- **Remote PWA or separately hosted Discord:** query a minimal Core-held snapshot using the caller's established owner/device/link identity.
- **Publisher:** the laptop sends latest-state updates outbound. It never grants the phone a direct connection to its camera or a shell.
- **Scheduling:** only one component owns each proactive rule. Remote delivery workers deliver already-approved intents; they do not independently re-evaluate the same rule.
- **Laptop offline:** Core can serve a clearly expired/last-known availability record. Running fresh laptop inference requires the laptop to be online. An always-available cloud assistant would be a separate product decision.

### 4.4 Reliability choices

Use a bounded in-process event queue and a latest-value context store. Start with a queue capacity of 256 events; coalesce repeated sensor samples by source and field. Never drop a user pause or shutdown control behind sensor traffic; handle these on a separate priority control path.

Each source has its own sequence counter and runtime boot identifier. Duplicate events are ignored; older events cannot overwrite newer state from that source. A restarted laptop publishes a new boot identifier and starts observations as unknown until reacquired.

Do not introduce Kafka, Redis, or a distributed workflow engine for this single-owner release. If more than one delivery worker is later required, Core's durable notification records and unique constraints own delivery claims.

## 5. Observation and current-context contracts

The following are proposed contracts. They do not imply these classes, endpoints, or fields already exist.

### 5.1 Observation envelope

| Field | Contract |
|---|---|
| `schemaVersion` | Integer `1`; unsupported versions are rejected. |
| `eventId` | Unique opaque identifier used for deduplication. |
| `ownerId`, `deviceId` | Bound to the authenticated publisher, not trusted solely from payload text. |
| `bootId`, `sequence` | Runtime identifier and monotonically increasing source sequence. |
| `source` | Allowlisted enum: webcam, desktop_activity, user_declaration, screen_analysis, core_mission, core_agent. |
| `kind` | Allowlisted event kind; no arbitrary command names. |
| `observedAt`, `expiresAt` | Timezone-aware UTC timestamps; expiry cannot exceed the policy for the field. |
| `value` | Small typed object specific to the event kind; maximum serialized envelope 4 KiB. |
| `evidenceKind` | observation, user_report, or inference. |
| `confidence` | Optional score from a documented detector/classifier; absent where not meaningful. Not represented as a calibrated probability unless evaluated as one. |

Raw frames, audio, screenshots, full window titles, filenames, page text, credentials, and conversation transcripts are excluded from this envelope.

### 5.2 Snapshot fields

`ContextSnapshot` contains `schemaVersion`, `snapshotId`, `deviceId`, `bootId`, `generatedAt`, an availability summary, and these independently timestamped fields:

| Field | Values / meaning | Initial freshness rule |
|---|---|---|
| `deskPresence` | present, away, unknown; source is webcam presence, not identity recognition. | Expires after 15 seconds without valid camera observations. |
| `desktopActivity` | input_active, input_idle, locked, unavailable. | Sample every 2 seconds; expire after 10 seconds without a valid sample. |
| `foregroundCategory` | development, communication, browser_unspecified, entertainment, other, unknown. | Local executable-to-category mapping; expires after 10 seconds. |
| `declaredIntent` | focus, break, research, meeting, none; supplied by user/session controls. | Until the declared end or a visible default of 15 minutes for an ad-hoc break. |
| `focusSession` | session ID, selected mission ID, start/end, running/paused/completed. | Driven by existing session commands; interrupted sessions are not silently resumed after restart. |
| `screenInterpretation` | Optional category and bounded evidence description. | Maximum 60 seconds as current evidence; older result may only be shown as a timestamped past observation. |
| `agentSummary` | Source-derived agent states and explicitly supported lifecycle fields. | Use the shelf reader's freshness checks and producer heartbeat limits; a local refresh cannot revive stale evidence. |
| `interactionPreferences` | quiet/companion/focus_coach, snooze expiry, permitted channel, speech availability. | User configuration; availability rechecked at delivery. |

No single `is_productive` Boolean is introduced. Productivity is a later interpretation of declared goals, observed coverage, and confirmed work.

### 5.3 Reduction and contradiction rules

1. A user's “I'm taking a break” changes declared intent, not the webcam's physical presence value.
2. A locked desktop suppresses input-active interpretation even if the last input sample was recent.
3. Healthy camera absence is confirmed only after a configurable 10-second dwell. Sensor loss immediately becomes unknown, not away.
4. A returned user requires 3 seconds of stable presence before a return event; tune this using the pilot's false-transition measurements.
5. A foreground category is a fact about a mapped application. “Research” is a user declaration or a qualified inference and remains separately labeled.
6. Core mission state outranks a language model's suggestion that a mission is finished.
7. Stale or unsupported sources are excluded from present-tense claims. The UI can show the last observation time without calling it current.
8. Data more than 5 seconds in the future relative to a receiving service is rejected or marked clock-invalid. Do not let a future timestamp keep state fresh indefinitely.
9. Local elapsed durations use a monotonic clock. Remote timestamps use UTC; service-receipt time is also retained to bound expiry when clocks disagree.
10. Live snapshot reads expire fields at read time, even if a timer, browser tab, or cleanup task was suspended.

### 5.4 Temporary corrections

Offer corrections such as “I'm researching,” “I'm on a break,” “That detection was wrong,” and “Stay quiet for 30 minutes.” Each correction has a scope, visible expiry, and undo action.

A correction never silently becomes a permanent preference or approved memory. A temporary research declaration suppresses incompatible prompts but does not erase the underlying application observation. A disputed detector event is excluded from future coaching and, where applicable, from opt-in aggregates; any already-awarded Core reward uses a separately audited reversal process.

### 5.5 Cross-device projection

The shared projection contains only availability, declared session/intent, coarse activity categories, permitted mission labels, agent summary, and per-field freshness. Omit raw app titles, desktop content, raw sensor events, and transcripts.

Initial publication policy: send on material change, coalesce to at most one update every 5 seconds, and send a 15-second availability heartbeat while enabled. A snapshot lease lasts at most 60 seconds. Renewing an availability heartbeat must not renew the freshness of an old observation.

Core authorizes reads through the existing account/device/link binding, and authorizes publication through a narrowly scoped laptop publisher identity. The phone and Discord bridge must not receive the laptop's producer secret.

Proposed endpoint responsibilities are `PUT /api/companion/context` for a bound publisher and `GET /api/companion/context` for an authorized reader. Final routing must follow Core's verified owner-binding conventions in the phase implementation spec; these are new APIs, not existing routes.

## 6. Memory, privacy, and retention

### 6.1 Separate kinds of state

| Data | Initial storage policy | User control |
|---|---|---|
| New raw webcam/audio/screen buffers | Process memory for the task; no new permanent recording feature. If a library requires a temp file, remove it after success/failure and recover orphaned files on next startup. | Enable the relevant sensor/capture mode; stop capture visibly. |
| New observation ring | RAM only, maximum 30 minutes and 10,000 events, whichever limit is reached first. | Pause/clear current context. |
| Current laptop snapshot | RAM; invalidated on pause, shutdown, or source expiry. | Inspect sources, correct intent, clear current context. |
| Core latest-context projection | Short-lived operational record, one per owner/device; invalid for reads after its 60-second lease. Physical deletion target: within 15 minutes while cleanup is healthy; alert and catch up after an outage. | Disable sharing; revocation immediately denies reads even before cleanup. Backups have a separately disclosed expiry. |
| Conversation context | Preserve the current session-only channel store and existing limits. | `/newchat` or corresponding local/PWA session controls. |
| Chat transport records | Existing Core/IPC transport policy, separately audited in Phase 0. Operational delivery storage is not permission to build a transcript archive. | Existing retention and device revocation; document exact cleanup behavior. |
| Approved personal memories | Existing approval-controlled memory store. | Approve, edit, delete, export, and disable through existing controls. |
| New daily activity aggregates | Disabled initially. If enabled, local aggregates retained for 30 days by default; no raw event timeline. | Separate opt-in, export/delete, and visible retention setting. |
| Notification delivery metadata | Minimal intent ID, state, timestamps, and reason code; proposed 7-day operational retention once notifications are enabled. | Delivery preferences and deletion policy; no raw prompt or screen content. |

These are proposed policies for new functionality. Phase 0 must inventory existing SQLite/Core tables and disclose differences before claiming the whole application follows them.

Database deletion means removal from active application storage; managed backup retention is a separate deployment property that must be documented. Do not promise immediate erasure from provider backups unless it is actually supported and configured.

### 6.2 Processing and sharing choices

- Keep routine camera presence and desktop metadata interpretation local.
- Screen images or voice sent to a cloud model need a specific enabled feature and clear processing disclosure. Local temporary deletion does not imply deletion by a model provider.
- Select the application/window to share when feasible; full desktop capture must be visibly identified.
- Treat text in webpages, screenshots, external tool output, and notifications as observed content. It cannot authorize tools, change rules, or alter memory permissions.
- Pausing companion sensing stops new companion observations, clears its transient context, and invalidates shared live context. It does not delete approved memories or silently change unrelated existing features.
- Show which legacy camera/audit/automation features remain enabled when companion sensing is paused, until their lifecycle controls are explicitly consolidated.

## 7. Intervention and personality design

### 7.1 Modes

| Mode | Behavior |
|---|---|
| Quiet | Answer direct requests; suppress unsolicited coaching. Explicit user-requested timers follow their own settings. |
| Companion | Permit selected desk-return and useful workflow check-ins. |
| Focus Coach | Permit focus/break prompts during a user-started session, with explicit rules and suppression controls. |

Companion mode is distinct from the existing session manager's focus/background mode. Store and display them separately so switching a personality mode cannot unexpectedly create or end a tracked session.

### 7.2 Policy before generation

A rule returns an `InterventionIntent` only if it has fresh evidence, is enabled, has not been snoozed, and fits the delivery budget. The intent contains a stable deduplication key, trigger ID, evidence timestamps, reason code, expiry, priority, and allowed channels.

The assistant receives a small factual packet and the chosen tone. It may phrase an approved intervention, but cannot turn a skipped rule into an action. When generation fails or misses the deadline, use a deterministic short sentence or skip the intervention.

Suggested pilot defaults:

- At most one unsolicited coaching message per 15 minutes and three per hour across all channels.
- Maximum eight unsolicited coaching messages per day; explicit requested timers and direct responses are tracked separately.
- One active speech item and no backlog of expired coaching speech.
- Suppress coaching while the user is speaking, Vision is speaking, the device is locked, quiet mode is active, or a declared meeting is in progress.
- General coaching intents expire after 60 seconds; explicit needs-input notifications after 5 minutes unless the source clears them earlier.
- User pause/snooze takes effect immediately, including after generation but before delivery.
- If presence is unknown, do not escalate a desk-absence alert to the phone.

These are initial tuning values, not claims that they are optimal. Adjust after measuring dismissals and interruptions in the pilot.

### 7.3 First three rules

| Rule | Eligibility | Response | Suppress when |
|---|---|---|---|
| Break suggestion | User-started focus session reaches the chosen interval, initially 50 minutes, with valid session coverage. | Offer a break; do not force it. | Break/meeting/quiet/snooze; same interval already acknowledged. |
| Desk check-in | User-enabled check-in; active focus session; healthy camera reports away for 5 minutes beyond dwell. | Ask whether to pause the session. | Camera unavailable; declared break; unknown presence; laptop locked; recent check-in. |
| Agent needs input | Producer explicitly identifies an unresolved input request for an operation. | Name the agent and the bounded decision summary. | Stale/cleared request, duplicate delivery, disconnected source, or no permitted channel. |

If producers do not yet emit explicit needs-input events, ship the first two rules and display the third as unavailable. Do not approximate needs-input from idle, stuck, or long elapsed time.

### 7.4 One response across channels

Select the delivery channel at dispatch time. Prefer laptop speech or desktop UI when the user is present and that channel is enabled. Use phone delivery only for an enabled remote notification class. Each intent has one selected channel by default.

Record `proposed`, `suppressed`, `claimed`, `attempted`, `acknowledged`, `failed`, `expired`, or `delivery_unknown`. A network timeout is not proof that a message was never delivered. Avoid an automatic second-channel send when delivery is uncertain; the notification inbox can show the unresolved state.

Deduplication and atomic claims can bound application dispatch. Do not promise exactly-once delivery across Discord, push providers, OS notification systems, and network failures.

### 7.5 Personality contract

Offer calm, playful, and strict tone settings. Tone affects wording, not facts, scoring, permissions, or notification frequency. The assistant should answer first, add brief character afterward, accept correction, and stop teasing when asked.

Example with verified evidence: “Codex is still working. Antigravity reported success three minutes ago.” Without a completion record: “Antigravity is idle; I don't have a confirmed completion result.”

A configurable persona may be affectionate or theatrical if the user wants it, but should not claim access to unseen activity or make the product difficult to pause or leave.

## 8. Laptop and mobile capability boundaries

### 8.1 Windows laptop first

Use supported foreground-window, last-input, and session-lock signals. `GetForegroundWindow` identifies the active window, while `GetLastInputInfo` is scoped to the calling session; session notifications distinguish lock/unlock. These APIs provide activity signals, not a complete account of what the user is doing. See sources S2–S4.

Resolve application identity locally and map it to a category. Keep titles and document paths out of the default event contract. Browser executable identity alone cannot determine the website or whether a video is work-related; a later, explicitly enabled extension can supply coarse domains or user-tagged categories.

Use existing camera frames where possible instead of opening the webcam twice. Measure presence inference on the user's actual camera angle and lighting. Degrade to desktop-only context when the camera is off or blocked.

The proposed native collector is Windows-specific. macOS/Linux need their own permission and collection adapters if requested later; portability is a design boundary, not a promised first release.

### 8.2 Common phone experience

Both Android and iOS should initially support the existing chat surfaces, a current-context card, last-seen availability, and later foreground push-to-talk. The phone is a companion UI; having a PWA or Discord chat does not grant whole-device observation.

For phone voice, use a user-initiated foreground recording with a visible timer and cancel action. Start with a 30-second utterance limit and a bounded upload size, transcribe, then submit through the existing session-specific chat pipeline. Delete temporary audio after processing or a short failure expiry. Do not add background listening as an incidental extension of voice chat.

Home Screen web apps on iOS/iPadOS support Web Push from 16.4, with permission requested in response to user interaction. Push and service workers are not a continuously running phone sensor service. See S7–S8.

### 8.3 Android native option

If phone usage awareness becomes important, build Android first. `UsageStatsManager` can provide app-usage statistics with explicit user-granted Usage Access in Settings. This grants app-level usage information, not arbitrary screen content or the meaning of a conversation inside another app. See S5.

Plan explicit opt-in, per-category aggregation, a visible collection state, permission-revocation handling, and device testing under battery restrictions. Native microphone/camera collection has foreground-service and while-in-use restrictions; do not design an invisible background observer. See S6.

An Android companion may eventually offer session controls and a foreground camera-sharing mode. A second camera angle should be an explicit active session, not an assumption that the phone is permanently available as a camera.

### 8.4 iOS native option

Individual users can authorize Screen Time features through Family Controls. Device Activity/Managed Settings capabilities and distribution entitlements constrain what can be built and distributed; Apple approval is required for the Family Controls distribution entitlement. See S9–S10.

Begin with a feasibility prototype demonstrating the exact desired screen-time behavior on a physical device and intended distribution method. Do not assume Android-style usage export, unrestricted access to other apps, or a continuous background camera/microphone pipeline.

Distinguish two paths. Ordinary `DeviceActivityReport` extensions cannot move sensitive report data outside their sandbox or use network requests to export it. Apple also documents a separate activity-data export API with an additional Family Controls App and Website Usage entitlement and `approvedWithDataAccess` authorization. Customer use requires both an EU-located device and an EU Apple Account region; only one app per device can hold that authorization. Development testing outside that region is not proof of customer eligibility. Treat this as an optional, region-gated branch, not universal iPhone telemetry. See S11–S13 and the research companion.

The PWA remains useful even if a native Screen Time prototype is limited. Native iOS release is an independent decision, not a prerequisite for laptop awareness or cross-platform phone chat.

## 9. File ownership and proposed module map

Paths in this section are repository-relative to the selected implementation checkout. Entries marked **new** are proposals and must be checked for naming conflicts during Phase 0. Keep documentation in the main checkout; implementation work must use an appropriate integrated worktree.

### 9.1 Vision

| File / directory | Status | Responsibility |
|---|---|---|
| `ascend-vision/assistant/context_models.py` | New | Typed observation, snapshot, field provenance, declaration, and availability contracts. |
| `ascend-vision/assistant/context_runtime.py` | New | Bounded reducer, expiry, source sequencing, declaration handling, and read-only snapshot provider. |
| `ascend-vision/integrations/desktop_activity.py` | New | Windows foreground category, input idle, and session lock adapter. |
| `ascend-vision/integrations/desk_presence.py` | New | Calibrated presence interpretation using the existing capture/detection path. |
| `ascend-vision/integrations/context_ipc.py` | New | Owner-restricted local snapshot transport for a separate dashboard process. |
| `ascend-vision/integrations/context_publisher.py` | New | Outbound, bounded, latest-snapshot publication to Core. |
| `ascend-vision/integrations/context_reader.py` | New | Authorized Core snapshot read, strict validation, and expiry for remote assistant processes. |
| `ascend-vision/assistant/companion_policy.py` | New | Deterministic intervention eligibility, evidence requirements, and suppression reasons. |
| `ascend-vision/assistant/intervention_delivery.py` | New | Dispatch reservation, cooldowns, channel choice, expiry recheck, and receipts. |
| `ascend-vision/assistant/activity_summary.py` | New in phase 5 | Opt-in aggregates, coverage, corrections, and deterministic progress calculations. |
| `ascend-vision/assistant/service.py`, `feedback.py` | Existing | Consume a bounded snapshot and consistent persona without merging channel history. |
| `ascend-vision/main.py`, `config.py`, `session_manager.py` | Existing | Construct/inject components, expose flags, and adapt existing lifecycle/session events. |
| `ascend-vision/screen_auditor.py`, `voice_listener.py` | Existing | Explicit capture/recording lifecycle and central feedback routing. |
| `ascend-vision/integrations/warning_state_machine.py` | Existing | Coordinate legacy warnings and Core actions with companion mode; preserve deliberate existing behavior. |
| `ascend-vision/dashboard.py`, `templates/`, `static/` | Existing | Current-context card, source availability, corrections, mode, and pause controls. |
| `ascend-vision/discord_bot.py`, `integrations/discord_phone_bot.py`, `integrations/phone_worker.py` | Existing | Inject the appropriate provider and preserve per-channel authorization, bounds, and session isolation. |

### 9.2 Core / PWA

| File / directory | Status | Responsibility |
|---|---|---|
| `server/schemas/companion_context.py` | New | Publisher/read contracts, size limits, and schema version. |
| `server/services/companion_context.py` | New | Owner/device binding, latest snapshot, expiry, deletion, and publisher sequence rules. |
| `server/routers/companion_context.py` | New | Narrow authenticated context endpoints. |
| `server/prisma/schema.prisma` | Existing | Only the minimal latest-context/delivery records justified by the phase. |
| `server/services/companion_notifications.py` | New in phase 4 | Notification intent claims, dispatch metadata, and provider receipts. |
| `server/routers/status.py`, `services/status_service.py`, `schemas/service_status.py` | Existing | Status/completion compatibility; explicit needs-input contract only if producers support it. |
| `server/cli/status_read_credentials.py` | Existing | Environment-specific shelf-reader provisioning and revocation. |
| `client/src/features/companion/` | New | Context card, notification inbox/preferences, and foreground phone voice UI. |
| `client/src/features/phone-chat/` | Existing | Reuse owner/device session and delivery behavior; add companion UI without duplicating chat transport. |

### 9.3 Proposed interfaces

| Interface | Inputs | Result / invariant |
|---|---|---|
| `ContextRuntime.accept(event)` | Validated `ObservationEnvelope` | Accepted, duplicate, stale, or rejected; no external action. |
| `ContextRuntime.declare(declaration)` | Scoped user intent with expiry | New snapshot revision; no permanent memory write. |
| `ContextProvider.read_snapshot()` | No model-supplied owner override | Immutable authorized `ContextSnapshot`, with stale fields expired at read time. |
| `CompanionPolicy.evaluate(snapshot, preferences, now)` | Fresh bounded state and deterministic clock | Zero or more proposed `InterventionIntent` objects with suppression reasons. |
| `InterventionDelivery.dispatch(intent)` | Validated, unexpired intent | Delivery receipt; rechecks mode, cooldown, current evidence, and availability. |
| `ContextPublisher.publish(snapshot)` | Redacted projection | Acknowledgment or bounded retry; cannot upgrade observation freshness. |
| `ActivitySummary.read(day)` | Local date plus timezone and retention permission | Aggregates, coverage, provenance, and rule version; no inferred missing activity. |

No interface exposes raw sensor buffers to the language model by default. No context interface accepts arbitrary tool calls.

## 10. Phases and dependencies

| Phase | Deliverable | Prerequisites | Release scope |
|---|---|---|---|
| 0 | Verified integration baseline, capability inventory, and reproducible environment | None | Preparation; no new sensing behavior. |
| 1 | Current Context V1 on the laptop | 0 | Inspectable presence/activity/intent state; local corrections. |
| 2 | Grounded conversation and verified Core context | 1; Core access for relevant fields | Voice/chat answers use context and admit uncertainty. |
| 3 | Proactive Companion V1 | 2; individual producer capability for each rule | Local opt-in coaching with one delivery controller. |
| 4 | Phone context, voice, and opt-in notifications | 2 for read/voice; 3 for proactive delivery | Android/iOS PWA + existing Discord extension. |
| 5 | Opt-in daily reflection and transparent progress | 1–3; separate history setting | Local aggregates and Core-grounded summaries. |
| 6 | Optional rewards and browser allowances | 5; Core idempotent actions | Explicitly enabled progression/access feature. |
| 7A | Optional Android native companion | 4; demonstrated need for native telemetry | User-authorized usage summaries. |
| 7B | Optional iOS native companion | 4; Screen Time feasibility and entitlement gates | Platform-supported features only. |
| 8 | Declarative skills; selective MCP integration | Stable tools, context, permissions, and delivery | Reusable bounded routines and justified connectors. |

Phases 4 and 5 can be planned independently after their dependencies pass. Native mobile and reward work are optional branches. Do not hold the laptop release for them.

Release effort should be estimated after Phase 0 confirms the branch base, device performance, and external integration state. This roadmap is a dependency plan, not a promise of delivery dates or model/API costs.

## 11. Detailed phase plans

### Phase 0 — Establish the integration baseline

**Outcome:** A selected, reproducible code base and an accurate capability inventory. The roadmap must not accidentally reimplement completed features or assume unmerged branches are deployed.

**Files to inspect:** Existing Vision/Core modules in sections 3 and 9; current deployment manifests and dependency files; Core's status credential CLI.  
**Document to create during implementation:** `docs/companion-baseline.md` in Vision, recording repository commits, enabled flags, environment roles, capability status, and benchmark results without secrets.  
**Interface produced:** `CapabilityInventory` with capability name, implementation revision, runtime availability, configuration status, and last verification time. This inventory informs UI availability; it does not grant permissions.

- [ ] Inventory active worktrees, dirty changes, and commits; choose a suitable integrated base and preserve unrelated user work.
- [ ] Reconcile assistant/memory/phone/Discord work and the Core upsert correction by reviewing diffs and ancestry; avoid blind cherry-picks of overlapping changes.
- [ ] Decide whether the explicit completion-history branch is part of this release; if excluded, mark completion narration unavailable.
- [ ] Enumerate existing database tables storing chat transport, observations, events, and penalties; record their current retention and cleanup behavior.
- [ ] Verify camera, microphone, speaker, local chat, PWA chat, and Discord chat independently. Separate code tests from live delivery proof.
- [ ] Verify Core's status-shelf route, credential provisioning state, real authenticated response, and actual producer heartbeats in the chosen environment.
- [ ] Use the existing `status_read_credentials` CLI where credential administration is needed. Bind the database to the chosen environment before invoking any mutating command; capture no raw credential in the baseline report.
- [ ] Record current behavior of the legacy warning state machine and screen-audit narration, including which events can mutate Core data.
- [ ] Measure a 30-minute baseline with the user's normal workload: CPU/GPU/RAM, camera frame processing, audio responsiveness, and battery condition.
- [ ] Record the exact runnable Python interpreter/dependencies and frontend workspace commands. Optional Discord dependencies must be installed for Discord tests; absence is not an application test result.

**Acceptance:** The baseline identifies one implementation base, known deployment gaps, valid local run/test commands, and which observations/actions are already enabled. Status-dependent work can remain disabled if provisioning is unavailable; local context work can still proceed.

**Rollback:** No new behavior is enabled. Any environment changes made during a later authorized execution have their own recorded rollback; writing this plan makes none.

### Phase 1 — Current Context V1

**Outcome:** Vision displays a coherent, timestamped account of laptop activity and accepts temporary corrections.

#### Task 1.1 — Contracts and bounded reducer

**Files:** Create `assistant/context_models.py`, `assistant/context_runtime.py`, and `tests/test_context_runtime.py` under the Vision project.  
**Consumes:** Observation envelope and freshness rules from section 5.  
**Produces:** `ContextRuntime.accept`, `ContextRuntime.declare`, and `ContextProvider.read_snapshot` as defined in section 9.3.

- [ ] Define the typed contracts, source enum, per-kind allowed values, field expiry, and size bounds.
- [ ] Add deterministic tests for duplicate/out-of-order observations, source restart, future timestamps, expiry, and overlapping declarations before implementation.
- [ ] Implement the reducer with an injected monotonic/UTC clock and bounded event storage.
- [ ] Separate user controls from coalesced sensor events so pause and shutdown remain responsive under load.
- [ ] Verify that no context operation calls Core mutation endpoints or writes approved memories.
- [ ] Review the contract and commit the independently tested reducer.

**Concrete verification:** With a fake clock, publish camera presence at time zero, advance beyond 15 seconds without a new valid frame, and assert `deskPresence=unknown`. Replay an older source sequence and assert it cannot restore presence. Declare a break and assert only the intent field changes.

#### Task 1.2 — Windows activity and desk-presence adapters

**Files:** Create `integrations/desktop_activity.py`, `integrations/desk_presence.py`, `tests/test_desktop_activity.py`, and `tests/test_desk_presence.py`; modify `main.py` and `config.py` narrowly.  
**Consumes:** Existing camera output and supported OS activity APIs.  
**Produces:** Typed observations for presence, category, input activity, lock, and source availability.

- [ ] Build a local executable-category mapping; publish only the category by default.
- [ ] Test active, idle, locked, permission-denied, and unavailable results through adapter fakes.
- [ ] Reuse the existing frame source and add desk-region calibration with an explicit preview/setup step.
- [ ] Implement presence dwell and return stability; keep camera failure distinct from healthy absence.
- [ ] Exercise chair movement, lighting changes, an empty desk, blocked camera, another person entering the frame, and sustained occlusion. Do not introduce biometric identity claims.
- [ ] Verify that unplugging the camera and resuming from laptop sleep invalidate old context.
- [ ] Measure incremental overhead against the Phase 0 baseline and adjust sampling without changing semantic expiry.

**Concrete verification:** A locked desktop with a visible seated user reports locked + present, not active work. A denied camera reports unknown while desktop activity continues.

#### Task 1.3 — Local context UI, provider, and corrections

**Files:** Create `integrations/context_ipc.py` and focused IPC/UI tests; modify `dashboard.py`, its existing templates/static assets, `session_manager.py`, and runtime construction.  
**Consumes:** `ContextProvider.read_snapshot`.  
**Produces:** A “What Vision knows” card and authenticated local declarations.

- [ ] Add the same-process provider and a bounded owner-restricted IPC adapter for a separate dashboard process.
- [ ] Show observation value, source, age, uncertainty, and whether sensing is paused.
- [ ] Provide temporary intent correction, undo, snooze, and clear-context controls with visible expiry.
- [ ] Map existing focus/background sessions into context without conflating them with companion modes.
- [ ] Reconcile component teardown so stopping Vision closes collectors, invalidates context, and leaves no orphaned worker loop.
- [ ] Verify local UI and voice/chat inspection read the same snapshot revision when no new event has arrived.

**Phase exit criteria:** Correct state for present/away/unknown, active/idle/locked/unavailable, and declared intent; no unsolicited interventions; no new durable activity history; no accidental Core penalty or memory writes. All corrections and pause controls work with the network disconnected.

**Future test command:** `python -m pytest tests/test_context_runtime.py tests/test_desktop_activity.py tests/test_desk_presence.py tests/test_context_ipc.py -q`, using the interpreter selected in Phase 0. These proposed test files do not exist merely because this command is written here.

**Rollback:** Disable the context feature flag and stop its adapters. Existing camera/chat behavior uses its pre-companion configuration.

### Phase 2 — Grounded conversation and richer context

**Outcome:** Vision can explain observable activity and verified work context through the shared assistant, while keeping personality useful.

**Files:** Modify `assistant/service.py`, `feedback.py`, `main.py`, `discord_bot.py`, `integrations/status_shelf.py`, and `screen_auditor.py` as required; create `tests/test_assistant_context.py` and `tests/test_context_screen_policy.py`. Core changes are limited to a proven missing read contract, if one is identified in Phase 0.

**Consumes:** `ContextProvider`, existing approved-memory retrieval, existing `ToolRuntime`, and authenticated Core projections.  
**Produces:** A bounded factual context packet and answers with explicit uncertainty/source age where relevant.

- [ ] Inject the provider into the assistant instead of reading runtime globals.
- [ ] Start with a 4-KiB serialized context packet budget, separate from existing conversation limits; measure provider token cost and reduce as needed.
- [ ] Add regression scenarios for “What am I doing?”, “Am I on a break?”, “What are my current missions?”, and “Which agents are working?” with missing, stale, and conflicting data.
- [ ] Preserve deterministic status answers and require explicit operation completion evidence before using “finished.” Test the selected Core shelf schema against the actual Vision reader.
- [ ] Retrieve mission context from Core through a read-only adapter if the required fields are not already available. Do not infer today's priorities from a task count alone.
- [ ] Integrate user-requested screen interpretation with visible capture state, bounded image handling, and source labels. Automatic cloud screenshots remain a separately enabled feature.
- [ ] Expire screenshot-derived context quickly; never reuse a 30-minute-old audit as a current description.
- [ ] Separate observation text from assistant/tool instructions and test a screenshot/webpage containing hostile instructions.
- [ ] Add persona examples demonstrating a direct answer in calm, playful, and strict tones; factual content must remain invariant.
- [ ] Preserve separate channel history and dashboard-only memory approval; repeated sensor data must never call `MemoryStore.propose` automatically.

**Acceptance examples:** With Core unavailable, Vision can still describe local desk activity but cannot verify mission/agent status. With a fresh editor category and a user-declared break, it says the editor is open and the user is on a declared break; it does not assert coding progress.

**Exit criteria:** All inspected facts have provenance; unavailable tools yield honest bounded replies; no unauthorized screen capture; equivalent facts are available to each connected channel without copying transcripts. Remote context delivery itself is introduced in Phase 4.

**Rollback:** Remove context injection via its flag; preserve existing chat, memory, and status behavior. Optional screen captures stop independently.

### Phase 3 — Proactive Companion V1

**Outcome:** Vision initiates a small number of useful, explainable interventions locally.

**Files:** Create `assistant/companion_policy.py`, `assistant/intervention_delivery.py`, and their focused tests. Modify runtime wiring, feedback/speech, screen-audit feedback routing, and legacy warning integration.

**Consumes:** Snapshot and preference contracts; fresh Core lifecycle events where available.  
**Produces:** `InterventionIntent` and `DeliveryReceipt`; selected delivery never implies an authorized Core mutation.

- [ ] Implement the three initial rules from section 7 with an injected clock and explicit suppression reasons.
- [ ] Prove repeated observations cannot produce repeated intent IDs for the same trigger window or agent operation.
- [ ] Route eligible companion speech through one delivery controller. Establish which legacy warnings remain independent and prevent duplicate narration for overlapping events.
- [ ] Confirm new presence/activity observations do not call the existing bad-habit offense path unless a separately enabled, reviewed rule explicitly requires it.
- [ ] Recheck evidence, quiet mode, snooze, and expiry after generation, immediately before delivery.
- [ ] Add stop-speaking/cancel behavior and pause input capture during TTS unless a tested echo-suppression/barge-in path is enabled.
- [ ] Implement declared meeting/quiet suppression first; automatic detection of arbitrary conferencing apps is a later adapter, not a guaranteed capability.
- [ ] Add an explanation view: trigger, factual evidence, rule, chosen channel, and suppression/delivery outcome, without raw user text.
- [ ] Start in shadow mode for three representative sessions: record decisions locally in transient state, show them in the UI, and do not speak automatically.
- [ ] Tune dwell/cooldown defaults using user feedback; then enable one rule at a time.

**Meaningful tests:** A stale presence event produces no desk check-in; a declared break suppresses a pending focus prompt; a generated message is discarded if the user snoozes before playback; an agent-idle event never triggers a completion or needs-input alert; an old intent does not speak after resume.

**Exit criteria:** No duplicate unsolicited delivery in the deterministic replay suite; pause/snooze always suppresses new companion output; all initial rules are inspectable and independently disableable. Run a seven-day personal pilot and retain a rule only if the user considers its interruption rate acceptable.

**Rollback:** Disable proactive rules centrally, drain/expire pending intents, and retain reactive chat/current context.

### Phase 4 — Phone context, voice, and notifications

**Outcome:** Both mobile platforms can access useful context and converse with Vision; selected proactive events can reach an opted-in phone channel.

#### Task 4.1 — Context publication and remote reading

**Files:** New Vision publisher/reader modules and tests; Core context schema/service/router and contract tests; minimal Prisma change; new PWA companion context card.  
**Consumes:** Redacted snapshot, established owner/device/link identities, and source sequencing.  
**Produces:** Authorized latest-context reads with per-field expiry and availability.

- [ ] Define publisher authentication and read access using existing identity boundaries. Do not reuse the shelf-reader credential as a write credential.
- [ ] Implement latest-record replacement, sequence/boot validation, size limits, source timestamp bounds, and lease expiry.
- [ ] Verify the migration and owner/device uniqueness on isolated PostgreSQL, including concurrent publishers, transaction failure, and compatibility with the old reader during rollback. Mocked tests alone do not satisfy the database gate.
- [ ] Implement logical read expiry plus a scheduled physical cleanup mechanism; test cleanup failure and report it operationally.
- [ ] Publish only when context sharing is enabled; redact fields before network serialization.
- [ ] Make the Discord process read through the verified account link, and make PWA reads use the active owner/device session.
- [ ] Test revoked devices, wrong owner, wrong Discord link, stale records, service restart, delayed packets, and laptop sleep.
- [ ] Show last-seen and unavailable states clearly. A Core receipt must not be displayed as fresh sensor evidence.

#### Task 4.2 — Foreground phone voice

**Files:** Add a focused voice UI module under `client/src/features/companion/`, a bounded transcription transport and tests, and the corresponding Core/worker contract if needed.  
**Consumes:** User-initiated audio and the existing channel session identity.  
**Produces:** Text submitted to the existing handler and optional foreground reply playback.

- [ ] Add press-to-record/start-stop, live elapsed time, cancel, and permission-denied states.
- [ ] Enforce the 30-second recording limit and explicit upload-size bound before accepting the job.
- [ ] Choose supported encodings by testing physical Android/iOS devices; reject unsupported media gracefully and retain typed chat.
- [ ] Bind transcription and generation to the same cancellable request. Delete temporary audio after completion/failure expiry; never reuse it as ambient history.
- [ ] Treat the transcript as user input, not as automatic approval of a consequential action.
- [ ] Test denied/revoked microphone permission, incoming calls, backgrounding, network loss, duplicate submit, cancellation, and device logout.

#### Task 4.3 — Opt-in notifications

**Files:** Core notification service/records and provider adapters; PWA subscription/inbox/preferences; optional outbound Discord notification adapter.  
**Consumes:** A validated `InterventionIntent` with consent and permitted channels.  
**Produces:** Bounded delivery attempts and a visible receipt state.

- [ ] Add separate opt-ins for browser push and unsolicited Discord messages. Existing slash-command use is not automatically notification consent.
- [ ] Store push subscriptions and Discord destination binding server-side; handle invalid/revoked subscriptions without repeated retries.
- [ ] Make lock-screen notifications generic by default, such as “Vision has an update”; fetch detailed content after authentication.
- [ ] Atomically claim delivery, apply the cross-channel budget, and expire intents before sending.
- [ ] Test retries, uncertain receipts, provider rate limits, device revocation, and a stopped laptop.
- [ ] Verify on a physical Android device and an installed iOS Home Screen PWA; desktop emulation is insufficient evidence for mobile delivery.

**Exit criteria:** Separate PWA/Discord conversation contexts remain intact; shared facts agree for the same snapshot revision; stale laptop state is visible; push/DM delivery is independently enabled; phone voice works foreground-first with typed fallback.

**Scope boundary:** Remote focus controls, laptop actions, and memory administration are not implicitly added by this phase. A later control feature needs a typed, user-visible action contract and confirmation behavior appropriate to the action.

**Rollback:** Stop new context publication and notification claims; retain existing text chat. Revoke new publisher/subscription credentials without invalidating unrelated chat credentials.

### Phase 5 — Daily reflection and transparent progress

**Outcome:** Vision can discuss measured patterns without converting every observation into a durable personal fact.

**Files:** New `assistant/activity_summary.py`, a dedicated opt-in aggregate schema/store, summary UI, and tests for time accounting and retention. Core remains the source for mission/reward truth.

**Consumes:** Eligible activity intervals, session declarations, corrections, and confirmed Core records.  
**Produces:** `DailySummary` with date/timezone, coverage, tracked duration, confirmed outcomes, optional score components, and rule version.

- [ ] Add a separate activity-history opt-in with a 30-day default retention and export/delete controls.
- [ ] Compute unions of eligible intervals so overlapping camera/input/browser signals do not multiply time. Paused, unknown, disconnected, and uncovered intervals remain explicit.
- [ ] Handle midnight, timezone changes, clock corrections, suspend/resume, and interrupted sessions without adding fictional work time.
- [ ] Label metrics precisely: tracked focus-session minutes, declared break minutes, observed entertainment-category minutes, and Core-confirmed completed missions.
- [ ] Generate summaries from the computed facts; the language model cannot invent totals or change arithmetic.
- [ ] Allow disputed classifications to be corrected and summaries to be regenerated with the correction recorded.
- [ ] Keep scoring disabled by default. If enabled, show the formula, inputs, missing-data treatment, and how to disable it.

**Optional starting score:** 40% declared focus-goal progress, 40% selected mission-goal progress confirmed by Core, and 20% adherence to the user's chosen break plan. Each component is capped at its target. Only components explicitly configured and sufficiently observed are scored; exclude unavailable components and display the resulting coverage/denominator. If none are eligible, show “insufficient information,” not zero.

Do not penalize posture, fatigue estimates, unobserved phone time, or time outside a declared goal. A score measures progress against selected rules, not personal worth or inferred health.

**Exit criteria:** The same evidence produces the same totals and score; absent data never becomes poor behavior; history remains off until enabled; deletion and retention are demonstrated on real application storage.

**Rollback:** Disable aggregation and summary generation. Retained records follow the chosen deletion policy; rolling back code does not automatically erase user-approved history.

### Phase 6 — Optional rewards and browser allowances

**Outcome:** Users can deliberately connect verified milestones to Ascend rewards or limited entertainment allowances.

**Files:** Existing Core habit/mission/automation services and ledger paths, focused reward contract tests, and an optional browser extension with a small settings UI. Exact reward modules are selected after reviewing the integrated Core base.

**Consumes:** Core-confirmed milestone or user-confirmed qualifying session, explicit reward rule/version, and idempotency key.  
**Produces:** Core-recorded reward grant or rejection; optional browser allowance state.

- [ ] Design the reward as an explicit rule the user can inspect and disable.
- [ ] Use one Core-authoritative ledger with unique grant keys; replay, reconnect, and two-device delivery must not duplicate rewards.
- [ ] Handle disputed evidence through an auditable reversal/correction, not a silent balance rewrite.
- [ ] Prototype a selected-site browser allowance with visible remaining time and an immediate user override.
- [ ] Keep essential services, OS login, emergency access, and unrelated apps outside the allowance feature.
- [ ] Verify that a browser extension can only enforce its stated browser scope. Do not advertise it as system-wide blocking.
- [ ] Keep the assistant's suggestions separate from Core's validated grants and permission checks.

**Exit criteria:** Rewards survive replay tests without duplication; users can override/disable the allowance; the model cannot independently subtract HP/XP or deny laptop access.

**Rollback:** Disable rule execution and browser enforcement while retaining the Core ledger for reconciliation. Restore normal site access immediately.

### Phase 7A — Optional Android native companion

**Outcome:** Add phone usage context only if the PWA cannot satisfy the user's chosen use case.

**Repository boundary:** A dedicated native companion module/repository selected during this phase's design, not embedded into Vision's Python runtime. Do not create a mobile project during earlier phases.

- [ ] Define one measurable use case, such as an opt-in daily entertainment-app duration summary.
- [ ] Prototype Usage Access enrollment, revocation, and unavailable-data states on a physical device.
- [ ] Aggregate categories on-device and synchronize bounded summaries; avoid collecting other-app message or screen content.
- [ ] Test manufacturer battery management, app termination, reboot, clock changes, airplane mode, and delayed synchronization.
- [ ] Define and measure battery/network cost against the same device without collection; do not promise continuous coverage when the OS stops the app.
- [ ] Add explicitly started phone camera or voice sessions only if they solve a demonstrated need, observing platform restrictions.

**Exit criteria:** The user can see and revoke collection, usage coverage is honest, and the measured battery cost is accepted. Android telemetry is optional for all laptop/phone chat behavior.

### Phase 7B — Optional iOS native companion

**Outcome:** Deliver validated iOS capabilities without promising Android-equivalent monitoring.

- [ ] Prototype individual Family Controls authorization and the exact desired Device Activity or Managed Settings behavior on a physical iPhone.
- [ ] Confirm the required entitlement for the app and any relevant extensions, including the intended distribution path.
- [ ] Document which information can be displayed, retained, or shared under the supported API; do not assume raw app-usage export.
- [ ] Keep ordinary on-device Screen Time reports separate from the additional-entitlement export path. For export, verify supported OS versions, EU device/account eligibility, `approvedWithDataAccess`, and authorization replacement/revocation on a distribution build. Hide export when any gate fails; do not work around the report sandbox.
- [ ] Test permission withdrawal, app lifecycle, extensions, device restart, Focus settings, and offline behavior.
- [ ] Release a narrower native feature set if needed; preserve the PWA/Discord fallback.

**Exit criteria:** The desired behavior is demonstrated under the intended distribution configuration. If the entitlement or APIs do not support the requirement, keep that feature unavailable and continue supporting phone chat/voice/notifications.

### Phase 8 — Declarative skills and selective MCP

**Outcome:** Package proven routines and add external integrations where a concrete workflow benefits.

**Skill boundary:** A skill declares inputs, permitted read tools/actions, step budget, timeout, output shape, and completion conditions. It cannot change sensor permissions, memory approval, delivery consent, or Core authority.

- [ ] Package “Start a focus session,” “Explain current context,” “Review my day,” and “Summarize agent activity” using already-tested underlying capabilities.
- [ ] Keep declarative skill manifests versioned and user-enabled. Routine text cannot execute shell/Python code or expand the tool allowlist.
- [ ] Limit each workflow's steps and elapsed time; require explicit user input for consequential transitions defined by the action contract.
- [ ] Add an MCP client adapter only for a named useful service, such as a selected calendar or task source, after native tools satisfy the same workflow.
- [ ] Apply existing owner binding, schema validation, response bounds, timeout, logging redaction, and action confirmation to imported tools.
- [ ] Treat external resource/tool text as untrusted content. It cannot command the companion to capture a screen or send a message.
- [ ] Avoid exposing Vision's live sensors or memory as a general MCP server in this roadmap.

**Exit criteria:** A skill cannot acquire more privilege than its declared tools; disabling a server removes its capabilities; unavailable integrations degrade to an honest partial result.

## 12. Evaluation and release evidence

### 12.1 Deterministic replay scenarios

| Scenario | Required outcome | Phases |
|---|---|---|
| Camera stops while presence was detected | Presence expires to unknown; no absence criticism. | 1–3 |
| Healthy camera confirms absence | Away only after dwell; eligible rules still require an active declared session. | 1 / 3 |
| Editor open during a declared break | Both facts preserved; no claim of current coding progress. | 1–2 |
| Old screen audit says entertainment | It is labeled historical and excluded from current intervention eligibility. | 2–3 |
| Duplicate/out-of-order event | No state regression or duplicate rule trigger. | 1 / 3 / 4 |
| Laptop sleep/resume or process restart | Old evidence expires; new boot starts unknown; no stale queued speech. | 1–4 |
| Core shelf rejects credentials | Local context still works; agent state is unavailable; no guessed completion. | 0 / 2 |
| Agent idle without completion | State can be reported as idle; completion cannot be inferred. | 2–3 |
| User snoozes during generation | Pending coaching is discarded before delivery. | 3 |
| Voice output is picked up by microphone | No self-triggered conversation loop. | 3–4 |
| Phone device or Discord link revoked | New reads/deliveries fail authorization. | 4 |
| Push provider times out after an attempt | Record unknown receipt; avoid automatic duplicate channel escalation. | 4 |
| Camera and input intervals overlap | Daily focus time counts the union once. | 5 |
| All activity data missing | Summary states insufficient coverage; no fabricated score. | 5 |
| Reward request replayed | One ledger grant; subsequent requests return the existing result or rejection. | 6 |
| External screen/tool text requests secrets | Treated as content; no privileged execution. | 2 / 8 |

### 12.2 Pilot targets

Targets below are proposed acceptance thresholds to measure on the user's laptop, not claims about current performance:

- Local snapshot reads: p95 under 100 ms without waiting for a model or network request.
- Input/foreground state update: visible within 5 seconds while the collector is healthy.
- Sensor loss: represented within the field's stated TTL, including when timers resume after sleep.
- Source pause and notification snooze: new capture/dispatch stops within 1 second in normal runtime operation; in-flight external work is separately labeled and must not produce a new local intervention afterward.
- Presence pilot: no more than one false present/away transition per hour across representative work sessions; missed departures and source-unavailable time are recorded separately.
- Performance: aim for no more than 5 percentage points of additional total CPU utilization over the Phase 0 baseline for metadata/reducer work, and no more than 250 MiB additional steady-state RAM excluding a newly selected model. Measure CV/model costs separately before selecting them.
- Resource stability: no monotonic memory, queue, task, handle, or temp-file growth during a four-hour soak; test cleanup at expiry/restart.
- Policy behavior: zero duplicate dispatches and zero automatic penalties from the new passive context path in deterministic tests.
- User usefulness: record accepted/dismissed/snoozed interventions during the opt-in pilot; reduce or disable rules that the user finds intrusive even if their detector tests pass.

LLM response latency and cloud spending depend on the chosen provider/model. Establish a measured baseline and a user-selected daily budget in Phase 0/2. Routine sensor updates must cause zero LLM calls; at most one generation attempt per approved intervention before fallback or skip.

### 12.3 Verification method

Use fake clocks and replayed structured observations for reducers/policies; adapter fakes for OS failures; real local process tests for IPC; contract tests for Core projections; and physical-device tests for camera, audio, browser permission, push, and mobile lifecycle.

Run focused tests for changed behavior first, followed by affected shared assistant/session/phone regressions. A document-only edit does not require application test runs. Passing fake tests cannot certify Discord command propagation, physical phone behavior, real database constraints, or provider delivery.

Maintain a release evidence sheet containing the commit, configuration flags, environment, exact command/device procedure, expected result, actual result, and unresolved limitation. Use pass, fail, unavailable, and not tested distinctly.

## 13. Rollout, recovery, and operation

### 13.1 Enablement sequence

1. Integrated baseline with all new companion flags off.
2. Local context display only; compare observations against the user's real session.
3. Reactive assistant context enabled.
4. Proactive rules in shadow mode.
5. One local proactive rule enabled at a time.
6. Minimal context sharing and foreground phone voice in staging.
7. Separately opted-in push/Discord notifications.
8. Optional history, scores, rewards, and native telemetry in independent releases.

### 13.2 Availability behavior

| Failure | User-visible behavior | Recovery |
|---|---|---|
| Camera denied/unplugged | Camera unavailable; presence unknown; desktop context can continue. | Re-enable/reconnect and collect fresh frames. |
| Microphone unavailable | Typed chat remains usable; recording control explains the unavailable input. | Re-grant permission or select another device. |
| Core offline/auth rejected | Core-derived missions/agents unavailable; local context remains usable. | Authenticate/connect successfully; do not relabel old data as new. |
| Model provider unavailable | Deterministic factual answer or short fallback; no repeated autonomous retry loop. | Next bounded user request or eligible intervention can try again. |
| Laptop offline | Phone shows unavailable/last seen; no promise of fresh laptop processing. | New boot/heartbeat and fresh observations. |
| Context publisher crashes | Core record expires; original chat delivery remains separately monitored. | Restart publisher with correct sequence/boot state. |
| Delivery acknowledgment uncertain | Notification inbox shows unknown attempt; avoid duplicate escalation. | Provider-specific reconciliation where supported. |
| History store fails | No invented totals; current context continues without new durable summaries. | Restore storage, preserving unobserved intervals as gaps. |

### 13.3 Operational diagnostics

Expose component state and non-secret reason codes: enabled/disabled, healthy/unavailable, last valid observation, source expiry, rejected event count, queue occupancy, current suppression reason, model budget, and delivery outcome.

Logs should identify the subsystem and failure category without dumping screenshots, audio, prompts, transcripts, credential values, or full sensitive server payloads. A diagnostic check should distinguish unconfigured status access, authentication rejection, network failure, and invalid/stale data without weakening the user-facing fail-closed behavior.

### 13.4 Recovery ownership

The laptop owner controls sensing and local modes. Core controls account/device binding and durable action records. Each delivery adapter owns its receipts. An operator should be able to disable a single sensor, rule, channel, or integration without disabling ordinary chat.

All new migrations must be reviewed against existing data. Turning a feature flag off should stop its behavior immediately; schema rollback and data deletion are separate deliberate operations.

## 14. Decisions, risks, and deferred scope

### 14.1 Recommended defaults for the first release

| Decision | Recommended default | Why |
|---|---|---|
| Laptop platform | Windows first | Matches the inspected OS-specific implementation. |
| Companion mode | Quiet during initial setup; user enables selected rules | Allows observation accuracy to be assessed first. |
| Camera observation | Existing enabled camera path with explicit desk calibration | Reuses computation and avoids competing captures. |
| Desktop collection | App category, idle, lock; no raw keystrokes/titles | Enough to improve context with a bounded data surface. |
| Screen images | User-requested first | Metadata and declared intent cover many cases without capture. |
| Conversational memory | Preserve existing approval model | Matches the user's prior decision. |
| New activity history | Off | Daily retention is a distinct feature decision. |
| Phone | Existing PWA + Discord; foreground voice next | Supports both requested platforms before native investment. |
| Native mobile order | Android first if usage telemetry is required | The specific usage-access workflow is directly supported, subject to consent and lifecycle constraints. |
| Scoring/rewards | Off until summaries are trustworthy | Avoids incentives built on incomplete observations. |
| MCP | Defer until a named connector is useful | Native current-context tools are sufficient for early phases. |

### 14.2 Main risks and responses

| Risk | Practical response |
|---|---|
| False camera inference | Healthy-source checks, dwell, unknown states, corrections, and physical-camera pilot. |
| Incorrect productivity judgments | Keep observations, declarations, and interpretations distinct; require declared goals for scoring. |
| Repeated speech from old and new components | One companion dispatch controller and explicit legacy-path reconciliation. |
| New observation accidentally triggers Core penalty | Isolate passive event contract and prove no mutation in integration tests. |
| Separate assistant processes disagree | Explicit snapshot provider/projection with revision, boot ID, and freshness. |
| Feature code exists only in another branch | Phase 0 integration inventory and schema compatibility tests. |
| Overstated “finished” or “needs input” | Require explicit producer lifecycle evidence; keep unsupported capability disabled. |
| Context survives longer than intended | Read-time expiry plus verified physical cleanup; audit prior stores separately. |
| Phone lifecycle limits | Foreground interactions, expiring snapshots, physical-device tests, and typed fallback. |
| Increasing battery/model cost | Sample locally, coalesce events, budget model calls, measure on actual devices. |
| Persona becomes unhelpful | Fact-first response examples and correction/snooze evaluations. |

### 14.3 Explicitly deferred or excluded

- Apartment-wide awareness, home arrival claims, smart-home control, and physical punishment devices.
- Medical diagnoses, emotion certainty, inferred sleep tracking, or health scoring from webcam cues.
- Unrestricted desktop automation or shell execution by conversational prompt.
- General background recording of phone microphone/camera or unrestricted observation of other apps.
- Automatic long-term transcript storage or automatic conversion of activity observations into approved memories.
- Universal app blocking, OS lockout, or non-overridable entertainment enforcement.
- A new vector database, generic autonomous agent loop, or public Vision MCP server.
- Always-available cloud execution of the laptop assistant while the laptop sleeps.

These can only become future work through a specific new product requirement and a separate design; they are not hidden obligations of this roadmap.

## 15. Source register and planning review

### 15.1 Primary sources

The research companion records access limitations and additional details. Platform behavior must be rechecked when implementation begins, particularly mobile entitlements and background execution.

| ID | Source | Used for |
|---|---|---|
| S1 | [TikTok creator reference](https://www.tiktok.com/@subject_shinji) and the user-supplied analysis | Product inspiration only; creator implementation and listed demonstrations were not independently verified in this planning pass. |
| S2 | [Microsoft: GetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getforegroundwindow) | Active-window observation on Windows. |
| S3 | [Microsoft: GetLastInputInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo) | Input-idle information and session scope. |
| S4 | [Microsoft: WM_WTSSESSION_CHANGE](https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change) | Session lock/unlock notifications. |
| S5 | [Android: UsageStatsManager](https://developer.android.com/reference/android/app/usage/UsageStatsManager) | App-usage data and explicit Usage Access settings. |
| S6 | [Android: foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types) | Camera/microphone permissions and while-in-use constraints. |
| S7 | [WebKit: Web Push for iOS/iPadOS Home Screen apps](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/) | Installed web-app push support and permission flow. |
| S8 | [WebKit: Meet Declarative Web Push](https://webkit.org/blog/16535/meet-declarative-web-push/) | Push/background execution boundaries. |
| S9 | [Apple: What's new in Screen Time API](https://developer.apple.com/videos/play/wwdc2022/110336/) | Individual authorization and Screen Time framework roles. |
| S10 | [Apple: Requesting the Family Controls entitlement](https://developer.apple.com/documentation/familycontrols/requesting-the-family-controls-entitlement) | Distribution entitlement and app-extension requirements. |
| S11 | [Apple: DeviceActivityReport](https://developer.apple.com/documentation/deviceactivity/deviceactivityreport?language=objc) | Ordinary report-extension sandbox and sensitive-data export restrictions. |
| S12 | [Apple: activityData(filteredBy:using:)](https://developer.apple.com/documentation/deviceactivity/deviceactivitydata/activitydata%28filteredby%3Ausing%3A%29) | Separate, conditional activity-data export API. |
| S13 | [Apple: Family Controls App and Website Usage entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.family-controls.app-and-website-usage?changes=_7) and [approvedWithDataAccess](https://developer.apple.com/documentation/FamilyControls/AuthorizationStatus/approvedWithDataAccess?changes=__3) | Additional entitlement, regional eligibility, and authorization constraints. |

### 15.2 Repository evidence pointers

Inspected Vision code: `D:/ascend-vision/.worktrees/phone-chat-discord/ascend-vision/assistant/service.py`, `assistant/session_store.py`, `assistant/memory.py`, `integrations/status_shelf.py`, `integrations/discord_phone_bot.py`, `integrations/phone_worker.py`, `integrations/warning_state_machine.py`, `screen_auditor.py`, `session_manager.py`, `voice_listener.py`, `main.py`, and `config.py`, all relative to that Vision project directory after the first full path.

Inspected Core evidence: `D:/ascend-core/.worktrees/phone-chat-core/server/cli/status_read_credentials.py`, `server/schemas/service_status.py`, the phone-chat feature/test inventory, and `D:/ascend-core/.worktrees/status-completion-history/server/schemas/service_status.py` plus `server/services/status_service.py` in that completion worktree.

These pointers describe where evidence was inspected. Proposed modules in section 9 are not reported as existing code, and no deployed version is certified by these file references.

### 15.3 Coverage review

| User requirement | Covered by |
|---|---|
| Luna-inspired companion | Sections 1–2 and phases 1–3. |
| Laptop webcam/microphone/screen scope | Sections 5, 7–8; phases 1–3. |
| Possible Android/iOS phone use | Section 8; phase 4 and optional phases 7A/7B. |
| Fit existing Ascend Vision/Core/Hub | Sections 3–4 and 9; phase 0 integration baseline. |
| Existing memory and separate sessions | Sections 4 and 6; phases 2/4/5. |
| Detailed phased plan | Sections 10–13, including interfaces, file ownership, tests, exit criteria, and rollback. |
| Skills/MCP future direction | Phase 8 and explicit deferrals. |
| No implementation now | Documentation-only status; all implementation tasks remain unchecked. |

**Recommended implementation entry point:** Phase 0, followed by Phase 1. The phase-1 demonstration should show a live, inspectable context card and correct temporary intent handling before any new unsolicited coaching is enabled.
