"""Dormant work revival: once a week Vision brings back one piece of left-behind work.

It looks at unmerged local branches, Agent Room sessions that were stopped or left at a gate, and GitHub issues
the owner opened that nobody touched, then asks: revive it, later, or forget it. Git, GitHub and the Room are
only read, never changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
_FORGET = re.compile(rf"{_LEAD}(?:forget(?: about)? it|forget it for good|drop it|drop)")
_ARCHIVE = re.compile(rf"{_LEAD}archive(?: it)?(?: please)?")
_SESSION_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
_LIST = re.compile(rf"{_LEAD}(?:what did i leave(?: behind)?|what have i left(?: behind)?|anything unfinished"
                   r"|what's unfinished)")


@dataclass(frozen=True)
class Leftover:
    key: str
    kind: str  # branch | room | issue
    label: str  # how Vision says it
    last: datetime
    detail: str
    repo: str | None = field(default=None, compare=False)
    ref: str | None = field(default=None, compare=False)  # branch name, issue number or worktree folder
    start: dict | None = field(default=None, compare=False)  # an Agent Room start body, when it can restart there


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


def _project(name: str, fallback: str) -> str:
    return '-'.join(re.findall(r'[a-z0-9]+', name.lower()))[:40].strip('-') or fallback


def left_branches(repo: Path, run_git, now: datetime, checked_out=frozenset()) -> list[Leftover]:
    head = (run_git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD') or '').strip()
    names = ([head.split('/', 1)[1]] if head.startswith('origin/') else []) + ['main', 'master']
    default = next((b for b in names if run_git(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{b}')), None)
    if default is None:
        return []
    listing = run_git(repo, 'for-each-ref', 'refs/heads', '--format=%(refname:short)%09%(committerdate:unix)') or ''
    found = []
    for line in listing.splitlines():
        name, _, stamp = line.partition('\t')
        # A branch put away as archive/..., or open in a worktree (that worktree is its own leftover), is skipped.
        if name == default or not stamp.strip().isdigit() or name.startswith('archive/') or name in checked_out:
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
        detail = f"{count} commit{'' if count == 1 else 's'} not merged"
        start = {'template': 'feature', 'project': _project(name, 'branch'), 'repo': str(repo),
                 'task': (f'Finish the work left on branch {name} ({detail}). Read `git log {default}..{name}` and '
                          f'`git diff {default}...{name}` first, then carry that work into this session.')}
        found.append(Leftover(f'branch:{repo.as_posix()}:{name}', 'branch',
                              f'branch {_spoken(name)} in {_spoken(repo.name)}', last, detail,
                              repo=str(repo), ref=name, start=start))
    return found


def _worktrees(repo: Path, run_git) -> list[dict]:
    """``git worktree list --porcelain`` as dicts; the first is the main checkout."""
    listing = run_git(repo, 'worktree', 'list', '--porcelain') or ''
    entries = []
    for block in listing.strip().split('\n\n'):
        entry = {}
        for line in block.splitlines():
            key, _, value = line.partition(' ')
            entry[key] = value.strip()
        if entry.get('worktree'):
            if entry.get('branch', '').startswith('refs/heads/'):
                entry['branch'] = entry['branch'][len('refs/heads/'):]
            entries.append(entry)
    return entries


def left_worktrees(repo: Path, run_git, now: datetime, entries=None) -> list[Leftover]:
    """Extra worktrees not committed to for two weeks, or whose folder is gone. Vision never removes them."""
    found = []
    for entry in (entries if entries is not None else _worktrees(repo, run_git))[1:]:
        folder, branch = Path(entry['worktree']), entry.get('branch')
        gone = 'prunable' in entry
        stamp = (run_git(repo, 'log', '-1', '--format=%ct', entry.get('HEAD', '')) if gone
                 else run_git(folder, 'log', '-1', '--format=%ct')) or ''
        if not stamp.strip().isdigit():
            continue
        last = datetime.fromtimestamp(int(stamp.strip()))
        if not BRANCH_DAYS <= (now - last).days <= MAX_DAYS:
            continue
        if gone:
            detail = 'its folder is gone'
        else:
            changed = len([line for line in (run_git(folder, 'status', '--porcelain') or '').splitlines() if line.strip()])
            detail = (f"{changed} file{'' if changed == 1 else 's'} not committed" if changed
                      else f'on branch {branch}, nothing uncommitted' if branch else 'nothing uncommitted')
        found.append(Leftover(f'worktree:{folder.as_posix()}', 'worktree',
                              f'worktree {folder.name} in {_spoken(repo.name)}', last, detail,
                              repo=str(repo), ref=str(folder)))
    return found


def _room_start(room_data, session_id: str) -> dict | None:
    """A stopped session's own start (task, repo, template, check), from its state.json."""
    if room_data is None or not _SESSION_ID.fullmatch(session_id):
        return None
    try:
        state = json.loads((Path(room_data) / 'sessions' / session_id / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    repo = state.get('repo') if isinstance(state, dict) else None
    task, project = (state.get('task'), state.get('project')) if isinstance(state, dict) else (None, None)
    if not (isinstance(repo, dict) and isinstance(repo.get('dir'), str) and isinstance(task, str) and task.strip()
            and isinstance(project, str) and project):
        return None
    start = {'template': state.get('template') if isinstance(state.get('template'), str) else 'feature',
             'project': project, 'task': task, 'repo': repo['dir']}
    if isinstance(state.get('check'), str) and state['check'].strip():
        start['check'] = state['check']
    return start


def left_room_sessions(payload, now: datetime, room_data=None) -> list[Leftover]:
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
        # A session waiting at a gate is still open in the Room; only a stopped one can be started again.
        start = _room_start(room_data, str(session.get('id'))) if stage == 'stopped' else None
        found.append(Leftover(f"room:{session.get('id')}", 'room', f'the Agent Room session {title[:80]}', last,
                              detail, start=start))
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
                                  f'issue {number} in {_spoken(repo.name)}, {title}', last, 'It is still open',
                                  repo=str(repo), ref=str(number),
                                  start={'template': 'feature', 'project': f'issue-{number}', 'repo': str(repo),
                                         'issue': number}))
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


def scan(repos, *, run_git, run_gh, room, now: datetime, room_data=None) -> list[Leftover]:
    found = []
    for repo in repos:
        repo = Path(repo)
        try:
            trees = _worktrees(repo, run_git)
        except Exception:
            trees = []
        checked_out = {t['branch'] for t in trees[1:] if t.get('branch')}
        for finder in (lambda: left_worktrees(repo, run_git, now, entries=trees),
                       lambda: left_branches(repo, run_git, now, checked_out=checked_out),
                       lambda: left_issues(repo, run_gh, now)):
            try:
                found += finder()
            except Exception as exc:  # one broken repo or a missing gh must not stop the rest
                LOG.debug('Revival skipped a finder (%s)', type(exc).__name__)
    try:
        found += left_room_sessions(room(), now, room_data)
    except Exception as exc:
        LOG.debug('Revival Room read skipped (%s)', type(exc).__name__)
    return sorted(found, key=lambda item: item.last, reverse=True)


def make_archiver(run_git, run_gh):
    """item -> (ok, reply): a branch is renamed to archive/..., an issue closed as not planned; nothing is deleted."""
    def archive(item: Leftover) -> tuple[bool, str]:
        if item.kind == 'branch' and item.repo and item.ref:
            if run_git(item.repo, 'branch', '-m', item.ref, f'archive/{item.ref}') is None:
                return False, "I couldn't rename that branch; it may be checked out somewhere."
            return True, f'Renamed the branch to archive/{item.ref}. Its commits are all still there.'
        if item.kind == 'issue' and item.repo and item.ref:
            if run_gh(item.repo, 'issue', 'close', item.ref, '--reason', 'not planned') is None:
                return False, f"I couldn't close issue {item.ref}."
            return True, f'Closed issue {item.ref} as not planned. You can reopen it on GitHub.'
        return True, "Okay. I'll stop bringing it up; I never delete worktrees or sessions."
    return archive


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
                 offer_window: float = 120.0, queue=None, archive=None):
        self.store, self.scan, self.announce, self.promise, self.clock = store, scan, announce, promise, clock
        self.queue = queue  # start body -> (ok, reply): revive in the Agent Room
        self.archive = archive  # leftover -> (ok, reply)
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
        if mark.get('status') in ('forgotten', 'archived'):
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
                      + ('Revive it, archive it, later, or forget it?' if self.archive else 'Revive it, later, or forget it?'),
                      on_said=self._opened(item))

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
        action = None
        with self._lock:
            offer = self._open_offer(now)
            if not offer:
                return None
            item = offer[0]
            if _REVIVE.fullmatch(said) and self.queue is not None and item.start:
                self._offer, action = None, 'revived'
            elif self.archive is not None and _ARCHIVE.fullmatch(said):
                self._offer, action = None, 'archived'
            elif _REVIVE.fullmatch(said):
                self._mark(item, 'revived', now + POSTPONE)
                if self.promise is not None:
                    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).replace(hour=10)
                    self.promise(f'get back to {item.label}', tomorrow)
                    return "Okay. I'll check on it tomorrow."
                return "Okay. It's yours again."
            elif _LATER.fullmatch(said):
                self._mark(item, 'later', now + POSTPONE)
                return "Okay. I'll bring it up again next month."
            elif _FORGET.fullmatch(said) or _ARCHIVE.fullmatch(said):
                self._mark(item, 'forgotten', None)
                return "Okay. I won't bring it up again."
            else:
                return None
        # The Room, git and gh can take seconds; the lock is not held for them.
        ok, reply = self.queue(item.start) if action == 'revived' else self.archive(item)
        if ok:
            with self._lock:
                self._mark(item, action, now + POSTPONE if action == 'revived' else None)
        return reply

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
