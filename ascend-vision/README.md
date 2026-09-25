# Phone Watch â€” Phase 3

Local YOLO26n + MediaPipe hold detection with **background/focus sessions and
SQLite logging**. Every confirmed pickup is persisted in either mode. Focus is
user-controlled. Confirmed focus pickups generate a short Gemini roast and speak
it with offline Windows TTS; background mode remains silent. Phase 5 adds an independent local habit dashboard.

## Install or upgrade

From the extracted `phone_watch` directory, in Windows PowerShell:

```powershell
# Only needed if you do not already have an environment:
py -3.11 -m venv .venv
# Install the exact tested environment:
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
# Prepare both local models (existing files are reused):
.\.venv\Scripts\python.exe main.py --download-model
# Initialize SQLite without opening a camera (also automatic on normal startup):
.\.venv\Scripts\python.exe main.py --init-db
# Start in background mode:
.\.venv\Scripts\python.exe main.py
```

For an existing Phase 4 environment, skip the environment-creation command.
Gemini feedback requires `GEMINI_API_KEY`; SQLite needs no database server. The archive contains both official model assets and excludes databases.
Existing databases are retained; startup creates missing schema objects without
dropping tables or data. Schema versions newer than this app are rejected.

The lock targets Windows/Python 3.11. `requirements.txt` pins runtime dependencies;
`requirements-dev.txt` adds pytest. Use `.venv/bin/python` on Linux/macOS. Native
tray/hotkey support was verified on Windows; other desktops may need platform
setup. Use `--no-tray --no-hotkey` when those integrations are unavailable.

## Phase 5: open the habit dashboard

From the extracted project directory, run this in a **second PowerShell window**
while detection runs in the first:

```powershell
.\.venv\Scripts\python.exe dashboard.py
```

It opens **http://127.0.0.1:8765** in your browser. Ctrl+C in that terminal stops
only the dashboard. Detection, Gemini feedback and SQLite logging run separately.
The dashboard itself needs no camera, model downloads or Gemini credentials.
It can also inspect existing history while detection is stopped.

The dashboard's chat panel needs both processes running. Start `main.py` in the
first window and `dashboard.py` in the second, using the same `--config` file if
you override the default. Typed messages wait in the local queue while Vision
is stopped; once Vision runs, the chat panel displays its generated text reply.
Without a configured model provider, ordinary dashboard chat uses the existing
offline reply. The dashboard remains available for statistics when Vision is off.

The chat panel's **What Vision remembers** section lets you review proposed
facts, approve or reject them, edit or delete approved facts, search, export,
and turn memory on or off. Say “Remember that …” to propose a fact; Vision
uses it only after dashboard approval. “What do you remember about me?” lists
approved facts, and “Forget …” deletes one unambiguous match. “Do not remember
this conversation” clears the current in-memory turn window and pending
proposals until Vision restarts. Ordinary chat turns are kept only in Vision's
RAM while it runs. The dashboard's SQLite queue temporarily holds undelivered
messages and replies; the browser acknowledges displayed replies and queue
maintenance removes old rows after 24 hours. This is logical cleanup, not
forensic erasure from filesystem snapshots or backups. Only approved facts
persist across Vision restarts.

Vision can answer “What is Ascend Hub doing?”, “Is Codex CLI still working?”,
and “What is Antigravity's status?” from Core's live status shelf. This requires
`ascend.enabled: true`, a reachable `ASCEND_CORE_BASE_URL` (or `ASCEND_BASE_URL`),
and the dedicated `ASCEND_STATUS_READ_CREDENTIAL` environment variable in the
Vision process. In Ascend Core's `server` environment, an operator can create
that credential with `python -m cli.status_read_credentials create`; store the
returned `credential-id.secret` in your local secret manager and supply it to
Vision. The status tool never uses Vision's bearer token or the status producer
credential. Remote Core URLs must use HTTPS; local loopback HTTP is allowed.
Without a current authenticated shelf, Vision says it cannot verify AI status.
An `idle` status does not prove that an agent finished its last task; completion
reports require a later Core completion-history feature.

```powershell
# Select a port and print the URL without opening a browser:
.\.venv\Scripts\python.exe dashboard.py --port 8766 --no-open-browser
# Read the database selected by another config:
.\.venv\Scripts\python.exe dashboard.py --config config.cpu.yaml
```

Daily stacked bars show confirmed pickups by mode. The weekly line groups pickups
into Monday–Sunday calendar weeks. Summary figures show total pickups, recorded
phone time, focus-session time and longest phone-free streak. Select inclusive
start/end dates (up to 366 days) and all/focus/background sessions, then Apply dates.
Today resets the dates; Refresh updates the applied selection. Automatic refresh
runs every 10 seconds and pauses while the browser tab is hidden. Exact chart
values are available in expandable tables, followed by the latest 20 pickups and
any recorded roast text.

No data is fabricated: a missing database or empty period shows an empty state;
a read error shows an error instead of misleading zero totals. New data appears
on refresh. The sample dashboard screenshot outside the project archive uses a
separate synthetic test database and is **not your habit history**.

### How the numbers are calculated

- Dates use `dashboard.timezone`: `local` follows this computer's timezone at each
  timestamp, or set an IANA zone such as `Asia/Shanghai`. Each midnight is resolved
  separately, including 23/25-hour daylight-saving days. Weeks at range edges may
  be partial and are marked. Today's numbers include only saved progress so far.
- Pickup count and mode belong to the original confirmation. A hold spanning
  midnight is counted once, on its confirmation date. Its duration is clipped to
  the selected period; overlapping held intervals are counted only once.
- Focus time is the union of selected focus-session intervals, including time
  spent holding the phone. Filtering to background makes focus time zero.
- Longest streak means a continuous recorded monitoring interval without a
  confirmed pickup or held phone. It resets at zero-duration pickups, excludes
  held intervals and never bridges a gap between sessions. Adjacent sessions can
  form one continuous interval. Streaks are clipped to the selected dates/mode.
- A hold crossing a mode switch retains its original mode for event totals, but
  still interrupts streaks in the later mode. Phone-time totals filtered by mode
  therefore need not be a subset of that mode's session time.
- An open session ends, for these calculations, at its last durable heartbeat or
  event progress. Closing or crashing the app does not add time until now. The
  dashboard never changes or recovers sessions; normal detection startup handles
  recovery. Counts are heuristic detections, not a guarantee of actual grip.

### Dashboard settings and privacy

The `dashboard` section in `config.yaml` configures port (8765), refresh interval
(10 seconds), default date range (28 days), timezone (`local`) and automatic
browser opening (`true`). The host is fixed to IPv4 loopback. Assets are bundled;
there are no CDN fonts, chart libraries, analytics or external dashboard requests.
Native SVG charts provide bars and trends without a Chart.js dependency.

Flask uses [Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/)
instead of its development server. Host/origin checks, a restrictive content
security policy and no-store responses protect the local read-only view. There
are no mutation endpoints, directory browsing or credential-serving routes.
The dashboard is intended for this computer; it has no remote-access login.
SQLite is opened with `mode=ro`, `query_only=ON` and a consistent read transaction.
It neither initializes missing databases nor competes for the app's writer lock.
No schema migration is needed for Phase 5.

## Gemini credentials and feedback

The supplied key is saved only in the local `.env` beside `config.yaml`. It is
excluded from Git and every delivery archive. On a fresh extracted copy, create
that file and enter your key:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `GEMINI_API_KEY` there. An existing process environment variable takes
precedence. With `--config`, `.env` is loaded from that configuration's directory.
Do not overwrite an existing `.env` when upgrading. Missing credentials disable
feedback with a warning; detection and SQLite logging continue.

```powershell
# Test offline speech without an API call or camera:
.\.venv\Scripts\python.exe main.py --test-speech
# One real Gemini request using synthetic counts, followed by speech:
.\.venv\Scripts\python.exe main.py --test-feedback
# Run detection and feedback in an explicitly selected focus session:
.\.venv\Scripts\python.exe main.py --focus
# Disable feedback while retaining focus/session logging:
.\.venv\Scripts\python.exe main.py --focus --no-feedback
```

Default model: `gemini-3.6-flash`, configurable under `feedback.model`. The supplied
key successfully generated and spoke a line with this model. Google rejected
2.5 Flash generation for this account as unavailable to new users. The adapter
uses the [official Google Gen AI SDK](https://googleapis.github.io/python-genai/).

The API receives only four fields: today's pickup count (both modes), this focus
session's pickup count, elapsed session minutes, and local `HH:MM`. No frames,
landmarks, bounding boxes, database files or keypresses enter the request. Requests
also include the static roast instructions. Google's API data-handling terms
apply to the submitted metadata; this app does not promise provider-side retention
controls. Generated lines are limited to 45 words and 280 characters by default.

Each newly confirmed focus pickup may submit one request. The 45-second
`hold.cooldown_seconds` interval is measured between accepted focus attempts,
including attempts that fail; background pickups never consume it. Every pickup
is still logged. A continuously held phone cannot trigger repeated roasts.
Generation and speech run on a separate worker, with no queued backlog while busy.
Changing focus session, returning to background or quitting cancels pending speech
and interrupts playback. In-flight HTTP can finish but cannot revive a cancelled
job. Mode commands apply between inference iterations, so a slow inference can
delay cancellation. Confirmation in background does not retroactively trigger
feedback when focus is switched on during the same hold.

Timeouts, empty/blocked/truncated responses and speech errors are contained in the
feedback worker; logs expose error class names, never raw provider response bodies.
The main thread writes speech results back to the original SQLite event. On forced
termination or a driver that ignores shutdown, playback results may not persist.

All tuning lives in `config.yaml`: generation timeout (10 seconds), maximum job
age before playback (15 seconds), shutdown wait (3 seconds), output token and
character caps, speech rate (175), volume (1.0), system voice ID and speech timeout
(20 seconds). Empty voice ID selects the Windows default. Other model families may
need a larger output-token allowance for reasoning. No cloud TTS is used.

## Session controls

- **Tray menu:** Focus mode toggle, Start focus, Return to background, Quit.
  Green means background; orange means focus. Check the notification-area overflow
  if the icon is hidden by Windows.
- **Global shortcut:** `Ctrl+Alt+F`, configurable in YAML. It triggers on release.
- **Preview shortcut:** `Space` toggles focus while the preview is focused.
  It uses a different key from the default global shortcut to avoid double toggles.
- **Exit:** tray Quit, preview Q/Escape/window close, or Ctrl+C.

```powershell
# Explicitly start a focus session:
.\.venv\Scripts\python.exe main.py --focus
# Background logging without the preview, with tray/hotkey controls:
.\.venv\Scripts\python.exe main.py --no-preview
# Bounded run without desktop integrations:
.\.venv\Scripts\python.exe main.py --no-preview --no-tray --no-hotkey --duration 60
# Bounded focus run:
.\.venv\Scripts\python.exe main.py --focus --no-preview --no-tray --no-hotkey --duration 60
# Override camera or phone confidence:
.\.venv\Scripts\python.exe main.py --camera 1 --confidence 0.65
```

Background is the default and has no audible alerts or toast notifications.
The optional preview and console logs remain available for calibration. Focus
speaks only for newly confirmed focus pickups outside cooldown. Commands apply between inference iterations;
slow inference can delay an apparent mode change or exit request. The preview
and tray reflect the applied mode. A failed tray/hotkey setup produces a warning
and leaves detection/logging running with the remaining controls.

## Storage and session semantics

Default database: **`data/phone_watch.db`**, relative to `config.yaml`.
The schema retains the spec's `sessions`, `phone_events`, and
`idx_phone_events_detected_at` index. `roast_text` stores the generated line once speech starts (including interrupted
playback); failed, cancelled-before-playback and background events retain NULL.
A small `app_state` table stores recovery heartbeats; `PRAGMA user_version=1`
identifies the initial schema.

- Startup opens a background session unless focus was explicitly selected.
- A mode change atomically ends the current session and opens the new one at the
  same boundary. Re-selecting the current mode does nothing.
- A confirmed hold inserts one `phone_events` row immediately. Its timestamp,
  confidence, session ID and mode are stored. Cooldown never suppresses logging.
- Open durations are committed approximately every `storage.update_seconds`
  (default one second); release and shutdown force a final duration update.
- A hold stays linked to the session/mode active **at confirmation**, even if the
  mode changes during that hold. It is not split or counted twice. A hold can
  therefore extend beyond the end of its originating session.
- Confirmation timestamps determine ownership, so an older buffered frame cannot
  be assigned to a newly switched session. Candidates that confirm after a switch
  belong to the mode at confirmation.
- Camera/model/database errors stop the run and attempt finalization. Independent
  cleanup callbacks release camera, MediaPipe, tray, hotkey and database resources
  even if another cleanup or persistence operation fails. Persistence errors are
  surfaced, never treated as successful logging.

SQLite uses foreign keys, transactions, WAL and `synchronous=FULL`. A per-database
OS advisory lock rejects another Phone Watch writer while permitting external
readers. The `.db.lock` file can remain after exit; the **OS lock**, not the file's
existence, determines ownership. Do not remove it to bypass a running instance.

After an abrupt exit, recovery closes an interrupted session at its last durable
heartbeat or persisted event progress, whichever is later. It does not count
downtime through the restart. With no recorded activity, it closes at the session
start. Heartbeats default to five seconds; crashes can lose up to the last
uncommitted progress interval, or more if the process/driver was stalled. Force
termination cannot guarantee a final duration. An ordinary shutdown closes the
session normally. The database does not store an event-end flag because the
supplied schema has no such column; current active-event state lives in memory.

## Inspect recorded data

These commands read the default database from the project directory without
creating it accidentally:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; db=sqlite3.connect('file:data/phone_watch.db?mode=ro',uri=True); print(db.execute('SELECT id,start_time,end_time,mode FROM sessions ORDER BY id DESC LIMIT 10').fetchall())"
.\.venv\Scripts\python.exe -c "import sqlite3; db=sqlite3.connect('file:data/phone_watch.db?mode=ro',uri=True); print(db.execute('SELECT id,session_id,detected_at,duration_seconds,confidence,mode FROM phone_events ORDER BY id DESC LIMIT 10').fetchall())"
```

Use SQLite's backup API or stop the app before copying the database; a live WAL
database can have committed data in its `-wal` file. The Phase 5 dashboard reads these records without taking the writer lock.

## Configuration

Edit `config.yaml`, or select another file with `--config`. Relative model and
database paths resolve from that file. CLI overrides take precedence. Unknown
keys, invalid types/ranges and nonfinite numeric values fail at startup.

```yaml
storage:
  database: data/phone_watch.db
  busy_timeout_seconds: 5.0
  heartbeat_seconds: 5.0
  update_seconds: 1.0
sessions:
  initial_mode: background
  tray: true
  hotkey_enabled: true
  hotkey: ctrl+alt+f
```

The existing detector, camera, hands and hold settings remain configurable:
12 consecutive processed observations; 60-pixel wrist/tip proximity; 45-second
alert eligibility cooldown; one-second maximum observation gap. The default
`center` geometry follows the spec pseudocode; optional `box` measures distance
to the nearest box point. Only wrist/fingertips 0,4,8,12,16,20 qualify.

The newest-frame buffer avoids growing inference delay. Skipped or superseded
frames do not advance the confirmation count. Missing evidence immediately
resets a candidate or ends a hold. Duration starts at confirmation. Gaps longer
than the configured limit end a hold at its last observation.

The optional `config.cpu.yaml` reduces YOLO resolution and threads; it is not a
guaranteed speedup. The 15 FPS target has not been achieved consistently on this
machine. At lower inference FPS, a 12-frame confirmation takes longer. See the
verification reports for measured results.

## Verification and calibration

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

1. Check phone boxes and hand landmarks in preview.
2. Leave a phone on the desk with hands away: no confirmed event should appear.
3. Move a hand nearby briefly: the candidate should reset before confirmation.
4. Hold steadily: one start, increasing duration, one end on release.
5. Switch modes during a hold: the event stays linked to its original session.
6. Release and confirm another hold inside cooldown: verify a second SQLite row.
7. Quit and inspect session end times; restart and confirm earlier records remain.

Proximity does **not prove a grip**: a hand resting near a desk phone long enough
can still confirm. The real scene needs calibration; universal desk-phone
rejection is not claimed. Phase 4 adds feedback and retains the existing classifier.

## Files

```text
dashboard.py            Independent loopback dashboard and HTTP API
dashboard_stats.py      Read-only statistics and monitored-time streaks
assistant/memory.py     Local approved facts and short-lived memory proposals
templates/ / static/    Local HTML, CSS, JavaScript and SVG charts
main.py                 Runtime, preview, persistence wiring and CLI
feedback.py             Gemini metadata adapter and bounded feedback worker
speech.py               Cancellable offline speech on the owning worker thread
capture.py              Threaded bounded webcam capture
detector.py / hands.py   Local models and explicit model downloads
state_machine.py        Hold confirmation and event snapshots
session_manager.py      Main-thread mode changes, event ownership and progress
db.py                   Schema, transactions, locking and recovery
controls.py             Tray menu and global focus shortcut
config.py / config.yaml Typed configuration
models/                 Official YOLO26n and Hand Landmarker weights
data/                   Local SQLite database (generated, excluded from archive)
tests/                  Hardware-independent regression/integration tests
docs/                   Spec, plans and verification reports
requirements*.txt       Runtime/development requirements and tested lock
```

The two OpenCV wheel dependencies share `cv2` and are pinned to matching versions.
Avoid upgrading/uninstalling only one; recreate the environment from the lock if
needed. Dependency/model installation uses the network; normal inference and
SQLite logging are local. No video, images or keystroke recordings are saved or
uploaded. Only derived pickup/session data is persisted. Third-party licenses
remain applicable. Gemini receives only derived counts, session duration and local time. Speech is
offline. The dashboard runs independently and reads local SQLite.

Official references: [pystray](https://pystray.readthedocs.io/en/latest/usage.html),
[keyboard](https://github.com/boppreh/keyboard),
[MediaPipe](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python),
[YOLO26](https://docs.ultralytics.com/models/yolo26/).
Historical Phase 1/2/3/4 instructions and measurements remain under `docs/`.
