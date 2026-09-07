"""Serialize mode requests and hold persistence on the application's main thread."""
from datetime import datetime, timezone
import logging
import queue
import time
import threading

LOG = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, database, *, now=None, heartbeat_seconds=5., update_seconds=1.):
        self.db = database
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.heartbeat_seconds = heartbeat_seconds
        self.update_seconds = update_seconds
        self.mode = 'background'
        self.session_id = None
        self.quit_requested = False
        self._requests = queue.Queue(maxsize=64)
        self._active_by_type: dict[str, tuple[int, int]] = {}
        self._last_event_id_by_type: dict[str, int] = {}
        self._last_event_id = 0
        self._last_heartbeat = self._last_update = 0.
        self._quit = threading.Event()

    @property
    def _active(self):
        if 'phone_held' in self._active_by_type:
            return self._active_by_type['phone_held']
        if self._active_by_type:
            return next(iter(self._active_by_type.values()))
        return None

    @_active.setter
    def _active(self, val):
        if val is None:
            self._active_by_type.clear()
        else:
            self._active_by_type['phone_held'] = val

    @property
    def active_event_id(self):
        act = self._active
        return act[0] if act is not None else None

    @property
    def active_event_types(self) -> set[str]:
        return set(self._active_by_type.keys())

    def start(self, mode='background'):
        if self.session_id is not None:
            raise RuntimeError('Session manager has already started')
        recovered = self.db.recover()
        if recovered:
            LOG.warning('Recovered %d interrupted session(s) at their last heartbeat', recovered)
        self.session_id = self.db.start_session(mode, self.now())
        self.mode = mode
        self._last_heartbeat = time.monotonic()
        LOG.info('SESSION_STARTED id=%d mode=%s', self.session_id, self.mode)

    def request(self, command):
        if command not in ('focus', 'background', 'toggle', 'quit'):
            raise ValueError('Unknown session command')
        if command == 'quit':
            self._quit.set()
            return
        # Bounded queue avoids unbounded key-repeat/callback accumulation.
        try:
            self._requests.put_nowait(command)
        except queue.Full:
            LOG.warning('Session command queue full; request ignored')

    def process_commands(self, at=None):
        if self._quit.is_set():
            self.quit_requested = True
            return
        at = at or self.now()
        for _ in range(64):
            try:
                command = self._requests.get_nowait()
            except queue.Empty:
                break
            if command == 'quit':
                self.quit_requested = True
                break
            mode = ('focus' if self.mode == 'background' else 'background') if command == 'toggle' else command
            if mode != self.mode:
                self.session_id = self.db.switch_session(self.session_id, mode, at)
                self.mode = mode
                LOG.info('SESSION_STARTED id=%d mode=%s', self.session_id, mode)
        if time.monotonic() - self._last_heartbeat >= self.heartbeat_seconds:
            self.db.heartbeat(self.session_id, at)
            self._last_heartbeat = time.monotonic()

    def record_start(self, event, event_type='phone_held', posture=None):
        if event_type in self._active_by_type:
            # If an earlier event of the same type was still marked active, close it cleanly
            old_eid, old_db_id = self._active_by_type[event_type]
            LOG.warning('Closing unended active event %s (type=%s) before starting new event %s',
                        old_eid, event_type, event.id)
            del self._active_by_type[event_type]
        if event.id <= self._last_event_id_by_type.get(event_type, 0):
            raise ValueError('Duplicate or overlapping hold event')
        sid, mode = self.db.session_at(event.started_at)
        actual_posture = getattr(event, 'posture', posture)
        event_id = self.db.start_event(sid, event.started_at, getattr(event, 'confidence', None),
                                       event_type=event_type, posture=actual_posture)
        self._active_by_type[event_type] = (event.id, event_id)
        self._last_event_id_by_type[event_type] = event.id
        self._last_event_id = max(self._last_event_id, event.id)
        self._last_update = time.monotonic()
        # A pickup is also durable evidence that the application is still running.
        self.db.heartbeat(self.session_id, self.now())
        LOG.info('EVENT_SAVED id=%d session_id=%d mode=%s event_type=%s posture=%s',
                 event_id, sid, mode, event_type, actual_posture)
        return event_id

    def record_update(self, event, *, ended=False, event_type=None):
        target_type = None
        if event_type is not None:
            if event_type in self._active_by_type and self._active_by_type[event_type][0] == event.id:
                target_type = event_type
        else:
            for etype, (eid, db_eid) in list(self._active_by_type.items()):
                if eid == event.id:
                    target_type = etype
                    break

        if target_type is None:
            LOG.debug('record_update ignored for unpersisted or already closed event %s (type=%s)',
                      getattr(event, 'id', None), event_type)
            return

        db_event_id = self._active_by_type[target_type][1]
        if ended or time.monotonic() - self._last_update >= self.update_seconds:
            self.db.update_event(db_event_id, event.duration_seconds)
            self.db.heartbeat(self.session_id, self.now())
            self._last_update = time.monotonic()
        if ended:
            del self._active_by_type[target_type]

    def close(self, at=None):
        if self.session_id is not None:
            self.db.end_session(self.session_id, at or self.now())
            LOG.info('SESSION_ENDED id=%d mode=%s', self.session_id, self.mode)
            self.session_id = None
