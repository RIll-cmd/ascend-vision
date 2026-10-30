# Focus doorman — design

Date: 2026-10-30. From the "missed-call receptionist" idea; the owner opted out of questions, so the calls marked
*(my call)* are Claude's.

## Why

In focus mode the owner should not be pulled out by weather briefings, GitHub nudges, food reminders or check-ins.
Those notices are not lost: they wait at the door and come in, in order, when the focus block ends.

## Research finding that shaped it

Vision never sees other people's messages: phone chat is the owner talking to Vision, and Windows has no public API
for an app to switch Do Not Disturb or read other apps' notifications without extra permissions. So the doorman
holds **Vision's own** unprompted notices, which all pass through `AnnouncementPacer`. *(my call: no Windows
notification access, no auto-replies to other people)*

## What it does

- While focus mode is on (`manager.mode == 'focus'`), the pacer keeps non-urgent notices queued.
- Urgent notices still come through, paced as before:
  - Agent Room lines (agents are blocked until the owner answers), unless `focus_doorman.let_room_through: false`.
  - Promise reminders the owner set for a clock time or a duration ("at 6", "in 20 minutes").
- When focus ends and something waited, Vision first says "While you focused, two notices waited." and then plays
  them in order with the usual gap.
- Anytime in focus: "what's waiting?" → "Two notices are waiting for the end of your focus block. Say 'let them
  through' to hear them now." / "Nothing is waiting."; "let them through" → "Okay." and the waiting ones play now.
- Answers to the owner, conversations and safety alerts never go through the pacer, so they are untouched.
- Config: `focus_doorman.enabled` (default true), `focus_doorman.let_room_through` (default true).
  Not added to `config.yaml`.

## Non-goals

- Reading, holding or answering other apps' notifications or other people's messages.
- Changing Windows Focus Assist / Do Not Disturb.
- A limit on how long a notice may wait: the owner chose focus. *(my call)*
