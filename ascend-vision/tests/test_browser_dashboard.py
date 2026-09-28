import time
from dataclasses import replace

from flask import Flask

from browser.dashboard_routes import register_browser_routes
from browser.service import EventPage, TaskReceipt
from config import BrowserAutomationConfig, Config
from dashboard import create_app


class BrowserClient:
    def __init__(self):
        self.submitted = None
        self.controlled = None
        self.decided = None
        self.requested = None

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


def test_dashboard_browser_routes_submit_status_and_control_only_local_tasks():
    app = Flask(__name__)
    client = BrowserClient()
    register_browser_routes(app, client, BrowserAutomationConfig(enabled=True))
    http = app.test_client()

    submitted = http.post('/api/browser/tasks', json={'goal': 'Find official docs'})
    assert submitted.status_code == 202
    assert submitted.json['taskId'] == client.submitted.task_id
    assert client.submitted.session_key == ('local', 'dashboard', 'local-dashboard')
    assert client.submitted.goal == 'Find official docs'

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


def test_dashboard_browser_routes_are_unavailable_when_feature_is_disabled():
    app = Flask(__name__)
    client = BrowserClient()
    register_browser_routes(app, client, BrowserAutomationConfig())
    response = app.test_client().post('/api/browser/tasks', json={'goal': 'Find docs'})

    assert response.status_code == 404
    assert client.submitted is None


def test_actual_dashboard_protects_browser_mutation_routes_and_renders_controls(tmp_path):
    browser = BrowserClient()
    config = replace(Config(), browser_automation=BrowserAutomationConfig(enabled=True))
    app = create_app(config, browser_client=browser)
    app.config['TESTING'] = True
    http = app.test_client()

    page = http.get('/')
    assert page.status_code == 200
    assert b'id="browser-task-form"' in page.data
    assert b'form submissions' in page.data

    rejected = http.post(
        '/api/browser/tasks', json={'goal': 'Find docs'},
        headers={'Origin': 'https://attacker.example'},
    )
    assert rejected.status_code == 403
    assert browser.submitted is None

    accepted = http.post('/api/browser/tasks', json={'goal': 'Find official docs'})
    assert accepted.status_code == 202
    assert browser.submitted.session_key == ('local', 'dashboard', 'local-dashboard')
