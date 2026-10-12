"""Which AI keys are rate-limited right now, shared by Vision and its browser process.

Only a short fingerprint of each key is stored, never the key. A record says
"this key is limited until this time", taken from the provider's own retry hint
when it gives one. Nothing here may ever break an AI call.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

LOG = logging.getLogger(__name__)
DEFAULT_COOLDOWN_SECONDS = 60.0
MAX_COOLDOWN_SECONDS = 24 * 60 * 60


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def retry_seconds(exc: BaseException) -> float:
    """Read the provider's own "retry in ..." hint; fall back to one minute."""
    text = str(exc)
    match = re.search(r"retryDelay'?\"?\s*:\s*'?\"?(\d+(?:\.\d+)?)s", text)
    if match:
        return min(float(match.group(1)), MAX_COOLDOWN_SECONDS)
    match = re.search(r'(?:retry|try again) in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?', text, re.I)
    if match and any(match.groups()):
        hours, minutes, seconds = (float(value) if value else 0.0 for value in match.groups())
        total = hours * 3600 + minutes * 60 + seconds
        if total > 0:
            return min(total, MAX_COOLDOWN_SECONDS)
    return DEFAULT_COOLDOWN_SECONDS


def default_path() -> Path:
    base = os.environ.get('LOCALAPPDATA')
    root = Path(base) / 'AscendVision' if base else Path.home() / '.ascend-vision'
    return root / 'ai-key-status.sqlite3'


class KeyStatusStore:
    def __init__(self, path: Path | None = None, *, clock=time.time):
        self.path = Path(path) if path is not None else default_path()
        self._clock = clock
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS limits (provider TEXT NOT NULL, fingerprint TEXT NOT NULL, '
                       'limited_until REAL NOT NULL, PRIMARY KEY (provider, fingerprint))')

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.execute('PRAGMA journal_mode=WAL')
        return connection

    def record_limit(self, provider: str, key: str, seconds: float) -> None:
        until = self._clock() + max(1.0, min(float(seconds), MAX_COOLDOWN_SECONDS))
        with self._lock, self._connect() as db:
            db.execute('INSERT INTO limits VALUES (?, ?, ?) ON CONFLICT(provider, fingerprint) '
                       'DO UPDATE SET limited_until = excluded.limited_until',
                       (provider, fingerprint(key), until))

    def limited_until(self, provider: str, keys: list[str]) -> dict[str, float]:
        """fingerprint -> epoch seconds, only for the given keys still limited now."""
        prints = [fingerprint(key) for key in keys]
        if not prints:
            return {}
        now = self._clock()
        with self._lock, self._connect() as db:
            rows = db.execute(
                f"SELECT fingerprint, limited_until FROM limits WHERE provider = ? AND limited_until > ? "
                f"AND fingerprint IN ({','.join('?' * len(prints))})", (provider, now, *prints)).fetchall()
        return dict(rows)

    def summary(self, pools: dict[str, list[str]]) -> dict[str, dict]:
        """Per provider: how many keys exist, how many are limited, and when the next one frees up."""
        result = {}
        for provider, keys in pools.items():
            if not keys:
                continue
            limited = self.limited_until(provider, keys)
            entry = {'total': len(keys), 'limited': len(limited), 'freeAt': None}
            if limited:
                soonest = min(limited.values())
                entry['freeAt'] = datetime.fromtimestamp(soonest, timezone.utc).isoformat()
            result[provider] = entry
        return result


_STORE: KeyStatusStore | None = None
_STORE_LOCK = threading.Lock()


def shared_store() -> KeyStatusStore | None:
    """The owner-local store, or None when it cannot be opened (AI calls continue regardless)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            try:
                _STORE = KeyStatusStore()
            except Exception as exc:
                LOG.info('AI key status storage unavailable (%s)', type(exc).__name__)
                return None
        return _STORE


def keys_from_env(env_names: dict[str, str]) -> dict[str, list[str]]:
    """provider -> keys, read the same way the router reads them (comma, semicolon or newline separated)."""
    result = {}
    for provider, name in env_names.items():
        raw = os.environ.get(name, '')
        result[provider] = [key.strip() for key in re.split(r'[,;\n]', raw) if key.strip()]
    return result


class KeyLowNotifier:
    """Say once when every key of a provider is used up; say it again only after one frees up."""

    NAMES = {'gemini': 'Gemini', 'groq': 'Groq', 'cerebras': 'Cerebras'}

    def __init__(self):
        self._warned: set[str] = set()

    def messages(self, summary: dict[str, dict]) -> list[str]:
        out = []
        for provider, entry in summary.items():
            if entry['total'] and entry['limited'] >= entry['total']:
                if provider not in self._warned:
                    self._warned.add(provider)
                    when = ''
                    if entry.get('freeAt'):
                        local = datetime.fromisoformat(entry['freeAt']).astimezone()
                        when = f" until about {local.strftime('%I:%M %p').lstrip('0')}"
                    name = self.NAMES.get(provider, provider.title())
                    out.append(f'Heads up: all your {name} keys are used up{when}. '
                               f'I will use another AI where I can.')
            else:
                self._warned.discard(provider)
        return out
