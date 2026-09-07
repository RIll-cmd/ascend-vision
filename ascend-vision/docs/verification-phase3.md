# Phase 3 verification — 2026-09-06

Environment: Windows, Python 3.11, CPU inference; the full tested environment is
in `requirements-lock.txt`. New direct dependencies: pystray 0.19.5 and keyboard
0.13.5. SQLite uses Python's standard library.

## Automated verification

- `python -m pytest -q`: **74 passed in 1.78 seconds**.
- `python -m pip check`: **No broken requirements found**.
- CLI help: exit 0 and all Phase 3 flags listed.
- `--init-db`: created the schema without opening a camera; exit 0.

Tests cover the previous phases and:

- Spec schema and mode constraints, foreign keys and durable event updates.
- Atomic switches and rollback when a switch fails.
- Background and focus events, including cooldown-suppressed confirmations.
- A switch during an active hold without splitting/reassigning that hold.
- Buffered-frame timestamp ownership across a session switch.
- Open duration visible from another SQLite reader before release.
- Thread callbacks enqueue requests without using the main-thread SQLite connection.
- Duplicate mode commands, quit commands, and native-control cleanup adapters.
- Per-database ownership lock and reacquisition after release.
- Recovery from heartbeat and the latest committed event duration.
- An actual subprocess calling `os._exit(17)` with an open session: the OS lock
  released, the committed row survived, and the next owner recovered it.
- Full main-loop integration persisting one background pickup and one focus pickup,
  with the first hold spanning a mode switch. Both sessions ended normally.
- Simulated persistence failure: camera and MediaPipe still close and the error
  propagates instead of silently continuing without event logging.

## Real desktop controls

The actual Windows tray icon started and was visible. The real pystray menu-item
actions were invoked programmatically, applying background → focus → background
and quit through the command queue. All three sessions persisted and ended.
The global `Ctrl+Alt+F` hotkey registered successfully and was removed on shutdown.
No keystrokes were injected into other applications. Physical hotkey delivery
under all desktop conditions was not independently verified; callback dispatch
and removal were covered by automated tests.

The preview toggle uses Space, distinct from the default global shortcut's F key.
Native controls were tested separately from the live camera run so neither test
depended on injecting input into an unrelated focused application.

## Live camera and SQLite

A real run used the current models and camera with `--focus --no-preview
--no-tray --no-hotkey --duration 8`, with a temporary database in the workspace
scratch directory. It processed 74 frames, averaging 8.60 overall inference FPS
and 89.31 ms combined inference time, with 25 superseded frames dropped. Exit 0.

The focus session persisted both timestamps (UTC):

- Start: `2026-09-05T20:26:18.603079+00:00`
- End: `2026-09-05T20:26:26.678723+00:00`

Post-run SQLite checks: `integrity_check = ok`, no foreign-key violations, and
zero open sessions. No phone/hand detections occurred in the available live scene;
positive pickup persistence was verified through controlled integration tests.
No live video frames were saved or uploaded.

The earlier detection limits remain: 15 FPS is not achieved consistently, and
proximity cannot prove a grip or universally reject a nearby resting hand.
Phase 3 changes storage and sessions, not that classifier. Real-scene calibration
remains necessary.

## Delivery and scope

The Phase 3 archive includes source, tests, instructions, the dependency lock,
and both local model files. Databases, WAL files, locks, runtime settings, caches,
and test-user activity are excluded. Earlier phase archives remain available.

Every confirmed event is persisted regardless of mode or alert cooldown. Session
changes are serialized on the main thread. Duration checkpoints default to one
second; normal completion forces the final duration. Crash recovery avoids
counting downtime and preserves already committed progress.

Spoken feedback, LLM calls and TTS are Phase 4; the dashboard is Phase 5. No stubs
for those components are included.
