# Show me once — design

Date: 2026-10-30. From the "SOP and training doc generator" idea; the owner opted out of questions, so the calls
marked *(my call)* are Claude's.

## Why

Things the owner does now and then (set up a printer, release an app, renew a certificate) are forgotten by the next
time. Instead of writing a guide, the owner says "Vision, watch me", does the task while talking, and says "done
recording". Vision writes a step-by-step guide with screenshots.

## Research findings that shaped it

- The microphone is already open in `voice_listener.py`, and every spoken sentence already reaches
  `local_intercept` through the local chat. Narration rides that path; no second microphone stream. Typed Fairy
  chat counts as narration too.
- `screen_auditor.capture_desktop()` already grabs the screen with PIL. `LLMRouter.generate_structured_response`
  returns provider-enforced JSON.
- A local reply is spoken when the message came by voice, so narration needs a reply that is shown but not spoken.

## What it does

- **Start:** "watch me", "show me once" or "record a how-to", optionally followed by a title ("watch me set up the
  printer"). Reply: "Recording. Talk me through each step, and say 'done recording' when you finish." A first
  screenshot is taken.
- **While recording:** every message is a step line with the time and a screenshot taken right then. Vision replies
  with the marker `📝`, shown in chat but never spoken, and the message never reaches the AI or other helpers.
  Every 15 s (the report loop) Vision also takes a screenshot if the screen changed noticeably, attached to the
  latest line. At most 60 screenshots. *(my call)*
- **Stop:** "done recording", "stop recording" or "finish recording" → "Got it. Writing the guide now." The guide
  is written in the background, then a paced notice: "Your how-to is ready: <title>, <n> steps."
  "cancel recording" → "Recording thrown away." and nothing is kept. After 30 minutes the recording stops by itself
  and is written the same way. *(my call)*
- **Writing:** with `show_me.ai_cleanup: true` (default) the numbered transcript lines — text only, never the
  screenshots — go to the AI, which returns a title, a one-line summary, 1 to 30 steps (each naming the lines it
  comes from) and up to 8 checklist items. Anything invalid, or no AI available, falls back to one step per line.
  The owner's own title wins over the AI's.
- **Output:** `data/howto/<YYYY-MM-DD>-<slug>/guide.md` (or under `show_me.folder`), with `shot-NN.jpg` files
  (JPEG, at most 1600 px wide) linked from each step. Screenshots stay on this computer.
- **Config:** `show_me.enabled` (true), `show_me.folder` ('' = `data/howto`), `show_me.ai_cleanup` (true).
  Not added to `config.yaml`.

## Non-goals

- Video recording, or recording system audio.
- Hiding passwords or private windows in screenshots: the owner decides what is on screen while recording.
- Saving into a project repository or sharing; the owner moves the folder if wanted.
- Playing a guide back step by step.
