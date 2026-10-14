"""Reads Ascend Hub's agent room and sends the owner's spoken gate answers back to it."""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

from assistant.room_dialog import RoomAction, RoomDialog

LOG = logging.getLogger(__name__)


class RoomClient:
    def __init__(self, base_url: str = 'http://127.0.0.1:5173', timeout_seconds: float = 3.0):
        self.base_url = base_url.rstrip('/')
        self.timeout_seconds = timeout_seconds
        # Hub is on this computer; a system proxy must never see room traffic.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def current(self) -> dict | None:
        """The current room session, or None when Hub or the room is unreachable."""
        try:
            with self._opener.open(f'{self.base_url}/api/room/sessions/current', timeout=self.timeout_seconds) as response:
                session = json.loads(response.read().decode('utf-8')).get('session')
            return session if isinstance(session, dict) else None
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
