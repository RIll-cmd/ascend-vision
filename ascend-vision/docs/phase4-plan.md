# Phase 4 â€” focus-only roast and offline speech

Implement the user's Gemini text + pyttsx3 offline choice. No frames or landmark
objects enter the feedback worker or API payload. No new cloud TTS provider or
dashboard is required. API key is read from an environment variable or excluded local .env.

- [x] Write failing tests for metadata-only requests, focus/cooldown gating,
  asynchronous execution, cancellation, failure isolation and SQLite results.
- [x] Add validated feedback settings, typed context and Gemini GenerateContent adapter.
- [x] Add bounded feedback worker and cancellable pyttsx3 playback, all speech
  engine operations on its owning worker thread.
- [x] Query actual pickup/session counts, persist the roast once speech starts,
  wire focus changes and cleanup to the runtime; keep background silent.
- [x] Add diagnostic/test commands, run regression/API-contract/native-TTS checks,
  verify live Gemini generation with synthetic metadata, then package.

Cooldown is owned by feedback, measured between accepted focus attempts using
monotonic time. Background pickups do not consume it. Busy/stale requests are
not queued for later surprise playback. Mode/session changes invalidate pending
work and stop speech; in-flight HTTP may finish, but its result is discarded.
SQLite remains on the main thread; the worker returns results through a queue.
Failure to generate or speak never stops camera capture or event logging.
