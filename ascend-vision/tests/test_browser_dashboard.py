import time
from dataclasses import replace
from io import BytesIO
import json

from flask import Flask

from browser.dashboard_routes import register_browser_routes
from browser.files import BrowserTaskFiles
from browser.service import EventPage, TaskReceipt
from config import BrowserAutomationConfig, Config
from dashboard import create_app


class BrowserClient:
    def __init__(self):
        self.submitted = None
        self.controlled = None
        self.decided = None
        self.saved_profile = None
        self.requested = None
        self.profiles = ['work-account']

    def submit(self, request):
        self.submitted = request
        return TaskReceipt(request.task_id, 'queued', 1)

    def events(self, task_id, after, session_key):
        self.requested = (task_id, after, session_key)
        return EventPage(task_id, 'running', (), after, False, None)

    def control(self, task_id, command, session_key):
        self.controlled = (task_id, command, session_key)
        return TaskReceipt(task_id, 'stopping' if command == 'stop' else command + 'd', 3)

    def decide(self, task_id, action_id, proposal_digest, approved, session_key):
        self.decided = (task_id, action_id, proposal_digest, approved, session_key)
        return TaskReceipt(task_id, 'running', 4)

    def save_profile(self, task_id, session_key):
        self.saved_profile = (task_id, session_key)
        return TaskReceipt(task_id, 'waiting_for_user', 5)

    def list_profiles(self, session_key):
        self.profile_list_owner = session_key
        return self.profiles

    def clear_profile(self, profile_id, session_key):
        self.profile_clear_request = (profile_id, session_key)
        return True

    def metrics_summary(self, cohort):
        self.metrics_cohort = cohort
        return {'eligible': 0, 'accounting': {'calls': 0}}

    def metrics_set_enabled(self, enabled):
        self.metrics_enabled = enabled
        return {'enabled': enabled}

    def metrics_export(self, cursor):
        self.metrics_cursor = cursor
        return {'cursor': cursor, 'runs': []}

    def metrics_clear(self):
        self.metrics_cleared = True
        return {'cleared': True, 'enabled': False}

    def feedback(self, task_id, verdict, session_key):
        self.feedback_request = (task_id, verdict, session_key)
        return True

    def routine_catalogue(self):
        return ({'routine_id': 'docs_brief', 'version': 1, 'enabled': False},)


def test_dashboard_browser_routes_submit_status_and_control_only_local_tasks():
    app = Flask(__name__)
    client = BrowserClient()
    register_browser_routes(app, client, BrowserAutomationConfig(enabled=True, b3_enabled=True))
    http = app.test_client()

    submitted = http.post('/api/browser/tasks', json={'goal': 'Find official docs'})
    assert submitted.status_code == 202
    assert submitted.json['taskId'] == client.submitted.task_id
    assert client.submitted.session_key == ('local', 'dashboard', 'local-dashboard')
    assert client.submitted.goal == 'Find official docs'

    selected = http.post('/api/browser/tasks', json={
        'goal': 'Prepare the selected account form',
        'scopeMode': 'selected_origins',
        'origin': 'https://account.example',
        'capabilities': ['fill', 'click'],
        'profileId': 'work-account',
    })
    assert selected.status_code == 202
    assert client.submitted.scope_mode == 'selected_origins'
    assert client.submitted.allowed_origins == ('https://account.example',)
    assert client.submitted.capabilities == ('fill', 'click')
    assert client.submitted.profile_id == 'work-account'

    status = http.get(f"/api/browser/tasks/{client.submitted.task_id}?after=2")
    assert status.status_code == 200
    assert status.json['state'] == 'running'
    assert client.requested == (
        client.submitted.task_id, 2, ('local', 'dashboard', 'local-dashboard'),
    )

    stopped = http.post(
        f"/api/browser/tasks/{client.submitted.task_id}/control", json={'command': 'stop'},
    )
    assert stopped.status_code == 200
    assert stopped.json['state'] == 'stopping'
    assert client.controlled == (
        client.submitted.task_id, 'stop', ('local', 'dashboard', 'local-dashboard'),
    )

    reviewed = http.post(f"/api/browser/tasks/{client.submitted.task_id}/decision", json={
        'actionId': 'action-1', 'proposalDigest': 'a' * 64, 'approved': True,
    })
    assert reviewed.status_code == 200
    assert client.decided == (
        client.submitted.task_id, 'action-1', 'a' * 64, True,
        ('local', 'dashboard', 'local-dashboard'),
    )

    saved = http.post(f"/api/browser/tasks/{client.submitted.task_id}/profile/save")
    assert saved.status_code == 202
    assert client.saved_profile == (
        client.submitted.task_id, ('local', 'dashboard', 'local-dashboard'),
    )

    profiles = http.get('/api/browser/profiles')
    assert profiles.json == {'profiles': ['work-account']}
    assert client.profile_list_owner == ('local', 'dashboard', 'local-dashboard')
    cleared = http.delete('/api/browser/profiles/work-account')
    assert cleared.json == {'cleared': True}
    assert client.profile_clear_request == (
        'work-account', ('local', 'dashboard', 'local-dashboard'),
    )


def test_dashboard_browser_routes_reject_malformed_or_oversized_requests():
    app = Flask(__name__)
    client = BrowserClient()
    register_browser_routes(app, client, BrowserAutomationConfig(enabled=True))
    http = app.test_client()

    assert http.post('/api/browser/tasks', json={'goal': 'x', 'provider': 'groq'}).status_code == 400
    assert http.post('/api/browser/tasks', json={'goal': 'x' * 4_001}).status_code == 400
    assert http.get('/api/browser/tasks/task-1?after=-1').status_code == 400
    assert http.post('/api/browser/tasks/task-1/control', json={'command': 'delete'}).status_code == 400
    assert http.post('/api/browser/tasks/task-1/decision', json={
        'actionId': 'action-1', 'proposalDigest': 'bad', 'approved': True,
    }).status_code == 400
    assert client.submitted is None


def test_dashboard_metrics_and_routine_routes_are_bounded_and_owner_scoped():
    app = Flask(__name__)
    client = BrowserClient()
    register_browser_routes(app, client, BrowserAutomationConfig(enabled=True))
    http = app.test_client()

    assert http.get('/api/browser/metrics?cohort=all').json == {
        'eligible': 0, 'accounting': {'calls': 0},
    }
    assert client.metrics_cohort == 'all'
    assert http.get('/api/browser/metrics?cohort=unknown').status_code == 400
    assert http.post('/api/browser/metrics/settings', json={'enabled': 1}).status_code == 400
    assert http.post('/api/browser/metrics/settings', json={'enabled': True}).json == {'enabled': True}
    assert client.metrics_enabled is True
    assert http.get('/api/browser/metrics/export?cursor=3').json == {'cursor': 3, 'runs': []}
    assert client.metrics_cursor == 3
    assert http.delete('/api/browser/metrics', data='not-empty').status_code == 400
    assert http.delete('/api/browser/metrics').json == {'cleared': True, 'enabled': False}

    assert http.post('/api/browser/tasks/task-1/feedback', json={'verdict': 'worked'}).json == {'recorded': True}
    assert client.feedback_request == (
        'task-1', 'worked', ('local', 'dashboard', 'local-dashboard'),
    )
    assert http.post('/api/browser/tasks/task-1/feedback', json={'verdict': 'great', 'owner': 'attacker'}).status_code == 400
    assert http.get('/api/browser/routines').json == {
        'routines': [{'routine_id': 'docs_brief', 'version': 1, 'enabled': False}],
    }


def test_dashboard_stages_only_an_explicitly_selected_upload_for_the_submitted_task(tmp_path):
    app = Flask(__name__)
    client = BrowserClient()
    files = BrowserTaskFiles(tmp_path)
    register_browser_routes(app, client, BrowserAutomationConfig(enabled=True, b3_enabled=True), files)
    response = app.test_client().post('/api/browser/tasks', data={
        'goal': 'Attach the selected report to this record',
        'scopeMode': 'selected_origins', 'origin': 'https://example.org', 'profileId': '',
        'capabilities': json.dumps(['upload']),
        'upload': (BytesIO(b'report bytes'), 'report.txt'),
    }, content_type='multipart/form-data')
    assert response.status_code == 202
    assert client.submitted.selected_file_token
    item = files.upload('local', client.submitted.task_id, client.submitted.selected_file_token)
    assert item.filename == 'report.txt'
    assert item.path.read_bytes() == b'report bytes'


def test_dashboard_rejects_an_upload_not_explicitly_enabled_by_owner(tmp_path):
    app = Flask(__name__)
    client = BrowserClient()
    files = BrowserTaskFiles(tmp_path)
    register_browser_routes(app, client, BrowserAutomationConfig(enabled=True, b3_enabled=True), files)
    response = app.test_client().post('/api/browser/tasks', data={
        'goal': 'Use this report', 'scopeMode': 'selected_origins',
        'origin': 'https://example.org', 'profileId': '',
        'capabilities': json.dumps(['fill']),
        'upload': (BytesIO(b'report bytes'), 'report.txt'),
    }, content_type='multipart/form-data')
    assert response.status_code == 400
    assert client.submitted is None


def test_dashboard_browser_routes_are_unavailable_when_feature_is_disabled():
    app = Flask(__name__)
    client = BrowserClient()
    register_browser_routes(app, client, BrowserAutomationConfig())
    response = app.test_client().post('/api/browser/tasks', json={'goal': 'Find docs'})

    assert response.status_code == 404
    assert client.submitted is None
    http = app.test_client()
    assert http.get('/api/browser/profiles').status_code == 404
    assert http.delete('/api/browser/profiles/work-account').status_code == 404


def test_actual_dashboard_protects_browser_mutation_routes_and_renders_controls(tmp_path):
    browser = BrowserClient()
    config = replace(Config(), browser_automation=BrowserAutomationConfig(enabled=True, b3_enabled=True))
    app = create_app(config, browser_client=browser)
    app.config['TESTING'] = True
    http = app.test_client()

    page = http.get('/')
    assert page.status_code == 200
    assert b'id="browser-task-form"' in page.data
    assert b'owner-selected site' in page.data

    rejected = http.post(
        '/api/browser/tasks', json={'goal': 'Find docs'},
        headers={'Origin': 'https://attacker.example'},
    )
    assert rejected.status_code == 403
    assert browser.submitted is None

    accepted = http.post('/api/browser/tasks', json={'goal': 'Find official docs'})
    assert accepted.status_code == 202
    assert browser.submitted.session_key == ('local', 'dashboard', 'local-dashboard')
