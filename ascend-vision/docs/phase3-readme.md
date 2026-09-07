# Phone Watch â€” Phase 3

Local YOLO26n + MediaPipe hold detection with **background/focus sessions and
SQLite logging**. Every confirmed pickup is persisted in either mode. Focus is
user-controlled; spoken feedback starts in Phase 4 and is not active here.

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

For an existing Phase 2 environment, skip the environment-creation command.
No API keys, database server, or `.env` file are required. SQLite is part of
Python. The archive contains both official model assets and excludes databases.
Existing databases are retained; startup creates missing schema objects without
dropping tables or data. Schema versions newer than this app are rejected.

The lock targets Windows/Python 3.11. `requirements.txt` pins runtime dependencies;
`requirements-dev.txt` adds pytest. Use `.venv/bin/python` on Linux/macOS. Native
tray/hotkey support was verified on Windows; other desktops may need platform
setup. Use `--no-tray --no-hotkey` when those integrations are unavailable.

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
also remains silent in Phase 3. Commands apply between inference iterations;
slow inference can delay an apparent mode change or exit request. The preview
and tray reflect the applied mode. A failed tray/hotkey setup produces a warning
and leaves detection/logging running with the remaining controls.

## Storage and session semantics

Default database: **`data/phone_watch.db`**, relative to `config.yaml`.
The schema retains the spec's `sessions`, `phone_events`, and
`idx_phone_events_detected_at` index. `roast_text` stays NULL in this phase.
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
database can have committed data in its `-wal` file. No dashboard is included yet.

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
rejection is not claimed. Phase 3 adds persistence, not a different classifier.

## Files

```text
main.py                 Runtime, preview, persistence wiring and CLI
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
remain applicable. No LLM, TTS, cloud logging, or dashboard code is stubbed in.

Official references: [pystray](https://pystray.readthedocs.io/en/latest/usage.html),
[keyboard](https://github.com/boppreh/keyboard),
[MediaPipe](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python),
[YOLO26](https://docs.ultralytics.com/models/yolo26/).
Historical Phase 1/2 instructions and measurements remain under `docs/`.
