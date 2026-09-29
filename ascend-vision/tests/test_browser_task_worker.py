import asyncio
import time
from uuid import uuid4

import pytest

from browser.remote_contracts import RemoteBrowserTaskRequest
from integrations.browser_task_worker import BrowserTaskWorker, CoreBrowserClient


def request(*, laptop='laptop-1', boot='boot-1'):
    return RemoteBrowserTaskRequest.from_payload({
        'schema_version': 1,
        'binding': {
            'task_id': str(uuid4()), 'owner_id': 'owner-1', 'channel': 'phone_pwa',
            'browser_session_id': str(uuid4()), 'laptop_id': laptop,
            'broker_boot_id': boot, 'lease_id': 'lease-1', 'fence': 1,
            'scope_id': 'public_research', 'scope_version': 1,
        },
        'goal': 'Research the official documentation.', 'provider': 'gemini',
        'provider_consent': True, 'expires_at': time.time() + 300,
    })


class Core:
    def __init__(self, task=None):
        self.task = task
        self.calls = []
        self.boot_id = None
        self.completion_ack = True

    async def identity(self):
        return {'ownerId': 'owner-1', 'laptopId': 'laptop-1', 'currentBootId': self.boot_id,
                'remoteEnabled': True, 'remoteWritesEnabled': False}

    async def register_boot(self, expected, new):
        self.calls.append(('register', expected, new))
        self.boot_id = new
        return True

    async def heartbeat(self, boot, availability, descriptor):
        self.calls.append(('heartbeat', availability, descriptor))

    async def claim(self, boot):
        self.calls.append(('claim', boot))
        return self.task

    async def task_action(self, binding, action, payload=None):
        self.calls.append((action, binding.task_id))
        return {'active': True} if action == 'start' else (
            {'active': True, 'command': None, 'decision': None} if action == 'renew' else {})

    async def publish_events(self, binding, events):
        self.calls.append(('events', len(events)))
        return True

    async def release(self, binding):
        self.calls.append(('release', binding.task_id))
        return True

    async def publish_proposal(self, binding, proposal, waiting_for_user_type):
        self.calls.append(('proposal', binding.task_id, proposal, waiting_for_user_type))
        return True

    async def complete(self, *args):
        self.calls.append(('complete', args[1]))
        return self.completion_ack

    async def reconcile(self, task_id, boot_id, payload):
        self.calls.append(('reconcile', task_id, boot_id, payload['actionId']))
        return True

class Broker:
    def __init__(self, *, busy=False, reconciliations=()):
        self.busy = busy
        self.reconciliations = list(reconciliations)
        self.submitted = []
        self.stopped = []
        self.terminal = False
        self.terminal_marks = []
        self.decisions = []

    def remote_decide(self, binding, action_id, digest, approved):
        self.decisions.append((action_id, digest, approved))

    def broker_identity(self):
        return 'boot-1'

    def availability(self):
        return {'busy': self.busy, 'broker_boot_id': 'boot-1'}

    def submit_remote(self, task):
        self.submitted.append(task)
        return object()

    def remote_control(self, binding, command):
        self.stopped.append((binding.task_id, command))

    def remote_reconciliations(self):
        return self.reconciliations

    def mark_remote_reconciled(self, task_id):
        self.reconciliations = [row for row in self.reconciliations if row['task_id'] != task_id]
        return True

    def mark_remote_terminal(self, binding):
        self.terminal_marks.append(binding.task_id)
        return True

    def remote_events(self, binding, after):
        from browser.service import EventPage
        state = 'completed' if self.terminal else 'running'
        result = {'status': 'completed', 'findings': [], 'sources': [], 'unfinished_steps': []} if self.terminal else None
        return EventPage(binding.task_id, state, (), after, False, result)


@pytest.mark.parametrize('action,key', [('complete', 'accepted'), ('release', 'released')])
@pytest.mark.parametrize('payload,expected', [({}, False), (False, False), (True, True), (1, False)])
def test_http_client_requires_exact_positive_terminal_acknowledgment(action, key, payload, expected):
    import httpx

    async def check():
        body = payload if isinstance(payload, dict) else {key: payload}
        core = CoreBrowserClient('http://127.0.0.1:8000', 'test-only',
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
        try:
            binding = request().binding
            result = await core.complete(binding, 'completed', 'Done', 0, None) if action == 'complete' else await core.release(binding)
            assert result is expected
        finally:
            await core.close()

    asyncio.run(check())


def test_worker_registers_boot_and_submits_claim_to_broker_once():
    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    assert asyncio.run(worker.run_once()) is True
    assert [call[0] for call in core.calls] == ['register', 'heartbeat', 'claim', 'start']
    assert broker.submitted == [task]
    assert worker._active.request == task


def test_worker_reconciles_prior_boot_evidence_before_claiming():
    pending = {
        'task_id': '51931fa8-1e2a-4d24-a098-3406ed4fd0a7', 'owner_id': 'owner-1',
        'laptop_id': 'laptop-1', 'channel': 'phone_pwa', 'browser_session_id': 'session-1',
        'broker_boot_id': 'old-boot', 'lease_hash': 'do-not-send', 'fence': 4,
        'scope_id': 'public_research', 'scope_version': 1, 'state': 'unknown',
        'attempts': [{'action_id': 'action-1', 'action': 'click',
                      'proposal_digest': 'a' * 64, 'outcome': 'unknown'}],
    }
    core, broker = Core(), Broker(reconciliations=[pending])
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')

    assert asyncio.run(worker.run_once()) is False

    assert ('reconcile', pending['task_id'], 'boot-1', 'action-1') in core.calls
    assert [call[0] for call in core.calls].index('reconcile') < [call[0] for call in core.calls].index('claim')
    assert 'do-not-send' not in repr(core.calls)
    assert broker.reconciliations == []


def test_worker_does_not_report_accepted_never_started_task_as_site_attempt():
    accepted = {
        'task_id': '51931fa8-1e2a-4d24-a098-3406ed4fd0a7', 'owner_id': 'owner-1',
        'laptop_id': 'laptop-1', 'channel': 'phone_pwa', 'browser_session_id': 'session-1',
        'broker_boot_id': 'old-boot', 'lease_hash': 'do-not-send', 'fence': 4,
        'scope_id': 'public_research', 'scope_version': 1, 'state': 'accepted',
        'attempts': [],
    }
    core, broker = Core(), Broker(reconciliations=[accepted])
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')

    assert asyncio.run(worker.run_once()) is False

    assert not any(call[0] == 'reconcile' for call in core.calls)
    assert broker.reconciliations == []


def test_worker_does_not_claim_when_local_broker_is_busy():
    core, broker = Core(request()), Broker(busy=True)
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    assert asyncio.run(worker.run_once()) is False
    assert not any(call[0] == 'claim' for call in core.calls)


def test_worker_releases_claim_when_broker_loses_busy_race():
    core, broker = Core(request()), Broker()
    states = iter([False, True])
    broker.availability = lambda: {'busy': next(states), 'broker_boot_id': 'boot-1'}
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    assert asyncio.run(worker.run_once()) is False
    assert ('release', core.task.binding.task_id) in core.calls
    assert not broker.submitted
    assert not any(call[0] == 'start' for call in core.calls)


def test_worker_forwards_authoritative_review_when_event_history_was_lost():
    from types import SimpleNamespace
    from browser.authorization import ActionProposal

    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini')
    asyncio.run(worker.run_once())
    proposal = ActionProposal(task.binding.task_id, 'action-1', 'owner-1', 'obs-1', 1,
                              'https://example.org', 'fill', 'field-1', 'Message',
                              {'value': 'Hello'}, 'Edit the message.', time.time() + 90).to_payload()
    broker.remote_events = lambda *_: SimpleNamespace(
        state='waiting_for_user', next_cursor=202, reset_required=True, events=(), result=None,
        proposal=proposal, waiting_for_user_type='action_review')
    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())
    published = [call for call in core.calls if call[0] == 'proposal']
    assert published == [('proposal', task.binding.task_id, proposal, 'action_review')]


def test_worker_applies_identical_core_review_decision_only_once():
    task = request()
    core, broker = Core(task), Broker()
    now = [0.0]
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini',
                               monotonic=lambda: now[0])
    asyncio.run(worker.run_once())

    async def response(_binding, _action, _payload=None):
        return {'active': True, 'command': None, 'decision': {
            'actionId': 'action-1', 'proposalDigest': 'a' * 64, 'approved': True}}

    core.task_action = response
    now[0] = 21
    asyncio.run(worker.run_once())
    now[0] = 42
    asyncio.run(worker.run_once())
    assert broker.decisions == [('action-1', 'a' * 64, True)]


def test_worker_does_not_apply_a_review_decision_after_lease_is_inactive():
    task = request()
    core, broker = Core(task), Broker()
    now = [0.0]
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini',
                               monotonic=lambda: now[0])
    asyncio.run(worker.run_once())

    async def response(_binding, _action, _payload=None):
        return {'active': False, 'command': 'stop', 'decision': {
            'actionId': 'action-1', 'proposalDigest': 'a' * 64, 'approved': True}}

    core.task_action = response
    now[0] = 21
    asyncio.run(worker.run_once())
    assert broker.stopped == [(task.binding.task_id, 'stop')]
    assert broker.decisions == []


def test_worker_does_not_republish_a_decided_or_expired_review_as_takeover():
    from types import SimpleNamespace

    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini')
    asyncio.run(worker.run_once())
    broker.remote_events = lambda *_: SimpleNamespace(
        state='waiting_for_user', next_cursor=2, reset_required=False, events=(), result=None,
        proposal=None, waiting_for_user_type='action_review')
    asyncio.run(worker.run_once())
    assert not [call for call in core.calls if call[0] == 'proposal']
    assert broker.stopped == []


def test_worker_stops_on_uncertain_review_publication_and_does_not_advance_cursor():
    from types import SimpleNamespace

    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini')
    asyncio.run(worker.run_once())
    broker.remote_events = lambda *_: SimpleNamespace(
        state='waiting_for_user', next_cursor=2, reset_required=False, events=(), result=None,
        proposal=None, waiting_for_user_type='local_takeover')

    async def unavailable(*_):
        raise ConnectionError('Lost response')

    core.publish_proposal = unavailable
    with pytest.raises(ConnectionError):
        asyncio.run(worker.run_once())
    assert broker.stopped == [(task.binding.task_id, 'stop')]
    assert worker._active.cursor == 0


def test_worker_stops_on_uncertain_event_publication_without_losing_cursor():
    from types import SimpleNamespace

    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini')
    asyncio.run(worker.run_once())
    broker.remote_events = lambda *_: SimpleNamespace(
        state='running', next_cursor=4, reset_required=False, result=None,
        events=(SimpleNamespace(sequence=4, state='running', summary='Observed the page.'),))

    async def unavailable(*_):
        raise ConnectionError('Lost response')

    core.publish_events = unavailable
    with pytest.raises(ConnectionError):
        asyncio.run(worker.run_once())
    assert broker.stopped == [(task.binding.task_id, 'stop')]
    assert worker._active.cursor == 0


def test_worker_acknowledges_stop_without_republishing_erased_content():
    from types import SimpleNamespace

    task = request()
    core, broker = Core(task), Broker()
    now = [0.0]
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1', provider='gemini',
                               monotonic=lambda: now[0])
    asyncio.run(worker.run_once())
    broker.remote_events = lambda *_: SimpleNamespace(
        state='cancelled', next_cursor=4, reset_required=False, result=None,
        events=(SimpleNamespace(sequence=4, state='cancelled', summary='Previously read private content'),))

    async def renewal(*_):
        return {'active': False, 'command': 'stop', 'decision': None}

    async def erased(*_):
        pytest.fail('Revoked task events must never be republished')

    core.task_action = renewal
    core.publish_events = erased
    now[0] = 21
    assert asyncio.run(worker.run_once()) is True
    assert ('complete', 'cancelled') in core.calls
    assert broker.terminal_marks == [task.binding.task_id]


def test_worker_rejects_a_claim_for_another_laptop_and_releases_it():
    core, broker = Core(request(laptop='wrong-laptop')), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    with pytest.raises(PermissionError, match='does not match'):
        asyncio.run(worker.run_once())
    assert ('release', core.task.binding.task_id) in core.calls
    assert not broker.submitted


def test_worker_stops_if_core_replaces_its_registered_broker_boot():
    core, broker = Core(), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    asyncio.run(worker.run_once())
    core.boot_id = 'new-boot'
    with pytest.raises(PermissionError, match='boot was replaced'):
        asyncio.run(worker.run_once())


def test_worker_marks_local_task_terminal_only_after_core_acknowledges_completion():
    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    asyncio.run(worker.run_once())
    broker.terminal = True

    assert asyncio.run(worker.run_once()) is True
    assert broker.terminal_marks == [task.binding.task_id]
    assert ('complete', 'completed') in core.calls


def test_worker_retains_uncertainty_if_core_does_not_ack_completion():
    task = request()
    core, broker = Core(task), Broker()
    worker = BrowserTaskWorker(core, broker, owner_id='owner-1', laptop_id='laptop-1',
                               provider='gemini')
    asyncio.run(worker.run_once())
    broker.terminal = True
    core.completion_ack = False

    with pytest.raises(PermissionError, match='did not acknowledge'):
        asyncio.run(worker.run_once())
    assert broker.terminal_marks == []


def test_core_browser_client_requires_https_except_loopback():
    with pytest.raises(ValueError, match='HTTPS'):
        CoreBrowserClient('http://core.example', 'worker')
    client = CoreBrowserClient('http://127.0.0.1:8000', 'worker')
    asyncio.run(client.close())


def test_core_browser_client_validates_registered_boot_identity():
    import httpx

    def handler(request):
        return httpx.Response(200, json={'registered': True, 'brokerBootId': 'boot-1'})

    client = CoreBrowserClient('https://core.example', 'worker', transport=httpx.MockTransport(handler))
    assert asyncio.run(client.register_boot(None, 'boot-1')) is True
    asyncio.run(client.close())


def test_core_browser_client_sends_content_free_reconciliation_receipt():
    import httpx

    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(200, json={'recorded': True})

    client = CoreBrowserClient('https://core.example', 'worker', transport=httpx.MockTransport(handler))
    payload = {'previousBrokerBootId': 'old-boot', 'fence': 4, 'actionId': 'action-1',
               'action': 'click', 'proposalDigest': 'a' * 64, 'outcome': 'unknown'}
    assert asyncio.run(client.reconcile('51931fa8-1e2a-4d24-a098-3406ed4fd0a7',
                                        'new-boot', payload)) is True
    asyncio.run(client.close())
    assert received[0].url.path.endswith('/51931fa8-1e2a-4d24-a098-3406ed4fd0a7/reconcile')
    assert received[0].headers['X-Browser-Boot'] == 'new-boot'
    assert received[0].read().decode() == (
        '{"previousBrokerBootId":"old-boot","fence":4,"actionId":"action-1",'
        '"action":"click","proposalDigest":"' + 'a' * 64 + '","outcome":"unknown"}'
    )


def test_claim_parser_rejects_unknown_core_fields():
    from integrations.browser_task_worker import _parse_claim
    payload = request().to_payload()
    payload['unexpected'] = True
    with pytest.raises(ValueError, match='invalid contract'):
        _parse_claim(payload)
