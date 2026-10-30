"""The focus doorman's front desk: messages people send Vision's Discord bot, and phone pushes during a block.

The bot runs in its own process, so the two sides share two files in Vision's data folder:
``focus_block.json`` (written by the doorman while a block runs) and ``doorman_inbox.jsonl`` (one message a line).
Messages from other people are only spoken; they never reach the AI and never trigger anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import threading

LOG = logging.getLogger(__name__)

INBOX = 'doorman_inbox.jsonl'
READING = 'doorman_inbox.reading.jsonl'
BLOCK = 'focus_block.json'
URGENT_WORDS = ('urgent', 'emergency', 'asap', 'hospital', 'call me', 'right now')
MAX_NAME = 40


def _clean(text: str, limit: int) -> str:
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in str(text)).split())[:limit].strip()


def _clock_words(at: datetime) -> str:
    hour = at.hour % 12 or 12
    return f"{hour}{f':{at.minute:02d}' if at.minute else ''} {'am' if at.hour < 12 else 'pm'}"


class Reception:
    """Bot side: answers a message from someone else and leaves it in the inbox for Vision to speak."""

    def __init__(self, folder: Path, *, clock=datetime.now, urgent_words=(), urgent_senders=(),
                 per_hour: int = 3, max_chars: int = 300):
        self.folder, self.clock = Path(folder), clock
        self.urgent_words = tuple(w.lower() for w in (*URGENT_WORDS, *urgent_words) if w.strip())
        self.urgent_senders = {s.strip().lower() for s in urgent_senders if s.strip()}
        self.per_hour, self.max_chars = per_hour, max_chars
        self._recent: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def _block_until(self, now: datetime) -> datetime | None:
        try:
            until = datetime.fromisoformat(json.loads((self.folder / BLOCK).read_text(encoding='utf-8'))['until'])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return until if until > now else None

    def take(self, sender_id: str, name: str, text: str) -> str:
        text, name = _clean(text, self.max_chars), _clean(name, MAX_NAME) or 'someone'
        if not text:
            return 'Send a message with some words in it.'
        now = self.clock()
        with self._lock:
            recent = [t for t in self._recent.get(sender_id, []) if now - t <= timedelta(hours=1)]
            if len(recent) >= self.per_hour:
                self._recent[sender_id] = recent
                return 'I have your earlier messages and will pass them on.'
            self._recent[sender_id] = recent + [now]
            lowered = f' {text.lower()} '
            urgent = (any(re.search(rf'\b{re.escape(w)}\b', lowered) for w in self.urgent_words)
                      or sender_id.lower() in self.urgent_senders or name.lower() in self.urgent_senders)
            line = json.dumps({'at': now.isoformat(), 'sender': sender_id, 'name': name, 'text': text,
                               'urgent': urgent})
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / INBOX, 'a', encoding='utf-8') as inbox:
                inbox.write(line + '\n')
        until = self._block_until(now)
        if until is None:
            return "I'll pass it on."
        if urgent:
            return f"In a focus block until {_clock_words(until)}, but this sounds urgent, so I'm passing it on now."
        return f"In a focus block until {_clock_words(until)}. I'll pass it on."


class Inbox:
    """Vision side: speaks each waiting message once. Routine ones are ordinary notices, so a block holds them."""

    def __init__(self, folder: Path, *, announce):
        self.folder, self.announce = Path(folder), announce

    def tick(self) -> None:
        inbox, reading = self.folder / INBOX, self.folder / READING
        messages = self._read(reading) if reading.exists() else []
        if inbox.exists():
            try:
                if messages:
                    messages += self._read(inbox, keep=False)
                else:
                    os.replace(inbox, reading)  # the bot keeps appending to a new file
                    messages = self._read(reading)
            except OSError as exc:  # the bot is writing right now; next tick
                LOG.info('Doorman inbox busy (%s)', type(exc).__name__)
        try:
            reading.unlink(missing_ok=True)
        except OSError:
            return
        for item in messages:
            who = _clean(item.get('name', ''), MAX_NAME) or 'someone'
            text = _clean(item.get('text', ''), 300)
            if not text:
                continue
            if item.get('urgent') is True:
                self.announce(f'Urgent message from {who} on Discord: {text}', urgent=True)
            else:
                self.announce(f'Message from {who} on Discord: {text}')

    @staticmethod
    def _read(path: Path, keep: bool = True) -> list[dict]:
        try:
            raw = path.read_text(encoding='utf-8')
            if not keep:
                path.unlink()
        except OSError:
            return []
        items = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                items.append(item)
        return items


class hold_pushes:
    """Wraps the phone push publisher: nothing is pushed while ``holding()``; the event is offered again later."""

    def __init__(self, publisher, holding):
        self._publisher, self._holding = publisher, holding

    def publish(self, intent) -> bool:
        try:
            if self._holding():
                return False
        except Exception:
            pass
        return self._publisher.publish(intent)
