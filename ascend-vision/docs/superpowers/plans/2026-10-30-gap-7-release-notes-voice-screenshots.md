# Gap 7: Release notes — your voice, fresh screenshots, Hub

**Goal:** the post sounds like the owner, and the notes carry fresh screenshots of the app.

## Research

- `assistant/release_notes.py`: `make_story_writer(router)` sends commit subjects with `STORY_PROMPT`; notes are
  `data/release-notes/<repo>-<date>.md`; nothing is pushed or posted.
- Playwright 1.63 and its Chromium are installed in Vision's venv (used by the browser broker), so a headless
  screenshot needs no new dependency.
- Hub's dev server serves the app at `http://127.0.0.1:5173/` on this PC.

## Design

1. Voice: `read_voice(folder) -> list[str]`: up to 5 samples (blank-line separated, 2000 characters in all) from
   `data/release-notes/voice.md`, which the owner fills with posts they wrote. When the file is missing Vision
   creates it with a short how-to comment (ignored as a sample). `make_story_writer(router, voice=None)` adds the
   samples to the request as the owner's own posts to match in tone, length, emoji and hashtags, never copied.
   The ready line adds `Add a few of your own posts to voice.md so the post sounds like you.` while there are none.
2. Screenshots: `release_notes.screenshots` — a list of `"<repo folder>=<url>"`, default
   `["ascend_hub=http://127.0.0.1:5173/"]`. `make_screenshotter(pairs, shoot)` returns `shots(repo, base) ->
   list[Path]`; `shoot(url, path)` uses headless Chromium (1440×900, 20 s). Files sit beside the notes
   (`<base>-shot-1.png`) and are linked under `## Screenshots`. A failed shot (server down) is skipped and the
   line says `The screenshot did not work, so the notes have none.`.
3. Hub: stays in Vision (see Rulings).

## Rulings

- The owner's voice comes from posts they paste into `voice.md`, not from their chats or messages — cost: no
  voice until they add some.
- Screenshots only of URLs listed per repo, default Hub's local page — cost: other repos need a line in config.
- Release notes stay in Vision: it already reads every repo, Hub included, and a second copy in TypeScript would
  have to be kept in step — cost: no button in Hub's pages; ask Vision by voice or chat.

## Tasks (TDD)

1. `read_voice` + writer prompt — tests: samples read, how-to comment skipped, capped; prompt holds them.
2. Screenshots — tests: pairs parsed, only matching repo shot, files linked, failure skipped and said.
3. Config field, wiring, README.
