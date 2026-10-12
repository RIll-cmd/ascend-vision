"""Tell the owner how a local browser task ended: a short chat message and a spoken line."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time


LOG = logging.getLogger(__name__)
_TERMINAL = frozenset({'completed', 'partial', 'failed', 'cancelled', 'unknown'})


class BrowserVoiceCompletionNotifier:
    """Poll one bounded task queue and speak results only to their live voice session."""

    def __init__(self, browser_client, speech, session_is_current, *,
                 poll_interval_seconds: float = .5, task_timeout_seconds: float = 180,
                 chat_publisher=None):
        if not callable(getattr(browser_client, 'events', None)):
            raise TypeError('browser_client must provide events')
        if not callable(getattr(speech, 'speak_announcement', None)):
            raise TypeError('speech must provide speak_announcement')
        if chat_publisher is not None and not callable(chat_publisher):
            raise TypeError('chat_publisher must be callable')
        if not callable(session_is_current):
            raise TypeError('session_is_current must be callable')
        if (type(poll_interval_seconds) not in {int, float} or poll_interval_seconds <= 0
                or type(task_timeout_seconds) not in {int, float} or task_timeout_seconds <= 0):
            raise ValueError('poll and task timeout settings must be positive')
        self._client = browser_client
        self._speech = speech
        self._chat_publisher = chat_publisher
        self._session_is_current = session_is_current
        self._poll_interval = float(poll_interval_seconds)
        self._task_timeout = float(task_timeout_seconds)
        self._queue: queue.Queue[tuple[str, tuple[str, str, str]]] = queue.Queue(maxsize=4)
        self._watched: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError('browser voice notifier is single-use')
        self._thread = threading.Thread(target=self._run, name='vision-browser-voice-notifier', daemon=True)
        self._thread.start()

    def watch(self, task_id: str, session_key: tuple[str, str, str]) -> bool:
        """Watch only tasks explicitly submitted from this laptop's voice session."""
        if (not isinstance(task_id, str) or not task_id
                or not isinstance(session_key, tuple) or len(session_key) != 3
                or session_key[0] != 'local' or session_key[1] != 'voice'
                or not isinstance(session_key[2], str) or not session_key[2]):
            return False
        with self._lock:
            if task_id in self._watched or self._stop.is_set():
                return False
            try:
                self._queue.put_nowait((task_id, session_key))
            except queue.Full:
                LOG.info('Browser voice completion queue is full; task %s will not be announced', task_id)
                return False
            self._watched.add(task_id)
            return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                task_id, session_key = self._queue.get(timeout=.2)
            except queue.Empty:
                continue
            self._wait_for_task(task_id, session_key)

    def _wait_for_task(self, task_id: str, session_key: tuple[str, str, str]) -> None:
        deadline = time.monotonic() + self._task_timeout + 10
        cursor = 0
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                page = self._client.events(task_id, cursor, session_key)
                cursor = page.next_cursor
                if page.state in _TERMINAL:
                    if page.result and self._session_is_current(session_key):
                        self._deliver(*self.compose(page.result))
                    return
            except Exception as exc:
                LOG.info('Browser voice completion check unavailable (%s)', type(exc).__name__)
            self._stop.wait(self._poll_interval)
        if not self._stop.is_set():
            LOG.info('Browser voice completion was not available before the task deadline')

    def _deliver(self, chat_text: str, spoken_text: str) -> None:
        """The answer to a request the owner just made: show it, then say it."""
        if self._chat_publisher is not None:
            try:
                self._chat_publisher(chat_text)
            except Exception as exc:
                LOG.info('Browser result could not be added to chat (%s)', type(exc).__name__)
        try:
            self._speech.speak_announcement(spoken_text)
        except Exception as exc:
            LOG.info('Browser result could not be spoken (%s)', type(exc).__name__)

    @staticmethod
    def _short(text: str, limit: int) -> str:
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        sentence_end = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
        if sentence_end >= limit // 3:
            return cut[:sentence_end + 1]
        return cut[:cut.rfind(' ')].rstrip(' ,;:') + '...' if ' ' in cut else cut + '...'

    @classmethod
    def compose(cls, result: dict) -> tuple[str, str]:
        """Return (chat text, spoken text) for a terminal task result. Never includes page markup."""
        status = result.get('status') if isinstance(result, dict) else None
        findings = result.get('findings', []) if isinstance(result, dict) else []
        reason = result.get('reason') if isinstance(result, dict) and isinstance(result.get('reason'), str) else ''
        finding, sources = '', []
        if isinstance(findings, list) and findings and isinstance(findings[0], dict):
            value = findings[0].get('text')
            if isinstance(value, str):
                finding = value
            raw_sources = findings[0].get('sources')
            if isinstance(raw_sources, list):
                sources = [item for item in raw_sources if isinstance(item, str)
                           and item.startswith(('http://', 'https://'))][:2]
        if status in {'completed', 'partial'} and finding:
            lead = "Here's what I found:" if status == 'completed' else "I only partly finished. Here's what I have:"
            chat = f'{lead} {cls._short(finding, 700)}'
            if sources:
                chat += '\n\nSource: ' + '\n'.join(sources)
            spoken = f"{lead.rstrip(':')}. {cls._short(finding, 220)}"
            return chat, spoken
        if status == 'cancelled':
            return 'Okay, I stopped that lookup.', 'Okay, I stopped.'
        if status == 'unknown':
            note = 'That lookup ended with an uncertain result. Please check the page yourself.'
            return note, note
        why = cls._short(reason, 200) if reason else ''
        chat = "I couldn't finish that lookup." + (f' {why}' if why else '')
        spoken = "Sorry, I couldn't finish that lookup." + (f' {why}' if why else '')
        return chat, spoken
