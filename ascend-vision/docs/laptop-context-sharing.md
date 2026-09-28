# Laptop context sharing with phone chat

This is an opt-in, read-only bridge from the laptop-owned context runtime to the authenticated PWA and linked Discord account. The laptop publishes a small typed snapshot outbound to Core. It does not open a listener for the phone.

## Enablement

In the laptop's `config.yaml`:

```yaml
companion_context:
  enabled: true
  share_with_phone: true
```

Configure the same laptop device ID on Vision and Core. Set these secret/environment values only on the laptop process and staging Core:

| Component | Variables |
|---|---|
| Vision laptop | `ASCEND_CORE_BASE_URL`, `ASCEND_CONTEXT_OWNER_ID`, `ASCEND_CONTEXT_PUBLISHER_TOKEN`, `ASCEND_DEVICE_ID` |
| Core | `ASCEND_CONTEXT_SHARING_ENABLED=true`, `ASCEND_CONTEXT_OWNER_ID`, `ASCEND_CONTEXT_DEVICE_ID`, `ASCEND_CONTEXT_PUBLISHER_TOKEN` |

The publisher token must be a dedicated random write credential and must differ from the status-read, phone-worker, Discord-bridge, Discord-pairing, and cron credentials. The owner/device values on Core bind that credential to one configured account and laptop; request bodies cannot select an owner.

Keep `share_with_phone` false (the default) and/or Core's sharing flag false to disable publication and reads. Do not turn this on in production until the additive Prisma schema has been applied and the staging gates below pass.

## What crosses the boundary

Only desk-presence category, coarse desktop activity, mapped foreground-app category, Vision session mode, and user-declared intent are eligible for the **context snapshot**. Each field keeps its evidence source and expiry. This publisher excludes screenshots, screen text, window titles, filenames, raw audio, transcripts, chat history, and credentials. Voice upload is a separate, explicit user action and uses the phone-chat audio endpoint described below; it is not part of context publication. The laptop publishes every few seconds; evidence expiry is not extended by a heartbeat.

The PWA card reads through the authenticated active phone device and displays freshness and last-seen information. Discord `/context` and context-related `/ask` prompts read through the active Discord link. Each channel retains its own conversation session. Revoking the phone device or Discord link blocks its subsequent reads.

## Phone voice

The PWA records only while foregrounded, stops at 30 seconds, and requires a separate Send voice action after review; the user can discard the recording or use typed chat. Upload is limited to 5 MiB and an allowlist of audio MIME types. The upload requires the PWA's explicit `google-gemini` provider-consent header and both server-side voice feature switches: Core `ASCEND_PHONE_AUDIO_ENABLED=true` and Vision `phone_chat.enabled: true` plus `phone_chat.voice_upload_enabled: true`. All are off by default. Core queues the audio temporarily for the existing phone-job TTL; Vision sends it to Google Gemini for transcription. The transcript is transient and is passed to the existing session handler, not appended to chat history. Core clears active queue audio on terminal job/device paths; database backup/WAL retention is separate. Do not enable this flow if sending the recording to Google for transcription is not acceptable. Microphone permission must be granted to the PWA origin.

## Verification still required

- Apply the Prisma schema to an isolated staging PostgreSQL database and verify unique-owner/device behavior, real concurrent transactions, cleanup, rollback compatibility, and service restart.
- Provision separate staging publisher credentials and verify wrong-owner/device, revoked PWA device, revoked Discord link, stale packet, laptop sleep, and publisher restart.
- Test microphone permission, interruption/backgrounding, recording review/discard/send, size and 30-second limits, network loss, typed fallback, and Gemini consent on physical Android and iOS devices. Browser emulation does not certify these behaviors. Provider-backed transcription has not been tested in staging.
- Confirm that staging context-sharing flags are independent from production and remain off until these checks pass.

The cleanup route is scheduled once daily as a compatibility baseline for Vercel Hobby, which does not permit an hourly cron schedule. Expired records are also erased when an offline read encounters them. Daily cleanup can leave an unread expired row in the database for up to roughly a day; the API masks expired field values immediately. No production or staging deployment was performed by this change.

## Not part of this implementation

Opt-in push and unsolicited Discord-DM delivery now have a separate, off-by-default Core intent/claim pipeline and independent per-channel consent. The Vision publisher is currently limited to fresh, explicit `agent_needs_input` intents and emits generic text; the PWA/Discord message does not carry private task details. The authenticated PWA detail endpoint is device-bound. Provider acceptance is not proof of device receipt, and transient outcome-write failures have a bounded idempotent retry; at-least-once delivery means a rare duplicate is still possible after a prolonged Core outage. Do not interpret slash-command use or laptop-context sharing as notification consent. PostgreSQL migration, staging, provider credentials, and physical-device notification verification remain required before enablement.
