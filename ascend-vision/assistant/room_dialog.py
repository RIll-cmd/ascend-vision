"""Voice dialog for Ascend Hub's agent room: announce what the agents need, and take the owner's answer.

Pure state machine: it never talks to the network. The caller feeds it session snapshots
(``observe``) and unmatched speech (``handle``), speaks the returned text, and sends the
returned ``RoomAction`` to Hub. Nothing is sent without a spoken yes.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading

SPEECH_LIMIT = 400
NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, '1': 1, '2': 2, '3': 3, '4': 4,
           'first': 1, 'second': 2, 'third': 3, 'fourth': 4}
YES = {'yes', 'yeah', 'yep', 'confirm', 'do it', 'send it', 'sure', 'ok', 'okay', 'yes please'}
NO = {'no', 'nope', 'cancel', 'never mind', 'nevermind', 'dont', 'do not', 'no thanks'}
VERBS = {'approve': ('approve', 'approved', 'looks good', 'go ahead', 'sounds good'),
         'accept': ('accept', 'accepted'),
         'reject': ('reject', 'rejected', 'decline'),
         'retry': ('retry', 'try again'),
         'stop': ('stop', 'end it')}
CHANGE = re.compile(r'^(?:please\s+)?(?:change|revise|edit)\b(?:\s+(?:it|the\s+(?:plan|roles)))?(?:\s+(?:to|so that|so))?\s*(?P<rest>.*)$')
OPTION = re.compile(r'^(?:option|number|choice)?\s*(?P<n>one|two|three|four|first|second|third|fourth|[1-4])(?:\s+(?:option|one|please))?$')


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


def _cut(text: str) -> str:
    text = ' '.join(str(text).split())
    if len(text) <= SPEECH_LIMIT:
        return text
    head = text[:SPEECH_LIMIT]
    return f"{head[:head.rfind('. ') + 1] or head} and more."


class RoomDialog:
    def __init__(self):
        self._lock = threading.Lock()
        self._attention = None
        self._key = None
        self._stage = None
        self._step = 'idle'
        self._pending: RoomAction | None = None

    def observe(self, session: dict | None) -> list[str]:
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
                if attention:
                    lines.append(self._announcement(attention))
            return lines

    def handle(self, text: str) -> RoomReply | None:
        with self._lock:
            if self._step in ('idle', 'sending', 'done') or not self._attention:
                return None
            said = _words(text)
            if not said:
                return None
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

    def _prompt(self) -> str:
        words = ['change' if d == 'revise' else d for d in self._attention.get('decisions', [])]
        return f"Say {', or '.join(words)}."

    def _subject(self) -> str:
        return 'the agent room' if self._attention.get('kind') == 'pause' else f"the {self._attention.get('gate')}"

    def _action(self, decision: str, note: str | None = None) -> RoomAction:
        if self._attention.get('kind') == 'pause':
            return RoomAction('resume', None, decision)
        return RoomAction('gate', self._attention.get('gate'), decision, note)

    def _ask(self, said: str, text: str) -> RoomReply | None:
        decisions = self._attention.get('decisions', [])
        if said in NO:
            return RoomReply(f'Okay, nothing was sent. {self._prompt()}')
        change = CHANGE.match(said)
        if change and 'revise' in decisions:
            rest = change.group('rest').strip()
            if len(rest.split()) >= 2:
                return self._propose(rest)
            self._step = 'choose'
            return RoomReply(self._options_text())
        for decision in decisions:
            if any(said == phrase or said.startswith(phrase + ' ') for phrase in VERBS.get(decision, ())):
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
        if said in NO:
            self._step = 'ask'
            return RoomReply(f'Okay, nothing was sent. {self._prompt()}')
        options = self._attention.get('options') or []
        picked = OPTION.match(said)
        if picked:
            n = NUMBERS[picked.group('n')]
            if n > len(options):
                return RoomReply(f'There are only {len(options)} options. Say an option number, or say what to change.')
            return self._propose(options[n - 1])
        return self._propose(' '.join(text.split()))

    def _propose(self, note: str) -> RoomReply:
        self._pending = self._action('revise', note)
        self._step = 'confirm'
        return RoomReply(f'Send this change: {note}? Say yes to confirm.')

    def _confirm(self, said: str) -> RoomReply:
        if said in YES:
            action, self._pending = self._pending, None
            self._step = 'sending'
            return RoomReply('Sending.', action)
        if said in NO:
            self._pending = None
            self._step = 'ask'
            return RoomReply(f'Okay, nothing was sent. {self._prompt()}')
        return RoomReply('Say yes to send it, or no to cancel.')
