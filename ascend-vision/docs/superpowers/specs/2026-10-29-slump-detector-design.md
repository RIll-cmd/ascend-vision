# Slump detector and win-back — design

Date: 2026-10-29. Status: approved for planning (owner said "start"; the calls below are Claude's).

## Why

Streaks break quietly. By the time the owner notices, a week is gone. Vision already sees some of the signs, but never puts them together. Idea source: "churn prediction and win-back" (watch usage signals, score churn risk, act before the customer leaves: a personal check-in first, then an offer, then a human), with the owner as the customer.

## What Vision can measure today (checked 2026-10-29)

| Signal | Source | Backfill |
| --- | --- | --- |
| Contributions per day | GitHub's public calendar, already read by `integrations/github_coach.py` | Yes, a year |
| Agent Room sessions started per day | Hub `GET /api/room/sessions` (`createdAt`) | Yes |
| Active minutes, and minutes active between 00:00 and 05:00 | Input-idle state from `integrations/desktop_activity.py`, sampled once a minute | No |
| Focus-mode minutes, drowsiness events | `phone_watch.db` `sessions` and `phone_events`, only when the camera runs | Yes, but the camera has not run since 2026-10-14 |
| Minutes on distracting sites | `distractions.db` `distraction_time` | Yes (empty so far) |

Core's local checkout was deleted on 2026-10-21, so Core habits are not a source for v1. The design must work in Assistant mode with no camera.

## Goals

1. A local daily record of these signals, kept 90 days.
2. A slump call made from the owner's own baseline, not fixed targets. A signal "drops" when its 3-day average falls below half of the median of the 14 days before. "Late nights" fire when late minutes average over 30 and over the baseline plus 30. A signal with fewer than 7 baseline days, or one with no data (camera off), is ignored. A **slump** is 2 or more dropped signals.
3. A gentle ladder, at most one step a day, with a step only at a sensible hour (after 30 active minutes that day, between 08:00 and 21:00):
   1. **Check-in:** one spoken line naming what changed, kindly, and asking "want a small plan for today?"
   2. **Small plan:** after a yes, or on the next slump day, one concrete step chosen from what dropped:
      - "one commit on anything" (contributions);
      - "one 25-minute focus block" (focus or active minutes); a yes starts focus mode;
      - "queue one small Room task" (Room sessions).
   3. **Phone nudge:** on the third slump day with no step taken, one ntfy push through the Room. The push text is generic and contains no numbers.
4. "Vision, how am I doing?" answers with the signals against the baseline, at any time.
5. The ladder resets after 2 days with no slump, or when the owner says "not now", which pauses it for 3 days.

## Non-goals

- Core habits, sleep tracking, or nutrition as signals (later).
- Graphs (the dashboard can show them later).

## Anti-goals

- Guilt, scores, or streak-shaming in the wording. Speak like a friend, once.
- More than one unprompted slump line per day.
- Any new data leaving the computer. The GitHub calendar read already exists; the phone push carries no metrics.
- Waking the camera models to measure anything.

## Shape (Claude's calls)

- `assistant/slump.py`:
  - pure `assess(days, today) -> Assessment` (signals, drops, slump, step due);
  - `SlumpStore` (SQLite `data/slump.db`: `day_metric(day, metric, value)`, `ladder(state, step, since, paused_until)`);
  - `SlumpCoach` with `intercept(text)` for "how am I doing" and "not now", following `WeatherCoach`, `GitHubCoach` and `NutritionCoach`;
  - a minute tick for activity sampling, and an hourly tick for backfill and the ladder.
- Wiring: `main.py` beside the other coaches. It speaks through `announce_paced` and joins `local_intercept`. Its yes/no follow-up uses the same short window idea as `RoomDialog`.
- Config: `slump` with `enabled: true`, `phone_nudge: true`, defaults in `config.py` only. Do **not** add the section to `config.yaml`: the installed desktop worktree reads the main config and rejects unknown keys until it is updated.
- Phone nudge: a new room endpoint, `POST /notify { text }`, reuses `phonePush` from `scripts/agent-room/notify.mjs`. It is proxied by Hub at `/api/room/notify` and does nothing when `notify.ntfy` is not set. This is a Hub change in the same plan.

## Left to the builder

Exact wording of the lines (kind, short, no numbers unless asked), and the sampling detail for idle state.
