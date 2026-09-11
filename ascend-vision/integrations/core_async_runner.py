"""Background asyncio event loop runner for non-blocking Core async operations."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import logging
import threading
from typing import Any, Coroutine

LOG = logging.getLogger(__name__)


class CoreAsyncRunner:
    """Runs a dedicated background asyncio event loop on a daemon thread.

    Keeps async network I/O completely off the real-time computer vision frame loop.
    """

    def __init__(self, name: str = "ascend-core-async"):
        self._loop = asyncio.new_event_loop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # Cancel remaining tasks on exit
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def submit(self, coro: Coroutine[Any, Any, Any]) -> Future:
        """Schedule an async coroutine on the background loop without blocking the caller."""
        if not self._loop.is_running():
            raise RuntimeError("CoreAsyncRunner event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[Any, Any, Any], timeout: float = 15.0) -> Any:
        """Execute a coroutine and block until completion up to timeout."""
        future = self.submit(coro)
        return future.result(timeout=timeout)

    def close(self, timeout: float = 2.0) -> None:
        """Gracefully stop the background event loop."""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)
