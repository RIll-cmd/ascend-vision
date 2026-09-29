"""Core-backed, just-before-dispatch authorization for remote browser work."""

from __future__ import annotations

from dataclasses import dataclass
import time
from urllib.parse import urlsplit

import httpx

from browser.remote_contracts import RemoteBrowserBinding


@dataclass(frozen=True)
class DispatchPermit:
    permit_id: str
    attempt_id: str
    task_id: str
    laptop_id: str
    broker_boot_id: str
    fence: int
    action: str
    proposal_digest: str | None
    expires_at: float


class RemoteDispatchGuard:
    """Synchronous guard used inside the broker immediately before remote work."""

    def __init__(self, base_url: str, worker_token: str, *, owner_id: str,
                 laptop_id: str, timeout_seconds: float = 2.0, transport=None,
                 clock=time.time, monotonic=time.monotonic):
        parts = urlsplit(base_url.strip())
        if (parts.scheme not in {'https', 'http'} or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment
                or (parts.scheme != 'https' and parts.hostname not in {'localhost', '127.0.0.1', '::1'})):
            raise ValueError('browser Core URL must be HTTPS (HTTP is allowed only for loopback development)')
        if not worker_token.strip() or not owner_id.strip() or not laptop_id.strip():
            raise ValueError('browser dispatch guard credentials are incomplete')
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 2:
            raise ValueError('remote dispatch authorization timeout must be at most 2 seconds')
        self.owner_id = owner_id.strip()
        self.laptop_id = laptop_id.strip()
        self.timeout_seconds = float(timeout_seconds)
        self._clock = clock
        self._monotonic = monotonic
        self._client = httpx.Client(
            base_url=base_url.rstrip('/'), timeout=self.timeout_seconds, transport=transport,
            headers={'Authorization': f'Bearer {worker_token.strip()}'},
        )

    def authorize(self, binding: RemoteBrowserBinding, attempt_id: str, action: str,
                  proposal_digest: str | None = None) -> DispatchPermit:
        if not isinstance(binding, RemoteBrowserBinding):
            raise TypeError('binding must be a validated RemoteBrowserBinding')
        if binding.owner_id != self.owner_id or binding.laptop_id != self.laptop_id:
            raise PermissionError('Remote browser identity does not match this installation.')
        if not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 128:
            raise ValueError('dispatch attempt identity is invalid')
        if action not in {'observe', 'plan', 'finish', 'navigate', 'click', 'fill', 'select',
                          'scroll', 'back', 'wait_for'}:
            raise PermissionError('Remote browser action is not supported.')
        if proposal_digest is not None and (not isinstance(proposal_digest, str)
                                            or len(proposal_digest) != 64
                                            or any(c not in '0123456789abcdef' for c in proposal_digest)):
            raise ValueError('proposal digest is invalid')
        if (action in {'fill', 'select'}
                or (action == 'click' and binding.scope_id != 'public_research')) and proposal_digest is None:
            raise PermissionError('Consequential browser actions require an approved proposal.')
        started = self._monotonic()
        expires_before = self._clock() + self.timeout_seconds
        response = self._client.post(
            f'/api/browser-tasks/worker/tasks/{binding.task_id}/authorize-dispatch',
            headers={
                'X-Browser-Lease': binding.lease_id,
                'X-Browser-Fence': str(binding.fence),
                'X-Browser-Boot': binding.broker_boot_id,
            },
            json={'attemptId': attempt_id, 'action': action, 'proposalDigest': proposal_digest},
        )
        if self._monotonic() - started > self.timeout_seconds:
            raise PermissionError('Remote browser dispatch authorization expired before dispatch.')
        if response.status_code in {401, 403, 404, 409, 410}:
            raise PermissionError('Core no longer authorizes this remote browser task.')
        response.raise_for_status()
        try:
            data = response.json()
            expected = {'permitId', 'expiresAt', 'taskId', 'attemptId', 'laptopId',
                        'brokerBootId', 'fence', 'action', 'proposalDigest'}
            if not isinstance(data, dict) or data.keys() != expected:
                raise ValueError('permit fields are invalid')
            expiry = data['expiresAt']
            if isinstance(expiry, str):
                from datetime import datetime
                expiry = datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp()
            if (not isinstance(data['permitId'], str) or not data['permitId']
                    or data['taskId'] != binding.task_id or data['attemptId'] != attempt_id
                    or data['laptopId'] != binding.laptop_id
                    or data['brokerBootId'] != binding.broker_boot_id
                    or type(data['fence']) is not int or data['fence'] != binding.fence
                    or data['action'] != action or data['proposalDigest'] != proposal_digest
                    or isinstance(expiry, bool) or not isinstance(expiry, (float, int))
                    or not self._clock() < float(expiry) <= expires_before):
                raise ValueError('permit binding or expiry is invalid')
        except (ValueError, TypeError, KeyError) as exc:
            raise PermissionError('Core returned an invalid remote browser permit.') from exc
        return DispatchPermit(data['permitId'], attempt_id, binding.task_id,
                              binding.laptop_id, binding.broker_boot_id, binding.fence,
                              action, proposal_digest, float(expiry))

    def close(self) -> None:
        self._client.close()
