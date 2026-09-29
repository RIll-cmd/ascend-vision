import httpx
import pytest

from browser.remote_contracts import RemoteBrowserBinding
from browser.remote_guard import RemoteDispatchGuard


def binding():
    return RemoteBrowserBinding.from_payload({
        'task_id': 'd2f62db5-31a7-43d8-9f17-9088ddc921d0',
        'owner_id': 'owner-1', 'channel': 'phone_pwa',
        'browser_session_id': '53260688-54d7-465b-ae45-d510060dc3e2',
        'laptop_id': 'laptop-1', 'broker_boot_id': 'boot-1', 'lease_id': 'lease-1',
        'fence': 2, 'scope_id': 'public_research', 'scope_version': 1,
    })


def test_guard_requires_exact_core_permit_and_binding():
    expected = {
        'permitId': 'permit-1', 'expiresAt': 1_800_000_001.0,
        'taskId': binding().task_id, 'attemptId': 'attempt-1', 'laptopId': 'laptop-1',
        'brokerBootId': 'boot-1', 'fence': 2, 'action': 'observe', 'proposalDigest': None,
    }
    def handler(request):
        assert request.url.path.endswith('/authorize-dispatch')
        assert request.headers['X-Browser-Lease'] == 'lease-1'
        assert request.headers['X-Browser-Fence'] == '2'
        assert request.headers['X-Browser-Boot'] == 'boot-1'
        import json
        assert set(json.loads(request.read())) == {'attemptId', 'action', 'proposalDigest'}
        return httpx.Response(200, json=expected)
    guard = RemoteDispatchGuard(
        'https://core.example', 'worker-secret', owner_id='owner-1', laptop_id='laptop-1',
        transport=httpx.MockTransport(handler), clock=lambda: 1_800_000_000.0,
    )
    try:
        permit = guard.authorize(binding(), 'attempt-1', 'observe')
    finally:
        guard.close()
    assert permit.permit_id == 'permit-1'
    assert permit.fence == 2


def test_guard_rejects_unbound_permit():
    guard = RemoteDispatchGuard(
        'https://core.example', 'worker-secret', owner_id='owner-1', laptop_id='laptop-1',
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
            'permitId': 'permit-1', 'expiresAt': 1_800_000_001.0,
            'taskId': 'wrong-task', 'attemptId': 'attempt-1', 'laptopId': 'laptop-1',
            'brokerBootId': 'boot-1', 'fence': 2, 'action': 'observe', 'proposalDigest': None,
        })), clock=lambda: 1_800_000_000.0,
    )
    try:
        with pytest.raises(PermissionError, match='invalid remote browser permit'):
            guard.authorize(binding(), 'attempt-1', 'observe')
    finally:
        guard.close()


def test_guard_rejects_remote_writes_without_review_digest():
    guard = RemoteDispatchGuard('https://core.example', 'worker-secret', owner_id='owner-1',
                                laptop_id='laptop-1')
    try:
        with pytest.raises(PermissionError, match='require an approved proposal'):
            guard.authorize(binding(), 'attempt-1', 'fill')
    finally:
        guard.close()


def test_guard_allows_public_research_link_click_without_review_digest():
    guard = RemoteDispatchGuard('https://core.example', 'worker-secret', owner_id='owner-1',
                                laptop_id='laptop-1', transport=httpx.MockTransport(
                                    lambda _request: httpx.Response(200, json={
                                        'permitId': 'permit-1', 'expiresAt': 1_800_000_001.0,
                                        'taskId': binding().task_id, 'attemptId': 'attempt-1',
                                        'laptopId': 'laptop-1', 'brokerBootId': 'boot-1',
                                        'fence': 2, 'action': 'click', 'proposalDigest': None,
                                    })), clock=lambda: 1_800_000_000.0)
    try:
        assert guard.authorize(binding(), 'attempt-1', 'click').action == 'click'
    finally:
        guard.close()


def test_guard_requires_https_outside_loopback():
    with pytest.raises(ValueError, match='HTTPS'):
        RemoteDispatchGuard('http://core.example', 'worker-secret', owner_id='owner-1',
                            laptop_id='laptop-1')
