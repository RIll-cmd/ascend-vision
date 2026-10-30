# Dormant work revival — design

Date: 2026-10-30. From the "dead lead reactivation" idea; the owner opted out of questions, so the calls marked
*(my call)* are Claude's.

## Why

The owner leaves work behind: feature branches never merged, `backup/*-wip` branches, Agent Room sessions stopped
halfway, GitHub issues nobody touched. Once a week Vision brings back one of them, says what it was and how far it
got, and asks whether to revive it, leave it for later, or forget it.

## Research findings that shaped it

- On this PC the git repos sit one level under `D:\` (`ascend-vision` has 18 local branches, `ascend_hub` 20).
- The Room's `GET /api/room/sessions` lists `{id, project, stage, title, createdAt}`; `stage` is `stopped` when the
  owner stopped a session, `gate-*` while it waits for the owner, `done` when finished.
- The `gh` CLI is installed; `gh issue list --author @me` reads open issues per repo.
- A Room start needs a repo and project name, so "revive" cannot simply queue a Room session.

## What it does

- **Finding:** in each repo, local branches other than the default (`main`, else `master`) with commits not on it,
  last committed 14 to 365 days ago; Room sessions `stopped` or waiting at a gate, started 3+ days ago; open GitHub
  issues the owner opened, untouched for 30+ days (only when `gh` works; skipped quietly otherwise). Nothing in git
  or GitHub is ever changed.
- **Repos:** `revival.repos` (list of paths); empty means every git repo one level under the folder that holds
  Vision's own repo, at most 20. *(my call)*
- **Asking:** at most once every 7 days, between 10:00 and 20:00, while the owner is at the PC. The scan runs on a
  background thread. Vision picks the most recently touched item that is not forgotten or postponed:
  "Something you left: branch fix login in ascend hub, last touched 3 weeks ago, 4 commits not merged. Revive it,
  later, or forget it?" (branch and repo names are spoken with spaces instead of `/`, `-`, `_`). *(my call:
  newest first — the freshest leftover is the likeliest to still matter)*
- **Answers** (whole reply, within 2 minutes of the question being spoken):
  - "revive it" → it becomes a promise "get back to <name>" due tomorrow (commitment chaser), reply
    "Okay. I'll check on it tomorrow."; without the promise coach: "Okay. It's yours again."
  - "later" → not mentioned for 30 days: "Okay. I'll bring it up again next month."
  - "forget it" → never again: "Okay. I won't bring it up again."
- **Anytime:** "what did I leave?" / "anything unfinished?" lists up to three from the last scan, or starts a scan
  ("Let me look. Ask me again in a minute.").
- **Storage:** `data/revival.json` (last ask time, postponed and forgotten keys).
- **Config:** `revival.enabled` (true), `revival.repos` ([]). Not added to `config.yaml`.

## Non-goals

- Deleting, archiving or renaming branches, closing issues, or touching Room sessions.
- Starting a Room session for a revived item.
- Pull requests and remote-only branches.
