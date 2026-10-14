# Lightweight Vision — design and results

Date: 2026-10-14.

## Goal

Vision runs all day next to Hub and the agent room. It should use far less CPU and memory while watching the camera, with the same detection rate and the same voice quality.

## What the measurements showed

Full Vision (`main.py --env-file .env`), measured over a 10-second window after a 25-second warm-up, on a 12-thread laptop:

- **The main process:**
  - kept about 7.5 cores busy;
  - used about 1.1 GB working set and 1.6 GB private memory;
  - ran 135 threads.
- **The detector:** used only about half a core (78 ms per inference at about 6.9 inferences per second).
- **Where the CPU went:** seven threads sat at 100% each. They were OpenMP workers spin-waiting between tasks.
  - Running with `OMP_WAIT_POLICY=PASSIVE` and `KMP_BLOCKTIME=0` dropped the process to 2.3 cores at the same inference rate.
  - Adding `OMP_NUM_THREADS=2` cut private memory by about 320 MB, with no speed change.
- **Kokoro TTS:** its ONNX Runtime session used the defaults, with one intra-op thread per physical core and spinning enabled.
  - Two non-spinning threads made one sentence take 1.97 s instead of 1.20 s.
  - Four non-spinning threads took 1.25 s.
- **Capping OpenCV threads** (`OPENCV_FOR_THREADS_NUM=2`) changed CPU only within run-to-run noise (2.05 against 1.90 cores), so it is not applied.

## Changes

- `runtime_env.py` sets `OMP_WAIT_POLICY=PASSIVE`, `KMP_BLOCKTIME=0` and `OMP_NUM_THREADS=2` with `setdefault`, so values the owner sets in the environment still win.
  - `main.py` imports it before OpenCV.
  - `desktop_host.py` imports it before its first application import.
  - OpenMP reads these values only once, when the first native library that uses it loads.
- `tts_engine.kokoro_session_options()` builds the Kokoro session with:
  - 4 intra-op threads and 1 inter-op thread;
  - intra-op and inter-op spinning disabled.
  - `KokoroSpeaker` loads the model through `Kokoro.from_session`.

## Results

| What | Before | After |
| --- | --- | --- |
| CPU (main process) | 7.55 cores | 2.05 cores |
| Private memory | 1,577 MB | 1,255 MB |
| Threads | 135 | 122 |
| Inference rate | 6.6–6.9 per s, 78–79 ms | 6.9 per s, 78 ms |
| One spoken sentence (Kokoro) | 1.20 s | 1.25 s |

Tests: `python -m pytest tests` → 1,244 passed. New tests:
- `tests/test_runtime_env.py`: the policy's defaults, owner overrides, and the import order in both entry points;
- `tests/test_tts_engine.py::test_kokoro_session_uses_four_quiet_threads`.

## Not changed

- Working-set memory stays about 1.1 GB. The largest remaining parts are:
  - the fp32 Kokoro model (310 MB);
  - torch and Ultralytics for YOLO (about 190 MB of imports);
  - MediaPipe;
  - faster-whisper.
- Further options, each a separate decision:
  - an int8 Kokoro model (a download, about 90 MB);
  - running YOLO through ONNX Runtime without torch;
  - loading voice commands and TTS only on first use (slower first response).
