# Browser Automation B4 — Implementation and Acceptance Audit

Date: 2026-09-29. **Local B4-A/B4-B/B4-C implementation is present; B4 release
acceptance is NOT complete. B5 rollout is not yet authorized or ready.**
Staging, minute-level cleanup scheduling, physical-phone testing and individually
accepted B3 site workflows remain explicit gates. All browser enable/write flags
remain off. No production data, deployment, slash-command synchronization, commit
or push was performed.

## Worktrees and evidence identity

| Repository | Selected worktree | Branch | Baseline HEAD |
|---|---|---|---|
| Vision | `D:/ascend-vision/.worktrees/laptop-companion-context/ascend-vision` | `codex/laptop-companion-context` | `3ee6985e9e0447f5d0333c32bbe800453d44ca9b` |
| Core | `D:/ascend-core/.worktrees/phone-chat-core` | `codex/phone-chat-core` | `d38846002a0f36fe1659c09fd3b2abeb53cb9648` |

Both trees already contained substantial user-owned uncommitted changes. Tests
exercise the current dirty source, not those baseline commits alone. Unrelated
changes were preserved. Agent reports/reviews and local fixtures are in the
Vision worktree parent's `.superpowers/sdd/2026-09-28-vision-browser-b4-remote-control`
and the Core worktree's `.superpowers`; these are local evidence, not deployment proof.

## Implemented paths

| Slice | Local outcome | Live acceptance |
|---|---|---|
| B4-A PWA public research | Authenticated durable Core queue; dedicated outbound worker; broker execution; visible-tab polling; progress/results; pause/resume/stop; offline/unknown distinction. | Open. |
| B4-B selected-origin actions | Canonical immutable review; exact bounded full values/origin/target/laptop; separate approve/reject; autosave warning; one-use dispatch authority; stale/paused/reobserved/expired reviews denied. | Keep writes off until accepted B3 workflow plus staging/physical gates. |
| B4-C Discord | DM-only start/status/stop/clear; stable actor/interaction UUIDs; provider consent; current link generation; newchat session end; configured task-only PWA handoff. No Discord action approval. | Commands not synced; actual DM/button/handoff acceptance open. |
| Source authority/privacy | Owner/device/link generation/channel/session/laptop/boot/lease/fence checks; source expiry/end/revoke/unlink erases task content and denies later publication/dispatch; temporary results never become approved memory. | Local transactional proof below. |
| Handoff | Random hash-only one-use token, URL fragment, authenticated explicit consume into a separate PWA reviewer session; one task only. | Local API/SQL/real-browser fixture proof; physical acceptance open. |
| Recovery | Content-free journal; fresh boot fence; uncertain consequential outcome becomes unknown without automatic repeat; journal terminal marking requires exact positive Core acknowledgment. | Local real Chromium crash proof below. |
| Cleanup | Authenticated bounded sweep endpoint; expiry erasure, starvation-safe batches, expired grants and 24h tombstone pruning. | A minute-level staging scheduler must still be provisioned and observed. |

The final review fixed expiry during database lock waits, all-state result erasure,
cleanup batch starvation, source/reviewer deadline clipping, a PostgreSQL
broker-restart timestamp cast, stale newchat/logout views, malformed completion
receipts, misleading stale waiting labels, and phone review clipping. Discord
consent is disposed after a fixed 60 seconds even while queued behind its source
lock; expired queued consent cannot enqueue a task. Redelivery receipts now use
the current task deadline, not the broader source-session lifetime.

## Verification evidence

### Python/TypeScript/build checks

- **Vision full suite:** `python -m pytest tests -q --basetemp <D-drive task temp>`:
  **1,060 passed**, one Google SDK deprecation warning. An earlier full run exposed
  ten Hub-status test failures because their import-time fixture timestamp aged
  beyond the real freshness TTL. Only the test fixture was corrected to refresh
  per test; production stale-status rejection was not weakened.
- **Vision browser + Discord/phone subset:** 283 passed, including the three actual
  Chromium crash scenarios. Discord adapter/chat subset independently passed 105.
- **Core browser + phone/pairing/worker-auth:** 135 tests in the final broader
  passing run, including all 16 opt-in real PostgreSQL scenarios.
- **Core broader server:** final rerun **377 passed**, two subtests passed, with
  `--ignore=tests/test_aira_registry.py`. The unrestricted 381-test run stalled in
  the unrelated AIRA registry dashboard-delegation area and was interrupted.
  **Do not describe the unrestricted Core suite as passing.** Its five excluded
  tests remain a separate repository verification issue.
- **Client full suite:** 35 test files, **152 passed**. TypeScript `--noEmit` passed.
- **Client production build:** `next build --webpack` passed; 52 static pages,
  including `/browser-tasks/review`. The default Turbopack local dev startup refused
  this pre-existing external node_modules junction; webpack was used without
  changing project configuration. The workspace-root warning remains advisory.
- **Prisma schema validation:** passed with both database URL variables pointing
  only at the isolated loopback database.
- Both repository `git diff --check` checks passed (Windows LF/CRLF advisories only).

The missing already-declared happy-dom test runtime was installed in task-local
tooling and linked into the existing dependency checkout; no package manifest or
lockfile was changed for that setup.

### Actual PostgreSQL, not in-memory SQL mocks

The opt-in suite `server/tests/test_browser_task_repository_postgres.py` connects
only to the explicitly named loopback database `ascend_browser_b4_test`, port
54329. PostgreSQL 17.11 ran as a task-owned process, not a Windows service and not
an external/production database. Docker is available but was not needed.

Each scenario creates an isolated owned schema, executes the full pre-B4 HEAD
schema, inserts a prior phone-chat row, applies **both additive B4 migrations
twice**, confirms the prior row is unchanged, and removes only its own schema.
Production raw repository SQL executes through an asyncpg adapter using UTC
sessions. The baseline lacks unrelated uncommitted phone-audio migrations; this
is a B4 additive-compatibility proof, not validation of all dirty migrations.

Coverage: concurrent claim; four waiting plus one active; immutable/idempotent
approval and conflicting rejection; single-use dispatch and lost-response retry;
paused renewal; simultaneous stop/approve; one-use handoff and unauthorized
owner/device/other-task access; reviewer session end; source end and late-event
rejection; unlink/relink generation; revoked device; broker restart; completed
result/source-expiry erasure; small-batch progress; tombstone pruning; expiry
during a real row-lock wait; Discord redelivery deadline consistency.

The revocation scenario changes the actual device row; unlink/relink invokes the
production generation-invalidation SQL under the owner lock. It does not claim
an authenticated deployed logout/unlink UI race was exercised.

### Real broker/Chromium termination boundaries

`tests/test_browser_remote_crash_acceptance.py` launches the real broker service,
executor, headless Chromium and journal in a child process against a counted
loopback fixture website. It terminates that owned process before the effect,
after the actual site POST, and before Core acknowledgment. Recorded effects are
respectively **0, 1, 1**; restart marks uncertainty and rejects automatic replay.
All three scenarios passed. Only owned broker/browser descendants are terminated.

The planner and Core HTTP permission responder are deterministic fixtures.
This proves actual local browser side effects/recovery, **not** a live provider,
deployed Core transaction, third-party website commit or signed-in account.

### Actual Next.js UI in Chromium, simulated phone viewport

Task-local `b4-ui-smoke.py` tested the real Next app at loopback using a 390x844
touch viewport. **Every API call was intercepted with fixtures**; no external
Core/provider/account traffic occurred. Verified:

- Existing auth hydration, new reviewer session and explicit handoff consumption.
- Fragment removed immediately; no automatic consume before confirmation.
- Full 1,800-character value, target/origin/laptop and autosave warning.
- Review content and decision buttons fit the viewport; buttons are at least 44px.
- Keyboard rejection carries the exact action/digest/session and refreshes state.
- No cross-channel chat/session storage replacement.

The first visual inspection found clipped values/buttons despite a non-overflowing
body. The regression now checks the actual decision-button bounds and review
container. Grid sizing/wrapping was corrected and visually rechecked.
Screenshot: Core task-local `.superpowers/b4-mobile-review.png`.
**This is not an actual Android/iOS or installed-PWA test.**

## Review and operational status

Core authority, Discord adapter and final broker/PWA independent scoped reviews
all passed after corrections; reports are `task-6-review.md`, `task-8-review.md`
and `task-5-7-review.md`. The latter is a concise PASS; the earlier reports record
the resolved findings in detail. Tested authority/execution/UI SHA256 hashes are
in local `final-source-hashes.md` beside these reports.
[Remote operations](../browser-remote-operations.md) records identities, defaults,
TTL/retention, minute cleanup, sleep/offline meaning, handoff, recovery and rollback.

No live credentials were used; no feature flags were enabled; no Discord
application commands were synchronized. Existing Docker setup is untouched.
Local database/browser fixtures are separate from the user's staging/production.
The owned Next dev server and PostgreSQL process were stopped after verification;
their fixture data/scripts/reports remain recoverable. No user Docker process,
container, volume or other worktree was removed.

## Remaining gates — deliberately not marked complete

1. Resolve/verify the five unrelated Core AIRA registry tests before a claim that
   the entire Core repository suite is green.
2. Select and explicitly authorize isolated staging; provision fresh owner,
   device/link, Core/PWA configuration, dedicated worker identity and a real
   minute-level cleanup scheduler. Record exact deployed revisions/URLs/database.
3. Demonstrate PWA research/control/result; selected-site review/reject/expiry;
   session/device/logout/unlink boundaries; sleep/wake/outage/stale heartbeat;
   hidden-tab resume, duplicate sends and erased-result refresh in that staging.
4. Explicitly sync Discord staging commands and verify actual DM consent,
   start/status/stop/clear/newchat plus task-specific PWA handoff.
5. Test an actual Android or iOS phone, recording model/browser and installed-PWA
   resume. Do not extrapolate to the other platform.
6. Close live-provider/voice and individually accepted B3 signed-in workflow gates
   where still open. Never enable all-site writes from the fixture tests.

**Next step:** provision and run these B4 acceptance gates before B5 rollout.
B5 planning can proceed independently; B5 implementation/enablement is not part
of this request.

## Reproduction commands

Use the selected worktree, not an arbitrary main checkout. Set TEMP/TMP to the
task's D-drive directory if C-drive space is constrained. The database suite is
skipped unless its explicitly guarded loopback URL and full baseline are provided.

Vision, from the selected Vision root:

```powershell
python -m pytest tests -q --basetemp <unique-task-temp-directory>
python -m compileall -q browser integrations/browser_task_worker.py integrations/discord_browser_commands.py integrations/discord_phone_bot.py
git diff --check
```

Core server, using the existing main-checkout test interpreter:

```powershell
$env:PYTHONPATH='D:/ascend-core/.worktrees/phone-chat-core/.superpowers/b4-postgres/python'
$env:ASCEND_BROWSER_TEST_POSTGRES_URL='postgresql://ascend_b4_test@127.0.0.1:54329/ascend_browser_b4_test'
& 'D:/ascend-core/server/.venv/Scripts/python.exe' -m pytest tests -q --ignore=tests/test_aira_registry.py -o faulthandler_timeout=45
```

Core client:

```powershell
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc --noEmit
node node_modules/next/dist/bin/next build --webpack
```

The actual Next UI smoke helper requires the loopback webpack dev server on
port 3108; it intercepts every API request. Preserve that isolation when rerunning
the helper. The task-owned PostgreSQL process can be restarted from its retained
data directory; no Docker container or production service is required.
