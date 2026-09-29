"""Bounded asynchronous owner-scoped browser task lifecycle."""

from collections import deque
from dataclasses import dataclass, field
import logging
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from browser.authorization import ActionProposal, GrantBook
from browser.contracts import BrowserDecision, BrowserTaskRequest
from browser.executor import BrowserExecutor, BrowserObservation
from browser.remote_contracts import RemoteBrowserBinding, RemoteBrowserTaskRequest


LOG = logging.getLogger(__name__)
EVENTS_PER_TASK = 200
EVENT_PAGE_SIZE = 50
ALLOWED_CONTROLS = {'pause', 'resume', 'stop'}
TERMINAL_STATES = {'completed', 'partial', 'failed', 'cancelled', 'unknown'}
TERMINAL_PAYLOAD_SECONDS = 60 * 60
TOMBSTONE_SECONDS = 24 * 60 * 60
# About ten research tasks per hour across the 25-hour retention window.
# Count every lifecycle state so cleanup and shutdown never scan more than 256.
MAX_RETAINED_TASKS = 256


class TaskNotFound(LookupError):
    """Task does not exist or is not owned by the requesting session."""


class TaskQueueFull(RuntimeError):
    """The pending-task queue or retained-task registry is at capacity."""


@dataclass(frozen=True)
class TaskReceipt:
    task_id: str
    state: str
    event_cursor: int
    result: dict | None = None


@dataclass(frozen=True)
class BrowserTaskEvent:
    task_id: str
    sequence: int
    timestamp: float
    state: str
    summary: str
    proposal: dict | None = None


@dataclass(frozen=True)
class EventPage:
    task_id: str
    state: str
    events: tuple[BrowserTaskEvent, ...]
    next_cursor: int
    reset_required: bool
    result: dict | None
    proposal: dict | None = None
    waiting_for_user_type: str | None = None


@dataclass
class _Task:
    request: BrowserTaskRequest | None
    prepared_routine: object | None = None
    routine_reference: tuple[str, int, str] | None = None
    routine_title: str | None = None
    remote_binding: RemoteBrowserBinding | None = None
    remote_request: RemoteBrowserTaskRequest | None = None
    state: str = 'queued'
    events: deque[BrowserTaskEvent] = field(default_factory=lambda: deque(maxlen=EVENTS_PER_TASK))
    next_sequence: int = 1
    result: dict | None = None
    pause_requested: bool = False
    stop_requested: bool = False
    pending_proposal: ActionProposal | None = None
    authorization_result: bool | None = None
    profile_save_requested: bool = False
    reobserve_requested: bool = False
    created_at: float = field(default_factory=time.monotonic)
    terminal_activity_at: float | None = None
    metrics_run_id: str | None = None
    metric_started_at: float = field(default_factory=time.monotonic)
    submitted_wall_time: float = field(default_factory=time.time)
    provider_reservations: dict = field(default_factory=dict)
    metrics_failed: bool = False
    verifier_verdict: str = 'unknown'


@dataclass(frozen=True)
class _Tombstone:
    task_id: str
    session_key: tuple[str, str, str]
    state: str
    event_cursor: int
    erased_at: float


class BrowserTaskService:
    """Owns one active task and a bounded queue; callers return without waiting for browser work."""

    def __init__(self, executor_factory: Callable[[], object] | None = None,
                 decision_provider: Callable[[BrowserTaskRequest, BrowserObservation], object] | None = None,
                 *, max_decisions: int = 20, max_actions: int = 30, max_pages: int = 3,
                 max_queued_tasks: int = 4, task_timeout_seconds: int = 180,
                 enable_mutations: bool = False,
                 executor_factory_for_task: Callable[[BrowserTaskRequest], object] | None = None,
                 profile_store=None,
                 action_journal=None,
                 file_store=None,
                 remote_enabled: bool = False,
                 remote_writes_enabled: bool = False,
                 remote_scopes=(),
                 remote_dispatch_guard=None,
                 broker_boot_id: str | None = None,
                 remote_provider: str = 'gemini',
                 metrics_store=None,
                 budget_tracker=None,
                 routine_registry=None,
                 routine_installation_budget=None,
                 decision_provider_with_usage=None,
                 clock: Callable[[], float] = time.time,
                 retention_clock: Callable[[], float] = time.monotonic):
        self._executor_factory = executor_factory or BrowserExecutor
        self._executor_factory_for_task = executor_factory_for_task
        self._profile_store = profile_store
        self._action_journal = action_journal
        self._file_store = file_store
        self._remote_enabled = remote_enabled
        self._remote_writes_enabled = remote_writes_enabled
        self._remote_scopes = {item['scope_id']: dict(item) for item in remote_scopes}
        self._remote_dispatch_guard = remote_dispatch_guard
        self.broker_boot_id = broker_boot_id
        self._remote_provider = remote_provider
        self._decision_provider = decision_provider
        self._decision_provider_with_usage = decision_provider_with_usage
        self._metrics = metrics_store
        self._budget_tracker = budget_tracker
        self._routine_registry = routine_registry
        self._routine_installation_budget = routine_installation_budget
        self._max_decisions = max_decisions
        self._max_actions = max_actions
        self._max_pages = max_pages
        self._max_queued_tasks = max_queued_tasks
        self._task_timeout_seconds = task_timeout_seconds
        if type(enable_mutations) is not bool:
            raise ValueError('enable_mutations must be a boolean')
        self._enable_mutations = enable_mutations
        self._clock = clock
        self._grants = GrantBook(clock=clock)
        self._retention_clock = retention_clock
        self._condition = threading.Condition(threading.RLock())
        self._tasks: dict[str, _Task | _Tombstone] = {}
        self._queue: deque[_Task] = deque()
        self._active: _Task | None = None
        self._worker: threading.Thread | None = None
        self._expiry_worker: threading.Thread | None = None
        self._closing = False

    def start(self) -> None:
        with self._condition:
            self._expire_tasks()
            if self._closing:
                raise RuntimeError('browser task service is closed')
            if self._worker is not None:
                return
            self._worker = threading.Thread(target=self._work, name='vision-browser-task', daemon=True)
            self._worker.start()
            self._expiry_worker = threading.Thread(target=self._expire_idle, name='vision-browser-expiry', daemon=True)
            self._expiry_worker.start()

    def submit(self, request: BrowserTaskRequest, *, _prepared_routine=None) -> TaskReceipt:
        if not isinstance(request, BrowserTaskRequest):
            raise TypeError('request must be a validated BrowserTaskRequest')
        if request.expires_at <= self._clock():
            raise ValueError('browser task request has expired')
        with self._condition:
            self._expire_tasks()
            if self._closing:
                raise RuntimeError('browser task service is closed')
            if self._worker is None:
                raise RuntimeError('browser task service has not started')
            existing = self._tasks.get(request.task_id)
            if existing is not None:
                if isinstance(existing, _Task) and existing.request is not None:
                    prior = existing.request
                    same_submission = (
                        prior.session_key == request.session_key
                        and prior.goal == request.goal
                        and prior.provider == request.provider
                        and prior.scope_mode == request.scope_mode
                        and prior.allowed_origins == request.allowed_origins
                        and prior.capabilities == request.capabilities
                        and prior.profile_id == request.profile_id
                        and prior.selected_file_token == request.selected_file_token
                        and self._same_routine_invocation(existing, _prepared_routine)
                    )
                    if same_submission:
                        return self._receipt(existing)
                raise ValueError('task id was already used with a different request or has expired; use a new idempotency key')
            if len(self._tasks) >= MAX_RETAINED_TASKS:
                raise TaskQueueFull('Vision already has the maximum number of retained browser tasks.')
            if len(self._queue) >= self._max_queued_tasks:
                raise TaskQueueFull('Vision already has the maximum number of queued browser tasks.')
            reference = ((_prepared_routine.definition.routine_id, _prepared_routine.definition.version,
                          _prepared_routine.definition.digest) if _prepared_routine is not None else None)
            task = _Task(request, prepared_routine=_prepared_routine, routine_reference=reference,
                         routine_title=_prepared_routine.definition.title if _prepared_routine is not None else None)
            self._tasks[request.task_id] = task
            self._append_event(task, 'queued',
                               f"Routine ‘{task.routine_title}’ accepted." if task.routine_title
                               else 'Browser task accepted.')
            self._queue.append(task)
            self._condition.notify_all()
            return self._receipt(task)

    @staticmethod
    def _same_routine_invocation(existing: _Task, prepared) -> bool:
        if existing.routine_reference is None or prepared is None:
            return existing.routine_reference is None and prepared is None
        if existing.prepared_routine is None:
            # Invocation values were erased at task completion; never replay a used routine ID.
            return False
        return (existing.routine_reference == (
                    prepared.definition.routine_id, prepared.definition.version, prepared.definition.digest)
                and dict(existing.prepared_routine.input_values) == dict(prepared.input_values))

    def submit_routine(self, request: BrowserTaskRequest, invocation) -> TaskReceipt:
        from browser.policy import BrowserPolicy
        from browser.routines.contracts import RoutineInvocation
        if self._routine_registry is None:
            raise PermissionError('Browser routines are disabled on this laptop.')
        if not isinstance(invocation, RoutineInvocation):
            invocation = RoutineInvocation.from_payload(invocation)
        prepared = self._routine_registry.prepare(
            invocation, request, base_policy=BrowserPolicy(),
            installation_budget=self._routine_installation_budget,
        )
        # The broker replaces any caller-provided goal with reviewed static routine text.
        payload = request.to_payload()
        payload['goal'] = prepared.definition.goal_template
        normalized = BrowserTaskRequest.from_payload(payload)
        return self.submit(normalized, _prepared_routine=prepared)

    def routine_catalogue(self) -> tuple[dict, ...]:
        return self._routine_registry.catalogue() if self._routine_registry is not None else ()

    def disable_routine(self, routine_id: str, version: int) -> None:
        if self._routine_registry is None:
            return
        self._routine_registry.disable(routine_id, version)
        finish_queued = []
        with self._condition:
            for task in tuple(self._tasks.values()):
                if (not isinstance(task, _Task) or task.routine_reference is None
                        or task.routine_reference[:2] != (routine_id, version)
                        or task.state in TERMINAL_STATES):
                    continue
                task.stop_requested = True
                self._invalidate_review(task)
                if task in self._queue:
                    self._queue.remove(task)
                    finish_queued.append(task)
                else:
                    self._append_event(task, 'stopping', 'This routine was disabled; no further browser actions will be dispatched.')
            self._condition.notify_all()
        for task in finish_queued:
            self._cancel_task(task)

    def enable_routine(self, routine_id: str, version: int) -> None:
        if self._routine_registry is None:
            raise PermissionError('Browser routines are disabled on this laptop.')
        self._routine_registry.enable(routine_id, version)

    def submit_remote(self, request: RemoteBrowserTaskRequest) -> TaskReceipt:
        """Accept a Core-issued task under a private synthetic session key."""
        if (not self._remote_enabled or self._remote_dispatch_guard is None
                or not isinstance(request, RemoteBrowserTaskRequest)):
            raise PermissionError('Remote browser tasks are disabled or invalid.')
        binding = request.binding
        if (binding.owner_id != getattr(self._remote_dispatch_guard, 'owner_id', None)
                or binding.laptop_id != getattr(self._remote_dispatch_guard, 'laptop_id', None)
                or binding.broker_boot_id != self.broker_boot_id
                or request.provider != self._remote_provider):
            raise PermissionError('Remote task does not match this owner, laptop, boot, or provider.')
        scope_mode = 'public_research'
        origins, capabilities, profile_id = (), (), None
        if binding.scope_id != 'public_research':
            if not self._remote_writes_enabled:
                raise PermissionError('Remote authenticated browser actions are disabled.')
            configured_scope = self._remote_scopes.get(binding.scope_id)
            if configured_scope is None or configured_scope['version'] != binding.scope_version:
                raise PermissionError('Remote browser scope is unavailable or changed.')
            scope_mode = 'selected_origins'
            origins = (configured_scope['origin'],)
            capabilities = tuple(configured_scope['actions'])
            profile_id = configured_scope.get('profile_id')
        if not request.provider_consent:
            raise PermissionError('Provider consent is invalid.')
        local_request = BrowserTaskRequest.from_payload({
            'schema_version': 1,
            'task_id': binding.task_id,
            'session_key': [binding.owner_id, 'dashboard', binding.browser_session_id],
            'goal': request.goal,
            'provider': request.provider,
            'scope_mode': scope_mode,
            'expires_at': request.expires_at,
            'allowed_origins': list(origins),
            'capabilities': list(capabilities),
            'profile_id': profile_id,
        })
        if local_request.expires_at <= self._clock():
            raise ValueError('browser task request has expired')
        if self._action_journal is None:
            raise PermissionError('Remote browser tasks require a durable journal.')
        with self._condition:
            self._expire_tasks()
            if self._closing or self._worker is None:
                raise RuntimeError('browser task service is unavailable')
            existing = self._tasks.get(binding.task_id)
            if existing is not None:
                if (isinstance(existing, _Task) and existing.remote_binding == binding
                        and existing.remote_request == request):
                    return self._receipt(existing)
                raise ValueError('remote task ID was already used with a different request or fence')
            if len(self._tasks) >= MAX_RETAINED_TASKS or len(self._queue) >= self._max_queued_tasks:
                raise TaskQueueFull('The local browser task queue is full.')
            journal_record = self._action_journal.record_remote_task(binding, state='accepted')
            if journal_record['state'] != 'accepted':
                raise PermissionError('This remote task has uncertain or terminal broker history and cannot be replayed.')
            task = _Task(local_request, remote_binding=binding, remote_request=request)
            self._tasks[binding.task_id] = task
            self._append_event(task, 'queued', 'Remote browser task accepted by this laptop.')
            self._queue.append(task)
            self._condition.notify_all()
            return self._receipt(task)

    def remote_events(self, binding: RemoteBrowserBinding, after: int) -> EventPage:
        task = self._owned_remote_task(binding)
        return self.events(binding.task_id, after, task.request.session_key)

    def remote_control(self, binding: RemoteBrowserBinding, command: str) -> TaskReceipt:
        task = self._owned_remote_task(binding)
        return self.control(binding.task_id, command, task.request.session_key)

    def remote_decide(self, binding: RemoteBrowserBinding, action_id: str,
                      proposal_digest: str, approved: bool) -> TaskReceipt:
        task = self._owned_remote_task(binding)
        return self.decide(binding.task_id, action_id, proposal_digest, approved,
                           task.request.session_key)

    def remote_reconciliations(self) -> list[dict]:
        if not self._remote_enabled or self._action_journal is None:
            return []
        return self._action_journal.remote_reconciliations()

    def mark_remote_reconciled(self, task_id: str) -> bool:
        if not self._remote_enabled or self._action_journal is None:
            return False
        return self._action_journal.mark_remote_reconciled(task_id)

    def mark_remote_terminal(self, binding: RemoteBrowserBinding) -> bool:
        if not self._remote_enabled or self._action_journal is None:
            return False
        self._owned_remote_task(binding)
        record = self._action_journal.record_remote_task(binding, state='terminal')
        return record['state'] == 'terminal'

    def _owned_remote_task(self, binding: RemoteBrowserBinding) -> _Task:
        if not self._remote_enabled or not isinstance(binding, RemoteBrowserBinding):
            raise TaskNotFound('Remote browser task was not found.')
        with self._condition:
            task = self._tasks.get(binding.task_id)
            if (not isinstance(task, _Task) or task.remote_binding != binding
                    or task.request is None):
                raise TaskNotFound('Remote browser task was not found.')
            return task

    def events(self, task_id: str, after: int, session_key: tuple[str, str, str]) -> EventPage:
        if type(after) is not int or after < 0:
            raise ValueError('event cursor must be a non-negative integer')
        with self._condition:
            task = self._owned_task(task_id, session_key)
            if isinstance(task, _Tombstone):
                return EventPage(task_id, task.state, (), task.event_cursor, False, None)
            oldest = task.events[0].sequence if task.events else task.next_sequence
            reset = after < oldest - 1
            cursor = oldest - 1 if reset else after
            page = tuple(event for event in task.events if event.sequence > cursor)[:EVENT_PAGE_SIZE]
            next_cursor = page[-1].sequence if page else cursor
            waiting_type = ('action_review' if task.pending_proposal is not None else 'local_takeover') if task.state == 'waiting_for_user' else None
            proposal = (task.pending_proposal.to_payload()
                        if waiting_type == 'action_review' and task.authorization_result is None
                        and task.pending_proposal.expires_at > self._clock() else None)
            return EventPage(task_id, task.state, page, next_cursor, reset, task.result, proposal, waiting_type)

    def feedback(self, task_id: str, verdict: str, session_key: tuple[str, str, str]) -> bool:
        from browser.metrics import OWNER_VERDICTS
        if verdict not in OWNER_VERDICTS - {'not_reviewed'}:
            raise ValueError('feedback verdict is invalid')
        with self._condition:
            task = self._owned_task(task_id, session_key)
            if not isinstance(task, _Task) or task.state not in TERMINAL_STATES:
                raise ValueError('feedback is available only after a task reaches a terminal state')
            run_id = task.metrics_run_id
        if self._metrics is None or run_id is None:
            return False
        return self._metrics.feedback(run_id, verdict)

    def metrics_status(self) -> dict:
        if self._metrics is None:
            return {'available': False, 'enabled': False}
        return {'available': True, 'enabled': self._metrics.enabled}

    def metrics_set_enabled(self, enabled: bool) -> dict:
        if self._metrics is None:
            return {'available': False, 'enabled': False}
        self._metrics.set_enabled(enabled)
        return self.metrics_status()

    def metrics_summary(self, cohort: str) -> dict:
        if self._metrics is None:
            return {'available': False, 'enabled': False, 'summary': None}
        return {'available': True, 'enabled': self._metrics.enabled,
                'summary': self._metrics.summary(cohort) if self._metrics.enabled else None}

    def metrics_export(self, cursor: int = 0) -> dict:
        if self._metrics is None:
            raise ValueError('metrics storage is unavailable')
        return self._metrics.export(cursor=cursor, limit=50)

    def metrics_clear(self) -> dict:
        if self._metrics is None:
            return {'available': False, 'enabled': False}
        self._metrics.clear()
        return {'available': True, 'enabled': False}

    @property
    def busy(self) -> bool:
        """Whether a local or remote task currently owns or waits for the broker."""
        with self._condition:
            return self._active is not None or bool(self._queue)

    def control(self, task_id: str, command: str, session_key: tuple[str, str, str]) -> TaskReceipt:
        if command not in ALLOWED_CONTROLS:
            raise ValueError('control must be pause, resume, or stop')
        with self._condition:
            task = self._owned_task(task_id, session_key)
            if task.state in TERMINAL_STATES:
                return self._receipt(task)
            if command == 'stop':
                task.stop_requested = True
                task.pause_requested = False
                if task in self._queue:
                    self._queue.remove(task)
                    task.state = 'cancelled'
                    task.result = {'status': 'cancelled', 'findings': [], 'sources': [], 'reason': 'Stopped before start.'}
                    self._append_event(task, 'cancelled', 'Browser task stopped before it started.')
                    self._cleanup_selected_file(task)
                else:
                    task.state = 'stopping'
                    self._append_event(task, 'stopping', 'Stop requested; no further browser actions will be dispatched.')
            elif command == 'pause':
                self._invalidate_review(task)
                task.pause_requested = True
                task.state = 'paused'
                self._append_event(task, 'paused', 'Browser task paused before its next action.')
            else:
                task.pause_requested = False
                if task.state in {'paused', 'waiting_for_user'}:
                    self._invalidate_review(task)
                    task.state = 'running'
                    self._append_event(task, 'running', 'Browser task resumed; a fresh observation will be used.')
            self._condition.notify_all()
            return self._receipt(task)

    def _invalidate_review(self, task: _Task) -> None:
        task.reobserve_requested = True
        if task.pending_proposal is not None:
            proposal = task.pending_proposal
            self._grants.reject(proposal.task_id, proposal.action_id, proposal.digest, proposal.owner)
            task.pending_proposal = None
            task.authorization_result = False

    def decide(self, task_id: str, action_id: str, proposal_digest: str, approved: bool,
               session_key: tuple[str, str, str]) -> TaskReceipt:
        if type(approved) is not bool:
            raise ValueError('approved must be a boolean')
        if not isinstance(action_id, str) or not action_id or len(action_id) > 128:
            raise ValueError('action_id is invalid')
        if not isinstance(proposal_digest, str) or len(proposal_digest) != 64:
            raise ValueError('proposal digest is invalid')
        with self._condition:
            task = self._owned_task(task_id, session_key)
            if isinstance(task, _Tombstone) or task.state != 'waiting_for_user':
                raise ValueError('browser task has no pending action review')
            proposal = task.pending_proposal
            owner = task.request.session_key[0]
            if proposal is None or proposal.action_id != action_id or proposal.digest != proposal_digest:
                raise ValueError('the browser action proposal changed; review the latest action')
            if approved:
                grant = self._grants.approve(task_id, action_id, proposal_digest, owner)
                if grant is None:
                    raise ValueError('the browser action proposal expired')
            elif not self._grants.reject(task_id, action_id, proposal_digest, owner):
                raise ValueError('the browser action proposal expired')
            task.authorization_result = approved
            self._condition.notify_all()
            return self._receipt(task)

    def save_profile(self, task_id: str, session_key: tuple[str, str, str]) -> TaskReceipt:
        with self._condition:
            task = self._owned_task(task_id, session_key)
            if isinstance(task, _Tombstone):
                raise TaskNotFound('Browser task was not found.')
            if (not self._enable_mutations or task.request.scope_mode != 'selected_origins'
                    or not task.request.profile_id):
                raise PermissionError('This task has no explicitly selected saved profile.')
            if (task.state not in {'paused', 'waiting_for_user'} or task.pending_proposal is not None
                    or task.authorization_result is not None):
                raise ValueError('Save a profile only during an explicit human takeover.')
            task.profile_save_requested = True
            self._condition.notify_all()
            return self._receipt(task)

    def list_profiles(self, session_key: tuple[str, str, str]) -> list[str]:
        if not self._enable_mutations or self._profile_store is None:
            raise PermissionError('Saved browser profiles are disabled.')
        return self._profile_store.list_profiles(session_key[0])

    def clear_profile(self, profile_id: str, session_key: tuple[str, str, str]) -> bool:
        if not self._enable_mutations or self._profile_store is None:
            raise PermissionError('Saved browser profiles are disabled.')
        with self._condition:
            owner = session_key[0]
            for task in self._tasks.values():
                if (isinstance(task, _Task) and task.request is not None
                        and task.request.session_key[0] == owner
                        and task.request.profile_id == profile_id
                        and task.state not in TERMINAL_STATES):
                    raise ValueError('A browser task is currently using this saved profile.')
            return self._profile_store.clear(owner, profile_id)

    def close(self, timeout_seconds: float = 3.0) -> None:
        join_deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            self._expire_tasks()
            self._closing = True
            for task in list(self._tasks.values()):
                if task.state in TERMINAL_STATES:
                    continue
                task.stop_requested = True
                task.pause_requested = False
                if task in self._queue:
                    self._queue.remove(task)
                    task.state = 'cancelled'
                    task.result = {'status': 'cancelled', 'findings': [], 'sources': [], 'reason': 'Service stopped.'}
                    self._append_event(task, 'cancelled', 'Browser service stopped before the task started.')
                    self._cleanup_selected_file(task)
                elif task is self._active:
                    task.state = 'stopping'
                    self._append_event(task, 'stopping', 'Browser service is stopping.')
            self._expire_tasks(erase_terminal=True)
            self._condition.notify_all()
            worker = self._worker
            expiry_worker = self._expiry_worker if not self._tasks else None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(0.0, join_deadline - time.monotonic()))
        if expiry_worker is not None and expiry_worker is not threading.current_thread():
            expiry_worker.join(timeout=max(0.0, join_deadline - time.monotonic()))

    def _owned_task(self, task_id: str, session_key: tuple[str, str, str]) -> _Task | _Tombstone:
        self._expire_tasks()
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFound('Browser task was not found.')
        owner = task.session_key if isinstance(task, _Tombstone) else task.request.session_key
        if owner != session_key:
            raise TaskNotFound('Browser task was not found.')
        if isinstance(task, _Task) and task.state in TERMINAL_STATES:
            task.terminal_activity_at = self._retention_clock()
        return task

    def _expire_tasks(self, *, erase_terminal: bool = False) -> None:
        """Erase expired payloads under the condition lock, then age out tombstones."""
        now = self._retention_clock()
        for task_id, task in list(self._tasks.items()):
            if isinstance(task, _Tombstone):
                if now >= task.erased_at + TOMBSTONE_SECONDS:
                    del self._tasks[task_id]
            elif (task.terminal_activity_at is not None
                  and (erase_terminal or now >= task.terminal_activity_at + TERMINAL_PAYLOAD_SECONDS)):
                self._tasks[task_id] = _Tombstone(
                    task_id, task.request.session_key, task.state, task.next_sequence - 1, now,
                )
                task.request = None
                task.remote_binding = None
                task.remote_request = None
                task.prepared_routine = None
                task.provider_reservations.clear()
                task.result = None
                task.events.clear()

    def _expire_idle(self) -> None:
        last_metrics_cleanup = time.monotonic()
        with self._condition:
            # Closed services keep only tombstones; still age those out without
            # a future request, but do not make close wait for their lifetime.
            while not self._closing or self._tasks:
                self._expire_tasks()
                if self._metrics is not None and time.monotonic() - last_metrics_cleanup >= 24 * 60 * 60:
                    last_metrics_cleanup = time.monotonic()
                    self._condition.release()
                    try:
                        self._metrics.maintenance()
                    finally:
                        self._condition.acquire()
                if self._closing and not self._tasks:
                    return
                self._condition.wait(timeout=.5)

    def _append_event(self, task: _Task, state: str, summary: str, proposal: dict | None = None) -> None:
        task.state = state
        if state in TERMINAL_STATES:
            task.terminal_activity_at = self._retention_clock()
        event = BrowserTaskEvent(task.request.task_id, task.next_sequence, self._clock(),
                                 state, summary[:500], proposal)
        task.next_sequence += 1
        task.events.append(event)

    @staticmethod
    def _receipt(task: _Task | _Tombstone) -> TaskReceipt:
        if isinstance(task, _Tombstone):
            return TaskReceipt(task.task_id, task.state, task.event_cursor)
        return TaskReceipt(task.request.task_id, task.state, task.next_sequence - 1, task.result)

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closing:
                    self._condition.wait()
                if self._closing and not self._queue:
                    return
                task = self._queue.popleft()
                if task.stop_requested or task.state in TERMINAL_STATES:
                    continue
                self._active = task
                self._append_event(task, 'running', 'Opening a separate temporary browser context.')
            task.metric_started_at = time.monotonic()
            channel = None
            if task.remote_binding is not None:
                channel = 'discord' if task.remote_binding.channel == 'discord_dm' else 'pwa'
            task.metrics_run_id = self._begin_metrics(task, channel_override=channel)
            self._run_task(task)
            with self._condition:
                if self._active is task:
                    self._active = None
                self._condition.notify_all()

    def _run_task(self, task: _Task) -> None:
        if task.remote_binding is not None:
            try:
                record = self._action_journal.record_remote_task(task.remote_binding, state='started')
                if record['state'] != 'started':
                    self._finish(task, 'unknown', 'Prior browser execution is uncertain and was not replayed.', [])
                    return
            except Exception:
                self._finish(task, 'failed', 'The broker could not durably record remote task execution.', [])
                return
        if self._decision_provider is None:
            self._finish(task, 'failed', 'No approved browser decision provider is configured.', [])
            return
        routine = task.prepared_routine
        if routine is not None and self._decision_provider_with_usage is None:
            self._finish(task, 'failed', 'Routine execution needs the policy-aware planner.', [])
            return
        task_timeout = min(self._task_timeout_seconds, routine.budget.seconds) if routine else self._task_timeout_seconds
        max_decisions = min(self._max_decisions, routine.budget.decisions) if routine else self._max_decisions
        max_actions = min(self._max_actions, routine.budget.actions) if routine else self._max_actions
        deadline = min(task.request.expires_at, self._clock() + task_timeout)
        observations: dict[str, BrowserObservation] = {}
        decisions = 0
        actions = 0
        effect_uncertain = False
        try:
            if routine is not None and self._executor_factory_for_task is None:
                raise RuntimeError('Routine execution requires the broker’s policy-aware executor factory.')
            if self._executor_factory_for_task is not None:
                executor_context = (self._executor_factory_for_task(
                    task.request, policy_override=routine.policy, max_pages=routine.budget.pages,
                ) if routine is not None else self._executor_factory_for_task(task.request))
            else:
                executor_context = self._executor_factory()
            with executor_context as executor:
                self._authorize_remote(task, 'observe')
                current = executor.observe(task.request.task_id)
                observations[current.observation_id] = current
                while decisions < max_decisions:
                    if not self._await_dispatch(task, deadline, executor):
                        self._cancel_task(task)
                        return
                    if self._clock() >= deadline:
                        self._finish(task, 'partial', 'The browser task reached its time limit.', [])
                        return
                    with self._condition:
                        refresh = task.reobserve_requested
                        task.reobserve_requested = False
                    if refresh:
                        self._authorize_remote(task, 'observe')
                        current = executor.observe(task.request.task_id)
                        observations[current.observation_id] = current
                    decisions += 1
                    self._authorize_remote(task, 'plan')
                    if self._decision_provider_with_usage is not None:
                        usage_callback = lambda record: self._record_provider_call(task, record)
                        before_call = lambda reservation: self._reserve_provider_call(task, reservation)
                        proposed = self._decision_provider_with_usage(
                            task.request, current, usage_callback=usage_callback,
                            before_call=before_call if self._budget_tracker is not None else None,
                            routine=routine,
                            routine_observations=tuple(observations.values()) if routine is not None else (),
                        )
                    else:
                        proposed = self._decision_provider(task.request, current)
                    decision = proposed if isinstance(proposed, BrowserDecision) else BrowserDecision.from_payload(proposed)
                    if routine is not None and decision.action not in routine.definition.allowed_actions:
                        raise PermissionError('This browser action is outside the selected routine version.')
                    if not self._await_dispatch(task, deadline, executor):
                        self._cancel_task(task)
                        return
                    if task.reobserve_requested:
                        continue
                    if decision.observation_id != current.observation_id:
                        self._authorize_remote(task, 'observe')
                        current = executor.observe(task.request.task_id)
                        observations[current.observation_id] = current
                        continue
                    if decision.action == 'finish':
                        self._authorize_remote(task, 'finish')
                        ids = decision.arguments['source_observation_ids']
                        if any(item not in observations for item in ids):
                            raise ValueError('The proposed finding cites an observation this task did not produce.')
                        sources = list(dict.fromkeys(observations[item].url for item in ids if observations[item].url))
                        if not sources:
                            raise ValueError('The proposed finding has no verified source URL.')
                        result = {
                            'status': 'completed',
                            'findings': [{'text': decision.arguments['finding'], 'sources': sources}],
                            'sources': sources,
                            'unfinished_steps': [],
                        }
                        if routine is not None:
                            result['source_observation_ids'] = ids
                            from browser.routines.verifiers import verify_completion
                            verification = verify_completion(
                                routine.definition.completion_verifier, routine,
                                tuple(observations.values()), result,
                            )
                            task.verifier_verdict = verification.verdict
                            result['verification'] = {
                                'verdict': verification.verdict,
                                'reason_code': verification.reason_code,
                            }
                            if verification.verdict != 'passed':
                                result['status'] = 'partial'
                                self._finish(task, 'partial',
                                             'Routine evidence did not meet its trusted completion checks.', result)
                                return
                        self._finish(task, 'completed', 'Research result was verified against observed source pages.', result)
                        return
                    if decision.action == 'ask_user':
                        with self._condition:
                            self._append_event(task, 'waiting_for_user', decision.arguments['question'])
                            task.pause_requested = True
                        if not self._await_dispatch(task, deadline, executor):
                            self._cancel_task(task)
                            return
                        self._authorize_remote(task, 'observe')
                        current = executor.observe(task.request.task_id)
                        observations[current.observation_id] = current
                        continue
                    reviewed_proposal = None
                    if self._requires_review(decision, current, task.request.scope_mode):
                        if not self._enable_mutations:
                            raise PermissionError('Authenticated browser actions are disabled.')
                        if (task.request.scope_mode != 'selected_origins'
                                or decision.action not in task.request.capabilities):
                            raise PermissionError('This action was not enabled for the owner-selected origins.')
                        proposal = self._proposal(task, current, decision, deadline)
                        if not self._await_authorization(task, proposal, deadline):
                            if task.reobserve_requested and not task.stop_requested and not self._closing:
                                continue
                            if task.stop_requested or self._closing:
                                self._cancel_task(task)
                            else:
                                self._finish(task, 'partial', 'The owner rejected or did not review the proposed browser action.', [])
                            return
                        if not self._grants.consume(proposal, owner=task.request.session_key[0]):
                            raise PermissionError('The one-time browser action grant expired or changed.')
                        reviewed_proposal = proposal
                    if decision.action != 'observe':
                        actions += 1
                        if actions > max_actions:
                            self._finish(task, 'partial', 'The browser task reached its action limit.', [])
                            return
                    if reviewed_proposal is not None and self._action_journal is not None:
                        parsed_origin = urlsplit(current.url)
                        origin = f'{parsed_origin.scheme}://{parsed_origin.netloc}'
                        self._action_journal.begin(
                            task.request.task_id, task.request.session_key[0],
                            reviewed_proposal.action_id, decision.action, origin, reviewed_proposal.digest,
                        )
                    try:
                        self._authorize_remote(
                            task, decision.action,
                            reviewed_proposal.digest if reviewed_proposal is not None else None,
                        )
                        with self._condition:
                            cancelled = task.stop_requested or self._closing
                            invalidated = task.pause_requested or task.reobserve_requested
                        if cancelled or invalidated:
                            if reviewed_proposal is not None and self._action_journal is not None:
                                self._action_journal.finish(reviewed_proposal.action_id, 'failed')
                            if cancelled:
                                self._cancel_task(task)
                                return
                            continue
                    except Exception:
                        if reviewed_proposal is not None and self._action_journal is not None:
                            self._action_journal.finish(reviewed_proposal.action_id, 'failed')
                        raise
                    try:
                        effect_uncertain = reviewed_proposal is not None
                        current = self._execute(executor, task.request.task_id, current, decision)
                    except Exception:
                        if reviewed_proposal is not None and self._action_journal is not None:
                            self._action_journal.finish(reviewed_proposal.action_id, 'unknown')
                        raise
                    if reviewed_proposal is not None and self._action_journal is not None:
                        self._action_journal.finish(reviewed_proposal.action_id, 'observed')
                    effect_uncertain = False
                    observations[current.observation_id] = current
                    download = executor.last_download if decision.action == 'download' else None
                    with self._condition:
                        if task.state not in TERMINAL_STATES:
                            if download:
                                summary = f"Saved {download['filename']} ({download['size']} bytes) in Vision's managed Downloads folder."
                            elif decision.action == 'upload':
                                summary = 'Attached the owner-selected file to the reviewed browser control; website receipt is not verified.'
                            else:
                                summary = f'Observed {current.title or current.url or "the current page"}.'
                            self._append_event(task, 'running', summary)
                self._finish(task, 'partial', 'The browser task reached its decision limit.', [])
        except Exception as exc:
            LOG.info('Browser task failed (%s)', type(exc).__name__)
            if effect_uncertain:
                self._finish(task, 'unknown', 'The reviewed action may have reached the site; it was not retried.', [])
            else:
                self._finish(task, 'failed', 'Vision could not safely complete this browser task.', [])
        finally:
            self._cleanup_selected_file(task)

    def _authorize_remote(self, task: _Task, action: str, proposal_digest: str | None = None) -> None:
        if task.remote_binding is None:
            return
        if self._remote_dispatch_guard is None:
            raise PermissionError('The remote dispatch authority is unavailable.')
        attempt_id = secrets.token_urlsafe(24)
        self._remote_dispatch_guard.authorize(
            task.remote_binding, attempt_id, action, proposal_digest,
        )

    def _cleanup_selected_file(self, task: _Task) -> None:
        if self._file_store is None or task.request is None or not task.request.selected_file_token:
            return
        try:
            self._file_store.cleanup_task(task.request.session_key[0], task.request.task_id)
        except Exception:
            LOG.info('Selected browser upload cleanup failed.')

    @staticmethod
    def _execute(executor, task_id: str, current: BrowserObservation, decision: BrowserDecision):
        args = decision.arguments
        if decision.action == 'navigate':
            return executor.navigate(task_id, args['url'])
        if decision.action == 'click':
            return executor.click(task_id, current.observation_id, args['element_ref'])
        if decision.action == 'fill':
            return executor.fill(task_id, current.observation_id, args['element_ref'], args['value'])
        if decision.action == 'select':
            return executor.select(task_id, current.observation_id, args['element_ref'], args['value'])
        if decision.action == 'upload':
            return executor.upload(task_id, current.observation_id, args['element_ref'])
        if decision.action == 'download':
            return executor.download(task_id, current.observation_id, args['element_ref'])
        if decision.action == 'scroll':
            return executor.scroll(task_id, current.observation_id, args['direction'], args['pixels'])
        if decision.action == 'back':
            return executor.back(task_id, current.observation_id)
        if decision.action == 'wait_for':
            return executor.wait_for(task_id, current.observation_id, args['milliseconds'])
        if decision.action == 'observe':
            return executor.observe(task_id)
        raise PermissionError('This browser capability is not available in public research.')

    @staticmethod
    def _requires_review(decision: BrowserDecision, current: BrowserObservation,
                         scope_mode: str = 'public_research') -> bool:
        if decision.action in {'fill', 'select'}:
            return True
        if decision.action == 'click':
            element = next((item for item in current.elements
                            if item.element_ref == decision.arguments['element_ref']), None)
            return scope_mode == 'selected_origins' or element is None or element.kind != 'page_link'
        return decision.action in {'submit', 'upload', 'download'}

    def _proposal(self, task: _Task, current: BrowserObservation, decision: BrowserDecision,
                  deadline: float) -> ActionProposal:
        args = decision.arguments
        target_ref = args.get('element_ref', '')
        element = next((item for item in current.elements if item.element_ref == target_ref), None)
        if element is None:
            raise PermissionError('The proposed browser action does not target a current observed element.')
        parsed = urlsplit(current.url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        action_arguments = {'value': args['value']} if decision.action in {'fill', 'select'} else {}
        if decision.action == 'upload':
            if self._file_store is None or not task.request.selected_file_token:
                raise PermissionError('No owner-selected upload file is attached to this task.')
            selected = self._file_store.upload(
                task.request.session_key[0], task.request.task_id, task.request.selected_file_token,
            )
            action_arguments = {'filename': selected.filename, 'sha256': selected.sha256}
        effect = decision.expected_result.strip() or (
            f"{decision.action.title()} {element.label or 'the selected control'}"
            + (f" with {args['value']}" if 'value' in args else '')
        )
        return ActionProposal(
            task_id=task.request.task_id,
            action_id=secrets.token_urlsafe(18),
            owner=task.request.session_key[0],
            observation_id=current.observation_id,
            document_revision=current.document_revision,
            origin=origin,
            action=decision.action,
            target_ref=target_ref,
            target_label=element.label,
            arguments=action_arguments,
            expected_effect=effect[:500],
            expires_at=min(deadline, self._clock() + (120 if task.remote_binding is not None else 600)),
        )

    def _await_authorization(self, task: _Task, proposal: ActionProposal, deadline: float) -> bool:
        with self._condition:
            task.pending_proposal = proposal
            task.authorization_result = None
            self._grants.register(proposal)
            self._append_event(task, 'waiting_for_user',
                               'Review and approve or reject this exact browser action.',
                               proposal=proposal.to_payload())
            while (task.authorization_result is None and not task.stop_requested and not self._closing
                   and self._clock() < min(deadline, proposal.expires_at)):
                self._condition.wait(timeout=min(.5, max(0.0, min(deadline, proposal.expires_at) - self._clock())))
            approved = (task.authorization_result is True and not task.stop_requested and not self._closing
                        and not task.pause_requested and not task.reobserve_requested
                        and self._clock() < proposal.expires_at)
            task.pending_proposal = None
            task.authorization_result = None
            return approved

    def _await_dispatch(self, task: _Task, deadline: float, executor) -> bool:
        while True:
            with self._condition:
                if task.stop_requested or self._closing:
                    return False
                if task.profile_save_requested:
                    task.profile_save_requested = False
                    save_profile = True
                else:
                    save_profile = False
                if not save_profile and not task.pause_requested:
                    return True
                if not save_profile:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return True
                    self._condition.wait(timeout=min(remaining, .5))
                    continue
            try:
                executor.save_profile()
                summary = 'Saved the dedicated login profile at the owner’s request.'
            except Exception:
                summary = 'Could not save the dedicated login profile; the current session remains temporary.'
            with self._condition:
                if task.state not in TERMINAL_STATES:
                    self._append_event(task, 'waiting_for_user', summary)

    def _cancel_task(self, task: _Task) -> None:
        result = {'status': 'cancelled', 'findings': [], 'sources': [], 'reason': 'Stopped before another action.'}
        self._finish(task, 'cancelled', 'Browser task cancelled; no further actions were dispatched.', result)

    def _finish(self, task: _Task, state: str, summary: str, result) -> None:
        with self._condition:
            if task.state in TERMINAL_STATES:
                return
            task.result = result if isinstance(result, dict) else {
                'status': state, 'findings': [], 'sources': [], 'reason': summary,
            }
            self._append_event(task, state, summary)
            task.prepared_routine = None
            task.provider_reservations.clear()
            metric_record = None
            if self._metrics is not None and task.metrics_run_id is not None:
                metric_record = (task.metrics_run_id, state, self._metric_reason(state, summary),
                                 max(0, round((self._retention_clock() - task.metric_started_at) * 1_000)),
                                 max(0, round((task.metric_started_at - task.created_at) * 1_000)))
            if self._closing:
                self._expire_tasks(erase_terminal=True)
            self._condition.notify_all()
        if metric_record is not None:
            from browser.metrics import RunOutcome
            run_id, final_state, reason_code, duration_ms, queue_ms = metric_record
            self._metrics.finish(run_id, RunOutcome(
                state=final_state, reason_code=reason_code, verifier_verdict=task.verifier_verdict,
                duration_ms=duration_ms, queue_ms=queue_ms, unknown_effect=final_state == 'unknown',
            ))
            if self._budget_tracker is not None:
                self._budget_tracker.forget(run_id)

    def _begin_metrics(self, task: _Task, *, channel_override: str | None = None) -> str | None:
        if self._metrics is None or task.request is None:
            return None
        from browser.metrics import RunStart
        request = task.request
        channel = channel_override or ('local_voice' if request.session_key[1] == 'voice' else 'local_dashboard')
        cohort = ('signed_in' if request.scope_mode == 'selected_origins'
                  else {'pwa': 'pwa', 'discord': 'discord'}.get(channel, 'live_public'))
        routine_id, routine_version, routine_digest = (
            task.routine_reference if task.routine_reference is not None else ('ad_hoc', None, None)
        )
        try:
            return self._metrics.begin(RunStart(channel=channel, cohort=cohort, provider=request.provider,
                                                model='unknown', routine_id=routine_id,
                                                routine_version=routine_version, routine_digest=routine_digest,
                                                started_at=task.submitted_wall_time))
        except Exception:
            return None

    def _reserve_provider_call(self, task: _Task, request: dict) -> None:
        from browser.budgets import PricingUnavailable
        if self._budget_tracker is None:
            return
        if (self._metrics is None or task.metrics_run_id is None or not self._metrics.enabled):
            raise PricingUnavailable('the B5 pilot requires local metrics to remain enabled')
        if task.metrics_failed:
            raise PricingUnavailable('a prior browser provider attempt could not be recorded')
        prompt, system_prompt = request.get('prompt'), request.get('system_prompt')
        if not isinstance(prompt, str) or not isinstance(system_prompt, str):
            raise PricingUnavailable('a bounded provider token reservation could not be calculated')
        # UTF-8 bytes are used as an intentionally conservative upper bound, not as an exact token count.
        input_bound = len(prompt.encode('utf-8')) + len(system_prompt.encode('utf-8'))
        reservation = self._budget_tracker.reserve(
            task.metrics_run_id, request['provider'], request['model'],
            conservative_input_tokens=input_bound,
            max_output_tokens=request['max_output_tokens'],
            attempt_id=request['attempt_id'],
        )
        task.provider_reservations[request['attempt_id']] = reservation

    def _record_provider_call(self, task: _Task, record) -> None:
        reservation = task.provider_reservations.pop(record.attempt_id, None)
        if reservation is not None and self._budget_tracker is not None:
            price = None
            actual_model = record.model_actual or record.model_requested
            if (actual_model == reservation.model and record.input_tokens is not None
                    and record.output_tokens is not None):
                try:
                    price = self._budget_tracker.price_table.estimate_micros(
                        record.provider, actual_model, record.input_tokens, record.output_tokens,
                    )
                except Exception:
                    price = None
            self._budget_tracker.settle(reservation, actual_cost_usd_micros=price)
            from dataclasses import replace
            record = replace(record, price_table_version=reservation.price_table_version,
                             estimated_cost_usd_micros=price,
                             reserved_cost_usd_micros=reservation.cost_usd_micros)
        if self._metrics is not None:
            if not self._metrics.record_call(task.metrics_run_id, record):
                task.metrics_failed = True

    @staticmethod
    def _metric_reason(state: str, summary: str) -> str:
        # Event summaries can contain page titles and other task data; persist fixed reason codes only.
        if state == 'completed':
            return 'finished'
        if state == 'cancelled':
            return 'stopped'
        if state == 'partial':
            if 'time limit' in summary.lower():
                return 'time_limit'
            if 'review' in summary.lower() or 'owner' in summary.lower():
                return 'review_interrupted'
            return 'partial'
        if state == 'unknown':
            return 'uncertain_effect'
        if state == 'failed':
            return 'provider_or_browser_error'
        return 'other'
