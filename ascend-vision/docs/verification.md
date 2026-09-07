# Phase 1 verification — 2026-09-06

Environment: Windows, Python 3.11; full package versions in
`../requirements-lock.txt`. CPU inference, YOLO26n, OpenCV 4.12.0.88.

## Passed checks

- `python -m pytest -q`: **32 passed in 0.64s**.
- `python -m pip check`: **No broken requirements found**.
- `python main.py --help`: exit 0 with all documented flags.
- `python main.py --download-model`: official YOLO26n weights downloaded
  atomically to `models/yolo26n.pt`; exit 0.
- Real PyTorch checkpoint loading and warmup: successful.
- 30 real-model inferences on synthetic blank 640×480 frames: no false positives;
  approximately 14.36 FPS at 640 inference size. This is a synthetic throughput
  check, not a detection-accuracy benchmark.
- Real OpenCV decoding of a synthetic local video through the threaded capture
  worker and real model: 44 processed frames over 3.05 seconds; 14.45 FPS;
  bounded buffering dropped 38 superseded frames. Worker released successfully.
- Live camera using DirectShow, 640×480 requested capture: opened, processed
  frames, and released. Windows `auto` now selects that verified backend.
- Default preview, eight-second run: exit 0, 96 processed frames, 11.42 overall
  FPS, 65.15 ms mean inference, two superseded frames. Preview calls and timed
  shutdown completed without an OpenCV error; no screenshot was recorded.
- Camera reopened in subsequent runs, confirming normal-path release.

Tests cover configuration defaults/validation, relative model paths, class and
confidence filtering, best-box selection, no detections, capture concurrency,
bounded buffering, open/read failures, timeouts, shutdown, loop logging and
skipping, inference-error cleanup, CLI validation, atomic downloads, missing
weights, preview exit and frame ownership.

## Hardware results and remaining acceptance checks

The initial Windows OpenCV automatic backend blocked before its first frame.
The read timeout and bounded shutdown prevented a process hang. DirectShow worked,
so it is used automatically on Windows; explicit `msmf` remains configurable.

A **20-second live CPU run at 320 inference size** processed **277 frames**:
13.62 overall inference FPS, 13.77 capture FPS, 46.43 ms mean inference, three
dropped frames, exit 0. Counts/rates include camera startup and shutdown overhead.
The camera reported 30 FPS but actual delivered throughput was lower. The
**15 FPS end-to-end target was not achieved in this run**. Do not interpret the
successful functional tests as performance acceptance.

No phone was detected in the available live scene. Positive filtering/logging
paths were tested with controlled model outputs; real-world phone detection
accuracy still requires the phone-in-view lighting/angle checklist in README.
Desk phones are expected detections in this phase. Holding accuracy cannot be
assessed until Phase 2.

No real webcam frames, images or videos were saved or uploaded. Only a generated
blank video was written in the workspace scratch directory for decoder testing.
The installed Ultralytics implementation was checked: `YOLO_OFFLINE` disables
online detection used by telemetry guards, and `settings.sync` is disabled before
model construction.
