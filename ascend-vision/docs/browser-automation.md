# Vision browser research (development status)

Browser automation is an optional local capability. It is disabled by default,
uses public-research scope and a fresh ephemeral browser context, and is not
available to PWA or Discord sessions. It does not read the everyday Chrome
profile, submit forms, access files, or create memories from browsing.

## Optional runtime setup

From the Vision checkout, install the pinned Python package and its matching
Chromium build explicitly:

```powershell
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python -c "import importlib.metadata as m; print('Playwright', m.version('playwright'))"
```

Playwright is intentionally absent from `requirements.txt`; a normal Vision
install and startup must not download or launch a browser. The package baseline
is Playwright 1.63.0, whose published Windows x86-64 wheel advertises Python
3.14 support. The Chromium revision must be the one installed by that exact
Playwright package; do not upgrade one without reinstalling the other.

## Configuration

`browser_automation.enabled` defaults to `false`. The only accepted initial
scope is `public_research`; the profile is always `ephemeral`. The initial
limits are 20 model decisions, 30 browser actions, three pages, four queued
tasks, and a 180-second deadline. This version does not accept saved profiles,
selected-origin authenticated tasks, uploads/downloads, form submission, or
remote initiation.

The broker IPC, Playwright fixture suite, browser-specific structured provider
call (with cross-provider fallback disabled), local dashboard/voice task
controls, and source-linked result flow are implemented. Terminal task payloads
expire after one hour of inactivity; only a content-free owner/session
tombstone remains for up to 24 hours. The in-memory task registry is capped at
256 entries, including tombstones, and rejects new work rather than evicting an
active or queued task. This is still an
opt-in development feature, not a production-ready browser: `enabled` remains
false by default, no configured provider key is available in the development
environment for live-model acceptance, and a human should review the first
real-provider runs before enabling it for routine use. Install `pywin32` from
this optional manifest on Windows; it is used to create an owner-restricted
named pipe rather than opening a local TCP port.

V1 blocks HTTP redirects instead of following them. That is intentionally
conservative because browser routing does not re-run the handler for a redirect
destination. Some public sites will therefore need a direct canonical URL.
Revisit this only with a redirect-hop implementation and tests that prove each
destination is validated before any request is sent.

## Security and data handling

Page text and accessible labels are untrusted input. They cannot grant actions,
change scope, choose another provider, run code, or access local files. Public
research is limited to HTTP(S) destinations; local, private, reserved, and
link-local destinations are rejected, redirects are refused, and WebSocket
connections are left unconnected. These checks are defense in depth, not a
substitute for OS-level network isolation or a complete DNS-rebinding boundary.
Do not use this release for secrets, signed-in accounts, form writes, or
high-impact actions.

The provider selected for browser decisions receives the goal and bounded page
observations. Do not enter secrets into a public-research task. Automatic
provider failover must remain disabled for browser data unless the owner has
explicitly approved that provider too. Browser excerpts/results are transient;
they are not persisted as conversational memory.

Stop is expected to revoke future actions; it cannot undo a website effect that
already began. Browser task state is transient and held by the local broker;
there is no durable task history or cross-session recovery. If a site outcome
cannot be verified, Vision must say `unknown` or `partial`, not claim
completion. Provider-backed acceptance, broker restart/recovery behavior, and
voice delivery of the final summary still need explicit validation before a
broader rollout.
