"""Periodic presence heartbeat worker for Ascend Core integration."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable, Optional

LOG = logging.getLogger(__name__)


class CoreHeartbeatWorker:
    """Sends presence heartbeat pings to Ascend Core every 25-30 seconds."""

    DEFAULT_INTERVAL_SECONDS = 25.0

    def __init__(
        self,
        core_client,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        callback: Optional[Callable[[dict[str, Any]], None]] = None,
        async_runner=None,
    ):
        if interval_seconds <= 0:
            raise ValueError("Heartbeat interval must be positive")
        self.core_client = core_client
        self.interval_seconds = interval_seconds
        self.callback = callback
        self.async_runner = async_runner

        self._stop_event = threading.Event()
        self._async_stop = None
        self._thread: Optional[threading.Thread] = None
        self._task = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()

            # If an async runner is available, schedule as a coroutine task
            if self.async_runner is not None and self.async_runner.loop.is_running():
                future = self.async_runner.submit(self._async_loop())
                self._task = future
                return True

            # Otherwise spawn a dedicated background thread
            self._thread = threading.Thread(
                target=self._thread_run, name="ascend-core-heartbeat", daemon=True
            )
            self._thread.start()
            return True

    def _thread_run(self) -> None:
        asyncio.run(self._async_loop())

    async def _async_loop(self) -> None:
        LOG.info("Starting Ascend Core presence heartbeat (interval=%.1fs)", self.interval_seconds)
        while not self._stop_event.is_set():
            try:
                if self.core_client is not None:
                    result = await self.core_client.send_heartbeat()
                    LOG.debug("Core heartbeat acknowledged: %s", result)
                    if self.callback is not None:
                        self.callback(result)
            except Exception as exc:
                LOG.warning("Core heartbeat unavailable: %s", exc)
                if self.callback is not None:
                    self.callback({"status": "OFFLINE", "error": str(exc)})

            # Interruptible sleep in small steps
            elapsed = 0.0
            step = min(0.5, max(0.01, self.interval_seconds / 4.0))
            while elapsed < self.interval_seconds and not self._stop_event.is_set():
                sleep_dur = min(step, max(0.001, self.interval_seconds - elapsed))
                await asyncio.sleep(sleep_dur)
                elapsed += sleep_dur

    def close(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
            task = self._task
        if task is not None and hasattr(task, "cancel"):
            try:
                task.cancel()
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
