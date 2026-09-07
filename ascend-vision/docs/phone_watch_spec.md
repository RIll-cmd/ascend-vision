# Phone-Use Detector — Technical Spec

## 1. Overview

A desktop app that watches the user via webcam, detects when they pick up
and hold their phone, and:

1. **Always** logs pickup events in the background for long-term habit
   tracking (digital wellbeing dashboard).
2. **Optionally**, during a user-started "focus session," reacts in real
   time with a sarcastic, spoken AI roast.

Detection itself uses classic computer vision / object detection — no LLM
is involved in spotting the phone. The LLM is only used to generate the
roast line, and only during focus sessions.

**Platform:** desktop, single machine, one webcam.
**Language:** Python 3.11+.

## 2. Architecture

```
Webcam capture
      |
      v
Frame detection (phone bbox + hand landmarks, every frame)
      |
      v
Hold-confirm state machine (debounce over N frames + cooldown)
      |
      +--> Event logger (SQLite) -- always, both modes
      |
      +--> Focus-mode alert (LLM roast + TTS) -- only if focus session active

Event logger --> Habit dashboard (reads SQLite, shows stats)
```

Two independent models run per frame and are fused by simple geometry —
no custom training required for v1:

- **Phone detector:** Ultralytics YOLO26 (nano variant, `yolo26n.pt`),
  using the pretrained COCO `cell phone` class. YOLO26 is optimized for
  CPU-only, real-time inference (Ultralytics benchmarks put the nano
  variant at roughly 39ms/frame on CPU, no GPU required), so this should
  run comfortably in real time on a laptop.
- **Hand tracker:** MediaPipe Hand Landmarker, returns 21 3D keypoints
  per detected hand (wrist, knuckles, fingertips).

"Phone held" = phone bbox present **and** at least one hand landmark
(wrist or fingertip) within a proximity threshold of the phone bbox,
sustained for several consecutive frames.

## 3. Component specs

### 3.1 Webcam capture
- `opencv-python` (`cv2.VideoCapture`), pull frames in a dedicated thread
  so detection latency never blocks the main loop.
- Target 15–30 FPS at 640×480 (no need for higher resolution — it only
  slows detection down).
- Runs continuously whenever the app is running, independent of focus
  mode.

### 3.2 Frame detection
- Run YOLO26n on each frame (or every 2nd frame if CPU-bound), filter
  detections to the `cell phone` class, keep the highest-confidence box
  above a configurable confidence threshold (default 0.5).
- Run MediaPipe Hand Landmarker on the same frame, get landmark lists
  for up to 2 hands.
- Output per frame: `phone_box | None`, `hand_landmarks: list`.

### 3.3 Hold-confirm state machine
Pseudocode:

```python
state = "idle"
consecutive_hold_frames = 0
HOLD_THRESHOLD_FRAMES = 12      # ~0.4s at 30fps, tune per camera/FPS
PROXIMITY_PX = 60               # tune for camera distance/resolution
COOLDOWN_SECONDS = 45
last_event_time = None

for frame in webcam_stream:
    phone_box, hands = detect(frame)

    is_held = phone_box is not None and any(
        distance(lm, phone_box.center) < PROXIMITY_PX
        for hand in hands for lm in hand
    )

    consecutive_hold_frames = consecutive_hold_frames + 1 if is_held else 0

    if consecutive_hold_frames == HOLD_THRESHOLD_FRAMES:
        if not last_event_time or (now() - last_event_time) > COOLDOWN_SECONDS:
            fire_pickup_event(started_at=now(), mode=current_session_mode())
            last_event_time = now()
```

- `HOLD_THRESHOLD_FRAMES`, `PROXIMITY_PX`, and `COOLDOWN_SECONDS` should
  all live in config — they will need tuning per desk setup, camera
  angle, and lighting.
- An event's `duration_seconds` should keep growing (update the open
  event) for as long as `is_held` stays true, then close it when it
  drops back to false.

### 3.4 Session manager
- Two modes: `background` (default, always on, silent) and `focus`
  (user-started, e.g. via a system tray icon or global hotkey).
- Background mode: log every confirmed pickup, no alert.
- Focus mode: log every confirmed pickup **and** trigger the feedback
  layer (3.6).
- A focus session has a start time and end time; store both on the
  `sessions` table (see schema).

### 3.5 Event logger (SQLite)
See schema in Section 4. Every confirmed pickup event is written
regardless of mode — this is what powers the habit dashboard.

### 3.6 Focus-mode feedback (sarcastic AI roast)
- Triggered only when a pickup event fires while `mode == "focus"`.
- Build a short context payload — **no image or video data, ever** —
  e.g.:
  ```json
  {
    "pickups_today": 7,
    "pickups_this_session": 2,
    "session_duration_minutes": 34,
    "time_of_day": "14:20"
  }
  ```
- Send that context to an LLM with a prompt roughly like:
  > "You are a sharp-witted friend calling out someone who just picked
  > up their phone during a focus session. Roast them in 1–2 short
  > sentences — playful, not cruel. Context: {context}"
- Speak the result with TTS. Two options:
  - `pyttsx3` — free, fully offline, robotic-sounding.
  - Cloud TTS (OpenAI TTS or ElevenLabs) — natural voice, needs network
    + API key.
- Respect the same `COOLDOWN_SECONDS` so it roasts once per pickup, not
  once per frame.

### 3.7 Habit dashboard
- Reads directly from SQLite — no separate data pipeline needed.
- Minimum viable views: pickups per day (bar chart), pickups per week
  (trend line), total focus-session time, longest phone-free streak.
- Implementation choice is open: a small `tkinter`/`PyQt` window, or a
  tiny local Flask app with Chart.js. Either is fine for v1.

## 4. Data model (SQLite)

```sql
CREATE TABLE sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time TEXT NOT NULL,          -- ISO 8601
    end_time TEXT,                     -- NULL while session is active
    mode TEXT NOT NULL DEFAULT 'background'
        CHECK (mode IN ('background', 'focus'))
);

CREATE TABLE phone_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    detected_at TEXT NOT NULL,         -- ISO 8601, event start
    duration_seconds REAL NOT NULL,
    confidence REAL,
    mode TEXT NOT NULL CHECK (mode IN ('background', 'focus')),
    roast_text TEXT                    -- NULL unless a roast fired
);

CREATE INDEX idx_phone_events_detected_at ON phone_events(detected_at);
```

## 5. Tech stack

| Purpose            | Library / tool                          |
|--------------------|------------------------------------------|
| Video capture       | `opencv-python`                          |
| Phone detection     | `ultralytics` (YOLO26, `yolo26n.pt`)     |
| Hand tracking       | `mediapipe`                              |
| Local storage       | `sqlite3` (stdlib)                       |
| System tray / hotkey| `pystray` + `keyboard` (or platform equiv.) |
| Roast generation    | Anthropic or OpenAI API (text only)      |
| Text-to-speech      | `pyttsx3` (offline) or cloud TTS API     |
| Dashboard           | `tkinter`/`PyQt`, or Flask + Chart.js    |
| Config              | `pyyaml` (single `config.yaml`)          |

## 6. Suggested project structure

```
phone_watch/
├── main.py            # entry point, wires everything together
├── detector.py        # YOLO26 + MediaPipe wrapper -> (phone_box, hands)
├── state_machine.py   # hold-confirm debounce logic (Section 3.3)
├── session_manager.py # background/focus mode, tray icon / hotkey
├── db.py               # SQLite access layer (schema + queries)
├── feedback.py          # LLM roast prompt + TTS playback
├── dashboard.py          # habit dashboard UI
├── config.yaml            # thresholds, API keys, TTS choice
└── requirements.txt
```

## 7. Build phases (implement + test in this order)

1. **MVP detection** — capture loop + YOLO26n phone detection only.
   Console-log each detection, tune confidence threshold, confirm
   real-time FPS on target hardware.
2. **Hold-confirm logic** — add MediaPipe hands, implement the state
   machine from 3.3, verify it doesn't fire on "phone sitting on desk."
3. **Sessions + logging** — tray icon/hotkey for focus mode, write every
   confirmed event to SQLite regardless of mode.
4. **Sarcastic feedback** — LLM roast + TTS, focus-mode only, with
   cooldown.
5. **Habit dashboard** — read SQLite, show daily/weekly stats and
   streaks.
6. **Polish** — config file for all thresholds, auto-start on login,
   on-desk calibration pass for lighting/camera angle.

## 8. Non-functional requirements

- Real-time performance: target ≥15 FPS on a mid-range laptop CPU with
  no GPU required.
- **Privacy:** all video frames are processed locally and never leave
  the machine. Only derived metadata (counts, durations, timestamps) is
  sent to the LLM API for roast generation — never an image or frame.
- API keys read from environment variables or `config.yaml`, never
  hardcoded.

## 9. Explicit non-goals (v1)

- No mobile app, no multi-camera support.
- No gaze tracking or face recognition — hand-to-phone proximity is
  sufficient for v1.
- No cloud storage of video or logs — SQLite stays local.

## 10. Open tuning parameters (expect to calibrate per setup)

- `HOLD_THRESHOLD_FRAMES`, `PROXIMITY_PX`, `COOLDOWN_SECONDS`
  (state machine, Section 3.3)
- YOLO confidence threshold (Section 3.2)
- Camera position/angle for reliable hand + phone visibility
