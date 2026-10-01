"""Small, credential-free desktop readiness record. No hardware imports."""
from __future__ import annotations

import os
import threading
from uuid import uuid4


class RuntimeHealth:
    def __init__(self, *, instance_id=None, build_id=None):
        self.instance_id = instance_id or os.environ.get('ASCEND_INSTANCE_ID') or uuid4().hex
        self.build_id = build_id or os.environ.get('ASCEND_BUILD_ID') or 'development'
        self._lock = threading.Lock()
        self._capabilities = {
            'ui': 'starting', 'chat': 'starting', 'camera': 'starting',
            'microphone': 'unknown', 'core': 'unchecked', 'ai': 'unknown',
        }
        self._failure = None

    def update(self, **capabilities):
        if capabilities.keys() - self._capabilities.keys():
            raise ValueError('Unknown capability')
        if any(not isinstance(value, str) or len(value) > 40 for value in capabilities.values()):
            raise ValueError('Invalid capability state')
        with self._lock:
            self._capabilities.update(capabilities)

    def fail(self, code):
        if code not in {'chat_initialization_failed', 'host_stopping'}:
            raise ValueError('Unknown failure code')
        with self._lock:
            self._failure = code

    def snapshot(self):
        with self._lock:
            caps = dict(self._capabilities)
            failure = self._failure
        if failure:
            state = 'stopping' if failure == 'host_stopping' else 'failed'
        elif caps['ui'] != 'ready' or caps['chat'] != 'ready':
            state = 'starting'
        elif any(value in {'unavailable', 'request-failed', 'not-configured', 'offline',
                           'reauth-required', 'unreachable'} for value in caps.values()):
            state = 'degraded'
        else:
            state = 'ready'
        return {'schemaVersion': 1, 'component': 'vision',
                'instanceId': self.instance_id, 'buildId': self.build_id,
                'state': state, 'capabilities': caps, 'failureCode': failure}


def microphone_availability(listener, *, configured: bool) -> str:
    """Report working voice input, not merely an enabled preference."""
    if not configured:
        return 'disabled'
    if listener is None or listener is False:
        return 'starting'
    if not getattr(listener, 'enabled', False):
        return 'unavailable'
    thread = getattr(listener, '_thread', None)
    if thread is None:
        return 'starting'
    return 'ready' if thread.is_alive() else 'unavailable'
