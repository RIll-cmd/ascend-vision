# Commitment chaser — design

Date: 2026-10-30. Owner asked for it from the "payment chasing" idea; owner opted out of questions, so every call
below marked *(my call)* is Claude's.

## Why

The owner says things like "I'll fix the login tomorrow" or "remind me to call mom at 6" to Vision, then forgets.
Vision should catch those promises, check on them when they are due, and follow up gently, then more firmly, until
each one is done, made smaller, or dropped. It must never nag: at most one question at a time, only while the owner
is at the PC and awake.

## What it does

- **Catching.** Typed or spoken text that reaches Vision's local chat (Fairy chat and voice, through
  `local_intercept`) is read for two shapes:
  - explicit: "remind me (to|about) X <when>" or "remind me <when> to X". Vision answers at once:
    "Okay. I'll check on X <when>." The message is not sent on to the AI.
  - implicit: "I'll / I will / I'm going to / I am going to X <when>". The AI still answers normally; Vision adds a
    paced notice "Noted: X, <when>. Say 'drop that' if I got it wrong." *(my call: transparent beats silent)*
  - `<when>`: today (17:00), tonight (20:00), this afternoon (15:00), this evening (19:00), tomorrow (10:00),
    tomorrow morning/afternoon/evening/night (10:00/15:00/19:00/20:00), on/by <weekday> (next such day after today,
    10:00), next week (next Monday 10:00), in N minutes/hours/days, at H[:MM][am|pm] (today, or tomorrow if passed),
    and "tomorrow at 3pm"-style pairs. A time that has already passed today moves to one hour from now.
    *(my call: default hours)*
  - Without a `<when>` nothing is caught. X must be 3 to 120 characters. An open promise with the same X is not
    added twice.
- **Checking.** When a promise is due, the owner is available (away mode not away/asleep) and no other question
  is open, Vision asks through the pacer. Date-only promises are asked only 08:00 to 22:00; promises with an explicit
  time are asked whenever due. *(my call)*
  1. first ask: "Checking in on X. Done?"
  2. next day: "X is still open from <day>. Done yet?"
  3. after that, daily: "X has been open <n> days. Keep it, make it smaller, or drop it?"
  - After 5 asks without an answer that closes it, the promise goes quiet (stale) and is no longer asked;
    it still shows in the list. *(my call)*
- **Answers** (within 2 minutes of the question being spoken, like the slump coach):
  - yes / done / did it → crossed off: "Nice. Crossed off."
  - no / not yet → asked again tomorrow 10:00: "Okay. I'll ask again tomorrow."
  - keep (third-stage) → asked again in 3 days.
  - smaller → "What's the first small step?"; the next thing said becomes a new promise due in 2 hours, replacing X.
  - drop it / forget it / cancel → removed: "Dropped."
- **Anytime commands:** "what did I promise" / "my promises" lists open ones (stale ones last);
  "drop that" / "forget that" within 2 minutes of a capture removes it; "done with X" / "cross off X" closes the
  open promise whose words overlap X most.
- **Storage:** `data/promises.json`, written atomically. Kept: open and stale promises, plus done or dropped ones
  for 30 days. *(my call)*
- **Config:** `promises.enabled` (default true). Not added to `config.yaml`.

## Non-goals

- Queuing a promise as an Agent Room session: a Room start needs a repo and project name the promise does not carry.
- Phone (Discord) messages: they go through the phone handler, not `local_intercept`.
- Checking GitHub or the Room for proof a promise is done: Vision asks instead.
- AI parsing: patterns only, so it works offline and costs nothing.

## Anti-goals

- More than one open question at a time, or a question while the owner is away.
- Catching ordinary chat that has no time in it.
