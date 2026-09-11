# Dashboard Chat IPC Design

## Goal

Let typed dashboard messages enter the same Vision routing and Core-confirmation flow as voice transcripts.

## Architecture

The dashboard and `main.py` are separate processes. They communicate through a local SQLite-backed inbox/outbox owned by Vision. The dashboard enqueues user text and polls for replies; the Vision runtime polls the inbox, invokes its existing `on_unmatched_speech` handler, and writes safe user-facing results to the outbox.

The queue is local-only, uses the existing storage directory, enables SQLite WAL mode, and stores no credentials or raw model secrets. Mutating automation actions continue to require the existing explicit confirmation command and Core API validation.

## Interfaces

- `POST /api/chat/messages` accepts `{ "text": "..." }`, validates a non-empty bounded message, and returns `{ "messageId": "..." }`.
- `GET /api/chat/messages?after=<cursor>` returns ordered replies with `messageId`, `text`, `status`, and `createdAt`; it never returns tokens or provider payloads.
- `ChatIpcQueue.enqueue(text, source="dashboard")` writes an inbound message.
- `ChatIpcQueue.receive_inbound()` claims pending inbound messages atomically.
- `ChatIpcQueue.reply(message_id, text, status)` writes a result.

The runtime owns routing and confirmation state. The dashboard is a transport and display surface only. Replies include `queued`, `reply`, `error`, or `confirmation_required` status and are rendered as chat messages with accessible live announcements.

## Failure and lifecycle behavior

- If the runtime is not running, dashboard submission remains queued and the UI says “Waiting for Vision.”
- Duplicate polling must not process a message twice; claims are transactional.
- Queue writes use bounded text and retention cleanup; malformed input receives HTTP 400.
- Dashboard remains loopback-only, and no endpoint accepts Core credentials.

## Testing

Add unit tests for queue atomicity/validation, dashboard enqueue/poll routes, runtime dispatch, and UI rendering. Existing voice, automation proposal, authentication, and heartbeat tests must remain green.
