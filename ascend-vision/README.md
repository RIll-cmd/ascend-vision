# Ascend Vision

A friendly eye on your desk that notices when you pick up your phone, keeps you
company while you focus, and chats with you by voice or text. Everything runs
on your computer.

## Get started (Windows)

1. **Double-click `Setup.bat`.** It installs everything and asks for one free
   AI key (press Enter to skip; Vision still works with simple replies).
2. **Double-click `Start Vision.bat`.** Your browser opens the Vision window.
3. **That's it.** Vision starts in focus mode. Allow the camera if asked.

Setup needs Python 3.11 and Node.js (LTS) installed once; the window tells you
if one is missing.

## Using Vision

Everything is in the one Vision window:

- **The eye** reacts to you. Click **Chat** to talk to it.
- **Chat / Stats / Memory** tabs: type to Vision, see your phone-pickup numbers
  for 7 or 28 days, and approve or forget what Vision remembers. Say
  "Remember that..." to propose a fact; nothing is kept until you press Keep.
- **Pause** (the pause button) stops the roasts. Vision still counts pickups
  quietly. Press it again to resume.
- **Mic and speech** buttons mute listening or talking independently.
- **Quit** from the tray icon (or close the Vision window's terminal).

There are no modes to learn: Vision always starts in focus. Pickups are camera
estimates, not proof of grip, and nothing leaves your computer except short
text sent to your AI provider (see below).

## Settings you may want

Everything has a default. Edit `config.yaml` only if you need to:

| Want to... | Change |
| --- | --- |
| Use a different camera | `camera.index` |
| Turn roasts off | `feedback.enabled: false` |
| Hide the tray icon | `sessions.tray: false` |
| Turn on desk/context awareness (off by default) | `companion_context.enabled: true` |
| Run as a light assistant: voice, chat and agent announcements without the camera (about 620 MB instead of 1.1 GB) | `runtime.mode: assistant` (or start with `--assistant`) |
| Stop Vision from announcing Ascend Hub's agent room | `agent_room.enabled: false` |
| Answer agent room gates by voice without saying "Vision" first, or use another wake word | `agent_room.wake_word: ''` (or e.g. `'hey fairy'`) |
| Send phone voice notes to Gemini instead of transcribing them on this computer | `phone_chat.voice_transcriber: gemini` |
| Stop slump check-ins | `slump.enabled: false` |
| Keep slump check-ins but never push the phone | `slump.phone_nudge: false` |
| Stop promise check-ins | `promises.enabled: false` |
| Turn off "do not disturb" focus blocks | `focus_doorman.enabled: false` |
| Hold agent room announcements during a focus block too | `focus_doorman.let_room_through: false` |
| Keep how-to guides somewhere else, or write them without the AI | `show_me.folder: 'D:/guides'`, `show_me.ai_cleanup: false` |
| Stop the weekly "something you left" check-in, or pick which repos it reads | `revival.enabled: false`, `revival.repos: ['D:/ascend_hub']` |
| Stop the weekly prompt coaching, or read the Agent Room from another folder | `prompt_coach.enabled: false`, `prompt_coach.room_data: 'E:/hub/.sites-runtime/agent-room'` |
| Turn off "read this for me", or read PDFs from another folder | `read_for_me.enabled: false`, `read_for_me.downloads: 'E:/Downloads'` |
| Turn off release notes, or keep them somewhere else | `release_notes.enabled: false`, `release_notes.folder: 'D:/notes'` |

While a gate waits, say the wake word, then the answer ("Vision, approve"); the
follow-up "yes" needs no wake word for 20 seconds. Phone messages and voice notes
(`phone_chat.voice_upload_enabled`) can answer gates too, but merge and reject are
only taken at the computer.

Vision keeps a small daily record in `data/slump.db` for 90 days: your GitHub
contributions, Agent Room sessions started, minutes active at the PC (and how many
after midnight), focus-mode minutes and minutes on distracting sites. It compares the
last three days with your own usual from the two weeks before. When two of them slip,
it says so once, kindly, during the day, and offers one small step; on the third such
day it can send one plain phone push through the Agent Room. Say "Vision, how am I
doing?" any time, or "not now" to pause it for three days. Nothing new leaves the
computer.

Say or type "remind me to call mom at 6" or "I'll fix the login tomorrow" and Vision
keeps it in `data/promises.json`. When it is due and you are at the PC, Vision asks
once whether it is done; if not, it asks again the next day, then offers to keep it,
make it smaller or drop it, and goes quiet after five asks. Answer "done", "not yet",
"smaller" or "drop it"; say "what did I promise?", "done with the login" or, right
after Vision notes one, "drop that". "Remind me Friday" on its own makes Vision ask
what for. Before the first check-in Vision looks for a commit of yours (by your git
email) or a finished Agent Room session that matches; if it finds one it asks "Looks
like that is done ... Cross it off?" instead of reminding you. From the third ask, with
the Agent Room on, answer "queue it" to add the job to the Room's queue (in the repo
the promise names, else the one you committed to last); it starts as soon as the Room
is free. In phone chat and in a Discord DM to Vision's bot, "remind me to ..." and
"what did I promise?" work too.

Say "Vision, do not disturb" (or "focus block for 25 minutes"; 50 minutes if you give
no time) and Vision's own notices (weather, GitHub, food, check-ins) wait until the
block ends and you are back at the PC; then it says how many waited and plays them.
Agent room announcements and reminders you set for a clock time still come in. Ask
"what's waiting?", say "let them through" to hear them early, or "end focus block".
During a block Vision also holds its phone pushes while you are at the PC. People who
message Vision's Discord bot get a front-desk answer ("In a focus block until 3:40 pm.
I'll pass it on.") and their message waits like any notice; one with "urgent",
"emergency", "asap", "call me" (or your `focus_doorman.urgent_words`), or from someone
in `focus_doorman.urgent_senders` (Discord user ids), comes through now. When the block
ends Vision says how many notices and Discord messages waited, then reads them, each
with how long ago it came in. Their messages are only read aloud, never sent to the AI;
set `focus_doorman.take_messages: false` to turn this off.

Say "Vision, watch me" (or "watch me set up the printer"), do the task while you talk
it through, then say "done recording". Vision takes a screenshot with each thing you
say (and when the screen changes), then writes `data/howto/<date>-<title>/guide.md`
with numbered steps and those screenshots. Only the words you said go to the AI to tidy
the steps (`show_me.ai_cleanup`); screenshots stay on this computer, so mind what is on
screen. "cancel recording" throws it away; recording stops by itself after 30 minutes.
It also saves a screen video of the monitor you work on (`recording.mp4`, H.264 at 2
frames a second, with your narration as sound, so it plays in a browser or on GitHub)
and, afterwards, a Whisper transcript with timestamps (`transcript.md`, made on this
computer). When the title names one of your repos ("watch me deploy ascend hub"), or
when you say "save it to ascend hub" within ten minutes, the guide and screenshots are
copied to that repo's `docs/howto/<title>/` and, with the Agent Room on, a line pointing
to it goes into that repo's Room project memory. Say "commit the guide" to commit just
that folder (anything else you staged stays as it was), so Room agents get the file
too. If Vision quits mid-recording, the draft guide and the video are kept.

Once a week, during the day, Vision brings back one thing you left: a branch with
unmerged commits untouched for two weeks, an extra worktree nobody committed to for two
weeks (or whose folder is gone), an Agent Room session you stopped or left at a gate,
or a GitHub issue you opened that nobody touched for a month. It looks at the git
repos next to Vision's own folder (or `revival.repos`). Answer "revive it" (with the
Agent Room on, a branch, issue or stopped session is queued there and starts when the
Room is free; anything else becomes a promise for tomorrow), "archive it" (a branch is
renamed to `archive/<name>` and keeps every commit; an issue is closed as not planned;
worktrees and sessions are never deleted, only not asked about again), "later" (asked
again in a month) or "forget it"; ask "what did I leave?" any time.

Once a week Vision also looks at your Agent Room sessions from the last 30 days (read
from the Room's own files, no AI) and coaches your task prompts in three lines: how many
went smoothly, what the smooth ones had that the rough ones lacked (named files, said
what done looks like, a check command, listed steps, an example), and one tip. Ask "how
are my prompts?" any time.

Say "read this for me" (the screen your active window is on), "read what I'm holding" (a page held to the camera)
or "read my latest download" (the newest PDF in Downloads from the last week), or name
one: "read the lease pdf" or "read the pdf called rental agreement" (the newest PDF with
those words in its name in Downloads, Desktop or Documents, OneDrive's too). You can
also drop a PDF or a picture into `data/drop-to-read/`: Vision reads it within a few
seconds and moves it to `data/drop-to-read/read/`. Vision
sends that one image or PDF to Google Gemini, then says what it is and the two most
serious red flags (automatic renewal, fees, cancellation notice, deadlines...), and keeps
the full notes in `data/readings/`. It is a first look, not legal advice. Nothing is sent
unless you ask.

Say "check the licenses" and Vision looks at the packages the newest Agent Room session
added (`package.json`, `requirements*.txt`, `pyproject.toml`, compared with where the
session started), looks each license up on npm or PyPI (only the package names are
sent), and names the ones to look at twice: GPL, AGPL, SSPL, non-commercial or no license
high; LGPL, MPL or unknown medium. Local, git and URL dependencies are never looked up,
and if the registries can't be reached it says so instead of guessing. Notes go to
`data/readings/`.

Say "write release notes for ascend hub" (or just "write release notes" for the repo you
committed to last; "vision" is enough for ascend-vision). Vision groups the commits since
its last notes for that repo (or the last tag, or the last two weeks, or at least the last
20 commits) into New, Fixes, Docs and Other, asks the AI for a short "what's new" list and
a post you could share, and saves it to `data/release-notes/`. It never tags or posts
anything. Within ten minutes, say "add it to the readme" to put the "What's new" list in
a marked section near the top of that repo's `README.md` (said again, it replaces that
section), and "commit the readme" to commit just that file. Paste two to five posts you wrote into `voice.md` in the
release-notes folder (blank line between them) and the post is written in your voice.
Fresh screenshots of the pages in `release_notes.screenshots` (`"repo=url"` lines; Hub's
local page by default) are taken with the installed headless browser and linked in the
notes; if that page is not running, the notes simply have none. A screenshot shows
whatever the page shows at that moment (Hub's home page includes what Spotify is
playing), so look at it before you post it.

Advanced and experimental features (phone chat, Discord, browser automation,
proactive check-ins, Core link) are off by default and are described below.
Reference notes follow for people who want the details.

---

# Reference

## Advanced: the separate habit dashboard (optional)

The Vision window already shows your stats and memory. This older, fuller
dashboard is only needed for browser automation, laptop context controls, daily
reflection, and the Core sign-in handoff. From the project directory, run this
in a **second PowerShell window** while Vision runs:

```powershell
.\.venv\Scripts\python.exe dashboard.py
```

It opens **http://127.0.0.1:8765** in your browser. Ctrl+C in that terminal stops
only the dashboard. Detection, Gemini feedback and SQLite logging run separately.
The dashboard itself needs no camera, model downloads or Gemini credentials.
It can also inspect existing history while detection is stopped.

The dashboard's chat panel needs both processes running. Start `main.py --focus`
in the first window and `dashboard.py` in the second, using the same `--config`
file if you override the default. Dashboard and Fairy Eye subscribe independently
to the same live laptop turns; opening one does not consume messages from the
other. Messages queued for a previous runtime are discarded at startup.
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
and “What is Antigravity's status?” by voice or dashboard chat from Core's live
status shelf. These read-only answers do not require a model API key, but do require
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

## Laptop companion context V1 (opt-in)

To try the new local context card, set `companion_context.enabled: true` in the
same YAML configuration passed to both `main.py` and `dashboard.py`. Start
Vision and the dashboard in separate PowerShell windows as above. The dashboard
shows desk presence, Windows input/lock state, a coarse foreground app category,
and the existing Focus/Background session mode. It never sends window titles,
webpage contents, keystrokes, audio, or camera frames to the context pipe.

The camera signal reuses Vision's existing face tracker on its current frame
stream. It reports only desk presence, not who is in view. A face must remain
visible for 3 seconds before Vision reports presence; healthy camera absence
must persist for 10 seconds before it reports away. Missing face-tracker or
camera availability is shown as unknown/unavailable.

The desktop adapter samples every 2 seconds. It maps a short local allowlist of
process names to categories and discards the process name after classification.
Input becomes idle after 60 seconds without keyboard/mouse input. If Windows
does not provide a reliable lock-state signal, Vision reports desktop activity
unavailable rather than claiming that the user is active.

The dashboard shows each value's source and age, and can set or undo a temporary
focus, break, research, or meeting intent; set or cancel a 30-minute quiet
interval; clear current context; or pause/resume this companion context feature.
A break defaults to 15 minutes; other intents default to one hour. These values
live only in the running Vision process. Phase 1 has no proactive prompts, so
the quiet interval is recorded for future companion behavior and does not affect
current monitoring. Pause stops the new companion context collector; existing
Phone Watch camera and activity features continue under their current controls.
Phase 1 does not publish laptop context to Core, PWA, or Discord.

Local voice/dashboard chat may read the same current snapshot. A serialized
allowlisted packet is capped at 4 KiB and labels each value with its source,
freshness, and observation age; normal channel transcripts remain separately
scoped. “What am I doing?” and “Am I on a break?” use deterministic wording.
Development-app presence is never described as proof of coding progress.
Current mission questions use Core's owner-checked `missions_summary` read with
the short-lived Vision token; no mission write operation is registered. AI
status answers name the Core shelf and refuse stale snapshots.

“Look at my screen” performs one user-requested inspection, resizes the image to
at most 1280 pixels per side and 2 MiB, and deletes its temporary image after
classification. The result is labeled as an untrusted visual estimate and
expires after 30 seconds; it is not saved as context or memory. Periodic cloud
screen audits are a separate setting (`screen_audit.enabled`) and default off.

Context snapshots and corrections are held in memory and served to the local
dashboard over a same-Windows-user authenticated named pipe. Restarting Vision
starts with unknown context. The feature is disabled by default until you enable
it in configuration.

### Proactive companion — Phase 3 implementation through delivery routing

Local interventions are also disabled by default. They require both
`companion_context.enabled: true` and `proactive_enabled: true`; each rule has
its own opt-in and the mode must be `companion` or `focus_coach` as appropriate.
The focus-break rule is available only in `focus_coach`, after a fresh active
focus session reaches its configured interval (50 minutes by default). A desk
check-in requires its own opt-in, fresh camera-confirmed absence for five
minutes, an active focus session, and an unlocked desktop. Unknown or stale
presence never triggers it.

Each proposed intervention carries a stable, content-free deduplication ID,
evidence timestamps, a reason code, and a short expiry. One local delivery
controller enforces a 15-minute cooldown, a three-per-hour limit, and an
eight-per-day limit. Queued speech rechecks live eligibility immediately
before playback, so a pause, snooze, changed session, or expired observation
cancels it. These interventions do not call Core mutation or bad-habit penalty
paths. Legacy detector warnings remain separate trigger policies but share the
speech queue: accepted non-companion speech preempts queued or active companion
prompts, and a companion prompt does not enqueue while other speech is pending.
The optional agent-needs-input rule requires fresh, explicit Core operation
evidence and its own opt-in; idle, generic stuck status, and elapsed time are
never substitutes. Producer wiring and selected-build integration still need
staging verification.

The shadow-mode explanation view and local pause/snooze controls are
implemented. Remaining acceptance work is the real-wiring arbitration replay,
review of three representative shadow sessions, tuning from that review, and
the seven-day personal pilot. Keep `proactive_enabled` false until those
acceptance checks are complete.

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

- **Tray menu:** Open Vision, Pause (still counts pickups, no roasts), Quit.
  Check the notification-area overflow if the icon is hidden by Windows.
- **Exit:** tray Quit, or Ctrl+C in the terminal.
- Vision always starts in focus mode. Pause is the only other state.

```powershell
# Normal start (what Start Vision.bat runs):
.\.venv\Scripts\python.exe main.py
# Bounded run without the tray:
.\.venv\Scripts\python.exe main.py --no-tray --duration 60
# Developer-only OpenCV camera window (the Vision window is the app UI):
.\.venv\Scripts\python.exe main.py --preview
# Override camera or phone confidence:
.\.venv\Scripts\python.exe main.py --camera 1 --confidence 0.65
```

Paused sessions count pickups silently with no audible alerts. Focus speaks only
for newly confirmed pickups outside the cooldown. Commands apply between
inference iterations; slow inference can delay an apparent change or exit.

## Storage and session semantics

Default database: **`data/phone_watch.db`**, relative to `config.yaml`.
The schema retains the spec's `sessions`, `phone_events`, and
`idx_phone_events_detected_at` index. `roast_text` stores the generated line once speech starts (including interrupted
playback); failed, cancelled-before-playback and background events retain NULL.
A small `app_state` table stores recovery heartbeats; `PRAGMA user_version=1`
identifies the initial schema.

- Startup opens a focus session. Pausing records a `background` session (stored name for "paused"): pickups are still counted, with no roasts.
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
  cleanup callbacks release camera, MediaPipe, tray and database resources
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
  tray: true
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
