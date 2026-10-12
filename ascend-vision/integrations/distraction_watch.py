"""Nudge during focus when a listed site is the front browser tab; keep minutes per site per day.

The front window's title is read in memory every poll, matched against the owner's
site list and discarded. Only the matched list entry (e.g. "YouTube") is kept or
sent anywhere. Nothing runs while Vision is paused.
"""
from __future__ import annotations

from datetime import date
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time

LOG = logging.getLogger(__name__)

BROWSERS = frozenset({'chrome.exe', 'msedge.exe', 'opera.exe', 'firefox.exe', 'brave.exe',
                      'vivaldi.exe', 'arc.exe', 'iexplore.exe'})
DEFAULT_SITES = ('YouTube', 'Instagram', 'TikTok', 'Facebook', 'X', 'Twitter', 'Reddit', 'Netflix', 'Twitch')
_SEPARATORS = re.compile(r'\s+[-|–—/:•·]\s+')
_BADGE = re.compile(r'^\(\d+\+?\)\s*')


def match_site(title: str, sites) -> str | None:
    """Return the list entry whose name is a whole part of the title, e.g. "Cats - YouTube - Google Chrome"."""
    if not title:
        return None
    wanted = {site.strip().lower(): site for site in sites if site and site.strip()}
    for part in _SEPARATORS.split(title):
        name = _BADGE.sub('', part).strip().lower()
        if name in wanted:
            return wanted[name]
    return None


class WindowsForegroundProbe:
    """Front window's executable name and title; nothing is kept by this class."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes, self._wintypes = ctypes, wintypes
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def foreground(self) -> tuple[str, str] | None:
        ctypes, wintypes = self._ctypes, self._wintypes
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self._kernel32.OpenProcess(0x1000, False, pid.value) if pid.value else None
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            executable = Path(buffer.value).name.lower()
        finally:
            self._kernel32.CloseHandle(handle)
        length = self._user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(min(length, 2048) + 1)
        self._user32.GetWindowTextW(hwnd, title, len(title))
        return executable, title.value


class DistractionStore:
    """Seconds per listed site per local day. No titles, no addresses."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS distraction_time (day TEXT NOT NULL, site TEXT NOT NULL, '
                       'seconds REAL NOT NULL, PRIMARY KEY (day, site))')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def add(self, site: str, seconds: float, day: date | None = None) -> None:
        if seconds <= 0:
            return
        with self._connect() as db:
            db.execute('INSERT INTO distraction_time VALUES (?, ?, ?) ON CONFLICT(day, site) '
                       'DO UPDATE SET seconds = seconds + excluded.seconds',
                       ((day or date.today()).isoformat(), site, float(seconds)))

    @staticmethod
    def totals(path, start: date, end: date) -> list[dict]:
        """Read-only totals for a date range; an absent file means no time yet."""
        path = Path(path)
        if not path.is_file():
            return []
        connection = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=2)
        try:
            rows = connection.execute(
                'SELECT site, SUM(seconds) FROM distraction_time WHERE day BETWEEN ? AND ? '
                'GROUP BY site ORDER BY SUM(seconds) DESC', (start.isoformat(), end.isoformat())).fetchall()
        finally:
            connection.close()
        return [{'site': site, 'seconds': round(seconds, 1)} for site, seconds in rows]


class DistractionWatch:
    def __init__(self, probe, sites, *, is_focus, nudge, store=None, grace_seconds=60.0,
                 repeat_seconds=300.0, poll_seconds=2.0, retry_seconds=10.0, clock=time.monotonic):
        self._probe, self._sites = probe, tuple(sites)
        self._is_focus, self._nudge, self._store = is_focus, nudge, store
        self._grace, self._repeat, self._poll = float(grace_seconds), float(repeat_seconds), float(poll_seconds)
        self._retry, self._clock = float(retry_seconds), clock
        self._site: str | None = None
        self._next_nudge = 0.0
        self._last_tick: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def current_site(self) -> str | None:
        return self._site

    def tick(self) -> None:
        now = self._clock()
        previous_tick, self._last_tick = self._last_tick, now
        site = None
        if self._is_focus():
            try:
                front = self._probe.foreground()
            except Exception as exc:
                LOG.debug('Foreground window unavailable (%s)', type(exc).__name__)
                front = None
            if front and front[0] in BROWSERS:
                site = match_site(front[1], self._sites)
        if site is None:
            self._site = None
            return
        if site != self._site:
            self._site, self._next_nudge = site, now + self._grace
        elif previous_tick is not None and self._store is not None:
            try:
                self._store.add(site, min(now - previous_tick, self._poll * 3))
            except Exception as exc:
                LOG.debug('Distraction time not saved (%s)', type(exc).__name__)
        if now >= self._next_nudge:
            accepted = False
            try:
                accepted = bool(self._nudge(site))
            except Exception as exc:
                LOG.debug('Distraction nudge failed (%s)', type(exc).__name__)
            self._next_nudge = now + (self._repeat if accepted else self._retry)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError('DistractionWatch is single-use')
        self._thread = threading.Thread(target=self._run, name='vision-distraction-watch', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._poll):
            self.tick()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

