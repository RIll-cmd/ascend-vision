# Phone Watch — Phase 1

Local webcam phone visibility detection using pretrained YOLO26n. This phase
does not establish whether a phone is held. A phone on a desk is an expected
detection. Hand tracking and debounce belong to Phase 2; sessions, event
durations, and SQLite logging belong to Phase 3.

## Install (Windows PowerShell)

Run from this `phone_watch` directory. Python 3.11 is recommended and is the
version used for verification. No GPU, API key, database server, or `.env` file
is required. A webcam and permission for desktop applications to access it are
needed for live operation.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --download-model
.\.venv\Scripts\python.exe main.py
```

Using the environment's Python directly avoids PowerShell activation-policy
issues. On Linux/macOS, use `python3.11 -m venv .venv` and replace
`.\.venv\Scripts\python.exe` with `.venv/bin/python` in subsequent commands.
Linux preview may additionally require your distribution's OpenGL/X11 runtime.

The explicit download command obtains `yolo26n.pt` from Ultralytics before
opening the camera. Normal runs require that local file and never fall back to
another model or auto-download a missing model. To install offline, copy the
weights into `models/yolo26n.pt` and install dependencies from a local wheelhouse.
Only load model files from a trusted source.

**Database setup: none for Phase 1.** The attached schema is preserved in
`docs/phone_watch_spec.md`; implementing it along with sessions is Phase 3.

## Run and tune

```powershell
# Preview: Q, Escape, or closing the window stops the app; Ctrl+C also works.
.\.venv\Scripts\python.exe main.py
# Console only, bounded 60-second hardware benchmark (after model warmup):
.\.venv\Scripts\python.exe main.py --no-preview --duration 60
# Camera selection and confidence tuning:
.\.venv\Scripts\python.exe main.py --camera 1 --confidence 0.65
# Alternate configuration file:
.\.venv\Scripts\python.exe main.py --config config.yaml
```

`config.yaml` is loaded relative to the application by default, independent of
the working directory. Model paths are relative to the selected config file.
Command-line camera, confidence and preview options override YAML. Unknown keys,
incorrect types, nonfinite numbers, and invalid ranges fail before camera startup.

The camera worker continuously reads into a single latest-frame slot. Slow
inference drops superseded frames instead of building an increasing delay.
The main thread processes each selected frame once. `every_n_frames: 2` skips
alternate *consumed* frames; it reduces inference frequency, not model latency.
The preview is updated on inferred frames only, so boxes never annotate a newer,
unprocessed frame. Shutdown, read failure and timeout paths release the camera
and close preview windows. A native driver that blocks forever can exceed the
configured shutdown timeout; the app logs this and its daemon worker cannot
keep the process alive.

Every positive inference logs `PHONE_VISIBLE`, capture timestamp in UTC, frame
sequence, confidence, pixel bounding box, inference latency and frame age. These
are frame detections, **not pickup events**. Periodic and final `STATS` report
camera FPS, inference FPS, dropped frames, intentional skips, detections and
mean inference latency. Model warmup is excluded. Final metrics include shutdown
time and console/preview overhead. `target_fps` controls the informational
`MEETS_TARGET` / `BELOW_TARGET` status; it does not manufacture or enforce FPS.

Tune `detector.confidence` (higher filters weaker detections), `image_size`
(try 480 or 320 if CPU-bound; multiples of 32), `cpu_threads`, and camera FPS.
Camera width/height/FPS are driver requests; the negotiated values are logged.
`camera.backend: auto` selects DirectShow on Windows (verified on this machine)
and OpenCV's default on other systems. Use `msmf` to explicitly try Windows
Media Foundation if DirectShow fails.
Hold threshold, proximity and cooldown are centralized under `hold`, validated,
and intentionally inactive until Phase 2.

## Hardware acceptance

1. Run the 60-second command with the default 640×480 capture and 640 inference.
2. Show a phone at different angles and lighting; confirm boxes and positive logs.
3. Remove the phone; confirm positive logs stop and FPS statistics continue.
4. Tune confidence against false positives. Test a phone on the desk too: it is
   supposed to be detected in this phase.
5. Check sustained **inference FPS ≥15**, frame age, and dropped count. If lower,
   reduce inference size and repeat; record the actual config and hardware.
6. Exit and rerun to verify the webcam was released.

Ultralytics' published 38.9 ms figure is **CPU ONNX**, not a guarantee for this
PyTorch pipeline or a particular webcam. See the official
[YOLO26 model documentation](https://docs.ultralytics.com/models/yolo26/) and
[prediction API](https://docs.ultralytics.com/modes/predict/).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

For the exact Windows/Python 3.11 environment used here, install
`requirements-lock.txt` instead of `requirements.txt` (it includes test tools).
Other Python versions/platforms may require different transitive wheels.

Hardware-independent tests use synthetic arrays and controlled camera/model
adapters. These adapters exist only in tests; the application always uses real
OpenCV capture and YOLO inference. Test results and live-hardware verification
limits are recorded in `docs/verification.md`.

## Files and phase boundaries

```text
phone_watch/
  main.py                CLI, loop, preview, logging and FPS metrics
  capture.py             bounded threaded webcam capture
  detector.py            YOLO26 wrapper and PhoneBox result
  config.py              typed configuration and validation
  config.yaml            camera/model/runtime and future hold settings
  requirements.txt       pinned direct runtime dependencies
  requirements-dev.txt   test dependencies
  requirements-lock.txt  complete tested environment
  models/                downloaded model weights
  tests/                 hardware-independent automated checks
  docs/                  supplied spec, implementation plan, verification
```

The spec's `state_machine.py`, `session_manager.py`, `db.py`, `feedback.py`,
and `dashboard.py` will be introduced when their phases are implemented. Empty
or misleading stubs are not included. Phase 1 intentionally does not install
MediaPipe, tray/hotkey tools, cloud SDKs or TTS dependencies.

All frame processing stays on this machine. Frames exist in memory only: no
video/image recording, network inference or image uploads. Ultralytics telemetry
is disabled before model import; its settings are kept in `.ultralytics/` beside
the app. Dependency installation and explicit model download require network
access; detection uses the local weights. Third-party dependencies retain their
licenses (Ultralytics offers AGPL-3.0 and enterprise licensing).
