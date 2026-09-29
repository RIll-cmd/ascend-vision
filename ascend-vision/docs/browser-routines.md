# Browser routines and local reliability metrics

Status: implementation in progress. No routine is live-enabled or promoted by
this document. The baseline pilot has not started; see the
[B5 readiness audit](audits/2026-09-29-browser-b5-pilot.md).

The local broker loads two strict manifest candidates—Documentation brief and
Compare documentation pages—from `browser/routines/definitions`. The exact
Playwright documentation paths are fixture candidates only. `enabled_routines`
is empty in `config.yaml`, so the picker explains that no accepted routine is
enabled and routine submission fails closed. The registry, restrictive policy,
same-broker execution path, bounded prior-observation context for multi-page
verification, evidence verifiers, and immediate per-version Stop path are
implemented locally. Unit and service-level fixtures cover strict validation,
scope narrowing, two-page evidence, and queued disable/Stop. A Chromium-specific
routine fixture and per-version live promotion acceptance remain open; no exact
digest is enabled.
Adding a reference to `enabled_routines` asserts that the exact digest passed the
required pilot review. A local disable marker persists under the current Windows
account; only a version already listed in that configuration can be explicitly
re-enabled from the dashboard.

## Metrics preference

The local dashboard's **Local reliability metrics** panel is off by default.
Enable it only if you want operational counters saved in the current Windows
account's Ascend Vision browser data directory. It does not save prompts, task
goals, full URLs, page titles or text, findings, form values, credentials,
cookies, screenshots, raw model responses, free-text feedback, or external task
and owner identifiers.

Stored records use random run/attempt IDs, a day/time, a fixed channel/cohort/site
family, provider and model names, bounded state/verdict/reason codes, token
categories when reported, durations, and cost only when a verified price table
covers the exact model. Unknown token or cost data stays unknown; it is never
recorded as zero. Fixtures have their own cohort and are excluded from live
success rates.

The local controls provide:

- Explicit collection on/off. Turning it off immediately prevents subsequent
  writes. The default is off.
- A bounded aggregate scorecard and fixed-schema JSON export.
- A clear action that removes the metrics database and its SQLite sidecars, then
  returns collection to off. The action does not clear the separate browser
  action-recovery journal or the separate persistent pilot-spend ledger. That
  ledger stores only provider attempt IDs, model, UTC date, and reserved USD
  micros so restarting the broker or clearing feedback cannot reset the pilot
  spend limit.
- Per-task `Worked`, `Needed correction`, or `Did not work` feedback after a
  result is visible. Feedback is accepted only for a terminal task still owned
  by the current local dashboard session. The browser result remains temporary.

Retention is at most 30 days and 10,000 run records, with at most 100 provider
attempt records per run. Metrics are not uploaded to Core. A separate source of
truth is required for provider billing. Until a current price table and a
conservative token reservation are available, cost coverage is reported as
unknown and B5's paid pilot must not begin. The current byte-length input bound
is a conservative upper-bound proposal, not a provider-certified token count;
the explicit `pilot_token_bound_reviewed` setting stays false until that bound
has been accepted for the selected provider/model.

The provider-call integration records key/model retries and malformed JSON
responses. Browser decisions are capped at two actual provider attempts each.
This does not change normal chat routing or permit provider switching for a
browser task. A rejected pre-call reservation must occur before the provider
request; its caller must supply a verified conservative token bound and reviewed
model price.

## Routine promotion status

The two locally installed B5 candidates are documentation brief and two-page
documentation comparison. Visible specification extraction remains a possible
later candidate; it is not installed. The installed candidates are not enabled
routines. Promotion requires the dated baseline and per-version live acceptance
described in the B5 plan. No page content can install or modify a routine; a
routine cannot add a browser grant or exceed the task's existing scope, action,
or time limits.

## Local API

These routes are available only when local browser automation is enabled and use
the existing local dashboard boundary:

| Method and path | Purpose |
|---|---|
| `GET /api/browser/metrics?cohort=all` | Read an allowlisted summary. |
| `POST /api/browser/metrics/settings` | Set exactly `{ "enabled": true/false }`. |
| `GET /api/browser/metrics/export` | Export fixed-schema operational counters while collection is on. |
| `DELETE /api/browser/metrics` | Clear only the metrics database and disable collection. |
| `POST /api/browser/tasks/{task_id}/feedback` | Record one of the three fixed owner verdicts for the task in the current dashboard session. |
| `GET /api/browser/routines` | Read the installed fixed routine catalogue and enabled flags. |
| `POST /api/browser/routine-tasks` | Submit a strict `{routine_id, version, digest, inputs}` invocation. The broker supplies the task goal and authority. |
| `POST /api/browser/routines/{routine_id}/{version}/disable` | Disable that version and stop its queued or active local routine tasks. |
| `POST /api/browser/routines/{routine_id}/{version}/enable` | Re-enable a previously accepted exact version already present in local configuration. |

Do not add prompt/URL fields to these records or route payloads. Do not treat a
model `finish`, task completion receipt, or unreviewed owner verdict as proof
that a real website outcome is correct.
