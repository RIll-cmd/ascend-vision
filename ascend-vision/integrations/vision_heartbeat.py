"""Failure-contained background presence reporting for Ascend Vision."""
from __future__ import annotations

import logging
import threading
from typing import Callable


LOG = logging.getLogger(__name__)


class VisionHeartbeatWorker:
    """One immediate, periodic, user-authenticated Core presence heartbeat worker."""
    def __init__(self, client, *, character_id: str, device_id: str, version: str,
                 interval_seconds: float = 10.0, callback: Callable | None = None,
                 shutdown_timeout_seconds: float = 6.0):
        if interval_seconds <= 0:
            raise ValueError('Heartbeat interval must be positive')
        if shutdown_timeout_seconds <= 0:
            raise ValueError('Heartbeat shutdown timeout must be positive')
        self._client = client
        self._character_id = character_id
        self._device_id = device_id
        self._version = version
        self._interval_seconds = interval_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._callback = callback
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None:
                return False
            self._thread = threading.Thread(target=self._run, name='ascend-vision-heartbeat', daemon=True)
            self._thread.start()
            return True

    def close(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(self._shutdown_timeout_seconds)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _run(self):
        while not self._stop.is_set():
            try:
                result = self._client.send_vision_heartbeat(
                    character_id=self._character_id,
                    device_id=self._device_id,
                    version=self._version,
                )
                if self._callback is not None:
                    self._callback(result)
                LOG.info('Vision heartbeat: %s', getattr(getattr(result, 'state', None), 'value', 'sent'))
            except Exception as exc:
                LOG.warning('Vision heartbeat unavailable (%s)', type(exc).__name__)
            self._stop.wait(self._interval_seconds)
