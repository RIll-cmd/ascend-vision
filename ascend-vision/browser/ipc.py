"""Owner-only Windows named-pipe IPC for the separate browser broker process."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any

from browser.contracts import BrowserTaskRequest, MAX_FRAME_BYTES
from browser.service import BrowserTaskEvent, EventPage, TaskNotFound, TaskQueueFull, TaskReceipt


MAX_FRAME_BYTES = 64 * 1024
BROKER_KEYRING_SERVICE = 'Ascend Vision Browser Broker'


def broker_identity() -> str:
    install_root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(str(install_root).casefold().encode('utf-8')).hexdigest()[:24]


def broker_pipe_address() -> str:
    return rf'\\.\pipe\AscendVisionBrowser-{broker_identity()}'


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError('Windows Credential Manager is required for browser broker IPC.') from exc
    return keyring


def _owner_only_pipe_listener(address: str):
    if os.name != 'nt':
        raise RuntimeError('The Vision browser broker currently requires Windows named pipes.')
    import win32api
    import win32con
    import win32pipe
    import win32security
    from multiprocessing import connection as multiprocessing_connection
    import _winapi

    class OwnerOnlyPipeListener(multiprocessing_connection.PipeListener):
        def _new_handle(self, first=False):
            process_token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY,
            )
            user_sid = win32security.GetTokenInformation(
                process_token, win32security.TokenUser,
            )[0]
            sid_text = win32security.ConvertSidToStringSid(user_sid)
            descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{sid_text})',
                win32security.SDDL_REVISION_1,
            )
            attributes = win32security.SECURITY_ATTRIBUTES()
            attributes.SECURITY_DESCRIPTOR = descriptor
            attributes.bInheritHandle = False
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            mode = (
                _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT
                | win32pipe.PIPE_REJECT_REMOTE_CLIENTS
            )
            handle = win32pipe.CreateNamedPipe(
                self._address, flags, mode, _winapi.PIPE_UNLIMITED_INSTANCES,
                MAX_FRAME_BYTES, MAX_FRAME_BYTES, 0, attributes,
            )
            return handle.Detach()

    return OwnerOnlyPipeListener(address)


class BrowserIpcClient:
    """Short-lived, bounded JSON requests to the owner-only local browser broker."""

    def __init__(self, address: str, authkey: bytes):
        if not isinstance(address, str) or not address.startswith('\\\\.\\pipe\\'):
            raise ValueError('browser IPC must use a local Windows named pipe')
        if not isinstance(authkey, bytes) or len(authkey) < 32:
            raise ValueError('browser IPC authkey must contain at least 32 bytes')
        self._address = address
        self._authkey = authkey

    @classmethod
    def from_keyring(cls) -> 'BrowserIpcClient':
        identity = broker_identity()
        encoded = _keyring().get_password(BROKER_KEYRING_SERVICE, identity)
        if not isinstance(encoded, str):
            raise RuntimeError('The local browser service is not running.')
        try:
            authkey = bytes.fromhex(encoded)
        except ValueError as exc:
            raise RuntimeError('Browser IPC credential is invalid.') from exc
        return cls(broker_pipe_address(), authkey)

    def _request(self, request: dict) -> dict:
        from multiprocessing.connection import AuthenticationError, Client

        raw = json.dumps(request, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError('browser IPC request exceeds 64 KiB')
        try:
            with Client(self._address, family='AF_PIPE', authkey=self._authkey) as connection:
                connection.send_bytes(raw)
                response_raw = connection.recv_bytes(MAX_FRAME_BYTES)
        except AuthenticationError as exc:
            raise RuntimeError('The local browser broker rejected this client.') from exc
        except OSError as exc:
            raise RuntimeError('The local browser broker is unavailable.') from exc
        try:
            response = json.loads(response_raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError('The local browser broker returned an invalid response.') from exc
        if not isinstance(response, dict) or type(response.get('ok')) is not bool:
            raise RuntimeError('The local browser broker returned an invalid response.')
        if not response['ok']:
            code = response.get('error')
            if code == 'not_found':
                raise PermissionError('Browser task was not found for this local session.')
            if code == 'queue_full':
                raise TaskQueueFull('The local browser task queue is full.')
            if code == 'invalid_request':
                raise ValueError('The browser broker rejected the request.')
            raise RuntimeError('The local browser broker could not process the request.')
        data = response.get('data')
        if not isinstance(data, dict):
            raise RuntimeError('The local browser broker returned invalid data.')
        return data

    def ping(self) -> bool:
        return self._request({'operation': 'ping'}).get('ready') is True

    def submit(self, request: BrowserTaskRequest) -> TaskReceipt:
        if not isinstance(request, BrowserTaskRequest):
            raise TypeError('request must be a validated BrowserTaskRequest')
        data = self._request({'operation': 'submit', 'request': request.to_payload()})
        return _receipt(data)

    def events(self, task_id: str, after: int, session_key: tuple[str, str, str]) -> EventPage:
        data = self._request({
            'operation': 'events', 'task_id': task_id, 'after': after,
            'session_key': list(session_key),
        })
        events = tuple(BrowserTaskEvent(**event) for event in data['events'])
        return EventPage(data['task_id'], data['state'], events, data['next_cursor'],
                         data['reset_required'], data.get('result'))

    def control(self, task_id: str, command: str, session_key: tuple[str, str, str]) -> TaskReceipt:
        data = self._request({
            'operation': 'control', 'task_id': task_id,
            'command': command, 'session_key': list(session_key),
        })
        return _receipt(data)

    def decide(self, task_id: str, action_id: str, proposal_digest: str, approved: bool,
               session_key: tuple[str, str, str]) -> TaskReceipt:
        data = self._request({
            'operation': 'decide', 'task_id': task_id, 'action_id': action_id,
            'proposal_digest': proposal_digest, 'approved': approved,
            'session_key': list(session_key),
        })
        return _receipt(data)

    def shutdown(self) -> None:
        self._request({'operation': 'shutdown'})


def _receipt(data: dict) -> TaskReceipt:
    return TaskReceipt(data['task_id'], data['state'], data['event_cursor'], data.get('result'))


class BrowserPipeServer:
    def __init__(self, address: str, authkey: bytes, service):
        if not isinstance(authkey, bytes) or len(authkey) < 32:
            raise ValueError('browser IPC authkey must contain at least 32 bytes')
        self._address = address
        self._authkey = authkey
        self._service = service
        self._stop = threading.Event()
        self.ready = threading.Event()
        self._listener = None

    def serve_forever(self) -> None:
        from multiprocessing.connection import AuthenticationError, answer_challenge, deliver_challenge

        listener = _owner_only_pipe_listener(self._address)
        self._listener = listener
        self.ready.set()
        try:
            while not self._stop.is_set():
                try:
                    connection = listener.accept()
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                try:
                    deliver_challenge(connection, self._authkey)
                    answer_challenge(connection, self._authkey)
                    raw = connection.recv_bytes(MAX_FRAME_BYTES)
                    if len(raw) > MAX_FRAME_BYTES:
                        self._send(connection, {'ok': False, 'error': 'invalid_request'})
                        continue
                    try:
                        request = json.loads(raw.decode('utf-8'))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._send(connection, {'ok': False, 'error': 'invalid_request'})
                        continue
                    should_stop = self._dispatch(connection, request)
                    if should_stop:
                        break
                except (OSError, EOFError, PermissionError, AuthenticationError):
                    # A malformed or unauthorized local client cannot terminate the broker.
                    continue
                finally:
                    connection.close()
        finally:
            self._listener = None
            listener.close()

    @staticmethod
    def _send(connection, response: dict) -> None:
        raw = json.dumps(response, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
        if len(raw) > MAX_FRAME_BYTES:
            raw = b'{"ok":false,"error":"invalid_request"}'
        connection.send_bytes(raw)

    def _dispatch(self, connection, request: Any) -> bool:
        if not isinstance(request, dict) or not isinstance(request.get('operation'), str):
            self._send(connection, {'ok': False, 'error': 'invalid_request'})
            return False
        operation = request['operation']
        fields = {
            'ping': {'operation'},
            'submit': {'operation', 'request'},
            'events': {'operation', 'task_id', 'after', 'session_key'},
            'control': {'operation', 'task_id', 'command', 'session_key'},
            'decide': {'operation', 'task_id', 'action_id', 'proposal_digest', 'approved', 'session_key'},
            'shutdown': {'operation'},
        }.get(operation)
        if fields is None or request.keys() != fields:
            self._send(connection, {'ok': False, 'error': 'invalid_request'})
            return False
        try:
            if operation == 'ping':
                data = {'ready': True}
            elif operation == 'submit':
                receipt = self._service.submit(BrowserTaskRequest.from_payload(request['request']))
                data = _receipt_payload(receipt)
            elif operation == 'events':
                key = _session_key(request['session_key'])
                page = self._service.events(request['task_id'], request['after'], key)
                data = _event_page_payload(page)
            elif operation == 'control':
                key = _session_key(request['session_key'])
                receipt = self._service.control(request['task_id'], request['command'], key)
                data = _receipt_payload(receipt)
            elif operation == 'decide':
                key = _session_key(request['session_key'])
                receipt = self._service.decide(
                    request['task_id'], request['action_id'], request['proposal_digest'],
                    request['approved'], key,
                )
                data = _receipt_payload(receipt)
            else:
                data = {'stopping': True}
                self._send(connection, {'ok': True, 'data': data})
                self._stop.set()
                return True
            self._send(connection, {'ok': True, 'data': data})
        except TaskNotFound:
            self._send(connection, {'ok': False, 'error': 'not_found'})
        except TaskQueueFull:
            self._send(connection, {'ok': False, 'error': 'queue_full'})
        except (ValueError, TypeError, KeyError):
            self._send(connection, {'ok': False, 'error': 'invalid_request'})
        except Exception:
            self._send(connection, {'ok': False, 'error': 'unavailable'})
        return False

    def stop(self) -> None:
        self._stop.set()


def _session_key(value: Any) -> tuple[str, str, str]:
    if (not isinstance(value, list) or len(value) != 3
            or any(not isinstance(item, str) or not item or len(item) > 128 for item in value)
            or value[1] not in {'dashboard', 'voice'}):
        raise ValueError('invalid local browser session')
    return tuple(value)


def _receipt_payload(receipt: TaskReceipt) -> dict:
    return {
        'task_id': receipt.task_id, 'state': receipt.state,
        'event_cursor': receipt.event_cursor, 'result': receipt.result,
    }


def _event_page_payload(page: EventPage) -> dict:
    return {
        'task_id': page.task_id, 'state': page.state,
        'events': [event.__dict__ for event in page.events],
        'next_cursor': page.next_cursor,
        'reset_required': page.reset_required,
        'result': page.result,
    }
