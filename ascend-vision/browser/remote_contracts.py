"""Versioned, identity-bound contracts for Core-initiated browser tasks."""

from dataclasses import dataclass
import math
import re
from typing import Any
from uuid import UUID


REMOTE_SCHEMA_VERSION = 1
_CHANNELS = {'phone_pwa', 'discord_dm'}
_PROVIDERS = {'gemini', 'cerebras', 'groq'}
_TOKEN = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_SCOPE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
_MAX_GOAL = 4_000


def _mapping(value: Any, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError(f'{label} must contain exactly the supported fields')
    return value


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{label} must be a UUID')
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f'{label} must be a UUID') from exc


@dataclass(frozen=True)
class RemoteBrowserBinding:
    task_id: str
    owner_id: str
    channel: str
    browser_session_id: str
    laptop_id: str
    broker_boot_id: str
    lease_id: str
    fence: int
    scope_id: str
    scope_version: int

    @classmethod
    def from_payload(cls, payload: Any) -> 'RemoteBrowserBinding':
        fields = {'task_id', 'owner_id', 'channel', 'browser_session_id', 'laptop_id',
                  'broker_boot_id', 'lease_id', 'fence', 'scope_id', 'scope_version'}
        data = _mapping(payload, fields, 'remote browser binding')
        for name in ('owner_id', 'laptop_id'):
            value = data[name]
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f'{name} must be nonempty bounded text')
        if not isinstance(data['channel'], str) or data['channel'] not in _CHANNELS:
            raise ValueError('channel is not an enabled remote browser channel')
        scope_id = data['scope_id']
        if not isinstance(scope_id, str) or not _SCOPE_ID.fullmatch(scope_id):
            raise ValueError('scope_id is invalid')
        for name in ('broker_boot_id', 'lease_id'):
            value = data[name]
            if not isinstance(value, str) or not _TOKEN.fullmatch(value):
                raise ValueError(f'{name} is invalid')
        if type(data['fence']) is not int or data['fence'] < 1:
            raise ValueError('fence must be a positive integer')
        if type(data['scope_version']) is not int or data['scope_version'] < 1:
            raise ValueError('scope_version must be a positive integer')
        return cls(
            task_id=_uuid(data['task_id'], 'task_id'),
            owner_id=data['owner_id'].strip(),
            channel=data['channel'],
            browser_session_id=_uuid(data['browser_session_id'], 'browser_session_id'),
            laptop_id=data['laptop_id'].strip(),
            broker_boot_id=data['broker_boot_id'], lease_id=data['lease_id'],
            fence=data['fence'], scope_id=scope_id, scope_version=data['scope_version'],
        )

    def to_payload(self) -> dict:
        return {
            'task_id': self.task_id, 'owner_id': self.owner_id, 'channel': self.channel,
            'browser_session_id': self.browser_session_id, 'laptop_id': self.laptop_id,
            'broker_boot_id': self.broker_boot_id, 'lease_id': self.lease_id,
            'fence': self.fence, 'scope_id': self.scope_id, 'scope_version': self.scope_version,
        }


@dataclass(frozen=True)
class RemoteBrowserTaskRequest:
    binding: RemoteBrowserBinding
    goal: str
    provider: str
    provider_consent: bool
    expires_at: float
    schema_version: int = REMOTE_SCHEMA_VERSION

    @classmethod
    def from_payload(cls, payload: Any) -> 'RemoteBrowserTaskRequest':
        fields = {'schema_version', 'binding', 'goal', 'provider', 'provider_consent', 'expires_at'}
        data = _mapping(payload, fields, 'remote browser task')
        if type(data['schema_version']) is not int or data['schema_version'] != REMOTE_SCHEMA_VERSION:
            raise ValueError('unsupported remote browser schema version')
        goal = data['goal']
        if not isinstance(goal, str) or not goal.strip() or len(goal) > _MAX_GOAL:
            raise ValueError('goal must contain 1–4000 characters')
        provider = data['provider']
        if not isinstance(provider, str) or provider not in _PROVIDERS:
            raise ValueError('provider is not enabled for browser decisions')
        if type(data['provider_consent']) is not bool or not data['provider_consent']:
            raise ValueError('explicit provider consent is required')
        expires_at = data['expires_at']
        if (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                or not math.isfinite(expires_at) or expires_at <= 0):
            raise ValueError('expires_at must be a finite positive timestamp')
        return cls(RemoteBrowserBinding.from_payload(data['binding']), goal.strip(), provider,
                   True, float(expires_at), data['schema_version'])

    def to_payload(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'binding': self.binding.to_payload(), 'goal': self.goal,
            'provider': self.provider, 'provider_consent': self.provider_consent,
            'expires_at': self.expires_at,
        }
