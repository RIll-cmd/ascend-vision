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
_FILES = re.compile(r'@[\w./-]+|\b[\w-]+\.(?:py|js|jsx|ts|tsx|mjs|md|json|html|css|yaml|yml|toml|sql)\b'
                    r'|\b(?:src|app|tests?|scripts|docs|lib|components)/', re.I)
_DONE_WHEN = re.compile(r'\b(?:should|must|so that|done when|expect(?:ed)?|acceptance|tests? pass(?:es)?'
                        r'|make sure|until)\b', re.I)
_STEPS = re.compile(r'^\s*(?:[-*•]|\d+[.)])\s+\S', re.M)
_EXAMPLE = re.compile(r'\b(?:for example|e\.g\.|such as|for instance|looks like)', re.I)
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
    except ValueError:
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
        if not isinstance(state, dict):
            continue
        created = _local(state.get('createdAt'))
        if created is None or not now - WINDOW <= created <= now + timedelta(hours=1):
            continue
        revisions, assigned = _transcript_counts(root / session_id / 'transcript.jsonl')
        if state.get('stage') == 'stopped':
            started = any(t.get('status') != 'pending' or (t.get('rounds') or 0) > 0 or (t.get('attempts') or 0) > 0
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
        reasons['retries'] += _number(task.get('attempts'))
        check = task.get('check')
        failures = task.get('failures')
        reasons['failed checks'] += (len(failures) if isinstance(failures, list) else 0) + (
            1 if isinstance(check, dict) and check.get('ok') is False else 0)
    stage = state.get('stage')
    if stage == 'rejected':
        reasons['rejected results'] += 3
    elif stage == 'stopped':
        reasons['stopped sessions'] += 2
    reasons = +reasons  # drop zeros
    rework = sum(reasons.values())
    text = str(state.get('task') or state.get('question') or '')
    return Grade(title=text.split('\n')[0].strip()[:60] or 'an untitled session', smooth=stage == 'done' and not rework,
                 rework=rework, reasons=reasons, traits=traits(text, state.get('check')))


def coaching(grades: list[Grade]) -> list[str]:
    smooth = [g for g in grades if g.smooth]
    rough = [g for g in grades if not g.smooth]
    if len(grades) < MIN_SESSIONS:
        return []
    if not rough:
        return [f'All {len(grades)} went smoothly. Keep writing them like that.']
    if not smooth:
        smooth = []
    reasons = sum((g.reasons for g in rough), Counter())
    lines = [f'Your Agent Room prompts, last 30 days: {len(grades)} sessions; {len(smooth)} went smoothly and '
             f'{len(rough)} needed rework, mostly {reasons.most_common(1)[0][0]}.']
    best, gap = None, MIN_GAP
    for name in TRAITS:
        have = sum(g.traits[name] for g in smooth), sum(g.traits[name] for g in rough)
        difference = (have[0] / len(smooth) if smooth else 0) - have[1] / len(rough)
        if difference >= gap and (best is None or difference > gap):
            best, gap, counts = name, difference, have
    if best is not None:
        lines.append(f'The smooth ones more often {TRAITS[best][0]}: {counts[0]} of {len(smooth)}, '
                     f'against {counts[1]} of {len(rough)}.')
        lines.append(TRAITS[best][1])
        return lines
    roughest = max(rough, key=lambda g: g.rework)
    lines.append(f"The roughest was '{roughest.title}', with {roughest.rework} "
                 f"round{'' if roughest.rework == 1 else 's'} of rework.")
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
        lines = coaching(self.read())
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
