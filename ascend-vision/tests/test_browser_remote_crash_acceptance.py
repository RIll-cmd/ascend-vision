"""Count real Chromium effects while terminating the real broker process.

The loopback site and HTTP authority are fixtures, not external accounts. The
production service, executor, dispatch guard and durable journal are exercised.
PostgreSQL authority/race proof lives in Core's opt-in database suite.
"""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
import threading
import time
from uuid import uuid4

import pytest

from browser.executor import BrowserExecutor
from browser.journal import BrowserActionJournal
from browser.policy import BrowserPolicy
from browser.remote_contracts import RemoteBrowserBinding, RemoteBrowserTaskRequest
from browser.remote_guard import RemoteDispatchGuard
from browser.service import BrowserTaskService


@contextmanager
def counted_site():
    effects, attempts = [], set()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (b'<!doctype html><title>Counted fixture</title><main id="state">Ready</main>'
                    b'<button onclick="fetch(\'/effect\',{method:\'POST\'}).then(() => '
                    b'document.querySelector(\'#state\').textContent=\'Saved once\')">Save change</button>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.path == '/effect':
                with lock:
                    effects.append(time.monotonic())
                self.send_response(204)
                self.end_headers()
                return
            data = json.loads(body)
            key = data['attemptId']
            with lock:
                duplicate = key in attempts
                attempts.add(key)
            if duplicate:
                self.send_response(403)
                self.end_headers()
                return
            task_id = self.path.split('/')[-2]
            permit = {'permitId': str(uuid4()), 'attemptId': key, 'taskId': task_id,
                      'laptopId': 'laptop-1', 'brokerBootId': 'boot-1', 'fence': 1,
                      'action': data['action'], 'proposalDigest': data['proposalDigest'],
                      'expiresAt': time.time() + 1}
            encoded = json.dumps(permit).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', effects
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _broker_child(connection, origin, journal_path, payload, stage):
    """Spawn-safe entrypoint; no planner/network/Chromium test doubles."""
    def breakpoint(name):
        connection.send(name)
        connection.recv()  # Parent kills the broker at this exact boundary.

    class CountedExecutor(BrowserExecutor):
        def click(self, *args):
            if stage == 'before_effect':
                breakpoint(stage)
            observation = super().click(*args)
            if stage == 'after_effect':
                self._page.wait_for_function("document.querySelector('#state').textContent === 'Saved once'")
                breakpoint(stage)
            return observation

    def decide(_request, observation):
        if not observation.url or observation.url == 'about:blank':
            return {'schema_version': 1, 'observation_id': observation.observation_id,
                    'action': 'navigate', 'arguments': {'url': origin + '/'}}
        if 'Saved once' in observation.visible_text:
            return {'schema_version': 1, 'observation_id': observation.observation_id, 'action': 'finish',
                    'arguments': {'finding': 'The fixture shows a saved change.',
                                  'source_observation_ids': [observation.observation_id]}}
        target = next(item for item in observation.elements if item.label == 'Save change')
        return {'schema_version': 1, 'observation_id': observation.observation_id,
                'action': 'click', 'arguments': {'element_ref': target.element_ref},
                'expected_result': 'Save one change to the counted fixture.'}

    guard = RemoteDispatchGuard(origin, 'fixture-worker', owner_id='owner-1', laptop_id='laptop-1')
    service = BrowserTaskService(
        lambda: CountedExecutor(headless=True, policy=BrowserPolicy(
            allow_test_origins={origin}, scope_mode='selected_origins',
            allowed_origins={'https://example.org'}, capabilities={'click'})), decide,
        remote_enabled=True, remote_writes_enabled=True, enable_mutations=True,
        remote_scopes=({'scope_id': 'fixture', 'version': 1, 'origin': 'https://example.org', 'actions': ['click']},),
        remote_dispatch_guard=guard, broker_boot_id='boot-1', action_journal=BrowserActionJournal(journal_path),
    )
    try:
        request = RemoteBrowserTaskRequest.from_payload(payload)
        service.start()
        service.submit_remote(request)
        approved = False
        until = time.monotonic() + 30
        while time.monotonic() < until:
            page = service.remote_events(request.binding, 0)
            if page.proposal and not approved:
                service.remote_decide(request.binding, page.proposal['action_id'], page.proposal['digest'], True)
                approved = True
            if page.state == 'completed' and stage == 'before_ack':
                breakpoint(stage)  # Core completion has not been acknowledged locally.
            if page.state in {'failed', 'unknown', 'cancelled', 'partial'}:
                connection.send({'error': page.state, 'events': [item.summary for item in page.events]})
                return
            time.sleep(.01)
        connection.send({'error': 'timeout'})
    finally:
        service.close()
        guard.close()
        connection.close()


@pytest.mark.parametrize('stage,expected_effects', [('before_effect', 0), ('after_effect', 1), ('before_ack', 1)])
def test_real_broker_crash_never_replays_a_counted_site_effect(tmp_path, stage, expected_effects):
    pytest.importorskip('playwright')
    psutil = pytest.importorskip('psutil')
    task_id = str(uuid4())
    binding = RemoteBrowserBinding(task_id, 'owner-1', 'phone_pwa', str(uuid4()),
                                   'laptop-1', 'boot-1', 'lease-1', 1, 'fixture', 1)
    request = RemoteBrowserTaskRequest(binding, 'Save one fixture change.', 'gemini', True, time.time() + 180)
    journal_path = str(tmp_path / 'crash.sqlite3')
    with counted_site() as (origin, effects):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(target=_broker_child, args=(child, origin, journal_path, request.to_payload(), stage))
        process.start()
        child.close()
        try:
            assert parent.poll(40), 'broker did not reach the selected crash boundary'
            assert parent.recv() == stage
            assert len(effects) == expected_effects
            descendants = psutil.Process(process.pid).children(recursive=True)
            process.terminate()
            process.join(timeout=5)
            for owned in reversed(descendants):
                try:
                    owned.kill()
                except psutil.NoSuchProcess:
                    pass
            assert not process.is_alive()
            journal = BrowserActionJournal(journal_path)
            recovery = journal.remote_reconciliations()
            assert len(recovery) == 1 and recovery[0]['state'] == 'unknown'
            assert recovery[0]['task_id'] == task_id
            assert len(recovery[0]['attempts']) == 1
            assert recovery[0]['attempts'][0]['outcome'] in {'unknown', 'attempted'}
            # Even the same request is denied, rather than replaying a lost acknowledgment.
            replacement = BrowserTaskService(
                decision_provider=lambda *_: pytest.fail('uncertain task must never be planned again'),
                remote_enabled=True, remote_writes_enabled=True, enable_mutations=True,
                remote_scopes=({'scope_id': 'fixture', 'version': 1, 'origin': 'https://example.org', 'actions': ['click']},),
                remote_dispatch_guard=type('Identity', (), {'owner_id': 'owner-1', 'laptop_id': 'laptop-1'})(),
                broker_boot_id='boot-1', action_journal=journal,
            )
            try:
                replacement.start()
                with pytest.raises(PermissionError, match='cannot be replayed'):
                    replacement.submit_remote(request)
            finally:
                replacement.close()
            assert len(effects) == expected_effects <= 1
        finally:
            if process.is_alive():
                for owned in reversed(psutil.Process(process.pid).children(recursive=True)):
                    try:
                        owned.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.terminate()
                process.join(timeout=5)
            parent.close()
