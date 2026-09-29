# Ascend Vision Phone Chat: PWA and Discord Design

**Status:** Approved by the owner on 2026-09-26.

## Goal

Let the single owner of an Ascend Vision installation chat with the same assistant from an authenticated Ascend Hub phone PWA or an optional Discord DM bot, while keeping the two channels' short-term conversations separate and sharing only the owner's approved memories.

## Scope and approach

This is one phone-chat capability with two independently releasable transports. The PWA is the supported, durable channel and is delivered first. Discord is an optional, best-effort convenience adapter added after the PWA identity/linking flow exists. Both enter one channel-aware Vision message handler; neither creates a second assistant or bypasses the Phone V1 policy.

V1 is single-owner. It supports ordinary chat, current read-only Ascend Hub status, approved-memory recall, and creation of pending memory proposals that still require explicit dashboard approval. It does not approve, edit, or delete memories; execute Core writes; control Vision devices; run shell/code; or call arbitrary tools, skills, or MCP servers. Voice, attachments, push notifications, and native applications are out of scope.

## Current project constraints

- The Vision candidate already has an `AssistantService`, an approved-memory store, a deterministic read-only Hub-status tool, and a local SQLite dashboard IPC queue. That queue is process-local and is not a safe remote phone API.
- `AssistantService` currently keeps recent turns in one in-memory deque. Phone requests need explicit session keys so one Discord session cannot see PWA context, and neither can see local voice/dashboard context.
- The Vision memory store is local to the paired installation and is not fully account-partitioned. V1 therefore binds one Core owner account to the Vision installation and rejects other owners; multi-user memory isolation is a separate future project.
- Ascend Core has a Next.js client, authenticated user routes, and Prisma/PostgreSQL, but no phone-device registry, durable phone-message queue, or phone PWA manifest today.
- Do not modify the dirty main checkouts. Implementation must use dedicated worktrees and reconcile against the reviewed assistant/status base first. The Gate C completion-history feature is not a prerequisite for phone V1; status answers remain limited to evidence Core actually provides.

## Components and ownership

### Ascend Core

Core owns the single phone-owner binding, revocable phone-device registrations, Discord link mapping, and the durable PWA delivery queue. User-facing PWA routes authenticate with the existing Core account mechanism. Every request is checked against the one owner configured for the paired Vision installation and an active registered device.

The queue is a delivery buffer, not a conversation-history database. A PWA message has a client-generated idempotency ID, owner/device/session IDs, bounded text, timestamps, status, and an expiry. Its states are `queued`, `claimed`, `processing`, `completed`, `failed`, and `expired`. Claiming uses a renewable worker lease; result writes are idempotent by message ID. The Vision worker authenticates with a dedicated, narrowly scoped Core credential that can claim and complete phone jobs only. It makes outbound HTTPS requests; Core never opens an inbound connection to Vision. If a request is processed after the 60-minute Vision session TTL or a Vision restart, it is handled without prior turns; the reply must make the lost temporary context clear rather than imply continuity.

### Vision

Add a `PhoneMessageHandler` boundary shared by both transports. It accepts the authenticated owner ID, channel (`phone_pwa` or `discord_dm`), session ID, and text; enforces the channel policy before delegating to `AssistantService`; and returns a typed reply. The assistant's existing status tool remains read-only. Phone session turns are kept in RAM under `(owner_id, channel, session_id)`, bounded by the existing turn/context budgets, and expire after 60 minutes idle. “New chat” clears only that channel's session. A Vision process restart clears these temporary turns.

An outbound PWA worker claims Core jobs, invokes the handler, and records a terminal result. It checks expiry before execution, renews its lease while working, and retries a lost lease up to three attempts. Duplicate delivery may repeat a model call after a crash, but it must not create multiple visible replies or repeat a consequential action; no consequential action is allowed in this release.

### Phone PWA

Add an authenticated chat surface to the existing Core Next.js client. It uses the normal Core login, registers a revocable device, and talks only to Core over HTTPS. The client displays queued/processing/completed/failed/expired states, receives updates using SSE with polling fallback, and can reconnect to its in-progress messages after refresh. A per-tab `sessionStorage` transcript supports the current session and is cleared by “New chat,” logout, or closing that browser session. “New chat” rotates the session ID so later requests cannot inherit its context. Logout or device revocation cancels queued/unclaimed jobs for that device and prevents further result polling; in-flight work is stopped where possible and its result is discarded. Do not place provider keys, Vision credentials, Discord bot tokens, or shelf credentials in browser code.

### Discord bot

Add an official Discord application/bot as a separate optional service on the Vision host. It connects through Discord's Gateway and accepts only `/ask`, `/status`, `/newchat`, and `/link` application commands in a DM. It does not read arbitrary messages or require message-content access. It verifies the invoking Discord user is linked to the single Core owner, then invokes the same `PhoneMessageHandler` with a Discord-specific session. Replies are ephemeral where Discord supports them. The bot acknowledges an interaction immediately, then edits the deferred response with the result. Discord commands are unavailable when the Vision-side bot process is offline; the PWA is the durable fallback.

## Identity and pairing

The owner signs into the PWA with the existing Core account. Each phone browser has a device registration that can be revoked in Core settings; phone endpoints reject revoked devices even if a broader account token has not expired.

To link Discord, an authenticated owner requests a cryptographically random, single-use pairing code from the PWA. Store only a hash; expire it after five minutes; limit attempts. The owner submits it through `/link` in a DM with the official bot. The bot sends the code and Discord user ID to a Core endpoint authenticated by a dedicated Discord-bridge credential. Core consumes the code atomically and binds that Discord user ID to the owner; the bot cannot choose or submit an owner ID. Only one Discord account may be linked at a time. The owner can revoke the link in Core settings. Commands fail closed if Core cannot validate the link.

## Session, memory, and retention policy

- PWA and Discord use separate session IDs and separate temporary conversation context. Approved memories are shared for the one owner; session turns are never shared across channels.
- The PWA's visible transcript is held in the browser tab's `sessionStorage`, not persistent browser storage. Vision keeps prompt context in memory only.
- Core holds PWA request/reply bodies only while needed for delivery. Pending work expires 24 hours after enqueue; completed bodies are removed after client acknowledgement or 24 hours after completion, whichever comes first. Minimal idempotency/status tombstones may remain for 24 hours and contain no message text.
- No PWA/Discord transcript is added to the persistent approved-memory store automatically. “Remember that …” may create a pending proposal only; the existing explicit dashboard approval is still required before it becomes an active memory. Phone requests cannot approve, edit, or delete memories.
- Discord receives the submitted command text as a platform interaction. Ascend does not retain a transcript for Discord, but users should understand that Discord is an additional service in that data path.
- Never log chat text, pairing codes, interaction tokens, or credentials. Operational logs may contain message IDs, owner/device IDs in redacted form, state transitions, and error classes.

## Failure and recovery behavior

- If Vision is offline, the PWA keeps the request visibly queued until Vision returns or the 24-hour expiry; an expired request is reported as expired, not silently discarded.
- If Core is unavailable, the PWA reports that it cannot submit or verify the request and does not guess an assistant response.
- If Discord's Gateway process is offline, Discord is unavailable; it does not claim the message was delivered. If the assistant fails, the bot returns a generic retryable error without exposing exception details.
- A revoked phone device or Discord link is denied on the next request. Invalid, expired, replayed, or owner-mismatched pairing codes are rejected and rate-limited.
- Failed status-shelf reads produce the existing “cannot verify” response; they never fall through to an LLM guess.

## Security and limits

- Enforce single-owner checks, active-device checks, and channel policy server-side on every request; UI hiding is not authorization.
- PWA writes require the existing Core authentication protections plus CSRF/origin protections appropriate to the existing auth transport, request-size limits, and per-owner rate limits.
- Discord uses a bot account only, DM command contexts only, minimal permissions, allowlisted command names, and a dedicated credential for Core link verification. Never automate a personal Discord account or enable broad guild message reading.
- Limit message text to 4,000 characters to match the current Vision assistant input boundary. Apply per-owner rate limits and bounded queue depth. Do not execute commands, arbitrary skills, shell, or MCP through either channel.
- Any future consequential action requires a separate feature review and an explicit, single-use, short-lived confirmation bound to the owner, device, action, and arguments. A conversational “yes” is not authorization.

## Rollout and acceptance

1. Reconcile and review the existing shared AssistantService, approved-memory, and read-only status candidates in isolated worktrees. Preserve the dirty main checkouts. Do not treat unverified completion-history work as a prerequisite.
2. Implement and test session isolation plus `PhoneMessageHandler` and the Phone V1 policy.
3. Implement Core owner/device/queue contracts and the Vision outbound worker; apply migrations only to a provisioned non-production environment after local/mocked checks.
4. Build and test the authenticated PWA, including installability over HTTPS, session-only display, reconnect, and visible expiry/failure states. Run a staging end-to-end test before production enablement.
5. Add the Discord pairing and DM slash-command adapter as a separate opt-in release after PWA pairing works. Test bot-offline and Core-unavailable behavior.
6. Do not deploy or provision production secrets as part of this design/spec task. Staging Core URL and credentials were previously unavailable; live validation remains blocked until an operator provides them.

Acceptance requires: one paired Core owner can use both channels; cross-channel session context never leaks; approved memories are the only persistent facts; revoked/unpaired users and devices cannot use chat; PWA messages survive a temporary Vision outage without duplicate visible replies; Discord cannot run when the Vision bot is offline; unauthorized actions are blocked server-side; and failures never fabricate Hub status.

## References

- [Discord: Receiving and Responding to Interactions](https://discord.com/developers/docs/interactions/receiving-and-responding) — Gateway or HTTP delivery, immediate acknowledgement, response lifetime, and ephemeral responses.
- [Discord: Application Commands](https://discord.com/developers/docs/interactions/slash-commands) — slash-command definitions and interaction contexts.
- [Discord: Automated User Accounts (Self-Bots)](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots) — automation must use bot accounts, not personal accounts.
- [MDN: Installing PWAs](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Installing) and [Service Worker API](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API) — installable web app behavior and HTTPS requirement for service workers.
