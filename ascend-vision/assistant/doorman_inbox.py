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
MORE = 'doorman_inbox.more.jsonl'
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
                 per_hour: int = 3, total_per_hour: int = 10, max_chars: int = 300):
        self.folder, self.clock = Path(folder), clock
        self.urgent_words = tuple(w.lower() for w in (*URGENT_WORDS, *urgent_words) if w.strip())
        self.urgent_senders = {s.strip() for s in urgent_senders if s.strip()}  # Discord ids: names can be changed
        self.per_hour, self.total_per_hour, self.max_chars = per_hour, total_per_hour, max_chars
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
            hour_ago = now - timedelta(hours=1)
            self._recent = {k: kept for k, times in self._recent.items() if (kept := [t for t in times if t > hour_ago])}
            recent = self._recent.get(sender_id, [])
            if len(recent) >= self.per_hour:
                return 'I have your earlier messages and will pass them on.'
            if sum(len(times) for times in self._recent.values()) >= self.total_per_hour:
                return 'Vision is not taking more messages right now. Please try again later.'
            self._recent[sender_id] = recent + [now]
            lowered = f' {text.lower()} '
            # "not urgent" and "not right now" are not urgent; "help!" matches with its punctuation.
            urgent = (any(re.search(rf"(?<![a-z0-9])(?<!not )(?<!no )(?<!isn't )(?<!isnt )(?<!not really )"
                                    rf"(?<!not that ){re.escape(w)}(?![a-z0-9])", lowered)
                          for w in self.urgent_words)
                      or sender_id in self.urgent_senders)
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
    """Vision side: speaks each waiting message once. While a focus block holds, routine ones stay on disk."""

    def __init__(self, folder: Path, *, announce, holding=lambda: False, clock=datetime.now, in_block=lambda: False):
        self.folder, self.announce, self.holding, self.clock = Path(folder), announce, holding, clock
        self.in_block = in_block  # messages the owner let through during a block must pass the pacer's hold
        self.released = None  # called once let-through messages are out, so later ones wait again

    def waiting(self) -> int:
        """Messages on disk not yet spoken (the summary at the end of a focus block counts them)."""
        count = 0
        for name in (READING, INBOX, MORE):
            try:
                count += sum(1 for line in (self.folder / name).read_text(encoding='utf-8').splitlines() if line.strip())
            except OSError:
                pass
        return count

    def _age(self, stamp) -> str:
        try:
            minutes = (self.clock() - datetime.fromisoformat(str(stamp))).total_seconds() / 60
        except (TypeError, ValueError):
            return ''
        if minutes < 10:
            return ''
        if minutes < 60:
            return f', {int(minutes)} minutes ago'
        if minutes < 24 * 60:
            hours = int(minutes // 60)
            return f", {hours} hour{'' if hours == 1 else 's'} ago"
        days = int(minutes // (24 * 60))
        return ', yesterday' if days == 1 else f', {days} days ago'

    def tick(self) -> None:
        inbox, reading = self.folder / INBOX, self.folder / READING
        if inbox.exists():
            try:
                if reading.exists():  # merge by renaming, so a line the bot adds meanwhile is never lost
                    more = self.folder / MORE
                    os.replace(inbox, more)
                    self._write(reading, self._read(reading) + self._read(more))
                    more.unlink()
                else:
                    os.replace(inbox, reading)  # the bot keeps appending to a new file
            except OSError as exc:  # the bot is writing right now; next tick
                LOG.info('Doorman inbox busy (%s)', type(exc).__name__)
        messages = self._read(reading)
        try:
            holding = bool(self.holding())
        except Exception:
            holding = False
        speak = [m for m in messages if not holding or m.get('urgent') is True]
        try:
            self._write(reading, [m for m in messages if m not in speak])
        except OSError as exc:
            LOG.info('Doorman inbox not updated (%s)', type(exc).__name__)
            return  # nothing is spoken twice
        try:
            let_through = bool(self.in_block())
        except Exception:
            let_through = False
        if let_through and speak and self.released is not None:
            try:
                self.released()
            except Exception:
                pass
        for item in speak:
            who = _clean(item.get('name', ''), MAX_NAME + 40) or 'someone'
            text = _clean(item.get('text', ''), 300)
            if not text:
                continue
            age = self._age(item.get('at'))
            if item.get('urgent') is True:
                self.announce(f'Urgent message from {who} on Discord{age}: {text}', urgent=True)
            else:
                self.announce(f'Message from {who} on Discord{age}: {text}', urgent=let_through)

    @staticmethod
    def _read(path: Path) -> list[dict]:
        try:
            raw = path.read_text(encoding='utf-8')
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

    @staticmethod
    def _write(path: Path, items: list[dict]) -> None:
        if not items:
            path.unlink(missing_ok=True)
            return
        temporary = path.with_name(path.name + '.tmp')
        temporary.write_text(''.join(json.dumps(i) + '\n' for i in items), encoding='utf-8')
        os.replace(temporary, path)


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
