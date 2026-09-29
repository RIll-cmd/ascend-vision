from contextlib import AbstractContextManager
from dataclasses import replace
import time
from uuid import uuid4

import pytest

from browser.executor import BrowserElement, BrowserObservation
from browser.remote_contracts import RemoteBrowserBinding, RemoteBrowserTaskRequest
from browser.journal import BrowserActionJournal
from browser.service import BrowserTaskService, TaskNotFound


def make_request():
    return RemoteBrowserTaskRequest.from_payload({
        'schema_version': 1,
        'binding': {
            'task_id': str(uuid4()), 'owner_id': 'owner-1', 'channel': 'phone_pwa',
            'browser_session_id': str(uuid4()), 'laptop_id': 'laptop-1',
            'broker_boot_id': 'boot-1', 'lease_id': 'lease-1', 'fence': 1,
            'scope_id': 'public_research', 'scope_version': 1,
        },
        'goal': 'Read the official documentation.', 'provider': 'gemini',
        'provider_consent': True, 'expires_at': time.time() + 60,
    })


class Guard:
    def __init__(self, *, fail_action=None):
        self.calls = []
        self.fail_action = fail_action
        self.owner_id = 'owner-1'
        self.laptop_id = 'laptop-1'

    def authorize(self, binding, attempt_id, action, proposal_digest):
        self.calls.append((binding, attempt_id, action, proposal_digest))
        if action == self.fail_action:
            raise PermissionError('Core denied this remote request.')


class Executor(AbstractContextManager):
    def __init__(self):
        self.observe_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def observe(self, task_id):
        self.observe_calls += 1
        return BrowserObservation(
            task_id, 'page-1', 1, 'obs-1', '2026-09-28T00:00:00+00:00',
            'https://example.org/docs', 'Docs', 'official documentation', (), False,
        )

    def close(self):
        return None


def wait_remote(service, binding, wanted):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        page = service.remote_events(binding, 0)
        if page.state in wanted:
            return page
        time.sleep(.01)
    raise AssertionError(f'remote task did not reach one of {wanted}')


class FormExecutor(Executor):
    def __init__(self, *, uncertain=False):
        super().__init__()
        self.calls = []
        self.uncertain = uncertain

    def observe(self, task_id):
        result = super().observe(task_id)
        return replace(result, observation_id=f'obs-{self.observe_calls}', elements=(
            BrowserElement('field-1', 'textbox', 'Message', 'input'),))

    def fill(self, task_id, observation_id, element_ref, value):
        self.calls.append((element_ref, value))
        if self.uncertain:
            raise ConnectionError('Site may have autosaved before the response was lost')
        return self.observe(task_id)


def remote_form_service(tmp_path, *, guard=None, uncertain=False):
    request = make_request()
    request = replace(request, binding=replace(request.binding, scope_id='form'), expires_at=time.time() + 180)
    executor = FormExecutor(uncertain=uncertain)
    journal = BrowserActionJournal(tmp_path / 'actions.sqlite3')
    service = BrowserTaskService(
        lambda: executor, lambda _request, observation: {
            'schema_version': 1, 'observation_id': observation.observation_id,
            'action': 'fill', 'arguments': {'element_ref': 'field-1', 'value': 'Reviewed text'},
        }, remote_enabled=True, remote_writes_enabled=True, enable_mutations=True,
        remote_scopes=({'scope_id': 'form', 'version': 1, 'origin': 'https://example.org', 'actions': ['fill']},),
        remote_dispatch_guard=guard or Guard(), broker_boot_id='boot-1', action_journal=journal,
    )
    service.start()
    service.submit_remote(request)
    return service, request, executor, journal


def test_remote_proposal_is_limited_to_two_minutes(tmp_path):
    service, request, _, _ = remote_form_service(tmp_path)
    try:
        page = wait_remote(service, request.binding, {'waiting_for_user'})
        assert 0 < page.proposal['expires_at'] - time.time() <= 120
    finally:
        service.close()


def test_pause_resume_invalidates_old_review_and_reobserves(tmp_path):
    service, request, executor, _ = remote_form_service(tmp_path)
    try:
        old = wait_remote(service, request.binding, {'waiting_for_user'}).proposal
        service.remote_control(request.binding, 'pause')
        service.remote_control(request.binding, 'resume')
        with pytest.raises(ValueError):
            service.remote_decide(request.binding, old['action_id'], old['digest'], True)
        until = time.monotonic() + 2
        while time.monotonic() < until:
            page = service.remote_events(request.binding, 0)
            if page.proposal and page.proposal['action_id'] != old['action_id']:
                break
            time.sleep(.01)
        assert page.proposal and page.proposal['action_id'] != old['action_id']
        assert page.proposal['observation_id'] != old['observation_id']
        assert executor.observe_calls >= 2
        assert executor.calls == []
    finally:
        service.close()


def test_stop_during_core_authorization_never_dispatches_the_reviewed_effect(tmp_path):
    guard = Guard()
    service, request, executor, journal = remote_form_service(tmp_path, guard=guard)
    try:
        proposal = wait_remote(service, request.binding, {'waiting_for_user'}).proposal
        original = guard.authorize

        def stop_in_flight(binding, attempt_id, action, digest):
            original(binding, attempt_id, action, digest)
            if action == 'fill':
                service.remote_control(binding, 'stop')

        guard.authorize = stop_in_flight
        service.remote_decide(request.binding, proposal['action_id'], proposal['digest'], True)
        page = wait_remote(service, request.binding, {'cancelled', 'failed', 'unknown'})
        assert executor.calls == []
        assert journal.get(proposal['action_id'])['state'] == 'failed'
        assert page.state == 'cancelled'
    finally:
        service.close()


def test_uncertain_reviewed_effect_is_unknown_not_failed(tmp_path):
    service, request, executor, journal = remote_form_service(tmp_path, uncertain=True)
    try:
        proposal = wait_remote(service, request.binding, {'waiting_for_user'}).proposal
        service.remote_decide(request.binding, proposal['action_id'], proposal['digest'], True)
        page = wait_remote(service, request.binding, {'unknown', 'failed'})
        assert executor.calls == [('field-1', 'Reviewed text')]
        assert journal.get(proposal['action_id'])['state'] == 'unknown'
        assert page.state == 'unknown'
    finally:
        service.close()


def test_remote_research_checks_core_before_observation_and_each_model_turn(tmp_path):
    request = make_request()
    executor = Executor()
    guard = Guard()
    decisions = iter([
        {'schema_version': 1, 'observation_id': 'obs-1', 'action': 'finish',
         'arguments': {'finding': 'Install it from the official guide.',
                       'source_observation_ids': ['obs-1']}},
    ])
    service = BrowserTaskService(
        lambda: executor, lambda *_args: next(decisions),
        remote_enabled=True, remote_dispatch_guard=guard, broker_boot_id='boot-1',
        action_journal=BrowserActionJournal(tmp_path / 'actions.sqlite3'),
    )
    try:
        service.start()
        accepted = service.submit_remote(request)
        page = wait_remote(service, request.binding, {'completed'})

        assert accepted.task_id == request.binding.task_id
        assert page.result['sources'] == ['https://example.org/docs']
        assert [call[2] for call in guard.calls] == ['observe', 'plan', 'finish']
        assert executor.observe_calls == 1
    finally:
        service.close()


def test_remote_submission_fails_closed_without_core_guard():
    request = make_request()
    executor = Executor()
    service = BrowserTaskService(lambda: executor, remote_enabled=True)
    try:
        service.start()
        with pytest.raises(PermissionError, match='disabled or invalid'):
            service.submit_remote(request)
        assert executor.observe_calls == 0
    finally:
        service.close()


def test_remote_submission_fails_closed_without_durable_remote_journal(tmp_path):
    request = make_request()
    service = BrowserTaskService(
        Executor, remote_enabled=True, remote_dispatch_guard=Guard(), broker_boot_id='boot-1',
    )
    try:
        service.start()
        with pytest.raises(PermissionError, match='durable journal'):
            service.submit_remote(request)
    finally:
        service.close()


def test_remote_task_journal_records_acceptance_and_terminal_state(tmp_path):
    request = make_request()
    journal = BrowserActionJournal(tmp_path / 'actions.sqlite3')
    service = BrowserTaskService(
        Executor, lambda *_args: {
            'schema_version': 1, 'observation_id': 'obs-1', 'action': 'finish',
            'arguments': {'finding': 'Verified result.', 'source_observation_ids': ['obs-1']},
        }, remote_enabled=True, remote_dispatch_guard=Guard(), broker_boot_id='boot-1',
        action_journal=journal,
    )
    try:
        service.start()
        service.submit_remote(request)
        wait_remote(service, request.binding, {'completed'})
        record = journal.remote_reconciliations()
        assert record == []
        with journal._connection() as connection:
            row = connection.execute('SELECT state FROM browser_remote_tasks WHERE task_id=?',
                                     (request.binding.task_id,)).fetchone()
        assert row['state'] == 'started'
        assert service.mark_remote_terminal(request.binding) is True
        with journal._connection() as connection:
            row = connection.execute('SELECT state FROM browser_remote_tasks WHERE task_id=?',
                                     (request.binding.task_id,)).fetchone()
        assert row['state'] == 'terminal'
    finally:
        service.close()


@pytest.mark.parametrize('field,value', [
    ('owner_id', 'other-owner'), ('laptop_id', 'other-laptop'),
    ('broker_boot_id', 'old-boot'),
])
def test_remote_submission_must_match_local_execution_identity(field, value):
    request = make_request()
    guard = Guard()
    service = BrowserTaskService(
        Executor, remote_enabled=True, remote_dispatch_guard=guard, broker_boot_id='boot-1',
    )
    try:
        service.start()
        invalid = replace(request, binding=replace(request.binding, **{field: value}))
        with pytest.raises(PermissionError, match='does not match this owner'):
            service.submit_remote(invalid)
        assert not guard.calls
    finally:
        service.close()


def test_remote_submission_must_use_the_laptop_selected_provider():
    request = make_request()
    invalid = replace(request, provider='groq')
    service = BrowserTaskService(
        Executor, remote_enabled=True, remote_dispatch_guard=Guard(), broker_boot_id='boot-1',
        remote_provider='gemini',
    )
    try:
        service.start()
        with pytest.raises(PermissionError, match='provider'):
            service.submit_remote(invalid)
    finally:
        service.close()


def test_remote_task_cannot_be_read_with_a_different_fence(tmp_path):
    request = make_request()
    guard = Guard(fail_action='observe')
    service = BrowserTaskService(
        Executor, lambda *_args: pytest.fail('the planner must not run'),
        remote_enabled=True, remote_dispatch_guard=guard, broker_boot_id='boot-1',
        action_journal=BrowserActionJournal(tmp_path / 'actions.sqlite3'),
    )
    try:
        service.start()
        service.submit_remote(request)
        wrong_binding = replace(request.binding, fence=2)
        with pytest.raises(TaskNotFound):
            service.remote_events(wrong_binding, 0)
        page = wait_remote(service, request.binding, {'failed'})
        assert page.state == 'failed'
        assert [call[2] for call in guard.calls] == ['observe']
    finally:
        service.close()
