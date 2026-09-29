"""Strict versioned browser task and model-decision contracts."""

from dataclasses import dataclass
import math
import re
from typing import Any
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
MAX_GOAL_CHARACTERS = 4_000
MAX_ARGUMENT_CHARACTERS = 2_000
MAX_FRAME_BYTES = 64 * 1024


class ContractError(ValueError):
    """Raised when an IPC or model payload violates the browser contract."""


def _exact_mapping(value: Any, fields: set[str], name: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError(f'{name} must be an object')
    if value.keys() - fields:
        raise ContractError(f'{name} contains unknown fields')
    if fields - value.keys():
        raise ContractError(f'{name} is missing required fields')
    return value


def _bounded_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ContractError(f'{name} must be a nonempty string of at most {limit} characters')
    return value.strip()


@dataclass(frozen=True)
class BrowserTaskRequest:
    task_id: str
    session_key: tuple[str, str, str]
    goal: str
    provider: str
    scope_mode: str
    expires_at: float
    allowed_origins: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    profile_id: str | None = None
    selected_file_token: str | None = None
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_payload(cls, payload: Any) -> 'BrowserTaskRequest':
        required = {'schema_version', 'task_id', 'session_key', 'goal', 'provider', 'scope_mode', 'expires_at'}
        optional = {'allowed_origins', 'capabilities', 'profile_id', 'selected_file_token'}
        if not isinstance(payload, dict) or payload.keys() - required - optional or required - payload.keys():
            raise ContractError('task request has unknown or missing fields')
        data = payload
        if type(data['schema_version']) is not int or data['schema_version'] != SCHEMA_VERSION:
            raise ContractError('unsupported schema_version')
        task_id = _bounded_text(data['task_id'], 'task_id', 64)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', task_id):
            raise ContractError('task_id contains invalid characters')
        key = data['session_key']
        if (not isinstance(key, (list, tuple)) or len(key) != 3
                or not isinstance(key[0], str) or not key[0].strip() or len(key[0]) > 128
                or not isinstance(key[1], str) or key[1] not in {'dashboard', 'voice'}
                or not isinstance(key[2], str) or not key[2].strip() or len(key[2]) > 128):
            raise ContractError('session_key must identify an owner and a local dashboard or voice session')
        goal = _bounded_text(data['goal'], 'goal', MAX_GOAL_CHARACTERS)
        provider = data['provider']
        if not isinstance(provider, str) or provider not in {'gemini', 'cerebras', 'groq'}:
            raise ContractError('provider is not enabled for browser decisions')
        scope_mode = data['scope_mode']
        if not isinstance(scope_mode, str) or scope_mode not in {'public_research', 'selected_origins'}:
            raise ContractError('scope_mode must be public_research or selected_origins')
        origins = data.get('allowed_origins', [])
        if (not isinstance(origins, list) or len(origins) > 10
                or any(not isinstance(item, str) for item in origins)):
            raise ContractError('allowed_origins must be a list of at most 10 origins')
        normalized_origins = []
        for origin in origins:
            parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ContractError('allowed origin is malformed') from exc
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                    or port not in {None, 80, 443}):
                raise ContractError('allowed origins must be HTTP(S) origins on standard ports')
            normalized = f'{parsed.scheme.lower()}://{parsed.hostname.rstrip(".").lower()}'
            if port is not None and port not in {80, 443}:
                normalized += f':{port}'
            if normalized in normalized_origins:
                raise ContractError('allowed_origins must be unique')
            normalized_origins.append(normalized)
        capabilities = data.get('capabilities', [])
        allowed_capabilities = {'fill', 'select', 'click', 'download', 'upload'}
        if (not isinstance(capabilities, list) or len(capabilities) > len(allowed_capabilities)
                or any(not isinstance(item, str) or item not in allowed_capabilities for item in capabilities)
                or len(set(capabilities)) != len(capabilities)):
            raise ContractError('capabilities contain an unsupported or duplicate action')
        profile_id = data.get('profile_id')
        if profile_id is not None and (not isinstance(profile_id, str)
                                       or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile_id)):
            raise ContractError('profile_id is invalid')
        selected_file_token = data.get('selected_file_token')
        if selected_file_token is not None and (
                not isinstance(selected_file_token, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', selected_file_token)):
            raise ContractError('selected_file_token is invalid')
        if ('upload' in capabilities) != (selected_file_token is not None):
            raise ContractError('upload requires exactly one owner-selected upload file')
        if scope_mode == 'public_research' and (normalized_origins or capabilities or profile_id is not None):
            raise ContractError('public_research cannot add authenticated origins, capabilities, or a profile')
        if scope_mode == 'selected_origins' and not normalized_origins:
            raise ContractError('selected_origins requires at least one owner-selected origin')
        expires_at = data['expires_at']
        if (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                or not math.isfinite(expires_at)):
            raise ContractError('expires_at must be a finite timestamp')
        return cls(task_id, (key[0].strip(), key[1], key[2].strip()), goal, provider,
                   scope_mode, float(expires_at), tuple(normalized_origins),
                   tuple(capabilities), profile_id, selected_file_token)

    def to_payload(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'task_id': self.task_id,
            'session_key': list(self.session_key),
            'goal': self.goal,
            'provider': self.provider,
            'scope_mode': self.scope_mode,
            'expires_at': self.expires_at,
            'allowed_origins': list(self.allowed_origins),
            'capabilities': list(self.capabilities),
            'profile_id': self.profile_id,
            'selected_file_token': self.selected_file_token,
        }


@dataclass(frozen=True)
class BrowserDecision:
    observation_id: str
    action: str
    arguments: dict
    expected_result: str = ''
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_payload(cls, payload: Any) -> 'BrowserDecision':
        fields = {'schema_version', 'observation_id', 'action', 'arguments', 'expected_result'}
        if not isinstance(payload, dict):
            raise ContractError('browser decision must be an object')
        if payload.keys() - fields:
            raise ContractError('browser decision contains unknown fields')
        required = fields - {'expected_result'}
        if required - payload.keys():
            raise ContractError('browser decision is missing required fields')
        data = {**payload, 'expected_result': payload.get('expected_result', '')}
        if type(data['schema_version']) is not int or data['schema_version'] != SCHEMA_VERSION:
            raise ContractError('unsupported schema_version')
        observation_id = _bounded_text(data['observation_id'], 'observation_id', 128)
        action = data['action']
        if not isinstance(action, str):
            raise ContractError('action is not allowed')
        schemas = {
            'navigate': {'url'}, 'observe': set(), 'click': {'element_ref'},
            'upload': {'element_ref'}, 'download': {'element_ref'},
            'fill': {'element_ref', 'value'}, 'select': {'element_ref', 'value'},
            'scroll': {'direction', 'pixels'}, 'back': set(), 'wait_for': {'milliseconds'},
            'ask_user': {'question'}, 'finish': {'finding', 'source_observation_ids'},
        }
        if action not in schemas:
            raise ContractError('action is not allowed')
        arguments = _exact_mapping(data['arguments'], schemas[action], 'decision arguments')
        if action == 'navigate':
            url = _bounded_text(arguments['url'], 'url', 2_048)
            parsed = urlsplit(url)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ContractError('navigate requires a public HTTP(S) URL without credentials')
            arguments = {'url': url}
        elif action in {'click', 'fill', 'select', 'upload', 'download'}:
            element_ref = _bounded_text(arguments['element_ref'], 'element_ref', 128)
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', element_ref):
                raise ContractError('element_ref contains invalid characters')
            arguments = {'element_ref': element_ref}
            if action in {'fill', 'select'}:
                arguments['value'] = _bounded_text(
                    data['arguments']['value'], 'value', MAX_ARGUMENT_CHARACTERS,
                )
        elif action == 'scroll':
            direction, pixels = arguments['direction'], arguments['pixels']
            if (not isinstance(direction, str) or direction not in {'up', 'down'}
                    or type(pixels) is not int or not 1 <= pixels <= 2_000):
                raise ContractError('scroll direction or distance is invalid')
            arguments = {'direction': direction, 'pixels': pixels}
        elif action == 'wait_for':
            milliseconds = arguments['milliseconds']
            if type(milliseconds) is not int or not 1 <= milliseconds <= 10_000:
                raise ContractError('wait_for must be between 1 and 10000 milliseconds')
            arguments = {'milliseconds': milliseconds}
        elif action == 'ask_user':
            arguments = {'question': _bounded_text(arguments['question'], 'question', 500)}
        elif action == 'finish':
            finding = _bounded_text(arguments['finding'], 'finding', 4_000)
            ids = arguments['source_observation_ids']
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 20
                    or any(not isinstance(item, str) or not item or len(item) > 128 for item in ids)):
                raise ContractError('finish must cite one or more bounded observation IDs')
            arguments = {'finding': finding, 'source_observation_ids': list(ids)}
        expected = data.get('expected_result', '')
        if not isinstance(expected, str) or len(expected) > 500:
            raise ContractError('expected_result must be at most 500 characters')
        return cls(observation_id, action, arguments, expected.strip())
