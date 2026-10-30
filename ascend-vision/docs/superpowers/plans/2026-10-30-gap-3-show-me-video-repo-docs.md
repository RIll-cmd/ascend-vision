# Gap 3: Show me once — screen video, repo docs, Room memory

**Goal:** a recording also saves a screen video; a guide whose title names a repo is copied into that repo's
`docs/howto/`; and the Agent Room's project memory for that repo gets a pointer to it, so Room agents read it.

## Research

- `assistant/show_me.py` `HowToRecorder`: screenshots per narrated line and on change, `guide.md` in
  `data/howto/<date>-<slug>/`. Capture is `capture_desktop` (active monitor, PIL image).
- Narration already comes through the voice listener, which transcribes with faster-whisper
  (`voice_listener.py`). A second microphone stream is not needed for the words.
- OpenCV (`cv2`) is installed (camera pipeline); `cv2.VideoWriter` with `mp4v` writes `.mp4` without ffmpeg.
- Room project memory: `<room data>/memory/<repo folder>.md`, one-line notes (≤200 chars, 60 kept), shown to
  every agent in that repo's sessions. API through Hub: `GET /api/room/memory?key=<folder>` → `{notes}`;
  `POST /api/room/memory {key, notes}` replaces the list.

## Design

1. `ScreenVideo(path, capture, *, fps=2, max_width=1280)`: a thread grabs `capture()` every `1/fps` s and writes
   frames with `cv2.VideoWriter(path, mp4v)`; frames are resized to the first frame's size; `stop()` joins and
   releases; capture errors skip a frame. Stops itself after `max_seconds` like the recorder.
2. `HowToRecorder(video=None, repos=None, remember=None)`:
   - `video(path) -> object with stop()`; started in `_begin` (`recording.mp4`), stopped on finish, cancel, time cap.
   - Guide gets `Screen recording: [recording.mp4](recording.mp4)` when the file exists.
   - `repos() -> list[Path]`; `named_repo(title, dirs)` (shared with the Room queue, in `integrations/agent_room.py`)
     picks a repo whose folder name is in the title. After the guide is written, `guide.md` and the screenshots
     (not the video) are copied to `<repo>/docs/howto/<slug>/`; nothing is committed.
   - `remember(repo_folder_name, note) -> bool`; note `How-to "<title>": docs/howto/<slug>/guide.md`.
   - Ready line: `Your how-to is ready: X, 4 steps. Also saved to ascend_hub's docs/howto.` (+ ` and its Room
     memory` when remembered).
3. `RoomClient.add_memory(key, note) -> bool`: GET, append if new, POST.
4. Main: `video=lambda path: ScreenVideo(path, capture_desktop)`, repos as the other helpers, remember via
   the Room client when the Agent Room is on.

## Rulings

- The words stay the live faster-whisper transcript; the video has no sound — cost: a silent video.
- 2 frames a second, 1280 px wide, kept in `data/howto` only (not copied to the repo) — cost: choppy video;
  repos stay small.
- Copy to a repo only when the title names one; files are left uncommitted — cost: the owner commits them.
- The Room memory note is added without a separate approval, because the owner asked for the guide — cost: one
  memory line per guide (the Room keeps 60).

## Tasks (TDD)

1. `ScreenVideo` — test: real mp4 written from fake frames, readable by `cv2.VideoCapture`, odd sizes resized,
   capture errors skipped.
2. Recorder video — tests: started on begin, stopped on finish and cancel, guide links it.
3. `named_repo` + repo copy + memory — tests: named repo gets `docs/howto/<slug>/guide.md` and shots, not the
   video; no name → no copy; memory note text; ready line wording; copy failure still reports the guide.
4. `RoomClient.add_memory` — tests: appends once, unreachable → False.
5. Wiring, README.
