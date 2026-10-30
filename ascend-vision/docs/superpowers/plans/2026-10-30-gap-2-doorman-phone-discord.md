# Gap 2: Focus doorman — phone pushes and Discord messages

**Goal:** during a focus block, hold what Vision pushes to the phone, and answer people who message Vision's
Discord bot like a receptionist: "In a focus block until 3:40. I'll pass it on." Urgent ones (by sender or
keyword) come through now; the rest wait and play when the block ends.

## Research

- What Vision pushes to the phone: `integrations/notification_publisher.py` publishes only
  `agent_needs_input` events to Core (opt-in env `ASCEND_NOTIFICATION_PUBLISHER_ENABLED`); Core delivers them as
  PWA push or Discord DM (`notification_worker.py`). `CompanionRuntime` calls `publisher.publish(intent)`;
  a `False` return leaves the event unpublished, so it is offered again on a later tick.
- Inbound: phone chat (PWA) and the Discord bot carry only the owner's own messages. The bot
  (`integrations/discord_phone_bot.py`, its own process via `discord_bot.py`, same `config.yaml` and data
  folder) answers a non-owner with `_LINK_FIRST`. Vision cannot read the owner's SMS, calls, or DMs sent to the
  owner's own Discord account (automating a user account is against Discord's rules).
- `FocusDoorman` (`assistant/announcement_pacer.py`) keeps `_until` in memory only; the pacer already holds
  ordinary notices and plays "While you focused, N notices waited." then the notices when the block ends.

## Design

1. `FocusDoorman(state_path=None)`: `until() -> datetime | None`; writes `{"until": iso}` to `state_path` when a
   block starts and removes the file when it ends (asked, or expired). The bot reads this file; a stale file is
   harmless because `until` is in the past.
2. New `assistant/doorman_inbox.py`:
   - `Reception(folder, *, clock, urgent_words=(), urgent_senders=(), per_hour=3, max_chars=300)` (bot side):
     `take(sender_id, name, text) -> str`. Cleans text (printable, one line, capped), appends
     `{"at","sender","name","text","urgent"}` to `folder/doorman_inbox.jsonl`. Urgent = a word from
     `URGENT_WORDS` + `urgent_words` in the text, or `sender_id`/`name` in `urgent_senders`. Replies:
     block + routine `In a focus block until 3:40 pm. I'll pass it on.`; block + urgent
     `In a focus block until 3:40 pm, but this sounds urgent, so I'm passing it on now.`; no block
     `I'll pass it on.`; over the hourly limit `I have your earlier messages and will pass them on.` (not stored).
   - `Inbox(folder, *, announce)` (Vision side): `tick()` renames the file to `doorman_inbox.reading.jsonl`
     (reads a leftover one first), announces each line, deletes it. Line: `Message from Alex on Discord: <text>`,
     urgent ones `Urgent message from Alex on Discord: <text>` with `urgent=True`. Bad lines are skipped.
   - `hold_pushes(publisher, holding)`: an object whose `publish(intent)` returns False while `holding()`.
3. Bot: `DiscordPhoneBot(receptionist=None)`; a non-owner `/ask` gets `receptionist(user_id, name, text)`.
   `discord_bot.py` builds a `Reception` when `focus_doorman.enabled` and `take_messages`.
4. Main: doorman gets `state_path=data/focus_block.json`; `Inbox` ticks in the report loop; the companion
   runtime gets `hold_pushes(publisher, lambda: doorman.until() is not None and present())`.
5. Config `FocusDoormanConfig`: `take_messages: bool = True`, `urgent_words: tuple[str, ...] = ()`,
   `urgent_senders: tuple[str, ...] = ()`.

## Rulings

- The receptionist covers people who message Vision's Discord bot, not the owner's own SMS, calls or Discord
  account — cost: only people who can reach the bot are answered.
- Messages from other people never reach the AI and never trigger anything; they are only spoken, capped at
  300 characters and 3 an hour per sender — cost: long messages are cut.
- Outside a block their messages are spoken as ordinary notices — cost: a stranger can make Vision talk (3/hour).
- Phone pushes are held only while a block runs and the owner is at the PC; when away they still go out —
  cost: none expected; the spoken line already reaches the owner at the PC.
- An owner who has not linked Discord gets the receptionist reply to `/ask` — cost: link first, as before.

## Tasks (TDD)

1. Doorman `until()` + state file — tests: written on start, removed on end and on expiry.
2. `Reception` — tests: block/no block/urgent word/urgent sender replies; file line; cleaning; hourly limit.
3. `Inbox` — tests: announces and empties; urgent flag; leftover reading file; bad lines skipped.
4. `hold_pushes` — test: False while holding, passes through otherwise.
5. Bot receptionist — test: non-owner `/ask` gets the receptionist reply; owner path unchanged; no receptionist →
   `_LINK_FIRST`.
6. Config fields, `discord_bot.py`, `main.py` wiring, README.
