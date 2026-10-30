"""Prompt coach: grades the owner's Agent Room task prompts by how the sessions went, and coaches once a week.

It reads the Room's own session files (read-only), splits the last 30 days into smooth and rough sessions, and
says which prompt trait the smooth ones had more often, with one tip. No AI is involved.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re

from assistant.answers import words

LOG = logging.getLogger(__name__)
SESSION_ID = re.compile(r'^\d+-[0-9a-f]{6}$')
REVISION = 'Owner asked for changes to the'
WINDOW = timedelta(days=30)
MAX_SESSIONS = 200
MIN_SESSIONS = 6
MIN_GAP = 0.3
SPEAK_EVERY = timedelta(days=7)
SPEAK_FROM, SPEAK_UNTIL = 10, 20
TRAITS = {  # name: (how it reads in the coaching, the tip)
    'files': ('named the files', 'Next time, name the files to change.'),
    'done_when': ('said what done looks like', "Next time, say what done looks like, like 'the tests pass'."),
    'check': ('had a check command', 'Next time, give the session a check command, like npm test.'),
    'steps': ('listed the steps', 'Next time, list the steps you expect, one per line.'),
    'example': ('gave an example', 'Next time, give one example of the result you want.'),
}
# Any file name (ok.txt, data/numbers.csv, @app/page.tsx), but not a web address or a version number.
_FILES = re.compile(r'@[\w./-]+|\b[\w-]{2,}\.(?!(?:com|org|net|io|dev|co|ai|gov|edu|app)\b)[a-z][a-z0-9]{0,4}\b'
                    r'|\b(?:src|app|tests?|scripts|docs|lib|components|data)/', re.I)
_DONE_WHEN = re.compile(r'\b(?:should|must|so that|done when|expected|acceptance|tests? pass(?:es)?'
                        r'|make sure)\b', re.I)
_STEPS = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s+\S', re.M)
_EXAMPLE = re.compile(r'\b(?:for example|e\.g\.|such as|for instance)', re.I)
FINISHED = {'done', 'rejected', 'stopped'}
# How each kind of rework reads in "the roughest was ...": (one, many)
REWORK_WORDS = {'plan revisions': ('a plan revision', 'plan revisions'),
                'review rounds': ('an extra review round', 'extra review rounds'),
                'retries': ('a retry', 'retries'), 'failed checks': ('a failed check', 'failed checks'),
                'rejected results': ('a rejected result', 'rejected results'),
                'stopped sessions': ('stopping it partway', 'stopping it partway')}
_ASK = re.compile(r"(?:(?:hey|ok|okay) )?(?:vision )?(?:how are my prompts(?: doing)?|coach my prompts|prompt tips"
                  r"|grade my prompts)")


@dataclass
class Grade:
    title: str
    smooth: bool
    rework: int
    reasons: Counter = field(default_factory=Counter)
    traits: dict = field(default_factory=dict)


def _local(stamp) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp).replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
    except (ValueError, OSError, OverflowError):  # year 1 raises OSError on Windows
        return None


def _transcript_counts(path: Path) -> tuple[int, bool]:
    """(owner revisions, any task assigned), reading line by line; bad lines are skipped."""
    revisions, assigned = 0, False
    try:
        with open(path, encoding='utf-8', errors='replace') as lines:
            for line in lines:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get('from') == 'room' and str(message.get('text', '')).startswith(REVISION):
                    revisions += 1
                assigned = assigned or message.get('kind') == 'assign'
    except OSError:
        pass
    return revisions, assigned


def _tasks(state: dict) -> list[dict]:
    tasks = (state.get('plan') or {}).get('tasks') if isinstance(state.get('plan'), dict) else None
    return [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []


def read_sessions(data_dir: Path, now: datetime) -> list[dict]:
    """The last 30 days of counted sessions: {'state', 'revisions'}. Stopped-before-work sessions are left out."""
    root = Path(data_dir) / 'sessions'
    try:
        ids = sorted((p.name for p in root.iterdir() if SESSION_ID.match(p.name)), reverse=True)[:MAX_SESSIONS]
    except OSError:
        return []
    found = []
    for session_id in ids:
        try:
            state = json.loads((root / session_id / 'state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        # Only finished build sessions: a running one has no outcome yet, and a debate has no task prompt.
        if not isinstance(state, dict) or state.get('stage') not in FINISHED or state.get('mode') == 'debate':
            continue
        created = _local(state.get('createdAt'))
        if created is None or not now - WINDOW <= created <= now + timedelta(hours=1):
            continue
        revisions, assigned = _transcript_counts(root / session_id / 'transcript.jsonl')
        if state.get('stage') == 'stopped':
            started = any(t.get('status') != 'pending' or _number(t.get('rounds')) > 0 or _number(t.get('attempts')) > 0
                          or t.get('failures') for t in _tasks(state))
            if not (started or assigned):
                continue
        found.append({'state': state, 'revisions': revisions})
    return found


def traits(task: str, check) -> dict[str, bool]:
    text = task or ''
    return {'files': bool(_FILES.search(text)), 'done_when': bool(_DONE_WHEN.search(text)),
            'check': bool(isinstance(check, str) and check.strip()), 'steps': bool(_STEPS.search(text)),
            'example': bool(_EXAMPLE.search(text))}


def _number(value) -> int:
    return value if type(value) is int and value > 0 else 0


def grade(session: dict) -> Grade:
    state = session['state']
    reasons = Counter()
    reasons['plan revisions'] += session.get('revisions', 0)
    for task in _tasks(state):
        reasons['review rounds'] += max(0, _number(task.get('rounds')) - 1)
        retries = _number(task.get('attempts'))
        reasons['retries'] += retries
        check = task.get('check')
        # A failed check that was retried is already a retry; only a check still red at the end counts again.
        reasons['failed checks'] += 1 if isinstance(check, dict) and check.get('ok') is False and not retries else 0
    stage = state.get('stage')
    if stage == 'rejected':
        reasons['rejected results'] += 1
    elif stage == 'stopped':
        reasons['stopped sessions'] += 1
    reasons = +reasons  # drop zeros
    rework = sum(reasons.values())
    text = str(state.get('task') or state.get('question') or '')
    return Grade(title=_title(text), smooth=stage == 'done' and not rework,
                 rework=rework, reasons=reasons, traits=traits(text, state.get('check')))


def _title(text: str, limit: int = 64) -> str:
    first = text.split('\n')[0].strip()
    if len(first) > limit:
        first = first[:limit].rsplit(' ', 1)[0].rstrip(',;:')
    return first or 'an untitled session'


def _rework_words(reasons: Counter) -> str:
    parts = [REWORK_WORDS[name][0] if n == 1 or name == 'stopped sessions' else f'{n} {REWORK_WORDS[name][1]}'
             for name, n in reasons.most_common() if name in REWORK_WORDS]
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def coaching(grades: list[Grade]) -> list[str]:
    smooth = [g for g in grades if g.smooth]
    rough = [g for g in grades if not g.smooth]
    if len(grades) < MIN_SESSIONS:
        return []
    if not rough:
        return [f'All {len(grades)} went smoothly. Keep writing them like that.']
    if not smooth:
        return []  # nothing smooth to learn from
    # "mostly" names the kind of rework the most rough sessions had, not the biggest weighted sum.
    reasons = Counter(name for g in rough for name in g.reasons) or Counter({'rework': 1})
    lines = [f'Your Agent Room prompts, last 30 days: {len(grades)} sessions; {len(smooth)} went smoothly and '
             f'{len(rough)} needed rework, mostly {reasons.most_common(1)[0][0]}.']
    best, gap = None, MIN_GAP
    for name in TRAITS:
        have = sum(g.traits[name] for g in smooth), sum(g.traits[name] for g in rough)
        difference = round(have[0] / len(smooth) - have[1] / len(rough), 6)  # 0.7 - 0.4 must reach 0.3
        if difference >= gap and (best is None or difference > gap):
            best, gap, counts = name, difference, have
    if best is not None:
        lines.append(f'The smooth ones more often {TRAITS[best][0]}: {counts[0]} of {len(smooth)}, '
                     f'against {counts[1]} of {len(rough)}.')
        lines.append(TRAITS[best][1])
        return lines
    roughest = max(rough, key=lambda g: g.rework)
    lines.append(f"The roughest was '{roughest.title}': {_rework_words(roughest.reasons)}.")
    missing = next((name for name in TRAITS if not roughest.traits[name]), None)
    if missing:
        lines.append(TRAITS[missing][1])
    return lines


class PromptCoach:
    def __init__(self, read, *, announce, store_path: Path, clock=datetime.now):
        self.read, self.announce, self.store_path, self.clock = read, announce, Path(store_path), clock
        self._retry_after: datetime | None = None

    def _last_spoken(self) -> datetime | None:
        try:
            return datetime.fromisoformat(json.loads(self.store_path.read_text(encoding='utf-8'))['last_spoken'])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _remember(self, now: datetime) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.store_path.with_name(self.store_path.name + '.tmp')
            temporary.write_text(json.dumps({'last_spoken': now.isoformat()}), encoding='utf-8')
            os.replace(temporary, self.store_path)
        except OSError as exc:
            LOG.warning('Prompt coach state not saved (%s)', type(exc).__name__)

    def tick(self, owner_available: bool) -> None:
        now = self.clock()
        if not owner_available or not SPEAK_FROM <= now.hour < SPEAK_UNTIL:
            return
        if self._retry_after and now < self._retry_after:
            return
        last = self._last_spoken()
        if last and now - last < SPEAK_EVERY:
            self._retry_after = last + SPEAK_EVERY  # no file reads every 5 s until then
            return
        try:
            lines = coaching(self.read())
        except Exception as exc:  # a bad session file must not mean a re-read every 5 s
            LOG.debug('Prompt coaching read failed (%s)', type(exc).__name__)
            self._retry_after = now + timedelta(hours=1)
            return
        if not lines:
            self._retry_after = now + timedelta(days=1)
            return
        self._remember(now)
        self.announce(' '.join(lines))

    def intercept(self, text: str) -> str | None:
        if not _ASK.fullmatch(words(text)):
            return None
        lines = coaching(self.read())
        return ' '.join(lines) if lines else 'Not enough Agent Room sessions in the last 30 days to compare yet.'
