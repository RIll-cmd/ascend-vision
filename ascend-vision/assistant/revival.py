"""Dormant work revival: once a week Vision brings back one piece of left-behind work.

It looks at unmerged local branches, Agent Room sessions that were stopped or left at a gate, and GitHub issues
the owner opened that nobody touched, then asks: revive it, later, or forget it. Git, GitHub and the Room are
only read, never changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading

from assistant.answers import words

LOG = logging.getLogger(__name__)
BRANCH_DAYS, ROOM_DAYS, ISSUE_DAYS, MAX_DAYS = 14, 3, 30, 365
ASK_EVERY = timedelta(days=7)
POSTPONE = timedelta(days=30)
SCAN_FRESH = timedelta(hours=1)
ASK_FROM, ASK_UNTIL = 10, 20
MAX_REPOS = 20
COUNTS = ('No', 'One', 'Two', 'Three')
_LEAD = r"(?:(?:hey|ok|okay) )?(?:vision )?"
_REVIVE = re.compile(rf"{_LEAD}(?:yes )?(?:revive(?: it)?|bring it back)")
_LATER = re.compile(rf"{_LEAD}(?:later|maybe later|not now|next month|no|nope|not yet)")  # a bare no only postpones
_FORGET = re.compile(rf"{_LEAD}(?:forget(?: about)? it|forget it for good)")
_LIST = re.compile(rf"{_LEAD}(?:what did i leave(?: behind)?|what have i left(?: behind)?|anything unfinished"
                   r"|what's unfinished)")


@dataclass(frozen=True)
class Leftover:
    key: str
    kind: str  # branch | room | issue
    label: str  # how Vision says it
    last: datetime
    detail: str


def _spoken(name: str) -> str:
    return ' '.join(re.split(r'[/_\-]+', name)).strip()


def _local(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)


def _age(last: datetime, now: datetime) -> str:
    days = (now - last).days
    if days <= 0:
        return 'today'
    if days == 1:
        return 'yesterday'
    if days < 14:
        return f'{days} days ago'
    if days < 60:
        return f'{days // 7} weeks ago'
    return f'{days // 30} months ago'


def left_branches(repo: Path, run_git, now: datetime) -> list[Leftover]:
    head = (run_git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD') or '').strip()
    names = ([head.split('/', 1)[1]] if head.startswith('origin/') else []) + ['main', 'master']
    default = next((b for b in names if run_git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{b}')), None)
    if default is None:
        return []
    listing = run_git(repo, 'for-each-ref', 'refs/heads', '--format=%(refname:short)%09%(committerdate:unix)') or ''
    found = []
    for line in listing.splitlines():
        name, _, stamp = line.partition('\t')
        if name == default or not stamp.strip().isdigit():
            continue
        last = datetime.fromtimestamp(int(stamp))
        if not BRANCH_DAYS <= (now - last).days <= MAX_DAYS:
            continue
        # Commits that reached the default branch by rebase or cherry-pick are not "left"; squash merges still are.
        ahead = (run_git(repo, 'rev-list', '--count', '--cherry-pick', '--right-only', '--no-merges',
                         f'{default}...{name}', '--') or '').strip()
        if not ahead.isdigit() or int(ahead) == 0:
            continue
        count = int(ahead)
        found.append(Leftover(f'branch:{repo.as_posix()}:{name}', 'branch',
                              f'branch {_spoken(name)} in {_spoken(repo.name)}', last,
                              f"{count} commit{'' if count == 1 else 's'} not merged"))
    return found


def left_room_sessions(payload, now: datetime) -> list[Leftover]:
    sessions = payload.get('sessions') if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return []
    found = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        stage, title = str(session.get('stage') or ''), str(session.get('title') or '').strip()
        if not title or not (stage == 'stopped' or stage.startswith('gate-')):
            continue
        try:
            last = _local(str(session.get('createdAt')))
        except ValueError:
            continue
        if not ROOM_DAYS <= (now - last).days <= MAX_DAYS:
            continue
        detail = 'You stopped it' if stage == 'stopped' else f"It is waiting for you at the {stage[5:]} gate"
        found.append(Leftover(f"room:{session.get('id')}", 'room', f'the Agent Room session {title[:80]}', last,
                              detail))
    return found


def left_issues(repo: Path, run_gh, now: datetime) -> list[Leftover]:
    answer = run_gh(repo, 'issue', 'list', '--state', 'open', '--author', '@me', '--limit', '30',
                    '--json', 'number,title,updatedAt')
    try:
        issues = json.loads(answer) if answer else []
    except ValueError:
        return []
    found = []
    for issue in issues if isinstance(issues, list) else []:
        try:
            number, title, last = int(issue['number']), str(issue['title'])[:80], _local(issue['updatedAt'])
        except (KeyError, TypeError, ValueError):
            continue
        if ISSUE_DAYS <= (now - last).days <= MAX_DAYS:
            found.append(Leftover(f'issue:{repo.as_posix()}:{number}', 'issue',
                                  f'issue {number} in {_spoken(repo.name)}, {title}', last, 'It is still open'))
    return found


def _plain(text: str) -> str:
    return ' ' + ' '.join(re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).split()) + ' '


def named_repo(text: str, dirs):
    """The first repo whose folder name ('ascend_hub' or 'ascend hub') appears in the text, or None."""
    said = _plain(text)
    for folder in dirs:
        name = _plain(re.split(r'[\\/]', str(folder).rstrip('\\/'))[-1])
        if name.strip() and name in said:
            return folder
    return None


def default_repos(vision_repo: Path) -> list[Path]:
    """Git repos one level under the folder that holds Vision's own repo."""
    def is_repo(folder: Path) -> bool:
        try:  # a protected folder such as $RECYCLE.BIN raises; it is simply not a repo
            return folder.is_dir() and (folder / '.git').exists()
        except OSError:
            return False
    try:
        return [p for p in sorted(Path(vision_repo).parent.iterdir()) if is_repo(p)][:MAX_REPOS]
    except OSError:
        return []


def scan(repos, *, run_git, run_gh, room, now: datetime) -> list[Leftover]:
    found = []
    for repo in repos:
        for finder, runner in ((left_branches, run_git), (left_issues, run_gh)):
            try:
                found += finder(Path(repo), runner, now)
            except Exception as exc:  # one broken repo or a missing gh must not stop the rest
                LOG.debug('Revival skipped %s (%s)', finder.__name__, type(exc).__name__)
    try:
        found += left_room_sessions(room(), now)
    except Exception as exc:
        LOG.debug('Revival Room read skipped (%s)', type(exc).__name__)
    return sorted(found, key=lambda item: item.last, reverse=True)


def command_runner(program: str, timeout: float = 15.0):
    """``run(repo, *args)`` → stdout, or None when the program fails, is missing or is too slow."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    def run(repo: Path, *args: str) -> str | None:
        try:
            done = subprocess.run([program, *args], cwd=repo, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', timeout=timeout, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None
    return run


class RevivalStore:
    """``revival.json``: when Vision last asked, and which leftovers are postponed, revived or forgotten."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('marks', {}), dict):
                raise ValueError('not a revival file')
            return {'last_asked': data.get('last_asked'), 'marks': data.get('marks', {})}
        except FileNotFoundError:
            return {'last_asked': None, 'marks': {}}
        except ValueError as exc:
            LOG.warning('Revival file unreadable (%s); starting fresh', type(exc).__name__)
            os.replace(self.path, self.path.with_name(self.path.name + '.bad'))
            return {'last_asked': None, 'marks': {}}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps(data, indent=1), encoding='utf-8')
        os.replace(temporary, self.path)


class RevivalCoach:
    def __init__(self, store: RevivalStore, *, scan, announce, promise=None, clock=datetime.now, run=None,
                 offer_window: float = 120.0):
        self.store, self.scan, self.announce, self.promise, self.clock = store, scan, announce, promise, clock
        self.run = run or (lambda job: threading.Thread(target=job, name='revival-scan', daemon=True).start())
        self.offer_window = offer_window
        self._lock = threading.Lock()
        self._data = store.load()
        self._found: list[Leftover] | None = None
        self._found_at: datetime | None = None
        self._scanning = self._unspoken = False
        self._retry_after: datetime | None = None
        self._offer: tuple[Leftover, datetime, int | None] | None = None

    def _save(self) -> None:
        try:
            self.store.save(self._data)
        except OSError as exc:
            LOG.warning('Revival state not saved (%s)', type(exc).__name__)

    def _start_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True

        def job() -> None:
            try:
                found = sorted(self.scan(), key=lambda item: item.last, reverse=True)  # newest first
            except Exception as exc:
                LOG.debug('Revival scan failed (%s)', type(exc).__name__)
                found = []
            with self._lock:
                self._found, self._found_at, self._scanning = found, self.clock(), False
        # The lock is not held here, so a synchronous runner (tests) can finish the job at once.
        self.run(job)

    def _open_offer(self, now: datetime):
        offer = self._offer
        if (offer and (now - offer[1]).total_seconds() <= self.offer_window
                and offer[2] == getattr(self.announce, 'question_seq', None)):
            return offer
        return None

    def _waiting(self, item: Leftover, now: datetime) -> bool:
        mark = self._data['marks'].get(item.key)
        if not isinstance(mark, dict):  # a hand-edited entry of the wrong shape counts as no mark
            return False
        if mark.get('status') == 'forgotten':
            return True
        try:
            return now < datetime.fromisoformat(mark.get('until') or '')
        except (TypeError, ValueError):
            return False

    def _stale(self, now: datetime) -> bool:
        return self._found is None or self._found_at is None or now - self._found_at > SCAN_FRESH

    def tick(self, owner_available: bool) -> None:
        now = self.clock()
        if not owner_available or not ASK_FROM <= now.hour < ASK_UNTIL or self._unspoken or self._open_offer(now):
            return
        if self._retry_after and now < self._retry_after:
            return
        last = self._data.get('last_asked')
        try:
            if last and now - datetime.fromisoformat(last) < ASK_EVERY:
                return
        except (TypeError, ValueError):
            pass
        if self._stale(now):
            self._found = None
            self._start_scan()
        with self._lock:
            found = self._found
            if found is None:
                return
            item = next((i for i in found if not self._waiting(i, now)), None)
            if item is None:
                # Nothing to offer (or every source failed): look again tomorrow, the week is not used up.
                self._retry_after = now + timedelta(days=1)
                return
            self._data['last_asked'] = now.isoformat()
            self._save()
            self._unspoken = True
        self.announce(f'Something you left: {item.label}, last touched {_age(item.last, now)}. {item.detail}. '
                      'Revive it, later, or forget it?', on_said=self._opened(item))

    def _opened(self, item: Leftover):
        def opened() -> None:
            with self._lock:
                self._unspoken = False
                self._offer = (item, self.clock(), getattr(self.announce, 'question_seq', None))
        return opened

    def intercept(self, text: str) -> str | None:
        said, now = words(text), self.clock()
        if _LIST.fullmatch(said):
            if self._stale(now):
                self._start_scan()
            with self._lock:
                found = self._found
                if found is None or self._scanning:
                    return 'Let me look. Ask me again in a minute.'
                return self._list(found, now)
        with self._lock:
            offer = self._open_offer(now)
            if not offer:
                return None
            item = offer[0]
            if _REVIVE.fullmatch(said):
                self._mark(item, 'revived', now + POSTPONE)
                if self.promise is not None:
                    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).replace(hour=10)
                    self.promise(f'get back to {item.label}', tomorrow)
                    return "Okay. I'll check on it tomorrow."
                return "Okay. It's yours again."
            if _LATER.fullmatch(said):
                self._mark(item, 'later', now + POSTPONE)
                return "Okay. I'll bring it up again next month."
            if _FORGET.fullmatch(said):
                self._mark(item, 'forgotten', None)
                return "Okay. I won't bring it up again."
            return None

    def _mark(self, item: Leftover, status: str, until: datetime | None) -> None:
        self._offer = None
        self._data['marks'][item.key] = {'status': status, 'until': until.isoformat() if until else None}
        self._save()

    def _list(self, found: list[Leftover], now: datetime) -> str:
        items = [i for i in found if not self._waiting(i, now)][:3]
        if not items:
            return 'Nothing left behind that I can see.'
        things = '; '.join(f'{i.label}, {_age(i.last, now)}' for i in items)
        return f"{COUNTS[len(items)]} thing{'' if len(items) == 1 else 's'}: {things}."
