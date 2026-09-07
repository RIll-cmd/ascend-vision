# Phone Watch — Phase 2

Local webcam phone detection (YOLO26n) plus hand tracking (MediaPipe Tasks) and
a hold-confirm state machine. Both models process the **same captured frame**.
Frames are never recorded or uploaded. This phase console-logs confirmed holds;
SQLite, sessions and feedback remain later phases.

## Install / upgrade (Windows PowerShell)

From this `phone_watch` directory, create an environment if you do not have one:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe main.py --download-model
.\.venv\Scripts\python.exe main.py
```

For an existing Phase 1 environment, run the last three commands to install the
new dependencies, prepare both models, and start Phase 2. The setup command is
idempotent and skips existing weights. Both official weights are also bundled in
the Phase 2 archive. No API key, `.env`, database or database migration is needed.

`requirements-lock.txt` reproduces the tested Windows/Python 3.11 environment.
`requirements.txt` pins direct runtime dependencies; `requirements-dev.txt` adds
pytest. On Linux/macOS use `python3.11 -m venv .venv` and `.venv/bin/python`.

The declared dependencies of Ultralytics and MediaPipe require both OpenCV wheel
names. Both are pinned to **4.12.0.88**, since they share the `cv2` namespace.
Avoid individually upgrading/uninstalling either OpenCV wheel in this environment;
recreate the environment from the lock if it becomes inconsistent.

## Run

```powershell
# Preview: Q, Escape, window close, or Ctrl+C exits and releases resources.
.\.venv\Scripts\python.exe main.py
# Bounded console-only run, excluding model setup/warmup:
.\.venv\Scripts\python.exe main.py --no-preview --duration 60
# Select camera / override YOLO confidence:
.\.venv\Scripts\python.exe main.py --camera 1 --confidence 0.65
# Choose an alternate settings file:
.\.venv\Scripts\python.exe main.py --config config.yaml
# Lower-load preset (320-pixel YOLO inference, one PyTorch thread):
.\.venv\Scripts\python.exe main.py --config config.cpu.yaml --no-preview --duration 60
# Verify:
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

The optional preset reduces computational work but did not improve the measured
live run under the observed system load. Compare settings on your own scene;
neither preset is verified to meet 15 FPS on this machine.

The preview displays the phone box, all 21 landmarks per hand, and
`idle`, `candidate N/12`, or `holding Xs`. Wrist/tips are yellow; the other
landmarks are blue. The confirmed-hold count resets on process restart.

Console messages:

- `PHONE_VISIBLE`: best phone detection on that frame; not itself a hold.
- `HOLD_STARTED`: one message after sustained proximity, with process-local ID,
  confirmation timestamp, confidence, distance, and `alert_allowed`.
- `HOLD_ENDED`: matching ID, duration, end timestamp and reason (`released`,
  `observation_gap`, `shutdown` or `error`).
- `STATS`: capture and combined-inference FPS, phone detections, frames with hands,
  confirmed holds, current duration, dropped frames and intentional skips.

## Hold algorithm and specification decisions

1. Select the highest-confidence COCO `cell phone` box above the YOLO threshold.
2. Track up to two hands in MediaPipe VIDEO mode with strictly increasing
   millisecond timestamps. Convert normalized landmarks to capture-frame pixels.
3. Check wrist/fingertips **0, 4, 8, 12, 16, 20**, as specified in the prose.
   The default `center` metric follows section 3.3's distance-to-center pseudocode.
   Optional `box` measures Euclidean distance to the nearest point on the box,
   matching the prose's alternative description. A point inside the box has
   distance zero in `box` mode. The proximity comparison is strictly `<`.
4. Require `hold.threshold_frames` consecutive positive **processed observations**.
   Missing phone, missing hands, or insufficient proximity immediately resets
   a candidate and ends an active hold. Unprocessed/dropped frames do not count.
5. Confirm once per continuous hold. Duration starts at confirmation, as in
   section 3.3, and updates in memory using monotonic time. Normal release ends at
   the first negative observation. Shutdown/error ends at the last observed
   positive frame, excluding unobserved cleanup time.
6. If observations are separated by more than `max_observation_gap_seconds`,
   end the previous hold at its last observation and start fresh confirmation.
7. Retain **every confirmed hold**. Cooldown determines `alert_allowed` for future
   feedback, rather than dropping habit events. This resolves the conflict
   between section 3.3's cooldown-gated pseudocode and section 3.4's requirement
   to log every pickup. Cooldown starts at the last alert-eligible confirmation;
   suppressed confirmations do not extend it. A hold already underway never
   produces another start when cooldown expires. Phase 2 emits no actual alert.

**A nearby hand is not proof of a grip.** A desk phone without a nearby wrist/tip,
or with only brief proximity, does not confirm. A hand resting within the configured
distance for long enough *can* confirm. This limitation is inherent in the spec's
geometric heuristic, and is not solved by debounce alone. Real-scene tuning is
required; the implementation does not claim universal desk-phone rejection.

## Configuration

Edit `config.yaml`; no code changes are required. Paths resolve relative to the
selected YAML file. CLI options override corresponding YAML values. Invalid
types, ranges, nonfinite numbers and unknown keys are rejected at startup.

| Setting | Default | Meaning |
|---|---:|---|
| `hold.threshold_frames` | 12 | Consecutive inferred positive frames |
| `hold.proximity_px` | 60 | Wrist/tip distance in capture-frame pixels |
| `hold.cooldown_seconds` | 45 | Interval between alert-eligible starts |
| `hold.distance_metric` | center | `center` or `box` |
| `hold.max_observation_gap_seconds` | 1 | Maximum continuity gap |
| `hands.num_hands` | 2 | Maximum hands, 1–2 |
| `hands.detection_confidence` | 0.5 | Palm detection threshold |
| `hands.presence_confidence` | 0.5 | Hand presence threshold |
| `hands.tracking_confidence` | 0.5 | Tracking threshold |
| `detector.confidence` | 0.5 | YOLO phone confidence |
| `detector.image_size` | 640 | YOLO inference size; try 320 if slow |
| `detector.every_n_frames` | 1 | Infer every Nth consumed frame |
| `detector.cpu_threads` | 4 | PyTorch CPU threads |

At 12 processed FPS, 12 consecutive observations take about one second, not the
0.4 seconds quoted for 30 FPS. Skipping frames lengthens confirmation time. Camera
resolution changes also change the physical meaning of `proximity_px`. The
frame buffer retains only the newest image to avoid growing inference delay.
`auto` selects DirectShow on Windows; other explicit backends remain configurable.

## Calibration / acceptance

1. Run with preview and confirm phone box and hand points align with the scene.
2. Place a phone on the desk with hands away: no `HOLD_STARTED` should appear.
3. Briefly pass a hand near the phone for fewer than the configured observations:
   the candidate should reset without a hold event.
4. Hold the phone steadily: one start, increasing duration, one end on release.
5. Repeat inside cooldown: a new hold is still logged, with `alert_allowed=False`.
6. Rest a hand near the desk phone: characterize this known false-positive case
   and tune proximity/confidence for the camera angle and lighting.
7. Check combined inference FPS and adjust size/threshold together. The 15 FPS
   target is not guaranteed; see `docs/verification-phase2.md` for actual results.

## Project files

```text
main.py                 Capture/inference loop, events, preview and metrics
capture.py              Threaded latest-frame camera capture
detector.py             YOLO phone detector and explicit model download
hands.py                MediaPipe hand tracker, coordinates and model setup
state_machine.py        Pure hold-confirm logic and event snapshots
config.py / config.yaml Validated configuration
models/                 YOLO26n and MediaPipe Hand Landmarker assets
tests/                  Regression and Phase 2 behavioral tests
docs/                   Original spec, plans and verification reports
requirements*.txt       Direct, development and fully locked dependencies
```

No future-phase stub modules are included. `HoldEvent` snapshots are available
for Phase 3 persistence; process-local IDs are not database IDs. Hand model and
camera native resources close on normal exit and failures. Native drivers that
block indefinitely can exceed the bounded camera shutdown timeout; the daemon
worker cannot keep the process running.

Network access is needed only for dependency installation and explicit model
setup. Normal runs require local model files; frames remain in memory. Ultralytics
online checks/telemetry are disabled and MediaPipe performs local inference.
Only trusted weights should be loaded. Third-party licenses remain applicable.

API references: [MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python)
and [Ultralytics YOLO26](https://docs.ultralytics.com/models/yolo26/).
Phase 1 historical instructions and measurements remain in `docs/phase1-readme.md`
and `docs/verification.md`; the Phase 1 archive is preserved separately.
