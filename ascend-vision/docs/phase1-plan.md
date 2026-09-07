# Phase 1 implementation plan

Goal: implement Section 7, Phase 1 of the supplied technical specification.

Architecture: a single camera worker owns VideoCapture and publishes only the
latest frame. The main thread runs a local YOLO26n detector, logs the best phone
box, draws optional preview, and reports capture and processing throughput.
Python 3.11+, OpenCV, Ultralytics, NumPy, PyYAML; CPU is the default device.

The user has authorized implementation of the supplied architecture. Execute
inline in this fresh project directory. Do not create future-phase stub modules.

- [x] Test configuration loading, strict validation, and config-relative model paths.
- [x] Test phone class filtering, confidence selection, and empty detections.
- [x] Test bounded latest-frame capture, timeouts, read failures, and cleanup.
- [x] Implement config.py, capture.py, detector.py and main.py with those interfaces.
- [x] Exercise CLI, real model inference and a finite local video integration run.
- [x] Document installation, tuning, no-database Phase 1 setup and hardware validation.

Interfaces: load_config(Path) -> Config; PhoneDetector(DetectorConfig).detect(BGR)
-> PhoneBox | None; CameraCapture(CameraConfig).read(after_sequence, timeout)
-> Frame; CameraCapture.close() releases the camera on its owning worker.

Acceptance: pytest passes; real YOLO26n can process local frames; command line
help and invalid inputs behave correctly. Measure live camera FPS if hardware is
available, otherwise report that acceptance check as unverified. Never infer
pickup/holding from Phase 1 phone visibility. No frames are saved or uploaded.
