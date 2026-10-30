# Gap 1: Commitment chaser — evidence, Room queue, phone

**Goal:** close the three gaps between the pitch and the built chaser: look for a matching commit or Room
session before the first check-in, let the owner queue a promise in the Agent Room, and catch promises typed in
phone chat.

## Research

- `assistant/promises.py` `PromiseCoach.tick` builds the ask line under its lock and announces it paced.
  Answers are whole-message regexes in `_answer`.
- The Room's queue: Hub proxies `POST /api/room/queue` with `{"body": <start body>}` to the room server
  (`scripts/agent-room/queue.mjs`), which starts the item as soon as no session runs. A start body with a repo
  needs `project` matching `^[a-z0-9][a-z0-9-]{0,39}$`, `task` (≤4000 chars), `repo` (an absolute path the
  room allows), optional `check`. `GET /api/room` returns `repos: [{dir, check}]`. Both go through the loopback
  proxy Vision already uses (`agent_room.base_url`, default `http://127.0.0.1:5173`).
- Room session files: `<room_data>/sessions/<id>/state.json`; `assistant/prompt_coach.read_sessions` already
  reads finished ones (title, task, stage, created).
- Phone chat (PWA) runs in the Vision process: `PhoneMessageHandler(room_answer=...)` gets each text first and
  its reply goes back to the phone. The Discord bot is a separate process with its own handler.

## Design

1. **Evidence.** `find_evidence(what, commits, sessions) -> str | None` (pure). Key words = `words(what)` minus
   `_STOP`, minus words under 3 letters. A commit subject or Room session title matches when it shares
   `min(2, len(keys))` keys. Returns `the commit 'X' in <repo>` or `the Room session 'X'`.
   `make_evidence(repos, run_git, room_data)` returns `lookup(promise_created: datetime) -> (commits, sessions)`:
   `git log --all --no-merges --since=<iso> --author=<user.email> --format=%s -n 50` per repo (no author filter
   when the repo has no email), and finished Room sessions created after the promise.
   Used on the **first** ask only, outside the coach lock. Line: `Checking in on X. I saw the commit 'Y' in Z.
   Done?` It shapes the question; it never crosses anything off by itself.
2. **Room queue.** `PromiseCoach(queue=None)`: `queue(what) -> (ok, message)`. A whole-message answer
   `queue it`, `queue it in the room`, `send it to the room`, `give it to the room`, `room` to any open ask:
   ok → status `queued`, reply the message; not ok → reply the message, promise unchanged. No `queue` → the
   answer is not taken. Ask 3+ wording with a queue: `Keep it, make it smaller, queue it in the Room, or drop
   it?`.
   `integrations/agent_room.py`: `RoomClient.status() -> dict | None` (GET `/api/room`),
   `RoomClient.queue(body) -> (bool, str)` (POST `/api/room/queue`), and pure
   `room_task(what, repos, newest) -> dict | None`: the repo whose folder name (or its words) appears in `what`,
   else the repo `newest(dirs)` names (latest commit); `project` = slug of the first 3 words; `task` =
   `"<What>. (Queued by Vision from a promise.)"`; `check` = the room's suggestion when given.
3. **Phone.** `PromiseCoach.phone(text) -> str | None`: only explicit `remind me to|about ...` catches and the
   promise list; reply text only, nothing spoken at the PC. Main chains it after the room answer in the PWA
   handler's `room_answer` hook.

## Rulings

- Evidence only on the first check-in, as part of the question — cost: one "yes" still needed.
- Match on 2 shared words (or the only word) — cost: differently worded commits missed; generic words can match.
- Commits from all branches by the repo's `user.email`, since the promise was made — cost: commits made under
  another email are missed.
- Queue repo = named repo, else the one with the newest commit — cost: wrong repo sometimes; the owner removes it
  from the Room queue.
- A queued item starts agent work as soon as the room is free — the owner asked for it in words.
- Phone: PWA only and explicit "remind me" only; the Discord bot runs in another process and is not covered —
  cost: Discord promises and implicit phone promises are missed.

## Tasks (TDD)

1. `find_evidence` + evidence in the first ask — tests: match 2 words; single-word promise; no match; Room
   session; first ask only; lookup failure → plain line.
2. Queue answer — tests: `queue it` closes as queued with the message; failure keeps it open; no queue callable →
   not taken; ask-3 wording names the Room.
3. `room_task`, `RoomClient.status/queue` — tests: named repo wins; newest fallback; slug valid; no repos → None;
   HTTP error message.
4. `phone()` — tests: explicit caught and replied, implicit ignored, list works, nothing announced.
5. Wiring in `main.py`, README lines.
