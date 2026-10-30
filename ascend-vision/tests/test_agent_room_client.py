import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from assistant.room_dialog import RoomAction
from config import AgentRoomConfig
from integrations.agent_room import RoomClient, newest_repo, promise_queue, room_task


class FakeHub(BaseHTTPRequestHandler):
    posts = []
    session = {'id': 's1', 'stage': 'gate-plan', 'attention': None}
    status = {'online': True, 'repos': [{'dir': 'D:/ascend_hub', 'check': 'npm test'},
                                        {'dir': 'D:/ascend-vision', 'check': None}]}

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
        if self.path == '/api/room':
            return self._send(200, FakeHub.status)
        self._send(404, {'error': 'NOT_FOUND'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        FakeHub.posts.append((self.path, body, self.headers.get('host')))
        if self.path == '/api/room/queue':
            if body['body']['task'].startswith('Slow'):
                import time
                time.sleep(0.6)
            if body['body']['task'].startswith('Full'):
                return self._send(409, {'error': 'QUEUE_FULL'})
            return self._send(201, {'item': {'id': 'q1'}})
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


REPOS = [{'dir': 'D:/ascend_hub', 'check': 'npm test'}, {'dir': 'D:/ascend-vision', 'check': None}]


def test_room_task_prefers_the_repo_the_promise_names():
    body = room_task('fix the login in ascend vision', REPOS, newest=lambda dirs: dirs[0])
    assert body == {'template': 'feature', 'project': 'fix-the-login', 'repo': 'D:/ascend-vision',
                    'task': 'Fix the login in ascend vision. (Queued by Vision from a promise.)'}
    assert room_task('tidy the ascend_hub readme', REPOS, newest=lambda dirs: dirs[1])['repo'] == 'D:/ascend_hub'


def test_room_task_falls_back_to_the_newest_repo_and_keeps_its_check():
    body = room_task('fix the login!!', REPOS, newest=lambda dirs: 'D:/ascend_hub')
    assert (body['repo'], body['check'], body['project']) == ('D:/ascend_hub', 'npm test', 'fix-the-login')
    assert room_task('fix the login', [], newest=lambda dirs: None) is None
    assert room_task('fix the login', REPOS, newest=lambda dirs: None) is None
    assert room_task('???', REPOS, newest=lambda dirs: dirs[0])['project'] == 'promise'


def test_newest_repo_reads_the_latest_commit_time():
    times = {'a': '1700000000', 'b': '1800000000', 'c': None}
    assert newest_repo(['a', 'b', 'c'], lambda repo, *args: times[repo]) == 'b'
    assert newest_repo(['c'], lambda repo, *args: None) is None


def test_queueing_a_promise_posts_a_start_body_to_the_room_queue(hub):
    queue = promise_queue(RoomClient(hub), newest=lambda dirs: dirs[0])
    assert queue('fix the login') == (True, 'Queued in the Agent Room for ascend_hub: fix the login.')
    path, body, _ = FakeHub.posts[-1]
    assert path == '/api/room/queue' and body['body']['repo'] == 'D:/ascend_hub'
    assert queue('Full queue test') == (False, 'The agent room did not queue it (QUEUE_FULL).')
    dead = promise_queue(RoomClient('http://127.0.0.1:1', timeout_seconds=0.5), newest=lambda dirs: dirs[0])
    assert dead('fix the login') == (False, "I couldn't reach the agent room.")
    lost = promise_queue(RoomClient(hub), newest=lambda dirs: None)
    assert lost('fix the login') == (False, "I couldn't tell which repo that is for. Queue it from the Room page.")


def test_windows_folders_from_the_room_are_named_by_their_last_part():
    windows = 'D:' + chr(92) + 'ascend_hub'
    body = room_task('fix the login in ascend hub', [{'dir': windows, 'check': None}], newest=lambda dirs: None)
    assert body['repo'] == windows


def test_queueing_waits_longer_than_a_status_read(hub):
    assert RoomClient(hub, timeout_seconds=0.2).queue({'task': 'Slow start'}) == (True, 'Queued in the Agent Room.')
