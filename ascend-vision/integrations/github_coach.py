"""Compare today's GitHub contributions with the owner's recent days, and keep the streak alive.

Reads the public contribution calendar (github.com/users/<name>/contributions), which
includes private contributions when the profile setting allows it. No token is used and
no AI is involved; every sentence here is fixed wording.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import html
import logging
import re
import threading
import time
import urllib.request

LOG = logging.getLogger(__name__)
_CELL = re.compile(r'<td[^>]*?data-date="(\d{4}-\d{2}-\d{2})"[^>]*?id="([^"]+)"', re.S)
_CELL_ALT = re.compile(r'<td[^>]*?id="([^"]+)"[^>]*?data-date="(\d{4}-\d{2}-\d{2})"', re.S)
_TIP = re.compile(r'<tool-tip[^>]*?for="([^"]+)"[^>]*>([^<]*)</tool-tip>', re.S)
_COUNT = re.compile(r'^\s*(\d[\d,]*|No) contributions?\b', re.I)
_QUESTION = re.compile(r'\b(github|commits?|contributions?|streak|pushed|push today)\b', re.I)


def parse_calendar(page: str) -> dict[date, int]:
    """date -> contributions from the calendar HTML; raises ValueError if the layout changed."""
    ids = {cell_id: day for day, cell_id in _CELL.findall(page)}
    ids.update({cell_id: day for cell_id, day in _CELL_ALT.findall(page)})
    counts = {}
    for cell_id, label in _TIP.findall(page):
        day = ids.get(cell_id)
        match = _COUNT.match(html.unescape(label))
        if day and match:
            raw = match.group(1)
            counts[date.fromisoformat(day)] = 0 if raw.lower() == 'no' else int(raw.replace(',', ''))
    if not counts:
        raise ValueError('contribution calendar could not be read')
    return counts


def fetch_calendar(username: str, *, opener=urllib.request.urlopen, timeout: float = 15.0) -> dict[date, int]:
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})', username or ''):
        raise ValueError('GitHub username is invalid')
    request = urllib.request.Request(f'https://github.com/users/{username}/contributions',
                                     headers={'User-Agent': 'ascend-vision'})
    with opener(request, timeout=timeout) as response:
        return parse_calendar(response.read().decode('utf-8', 'replace'))


@dataclass(frozen=True)
class Progress:
    today: int
    average: float          # the 7 days before today
    streak: int             # consecutive days with at least one, ending today or yesterday
    this_week: int          # last 7 days including today
    last_week: int          # the 7 days before that

    @property
    def behind(self) -> int:
        return max(0, round(self.average) - self.today)


def progress(calendar: dict[date, int], today: date) -> Progress:
    def count(day):
        return calendar.get(day, 0)
    previous = [count(today - timedelta(days=offset)) for offset in range(1, 8)]
    streak, day = 0, today if count(today) else today - timedelta(days=1)
    while count(day) > 0:
        streak, day = streak + 1, day - timedelta(days=1)
    return Progress(
        today=count(today), average=sum(previous) / 7, streak=streak,
        this_week=sum(count(today - timedelta(days=offset)) for offset in range(0, 7)),
        last_week=sum(count(today - timedelta(days=offset)) for offset in range(7, 14)))


def _plural(number: int, word: str) -> str:
    return f'{number} {word}{"" if number == 1 else "s"}'


def report(p: Progress) -> str:
    """Answer to 'how is my GitHub?'"""
    average = f'{p.average:.1f}'.rstrip('0').rstrip('.')
    if p.today == 0:
        today = 'No contributions yet today.'
    else:
        today = f'Today you have {_plural(p.today, "contribution")}.'
    if p.today > p.average and p.today > 0:
        compare = f"That's above your 7-day average of {average}."
    elif p.today == round(p.average) and p.today > 0:
        compare = f"That's right at your 7-day average of {average}."
    elif p.average > 0:
        compare = f"Your 7-day average is {average}, so you're {_plural(p.behind, 'contribution')} behind."
    else:
        compare = 'You had no contributions in the last 7 days, so any push today is a win.'
    streak = (f'Streak: {_plural(p.streak, "day")}.' if p.streak
              else 'No streak right now; one push today starts a new one.')
    if p.this_week > p.last_week:
        trend = f'This week is up on last week ({p.this_week} vs {p.last_week}).'
    elif p.this_week < p.last_week:
        trend = f'This week is behind last week ({p.this_week} vs {p.last_week}).'
    else:
        trend = f'This week matches last week ({p.this_week}).'
    return ' '.join((today, compare, streak, trend))


def afternoon_nudge(p: Progress) -> str | None:
    if p.behind <= 0:
        return None
    average = f'{p.average:.1f}'.rstrip('0').rstrip('.')
    return (f"GitHub check: {_plural(p.today, 'contribution')} today; your 7-day average is {average}, "
            f"so you're {p.behind} behind. A small commit counts.")


def evening_nudge(p: Progress) -> str | None:
    if p.today > 0:
        return None
    if p.streak > 0:
        return f'No contributions yet today. One push keeps your {p.streak}-day streak alive.'
    return 'No contributions yet today. One push starts a new streak.'


class GitHubCoach:
    """Afternoon and evening reminders (each at most once a day) and answers to GitHub questions."""

    def __init__(self, username: str, *, announce, afternoon_hour: int = 15, evening_hour: int = 20,
                 fetcher=fetch_calendar, now=datetime.now, clock=time.monotonic, cache_seconds: float = 1800.0):
        self._username, self._announce, self._fetch = username, announce, fetcher
        self._afternoon, self._evening = afternoon_hour, evening_hour
        self._now, self._clock, self._cache_seconds = now, clock, cache_seconds
        self._lock = threading.Lock()
        self._cache: tuple[float, dict[date, int]] | None = None
        self._done: set[tuple[str, str]] = set()        # (date, 'afternoon' | 'evening')
        self._running = False

    def calendar(self) -> dict[date, int]:
        with self._lock:
            cached = self._cache
        if cached and self._clock() - cached[0] < self._cache_seconds:
            return cached[1]
        result = self._fetch(self._username)
        with self._lock:
            self._cache = (self._clock(), result)
        return result

    def maybe_remind(self, owner_available: bool) -> None:
        """Cheap; call often. Starts at most one background check at each reminder hour."""
        current = self._now()
        today = current.date().isoformat()
        due = None
        if current.hour >= self._evening and (today, 'evening') not in self._done:
            due = 'evening'
        elif self._afternoon <= current.hour < self._evening and (today, 'afternoon') not in self._done:
            due = 'afternoon'
        if due is None or not owner_available or self._running:
            return
        self._done.add((today, due))
        if due == 'evening':
            self._done.add((today, 'afternoon'))      # never say both back to back
        self._running = True
        threading.Thread(target=self._remind, args=(due,), name='vision-github-coach', daemon=True).start()

    def _remind(self, which: str) -> None:
        try:
            p = progress(self.calendar(), self._now().date())
            text = evening_nudge(p) if which == 'evening' else afternoon_nudge(p)
            if text:
                self._announce(text)
        except Exception as exc:
            LOG.info('GitHub check skipped (%s)', type(exc).__name__)
        finally:
            self._running = False

    def intercept(self, text: str) -> str | None:
        if not _QUESTION.search(text):
            return None
        try:
            return report(progress(self.calendar(), self._now().date()))
        except Exception as exc:
            LOG.info('GitHub activity unavailable (%s)', type(exc).__name__)
            return 'I could not read your GitHub activity right now. Try again in a bit.'
