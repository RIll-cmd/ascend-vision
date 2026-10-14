import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from assistant.room_dialog import RoomAction
from config import AgentRoomConfig
from integrations.agent_room import RoomClient


class FakeHub(BaseHTTPRequestHandler):
    posts = []
    session = {'id': 's1', 'stage': 'gate-plan', 'attention': None}

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/api/room/sessions/current':
            return self._send(200, {'session': FakeHub.session})
        self._send(404, {'error': 'NOT_FOUND'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        FakeHub.posts.append((self.path, body, self.headers.get('host')))
        if body.get('decision') == 'approve' and body.get('gate') == 'roles':
            return self._send(409, {'error': 'GATE_NOT_OPEN'})
        self._send(200, {'ok': True})


@pytest.fixture
def hub():
    server = HTTPServer(('127.0.0.1', 0), FakeHub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    FakeHub.posts = []
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def test_current_session_is_read_and_a_dead_hub_reads_as_none(hub):
    assert RoomClient(hub).current() == FakeHub.session
    assert RoomClient('http://127.0.0.1:1', timeout_seconds=0.5).current() is None


def test_decisions_post_to_the_gate_and_resume_routes(hub):
    client = RoomClient(hub)
    assert client.send(RoomAction('gate', 'plan', 'revise', 'Merge tasks')) == (True, 'Done. The agent room has your answer.')
    assert client.send(RoomAction('resume', None, 'retry'))[0] is True
    assert FakeHub.posts[0][:2] == ('/api/room/sessions/current/gate', {'gate': 'plan', 'decision': 'revise', 'note': 'Merge tasks'})
    assert FakeHub.posts[1][:2] == ('/api/room/sessions/current/resume', {'action': 'retry'})
    assert FakeHub.posts[0][2].startswith('127.0.0.1')


def test_a_refused_decision_is_reported_in_plain_words(hub):
    ok, message = RoomClient(hub).send(RoomAction('gate', 'roles', 'approve'))
    assert ok is False and message == 'The agent room did not take that answer (GATE_NOT_OPEN).'
    ok, message = RoomClient('http://127.0.0.1:1', timeout_seconds=0.5).send(RoomAction('gate', 'plan', 'approve'))
    assert ok is False and message == "I couldn't reach the agent room."


def test_agent_room_config_only_talks_to_this_computer():
    assert AgentRoomConfig().base_url == 'http://127.0.0.1:5173'
    AgentRoomConfig(base_url='http://localhost:5174')
    for bad in ('http://192.168.1.5:5173', 'https://127.0.0.1:5173', 'http://evil.example'):
        with pytest.raises(ValueError):
            AgentRoomConfig(base_url=bad)
    with pytest.raises(ValueError):
        AgentRoomConfig(poll_seconds=0.1)


def test_no_session_reads_as_empty_so_the_dialog_can_end(hub):
    saved = FakeHub.session
    FakeHub.session = None
    try:
        assert RoomClient(hub).current() == {}
    finally:
        FakeHub.session = saved
