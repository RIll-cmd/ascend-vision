# Slump Detector and Win-Back Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vision keeps a local daily record of the owner's work signals, notices a slump against the owner's own baseline, and responds with a gentle ladder: a check-in, then one small step, then one phone nudge through the Room.

**Architecture:**
- `assistant/slump.py` holds a pure `assess()`, a small SQLite store, and a `SlumpCoach` that samples activity each minute and backfills the other signals each hour.
- The coach speaks through `announce_paced` and answers through `local_intercept`, like the weather, GitHub and food coaches.
- The phone nudge goes through a new Room endpoint, `POST /notify`, that reuses the Room's ntfy push.

**Tech Stack:**
- Vision: Python 3, SQLite, pytest.
- Hub room: Node ESM, `node:test`.

**Spec:** `ascend-vision/docs/superpowers/specs/2026-10-29-slump-detector-design.md`

## Global Constraints

- **Metrics:** `commits`, `room_sessions`, `active_minutes`, `late_minutes`, `focus_minutes`, `distraction_minutes`. Keep 90 days.
- **Recent window:** the 3 complete days before today.
- **Baseline:** the median of the 14 days before the recent window, used only when 7 or more of those days have a value for that metric.
- **Drops:** `commits`, `room_sessions`, `active_minutes` and `focus_minutes` drop when the recent average is below 0.5 × baseline and the baseline is above 0.
- **Rises:** `late_minutes` fires when the recent average is over 30 and over baseline + 30. `distraction_minutes` fires when the recent average is over 30 and over 2 × baseline. The distraction rule is the plan's call; the spec lists the signal without a rule.
- A slump is 2 or more fired signals.
- **Ladder:** at most one unprompted line per day. Only between 08:00 and 21:00 local, and only after 30 active minutes today.
  - Step 1 is the check-in.
  - Step 2 is the small plan (after a yes, or on the next slump day).
  - Step 3 is one phone nudge, on the third slump day when no plan was accepted.
  - The ladder resets after 2 days with no slump. "Not now" pauses it for 3 days.
- **Copy:** kind, short, no numbers in unprompted lines, no streak or score talk.
- **Phone push text:** exactly `Ascend: a small plan is waiting for you on the PC.`
- **Config:** `slump.enabled: true`, `slump.phone_nudge: true`, defaults in `config.py` only. Do **not** add `slump` to `config.yaml` (the desktop worktree rejects unknown keys).
- No camera model loads for this feature. No new dependencies. Only the existing GitHub calendar read leaves the computer.

## Review Focus

- **Assistant mode, camera off:** `focus_minutes` has no recent data, so it is ignored. It never counts as a drop to zero.
- **A brand-new install with 2 days of data:** no baseline, so no slump and no lines. "How am I doing?" says it is still learning.
- **Hub or GitHub unreachable during backfill:** keep the last stored values and do not write zeros for days it could not read.
- **The PC asleep overnight or Vision restarted mid-day:** active minutes keep counting from the stored value for today, never reset.
- **The owner says "yes" long after the offer** (more than 2 minutes): not taken as accepting. It falls through to normal chat.

---

### Task 1: Room `POST /notify` (repo `D:\ascend_hub`)

**Files:**
- Modify: `scripts/agent-room/server.mjs`
- Create: `app/api/room/notify/route.ts`
- Test: `tests/agent-room/server.test.mjs`

**Interfaces:**
- Consumes: `phonePush(settings.notify, text, fetchImpl)` (`notify.mjs`).
- Produces: `POST /notify { text }` with 1–200 characters.
  - It answers 200 `{ sent: true }` when `settings.notify?.ntfy` is set.
  - It answers 200 `{ sent: false }` when not set.
  - Bad text answers 400 `NOTIFY_INVALID`.
  - Hub forwards `/api/room/notify` the same way.

- [ ] **Step 1: Write the failing test** `notify pushes through ntfy only when it is set up`. Use a stub `fetchImpl` that records calls. With `settings.notify = { ntfy: 'https://ntfy.sh/t' }`, expect 1 call and `sent: true`. Without notify, expect 0 calls and `sent: false`. An empty text gives 400.
- [ ] **Step 2: Run it and see it fail.** Run `node --no-warnings --test tests/agent-room/server.test.mjs`. Expected: the new test fails with 404.
- [ ] **Step 3: Implement the route and the proxy.** Copy `queue/remove/route.ts`.
- [ ] **Step 4: Run it and see it pass.** Run the same command and `npx --no-install tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** (Hub). Message: `feat(room): a plain phone push endpoint for Vision`.

### Task 2: Store and `assess()`

**Files:**
- Create: `ascend-vision/assistant/slump.py`
- Test: `ascend-vision/tests/test_slump.py`

**Interfaces:**
- Produces:
  - `METRICS = ('commits', 'room_sessions', 'active_minutes', 'late_minutes', 'focus_minutes', 'distraction_minutes')`.
  - `Assessment` is a frozen dataclass: `fired: tuple[str, ...]`, `slump: bool`, `learning: bool` (no metric has a baseline), and `signals: dict[str, tuple[float, float]]` mapping metric to (recent average, baseline).
  - `assess(days: dict[str, dict[date, float]], today: date) -> Assessment`.
  - `SlumpStore(path: Path)` provides:
    - `add(day: date, metric: str, amount: float)`: upsert add;
    - `put(day, metric, value)`: upsert set;
    - `days(since: date) -> dict[str, dict[date, float]]`;
    - `prune(today)`: drops rows older than 90 days;
    - `ladder() -> Ladder` and `save_ladder(Ladder)`.
  - `Ladder` is a dataclass: `step: int = 0`, `last_day: date | None`, `slump_days: int = 0`, `calm_days: int = 0`, `paused_until: date | None`, `accepted: bool = False`.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_drop_needs_the_recent_average_below_half_the_baseline`: commits of 4/day for 14 days, then 1/day for 3 days, fire `commits`; 3/day does not.
  - `test_two_signals_make_a_slump`: commits and active minutes both drop, so `slump` is True; one alone is False.
  - `test_late_nights_fire_over_thirty_and_baseline_plus_thirty`.
  - `test_metrics_without_seven_baseline_days_are_ignored`: 6 baseline days, so the metric is not in `signals`; all metrics like that give `learning`.
  - `test_missing_recent_days_are_not_zero`: focus has a baseline but no recent rows, so it is not fired.
  - `test_today_is_not_part_of_the_recent_window`.
  - `test_store_adds_and_survives_reopen`: add 1, reopen, add 1, giving 2.
  - `test_store_prunes_after_ninety_days`.
- [ ] **Step 2: Run them and see them fail.** From `ascend-vision/`, run `.\.venv\Scripts\python.exe -m pytest tests/test_slump.py -q`. Expected: FAIL, ImportError.
- [ ] **Step 3: Implement.**
  - Use `statistics.median`.
  - Tables: `day_metric(day TEXT, metric TEXT, value REAL, PRIMARY KEY(day, metric))` and `ladder(id INTEGER PRIMARY KEY CHECK(id=1), json TEXT)`.
  - One connection per call with `timeout=5`, following `assistant/nutrition.py`.
- [ ] **Step 4: Run them and see them pass.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** Message: `feat(vision): slump store and assessment`.

### Task 3: Signal collectors

**Files:**
- Modify: `ascend-vision/assistant/slump.py`
- Test: `ascend-vision/tests/test_slump.py`

**Interfaces:**
- Consumes:
  - `fetch_calendar(username)` (`integrations/github_coach.py`), which returns `dict[date, int]`;
  - Hub `GET {base_url}/api/room/sessions`, which returns `{ sessions: [{ createdAt, … }] }`;
  - `phone_watch.db` `sessions(start_time, end_time, mode)`;
  - `distractions.db` `distraction_time(day, site, seconds)`;
  - `WindowsActivityAdapter().sample().desktop_activity` (`'input_active'` counts) from `integrations/desktop_activity.py`.
- Produces:
  - `sample_minute(store, sample: str, now: datetime) -> None`: one active minute, plus one late minute when `now.hour < 5`.
  - `backfill(store, *, today, calendar=None, room_sessions=None, focus_db=None, distraction_db=None) -> None`. Each source is optional and may be None when unreachable. Days present in a source are `put`; days absent are untouched.
  - `room_session_days(payload: dict) -> dict[date, int]`, using local dates.
  - `read_room_sessions(base_url) -> dict | None`: no proxy, 3 s timeout, None on any error, like `RoomClient.current`.
  - Database readers open SQLite read-only (`file:…?mode=ro`) and return `{}` when the file is missing.

- [ ] **Step 1: Write the failing tests:**
  - `test_minutes_count_active_time_and_late_time`: input_active at 02:10 adds `active_minutes` and `late_minutes`; input_idle adds nothing.
  - `test_backfill_puts_github_and_room_days`;
  - `test_an_unreachable_source_leaves_stored_days_alone`: put commits=5, then backfill with `calendar=None`, and it is still 5;
  - `test_focus_minutes_sum_focus_sessions_per_local_day`: temp db with two focus sessions and one background session;
  - `test_room_session_days_use_local_dates`.
- [ ] **Step 2: Run them and see them fail.** Same command as Task 2. Expected: FAIL.
- [ ] **Step 3: Implement.** Focus minutes come from sessions `mode = 'focus'` with an `end_time`, `(end - start)` in minutes, keyed by the local date of `start_time`.
- [ ] **Step 4: Run them and see them pass.** Expected: PASS.
- [ ] **Step 5: Commit.** Message: `feat(vision): collect slump signals`.

### Task 4: `SlumpCoach`: ladder, answers, nudge

**Files:**
- Modify: `ascend-vision/assistant/slump.py`, `ascend-vision/config.py` (new `SlumpConfig`, a `Config.slump` field, and an entry in the section map near line 796)
- Test: `ascend-vision/tests/test_slump.py`, `ascend-vision/tests/test_config.py`

**Interfaces:**
- Consumes: Tasks 2–3.
- Produces:
  - `SlumpConfig(enabled: bool = True, phone_nudge: bool = True)`, with YAML booleans validated like `AgentRoomConfig.enabled`.
  - `SlumpCoach(store, *, announce, sample=None, backfill_sources=None, notify=None, clock=datetime.now, offer_window=120.0)`, where:
    - `sample: () -> str` (desktop activity);
    - `backfill_sources: () -> dict` (the keyword arguments for `backfill`);
    - `notify: (text) -> None`.
  - `SlumpCoach.tick(owner_available: bool) -> None`: rate-limited inside. It samples each 60 s, backfills each hour, and takes at most one ladder step a day.
  - `SlumpCoach.intercept(text) -> str | None` handles:
    - `how am i doing` / `how am i doing lately`: a plain summary of fired signals against the baseline, or `"I'm still learning your normal week."`;
    - `not now` (within `offer_window` of an offer): pauses 3 days, `"Okay. I'll leave it for a few days."`;
    - `yes` / `sure` / `okay` (within `offer_window` of a check-in or plan): accept;
    - anything else: None.
  - Plan choice, in order: `commits` → `"one small commit on anything"`; `focus_minutes` or `active_minutes` → `"one 25-minute focus block"`; `room_sessions` → `"one small task for the Agent Room"`.
  - Accepting a focus block says `"Good. I'll check in after 25 minutes."` and announces `"That's 25 minutes. Nice work."` 25 minutes later from `tick`.

- [ ] **Step 1: Write the failing tests** (fake clock, a list for `announce`, a store with a seeded slump):
  - `test_check_in_waits_for_daytime_and_thirty_active_minutes`;
  - `test_at_most_one_unprompted_line_per_day`;
  - `test_yes_to_the_check_in_offers_one_small_step_from_what_dropped`;
  - `test_the_next_slump_day_offers_the_step_without_asking`;
  - `test_the_third_slump_day_without_acceptance_sends_one_phone_nudge`: `notify` is called once with the exact text;
  - `test_no_nudge_when_phone_nudge_is_off_or_a_plan_was_accepted`;
  - `test_two_calm_days_reset_the_ladder`;
  - `test_not_now_pauses_three_days`;
  - `test_a_late_yes_is_not_taken`;
  - `test_unprompted_lines_hold_no_numbers`: assert `not re.search(r'\d', line)` for every announced line;
  - in `test_config.py`, `test_slump_config_defaults_and_validation`.
- [ ] **Step 2: Run them and see them fail.** Run `.\.venv\Scripts\python.exe -m pytest tests/test_slump.py tests/test_config.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The check-in names the fired signals in plain words. For example: `"Lately there have been fewer commits and shorter days at the PC than usual. Want a small plan for today?"`
- [ ] **Step 4: Run them and see them pass.** Expected: PASS.
- [ ] **Step 5: Commit.** Message: `feat(vision): slump check-ins and win-back steps`.

### Task 5: Wire into Vision

**Files:**
- Modify: `ascend-vision/main.py` (build next to `github_coach` around line 760; add to the `local_intercept` tuple; call `slump_coach.tick(owner_available)` beside `github_coach.maybe_remind` around line 1669), `ascend-vision/README.md`
- Test: none new; `ascend-vision/tests/test_main.py` must still pass (the live check covers the wiring)

**Interfaces:**
- Consumes: `SlumpCoach`, `SlumpStore(Path(config.storage.database).parent / 'slump.db')`.
- `backfill_sources` uses:
  - `fetch_calendar(config.github.username)` when the username is set;
  - `read_room_sessions(config.agent_room.base_url)`;
  - `config.storage.database` (`phone_watch.db`);
  - `Path(config.storage.database).parent / 'distractions.db'`.
- `notify` POSTs `{text}` to `{agent_room.base_url}/api/room/notify` through the same no-proxy opener as `RoomClient`, and only when `slump.phone_nudge` is set.

- [ ] **Step 1: Wire it.** Wrap `tick` in `try/except Exception` with a debug log, like the nutrition coach.
- [ ] **Step 2: Run the full suite.** Run `.\.venv\Scripts\python.exe -m pytest tests -q`. Expected: only the 12 known date-pinned failures.
- [ ] **Step 3: Check it live** in Assistant mode: `.\.venv\Scripts\python.exe main.py --assistant --env-file .env`. Expected:
  - after 2 minutes, `data/slump.db` has today's `active_minutes` of 1 or more;
  - after the first hourly backfill (or a forced call from a Python shell), the `commits` and `room_sessions` rows for past days exist;
  - saying "how am I doing?" answers.
- [ ] **Step 4: Docs.**
  - Add a settings-table row to `README.md`: "Stop slump check-ins | `slump.enabled: false`".
  - Add a short paragraph on what is measured, kept locally for 90 days.
  - Add an M13 row to Hub `docs/ROADMAP.md`.
- [ ] **Step 5: Commit** each repo. Messages: `feat(vision): slump detector` and `docs: slump detector done`.
- [ ] **Step 6: Update the installed app.** Fast-forward `D:\ascend-vision\.worktrees\desktop-reliability` to `main` (no `fairy-ui` change, so no rebuild). Load the main config with the worktree's code to prove that it still loads.
