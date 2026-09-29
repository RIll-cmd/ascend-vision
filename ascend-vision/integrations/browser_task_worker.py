"""Outbound Core worker for laptop-owned remote browser tasks."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from browser.remote_contracts import RemoteBrowserBinding, RemoteBrowserTaskRequest
from browser.authorization import ActionProposal


LOG = logging.getLogger(__name__)
_REMOTE_STATES = {'completed', 'partial', 'failed', 'cancelled', 'unknown'}


def _parse_claim(payload: object) -> RemoteBrowserTaskRequest:
    if not isinstance(payload, dict) or set(payload) != {
            'schema_version', 'binding', 'goal', 'provider', 'provider_consent', 'expires_at'}:
        raise ValueError('Core browser claim has an invalid contract')
    binding = payload.get('binding')
    if not isinstance(binding, dict):
        raise ValueError('Core browser claim binding is invalid')
    # Core HTTP models expose camelCase aliases; the shared IPC contract uses snake_case.
    aliases = {
        'taskId': 'task_id', 'ownerId': 'owner_id', 'browserSessionId': 'browser_session_id',
        'laptopId': 'laptop_id', 'brokerBootId': 'broker_boot_id', 'leaseId': 'lease_id',
        'scopeId': 'scope_id', 'scopeVersion': 'scope_version',
    }
    if set(binding) == set(aliases) | {'channel', 'fence'}:
        binding = {aliases.get(key, key): value for key, value in binding.items()}
    normalized = dict(payload)
    normalized['binding'] = binding
    return RemoteBrowserTaskRequest.from_payload(normalized)


class CoreBrowserClient:
    """HTTP client limited to the browser worker's Core routes."""

    def __init__(self, base_url: str, worker_token: str, *, timeout_seconds: float = 10,
                 transport=None):
        parts = urlsplit(base_url.strip())
        if (parts.scheme not in {'https', 'http'} or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment
                or (parts.scheme != 'https' and parts.hostname not in {'localhost', '127.0.0.1', '::1'})):
            raise ValueError('browser Core URL must be HTTPS (HTTP is allowed only for loopback development)')
        if not isinstance(worker_token, str) or not worker_token.strip():
            raise ValueError('browser worker token must be nonempty')
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip('/'), timeout=timeout_seconds, transport=transport,
            headers={'Authorization': f'Bearer {worker_token.strip()}'},
        )

    async def identity(self) -> dict:
        response = await self._client.get('/api/browser-tasks/worker/identity')
        response.raise_for_status()
        data = response.json()
        expected = {'ownerId', 'laptopId', 'currentBootId', 'remoteEnabled', 'remoteWritesEnabled'}
        if not isinstance(data, dict) or data.keys() != expected:
            raise ValueError('Core browser identity response is invalid')
        if (not isinstance(data['ownerId'], str) or not data['ownerId']
                or not isinstance(data['laptopId'], str) or not data['laptopId']
                or (data['currentBootId'] is not None and not isinstance(data['currentBootId'], str))
                or type(data['remoteEnabled']) is not bool or type(data['remoteWritesEnabled']) is not bool):
            raise ValueError('Core browser identity response is invalid')
        return data

    async def register_boot(self, expected_boot_id: str | None, new_boot_id: str) -> bool:
        response = await self._client.post('/api/browser-tasks/worker/register-boot', json={
            'expectedBootId': expected_boot_id, 'newBootId': new_boot_id,
        })
        if response.status_code in {409, 412}:
            return False
        response.raise_for_status()
        data = response.json()
        if (not isinstance(data, dict) or set(data) != {'registered', 'brokerBootId'}
                or type(data['registered']) is not bool
                or (data['registered'] and data['brokerBootId'] != new_boot_id)
                or (not data['registered'] and data['brokerBootId'] is not None)):
            raise ValueError('Core browser boot response is invalid')
        return data['registered']

    async def heartbeat(self, boot_id: str, availability: str, descriptor: dict) -> None:
        response = await self._client.post('/api/browser-tasks/worker/heartbeat', json={
            'brokerBootId': boot_id, 'availability': availability, 'descriptor': descriptor,
        })
        response.raise_for_status()

    async def claim(self, boot_id: str) -> RemoteBrowserTaskRequest | None:
        response = await self._client.post('/api/browser-tasks/worker/claim',
                                           json={'brokerBootId': boot_id})
        if response.status_code == 204:
            return None
        response.raise_for_status()
        try:
            return _parse_claim(response.json())
        except (ValueError, TypeError) as exc:
            raise ValueError('Core browser claim response is invalid') from exc

    @staticmethod
    def _headers(binding: RemoteBrowserBinding) -> dict:
        return {'X-Browser-Lease': binding.lease_id,
                'X-Browser-Fence': str(binding.fence),
                'X-Browser-Boot': binding.broker_boot_id}

    async def task_action(self, binding: RemoteBrowserBinding, action: str, payload: dict | None = None):
        response = await self._client.post(
            f'/api/browser-tasks/worker/tasks/{binding.task_id}/{action}',
            headers=self._headers(binding), json=payload or {},
        )
        if response.status_code in {404, 409, 410}:
            return None
        response.raise_for_status()
        return response.json() if response.content else {}

    async def publish_events(self, binding: RemoteBrowserBinding, events: list[dict]) -> bool:
        response = await self._client.post(
            f'/api/browser-tasks/worker/tasks/{binding.task_id}/publish-events',
            headers=self._headers(binding), json={'events': events},
        )
        if response.status_code in {404, 409, 410}:
            return False
        response.raise_for_status()
        data = response.json()
        return (isinstance(data, dict) and set(data) == {'accepted'}
                and type(data['accepted']) is int and data['accepted'] == len(events))

    async def publish_proposal(self, binding: RemoteBrowserBinding, proposal: dict | None,
                               waiting_for_user_type: str) -> bool:
        if waiting_for_user_type not in {'action_review', 'local_takeover'}:
            raise ValueError('Invalid browser waiting subtype')
        if waiting_for_user_type == 'action_review':
            if not isinstance(proposal, dict) or set(proposal) != set(ActionProposal.__dataclass_fields__) | {'digest'}:
                raise ValueError('Invalid browser action proposal')
            validated = ActionProposal(**{key: value for key, value in proposal.items() if key != 'digest'})
            if (validated.task_id != binding.task_id or validated.owner != binding.owner_id
                    or validated.digest != proposal['digest']):
                raise PermissionError('Browser proposal identity or digest does not match')
        elif proposal is not None:
            raise ValueError('Laptop takeover cannot carry an action approval')
        data = await self.task_action(binding, 'proposal', {
            'waitingForUserType': waiting_for_user_type, 'proposal': proposal,
        })
        return isinstance(data, dict) and set(data) == {'published'} and data['published'] is True

    async def reconcile(self, task_id: str, current_boot_id: str, payload: dict) -> bool:
        try:
            task_id = str(UUID(task_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('browser task reconciliation ID must be a UUID') from exc
        if (not isinstance(current_boot_id, str) or not current_boot_id
                or not isinstance(payload, dict)
                or payload.keys() != {'previousBrokerBootId', 'fence', 'actionId', 'action',
                                      'proposalDigest', 'outcome'}):
            raise ValueError('browser task reconciliation request is invalid')
        response = await self._client.post(
            f'/api/browser-tasks/worker/tasks/{task_id}/reconcile',
            headers={'X-Browser-Boot': current_boot_id}, json=payload,
        )
        if response.status_code in {404, 409, 410}:
            return False
        response.raise_for_status()
        data = response.json()
        return isinstance(data, dict) and set(data) == {'recorded'} and data['recorded'] is True

    async def complete(self, binding: RemoteBrowserBinding, state: str, summary: str,
                       event_cursor: int, result: dict | None) -> bool:
        data = await self.task_action(binding, 'complete', {
            'state': state, 'summary': summary[:8192], 'eventsCursor': event_cursor,
            'result': result,
        })
        return isinstance(data, dict) and set(data) == {'accepted'} and data['accepted'] is True

    async def release(self, binding: RemoteBrowserBinding) -> bool:
        data = await self.task_action(binding, 'release')
        return isinstance(data, dict) and set(data) == {'released'} and data['released'] is True

    async def close(self) -> None:
        await self._client.aclose()


@dataclass
class _ActiveTask:
    request: RemoteBrowserTaskRequest
    receipt: object
    cursor: int = 0
    last_renew: float = 0.0
    published_proposal: str | None = None
    applied_decisions: set[tuple[str, str, bool]] = field(default_factory=set)
    stopping: bool = False


class BrowserTaskWorker:
    """Single-owner polling worker; browser actions execute only in the broker."""

    def __init__(self, core, broker, *, owner_id: str, laptop_id: str, provider: str,
                 scopes=(), remote_writes_enabled: bool = False,
                 provider_ready: bool = True,
                 poll_interval_seconds: float = 2, heartbeat_interval_seconds: float = 15,
                 renew_interval_seconds: float = 20, shutdown_timeout_seconds: float = 4,
                 monotonic=time.monotonic):
        if min(poll_interval_seconds, heartbeat_interval_seconds, renew_interval_seconds,
               shutdown_timeout_seconds) <= 0:
            raise ValueError('browser worker intervals must be positive')
        self.core, self.broker = core, broker
        self.owner_id, self.laptop_id, self.provider = owner_id, laptop_id, provider
        self.scopes = tuple(scopes)
        self.remote_writes_enabled = remote_writes_enabled
        self.provider_ready = provider_ready
        self.poll_interval_seconds = poll_interval_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.renew_interval_seconds = renew_interval_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._boot_id: str | None = None
        self._active: _ActiveTask | None = None
        self._last_heartbeat = float('-inf')
        self._reconciled_boot_id: str | None = None

    async def _ensure_identity(self) -> bool:
        identity = await self.core.identity()
        if (identity['ownerId'] != self.owner_id or identity['laptopId'] != self.laptop_id
                or not identity['remoteEnabled']
                or (self.remote_writes_enabled and not identity['remoteWritesEnabled'])):
            raise PermissionError('Core browser worker identity or feature flags do not match this installation')
        broker_id = await asyncio.to_thread(self.broker.broker_identity)
        if self._boot_id is None:
            if not await self.core.register_boot(identity['currentBootId'], broker_id):
                raise PermissionError('Core rejected the broker boot registration')
            self._boot_id = broker_id
        elif self._boot_id != broker_id or identity['currentBootId'] != self._boot_id:
            raise PermissionError('Browser broker boot was replaced; this worker is fenced and must stop')
        if self._reconciled_boot_id != self._boot_id:
            await self._reconcile_prior_tasks()
            self._reconciled_boot_id = self._boot_id
        return True

    async def _reconcile_prior_tasks(self) -> None:
        records = await asyncio.to_thread(self.broker.remote_reconciliations)
        for record in records:
            if (not isinstance(record, dict)
                    or not isinstance(record.get('task_id'), str)
                    or not isinstance(record.get('owner_id'), str)
                    or not isinstance(record.get('laptop_id'), str)
                    or not isinstance(record.get('broker_boot_id'), str)
                    or type(record.get('fence')) is not int or record['fence'] < 1
                    or record.get('state') not in {'accepted', 'unknown'}
                    or not isinstance(record.get('attempts'), list)
                    or len(record['attempts']) > 64):
                raise ValueError('Local browser reconciliation record is invalid')
            try:
                task_id = str(UUID(record['task_id']))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('Local browser reconciliation task ID is invalid') from exc
            if record['owner_id'] != self.owner_id or record['laptop_id'] != self.laptop_id:
                raise PermissionError('Local browser reconciliation belongs to another owner or laptop')
            if record['broker_boot_id'] == self._boot_id:
                continue
            if record['state'] == 'accepted':
                # Core requeues a pre-start claim on broker boot replacement. No browser
                # executor began, so report no site-attempt evidence for this record.
                if not await asyncio.to_thread(self.broker.mark_remote_reconciled, task_id):
                    raise RuntimeError('The local browser journal could not clear pre-start recovery metadata')
                continue
            attempts = record['attempts'] or [{
                'action_id': 'broker_task', 'action': 'task_status',
                'proposal_digest': None, 'outcome': 'unknown',
            }]
            for attempt in attempts:
                if (not isinstance(attempt, dict)
                        or attempt.keys() != {'action_id', 'action', 'proposal_digest', 'outcome'}
                        or not isinstance(attempt['action_id'], str)
                        or not 1 <= len(attempt['action_id']) <= 128
                        or not isinstance(attempt['action'], str)
                        or attempt['action'] not in {'click', 'fill', 'select', 'submit', 'upload',
                                                     'download', 'task_status'}
                        or (attempt['action'] == 'task_status'
                            and attempt['action_id'] != 'broker_task')
                        or (attempt['action'] != 'task_status'
                            and attempt['action_id'] == 'broker_task')
                        or (attempt['proposal_digest'] is not None
                            and (not isinstance(attempt['proposal_digest'], str)
                                 or len(attempt['proposal_digest']) != 64
                                 or any(char not in '0123456789abcdef' for char in attempt['proposal_digest'])))
                        or not isinstance(attempt['outcome'], str)
                        or attempt['outcome'] not in {'not_attempted', 'attempted', 'unknown'}):
                    raise ValueError('Local browser reconciliation action evidence is invalid')
                payload = {
                    'previousBrokerBootId': record['broker_boot_id'], 'fence': record['fence'],
                    'actionId': attempt['action_id'], 'action': attempt['action'],
                    'proposalDigest': attempt['proposal_digest'], 'outcome': attempt['outcome'],
                }
                if not await self.core.reconcile(task_id, self._boot_id, payload):
                    raise PermissionError('Core did not accept prior browser execution reconciliation')
            if not await asyncio.to_thread(self.broker.mark_remote_reconciled, task_id):
                raise RuntimeError('The local browser journal could not mark reconciliation complete')

    def _descriptor(self) -> dict:
        return {
            'provider': self.provider,
            'scopes': [{'scopeId': item['scope_id'], 'origin': item['origin'],
                        'actions': list(item['actions']), 'version': item['version']}
                       for item in self.scopes],
        }

    async def run_once(self) -> bool:
        await self._ensure_identity()
        availability = await asyncio.to_thread(self.broker.availability)
        if availability['broker_boot_id'] != self._boot_id:
            raise PermissionError('Browser broker boot identity changed')
        busy = availability['busy']
        now = self._monotonic()
        if now - self._last_heartbeat >= self.heartbeat_interval_seconds:
            availability_state = ('disabled' if not self._boot_id else
                                  'busy' if busy else
                                  'online' if self.provider_ready else 'provider_unavailable')
            await self.core.heartbeat(self._boot_id, availability_state, self._descriptor())
            self._last_heartbeat = now
        if self._active is not None:
            return await self._monitor_active(now)
        if busy or not self.provider_ready:
            return False
        request = await self.core.claim(self._boot_id)
        if request is None:
            return False
        binding = request.binding
        if (binding.owner_id != self.owner_id or binding.laptop_id != self.laptop_id
                or binding.broker_boot_id != self._boot_id or request.provider != self.provider):
            await self.core.release(binding)
            raise PermissionError('Core browser claim does not match this laptop, boot, or provider')
        configured = {item['scope_id']: item for item in self.scopes}
        if binding.scope_id != 'public_research':
            scope = configured.get(binding.scope_id)
            if (not self.remote_writes_enabled or scope is None
                    or scope['version'] != binding.scope_version):
                await self.core.release(binding)
                raise PermissionError('Core browser claim selected a scope not enabled locally')
        # Recheck after claiming. If a dashboard/voice task won the race, return only a
        # provably unstarted claim to Core; never queue remote work behind local work.
        availability = await asyncio.to_thread(self.broker.availability)
        if availability['busy']:
            await self.core.release(binding)
            return False
        start = await self.core.task_action(binding, 'start')
        if not isinstance(start, dict) or type(start.get('active')) is not bool:
            raise ValueError('Core browser start response is invalid')
        if not start['active']:
            return False
        try:
            receipt = await asyncio.to_thread(self.broker.submit_remote, request)
        except Exception:
            await self.core.complete(binding, 'unknown',
                                     'The laptop could not confirm whether browser work started.', 0, None)
            raise
        self._active = _ActiveTask(request, receipt, last_renew=now)
        return True

    async def _monitor_active(self, now: float) -> bool:
        active = self._active
        binding = active.request.binding
        if now - active.last_renew >= self.renew_interval_seconds:
            try:
                renewal = await self.core.task_action(binding, 'renew')
                if renewal is None:
                    active.stopping = True
                    await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                else:
                    if (not isinstance(renewal, dict)
                            or renewal.keys() != {'active', 'command', 'decision'}
                            or type(renewal['active']) is not bool
                            or renewal['command'] not in {None, 'pause', 'resume', 'stop'}):
                        raise ValueError('Core browser renewal response is invalid')
                    if renewal['active'] is not True:
                        active.stopping = True
                        await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                    elif renewal.get('command') in {'pause', 'resume', 'stop'}:
                        if renewal['command'] == 'stop':
                            active.stopping = True
                        await asyncio.to_thread(self.broker.remote_control, binding, renewal['command'])
                    decision = renewal['decision'] if renewal['active'] and renewal['command'] != 'stop' else None
                    if decision is not None:
                        if (not isinstance(decision, dict)
                                or decision.keys() != {'actionId', 'proposalDigest', 'approved'}
                                or not isinstance(decision['actionId'], str)
                                or not isinstance(decision['proposalDigest'], str)
                                or type(decision['approved']) is not bool):
                            raise ValueError('Core browser review decision is invalid')
                        decision_key = (decision['actionId'], decision['proposalDigest'], decision['approved'])
                        if decision_key not in active.applied_decisions:
                            await asyncio.to_thread(
                                self.broker.remote_decide, binding, *decision_key,
                            )
                            active.applied_decisions.add(decision_key)
            except Exception:
                # Stop immediately on network uncertainty; broker dispatch checks independently.
                await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                raise
            active.last_renew = now
        page = await asyncio.to_thread(self.broker.remote_events, binding, active.cursor)
        if page.state == 'waiting_for_user' and not active.stopping:
            proposal = getattr(page, 'proposal', None)
            subtype = getattr(page, 'waiting_for_user_type', None) or 'local_takeover'
            proposal_key = proposal['digest'] if proposal is not None else 'local_takeover'
            if (subtype == 'local_takeover' or proposal is not None) and active.published_proposal != proposal_key:
                try:
                    published = await self.core.publish_proposal(binding, proposal, subtype)
                except Exception:
                    await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                    raise
                if not published:
                    await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                    raise PermissionError('Core rejected the current browser review')
                active.published_proposal = proposal_key
        else:
            active.published_proposal = None
        if page.reset_required:
            summary = f'Browser event history reset; current state is {page.state}.'
            events = [{'sequence': page.next_cursor, 'eventType': 'snapshot', 'summary': summary}]
        else:
            events = [{'sequence': event.sequence, 'eventType': event.state,
                       'summary': event.summary[:8192]} for event in page.events]
        if events and not active.stopping and page.state not in {'stopping', 'cancelled'}:
            try:
                published = await self.core.publish_events(binding, events)
            except Exception:
                await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                raise
            if not published:
                active.stopping = True
                await asyncio.to_thread(self.broker.remote_control, binding, 'stop')
                # Revocation can race renewal. Wait for the broker's Stop receipt
                # and send only terminal metadata, never erased page content.
                return True
        active.cursor = page.next_cursor
        if page.state not in _REMOTE_STATES:
            return True
        result = page.result if page.state in {'completed', 'partial'} and not active.stopping else None
        summary = (f'Browser task {page.state}.' if not result else 'Browser task produced a result.')
        if not await self.core.complete(binding, page.state, summary, active.cursor, result):
            raise PermissionError('Core did not acknowledge browser task completion')
        if not await asyncio.to_thread(self.broker.mark_remote_terminal, binding):
            raise RuntimeError('The broker did not durably acknowledge Core task completion')
        self._active = None
        return True

    async def _poll(self):
        while not self._stop.is_set():
            try:
                await self.run_once()
            except PermissionError as exc:
                LOG.warning('Browser task worker fenced or unauthorized (%s)', type(exc).__name__)
                self._stop.set()
            except Exception as exc:
                LOG.warning('Browser task worker request failed (%s)', type(exc).__name__)
            await asyncio.to_thread(self._stop.wait, self.poll_interval_seconds)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='ascend-browser-task-worker', daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._poll())
        except Exception as exc:
            LOG.warning('Browser task worker stopped (%s)', type(exc).__name__)
        finally:
            close = getattr(self.core, 'close', None)
            if callable(close):
                try:
                    asyncio.run(close())
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._active is not None:
            try:
                self.broker.remote_control(self._active.request.binding, 'stop')
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=self.shutdown_timeout_seconds)
            if self._thread.is_alive():
                LOG.warning('Browser task worker did not stop within the configured timeout')


def build_browser_task_worker(browser_config, broker, *, llm_config=None, environ=None, transport=None):
    env = os.environ if environ is None else environ
    if not browser_config.remote_enabled:
        return None
    required = ('ASCEND_PHONE_CORE_URL', 'ASCEND_BROWSER_WORKER_TOKEN',
                'ASCEND_PHONE_OWNER_ID', 'ASCEND_BROWSER_LAPTOP_ID')
    if any(not env.get(key, '').strip() for key in required):
        raise ValueError('remote browser is enabled but its dedicated Core worker credentials are missing')
    core = CoreBrowserClient(env['ASCEND_PHONE_CORE_URL'], env['ASCEND_BROWSER_WORKER_TOKEN'],
                             transport=transport)
    key_env_names = {
        'gemini': getattr(llm_config, 'gemini_api_key_env', 'GEMINI_API_KEY'),
        'cerebras': getattr(llm_config, 'cerebras_api_key_env', 'CEREBRAS_API_KEY'),
        'groq': getattr(llm_config, 'groq_api_key_env', 'GROQ_API_KEY'),
    }
    provider_keys = env.get(key_env_names[browser_config.provider], '').split(',')
    provider_ready = any(key.strip() for key in provider_keys)
    return BrowserTaskWorker(
        core, broker, owner_id=env['ASCEND_PHONE_OWNER_ID'].strip(),
        laptop_id=env['ASCEND_BROWSER_LAPTOP_ID'].strip(), provider=browser_config.provider,
        scopes=browser_config.remote_scopes,
        remote_writes_enabled=browser_config.remote_writes_enabled,
        provider_ready=provider_ready,
    )
