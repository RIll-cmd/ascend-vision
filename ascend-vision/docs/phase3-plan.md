# Phase 3 — sessions and SQLite logging

Authorized scope: background/focus sessions, tray/hotkey controls, durable logging
of every confirmed hold. No LLM/TTS or dashboard yet.

- [x] Test schema creation, constraints, durable event updates, session switching,
  rollback, restart recovery, and concurrent-process exclusion.
- [x] Implement db.py with the spec's sessions/phone_events tables and index,
  foreign keys, WAL, transactions, and a small recovery metadata table.
- [x] Implement session_manager.py: main-thread command queue, background default,
  atomic session switches, event ownership fixed at confirmation, heartbeat.
- [x] Implement controls.py: pystray start/stop focus/quit, configurable hotkey,
  preview Space shortcut and headless CLI controls; remove registrations on exit.
- [x] Wire event creation/update/end and lifecycle cleanup to the existing loop.
- [x] Run unit/integration tests and live tray/camera/database checks; update
  dependency lock, instructions, verification report, and Phase 3 archive.

Session switches apply at a main-loop boundary. Frame timestamps determine event
ownership so buffered frames cannot move an earlier pickup into a later session.
An ongoing hold stays linked to its original session without double counting.
One OS advisory lock per database prevents competing writers/recovery races.
Unclean sessions close at their last persisted heartbeat, not at restart time.
