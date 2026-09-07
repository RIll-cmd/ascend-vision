# Phase 2 verification — 2026-09-06

Windows, Python 3.11, AMD Ryzen 5 5600H, CPU inference. Versions are captured in
`requirements-lock.txt`: MediaPipe 1.0.1, Ultralytics 8.4.141, matching OpenCV
4.12.0.88 distributions, NumPy 2.2.6, PyTorch 2.14.0.

## Automated checks

- `python -m pytest -q`: **55 passed in 2.03 seconds**.
- `python -m pip check`: **No broken requirements found**.
- `python main.py --help`: exit 0, updated Phase 2 options.
- Explicit setup fetched Google's Hand Landmarker v1 float16 task file and reused
  the existing YOLO26n checkpoint. Downloads are atomic and idempotent.

Coverage includes all Phase 1 regressions plus wrist/tip-only geometry, strict
proximity boundary, center versus box distance, absent/distant hands, missing
phone, short false candidates, consecutive confirmation, one start per continuous
hold, evolving duration, release, cooldown suppression without losing pickups,
observation gaps, repeated/reversed timestamps, two hands, nonfinite landmarks,
shutdown finalization, RGB conversion, pixel coordinate scaling, millisecond
timestamp collisions, hand-resource cleanup, invalid settings, hand-model
download failures, and main-loop hold start/end logging.

## Real models

Both actual model APIs processed ten blank 640×480 frames without phone/hand false
detections. The MediaPipe tracker closed successfully, including repeated close.

Google's official
[woman_hands.jpg test asset](https://storage.googleapis.com/mediapipe-assets/woman_hands.jpg)
was downloaded into the workspace scratch directory. Actual MediaPipe output
contained 21 landmarks per detected hand on 15 successive inferences. Combining
those real landmarks with a **controlled nearby phone box** produced exactly one
confirmation on observation 12 and a final duration of 0.3 seconds. A controlled
distant phone box never confirmed. This validates real hand output integration;
it is not an end-to-end real-phone accuracy test.

## Live camera / preview

The warmed-up default preview ran for 12 requested seconds and exited cleanly:
90 processed frames, 6.98 overall combined-inference FPS, 110.82 ms mean combined
inference latency, 61 superseded frames dropped. Both camera and MediaPipe closed.
The camera reopened successfully for another run. No phone or hands were found
in the available live scene; an actual held-phone demonstration remains manual.

The optional 320-pixel / one-thread preset completed a 15-second console run,
but averaged only 0.83 FPS in that run, with highly variable latency. It is a
smaller computational workload, **not a proven performance improvement** on this
machine. A separate boundary timing check measured hand inference around 19–23 ms
and substantially variable YOLO latency. A system snapshot showed significant
competing CPU usage from antivirus and other applications. These were not
controlled performance benchmarks; no conclusion about a single bottleneck or
guaranteed speedup is justified.

The **15 FPS target remains unmet/unverified**. Slow observations also lengthen
the 12-frame confirmation interval. Gaps over one second deliberately reset
continuity; adjust `max_observation_gap_seconds` only with the implications for
stale evidence in mind. Repeat calibration and benchmarks under representative
load before relying on detection behavior.

MediaPipe emitted native feedback-tensor and normalized-ROI warnings during
inference. They did not prevent successful blank/positive-image inference or
clean shutdown. First initialization also generated a Matplotlib font cache.
Model creation and explicit blank warmup now precede loop timing; lazy work for
the first real hand and camera startup can still affect observed throughput.

## Interpretation and privacy

The tested algorithm rejects desk phones when wrists/tips are absent, distant or
only briefly nearby. It cannot distinguish a sustained nearby resting hand from
a grip using proximity alone. Full scene accuracy and universal desk-phone
rejection are **not claimed**. Use the README's manual calibration checklist.

Every confirmed hold is exposed as an immutable event snapshot. Cooldown marks
alert eligibility without deleting habit events, as documented in the README.
No database, session UI or feedback is implemented in Phase 2.

No live webcam frame was recorded or uploaded. Only model files and the public
test image were downloaded. Normal inference uses local model files and memory.
