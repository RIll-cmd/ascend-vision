from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
from io import BytesIO
import hashlib
from urllib.parse import urlsplit

import pytest

from browser.executor import BrowserExecutor, StaleObservation
from browser.files import BrowserTaskFiles
from browser.policy import BrowserPolicy


@contextmanager
def fixture_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/download':
                data = b'fixture report contents'
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', 'attachment; filename="report.txt"')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == '/redirect-private':
                self.send_response(302)
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data/')
                self.end_headers()
                return
            if path == '/delayed':
                body = '<title>Delayed</title><main id="result">Loading…</main><script>setTimeout(() => document.querySelector("main").textContent = "Delayed result ready.", 150)</script>'
            elif path == '/duplicates':
                body = '<title>Duplicate links</title><main>Choose one result.</main><a href="/next?item=1">Read result</a><a href="/next?item=2">Read result</a>'
            elif path == '/mutable':
                body = '<title>Mutable link</title><a id="target" href="/next">Read result</a>'
            elif path == '/popup':
                body = '<title>Popup links</title><a target="_blank" href="/next">Open result in new tab</a>'
            elif path == '/form':
                body = ('<title>Form</title><form action="/submit" method="post">'
                        '<label>Message<input name="message" aria-label="Message"></label>'
                        '<label>Password<input type="password" aria-label="Password" value="do-not-observe"></label>'
                        '<label>Priority<select name="priority" aria-label="Priority">'
                        '<option value="low">Low</option><option value="high">High</option></select></label>'
                        '<button type="submit">Send message</button></form>')
            elif path == '/file-input':
                body = ('<title>File input</title><label>Report<input type="file" id="upload" '
                        'onchange="document.querySelector(\'#selected\').textContent=this.files[0].name"></label>'
                        '<main id="selected">No file selected</main>')
            elif path == '/download-page':
                body = '<title>Download</title><a href="/download">Download report</a>'
            elif path == '/submit':
                body = '<title>Ambiguous submission</title><main>Request received; processing status unknown.</main>'
            if path == '/next':
                body = '<title>Next</title><main>Verified destination text.</main>'
            elif path == '/':
                body = ('<title>Research fixture</title><main>Expected fixture text.</main>'
                        '<a href="/next">Open next page</a>'
                        '<button>Do not click</button>')
            data = f'<!doctype html><html><body>{body}</body></html>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_executor_reads_a_visible_reference_from_an_ephemeral_context():
    pytest.importorskip('playwright')
    with fixture_site() as origin:
        policy = BrowserPolicy(allow_test_origins={origin})
        with BrowserExecutor(policy=policy, headless=True) as executor:
            first = executor.navigate('task-1', origin + '/')
            assert first.title == 'Research fixture'
            assert 'Expected fixture text.' in first.visible_text
            link = next(item for item in first.elements if item.label == 'Open next page')
            assert link.kind == 'page_link'

            second = executor.click('task-1', first.observation_id, link.element_ref)
            assert second.title == 'Next'
            assert 'Verified destination text.' in second.visible_text


def test_executor_rejects_stale_or_non_link_element_references():
    pytest.importorskip('playwright')
    with fixture_site() as origin:
        policy = BrowserPolicy(allow_test_origins={origin})
        with BrowserExecutor(policy=policy, headless=True) as executor:
            first = executor.navigate('task-1', origin + '/')
            button = next(item for item in first.elements if item.kind == 'button')
            refreshed = executor.observe('task-1')

            with pytest.raises(StaleObservation):
                executor.click('task-1', first.observation_id, button.element_ref)

            current_button = next(item for item in refreshed.elements if item.kind == 'button')
            with pytest.raises(PermissionError):
                executor.click('task-1', refreshed.observation_id, current_button.element_ref)


def test_executor_revalidates_navigation_targets_before_browser_dispatch():
    pytest.importorskip('playwright')
    policy = BrowserPolicy()
    with BrowserExecutor(policy=policy, headless=True) as executor:
        with pytest.raises(ValueError):
            executor.navigate('task-1', 'http://127.0.0.1:1/')


def test_executor_blocks_a_redirect_to_link_local_metadata():
    pytest.importorskip('playwright')
    from browser.policy import PolicyDenied

    with fixture_site() as origin:
        policy = BrowserPolicy(allow_test_origins={origin})
        with BrowserExecutor(policy=policy, headless=True) as executor:
            with pytest.raises(PolicyDenied):
                executor.navigate('task-1', origin + '/redirect-private')


def test_executor_blocks_websocket_attempts_before_private_network_connect():
    pytest.importorskip('playwright')
    with fixture_site() as origin, socket.create_server(('127.0.0.1', 0)) as listener:
        listener.settimeout(.15)
        with BrowserExecutor(policy=BrowserPolicy(allow_test_origins={origin}), headless=True) as executor:
            executor.navigate('task-1', origin + '/')
            executor._page.evaluate(
                "url => { window.testSocket = new WebSocket(url); return true; }",
                f'ws://127.0.0.1:{listener.getsockname()[1]}/private',
            )
            executor._page.wait_for_timeout(100)

            assert executor._request_blocked
            with pytest.raises(socket.timeout):
                listener.accept()


def test_executor_waits_for_delayed_page_content_with_a_fresh_observation():
    pytest.importorskip('playwright')
    with fixture_site() as origin:
        with BrowserExecutor(policy=BrowserPolicy(allow_test_origins={origin}), headless=True) as executor:
            first = executor.navigate('task-1', origin + '/delayed')
            assert 'Loading' in first.visible_text
            second = executor.wait_for('task-1', first.observation_id, 250)
            assert 'Delayed result ready.' in second.visible_text


def test_executor_uses_distinct_references_for_duplicate_links_and_opens_owned_popup():
    pytest.importorskip('playwright')
    with fixture_site() as origin:
        with BrowserExecutor(policy=BrowserPolicy(allow_test_origins={origin}), headless=True) as executor:
            duplicates = executor.navigate('task-1', origin + '/duplicates')
            links = [item for item in duplicates.elements if item.label == 'Read result']
            assert len(links) == 2
            assert links[0].element_ref != links[1].element_ref

            popup = executor.navigate('task-1', origin + '/popup')
            popup_link = next(item for item in popup.elements if item.label == 'Open result in new tab')
            result = executor.click('task-1', popup.observation_id, popup_link.element_ref)
            assert result.title == 'Next'
            assert 'Verified destination text.' in result.visible_text


def test_executor_rejects_a_link_that_mutated_after_observation_and_never_submits_forms():
    pytest.importorskip('playwright')
    with fixture_site() as origin:
        with BrowserExecutor(policy=BrowserPolicy(allow_test_origins={origin}), headless=True) as executor:
            observed = executor.navigate('task-1', origin + '/mutable')
            link = next(item for item in observed.elements if item.label == 'Read result')
            executor._page.locator('#target').evaluate(
                "node => node.href = 'http://169.254.169.254/latest/meta-data/'",
            )
            with pytest.raises(StaleObservation, match='changed'):
                executor.click('task-1', observed.observation_id, link.element_ref)

            form = executor.navigate('task-1', origin + '/form')
            submit = next(item for item in form.elements if item.label == 'Send message')
            with pytest.raises(PermissionError):
                executor.click('task-1', form.observation_id, submit.element_ref)


def test_selected_origin_executor_fills_and_selects_only_reviewable_nonsecret_fields():
    pytest.importorskip('playwright')
    with fixture_site() as origin:
        policy = BrowserPolicy(
            scope_mode='selected_origins',
            allowed_origins={'https://example.org'},
            capabilities={'fill', 'select'},
            allow_test_origins={origin},
        )
        with BrowserExecutor(policy=policy, headless=True) as executor:
            form = executor.navigate('task-1', origin + '/form')
            labels = {item.label for item in form.elements}
            assert 'Message' in labels
            assert 'Priority' in labels
            assert 'Password' not in labels

            message = next(item for item in form.elements if item.label == 'Message')
            executor._page.locator('[name=message]').evaluate("node => node.setAttribute('placeholder', 'Changed target')")
            with pytest.raises(StaleObservation, match='changed after review'):
                executor.fill('task-1', form.observation_id, message.element_ref, 'Reviewed text')

            form = executor.observe('task-1')
            message = next(item for item in form.elements if item.label == 'Message')
            edited = executor.fill('task-1', form.observation_id, message.element_ref, 'Reviewed text')
            priority = next(item for item in edited.elements if item.label == 'Priority')
            selected = executor.select('task-1', edited.observation_id, priority.element_ref, 'high')

            assert executor._page.locator('[name=message]').input_value() == 'Reviewed text'
            assert executor._page.locator('[name=priority]').input_value() == 'high'
            assert selected.document_revision == edited.document_revision + 1


def test_public_research_policy_never_allows_form_mutations():
    policy = BrowserPolicy()

    assert not policy.allows_action('fill', target_kind='input')
    assert not policy.allows_action('select', target_kind='select')


def test_browser_profile_is_loaded_for_owner_and_saved_only_on_explicit_request():
    pytest.importorskip('playwright')

    class Profiles:
        def __init__(self):
            self.saved = None

        def load(self, owner, profile_id):
            assert (owner, profile_id) == ('owner-a', 'account-one')
            return {'cookies': [], 'origins': []}

        def save(self, owner, profile_id, state):
            self.saved = (owner, profile_id, state)

    profiles = Profiles()
    with fixture_site() as origin:
        with BrowserExecutor(
            policy=BrowserPolicy(allow_test_origins={origin}, scope_mode='selected_origins',
                                 allowed_origins={'https://example.org'}),
            headless=True, profile_store=profiles, profile_owner='owner-a', profile_id='account-one',
        ) as executor:
            executor.navigate('task-1', origin + '/')
            assert profiles.saved is None
            executor.save_profile()
        assert profiles.saved is not None
        assert profiles.saved[:2] == ('owner-a', 'account-one')
        assert isinstance(profiles.saved[2], dict)


def test_selected_origin_file_upload_and_download_use_only_managed_file_paths(tmp_path):
    pytest.importorskip('playwright')
    files = BrowserTaskFiles(tmp_path)
    upload_token, _ = files.stage_upload('owner-a', 'task-1', 'report.txt', BytesIO(b'owner file'))
    with fixture_site() as origin:
        policy = BrowserPolicy(
            scope_mode='selected_origins', allowed_origins={'https://example.org'},
            capabilities={'upload', 'download'}, allow_test_origins={origin},
        )
        with BrowserExecutor(
            policy=policy, headless=True, file_store=files, task_owner='owner-a', task_id='task-1',
            selected_file_token=upload_token,
        ) as executor:
            page = executor.navigate('task-1', origin + '/file-input')
            file_input = next(item for item in page.elements if item.kind == 'file_input')
            uploaded = executor.upload('task-1', page.observation_id, file_input.element_ref)
            assert 'report.txt' in uploaded.visible_text
            assert executor._page.locator('#upload').input_value().replace('\\', '/').endswith('report.txt')

            page = executor.navigate('task-1', origin + '/download-page')
            link = next(item for item in page.elements if item.label == 'Download report')
            executor._page.locator('a').evaluate("node => node.href = '/next'")
            with pytest.raises(StaleObservation, match='destination changed'):
                executor.download('task-1', page.observation_id, link.element_ref)
            page = executor.observe('task-1')
            link = next(item for item in page.elements if item.label == 'Download report')
            executor._page.locator('a').evaluate("node => node.href = '/download'")
            page = executor.observe('task-1')
            link = next(item for item in page.elements if item.label == 'Download report')
            downloaded = executor.download('task-1', page.observation_id, link.element_ref)
            item = executor.last_download
            assert item == {
                'filename': 'report.txt', 'size': len(b'fixture report contents'),
                'sha256': hashlib.sha256(b'fixture report contents').hexdigest(),
            }
            assert 'Download' in downloaded.title
            assert (tmp_path / 'downloads' / 'report.txt').read_bytes() == b'fixture report contents'
