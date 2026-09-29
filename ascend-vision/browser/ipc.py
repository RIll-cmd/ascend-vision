"""Owner-only Windows named-pipe IPC for the separate browser broker process."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any

from browser.contracts import BrowserTaskRequest, MAX_FRAME_BYTES
from browser.remote_contracts import RemoteBrowserBinding, RemoteBrowserTaskRequest
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

    def availability(self) -> dict:
        data = self._request({'operation': 'availability'})
        if set(data) != {'busy', 'broker_boot_id'} or type(data['busy']) is not bool:
            raise RuntimeError('The local browser broker returned invalid availability.')
        if not isinstance(data['broker_boot_id'], str) or not data['broker_boot_id']:
            raise RuntimeError('The local browser broker did not provide its boot identity.')
        return data

    def metrics_status(self) -> dict:
        return self._request({'operation': 'metrics_status'})

    def metrics_summary(self, cohort: str = 'all') -> dict:
        if cohort not in {'all', 'live_public', 'pwa', 'discord', 'signed_in', 'fixture'}:
            raise ValueError('metrics cohort is invalid')
        return self._request({'operation': 'metrics_summary', 'cohort': cohort})

    def metrics_set_enabled(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError('metrics enabled must be boolean')
        return self._request({'operation': 'metrics_set_enabled', 'enabled': enabled})

    def metrics_export(self, cursor: int = 0) -> dict:
        if type(cursor) is not int or cursor < 0:
            raise ValueError('metrics export cursor is invalid')
        return self._request({'operation': 'metrics_export', 'cursor': cursor})

    def metrics_clear(self) -> dict:
        return self._request({'operation': 'metrics_clear'})

    def feedback(self, task_id: str, verdict: str, session_key: tuple[str, str, str]) -> bool:
        if verdict not in {'worked', 'needed_correction', 'did_not_work'}:
            raise ValueError('feedback verdict is invalid')
        data = self._request({'operation': 'feedback', 'task_id': task_id,
                              'verdict': verdict, 'session_key': list(session_key)})
        if type(data.get('recorded')) is not bool:
            raise RuntimeError('the local browser broker returned invalid feedback status')
        return data['recorded']

    def broker_identity(self) -> str:
        value = self._request({'operation': 'ping'}).get('broker_boot_id')
        if not isinstance(value, str) or not value:
            raise RuntimeError('The local browser broker did not provide its boot identity.')
        return value

    def submit(self, request: BrowserTaskRequest) -> TaskReceipt:
        if not isinstance(request, BrowserTaskRequest):
            raise TypeError('request must be a validated BrowserTaskRequest')
        data = self._request({'operation': 'submit', 'request': request.to_payload()})
        return _receipt(data)

    def routine_catalogue(self) -> tuple[dict, ...]:
        data = self._request({'operation': 'routine_catalogue'}).get('routines')
        if not isinstance(data, list) or len(data) > 20 or any(not isinstance(item, dict) for item in data):
            raise RuntimeError('the local browser broker returned an invalid routine catalogue')
        return tuple(data)

    def submit_routine(self, request: BrowserTaskRequest, invocation) -> TaskReceipt:
        from browser.routines.contracts import RoutineInvocation
        if not isinstance(request, BrowserTaskRequest) or not isinstance(invocation, RoutineInvocation):
            raise TypeError('routine submission requires validated browser contracts')
        return _receipt(self._request({
            'operation': 'submit_routine', 'request': request.to_payload(),
            'invocation': invocation.to_payload(),
        }))

    def disable_routine(self, routine_id: str, version: int) -> bool:
        data = self._request({'operation': 'disable_routine', 'routine_id': routine_id, 'version': version})
        if type(data.get('disabled')) is not bool:
            raise RuntimeError('the local browser broker returned an invalid routine status')
        return data['disabled']

    def enable_routine(self, routine_id: str, version: int) -> bool:
        data = self._request({'operation': 'enable_routine', 'routine_id': routine_id, 'version': version})
        if type(data.get('enabled')) is not bool:
            raise RuntimeError('the local browser broker returned an invalid routine status')
        return data['enabled']

    def submit_remote(self, request: RemoteBrowserTaskRequest) -> TaskReceipt:
        if not isinstance(request, RemoteBrowserTaskRequest):
            raise TypeError('request must be a validated remote browser request')
        return _receipt(self._request({
            'operation': 'submit_remote', 'request': request.to_payload(),
        }))

    def remote_events(self, binding: RemoteBrowserBinding, after: int) -> EventPage:
        if not isinstance(binding, RemoteBrowserBinding) or type(after) is not int or after < 0:
            raise ValueError('remote browser binding or event cursor is invalid')
        data = self._request({
            'operation': 'remote_events', 'binding': binding.to_payload(), 'after': after,
        })
        events = tuple(BrowserTaskEvent(**event) for event in data['events'])
        return EventPage(data['task_id'], data['state'], events, data['next_cursor'],
                         data['reset_required'], data.get('result'), data.get('proposal'),
                         data.get('waiting_for_user_type'))

    def remote_control(self, binding: RemoteBrowserBinding, command: str) -> TaskReceipt:
        if not isinstance(binding, RemoteBrowserBinding):
            raise TypeError('binding must be validated')
        return _receipt(self._request({
            'operation': 'remote_control', 'binding': binding.to_payload(), 'command': command,
        }))

    def remote_decide(self, binding: RemoteBrowserBinding, action_id: str,
                      proposal_digest: str, approved: bool) -> TaskReceipt:
        if not isinstance(binding, RemoteBrowserBinding):
            raise TypeError('binding must be validated')
        return _receipt(self._request({
            'operation': 'remote_decide', 'binding': binding.to_payload(),
            'action_id': action_id, 'proposal_digest': proposal_digest, 'approved': approved,
        }))

    def remote_reconciliations(self) -> list[dict]:
        rows = self._request({'operation': 'remote_reconciliations'}).get('items')
        if not isinstance(rows, list) or len(rows) > 256 or any(not isinstance(item, dict) for item in rows):
            raise RuntimeError('The local browser broker returned invalid reconciliation records.')
        return rows

    def mark_remote_reconciled(self, task_id: str) -> bool:
        data = self._request({'operation': 'mark_remote_reconciled', 'task_id': task_id})
        if set(data) != {'reconciled'} or type(data['reconciled']) is not bool:
            raise RuntimeError('The local browser broker returned invalid reconciliation status.')
        return data['reconciled']

    def mark_remote_terminal(self, binding: RemoteBrowserBinding) -> bool:
        if not isinstance(binding, RemoteBrowserBinding):
            raise TypeError('binding must be validated')
        data = self._request({'operation': 'mark_remote_terminal', 'binding': binding.to_payload()})
        if set(data) != {'terminal'} or type(data['terminal']) is not bool:
            raise RuntimeError('The local browser broker returned invalid terminal journal status.')
        return data['terminal']

    def events(self, task_id: str, after: int, session_key: tuple[str, str, str]) -> EventPage:
        data = self._request({
            'operation': 'events', 'task_id': task_id, 'after': after,
            'session_key': list(session_key),
        })
        events = tuple(BrowserTaskEvent(**event) for event in data['events'])
        return EventPage(data['task_id'], data['state'], events, data['next_cursor'],
                         data['reset_required'], data.get('result'), data.get('proposal'),
                         data.get('waiting_for_user_type'))

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

    def save_profile(self, task_id: str, session_key: tuple[str, str, str]) -> TaskReceipt:
        data = self._request({
            'operation': 'save_profile', 'task_id': task_id,
            'session_key': list(session_key),
        })
        return _receipt(data)

    def list_profiles(self, session_key: tuple[str, str, str]) -> list[str]:
        data = self._request({'operation': 'list_profiles', 'session_key': list(session_key)})
        profiles = data.get('profiles')
        if not isinstance(profiles, list) or any(not isinstance(item, str) for item in profiles):
            raise RuntimeError('The local browser broker returned invalid profile data.')
        return profiles

    def clear_profile(self, profile_id: str, session_key: tuple[str, str, str]) -> bool:
        data = self._request({
            'operation': 'clear_profile', 'profile_id': profile_id,
            'session_key': list(session_key),
        })
        return data.get('cleared') is True

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
            'availability': {'operation'},
            'metrics_status': {'operation'},
            'metrics_summary': {'operation', 'cohort'},
            'metrics_set_enabled': {'operation', 'enabled'},
            'metrics_export': {'operation', 'cursor'},
            'metrics_clear': {'operation'},
            'feedback': {'operation', 'task_id', 'verdict', 'session_key'},
            'submit': {'operation', 'request'},
            'routine_catalogue': {'operation'},
            'submit_routine': {'operation', 'request', 'invocation'},
            'disable_routine': {'operation', 'routine_id', 'version'},
            'enable_routine': {'operation', 'routine_id', 'version'},
            'submit_remote': {'operation', 'request'},
            'remote_events': {'operation', 'binding', 'after'},
            'remote_control': {'operation', 'binding', 'command'},
            'remote_decide': {'operation', 'binding', 'action_id', 'proposal_digest', 'approved'},
            'remote_reconciliations': {'operation'},
            'mark_remote_reconciled': {'operation', 'task_id'},
              'mark_remote_terminal': {'operation', 'binding'},
            'events': {'operation', 'task_id', 'after', 'session_key'},
            'control': {'operation', 'task_id', 'command', 'session_key'},
            'decide': {'operation', 'task_id', 'action_id', 'proposal_digest', 'approved', 'session_key'},
            'save_profile': {'operation', 'task_id', 'session_key'},
            'list_profiles': {'operation', 'session_key'},
            'clear_profile': {'operation', 'profile_id', 'session_key'},
            'shutdown': {'operation'},
        }.get(operation)
        if fields is None or request.keys() != fields:
            self._send(connection, {'ok': False, 'error': 'invalid_request'})
            return False
        try:
            if operation == 'ping':
                data = {'ready': True, 'broker_boot_id': getattr(self._service, 'broker_boot_id', None)}
            elif operation == 'availability':
                data = {'busy': bool(self._service.busy),
                        'broker_boot_id': getattr(self._service, 'broker_boot_id', None)}
            elif operation == 'metrics_status':
                data = self._service.metrics_status()
            elif operation == 'metrics_summary':
                data = self._service.metrics_summary(request['cohort'])
            elif operation == 'metrics_set_enabled':
                if type(request['enabled']) is not bool:
                    raise ValueError('metrics enabled must be boolean')
                data = self._service.metrics_set_enabled(request['enabled'])
            elif operation == 'metrics_export':
                if type(request['cursor']) is not int or request['cursor'] < 0:
                    raise ValueError('metrics export cursor is invalid')
                data = self._service.metrics_export(request['cursor'])
            elif operation == 'metrics_clear':
                data = self._service.metrics_clear()
            elif operation == 'feedback':
                key = _session_key(request['session_key'])
                data = {'recorded': self._service.feedback(request['task_id'], request['verdict'], key)}
            elif operation == 'submit':
                receipt = self._service.submit(BrowserTaskRequest.from_payload(request['request']))
                data = _receipt_payload(receipt)
            elif operation == 'routine_catalogue':
                data = {'routines': list(self._service.routine_catalogue())}
            elif operation == 'submit_routine':
                from browser.routines.contracts import RoutineInvocation
                routine_request = BrowserTaskRequest.from_payload(request['request'])
                invocation = RoutineInvocation.from_payload(request['invocation'])
                receipt = self._service.submit_routine(routine_request, invocation)
                data = _receipt_payload(receipt)
            elif operation == 'disable_routine':
                if (not isinstance(request['routine_id'], str) or type(request['version']) is not int
                        or request['version'] < 1):
                    raise ValueError('routine reference is invalid')
                self._service.disable_routine(request['routine_id'], request['version'])
                data = {'disabled': True}
            elif operation == 'enable_routine':
                if (not isinstance(request['routine_id'], str) or type(request['version']) is not int
                        or request['version'] < 1):
                    raise ValueError('routine reference is invalid')
                self._service.enable_routine(request['routine_id'], request['version'])
                data = {'enabled': True}
            elif operation == 'submit_remote':
                remote_request = RemoteBrowserTaskRequest.from_payload(request['request'])
                receipt = self._service.submit_remote(remote_request)
                data = _receipt_payload(receipt)
            elif operation == 'remote_events':
                binding = RemoteBrowserBinding.from_payload(request['binding'])
                page = self._service.remote_events(binding, request['after'])
                data = _event_page_payload(page)
            elif operation == 'remote_control':
                binding = RemoteBrowserBinding.from_payload(request['binding'])
                receipt = self._service.remote_control(binding, request['command'])
                data = _receipt_payload(receipt)
            elif operation == 'remote_decide':
                binding = RemoteBrowserBinding.from_payload(request['binding'])
                receipt = self._service.remote_decide(
                    binding, request['action_id'], request['proposal_digest'], request['approved'],
                )
                data = _receipt_payload(receipt)
            elif operation == 'remote_reconciliations':
                data = {'items': self._service.remote_reconciliations()}
            elif operation == 'mark_remote_reconciled':
                data = {'reconciled': self._service.mark_remote_reconciled(request['task_id'])}
            elif operation == 'mark_remote_terminal':
                binding = RemoteBrowserBinding.from_payload(request['binding'])
                data = {'terminal': self._service.mark_remote_terminal(binding)}
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
            elif operation == 'save_profile':
                key = _session_key(request['session_key'])
                receipt = self._service.save_profile(request['task_id'], key)
                data = _receipt_payload(receipt)
            elif operation == 'list_profiles':
                key = _session_key(request['session_key'])
                data = {'profiles': self._service.list_profiles(key)}
            elif operation == 'clear_profile':
                key = _session_key(request['session_key'])
                cleared = self._service.clear_profile(request['profile_id'], key)
                data = {'cleared': cleared}
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
        'proposal': page.proposal,
        'waiting_for_user_type': page.waiting_for_user_type,
    }
