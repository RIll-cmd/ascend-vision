# Phase 5 implementation plan

Goal: implement specification section 3.7: daily pickup bars, weekly pickup trend,
total focus-session time, longest phone-free streak, reading existing SQLite.

Architecture: separate Flask application served by Waitress on IPv4 loopback.
Read-only SQLite snapshot per request, no writer lock, migration, camera or Gemini
initialization. Native SVG charts and local assets avoid CDN dependencies.

- [x] Write analytics tests for calendar boundaries, DST, mode filters, session
  gaps, zero-duration pickups, ongoing/crashed sessions and concurrent readers.
- [x] Implement dashboard_stats.py with range-limited snapshot queries, timezone
  boundaries, interval union/subtraction, daily and Monday-based weekly buckets.
- [x] Implement dashboard.py, validated YAML settings, CLI and HTTP tests for
  validation, missing data, schema errors and loopback request restrictions.
- [x] Implement responsive, accessible template/CSS/JS with filters, refresh,
  chart data tables, recent events, and empty/error states.
- [x] Verify regression suite, real browser desktop/mobile behavior and offline
  assets, update README/lock and package a credential-free Phase 5 archive.

Semantics: inclusive calendar start/end dates; counts belong to confirmation day
and original event mode. Duration is clipped at range boundaries. Focus duration
uses actual focus session intervals. Streaks span contiguous recorded monitoring,
break at pickup confirmation, exclude held intervals and never bridge downtime.
Open sessions end at durable heartbeat/event progress, not current wall clock.
Mode filters restrict monitoring intervals; all phone intervals break streaks,
including a hold that crosses a mode switch. Partial edge weeks are labelled.
