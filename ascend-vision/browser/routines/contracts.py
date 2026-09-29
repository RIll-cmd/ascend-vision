"""Strict, versioned contracts for broker-owned browser routines."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit
from urllib.parse import unquote


MAX_MANIFEST_BYTES = 32 * 1024
MAX_MANIFESTS = 20
MAX_INPUTS = 10
MAX_ORIGINS = 10
MAX_PATH_PREFIXES = 10
MAX_INPUT_BYTES = 2_048
MAX_INVOCATION_BYTES = 15 * 1024
ROUTINE_ACTIONS = frozenset({'navigate', 'observe', 'click', 'scroll', 'back', 'wait_for', 'ask_user', 'finish'})
VERIFIER_IDS = frozenset({'cited_document_v1', 'two_documents_v1'})
ROUTINE_ID = re.compile(r'[a-z][a-z0-9_]{0,47}\Z')
INPUT_ID = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')


class RoutineContractError(ValueError):
    """Raised when a routine definition or invocation is malformed."""


@dataclass(frozen=True)
class RoutineInputSpec:
    name: str
    input_type: str
    required: bool
    max_length: int


@dataclass(frozen=True)
class RoutineBudget:
    decisions: int
    actions: int
    pages: int
    seconds: int

    def __post_init__(self):
        for name, maximum in (('decisions', 20), ('actions', 30), ('pages', 3), ('seconds', 180)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise RoutineContractError(f'routine budget {name} is outside the installation limit')


@dataclass(frozen=True)
class RoutineDefinition:
    schema_version: int
    routine_id: str
    version: int
    title: str
    mode: str
    inputs: Mapping[str, RoutineInputSpec]
    allowed_origins: tuple[str, ...]
    input_path_prefixes: Mapping[str, tuple[str, ...]]
    allowed_actions: frozenset[str]
    budget: RoutineBudget
    goal_template: str
    completion_verifier: str
    site_adapter: str | None
    digest: str


@dataclass(frozen=True)
class RoutineInvocation:
    routine_id: str
    version: int
    digest: str
    inputs: Mapping[str, str]

    @classmethod
    def from_payload(cls, payload) -> 'RoutineInvocation':
        fields = {'routine_id', 'version', 'digest', 'inputs'}
        if not isinstance(payload, dict) or payload.keys() != fields:
            raise RoutineContractError('routine invocation has unknown or missing fields')
        if not isinstance(payload['routine_id'], str) or not ROUTINE_ID.fullmatch(payload['routine_id']):
            raise RoutineContractError('routine_id is invalid')
        if type(payload['version']) is not int or payload['version'] < 1:
            raise RoutineContractError('routine version must be positive')
        digest = payload['digest']
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise RoutineContractError('routine digest is invalid')
        values = payload['inputs']
        if not isinstance(values, dict) or len(values) > MAX_INPUTS:
            raise RoutineContractError('routine inputs must be a bounded object')
        if len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_INVOCATION_BYTES:
            raise RoutineContractError('routine invocation exceeds 15 KiB')
        if any(not isinstance(key, str) or not INPUT_ID.fullmatch(key)
               or not isinstance(value, str) or len(value.encode('utf-8')) > MAX_INPUT_BYTES
               for key, value in values.items()):
            raise RoutineContractError('routine input contains an invalid value')
        return cls(payload['routine_id'], payload['version'], digest,
                   MappingProxyType({key: value for key, value in values.items()}))

    def to_payload(self) -> dict:
        return {'routine_id': self.routine_id, 'version': self.version,
                'digest': self.digest, 'inputs': dict(self.inputs)}


@dataclass(frozen=True)
class PreparedRoutine:
    definition: RoutineDefinition
    invocation: RoutineInvocation
    budget: RoutineBudget
    input_values: Mapping[str, str]
    policy: object
    task_id: str


def canonical_digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def safe_decoded_path(path: str) -> str:
    decoded = unquote(path or '/')
    if ('\\' in decoded or '\x00' in decoded or '//' in decoded
            or re.search(r'%[0-9a-fA-F]{2}', decoded)
            or any(part in {'.', '..'} for part in decoded.split('/'))):
        raise RoutineContractError('URL path contains an encoded or ambiguous path segment')
    return decoded


def parse_definition(payload) -> RoutineDefinition:
    fields = {'schema_version', 'routine_id', 'version', 'title', 'mode', 'inputs',
              'allowed_origins', 'input_path_prefixes', 'allowed_actions', 'budget',
              'goal_template', 'completion_verifier', 'site_adapter'}
    if not isinstance(payload, dict) or payload.keys() != fields:
        raise RoutineContractError('routine definition has unknown or missing fields')
    if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
        raise RoutineContractError('unsupported routine schema version')
    routine_id, version = payload['routine_id'], payload['version']
    if not isinstance(routine_id, str) or not ROUTINE_ID.fullmatch(routine_id):
        raise RoutineContractError('routine_id is invalid')
    if type(version) is not int or version < 1:
        raise RoutineContractError('routine version must be positive')
    title = payload['title']
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise RoutineContractError('routine title is invalid')
    if payload['mode'] != 'public_research':
        raise RoutineContractError('only public research routines are supported')
    if not isinstance(payload['goal_template'], str) or not payload['goal_template'].strip() \
            or len(payload['goal_template']) > 2_000 or '${' in payload['goal_template']:
        raise RoutineContractError('goal_template must be bounded static text without interpolation')
    verifier = payload['completion_verifier']
    if not isinstance(verifier, str) or verifier not in VERIFIER_IDS:
        raise RoutineContractError('completion verifier is not registered')
    adapter = payload['site_adapter']
    if adapter is not None:
        raise RoutineContractError('site adapters are not enabled before baseline evidence')

    raw_inputs = payload['inputs']
    if not isinstance(raw_inputs, dict) or not 1 <= len(raw_inputs) <= MAX_INPUTS:
        raise RoutineContractError('routine inputs are invalid')
    inputs = {}
    for name, spec in raw_inputs.items():
        if not isinstance(name, str) or not INPUT_ID.fullmatch(name):
            raise RoutineContractError('routine input name is invalid')
        if not isinstance(spec, dict) or spec.keys() != {'type', 'required', 'max_length'}:
            raise RoutineContractError('routine input specification has unknown or missing fields')
        if spec['type'] not in {'url', 'text'} or type(spec['required']) is not bool:
            raise RoutineContractError('routine input type or required flag is invalid')
        maximum = spec['max_length']
        ceiling = 2_048 if spec['type'] == 'url' else 500
        if type(maximum) is not int or not 1 <= maximum <= ceiling:
            raise RoutineContractError('routine input maximum is outside its supported limit')
        inputs[name] = RoutineInputSpec(name, spec['type'], spec['required'], maximum)

    origins = payload['allowed_origins']
    if not isinstance(origins, list) or not 1 <= len(origins) <= MAX_ORIGINS:
        raise RoutineContractError('routine requires one to ten exact origins')
    normalized_origins = []
    for origin in origins:
        if not isinstance(origin, str):
            raise RoutineContractError('routine origin must be text')
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError as exc:
            raise RoutineContractError('routine origin is malformed') from exc
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment or port not in {None, 443}
                or '*' in parsed.hostname):
            raise RoutineContractError('routine origins must be exact public HTTPS origins')
        host = parsed.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        if ':' in host or not host:
            raise RoutineContractError('routine origin host is ambiguous')
        normalized = f'https://{host}'
        if normalized in normalized_origins:
            raise RoutineContractError('routine origins must be unique')
        normalized_origins.append(normalized)

    prefixes = payload['input_path_prefixes']
    if not isinstance(prefixes, dict) or prefixes.keys() - {key for key, spec in inputs.items() if spec.input_type == 'url'}:
        raise RoutineContractError('routine path-prefix keys must name URL inputs')
    normalized_prefixes = {}
    for name, values in prefixes.items():
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_PATH_PREFIXES:
            raise RoutineContractError('routine URL input requires one to ten path prefixes')
        checked = []
        for prefix in values:
            if (not isinstance(prefix, str) or not prefix.startswith('/') or len(prefix) > 300
                    or any(char in prefix for char in ('?', '#', '%', '\\', '\x00'))
                    or any(part in {'.', '..'} for part in prefix.split('/'))):
                raise RoutineContractError('routine path prefix is invalid')
            checked.append(prefix)
        normalized_prefixes[name] = tuple(checked)
    for name, spec in inputs.items():
        if spec.input_type == 'url' and name not in normalized_prefixes:
            raise RoutineContractError('every routine URL input requires a path-prefix restriction')

    actions = payload['allowed_actions']
    if (not isinstance(actions, list) or not actions or len(actions) > len(ROUTINE_ACTIONS)
            or any(not isinstance(action, str) or action not in ROUTINE_ACTIONS for action in actions)
            or len(set(actions)) != len(actions)):
        raise RoutineContractError('routine action list is invalid')
    raw_budget = payload['budget']
    if not isinstance(raw_budget, dict) or raw_budget.keys() != {'decisions', 'actions', 'pages', 'seconds'}:
        raise RoutineContractError('routine budget has unknown or missing fields')
    budget = RoutineBudget(**raw_budget)
    digest = canonical_digest(payload)
    return RoutineDefinition(1, routine_id, version, title.strip(), 'public_research',
                             MappingProxyType(inputs), tuple(normalized_origins),
                             MappingProxyType(normalized_prefixes), frozenset(actions), budget,
                             payload['goal_template'].strip(), verifier, adapter, digest)
