# Release notes factory — design

Date: 2026-10-30. From the "product listing factory" idea; the owner opted out of questions, so the calls marked
*(my call)* are Claude's.

## Why

The owner ships often (Hub: 92 commits in two weeks) but never writes what changed. "Vision, write release notes
for ascend hub" turns the commits since last time into a grouped changelog, a short "what's new" for people who use
the app, and a post the owner can share.

## Research findings that shaped it

- Commit subjects follow Conventional Commits (`feat(room): ...`, `fix(vision): ...`, `docs: ...`).
- Tags are rare: Vision has two, none reachable from `main`; Hub and Core have none. So the range cannot rely on
  tags.
- The repos are the same set the leftover check-in reads (`revival.repos`, or the git folders next to Vision's).

## What it does

- **Asking:** "write release notes" (the repo with the newest commit) or "write release notes for <repo>" (spoken
  names match folder names ignoring case, spaces, `-` and `_`; several matches: the one with the newest commit).
  Unknown: "I don't know a repo called <name>." Reply "Writing the release notes for <repo>." and the work runs on
  a background thread; one at a time.
- **Range:** commits since the last notes Vision wrote for that repo (stored commit), else since the newest tag
  reachable from HEAD, else the last 14 days; no merges; at most 200. None: "Nothing new in <repo> since the last
  notes." *(my call)*
- **Changelog:** grouped by type: New (`feat`), Fixes (`fix`), Docs (`docs`), Other (everything else), each line the
  subject without its prefix and the short hash.
- **What's new and the post:** the AI turns the subjects into up to 5 plain bullets about what people can do now and
  one first-person post under 280 characters. No AI, or a bad answer: the first five new features become the
  bullets and the post is "New in <repo>: a, b and c." *(my call)*
- **Output:** `data/release-notes/<repo>-<YYYY-MM-DD>.md` (a second one the same day gets `-2`). Nothing is written
  into the repo, no tag is made, nothing is pushed or posted.
- **Answer:** paced notice "Release notes for <repo> are ready: 12 changes, 8 new and 3 fixes."
- **Config:** `release_notes.enabled` (true), `release_notes.folder` ('' = `data/release-notes`). Not added to
  `config.yaml`.

## Non-goals

- Fresh screenshots for the notes.
- Writing CHANGELOG.md into the repo, tagging, or publishing the post.
