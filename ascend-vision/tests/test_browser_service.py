from contextlib import AbstractContextManager
from dataclasses import replace
import threading
import time
import weakref

import pytest

from browser.contracts import BrowserTaskRequest
from browser.executor import BrowserElement, BrowserObservation
from browser.service import BrowserTaskService, TaskNotFound, TaskQueueFull


def request(task_id='task-1', owner='owner-1', *, scope_mode='public_research',
            allowed_origins=None, capabilities=None, profile_id=None, selected_file_token=None):
    payload = {
        'schema_version': 1,
        'task_id': task_id,
        'session_key': [owner, 'dashboard', 'session-1'],
        'goal': 'Find official documentation',
        'provider': 'gemini',
        'scope_mode': scope_mode,
        'expires_at': time.time() + 60,
    }
    if allowed_origins is not None:
        payload['allowed_origins'] = allowed_origins
    if capabilities is not None:
        payload['capabilities'] = capabilities
    if profile_id is not None:
        payload['profile_id'] = profile_id
    if selected_file_token is not None:
        payload['selected_file_token'] = selected_file_token
    return BrowserTaskRequest.from_payload(payload)


def observation(task_id, observation_id='obs-1', url='https://example.org/docs', elements=()):
    return BrowserObservation(task_id, 'page-1', 1, observation_id,
                              '2026-09-28T00:00:00+00:00', url,
                              'Example', 'Official documentation text.', tuple(elements), False)


class ScriptedExecutor(AbstractContextManager):
    def __init__(self):
        self.calls = []
        self.observe_calls = 0
        self.current = None
        self.profile_saves = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def observe(self, task_id):
        self.observe_calls += 1
        self.current = observation(task_id, elements=(
            BrowserElement('field-1', 'textbox', 'Message', 'input'),
            BrowserElement('send-1', 'button', 'Send message', 'button'),
        ))
        return self.current

    def navigate(self, task_id, url):
        self.calls.append(('navigate', url))
        return observation(task_id, 'obs-2', url)

    def fill(self, task_id, observation_id, element_ref, value):
        self.calls.append(('fill', element_ref, value))
        return observation(task_id, 'obs-2')

    def click(self, task_id, observation_id, element_ref):
        self.calls.append(('click', element_ref))
        return observation(task_id, 'obs-2')

    def upload(self, task_id, observation_id, element_ref):
        self.calls.append(('upload', element_ref))
        return observation(task_id, 'obs-2')

    def save_profile(self):
        self.profile_saves += 1

    def close(self):
        pass


def wait_for_state(service, task_id, owner, wanted):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        receipt = service.events(task_id, 0, (owner, 'dashboard', 'session-1'))
        if receipt.state in wanted:
            return receipt
        time.sleep(.01)
    pytest.fail(f'task did not reach state {wanted}')


def test_service_finishes_only_from_current_observation_evidence():
    executor = ScriptedExecutor()
    decisions = iter([
        {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'navigate',
         'arguments': {'url': 'https://example.org/docs'}},
        {'schema_version': 1, 'observation_id': 'obs-2', 'action': 'finish',
         'arguments': {'finding': 'The official docs explain the option.',
                       'source_observation_ids': ['obs-2']}},
    ])
    service = BrowserTaskService(lambda: executor, lambda _request, _observation: next(decisions))
    try:
        service.start()
        receipt = service.submit(request())
        done = wait_for_state(service, receipt.task_id, 'owner-1', {'completed', 'failed'})

        assert done.state == 'completed'
        assert done.result['findings'][0]['text'] == 'The official docs explain the option.'
        assert done.result['findings'][0]['sources'] == ['https://example.org/docs']
        assert executor.calls == [('navigate', 'https://example.org/docs')]
    finally:
        service.close()


def test_service_rejects_cross_owner_status_and_control_requests():
    service = BrowserTaskService(lambda: ScriptedExecutor(), lambda *_args: None)
    try:
        service.start()
        receipt = service.submit(request())
        with pytest.raises(TaskNotFound):
            service.events(receipt.task_id, 0, ('other-owner', 'dashboard', 'session-1'))
        with pytest.raises(TaskNotFound):
            service.control(receipt.task_id, 'stop', ('other-owner', 'dashboard', 'session-1'))
    finally:
        service.close()


def test_duplicate_task_submission_is_idempotent_but_changed_payload_is_rejected():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def decide(*_args):
        calls.append('decision')
        entered.set()
        release.wait(timeout=2)
        return {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'ask_user',
                'arguments': {'question': 'Take over.'}}

    service = BrowserTaskService(lambda: ScriptedExecutor(), decide)
    try:
        service.start()
        original = request()
        first = service.submit(original)
        assert entered.wait(timeout=1)
        duplicate = service.submit(replace(original, expires_at=original.expires_at + 10))
        assert duplicate.task_id == first.task_id
        assert calls == ['decision']
        with pytest.raises(ValueError, match='different request'):
            service.submit(replace(original, goal='A different task'))
    finally:
        release.set()
        service.close()


def test_service_stop_prevents_a_late_model_decision_from_dispatching():
    started = threading.Event()
    release = threading.Event()
    executor = ScriptedExecutor()

    def decide(_request, _observation):
        started.set()
        release.wait(timeout=2)
        return {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'navigate',
                'arguments': {'url': 'https://example.org/docs'}}

    service = BrowserTaskService(lambda: executor, decide)
    try:
        service.start()
        receipt = service.submit(request())
        assert started.wait(timeout=1)
        ack = service.control(receipt.task_id, 'stop', ('owner-1', 'dashboard', 'session-1'))
        assert ack.state == 'stopping'
        release.set()
        done = wait_for_state(service, receipt.task_id, 'owner-1', {'cancelled', 'failed'})
        assert done.state == 'cancelled'
        assert executor.calls == []
    finally:
        release.set()
        service.close()


def test_service_holds_a_form_edit_until_owner_approves_the_exact_proposal(tmp_path):
    from browser.journal import BrowserActionJournal

    executor = ScriptedExecutor()
    decisions = iter([
        {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'fill',
         'arguments': {'element_ref': 'field-1', 'value': 'Hello there'},
         'expected_result': 'Prepare the message.'},
        {'schema_version': 1, 'observation_id': 'obs-2', 'action': 'finish',
         'arguments': {'finding': 'The message was prepared.',
                       'source_observation_ids': ['obs-2']}},
    ])
    service = BrowserTaskService(
        lambda: executor,
        lambda _request, _observation: next(decisions),
        enable_mutations=True,
        action_journal=BrowserActionJournal(tmp_path / 'actions.sqlite3'),
    )
    try:
        service.start()
        receipt = service.submit(request(scope_mode='selected_origins',
                                        allowed_origins=['https://example.org'],
                                        capabilities=['fill']))
        pending = wait_for_state(service, receipt.task_id, 'owner-1', {'waiting_for_user'})
        event = next(item for item in pending.events if item.proposal is not None)

        assert event.proposal['action'] == 'fill'
        assert event.proposal['target_label'] == 'Message'
        assert event.proposal['arguments'] == {'value': 'Hello there'}
        assert getattr(pending, 'proposal', None) == event.proposal
        assert getattr(pending, 'waiting_for_user_type', None) == 'action_review'
        assert executor.calls == []
        with pytest.raises(TaskNotFound):
            service.decide(receipt.task_id, event.proposal['action_id'], event.proposal['digest'], True,
                           ('other-owner', 'dashboard', 'session-1'))

        service.decide(receipt.task_id, event.proposal['action_id'], event.proposal['digest'], True,
                       ('owner-1', 'dashboard', 'session-1'))
        done = wait_for_state(service, receipt.task_id, 'owner-1', {'completed', 'failed'})
        assert done.state == 'completed'
        assert executor.calls == [('fill', 'field-1', 'Hello there')]
        row = next((BrowserActionJournal(tmp_path / 'actions.sqlite3').get(item.proposal['action_id'])
                    for item in pending.events if item.proposal is not None), None)
        assert row['state'] == 'observed'
    finally:
        service.close()


def test_upload_grant_binds_the_explicitly_selected_file_and_target(tmp_path):
    from io import BytesIO
    from browser.files import BrowserTaskFiles

    files = BrowserTaskFiles(tmp_path)
    token, _ = files.stage_upload('owner-1', 'task-upload', 'report.txt', BytesIO(b'approved content'))
    executor = ScriptedExecutor()
    decisions = iter([
        {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'upload',
         'arguments': {'element_ref': 'field-1'}},
        {'schema_version': 1, 'observation_id': 'obs-2', 'action': 'finish',
         'arguments': {'finding': 'The selected report is attached.', 'source_observation_ids': ['obs-2']}},
    ])
    service = BrowserTaskService(lambda: executor, lambda *_args: next(decisions),
                                 enable_mutations=True, file_store=files)
    try:
        service.start()
        receipt = service.submit(request(
            task_id='task-upload', scope_mode='selected_origins',
            allowed_origins=['https://example.org'], capabilities=['upload'],
            selected_file_token=token,
        ))
        pending = wait_for_state(service, receipt.task_id, 'owner-1', {'waiting_for_user'})
        proposal = next(event.proposal for event in pending.events if event.proposal)
        assert proposal['arguments']['filename'] == 'report.txt'
        assert proposal['arguments']['sha256'] == files.upload('owner-1', 'task-upload', token).sha256
        assert executor.calls == []
        service.decide(receipt.task_id, proposal['action_id'], proposal['digest'], True,
                       ('owner-1', 'dashboard', 'session-1'))
        done = wait_for_state(service, receipt.task_id, 'owner-1', {'completed', 'failed'})
        assert done.state == 'completed'
        assert executor.calls == [('upload', 'field-1')]
    finally:
        service.close()


def test_authenticated_page_link_click_is_reviewed_as_a_potential_write():
    from browser.contracts import BrowserDecision
    link = BrowserElement('link-1', 'link', 'Manage account', 'page_link')
    current = observation('task-1', elements=(link,))
    decision = BrowserDecision.from_payload({
        'schema_version': 1, 'observation_id': 'obs-1', 'action': 'click',
        'arguments': {'element_ref': 'link-1'},
    })

    assert BrowserTaskService._requires_review(decision, current, 'selected_origins') is True
    assert BrowserTaskService._requires_review(decision, current, 'public_research') is False


def test_owner_can_save_a_dedicated_profile_only_during_human_takeover():
    executor = ScriptedExecutor()
    decisions = iter([
        {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'ask_user',
         'arguments': {'question': 'Please sign in to the selected site, then resume.'}},
        {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'finish',
         'arguments': {'finding': 'Signed-in page is ready.', 'source_observation_ids': ['obs-1']}},
    ])
    service = BrowserTaskService(lambda: executor, lambda *_args: next(decisions), enable_mutations=True)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        receipt = service.submit(request(
            scope_mode='selected_origins', allowed_origins=['https://example.org'],
            profile_id='work-account',
        ))
        wait_for_state(service, receipt.task_id, 'owner-1', {'waiting_for_user'})
        with pytest.raises(TaskNotFound):
            service.save_profile(receipt.task_id, ('other-owner', 'dashboard', 'session-1'))

        service.save_profile(receipt.task_id, key)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            page = service.events(receipt.task_id, 0, key)
            if any('profile' in event.summary.lower() and 'saved' in event.summary.lower() for event in page.events):
                break
            time.sleep(.01)
        assert executor.profile_saves == 1
        service.control(receipt.task_id, 'resume', key)
        done = wait_for_state(service, receipt.task_id, 'owner-1', {'completed', 'failed'})
        assert done.state == 'completed'
    finally:
        service.close()


def test_profile_clear_is_owner_scoped_and_refuses_a_profile_in_use(tmp_path):
    from browser.profile_store import BrowserProfileStore

    store = BrowserProfileStore(tmp_path, protect=lambda value: value, unprotect=lambda value: value)
    store.save('owner-1', 'work-account', {'cookies': []})
    store.save('other-owner', 'work-account', {'cookies': ['other']})
    service = BrowserTaskService(lambda: ScriptedExecutor(), lambda *_args: None,
                                 enable_mutations=True, profile_store=store)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        assert service.clear_profile('work-account', ('other-owner', 'dashboard', 'session-1')) is True
        assert store.load('owner-1', 'work-account') == {'cookies': []}
        assert service.clear_profile('work-account', key) is True
        with pytest.raises(FileNotFoundError):
            store.load('owner-1', 'work-account')
    finally:
        service.close()


def test_profile_cannot_be_cleared_while_an_authenticated_task_is_using_it(tmp_path):
    from browser.profile_store import BrowserProfileStore
    store = BrowserProfileStore(tmp_path, protect=lambda value: value, unprotect=lambda value: value)
    store.save('owner-1', 'work-account', {'cookies': []})
    entered = threading.Event()
    release = threading.Event()

    def decide(*_args):
        entered.set()
        release.wait(timeout=2)
        return {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'ask_user',
                'arguments': {'question': 'Sign in or continue.'}}

    service = BrowserTaskService(lambda: ScriptedExecutor(), decide,
                                 enable_mutations=True, profile_store=store)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        receipt = service.submit(request(scope_mode='selected_origins',
                                        allowed_origins=['https://example.org'],
                                        profile_id='work-account'))
        assert entered.wait(timeout=1)
        with pytest.raises(ValueError, match='currently using'):
            service.clear_profile('work-account', key)
        assert store.load('owner-1', 'work-account') == {'cookies': []}
        service.control(receipt.task_id, 'stop', key)
    finally:
        release.set()
        service.close()


def test_service_rejects_overloaded_queue_without_losing_existing_tasks():
    gate = threading.Event()
    entered = threading.Event()

    def decide(*_args):
        entered.set()
        gate.wait(timeout=2)
        return None

    service = BrowserTaskService(lambda: ScriptedExecutor(), decide, max_queued_tasks=1)
    try:
        service.start()
        first = service.submit(request('task-1'))
        assert entered.wait(timeout=1)
        second = service.submit(request('task-2'))
        with pytest.raises(TaskQueueFull):
            service.submit(request('task-3'))
        assert service.events(first.task_id, 0, ('owner-1', 'dashboard', 'session-1')).state == 'running'
        assert service.events(second.task_id, 0, ('owner-1', 'dashboard', 'session-1')).state == 'queued'
    finally:
        gate.set()
        service.close()


class ManualClock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def completed_service(clock):
    service = BrowserTaskService(
        lambda: ScriptedExecutor(),
        lambda *_args: {
            'schema_version': 1, 'observation_id': 'obs-1', 'action': 'finish',
            'arguments': {'finding': 'Private research result.',
                          'source_observation_ids': ['obs-1']},
        },
        clock=clock,
        retention_clock=clock,
    )
    service.start()
    service.submit(request())
    done = wait_for_state(service, 'task-1', 'owner-1', {'completed'})
    return service, done


def test_terminal_payload_expires_after_one_hour_of_inactivity():
    clock = ManualClock()
    service, done = completed_service(clock)
    try:
        assert done.result['findings'][0]['text'] == 'Private research result.'
        clock.advance(3600)
        expired = service.events('task-1', 0, ('owner-1', 'dashboard', 'session-1'))
        assert expired.state == 'completed'
        assert expired.result is None
        assert expired.events == ()
        assert expired.next_cursor == done.next_cursor
        ack = service.control('task-1', 'stop', ('owner-1', 'dashboard', 'session-1'))
        assert ack.state == 'completed'
        assert ack.result is None
        assert ack.event_cursor == done.next_cursor
    finally:
        service.close()


@pytest.mark.parametrize('activity', ['events', 'control'])
def test_terminal_activity_renews_payload_inactivity_deadline(activity):
    clock = ManualClock()
    service, _done = completed_service(clock)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        clock.advance(3599)
        if activity == 'events':
            assert service.events('task-1', 0, key).result is not None
        else:
            assert service.control('task-1', 'pause', key).result is not None
        clock.advance(3599)
        assert service.events('task-1', 0, key).result is not None
        clock.advance(3600)
        assert service.events('task-1', 0, key).result is None
    finally:
        service.close()


@pytest.mark.parametrize('key', [
    ('other-owner', 'dashboard', 'session-1'),
    ('owner-1', 'voice', 'session-1'),
    ('owner-1', 'dashboard', 'other-session'),
])
def test_terminal_payload_and_tombstone_require_complete_owner_tuple(key):
    clock = ManualClock()
    service, _done = completed_service(clock)
    try:
        clock.advance(3599)
        for command in ('events', 'control'):
            with pytest.raises(TaskNotFound, match='^Browser task was not found\\.$'):
                if command == 'events':
                    service.events('task-1', 0, key)
                else:
                    service.control('task-1', 'stop', key)
        clock.advance(1)
        assert service.events('task-1', 0, ('owner-1', 'dashboard', 'session-1')).result is None
        with pytest.raises(TaskNotFound):
            service.events('task-1', 0, key)
        with pytest.raises(TaskNotFound):
            service.control('task-1', 'stop', key)
    finally:
        service.close()


def test_tombstone_expires_24_hours_after_erasure_despite_status_activity():
    clock = ManualClock()
    service, done = completed_service(clock)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        clock.advance(3600)
        assert service.events('task-1', 0, key).result is None
        clock.advance(86400 - 1)
        assert service.events('task-1', done.next_cursor + 100, key).next_cursor == done.next_cursor
        assert service.control('task-1', 'resume', key).state == 'completed'
        clock.advance(1)
        with pytest.raises(TaskNotFound):
            service.events('task-1', 0, key)
        with pytest.raises(TaskNotFound):
            service.control('task-1', 'stop', key)
    finally:
        service.close()


def test_idle_service_erases_request_and_event_payload_without_status_request():
    clock = ManualClock()
    service = BrowserTaskService(clock=clock, retention_clock=clock)
    task_request = request()
    request_reference = weakref.ref(task_request)
    try:
        service.start()
        service.submit(task_request)
        del task_request
        done = wait_for_state(service, 'task-1', 'owner-1', {'failed'})
        event_references = [weakref.ref(event) for event in done.events]
        del done
        clock.advance(3600)
        deadline = time.monotonic() + 2
        while request_reference() is not None and time.monotonic() < deadline:
            time.sleep(.01)
        assert request_reference() is None
        assert all(reference() is None for reference in event_references)
    finally:
        service.close()


def test_close_erases_terminal_payload_without_waiting_for_retention():
    clock = ManualClock()
    service = BrowserTaskService(clock=clock, retention_clock=clock)
    task_request = request()
    request_reference = weakref.ref(task_request)
    try:
        service.start()
        service.submit(task_request)
        del task_request
        done = wait_for_state(service, 'task-1', 'owner-1', {'failed'})
        cursor = done.next_cursor
        del done
        service.close()
        assert request_reference() is None
        expired = service.events('task-1', 0, ('owner-1', 'dashboard', 'session-1'))
        assert expired.state == 'failed'
        assert expired.next_cursor == cursor
        assert expired.result is None
        assert expired.events == ()
    finally:
        service.close()


def test_terminal_expiry_preserves_active_and_queued_tasks():
    clock = ManualClock()
    entered = threading.Event()
    release = threading.Event()

    def decide(*_args):
        entered.set()
        release.wait(timeout=2)
        return None

    service = BrowserTaskService(lambda: ScriptedExecutor(), decide, clock=clock, retention_clock=clock)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        service.submit(request('active'))
        assert entered.wait(timeout=1)
        service.submit(request('queued'))
        clock.advance(3600)
        assert service.events('active', 0, key).state == 'running'
        assert service.events('queued', 0, key).state == 'queued'
        stopped = service.control('queued', 'stop', key)
        assert stopped.state == 'cancelled'
        assert stopped.result['reason'] == 'Stopped before start.'
        clock.advance(3600)
        expired = service.control('queued', 'stop', key)
        assert expired.state == 'cancelled'
        assert expired.event_cursor == stopped.event_cursor
        assert expired.result is None
    finally:
        release.set()
        service.close()


def test_task_completing_after_bounded_close_erases_payload():
    clock = ManualClock()
    entered = threading.Event()
    release = threading.Event()
    task_request = request()
    request_reference = weakref.ref(task_request)

    def decide(*_args):
        entered.set()
        release.wait(timeout=2)
        return None

    service = BrowserTaskService(lambda: ScriptedExecutor(), decide, clock=clock, retention_clock=clock)
    try:
        service.start()
        service.submit(task_request)
        del task_request
        assert entered.wait(timeout=1)
        service.close(timeout_seconds=0)
        release.set()
        done = wait_for_state(service, 'task-1', 'owner-1', {'cancelled', 'failed'})
        assert done.result is None
        assert done.events == ()
        deadline = time.monotonic() + 2
        while request_reference() is not None and time.monotonic() < deadline:
            time.sleep(.01)
        assert request_reference() is None
    finally:
        release.set()
        service.close()


def test_closed_service_ages_out_tombstones_and_releases_cleanup_worker():
    clock = ManualClock()
    service, _done = completed_service(clock)
    service_reference = weakref.ref(service)
    service.close()
    clock.advance(86400)
    del service
    deadline = time.monotonic() + 2
    while service_reference() is not None and time.monotonic() < deadline:
        time.sleep(.01)
    assert service_reference() is None


def test_cleanup_erases_terminal_payload_while_another_decision_is_blocked():
    clock = ManualClock()
    entered = threading.Event()
    release = threading.Event()

    def decide(task_request, _observation):
        if task_request.task_id == 'busy':
            entered.set()
            release.wait(timeout=5)
            return None
        return {
            'schema_version': 1, 'observation_id': 'obs-1', 'action': 'finish',
            'arguments': {'finding': 'Private research result.',
                          'source_observation_ids': ['obs-1']},
        }

    task_request = request()
    request_reference = weakref.ref(task_request)
    service = BrowserTaskService(lambda: ScriptedExecutor(), decide, clock=clock, retention_clock=clock)
    try:
        service.start()
        service.submit(task_request)
        del task_request
        wait_for_state(service, 'task-1', 'owner-1', {'completed'})
        service.submit(request('busy'))
        assert entered.wait(timeout=1)
        clock.advance(3600)
        deadline = time.monotonic() + 2
        while request_reference() is not None and time.monotonic() < deadline:
            time.sleep(.01)
        assert request_reference() is None
        assert not release.is_set()
    finally:
        release.set()
        service.close()


def test_backward_wall_clock_changes_do_not_extend_payload_or_tombstone_retention():
    wall_clock = ManualClock()
    retention_clock = ManualClock()
    service = BrowserTaskService(clock=wall_clock, retention_clock=retention_clock)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        service.submit(request())
        done = wait_for_state(service, 'task-1', 'owner-1', {'failed'})
        wall_clock.advance(-86400)
        retention_clock.advance(3600)
        expired = service.events('task-1', 0, key)
        assert expired.state == 'failed'
        assert expired.next_cursor == done.next_cursor
        assert expired.result is None
        assert expired.events == ()
        wall_clock.advance(-86400)
        retention_clock.advance(86400)
        with pytest.raises(TaskNotFound):
            service.events('task-1', 0, key)
        with pytest.raises(TaskNotFound):
            service.control('task-1', 'stop', key)
    finally:
        service.close()


def test_registry_capacity_rejects_new_task_without_losing_active_or_queued_tasks():
    entered = threading.Event()
    release = threading.Event()

    def decide(*_args):
        entered.set()
        release.wait(timeout=5)
        return None

    service = BrowserTaskService(lambda: ScriptedExecutor(), decide, max_queued_tasks=256)
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        service.submit(request('active'))
        assert entered.wait(timeout=1)
        for index in range(255):
            service.submit(request(f'queued-{index}'))
        with pytest.raises(TaskQueueFull, match='maximum number of retained browser tasks'):
            service.submit(request('overflow'))
        assert service.events('active', 0, key).state == 'running'
        assert service.events('queued-0', 0, key).state == 'queued'
        assert service.events('queued-254', 0, key).state == 'queued'
        with pytest.raises(TaskNotFound):
            service.events('overflow', 0, key)
    finally:
        release.set()
        service.close()


def test_terminal_tasks_and_tombstones_use_registry_capacity_until_expiry():
    wall_clock = ManualClock()
    retention_clock = ManualClock()
    service = BrowserTaskService(
        clock=wall_clock, retention_clock=retention_clock, max_queued_tasks=256,
    )
    key = ('owner-1', 'dashboard', 'session-1')
    try:
        service.start()
        for index in range(256):
            service.submit(request(f'terminal-{index}'))
        wait_for_state(service, 'terminal-255', 'owner-1', {'failed'})
        with pytest.raises(TaskQueueFull, match='retained browser tasks'):
            service.submit(request('overflow'))
        retention_clock.advance(3600)
        assert service.events('terminal-0', 0, key).result is None
        with pytest.raises(TaskQueueFull, match='retained browser tasks'):
            service.submit(request('overflow'))
        retention_clock.advance(86400)
        assert service.submit(request('recovered')).task_id == 'recovered'
        with pytest.raises(TaskNotFound):
            service.events('terminal-0', 0, key)
        with pytest.raises(TaskNotFound):
            service.events('terminal-255', 0, key)
    finally:
        service.close()


def test_absolute_request_expiry_and_event_timestamps_still_use_wall_clock():
    wall_clock = ManualClock()
    retention_clock = ManualClock()
    retention_clock.advance(1_000_000)
    service = BrowserTaskService(clock=wall_clock, retention_clock=retention_clock)
    try:
        service.start()
        service.submit(request())
        done = wait_for_state(service, 'task-1', 'owner-1', {'failed'})
        assert all(event.timestamp == wall_clock.now for event in done.events)
        expired_request = request('expired')
        wall_clock.advance(61)
        with pytest.raises(ValueError, match='request has expired'):
            service.submit(expired_request)
        assert service.events('task-1', 0, ('owner-1', 'dashboard', 'session-1')).result is not None
    finally:
        service.close()
