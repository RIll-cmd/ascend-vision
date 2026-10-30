"""Reads Ascend Hub's agent room and sends the owner's spoken gate answers back to it."""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request

from assistant.room_dialog import RoomAction, RoomDialog

LOG = logging.getLogger(__name__)


class RoomClient:
    def __init__(self, base_url: str = 'http://127.0.0.1:5173', timeout_seconds: float = 3.0,
                 queue_timeout_seconds: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.timeout_seconds = timeout_seconds
        # Hub gives a queue add 30 s; giving up sooner could make a second "queue it" add a duplicate.
        self.queue_timeout_seconds = queue_timeout_seconds
        # Hub is on this computer; a system proxy must never see room traffic.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def current(self) -> dict | None:
        """The current room session, {} when there is none, or None when Hub or the room is unreachable."""
        try:
            with self._opener.open(f'{self.base_url}/api/room/sessions/current', timeout=self.timeout_seconds) as response:
                session = json.loads(response.read().decode('utf-8')).get('session')
            return session if isinstance(session, dict) else {}
        except (OSError, ValueError, AttributeError):
            return None

    def send(self, action: RoomAction) -> tuple[bool, str]:
        if action.kind == 'resume':
            path, body = '/api/room/sessions/current/resume', {'action': action.decision}
        else:
            path, body = '/api/room/sessions/current/gate', {'gate': action.gate, 'decision': action.decision}
            if action.note:
                body['note'] = action.note
        request = urllib.request.Request(f'{self.base_url}{path}', data=json.dumps(body).encode('utf-8'),
                                         headers={'content-type': 'application/json'}, method='POST')
        try:
            with self._opener.open(request, timeout=self.timeout_seconds):
                return True, 'Done. The agent room has your answer.'
        except urllib.error.HTTPError as error:
            try:
                code = json.loads(error.read().decode('utf-8')).get('error', error.code)
            except (ValueError, AttributeError):
                code = error.code
            return False, f'The agent room did not take that answer ({code}).'
        except OSError:
            return False, "I couldn't reach the agent room."


    def status(self) -> dict | None:
        """The room's status (its repos and their suggested checks), or None when it is unreachable."""
        try:
            with self._opener.open(f'{self.base_url}/api/room', timeout=self.timeout_seconds) as response:
                status = json.loads(response.read().decode('utf-8'))
            return status if isinstance(status, dict) else None
        except (OSError, ValueError):
            return None

    def queue(self, body: dict) -> tuple[bool, str]:
        """Adds a start body to the room's queue; it starts as soon as no session runs."""
        request = urllib.request.Request(f'{self.base_url}/api/room/queue', data=json.dumps({'body': body}).encode('utf-8'),
                                         headers={'content-type': 'application/json'}, method='POST')
        try:
            with self._opener.open(request, timeout=self.queue_timeout_seconds):
                return True, 'Queued in the Agent Room.'
        except urllib.error.HTTPError as error:
            try:
                code = json.loads(error.read().decode('utf-8')).get('error', error.code)
            except (ValueError, AttributeError):
                code = error.code
            return False, f'The agent room did not queue it ({code}).'
        except OSError:
            return False, "I couldn't reach the agent room."


def _folder(path: str) -> str:
    return re.split(r'[\\/]', path.rstrip('\\/'))[-1]


def room_task(what: str, repos: list, *, newest) -> dict | None:
    """A Room start body for a promise: the repo it names, else the one worked on last; None without a repo."""
    dirs = [r['dir'] for r in repos if isinstance(r, dict) and isinstance(r.get('dir'), str)]
    said = ' ' + re.sub(r'[^a-z0-9]+', ' ', what.lower()) + ' '
    named = [d for d in dirs if f" {re.sub(r'[^a-z0-9]+', ' ', _folder(d).lower()).strip()} " in said]
    repo = named[0] if named else (newest(dirs) if dirs else None)
    if repo not in dirs:
        return None
    slug = '-'.join(re.sub(r'[^a-z0-9]+', ' ', what.lower()).split()[:3])[:40].strip('-')
    plain = what.strip().rstrip('.!?').strip()
    body = {'template': 'feature', 'project': slug or 'promise', 'repo': repo,
            'task': f'{plain[:1].upper()}{plain[1:]}. (Queued by Vision from a promise.)'}
    check = next((r.get('check') for r in repos if isinstance(r, dict) and r.get('dir') == repo), None)
    if isinstance(check, str) and check.strip():
        body['check'] = check
    return body


def newest_repo(dirs: list, run_git) -> str | None:
    """The repo with the latest commit, by ``git log -1 --format=%ct``."""
    best, when = None, -1
    for repo in dirs:
        out = run_git(repo, 'log', '-1', '--format=%ct')
        if out and out.strip().isdigit() and int(out) > when:
            best, when = repo, int(out)
    return best


def promise_queue(client: RoomClient, *, newest):
    """what -> (ok, spoken reply): queues a promise in the Agent Room."""
    def queue(what: str) -> tuple[bool, str]:
        status = client.status()
        if status is None:
            return False, "I couldn't reach the agent room."
        body = room_task(what, status.get('repos') or [], newest=newest)
        if body is None:
            return False, "I couldn't tell which repo that is for. Queue it from the Room page."
        ok, message = client.queue(body)
        return (True, f'Queued in the Agent Room for {_folder(body["repo"])}: {what}.') if ok else (False, message)
    return queue


class RoomWatcher:
    """Polls the room and speaks what the dialog announces; stops on close()."""

    def __init__(self, client: RoomClient, dialog: RoomDialog, announce, poll_seconds: float = 3.0):
        self.client, self.dialog, self.announce, self.poll_seconds = client, dialog, announce, poll_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='agent-room-watcher', daemon=True)

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=self.poll_seconds + self.client.timeout_seconds + 1)

    def _run(self):
        while not self._stop.is_set():
            try:
                for line in self.dialog.observe(self.client.current()):
                    self.announce(line)
            except Exception:  # A speech or parsing failure must not end the watcher.
                LOG.exception('Agent room watcher step failed')
            self._stop.wait(self.poll_seconds)
