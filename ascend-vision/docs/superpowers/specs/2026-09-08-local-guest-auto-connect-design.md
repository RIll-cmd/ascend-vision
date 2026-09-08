# Local Guest Session Auto-Connect Design

## Goal

Allow the local Ascend Vision dashboard to reuse the browser's existing Ascend Core Guest session to mint and store the existing 15-minute, purpose-scoped Vision token without presenting account or password fields.

## Verified Core Contract

- Core frontend: `http://localhost:3000`; backend: `http://localhost:8000`.
- Core sets an `HttpOnly`, `SameSite=Lax` `ascend_session` cookie and its current-user dependency reads that cookie before a Bearer header.
- `GET /api/auth/me` returns the authenticated user and their owned character.
- `POST /api/auth/vision-token` uses the same current-user dependency and returns the existing purpose-scoped Vision token.
- Core CORS permits credentialed requests from `localhost` and `127.0.0.1` origins. The auto-connect browser flow must use `http://localhost:8765`, because host-only browser cookies set for `localhost` are not sent from a `127.0.0.1` page.

## Architecture

```text
Core Guest session cookie (browser-managed, HttpOnly)
    -> Vision dashboard at localhost:8765
    -> GET /api/auth/me with credentials: include
    -> POST /api/auth/vision-token with credentials: include
    -> loopback-only Vision handoff endpoint
    -> VisionTokenStore / Windows Credential Manager
    -> safe runtime character context
    -> main.py automation and heartbeat clients
```

The browser never reads the Core cookie. The browser holds the returned Vision token only long enough to POST it to Vision's loopback backend. The loopback backend stores the token exclusively through `VisionTokenStore`; it does not return the token to the DOM or write it to logs.

## Local and Production UX

Local mode is selected only when the configured Core URL resolves to `localhost` or `127.0.0.1`. The local dashboard replaces the password form with status, authenticated character, authorization expiry, Core reachability, and Retry/Reconnect controls.

The existing username/password handoff route and UI remain available outside local mode. No new credential or token format is introduced.

## Character Context

The character returned by Core `/api/auth/me` is authoritative. Vision stores safe, non-secret character metadata next to its existing local Vision credential. At normal Vision startup, that metadata supersedes `ASCEND_CHARACTER_ID`; if no valid synced context exists, the configured character remains the fallback.

The token and its character context are cleared together when the Vision token is expired or rejected. A heartbeat is created only from the resolved runtime character context, so it cannot report a stale Guest character.

## Security Constraints

- Do not modify Ascend Core.
- Do not read, copy, persist, or log the Core session cookie or normal Core JWT.
- Do not put tokens in URLs, DOM text, local storage, `.env`, or `config.yaml`.
- Do not use `X-Integration-Key` as user identity.
- Do not broaden Core CORS or use wildcard origins.
- Do not send credentials or tokens to LLMs.
- Keep the normal Phase 6 Vision token purpose, TTL, validation, and Core confirmation architecture unchanged.

## Tests

Focused tests cover valid and expired credential behavior, local handoff status, token-only loopback storage, authoritative character replacement, local UI rendering, preserved production login, heartbeat character selection, and regressions for gesture/CV/automation modules.
