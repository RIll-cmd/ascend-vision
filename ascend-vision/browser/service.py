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


@dataclass
class _Task:
    request: BrowserTaskRequest | None
    state: str = 'queued'
    events: deque[BrowserTaskEvent] = field(default_factory=lambda: deque(maxlen=EVENTS_PER_TASK))
    next_sequence: int = 1
    result: dict | None = None
    pause_requested: bool = False
    stop_requested: bool = False
    pending_proposal: ActionProposal | None = None
    authorization_result: bool | None = None
    created_at: float = field(default_factory=time.monotonic)
    terminal_activity_at: float | None = None


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
                 clock: Callable[[], float] = time.time,
                 retention_clock: Callable[[], float] = time.monotonic):
        self._executor_factory = executor_factory or BrowserExecutor
        self._decision_provider = decision_provider
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

    def submit(self, request: BrowserTaskRequest) -> TaskReceipt:
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
            if request.task_id in self._tasks:
                raise ValueError('task_id has already been used')
            if len(self._tasks) >= MAX_RETAINED_TASKS:
                raise TaskQueueFull('Vision already has the maximum number of retained browser tasks.')
            if len(self._queue) >= self._max_queued_tasks:
                raise TaskQueueFull('Vision already has the maximum number of queued browser tasks.')
            task = _Task(request)
            self._tasks[request.task_id] = task
            self._append_event(task, 'queued', 'Browser task accepted.')
            self._queue.append(task)
            self._condition.notify_all()
            return self._receipt(task)

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
            return EventPage(task_id, task.state, page, next_cursor, reset, task.result)

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
                else:
                    task.state = 'stopping'
                    self._append_event(task, 'stopping', 'Stop requested; no further browser actions will be dispatched.')
            elif command == 'pause':
                task.pause_requested = True
                task.state = 'paused'
                self._append_event(task, 'paused', 'Browser task paused before its next action.')
            else:
                task.pause_requested = False
                if task.state in {'paused', 'waiting_for_user'}:
                    task.state = 'running'
                    self._append_event(task, 'running', 'Browser task resumed; a fresh observation will be used.')
            self._condition.notify_all()
            return self._receipt(task)

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
                task.result = None
                task.events.clear()

    def _expire_idle(self) -> None:
        with self._condition:
            # Closed services keep only tombstones; still age those out without
            # a future request, but do not make close wait for their lifetime.
            while not self._closing or self._tasks:
                self._expire_tasks()
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
            self._run_task(task)
            with self._condition:
                if self._active is task:
                    self._active = None
                self._condition.notify_all()

    def _run_task(self, task: _Task) -> None:
        if self._decision_provider is None:
            self._finish(task, 'failed', 'No approved browser decision provider is configured.', [])
            return
        deadline = min(task.request.expires_at, self._clock() + self._task_timeout_seconds)
        observations: dict[str, BrowserObservation] = {}
        decisions = 0
        actions = 0
        try:
            with self._executor_factory() as executor:
                current = executor.observe(task.request.task_id)
                observations[current.observation_id] = current
                while decisions < self._max_decisions:
                    if not self._await_dispatch(task, deadline):
                        self._cancel_task(task)
                        return
                    if self._clock() >= deadline:
                        self._finish(task, 'partial', 'The browser task reached its time limit.', [])
                        return
                    decisions += 1
                    proposed = self._decision_provider(task.request, current)
                    decision = proposed if isinstance(proposed, BrowserDecision) else BrowserDecision.from_payload(proposed)
                    if not self._await_dispatch(task, deadline):
                        self._cancel_task(task)
                        return
                    if decision.observation_id != current.observation_id:
                        current = executor.observe(task.request.task_id)
                        observations[current.observation_id] = current
                        continue
                    if decision.action == 'finish':
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
                        self._finish(task, 'completed', 'Research result was verified against observed source pages.', result)
                        return
                    if decision.action == 'ask_user':
                        with self._condition:
                            self._append_event(task, 'waiting_for_user', decision.arguments['question'])
                            task.pause_requested = True
                        if not self._await_dispatch(task, deadline):
                            self._cancel_task(task)
                            return
                        current = executor.observe(task.request.task_id)
                        observations[current.observation_id] = current
                        continue
                    if self._requires_review(decision, current):
                        if not self._enable_mutations:
                            raise PermissionError('Authenticated browser actions are disabled.')
                        proposal = self._proposal(task, current, decision, deadline)
                        if not self._await_authorization(task, proposal, deadline):
                            if task.stop_requested or self._closing:
                                self._cancel_task(task)
                            else:
                                self._finish(task, 'partial', 'The owner rejected or did not review the proposed browser action.', [])
                            return
                        if not self._grants.consume(proposal, owner=task.request.session_key[0]):
                            raise PermissionError('The one-time browser action grant expired or changed.')
                    if decision.action != 'observe':
                        actions += 1
                        if actions > self._max_actions:
                            self._finish(task, 'partial', 'The browser task reached its action limit.', [])
                            return
                    current = self._execute(executor, task.request.task_id, current, decision)
                    observations[current.observation_id] = current
                    with self._condition:
                        if task.state not in TERMINAL_STATES:
                            self._append_event(task, 'running', f'Observed {current.title or current.url or "the current page"}.')
                self._finish(task, 'partial', 'The browser task reached its decision limit.', [])
        except Exception as exc:
            LOG.info('Browser task failed (%s)', type(exc).__name__)
            self._finish(task, 'failed', 'Vision could not safely complete this browser task.', [])

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
    def _requires_review(decision: BrowserDecision, current: BrowserObservation) -> bool:
        if decision.action in {'fill', 'select'}:
            return True
        if decision.action == 'click':
            element = next((item for item in current.elements
                            if item.element_ref == decision.arguments['element_ref']), None)
            return element is None or element.kind != 'page_link'
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
            expires_at=min(deadline, self._clock() + 600),
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
                   and self._clock() < deadline):
                self._condition.wait(timeout=min(.5, max(0.0, deadline - self._clock())))
            approved = task.authorization_result is True and not task.stop_requested and not self._closing
            task.pending_proposal = None
            task.authorization_result = None
            return approved

    def _await_dispatch(self, task: _Task, deadline: float) -> bool:
        with self._condition:
            while task.pause_requested and not task.stop_requested and not self._closing:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return True
                self._condition.wait(timeout=min(remaining, .5))
            return not task.stop_requested and not self._closing

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
            if self._closing:
                self._expire_tasks(erase_terminal=True)
            self._condition.notify_all()
