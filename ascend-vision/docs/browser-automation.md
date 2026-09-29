# Vision browser research (development status)

Browser automation is an optional local capability and is disabled by default.
The first release is local-only and is not available to PWA or Discord sessions.
It does not read the everyday Chrome profile or create memories from browsing.

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

`browser_automation.enabled` defaults to `false`. It enables public research
with a fresh ephemeral context. `browser_automation.b3_enabled` also defaults
to `false` and requires the parent browser feature to be enabled. B3 exposes
selected-origin tasks, exact owner-review cards for form edits/control
activation/uploads/downloads, and explicitly named saved profiles. It does not
enable payments, security/account changes, arbitrary file access, or remote
initiation. Enable B3 only for local fixture testing until its live-account
acceptance gates are met.

An authenticated workflow requires the owner to select one exact HTTP(S) site
origin and the actions available to that task. The visible browser is separate
from the owner's everyday Chrome/Edge profile. Sign in manually in the Vision
window when it pauses for takeover; passwords and MFA values are not sent to
the planner. Saving a profile is a separate explicit action. On Windows,
Playwright storage state is protected with DPAPI under the current Windows user
in `%LOCALAPPDATA%\AscendVision\browser\profiles`; use the profile controls to
list or clear it. Do not enable B3 on a shared Windows account.

An upload can include only the one file chosen in the local task form (10 MiB
maximum). The model receives an opaque attachment-present flag, never a local
path; its proposal is bound to the selected filename and SHA-256 digest. Upload
staging is removed when the task ends or is stopped. Downloads are limited to
25 MiB each and 500 MiB/100 files total, sanitized, never overwrite an existing file, and are saved under
`%LOCALAPPDATA%\AscendVision\browser\files\downloads`; they are not opened or
executed. These are managed destinations rather than a user-selected arbitrary
folder.

For consequential form edits and clicks, a local SQLite journal records
task/owner/action/origin/proposal digest before browser dispatch and records an
outcome after the next observation. It intentionally omits page text and action
values. Any unresolved dispatch is changed to `unknown` on broker restart and
is never blindly replayed. Task state itself remains in memory, so this journal
is not a task-history or resume service.

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

For the opt-in B4 Core-backed PWA/Discord task lifecycle, exact-action phone
review, retention and rollout gates, use the [remote operations guide](browser-remote-operations.md).
The local/public-research restrictions below still apply unless a separately
accepted local B3 workflow and B4 site scope explicitly permit an action.

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
there is no durable task history or cross-session resume. Generic observations
do not prove an autosaving site persisted a value; the owner must verify the
site's resulting state, and site-specific verification adapters remain future
work. Live-provider/public-site and real signed-in-account B3 acceptance, crash
injection around dispatch, voice completion, and download-to-user-folder UX
still need explicit validation before a broader rollout. Never interpret a
journal entry of `observed` as proof that a third-party service committed a
write.
