"""Separate-process entry point and lifecycle manager for Vision's browser broker."""

import argparse
import logging
import multiprocessing
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

from browser.executor import BrowserExecutor
from browser.files import BrowserTaskFiles
from browser.journal import BrowserActionJournal
from browser.ipc import (
    BROKER_KEYRING_SERVICE,
    BrowserIpcClient,
    BrowserPipeServer,
    _keyring,
    broker_identity,
    broker_pipe_address,
)
from browser.planner import BrowserPlanner
from browser.profile_store import BrowserProfileStore
from browser.policy import BrowserPolicy
from browser.service import BrowserTaskService
from llm_router import LLMRouter


LOG = logging.getLogger(__name__)


def _serve(address, authkey, browser_config, llm_config, broker_boot_id=None):
    broker_boot_id = broker_boot_id or str(uuid4())
    remote_guard = None
    if browser_config.remote_enabled:
        core_url = os.environ.get('ASCEND_PHONE_CORE_URL', '').strip()
        worker_token = os.environ.get('ASCEND_BROWSER_WORKER_TOKEN', '').strip()
        owner_id = os.environ.get('ASCEND_PHONE_OWNER_ID', '').strip()
        laptop_id = os.environ.get('ASCEND_BROWSER_LAPTOP_ID', '').strip()
        if core_url and worker_token and owner_id and laptop_id:
            try:
                from browser.remote_guard import RemoteDispatchGuard
                remote_guard = RemoteDispatchGuard(
                    core_url, worker_token, owner_id=owner_id, laptop_id=laptop_id,
                )
            except Exception as exc:
                LOG.warning('Remote browser dispatch authorization unavailable (%s)', type(exc).__name__)
        else:
            LOG.warning('Remote browser tasks are configured but worker credentials are incomplete')
    profiles = BrowserProfileStore() if browser_config.b3_enabled else None
    files = BrowserTaskFiles() if browser_config.b3_enabled else None
    from browser.routines import RoutineBudget, RoutineRegistry
    routine_registry = RoutineRegistry(
        Path(__file__).resolve().parent / 'routines' / 'definitions',
        enabled_versions=tuple((item['routine_id'], item['version'], item['digest'])
                               for item in browser_config.enabled_routines),
        disabled_path=(Path(os.environ['LOCALAPPDATA']) / 'AscendVision' / 'browser' / 'routines-disabled.json'
                       if os.environ.get('LOCALAPPDATA') else None),
    )
    metrics = None
    try:
        from browser.metrics import MetricsStore, default_metrics_path
        metrics_path = default_metrics_path()
        if metrics_path is not None:
            metrics = MetricsStore(
                metrics_path, retention_days=browser_config.metrics_retention_days,
                max_runs=browser_config.metrics_max_runs, enabled=browser_config.metrics_enabled,
            )
    except Exception as exc:
        LOG.warning('Browser metrics storage is unavailable (%s)', type(exc).__name__)
    budget_tracker = None
    if browser_config.pilot_enabled:
        from browser.budgets import BudgetTracker, PriceTable
        price_path = Path(browser_config.pilot_price_table_path)
        if not price_path.is_absolute():
            price_path = Path(__file__).resolve().parent.parent / price_path
        price_table = PriceTable.from_json(price_path)
        price_table.require_fresh()
        local_app_data = os.environ.get('LOCALAPPDATA')
        if not local_app_data:
            raise RuntimeError('The browser pilot requires owner-local persistent budget storage.')
        budget_tracker = BudgetTracker(
            price_table,
            daily_cost_usd_micros=browser_config.pilot_daily_budget_usd_micros,
            pilot_cost_usd_micros=browser_config.pilot_total_budget_usd_micros,
            ledger_path=Path(local_app_data) / 'AscendVision' / 'browser' / 'pilot-budget.sqlite3',
        )
    journal = None
    if browser_config.b3_enabled:
        local_app_data = os.environ.get('LOCALAPPDATA')
        if not local_app_data:
            raise RuntimeError('B3 action recovery requires Windows owner-local storage.')
        journal = BrowserActionJournal(
            Path(local_app_data) / 'AscendVision' / 'browser' / 'actions.sqlite3',
        )
    planner = BrowserPlanner(LLMRouter(llm_config))
    service = BrowserTaskService(
        executor_factory_for_task=lambda request, policy_override=None, max_pages=None: BrowserExecutor(
            policy=policy_override or BrowserPolicy(
                scope_mode=request.scope_mode,
                allowed_origins=set(request.allowed_origins),
                capabilities=set(request.capabilities),
            ),
            headless=False, max_pages=min(browser_config.max_pages, max_pages or browser_config.max_pages),
            profile_store=profiles if request.profile_id else None,
            profile_owner=request.session_key[0] if request.profile_id else None,
            profile_id=request.profile_id,
            file_store=files,
            task_owner=request.session_key[0],
            task_id=request.task_id,
            selected_file_token=request.selected_file_token,
        ),
        decision_provider=planner.propose,
        decision_provider_with_usage=planner.propose,
        max_decisions=browser_config.max_decisions,
        max_actions=browser_config.max_actions,
        max_pages=browser_config.max_pages,
        max_queued_tasks=browser_config.max_queued_tasks,
        task_timeout_seconds=browser_config.task_timeout_seconds,
        enable_mutations=browser_config.b3_enabled,
        file_store=files,
        action_journal=journal,
        remote_enabled=browser_config.remote_enabled,
        remote_writes_enabled=browser_config.remote_writes_enabled,
        remote_scopes=browser_config.remote_scopes,
        remote_dispatch_guard=remote_guard,
        broker_boot_id=broker_boot_id,
        remote_provider=browser_config.provider,
        metrics_store=metrics,
        budget_tracker=budget_tracker,
        routine_registry=routine_registry,
        routine_installation_budget=RoutineBudget(
            browser_config.max_decisions, browser_config.max_actions,
            browser_config.max_pages, browser_config.task_timeout_seconds,
        ),
    )
    service.start()
    server = BrowserPipeServer(address, authkey, service)
    try:
        server.serve_forever()
    finally:
        service.close()
        if remote_guard is not None:
            remote_guard.close()


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
        self.broker_boot_id: str | None = None

    def start(self, timeout_seconds: float = 8.0) -> BrowserIpcClient:
        if self._process is not None:
            if self.client is None or not self.client.ping():
                raise RuntimeError('The browser broker process is already started but unavailable.')
            return self.client
        self._authkey = secrets.token_bytes(32)
        self.broker_boot_id = str(uuid4())
        vault = _keyring()
        try:
            vault.set_password(BROKER_KEYRING_SERVICE, self._identity, self._authkey.hex())
        except Exception as exc:
            self._authkey = None
            raise RuntimeError('Windows Credential Manager could not protect browser IPC credentials.') from exc
        context = multiprocessing.get_context('spawn')
        process = context.Process(
            target=_serve,
            args=(self._address, self._authkey, self._browser_config, self._llm_config, self.broker_boot_id),
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
