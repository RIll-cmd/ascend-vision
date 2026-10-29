"""Voice dialog for Ascend Hub's agent room: announce what the agents need, and take the owner's answer.

Pure state machine: it never talks to the network. The caller feeds it session snapshots
(``observe``) and unmatched speech (``handle``), speaks the returned text, and sends the
returned ``RoomAction`` to Hub. Nothing is sent without a spoken yes.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time

SPEECH_LIMIT = 400
NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, '1': 1, '2': 2, '3': 3, '4': 4,
           'first': 1, 'second': 2, 'third': 3, 'fourth': 4}
YES_START = {'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'confirm', 'confirmed'}
YES = {'do it', 'send it', 'go ahead and send it'}
NO_START = {'no', 'nope', 'cancel', 'nevermind'}
NO = {'never mind', 'dont', 'do not', 'forget it'}
NEGATION = {'no', 'not', 'dont', 'never', 'cant', 'wont'}
FILLERS = {'option', 'number', 'choice', 'the', 'please', 'go', 'with', 'pick', 'take', 'lets', 'use', 'i', 'want'}
VERBS = {'approve': ('approve', 'approved', 'looks good', 'go ahead', 'sounds good'),
         'merge': ('merge', 'merge it', 'ship it'),
         'accept': ('accept', 'accepted'),
         'reject': ('reject', 'rejected', 'decline'),
         'retry': ('retry', 'try again'),
         'stop': ('stop', 'end it')}
CHANGE = re.compile(r'^(?:please\s+)?(?:change|revise|edit)\b(?:\s+(?:it|the\s+(?:plan|roles)))?(?:\s+(?:to|so that|so))?\s*(?P<rest>.*)$')
# Hub's own phone answers never merge or reject either; those stay at the computer.
REMOTE_BLOCKED = {'merge', 'reject'}


@dataclass(frozen=True)
class RoomAction:
    kind: str  # 'gate' or 'resume'
    gate: str | None
    decision: str
    note: str | None = None


@dataclass(frozen=True)
class RoomReply:
    text: str
    action: RoomAction | None = None


def _words(text: str) -> str:
    return ' '.join(re.sub(r"[^a-z0-9' ]+", ' ', text.casefold()).replace("'", '').split())


def _yes(said: str) -> bool:
    words = said.split()
    return said in YES or (words[0] in YES_START and not NEGATION & set(words[1:]))


def _no(said: str) -> bool:
    return said in NO or said.split()[0] in NO_START or said.startswith('never mind')


def _option_number(said: str) -> int | None:
    """'2', 'option two', 'option number two', 'the first one': a lone number among filler words."""
    words = said.split()
    numbers = [w for w in words if w in NUMBERS]
    if len(numbers) > 1 and 'one' in numbers:
        numbers.remove('one')
    if len(numbers) != 1 or any(w not in NUMBERS and w not in FILLERS and w != 'one' for w in words):
        return None
    return NUMBERS[numbers[0]]


def _cut(text: str) -> str:
    text = ' '.join(str(text).split())
    if len(text) <= SPEECH_LIMIT:
        return text
    head = text[:SPEECH_LIMIT]
    return f"{head[:head.rfind('. ') + 1] or head} and more."


class RoomDialog:
    def __init__(self, clock=time.monotonic, remind_after: float = 600.0, wake_word: str = '',
                 wake_window: float = 20.0):
        self._clock, self._remind_after = clock, remind_after
        # With a wake word, spoken answers count only after it; each answer keeps the window open.
        self._wake = _words(wake_word)
        self._wake_pattern = re.compile(
            r"^\W*(?:(?:hey|hi|ok|okay)\W+)?" + r"\W+".join(map(re.escape, self._wake.split())) + r"\b\W*",
            re.IGNORECASE) if self._wake else None
        self._wake_window = wake_window
        self._woken_at = float('-inf')
        self._remote = False
        self._since = 0.0
        self._reminded = False
        self._last_line = ''
        self._lock = threading.Lock()
        self._attention = None
        self._key = None
        self._stage = None
        self._step = 'idle'
        self._pending: RoomAction | None = None

    def observe(self, session: dict | None) -> list[str]:
        """``None`` means Hub is unreachable (keep the dialog); ``{}`` means no session (end it)."""
        if session is None:
            return []
        with self._lock:
            lines = []
            stage = session.get('stage') if session else None
            if session and session.get('mode') == 'debate' and stage == 'done' and self._stage not in (None, 'done'):
                lines.append('The debate is finished. The verdict is in the agent room.')
            self._stage = stage
            attention = session.get('attention') if session else None
            key = (session.get('id'), stage, attention.get('kind'), attention.get('summary')) if attention else None
            if key != self._key:
                self._key, self._attention, self._pending = key, attention, None
                self._step = 'ask' if attention else 'idle'
                self._since, self._reminded = self._clock(), False
                if attention:
                    self._last_line = self._announcement(attention)
                    lines.append(self._last_line)
            elif (attention and self._step == 'ask' and not self._reminded
                  and self._clock() - self._since >= self._remind_after):
                self._reminded = True
                lines.append(f'Reminder: the agents are still waiting for you {self._waiting_for()}. {self._prompt()}')
            return lines

    def handle(self, text: str, *, remote: bool = False) -> RoomReply | None:
        """``remote`` is a typed or recorded phone message: no wake word, and no merge or reject."""
        with self._lock:
            if self._step in ('idle', 'sending', 'done') or not self._attention:
                return None
            self._remote = remote
            if self._wake_pattern is not None and not remote:
                woke = self._wake_pattern.match(text)
                if woke:
                    text = text[woke.end():]
                elif self._clock() - self._woken_at > self._wake_window:
                    return None
                self._woken_at = self._clock()
                if woke and not _words(text):
                    return RoomReply("I'm listening.")
            said = _words(text)
            if not said:
                return None
            if any(w in said.split() for w in ('repeat',)) or 'say that again' in said or 'say it again' in said:
                return RoomReply(self._last_line)
            if 'options' in said.split() and 'revise' in self._attention.get('decisions', []) and self._step != 'confirm':
                self._step = 'choose'
                return RoomReply(self._options_text())
            if self._step == 'confirm':
                return self._confirm(said)
            if self._step == 'choose':
                return self._choose(said, text)
            return self._ask(said, text)

    def sent(self, ok: bool) -> None:
        """Called after the caller sent the action: success ends the dialog, failure lets the owner try again."""
        with self._lock:
            self._step = 'done' if ok else 'ask'
            self._pending = None

    def _announcement(self, attention: dict) -> str:
        summary = _cut(attention.get('summary') or '')
        if attention.get('kind') == 'pause':
            return f'The agent room is paused. {summary} {self._prompt()}'
        if attention.get('gate') == 'result':
            return f'Codex, Claude and Antigravity are done. {summary} {self._prompt()}'
        return f"The agents need you to review the {attention.get('gate')}. {summary} {self._prompt()}"

    def _waiting_for(self) -> str:
        if self._attention.get('kind') == 'pause':
            return 'to resume the agent room'
        if self._attention.get('gate') == 'result':
            return 'to accept or reject the result'
        return f"to review the {self._attention.get('gate')}"

    def _prompt(self) -> str:
        words = ['change' if d == 'revise' else d for d in self._attention.get('decisions', [])]
        listed = ', or '.join(words) if len(words) < 3 else f"{', '.join(words[:-1])}, or {words[-1]}"
        return f'Say {self._wake.title()}, then {listed}.' if self._wake else f'Say {listed}.'

    def _subject(self) -> str:
        return 'the agent room' if self._attention.get('kind') == 'pause' else f"the {self._attention.get('gate')}"

    def _action(self, decision: str, note: str | None = None) -> RoomAction:
        if self._attention.get('kind') == 'pause':
            return RoomAction('resume', None, decision)
        return RoomAction('gate', self._attention.get('gate'), decision, note)

    def _ask(self, said: str, text: str) -> RoomReply | None:
        decisions = self._attention.get('decisions', [])
        if _no(said) and len(said.split()) <= 3:
            return RoomReply(f'Okay, nothing was sent. {self._prompt()}')
        if NEGATION & set(said.split()):
            return None
        change = CHANGE.match(said)
        if change and 'revise' in decisions:
            rest = change.group('rest').strip()
            if len(rest.split()) >= 2:
                return self._propose(rest)
            self._step = 'choose'
            return RoomReply(self._options_text())
        for decision in decisions:
            if len(said.split()) <= 5 and any(f' {phrase} ' in f' {said} ' for phrase in VERBS.get(decision, ())):
                if self._remote and decision in REMOTE_BLOCKED:
                    return RoomReply(f'{decision.capitalize()} is only taken at the computer, not from the phone.')
                self._pending = self._action(decision)
                self._step = 'confirm'
                return RoomReply(f'{decision.capitalize()} {self._subject()}? Say yes to confirm.')
        return None

    def _options_text(self) -> str:
        options = self._attention.get('options') or []
        if not options:
            return 'What should change?'
        listed = ' '.join(f'Option {i}: {option.rstrip(".")}.' for i, option in enumerate(options, 1))
        return f'{listed} Say an option number, or say what to change.'

    def _choose(self, said: str, text: str) -> RoomReply:
        if _no(said) and len(said.split()) <= 3:
            self._step = 'ask'
            return RoomReply(f'Okay, nothing was sent. {self._prompt()}')
        options = self._attention.get('options') or []
        n = _option_number(said)
        if n is not None:
            if n > len(options):
                return RoomReply(f'There are only {len(options)} options. Say an option number, or say what to change.')
            return self._propose(options[n - 1])
        return self._propose(' '.join(text.split()))

    def _propose(self, note: str) -> RoomReply:
        self._pending = self._action('revise', note)
        self._step = 'confirm'
        return RoomReply(f'Send this change: {note}? Say yes to confirm.')

    def _confirm(self, said: str) -> RoomReply:
        if _yes(said) and self._remote and self._pending.decision in REMOTE_BLOCKED:
            self._pending, self._step = None, 'ask'
            return RoomReply(f'That is only taken at the computer, not from the phone. Nothing was sent. {self._prompt()}')
        if _yes(said):
            action, self._pending = self._pending, None
            self._step = 'sending'
            return RoomReply('Sending.', action)
        if _no(said):
            self._pending = None
            self._step = 'ask'
            return RoomReply(f'Okay, nothing was sent. {self._prompt()}')
        return RoomReply('Say yes to send it, or no to cancel.')
