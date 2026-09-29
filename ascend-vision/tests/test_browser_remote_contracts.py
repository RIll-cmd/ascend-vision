import copy
import time
from uuid import uuid4

import pytest

from browser.remote_contracts import RemoteBrowserBinding, RemoteBrowserTaskRequest


@pytest.fixture
def remote_payload():
    return {
        'schema_version': 1,
        'binding': {
            'task_id': str(uuid4()), 'owner_id': 'owner-one', 'channel': 'phone_pwa',
            'browser_session_id': str(uuid4()), 'laptop_id': 'laptop-one',
            'broker_boot_id': 'boot-123', 'lease_id': 'lease-123', 'fence': 1,
            'scope_id': 'public_research', 'scope_version': 1,
        },
        'goal': 'Read the official Python documentation.', 'provider': 'gemini',
        'provider_consent': True, 'expires_at': time.time() + 600,
    }


def test_remote_request_round_trips_full_execution_binding(remote_payload):
    request = RemoteBrowserTaskRequest.from_payload(remote_payload)
    restored = RemoteBrowserTaskRequest.from_payload(request.to_payload())

    assert restored == request
    assert restored.binding.fence == 1
    assert restored.binding.scope_id == 'public_research'


@pytest.mark.parametrize('field,value', [
    ('channel', ['phone_pwa']),
    ('broker_boot_id', '../old-boot'),
    ('fence', True),
    ('scope_version', 0),
    ('scope_id', '../private-site'),
])
def test_remote_binding_rejects_malformed_execution_identity(remote_payload, field, value):
    payload = copy.deepcopy(remote_payload['binding'])
    payload[field] = value

    with pytest.raises(ValueError):
        RemoteBrowserBinding.from_payload(payload)


def test_remote_request_rejects_unknown_fields_and_missing_provider_consent(remote_payload):
    unknown = copy.deepcopy(remote_payload)
    unknown['profile_path'] = 'C:/private/chrome'
    with pytest.raises(ValueError):
        RemoteBrowserTaskRequest.from_payload(unknown)

    missing_consent = copy.deepcopy(remote_payload)
    missing_consent['provider_consent'] = False
    with pytest.raises(ValueError, match='consent'):
        RemoteBrowserTaskRequest.from_payload(missing_consent)


def test_remote_request_rejects_expired_or_oversized_payload(remote_payload):
    expired = copy.deepcopy(remote_payload)
    expired['expires_at'] = 0
    with pytest.raises(ValueError):
        RemoteBrowserTaskRequest.from_payload(expired)

    oversized = copy.deepcopy(remote_payload)
    oversized['goal'] = 'x' * 4_001
    with pytest.raises(ValueError):
        RemoteBrowserTaskRequest.from_payload(oversized)
