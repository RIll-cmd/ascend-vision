# Remote browser operations — B4

Date: 2026-09-29. Single owner, one selected laptop. Source implementation is opt-in;
this guide does not authorize deployment or real-account writes. See the
[acceptance audit](audits/2026-09-29-browser-b4-implementation-status.md).

## Ownership and channels

Core owns durable tasks, source sessions, identity, approvals, fencing and cleanup.
Vision polls Core outbound; its local broker alone owns Chromium. There is no phone
connection to a camera, shell, Playwright/CDP endpoint, or local browser listener.
PWA and Discord sessions remain separate. A Discord handoff grants access to one
task, not a listing of Discord tasks or access to the Discord chat transcript.

Remote scope is public research, or an individually enabled local site scope with
exact-origin fill/select/click. Each consequential action requires exact phone
review and a fresh Core dispatch permit. Passwords, CAPTCHA/MFA, remote files,
profile configuration and arbitrary scripts are not phone capabilities. Login
and manual takeover happen in the visible laptop browser.

## Configuration, without secret values

Provision fresh staging-only credentials through the existing environment/secret
store. Never paste tokens into Markdown, goals, logs or URL queries. The dedicated
browser worker credential must differ from phone, Discord, context, notification,
status and cron credentials. Previously exposed credentials must not be reused.

| Location | Setting | Requirement |
|---|---|---|
| Core + Vision | `ASCEND_PHONE_OWNER_ID` | Same single owner. |
| Core + Vision | `ASCEND_BROWSER_LAPTOP_ID` | Same chosen laptop identifier. |
| Core + Vision | `ASCEND_BROWSER_WORKER_TOKEN` | Dedicated random worker bearer; no PWA/Discord use. |
| Core | `ASCEND_BROWSER_REMOTE_ENABLED` | Defaults off; `true` only for accepted remote research. |
| Core | `ASCEND_BROWSER_REMOTE_WRITES_ENABLED` | Defaults off; independently gated site actions. |
| Vision | `ASCEND_PHONE_CORE_URL` | Exact staging Core HTTPS origin. Loopback HTTP is development-only. |
| Vision | Provider's existing API-key environment variable | Selected provider must be provisioned; no automatic provider change. |
| Discord host | `ASCEND_PHONE_PWA_ORIGIN` | HTTPS origin only; no path, query, fragment or credentials. |
| Discord host + Core | Existing `ASCEND_DISCORD_BRIDGE_TOKEN` | Preserve dedicated paired Discord bridge identity. |
| Core cleanup scheduler | `CRON_SECRET` | Separate secret; must not equal browser worker token. |

Vision `config.yaml` retains `browser_automation.enabled`, `b3_enabled`,
`remote_enabled` and `remote_writes_enabled` **false** by default. Public research
needs local `enabled` and `remote_enabled`, but not remote writes. Site actions
also require `b3_enabled`, `remote_writes_enabled` and a locally configured
`remote_scopes` descriptor. Remote callers cannot supply that descriptor or choose
a browser profile/provider. Match worker/Core settings before starting Vision.

Apply both additive migrations in order using the repository's normal migration
workflow: `20260928_browser_tasks_b4`, then
`20260929_browser_review_authority`. Validate on the chosen isolated database first.
Use UTC database sessions for the existing Prisma `TIMESTAMP(3)` schema. Do not
apply these instructions to production as part of a smoke test.

## Limits and availability

- Four waiting tasks plus one active task per laptop; claim is serialized.
- Queue/source-session lifetime: at most 24 hours. Active task: 180 seconds,
  including time spent paused or waiting for review.
- Action review: at most 120 seconds and never beyond task/source authority.
- Dispatch response: one-second permit clipped to source, reviewer, grant,
  lease, proposal and task deadlines; the broker also enforces a two-second total
  round-trip limit. It discards late permits.
- Discord provider-consent intent: 60 seconds in memory only, including time
  waiting on its source lock. Restart/reset/shutdown discards unconfirmed intent.
- Handoff: 32 random bytes, hash-only Core storage, five minutes to consume once.
  Consumption requires an authenticated linked owner/device and new PWA browser
  session. Task-specific access lasts at most ten minutes, further capped by the
  source/reviewer/task lifetimes.
- Result retention: at most one hour, further capped by source expiry/end or
  explicit clear/revocation. Content-free terminal tombstones: 24 hours.

An online heartbeat means the configured worker recently reported availability;
it is not proof of successful task completion. A sleeping laptop cannot execute.
Accepted tasks can queue in Core; the bot itself cannot receive new Discord
commands while its hosting laptop is asleep. A cloud-hosted bot is a separate
hosting choice, not an always-online laptop browser.

## Required cleanup scheduling

Provision a scheduler that calls authenticated
`POST /api/cron/browser-tasks-sweep` at least once per minute. Use
`Authorization: Bearer <CRON_SECRET>` from the secret store, or the existing
`x-cron-secret` header. `batch_size` defaults to 100 and accepts 1–500.

The endpoint expires source sessions/tasks/leases, erases expired payloads and
proposal decisions, removes expired grants and prunes old content-free tombstones.
Batches select eligible payload/child rows, so already-erased tombstones cannot
starve later cleanup. Monitor failures and backlog; if a batch fills up, repeat
until caught up. Expiry checks deny access independently of sweep timing, but
denial is not a substitute for deleting retained content.

The current deployment cron configuration does **not** provision this minute
schedule. Configure the selected staging host's scheduler and verify actual
invocations before enabling the feature. Do not assume a platform tier supports
minute-level cron or silently purchase one. No scheduler was provisioned here.

## PWA and Discord use

PWA: sign in, connect device, select Browser beside Chat, inspect laptop/scope,
approve provider use and submit. Progress polls while visible and refreshes on
return. Stop means requested until the laptop acknowledges. End session/new chat
clear source context; failed offline revocation retains IDs only for retry and
must not be described as a confirmed remote stop.

Discord, DMs only: `/browser start goal`, `/browser status task_id`,
`/browser stop task_id`, `/browser clear task_id`. First start may require the
provider-consent button. `/newchat` ends that Discord browser source session.
Ordinary `/ask`, `/context`, and Hub `/status` keep their existing meanings.

Detailed output/review uses `/browser-tasks/review#handoff=...`. Open it in the
linked authenticated account, explicitly approve provider use and click **Open
this task**. The fragment is removed from browser history and is not persisted.
For an unauthenticated link, sign in and reopen a fresh Discord link. No emoji,
free-text “yes” or Discord guild message can approve a site action. Slash commands
are synchronized only as an explicit staging rollout step, never on import.

## Recovery and disable procedure

After a broker/Core interruption, inspect `unknown` outcomes on the laptop/site.
Never automatically retry an uncertain fill/select/click. The local journal keeps
identifiers/hashes/action outcomes, not page text or form values. Reconciliation
records uncertainty; it does not prove the website committed or undo a write.
Broker restart uses a new boot ID and invalidates the old execution authority.

Disable remote writes in Core first; this denies subsequent consequential
permits. Then disable new remote starts, request stop/end for active source
sessions, confirm terminal/unknown outcomes and run cleanup. Stop cannot undo an
effect already begun. Keep additive tables dormant rather than dropping live data.
Stopping the Vision worker must not clear uncertain-action evidence prematurely.
Status/stop controls remain useful while new starts are disabled.

## Staging/physical-phone acceptance

Record the actual isolated Core/PWA URLs, database branch/schema, owner/device/link,
worker laptop, exact deployed revisions and evidence IDs. Demonstrate research,
pause/resume/stop/result, review/rejection/expiry, logout/unlink during review,
sleep/wake/outage, hidden-tab return, repeated submission and erased-result refresh.
Demonstrate Discord DM commands and the task-specific PWA handoff without merging
sessions. Test an actual Android **or** iOS phone and installed-PWA resume; record
the device/browser and do not claim the other platform passed. Enable site writes
only for individually accepted B3 workflows. Local mocks/desktop mobile emulation
are useful checks, not this release acceptance.
