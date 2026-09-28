"""Separate-process entry point and lifecycle manager for Vision's browser broker."""

import argparse
import logging
import multiprocessing
import secrets
import time

from browser.executor import BrowserExecutor
from browser.ipc import (
    BROKER_KEYRING_SERVICE,
    BrowserIpcClient,
    BrowserPipeServer,
    _keyring,
    broker_identity,
    broker_pipe_address,
)
from browser.planner import BrowserPlanner
from browser.policy import BrowserPolicy
from browser.service import BrowserTaskService
from llm_router import LLMRouter


LOG = logging.getLogger(__name__)


def _serve(address, authkey, browser_config, llm_config):
    service = BrowserTaskService(
        executor_factory=lambda: BrowserExecutor(
            policy=BrowserPolicy(), headless=False, max_pages=browser_config.max_pages,
        ),
        decision_provider=BrowserPlanner(LLMRouter(llm_config)).propose,
        max_decisions=browser_config.max_decisions,
        max_actions=browser_config.max_actions,
        max_pages=browser_config.max_pages,
        max_queued_tasks=browser_config.max_queued_tasks,
        task_timeout_seconds=browser_config.task_timeout_seconds,
    )
    service.start()
    server = BrowserPipeServer(address, authkey, service)
    try:
        server.serve_forever()
    finally:
        service.close()


class BrowserBrokerProcess:
    """Launches the isolated broker and publishes a random authkey in Windows Credential Manager."""

    def __init__(self, browser_config, llm_config, *, address=None, identity=None):
        self._browser_config = browser_config
        self._llm_config = llm_config
        self._address = address or broker_pipe_address()
        self._identity = identity or broker_identity()
        self._process = None
        self.client: BrowserIpcClient | None = None
        self._authkey: bytes | None = None

    def start(self, timeout_seconds: float = 8.0) -> BrowserIpcClient:
        if self._process is not None:
            if self.client is None or not self.client.ping():
                raise RuntimeError('The browser broker process is already started but unavailable.')
            return self.client
        self._authkey = secrets.token_bytes(32)
        vault = _keyring()
        try:
            vault.set_password(BROKER_KEYRING_SERVICE, self._identity, self._authkey.hex())
        except Exception as exc:
            self._authkey = None
            raise RuntimeError('Windows Credential Manager could not protect browser IPC credentials.') from exc
        context = multiprocessing.get_context('spawn')
        process = context.Process(
            target=_serve,
            args=(self._address, self._authkey, self._browser_config, self._llm_config),
            name='Ascend Vision Browser Broker',
            daemon=True,
        )
        try:
            process.start()
            self._process = process
            self.client = BrowserIpcClient(self._address, self._authkey)
            deadline = time.monotonic() + timeout_seconds
            last_error = None
            while time.monotonic() < deadline and process.is_alive():
                try:
                    if self.client.ping():
                        return self.client
                except Exception as exc:
                    last_error = exc
                    time.sleep(.05)
            raise RuntimeError('The separate browser broker did not become ready.') from last_error
        except Exception:
            self.stop()
            raise

    def stop(self, timeout_seconds: float = 4.0) -> None:
        process = self._process
        if process is not None and process.is_alive():
            try:
                if self.client is not None:
                    self.client.shutdown()
            except Exception:
                pass
            process.join(timeout=max(0.0, timeout_seconds))
            if process.is_alive():
                LOG.error('Browser broker did not stop gracefully; leaving it running rather than force-killing an active browser action.')
        self._process = None if process is None or not process.is_alive() else process
        self.client = None if self._process is None else self.client
        if self._process is None:
            try:
                _keyring().delete_password(BROKER_KEYRING_SERVICE, self._identity)
            except Exception:
                pass
            self._authkey = None


def start_browser_broker(browser_config, llm_config, resources):
    """Start only the explicitly enabled broker and tie its lifetime to Vision."""
    if not browser_config.enabled:
        return None
    broker = BrowserBrokerProcess(browser_config, llm_config)
    client = broker.start()
    resources.callback(broker.stop)
    return client


def run_standalone() -> None:
    """Run a standalone broker for development without starting the camera runtime."""
    from config import load_config
    config = load_config('config.yaml')
    authkey = secrets.token_bytes(32)
    vault = _keyring()
    identity = broker_identity()
    vault.set_password(BROKER_KEYRING_SERVICE, identity, authkey.hex())
    try:
        _serve(broker_pipe_address(), authkey, config.browser_automation, config.llm)
    finally:
        try:
            vault.delete_password(BROKER_KEYRING_SERVICE, identity)
        except Exception:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the isolated Ascend Vision browser broker.')
    parser.add_argument('--standalone', action='store_true', help='run until Ctrl+C without the camera runtime')
    args = parser.parse_args(argv)
    if args.standalone:
        try:
            run_standalone()
        except KeyboardInterrupt:
            return 0
        return 0
    parser.error('use --standalone, or let Vision launch the broker when browser automation is enabled')


if __name__ == '__main__':
    raise SystemExit(main())
