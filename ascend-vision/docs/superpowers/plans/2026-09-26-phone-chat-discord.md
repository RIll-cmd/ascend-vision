# Phone Chat Discord Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional official Discord bot that privately forwards only linked-owner DM slash commands to the already-shipped PWA/Vision phone-chat handler, without creating a second assistant or retaining transcripts.

**Architecture:** Core issues and consumes short-lived, single-use pairing codes, owns the Discord-user-to-single-owner link, and validates every command. A separately launched Vision-host process uses Discord Gateway slash interactions and the PWA plan's `PhoneMessageHandler`; it does not read messages or open a public Vision endpoint. Discord remains optional and best-effort: when the bot process is down, only the PWA remains available.

**Tech Stack:** Python 3.11+, `discord.py==2.7.1` application commands, FastAPI/Pydantic, Prisma Python/PostgreSQL, pytest. Keep Discord dependencies in an optional requirements file and out of the default Vision install.

**Spec:** `docs/superpowers/specs/2026-09-26-phone-chat-dual-channel-design.md`

**Prerequisite:** Complete and review `docs/superpowers/plans/2026-09-26-phone-chat-pwa.md`; this adapter consumes its `PhoneMessageHandler`, owner binding, and Core deployment contract.

## Global Constraints

- Use an official Discord bot/application only; never automate a personal Discord account.
- Register `/ask`, `/status`, `/newchat`, and `/link` as application commands. Accept them only in a direct/private DM; reject guild interactions server-side even if command UI restrictions are misconfigured.
- Do not enable message-content, member, or presence intents; do not subscribe to/read arbitrary messages.
- Defer slash-command responses immediately, then edit the deferred ephemeral response. Keep prompts/replies out of logs, bot persistence, and the approved-memory store.
- Require a valid Core link for every command except `/link`; Core validates link status on each ask/status/newchat request and fails closed when unavailable.
- Pairing code: cryptographically random, single-use, hashed at rest, five-minute lifetime, bounded/rate-limited attempts, consumed atomically, never logged or echoed after submission.
- Exactly one Discord account may be linked to the configured Core owner at a time; unlink/revoke from authenticated Core settings takes effect on the next command.
- `/newchat` clears only `(owner_id, "discord_dm", session_id)` RAM turns. It does not affect PWA sessions or approved memories.
- No Core mutations, memory approve/edit/delete, shell/code, arbitrary tools, skills, MCP, voice, attachments, guild support, or transcript retention.
- Do not deploy/register production bot secrets or apply database migrations as part of this plan. Staging credentials and the official application must be operator-provisioned before live validation.

---

## File Map

| Repository | File | Responsibility |
|---|---|---|
| Core | `server/prisma/schema.prisma` | Add hashed pairing challenge and one-owner Discord link records, with unique Discord user binding and expiry indexes. |
| Core | `server/schemas/phone_chat.py` | Add strict pairing/link DTOs to the existing phone-chat schema module. |
| Core | `server/services/phone_chat_pairing.py` | Create/expire/consume one-time codes, bind/unbind Discord user, validate active link without exposing owner selection. |
| Core | `server/routers/phone_chat.py` | Authenticated pairing creation/status/revocation endpoints and credential-protected Discord bridge consume/verify endpoints. |
| Core | `server/tests/test_phone_chat_pairing.py` | Hash-at-rest, expiry, replay, attempt limits, atomic consumption, one-link-only, and revoked-link tests. |
| Core client | `client/src/features/phone-chat/DiscordLinkCard.tsx` | Authenticated PWA settings UI to create a pairing code, show its short expiry once, inspect linked state, and revoke link. |
| Core client | `client/src/features/phone-chat/DiscordLinkCard.test.tsx` | Pairing display/revoke behavior and no code persistence. |
| Vision | `ascend-vision/integrations/discord_phone_bot.py` | Discord Gateway client, DM-only slash commands, acknowledge/defer/edit response, Core link checks, handler adapter. |
| Vision | `ascend-vision/discord_bot.py` | Minimal optional process entry point and graceful shutdown. |
| Vision | `ascend-vision/requirements-discord.txt` | Optional pinned Discord client dependency; do not add it to default `requirements.txt`. |
| Vision | `ascend-vision/config.py` | Optional bot token/application settings and Core bridge URL/credential; bot disabled unless all required settings exist. |
| Vision | `ascend-vision/tests/test_discord_phone_bot.py` | Command allowlist, DM-only check, defer-before-work, pairing, link/revoke, session isolation, safe failures, and no transcript logging. |
| Vision | `ascend-vision/docs/phone-chat-discord-operations.md` | Operator setup, minimal Discord permissions/intents, Discord data-path disclosure, optional process run/stop, token rotation, and offline fallback. |

## Cross-system Contract

- `POST /api/phone-chat/discord/pairing-codes` — Core browser-authenticated, active device and bound-owner required; return `{code, expiresAt}` once. Persist only a keyed cryptographic hash, not plaintext.
- `GET /api/phone-chat/discord/link` — Core browser-authenticated; return only `{linked: boolean, discordLabel?: redactedLabel}`.
- `DELETE /api/phone-chat/discord/link` — Core browser-authenticated; revoke the single current link.
- `POST /api/phone-chat/worker/discord/consume-link` — dedicated Discord-bridge credential; body `{code, discordUserId}`; Core consumes once and derives owner from challenge record; response `{linked: true}` or generic rejection.
- `POST /api/phone-chat/worker/discord/verify-link` — same dedicated credential; body `{discordUserId}`; response `{linked: boolean, ownerId?: string}` only for an active linked ID. Never accept owner ID from Discord.

The Vision bot passes verified owner ID from Core, channel `discord_dm`, stable Discord user ID as the per-owner session ID, and bounded text to `PhoneMessageHandler`. The PWA's device ID does not apply to Discord. Core link verification occurs before every chat command; a short in-memory bot cache is not permitted in V1.

## Tasks

### Task 1: Add Core pairing/link persistence contracts

**Files:**
- Modify: `D:/ascend-core/server/prisma/schema.prisma`, `server/schemas/phone_chat.py`
- Create: `D:/ascend-core/server/tests/test_phone_chat_pairing.py`

**Interfaces:**
- `PhoneDiscordPairing`: `id`, `ownerId`, `codeHash`, `attempts`, `expiresAt`, `consumedAt`, `createdAt`.
- `PhoneDiscordLink`: unique `discordUserId`, `ownerId`, redacted display label only if needed, `createdAt`, `revokedAt`.
- `CreateDiscordPairingResponse(code: str, expiresAt: datetime)`, `ConsumeDiscordPairingRequest(code: str, discordUserId: str)`, `VerifyDiscordLinkRequest(discordUserId: str)`; all request models forbid extra fields.

- [ ] **Step 1: Write red pairing-store tests.** Cover random code format/entropy, stored value differs from submitted code, exact five-minute expiry, failed attempt increments, invalid/expired/replayed rejection, successful atomic consume, second Discord ID replacement rejection, duplicate account link rejection, revoke takes effect immediately, and owner ID is never caller-controlled.
- [ ] **Step 2: Run focused tests to verify red.** From `D:/ascend-core/server`, run `python -m pytest tests/test_phone_chat_pairing.py -q`; expected failure is missing pairing service/models.
- [ ] **Step 3: Add schema/migration/DTOs.** Add unique constraints and indexes for `(ownerId, revokedAt)` and `(expiresAt, consumedAt)`; create a migration using repository convention; create code values with `secrets.token_urlsafe(32)` and store HMAC-SHA-256 using a dedicated server secret so database-only disclosure cannot recover valid codes.
- [ ] **Step 4: Run schema validation and DTO tests.** Run the repository's Prisma validate/generate command and `python -m pytest tests/test_phone_chat_pairing.py -q`; model and DTO tests should pass while service tests remain red.
- [ ] **Step 5: Commit pairing contract.** Stage only schema, migration, DTO, and test files; commit `feat(core): define Discord phone pairing contract`.

### Task 2: Implement one-time pairing and authenticated Core endpoints

**Files:**
- Create: `D:/ascend-core/server/services/phone_chat_pairing.py`
- Modify: `D:/ascend-core/server/routers/phone_chat.py`, `server/tests/test_phone_chat_pairing.py`, `server/tests/test_phone_chat_worker_auth.py`

**Interfaces:**
- `create_pairing(owner_id, now) -> CreateDiscordPairingResponse` invalidates prior unconsumed challenges for that owner.
- `consume_pairing(code, discord_user_id, now) -> bool` atomically consumes once, updates attempts, and enforces the one-account rule.
- `verify_link(discord_user_id) -> str | None`; `revoke_link(owner_id) -> bool`.

- [ ] **Step 1: Add red route authorization tests.** Verify pairing creation/status/revoke require normal Core login, account matches the configured owner, device/session-origin protections are applied to writes, and only the dedicated Discord bridge credential can consume/verify.
- [ ] **Step 2: Implement pairing service transactionally.** HMAC the presented code before database lookup; compare hashes in constant time; expire after five minutes; reject after five attempts; consume in the same transaction that creates the Discord link; enforce one active link per owner and no Discord ID linked to another owner.
- [ ] **Step 3: Implement browser routes.** Return plaintext pairing code once and never include it in later reads, logs, or error detail. Add rate limits for challenge creation and status checks. Link revocation clears the unique active binding immediately.
- [ ] **Step 4: Implement Discord bridge routes.** Require a separate `ASCEND_DISCORD_BRIDGE_TOKEN`, compare in constant time, reject owner fields, and return generic errors for invalid/replayed code. `verify-link` returns the owner associated with the supplied Discord user ID only after validating active status.
- [ ] **Step 5: Run Core security tests.** Run `python -m pytest tests/test_phone_chat_pairing.py tests/test_phone_chat_worker_auth.py tests/test_phone_chat.py -q`; expect one-time consumption, auth separation, owner binding, and existing PWA queue behavior to pass.
- [ ] **Step 6: Commit Core pairing endpoints.** Stage pairing service, router/DTO changes, tests, and migration changes; commit `feat(core): add Discord pairing and link verification`.

### Task 3: Add PWA settings UI for link/revoke

**Files:**
- Create: `D:/ascend-core/client/src/features/phone-chat/DiscordLinkCard.tsx`, `DiscordLinkCard.test.tsx`
- Modify: `D:/ascend-core/client/src/features/phone-chat/api.ts`, `D:/ascend-core/client/src/app/(dashboard)/phone-chat/page.tsx`

**Interfaces:**
- `PhoneChatApi.createDiscordPairing()`, `getDiscordLink()`, `revokeDiscordLink()` consume the Core routes and return typed results.
- `DiscordLinkCard` shows a freshly returned code in memory only, visible until expiry/navigation, and a linked/unlinked state.

- [ ] **Step 1: Write red UI tests.** Assert code appears only after explicit user action; code is never written to storage; expired code is hidden; link status is redacted; revoke requires confirmation and then shows unlinked state; API errors reveal no code or backend exception.
- [ ] **Step 2: Run focused Vitest.** From `D:/ascend-core/client`, run `npm test -- src/features/phone-chat/DiscordLinkCard.test.tsx`; expected failure is the missing component/API.
- [ ] **Step 3: Implement the typed API and card.** Use authenticated same-origin fetch, loading/error states, visible five-minute countdown, and explicit revoke confirmation. Do not copy pairing code automatically or put it in URL/query state.
- [ ] **Step 4: Run frontend test/lint/build.** From `D:/ascend-core/client`, run `npm test -- src/features/phone-chat/DiscordLinkCard.test.tsx`, `npm run lint`, and `npm run build`; expect pass without unrelated UI changes.
- [ ] **Step 5: Commit link UI.** Stage only phone-chat API/component/tests and settings integration; commit `feat(core): add Discord phone link settings`.

### Task 4: Implement the optional Discord DM bot adapter

**Files:**
- Create: `ascend-vision/integrations/discord_phone_bot.py`, `ascend-vision/discord_bot.py`, `ascend-vision/tests/test_discord_phone_bot.py`, `ascend-vision/requirements-discord.txt`
- Modify: `ascend-vision/config.py`, `ascend-vision/assistant/phone_handler.py` only if a `discord_dm` channel is not already accepted

**Interfaces:**
- `DiscordPhoneBot.start()`, `close()`, and async interaction handlers for `ask`, `status`, `newchat`, `link`.
- `DiscordCoreClient.consume_link(code, discord_user_id) -> bool`; `verify_link(discord_user_id) -> str | None`.
- `/ask` accepts one bounded `prompt`; `/status` takes no arbitrary target argument; `/newchat` clears only the caller's channel context; `/link` accepts one pairing code.

- [ ] **Step 1: Read current discord.py interaction docs and write red adapter tests.** Use fakes for interaction/Core/handler; assert only the four allowlisted commands are registered; guild interactions are rejected; no message-content intent is enabled; ask/status/newchat verify active link; link consumes code; each handler defers ephemerally before any network/model work; all errors return generic text; and log capture contains no prompt, code, token, or response.
- [ ] **Step 2: Run focused tests to verify red.** Run `.venv/Scripts/python.exe -m pytest tests/test_discord_phone_bot.py -q` from Vision; expected failure is the missing adapter.
- [ ] **Step 3: Add optional dependency and config validation.** Pin `discord.py==2.7.1` in `requirements-discord.txt`; add bot token, application ID, Core URL, bridge token, and owner-binding settings. If any required value is missing, process startup exits with a clear config error before connecting; ordinary Vision runtime does not import discord.py.
- [ ] **Step 4: Implement bot transport and command restrictions.** Use `discord.Intents.none()`; register commands with `@app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)` and explicitly reject every interaction whose `guild_id` is not `None`. Do not define prefix commands or message listeners. `ask` and `status` call `interaction.response.defer(ephemeral=True, thinking=True)` before Core validation or model work, then edit the deferred response once with the result.
- [ ] **Step 5: Implement pairing and command routing.** `/link` sends the code plus the authenticated Discord user ID to Core and does not echo the code; other commands call `verify_link` on every invocation, pass the verified owner and stable Discord user ID into the handler, and use channel `discord_dm`. `/status` supplies a fixed status question rather than accepting arbitrary target text. Run synchronous `PhoneMessageHandler` generation via `asyncio.to_thread` behind a 10-minute interaction timeout so model work cannot block Gateway heartbeats; return a generic retryable response if Core/model work or the Discord interaction expires. `/newchat` calls `clear_session` for that exact owner/channel/session only.
- [ ] **Step 6: Add isolated process entry point.** Start bot only via `python discord_bot.py`; add signal-aware graceful shutdown; do not start it automatically in the Vision desktop process or open an inbound HTTP port. Keep command registration/sync explicit and log only command names and outcome classes.
- [ ] **Step 7: Run adapter tests and Vision regressions.** Run `.venv/Scripts/python.exe -m pytest tests/test_discord_phone_bot.py tests/test_phone_handler.py tests/test_assistant_phone_sessions.py -q`; expect all faked interaction and session-isolation tests to pass.
- [ ] **Step 8: Commit optional adapter.** Stage only the bot adapter/entry point/config/optional requirements/tests; commit `feat(vision): add optional Discord phone bot`.

### Task 5: Document staging setup and validate offline behavior

**Files:**
- Create: `ascend-vision/docs/phone-chat-discord-operations.md`
- Modify: `D:/ascend-core/client/src/features/phone-chat/DiscordLinkCard.test.tsx` or Vision tests only if a missing failure case is discovered.

- [ ] **Step 1: Document safe operator setup.** Explain official Discord application/bot creation, minimum scopes/permissions, enabling only application commands, never enabling message-content intent, disclosing that Discord processes submitted prompts, storing tokens outside the repo, rotating bot/bridge tokens, installing the optional requirements file, and starting/stopping the optional process.
- [ ] **Step 2: Add failure-path tests.** Simulate Discord Gateway offline, Core unavailable, revoked link, expired pairing code, unlinked user, handler timeout, and bot shutdown during an interaction; confirm no false success, no private exception details, and PWA remains unaffected.
- [ ] **Step 3: Run focused verification.** Run Vision Discord/phone tests and Core pairing/phone tests; run the client focused UI tests and build. Do not require real Discord secrets for local verification.
- [ ] **Step 4: Gate live staging.** Only after an operator provides staging Core URL/credentials and a test Discord application, execute link/revoke, DM-only command, cross-session isolation, offline bot, and Core-unavailable tests. No production registration/deployment in this plan.
- [ ] **Step 5: Commit operations docs and failure tests.** Stage only operations docs and related test additions; commit `docs(vision): document Discord phone bot operations`.

## Spec Coverage Review

- Official bot, no self-bot, no broad intents or arbitrary message reads: Tasks 4–5.
- DM-only `/ask`, `/status`, `/newchat`, `/link`; deferred/private responses: Task 4.
- Discord interaction payloads are processed by Discord as an external platform; the bot itself stores no transcript: Tasks 4–5 and operator docs.
- Single-use hashed pairing, five-minute expiry, attempts, atomic consume, one link, revocation: Tasks 1–3.
- Same handler and distinct channel/session, no transcript retention or memory administration: Task 4.
- Core outage, bot-offline, revoke, and staging gates: Tasks 2, 4, and 5.

## References

- [Discord: Application Commands](https://discord.com/developers/docs/interactions/slash-commands) — interaction command contexts and application-command behavior.
- [Discord: Receiving and Responding to Interactions](https://discord.com/developers/docs/interactions/receiving-and-responding) — acknowledgement, deferred response, and interaction-token lifecycle.
- [discord.py: Interaction API](https://discordpy.readthedocs.io/en/stable/interactions/api.html) — `CommandTree`, DM-context restrictions, and deferred interaction responses.
- [discord.py 2.7.1 on PyPI](https://pypi.org/project/discord.py/2.7.1/) — version available when this plan was written.
