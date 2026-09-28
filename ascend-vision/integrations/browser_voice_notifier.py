"""Deliver local browser-task completion summaries to the originating voice session."""

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
                 poll_interval_seconds: float = .5, task_timeout_seconds: float = 180):
        if not callable(getattr(browser_client, 'events', None)):
            raise TypeError('browser_client must provide events')
        if not callable(getattr(speech, 'speak_guarded_announcement', None)):
            raise TypeError('speech must provide speak_guarded_announcement')
        if not callable(session_is_current):
            raise TypeError('session_is_current must be callable')
        if (type(poll_interval_seconds) not in {int, float} or poll_interval_seconds <= 0
                or type(task_timeout_seconds) not in {int, float} or task_timeout_seconds <= 0):
            raise ValueError('poll and task timeout settings must be positive')
        self._client = browser_client
        self._speech = speech
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
                        message = self._summary(page.result)
                        if message:
                            self._speech.speak_guarded_announcement(
                                message, lambda key=session_key: self._session_is_current(key),
                            )
                    return
            except Exception as exc:
                LOG.info('Browser voice completion check unavailable (%s)', type(exc).__name__)
            self._stop.wait(self._poll_interval)
        if not self._stop.is_set():
            LOG.info('Browser voice completion was not available before the task deadline')

    @staticmethod
    def _summary(result: dict) -> str:
        status = result.get('status') if isinstance(result, dict) else None
        findings = result.get('findings', []) if isinstance(result, dict) else []
        finding = ''
        if isinstance(findings, list) and findings and isinstance(findings[0], dict):
            value = findings[0].get('text')
            if isinstance(value, str):
                finding = re.sub(r'\s+', ' ', value).strip()[:180]
        if status == 'completed' and finding:
            return f'Browser research complete: {finding}'[:240]
        if status == 'partial' and finding:
            return f'Browser research is partial: {finding}'[:240]
        if status == 'cancelled':
            return 'Browser research was stopped.'
        if status == 'unknown':
            return 'Browser research ended with an uncertain result. Ask me to review it.'
        return 'I could not complete the browser research task.'
