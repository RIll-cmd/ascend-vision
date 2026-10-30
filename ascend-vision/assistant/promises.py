"""Commitment chaser: catches "remind me to X tomorrow" and "I'll X tomorrow", then checks on X when it is due.

The check-ins climb gently: a question, a firmer question the next day, then keep, make it smaller, or drop it.
Promises live in a small JSON file next to Vision's other data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
PART_HOURS = {'morning': 10, 'afternoon': 15, 'evening': 19, 'night': 20}
_CLOCK = r"\d{1,2}(?::\d{2})? ?(?:am|pm)?"
_WHEN = (r"(?P<when>today|tonight|this (?:morning|afternoon|evening)"
         rf"|tomorrow(?: (?:morning|afternoon|evening|night))?(?: at {_CLOCK})?"
         rf"|(?:on|by) (?:{'|'.join(WEEKDAYS)})|next week"
         r"|in (?:a|an|one|\d+) (?:minutes?|hours?|days?)"
         rf"|at {_CLOCK})")
_EXPLICIT_AFTER = re.compile(rf"\bremind me (?:to |about )?(?P<what>.+?) {_WHEN}$")
_EXPLICIT_BEFORE = re.compile(rf"\bremind me {_WHEN} (?:to|about) (?P<what>.+)$")
_IMPLICIT = re.compile(rf"\b(?:i'll|i will|i'm going to|i am going to) (?P<what>.+?) {_WHEN}(?= |$)")
_NOT_A_TASK = re.compile(r"(?:be|have been|never|not|probably)\b")


@dataclass(frozen=True)
class Caught:
    what: str
    due: datetime
    timed: bool  # a clock time or a duration, asked whenever due; date-only promises wait for daytime
    explicit: bool
    when: str


def _clean(sentence: str) -> str:
    return ' '.join(re.sub(r"[^a-z0-9': ]", ' ', sentence.lower()).split())


def _clock(text: str, day: date, now: datetime, *, roll: bool) -> datetime:
    """``H[:MM][am|pm]`` on ``day``; with ``roll``, a time already gone moves forward (12 h if no am/pm, else a day)."""
    hour, minute, half = re.fullmatch(r"(\d{1,2})(?::(\d{2}))? ?(am|pm)?", text).groups()
    hour, minute = int(hour), int(minute or 0)
    if half == 'pm' and hour < 12:
        hour += 12
    elif half == 'am' and hour == 12:
        hour = 0
    at = datetime.combine(day, datetime.min.time()).replace(hour=hour % 24, minute=minute % 60)
    if roll and at <= now:
        at += timedelta(hours=12) if half is None and hour < 12 and at + timedelta(hours=12) > now else timedelta(days=1)
    return at


def _due(when: str, now: datetime) -> tuple[datetime, bool]:
    today = now.date()

    def at_hour(day: date, hour: int) -> datetime:
        return datetime.combine(day, datetime.min.time()).replace(hour=hour)

    if when in ('today', 'tonight') or when.startswith('this '):
        hour = {'today': 17, 'tonight': 20}.get(when) or PART_HOURS[when.split()[1]]
        due = at_hour(today, hour)
        return (due if due > now else now + timedelta(hours=1)), False
    if when.startswith('tomorrow'):
        tomorrow = today + timedelta(days=1)
        if ' at ' in when:
            return _clock(when.split(' at ')[1], tomorrow, now, roll=False), True
        parts = when.split()
        return at_hour(tomorrow, PART_HOURS[parts[1]] if len(parts) > 1 else 10), False
    if when.startswith(('on ', 'by ')):
        ahead = (WEEKDAYS.index(when.split()[1]) - today.weekday()) % 7 or 7
        return at_hour(today + timedelta(days=ahead), 10), False
    if when == 'next week':
        return at_hour(today + timedelta(days=7 - today.weekday()), 10), False
    if when.startswith('in '):
        _, count, unit = when.split()
        amount = 1 if count in ('a', 'an', 'one') else int(count)
        return now + timedelta(**{unit.rstrip('s') + 's': amount}), True
    return _clock(when[3:], today, now, roll=True), True


def parse_promise(text: str, now: datetime) -> Caught | None:
    """The first promise with a time in ``text``, or None."""
    for sentence in re.split(r'[.!?;\n]+', text):
        said = _clean(sentence)
        for pattern, explicit in ((_EXPLICIT_AFTER, True), (_EXPLICIT_BEFORE, True), (_IMPLICIT, False)):
            found = pattern.search(said)
            if not found:
                continue
            what = found['what'].strip()
            if not 3 <= len(what) <= 120 or _NOT_A_TASK.match(what):
                return None
            due, timed = _due(found['when'], now)
            return Caught(what=what, due=due, timed=timed, explicit=explicit, when=found['when'])
    return None
