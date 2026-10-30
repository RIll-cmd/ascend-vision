"""Commitment chaser: catches "remind me to X tomorrow" and "I'll X tomorrow", then checks on X when it is due.

The check-ins climb gently: a question, a firmer question the next day, then keep, make it smaller, or drop it.
Promises live in a small JSON file next to Vision's other data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading

from assistant.answers import words

LOG = logging.getLogger(__name__)
WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
PART_HOURS = {'morning': 10, 'afternoon': 15, 'evening': 19, 'night': 20}
_CLOCK = r"\d{1,2}(?::\d{2})? ?(?:am|pm)?"
_DAY_CLOCK = re.compile(rf"(today|tonight|tomorrow) at ({_CLOCK})|at ({_CLOCK}) (today|tonight|tomorrow)")
# Longer forms first, so "at 10am tomorrow" is not cut to "at 10am".
_WHEN = (rf"(?P<when>(?:today|tonight|tomorrow) at {_CLOCK}|at {_CLOCK} (?:today|tonight|tomorrow)"
         r"|tomorrow(?: (?:morning|afternoon|evening|night))?|today|tonight|this (?:morning|afternoon|evening)"
         rf"|(?:on|by) (?:{'|'.join(WEEKDAYS)})|next week"
         r"|in (?:a|an|one|\d+) (?:minutes?|hours?|days?)"
         rf"|at {_CLOCK})")
_EXPLICIT_AFTER = re.compile(rf"\bremind me (?:to|about) (?P<what>.+?) {_WHEN}$")
_EXPLICIT_BEFORE = re.compile(rf"\bremind me {_WHEN} (?:to|about) (?P<what>.+)$")
_IMPLICIT = re.compile(rf"\b(?:i'll|i will|i'm going to|i am going to) (?P<what>.+?) {_WHEN}(?= |$)")
# Plans that are not tasks to chase ("I'll be late today", "I'll see you tomorrow").
_NOT_A_TASK = re.compile(r"(?:be|have|never|not|probably|maybe|try|go to (?:bed|sleep)|head to bed|see you"
                         r"|talk to you|call you|text you|message you|get back to you|let you know|ping you)\b")
MAX_AHEAD = {'minutes': 7 * 24 * 60, 'hours': 30 * 24, 'days': 365}


@dataclass(frozen=True)
class Caught:
    what: str
    due: datetime
    timed: bool  # a clock time or a duration, asked whenever due; date-only promises wait for daytime
    explicit: bool
    when: str


def _clean(sentence: str) -> str:
    return ' '.join(re.sub(r"[^a-z0-9': ]", ' ', sentence.lower()).split())


def _clock(text: str, day: date, now: datetime, *, roll: bool, evening: bool = False) -> datetime:
    """``H[:MM][am|pm]`` on ``day``. Without am/pm: with ``roll`` the next such time from now, else 1 to 6 (or any
    hour ``evening``) mean pm. With ``roll`` a time already gone moves forward. Impossible times raise ValueError."""
    hour, minute, half = re.fullmatch(r"(\d{1,2})(?::(\d{2}))? ?(am|pm)?", text).groups()
    hour, minute = int(hour), int(minute or 0)
    if minute > 59 or hour > (12 if half else 23) or (half and hour == 0):
        raise ValueError(f'no such time: {text}')
    if half == 'pm' and hour < 12:
        hour += 12
    elif half == 'am' and hour == 12:
        hour = 0
    elif half is None and hour < 12 and (evening or (not roll and 1 <= hour <= 6)):
        hour += 12
    at = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    if roll and at <= now:
        at += timedelta(hours=12) if half is None and hour < 12 and at + timedelta(hours=12) > now else timedelta(days=1)
    return at


def _due(when: str, now: datetime) -> tuple[datetime, bool]:
    today = now.date()
    # Before 04:00 "tomorrow" still means the coming day.
    tomorrow = today if now.hour < 4 else today + timedelta(days=1)

    def at_hour(day: date, hour: int) -> datetime:
        return datetime.combine(day, datetime.min.time()).replace(hour=hour)

    paired = _DAY_CLOCK.fullmatch(when)
    if paired:
        day_word, clock = paired[1] or paired[4], paired[2] or paired[3]
        if day_word == 'tomorrow':
            return _clock(clock, tomorrow, now, roll=False), True
        return _clock(clock, today, now, roll=True, evening=day_word == 'tonight'), True
    if when in ('today', 'tonight') or when.startswith('this '):
        hour = {'today': 17, 'tonight': 20}.get(when) or PART_HOURS[when.split()[1]]
        due = at_hour(today, hour)
        return (due if due > now else now + timedelta(hours=1)), False
    if when.startswith('tomorrow'):
        parts = when.split()
        return at_hour(tomorrow, PART_HOURS[parts[1]] if len(parts) > 1 else 10), False
    if when.startswith(('on ', 'by ')):
        ahead = (WEEKDAYS.index(when.split()[1]) - today.weekday()) % 7 or 7
        return at_hour(today + timedelta(days=ahead), 10), False
    if when == 'next week':
        return at_hour(today + timedelta(days=7 - today.weekday()), 10), False
    if when.startswith('in '):
        _, count, unit = when.split()
        amount, unit = (1 if count in ('a', 'an', 'one') else int(count)), unit.rstrip('s') + 's'
        if amount > MAX_AHEAD[unit]:
            raise ValueError(f'too far ahead: {when}')
        return now + timedelta(**{unit: amount}), True
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
            if not 3 <= len(what) <= 120 or (not explicit and (len(what.split()) < 2 or _NOT_A_TASK.match(what))):
                continue
            try:
                due, timed = _due(found['when'], now)
            except ValueError:
                continue
            return Caught(what=what, due=due, timed=timed, explicit=explicit, when=found['when'])
    return None


KEEP_CLOSED_DAYS = 30
MAX_ASKS = 5
SPEAK_FROM, SPEAK_UNTIL = 8, 22
COUNTS = ('No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten')
_STOP = {'the', 'a', 'an', 'my', 'to', 'with', 'on', 'of', 'for', 'and', 'it', 'i', "i'm", 'im', 'am', 'done'}
# Answers count only as the whole message; anything longer is chat for the AI.
_LEAD = r"(?:(?:hey|ok|okay) )?(?:vision )?"
_FINISHED = r"(?:i did(?: it)?|did it|done|all done|finished|i finished(?: it)?|it's done)"
_DONE = re.compile(rf"{_LEAD}(?:(?:yes|yeah|yep|yup)(?: {_FINISHED})?|(?:ok|okay|sure) {_FINISHED}|{_FINISHED})")
DROP_THAT_SECONDS = 180  # the note can wait one pacer gap before it is heard
_NO = re.compile(rf"{_LEAD}(?:no|nope|nah|not yet|no not yet|nope not yet|not now|not today|no thanks|not really)")
_KEEP = re.compile(rf"{_LEAD}keep(?: it)?(?: please| on the list)?")
_DROP = re.compile(rf"{_LEAD}(?:drop(?: it)?|forget(?: about)? it|cancel(?: it)?)")
_SMALLER = re.compile(rf"{_LEAD}(?:(?:make it )?smaller|make it small|split it)(?: please)?")
_DROP_THAT = re.compile(rf"{_LEAD}(?:drop|forget) that")
_DONE_WITH = re.compile(rf"{_LEAD}(?:i'm |im |i am )?(?:done with|cross off|crossed off) (?P<what>(?:\S+ ?){{1,6}})")
_LIST = re.compile(rf"{_LEAD}(?:what did i promise|what have i promised|what are my promises|my promises"
                   r"|list my promises)")
_QUESTION = re.compile(r"(?:what|why|how|who|when|where|which|can|could|would|should|is|are|do|does|did|will)\b")
_QUEUE = re.compile(rf"{_LEAD}(?:(?:queue|send|give) it(?: (?:in|to) the (?:agent )?room)?(?: please)?|room)")
_STEP_LEAD = re.compile(r"^(?:the first (?:small )?step is |first step is |first |just |i'll |i will )+")


@dataclass
class Promise:
    id: str
    what: str
    created: str
    next: str
    timed: bool
    asks: int = 0
    status: str = 'open'  # open | stale | done | dropped | queued
    closed: str | None = None


class PromiseStore:
    """``promises.json``: open and quiet promises, and closed ones for 30 days. A broken file is set aside."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[Promise]:
        try:
            items = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(items, list):
                raise ValueError('not a list')
        except FileNotFoundError:
            return []
        except ValueError as exc:
            LOG.warning('Promises file unreadable (%s); starting fresh', type(exc).__name__)
            os.replace(self.path, self.path.with_name(self.path.name + '.bad'))
            return []
        promises = []
        for item in items:
            try:
                promise = Promise(**item)
                for stamp in (promise.created, promise.next, promise.closed):
                    if stamp is not None:
                        datetime.fromisoformat(stamp)
                promises.append(promise)
            except (TypeError, ValueError) as exc:  # A hand-edited entry is skipped, the rest still count.
                LOG.warning('Skipping an unreadable promise (%s)', type(exc).__name__)
        return promises

    def save(self, promises: list[Promise], now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_CLOSED_DAYS)
        promises[:] = [p for p in promises if not p.closed or datetime.fromisoformat(p.closed) >= cutoff]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps([asdict(p) for p in promises], indent=1), encoding='utf-8')
        os.replace(temporary, self.path)


def _at_ten(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=10)


def _clock_words(at: datetime) -> str:
    hour = at.hour % 12 or 12
    return f"{hour}{f':{at.minute:02d}' if at.minute else ''} {'am' if at.hour < 12 else 'pm'}"


def _day_words(day: date, today: date) -> str:
    if day == today:
        return 'today'
    if day == today + timedelta(days=1):
        return 'tomorrow'
    if day == today - timedelta(days=1):
        return 'yesterday'
    return WEEKDAYS[day.weekday()].title() if abs((day - today).days) < 7 else f'{day:%B} {day.day}'


def find_evidence(what: str, commits, sessions) -> str | None:
    """The first commit (repo, subject) or Room session title sharing two key words with the promise."""
    def split(text: str) -> set[str]:
        return {w for w in re.split(r'[^a-z0-9]+', text.lower()) if len(w) >= 3} - _STOP

    def matches(text: str, ignore: frozenset | set = frozenset()) -> bool:
        keys = split(what) - ignore
        return bool(keys) and len(keys & split(text)) >= min(2, len(keys))

    for repo, subject in commits:
        # The repo's own name (often the 'fix(vision):' scope) is on nearly every commit, so it proves nothing.
        if matches(subject, split(repo)):
            return f"the commit '{subject}' in {repo}"
    for title in sessions:
        if matches(title):
            return f"the Room session '{title}'"
    return None


MAX_ROOM_SESSIONS = 40


def _done_room_titles(room_data: Path, since: datetime) -> list[str]:
    """First task lines of finished Room sessions made after ``since`` (local time)."""
    try:
        folders = sorted((p for p in (Path(room_data) / 'sessions').iterdir() if p.is_dir()),
                         key=lambda p: p.name, reverse=True)[:MAX_ROOM_SESSIONS]
    except OSError:
        return []
    titles = []
    for folder in folders:
        try:
            state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
            made = datetime.fromisoformat(str(state['createdAt']).replace('Z', '+00:00'))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        made = made.astimezone().replace(tzinfo=None) if made.tzinfo else made
        if state.get('stage') == 'done' and made >= since and isinstance(state.get('task'), str):
            titles.append(_short_title(state['task']))
    return titles


def _short_title(task: str, limit: int = 80) -> str:
    """The first sentence of a task, cut at a word so it reads well aloud."""
    first = re.split(r'(?<=[.!?])\s|\n', task.strip(), maxsplit=1)[0].rstrip('.!? ')
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(' ', 1)[0].rstrip(',;: ')


def make_evidence(repos, run_git, room_data: Path):
    """(what, made at) -> a matching commit by the owner or finished Room session since then, or None."""
    def lookup(what: str, since: datetime) -> str | None:
        commits = []
        for repo in repos:
            email = (run_git(repo, 'config', 'user.email') or '').strip()
            args = ['log', '--all', '--no-merges', f'--since={since.isoformat(timespec="seconds")}']
            if email:
                args.append(f'--author={email}')
            out = run_git(repo, *args, '--format=%s', '-n', '50') or ''
            commits += [(Path(repo).name, line.strip()) for line in out.splitlines() if line.strip()]
        return find_evidence(what, commits, _done_room_titles(room_data, since))
    return lookup


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


class PromiseCoach:
    """Catches promises in chat, and asks about one due promise at a time while the owner is around."""

    def __init__(self, store: PromiseStore, *, announce, clock=datetime.now, offer_window: float = 120.0,
                 evidence=None, queue=None):
        self.store = store
        self.evidence = evidence  # (what, made at) -> 'the commit ...' | None, for the first check-in
        self.queue = queue  # what -> (ok, message): hands the promise to the Agent Room
        self.announce = announce
        self.clock = clock
        self.offer_window = offer_window
        self._lock = threading.RLock()
        self._promises = store.load()
        self._asking: set[str] = set()  # queued behind other notices, not yet spoken
        # ('ask' | 'smaller', promise id, spoken at, the pacer's question count then)
        self._offer: tuple[str, str, datetime, int | None] | None = None
        self._captured: tuple[str, datetime] | None = None
        self._queueing: Promise | None = None  # handed to the Room once the lock is let go

    def _open_offer(self, now: datetime):
        """The open question, if it is recent and no other question (a slump check-in) was spoken after it."""
        offer = self._offer
        if (offer and (now - offer[2]).total_seconds() <= self.offer_window
                and offer[3] == getattr(self.announce, 'question_seq', None)):
            return offer
        return None

    def _save(self) -> None:
        try:
            self.store.save(self._promises, self.clock())
        except OSError as exc:
            LOG.warning('Promises not saved (%s)', type(exc).__name__)

    def _find(self, promise_id: str) -> Promise | None:
        return next((p for p in self._promises if p.id == promise_id), None)

    def _close(self, promise: Promise, status: str) -> None:
        promise.status, promise.closed = status, self.clock().isoformat()

    def add(self, what: str, due: datetime) -> bool:
        """A promise another helper made for the owner (a revived leftover); False if it is already open."""
        with self._lock:
            if any(p.status == 'open' and p.what == what for p in self._promises):
                return False
            now = self.clock()
            self._promises.append(Promise(id=secrets.token_hex(4), what=what, created=now.isoformat(),
                                          next=due.isoformat(), timed=False))
            self._save()
            return True

    def tick(self, owner_available: bool) -> None:
        if not owner_available:
            return
        with self._lock:
            now = self.clock()
            if self._open_offer(now):
                return
            # One question at a time, except that a reminder for a clock time may pass an ask held by a focus block.
            due = sorted((p for p in self._promises if p.status == 'open' and datetime.fromisoformat(p.next) <= now
                          and (p.timed or SPEAK_FROM <= now.hour < SPEAK_UNTIL)
                          and p.id not in self._asking and (p.timed or not self._asking)), key=lambda p: p.next)
            if not due:
                return
            promise = due[0]
            if promise.asks >= MAX_ASKS:
                promise.status = 'stale'
                self._save()
                return
            promise.asks += 1
            urgent = promise.timed  # a clock time the owner chose gets past focus mode
            promise.timed = False  # asked again on later days, in daytime only
            promise.next = _at_ten(now.date() + timedelta(days=1)).isoformat()
            created = datetime.fromisoformat(promise.created).date()
            if promise.asks == 1:
                line = None
            elif promise.asks == 2:
                line = f'{_sentence(promise.what)} is still open from {_day_words(created, now.date())}. Done yet?'
            else:
                days = (now.date() - created).days
                line = (f"{_sentence(promise.what)} has been open {'a day' if days == 1 else f'{days} days'}. "
                        + ('Keep it, make it smaller, queue it in the Room, or drop it?' if self.queue
                           else 'Keep it, make it smaller, or drop it?'))
            self._asking.add(promise.id)
            self._save()
        if line is None:
            found = None
            if self.evidence:
                try:  # git and the Room files are read outside the lock
                    found = self.evidence(promise.what, datetime.fromisoformat(promise.created))
                except Exception as exc:
                    LOG.warning('Promise evidence lookup failed (%s)', type(exc).__name__)
            line = f'Checking in on {promise.what}. ' + (f'I saw {found}. ' if found else '') + 'Done?'
        self.announce(line, on_said=self._opened('ask', promise.id), urgent=urgent)

    def _opened(self, kind: str, promise_id: str):
        """The answer window starts when the question is spoken, not when it is queued."""
        def opened() -> None:
            with self._lock:
                self._asking.discard(promise_id)
                self._offer = (kind, promise_id, self.clock(), getattr(self.announce, 'question_seq', None))
        return opened

    def intercept(self, text: str) -> str | None:
        with self._lock:
            reply, notice = self._intercept(text)
            queueing, self._queueing = self._queueing, None
        if queueing is not None:  # the Room and git can take seconds; chat and the check-ins must not wait
            ok, reply = self.queue(queueing.what)
            with self._lock:
                if ok and queueing.status == 'open':
                    self._close(queueing, 'queued')
                    self._save()
        if notice:
            self.announce(notice, urgent=True)  # it answers what the owner just said; no question is opened
        return reply

    def phone(self, text: str) -> str | None:
        """Phone chat: explicit reminders and the list only; nothing is spoken at the PC."""
        said, now = words(text), self.clock()
        with self._lock:
            if _LIST.fullmatch(said):
                return self._list(now)
            caught = parse_promise(text, now)
            if caught is None or not caught.explicit:
                return None
            # Never an answer to a question open at the PC, and the PC's 'drop that' stays as it was.
            if any(p.status == 'open' and p.what == caught.what for p in self._promises):
                return "That's already on your list."
            self._promises.append(Promise(id=secrets.token_hex(4), what=caught.what, created=now.isoformat(),
                                          next=caught.due.isoformat(), timed=caught.timed))
            self._save()
            return f"Okay. I'll check on {caught.what} {caught.when}."

    def _intercept(self, text: str) -> tuple[str | None, str | None]:
        said, now = words(text), self.clock()
        if _LIST.fullmatch(said):
            return self._list(now), None
        if (_DROP_THAT.fullmatch(said) and self._captured
                and (now - self._captured[1]).total_seconds() <= DROP_THAT_SECONDS):
            promise = self._find(self._captured[0])
            self._captured = None
            if promise and promise.status == 'open':
                self._close(promise, 'dropped')
                self._save()
                return 'Dropped.', None
        offer = self._open_offer(now)
        if offer:
            reply = self._answer(offer, text, said, now)
            if reply:
                self._save()
                return reply, None
        found = _DONE_WITH.fullmatch(said)
        if found and '?' not in text:
            promise = self._closest(found['what'])
            if promise:
                self._close(promise, 'done')
                self._save()
                return f'Nice. Crossed off {promise.what}.', None
        caught = parse_promise(text, now)
        if caught is None:
            return None, None
        if any(p.status == 'open' and p.what == caught.what for p in self._promises):
            return ("That's already on your list." if caught.explicit else None), None
        promise = Promise(id=secrets.token_hex(4), what=caught.what, created=now.isoformat(),
                          next=caught.due.isoformat(), timed=caught.timed)
        self._promises.append(promise)
        self._captured = (promise.id, now)
        self._save()
        if caught.explicit:
            return f"Okay. I'll check on {caught.what} {caught.when}.", None
        return None, f"Noted: {caught.what}, {caught.when}. Say 'drop that' if I got it wrong."

    def _answer(self, offer: tuple, text: str, said: str, now: datetime) -> str | None:
        kind, promise_id, _, seq = offer
        promise = self._find(promise_id)
        if promise is None or promise.status != 'open':
            self._offer = None
            return None
        if kind == 'smaller':
            step = _STEP_LEAD.sub('', said).strip()
            if '?' in text or _QUESTION.match(step) or not 3 <= len(step) <= 120 or len(step.split()) > 12:
                return None  # a question for the AI, not the step
            self._offer = None
            self._close(promise, 'dropped')
            self._promises.append(Promise(id=secrets.token_hex(4), what=step, created=now.isoformat(),
                                          next=(now + timedelta(hours=2)).isoformat(), timed=True))
            return f"Okay: {step}. I'll check on it in two hours."
        if _DROP.fullmatch(said):
            self._offer = None
            self._close(promise, 'dropped')
            return 'Dropped.'
        if self.queue and promise.asks >= 3 and _QUEUE.fullmatch(said):  # only once the Room was offered
            self._offer = None
            self._queueing = promise
            return 'queueing'
        if _SMALLER.fullmatch(said):
            self._offer = ('smaller', promise.id, now, seq)
            return "What's the first small step?"
        if _KEEP.fullmatch(said):
            self._offer = None
            promise.next = _at_ten(now.date() + timedelta(days=3)).isoformat()
            return "Okay. I'll ask again in three days."
        if _NO.fullmatch(said):
            self._offer = None
            promise.next = _at_ten(now.date() + timedelta(days=1)).isoformat()
            return "Okay. I'll ask again tomorrow."
        if _DONE.fullmatch(said):
            self._offer = None
            self._close(promise, 'done')
            return 'Nice. Crossed off.'
        return None

    def _closest(self, text: str) -> Promise | None:
        wanted = set(text.split()) - _STOP
        best, score = None, 0
        for promise in self._promises:
            if promise.status in ('open', 'stale'):
                overlap = len(wanted & (set(promise.what.split()) - _STOP))
                if overlap > score:
                    best, score = promise, overlap
        return best

    def _list(self, now: datetime) -> str:
        open_ = sorted((p for p in self._promises if p.status == 'open'), key=lambda p: p.next)
        quiet = [p.what for p in self._promises if p.status == 'stale']
        parts = []
        for promise in open_:
            due = datetime.fromisoformat(promise.next)
            when = _day_words(due.date(), now.date())
            if promise.timed:
                when += f' at {_clock_words(due)}'
            parts.append(f'{promise.what}, due {when}')
        count = COUNTS[len(open_)] if len(open_) < len(COUNTS) else str(len(open_))
        answer = f"{count} open: {'; '.join(parts)}." if parts else 'No open promises.'
        return f"{answer} Gone quiet: {', '.join(quiet)}." if quiet else answer
