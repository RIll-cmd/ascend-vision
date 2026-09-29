"""Owner-local manifest loading and exact-digest routine preparation."""

from __future__ import annotations

import json
from pathlib import Path
import os
import tempfile
from types import MappingProxyType
from urllib.parse import urlsplit

from browser.contracts import BrowserTaskRequest
from browser.routines.contracts import (
    MAX_MANIFESTS, MAX_MANIFEST_BYTES, PreparedRoutine, RoutineBudget,
    RoutineContractError, RoutineInvocation, RoutineDefinition, ROUTINE_ID,
    parse_definition, safe_decoded_path,
)
from browser.routines.policy import RoutinePolicy


class RoutineRegistry:
    def __init__(self, directory: str | Path, *, enabled_versions=(), disabled_path: str | Path | None = None):
        self.directory = Path(directory).resolve()
        self.disabled_path = Path(disabled_path).resolve() if disabled_path is not None else None
        if not self.directory.exists():
            self._definitions = {}
        else:
            paths = sorted(self.directory.glob('*.json'))
            if len(paths) > MAX_MANIFESTS:
                raise RoutineContractError('installation contains too many routine definitions')
            definitions = {}
            for path in paths:
                if path.is_symlink() or path.resolve().parent != self.directory:
                    raise RoutineContractError('routine definition is outside its installation directory')
                if path.stat().st_size > MAX_MANIFEST_BYTES:
                    raise RoutineContractError('routine definition exceeds 32 KiB')
                try:
                    payload = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise RoutineContractError('routine definition could not be read') from exc
                definition = parse_definition(payload)
                key = (definition.routine_id, definition.version)
                if key in definitions:
                    raise RoutineContractError('duplicate routine ID and version')
                definitions[key] = definition
            self._definitions = definitions
        accepted = set()
        for item in enabled_versions:
            if (not isinstance(item, tuple) or len(item) != 3 or not isinstance(item[0], str)
                    or type(item[1]) is not int or not isinstance(item[2], str)):
                raise RoutineContractError('enabled routine reference is invalid')
            key = (item[0], item[1])
            definition = self._definitions.get(key)
            if definition is None or definition.digest != item[2]:
                raise RoutineContractError('enabled routine reference does not match an installed digest')
            accepted.add((item[0], item[1], item[2]))
        self._configured = accepted
        self._disabled = self._read_disabled()
        self._enabled = accepted - self._disabled

    def catalogue(self) -> tuple[dict, ...]:
        return tuple({
            'routine_id': item.routine_id, 'version': item.version, 'digest': item.digest,
            'title': item.title, 'mode': item.mode, 'allowed_origins': list(item.allowed_origins),
            'input_schema': {name: {'type': spec.input_type, 'required': spec.required,
                                    'max_length': spec.max_length} for name, spec in item.inputs.items()},
            'accepted': (item.routine_id, item.version, item.digest) in self._configured,
            'enabled': (item.routine_id, item.version, item.digest) in self._enabled,
        } for item in sorted(self._definitions.values(), key=lambda definition: (definition.routine_id, definition.version)))

    def digest(self, routine_id: str, version: int) -> str:
        return self._definition(routine_id, version).digest

    def resolve(self, routine_id: str, version: int, digest: str) -> RoutineDefinition:
        definition = self._definition(routine_id, version)
        if definition.digest != digest:
            raise RoutineContractError('routine definition changed and needs fresh acceptance')
        if (routine_id, version, digest) not in self._enabled:
            raise RoutineContractError('routine version is disabled')
        return definition

    def disable(self, routine_id: str, version: int) -> None:
        if not isinstance(routine_id, str) or not ROUTINE_ID.fullmatch(routine_id) \
                or type(version) is not int or version < 1:
            raise RoutineContractError('routine disable reference is invalid')
        blocked = {item for item in self._configured if item[:2] == (routine_id, version)}
        if not blocked:
            return
        updated = self._disabled | blocked
        self._write_disabled(updated)
        self._disabled = updated
        self._enabled -= blocked

    def enable(self, routine_id: str, version: int) -> None:
        if not isinstance(routine_id, str) or not ROUTINE_ID.fullmatch(routine_id) \
                or type(version) is not int or version < 1:
            raise RoutineContractError('routine enable reference is invalid')
        allowed = {item for item in self._configured if item[:2] == (routine_id, version)}
        if not allowed:
            raise RoutineContractError('routine version has no owner-configured acceptance reference')
        self._definition(routine_id, version)
        updated = self._disabled - allowed
        self._write_disabled(updated)
        self._disabled = updated
        self._enabled |= allowed

    def prepare(self, invocation: RoutineInvocation, request: BrowserTaskRequest, *,
                base_policy, installation_budget: RoutineBudget | None = None) -> PreparedRoutine:
        if not isinstance(invocation, RoutineInvocation) or not isinstance(request, BrowserTaskRequest):
            raise TypeError('routine preparation requires validated browser contracts')
        if request.scope_mode != 'public_research' or request.allowed_origins or request.capabilities or request.profile_id:
            raise RoutineContractError('public routines cannot convert an authenticated or expanded browser task')
        definition = self.resolve(invocation.routine_id, invocation.version, invocation.digest)
        if invocation.inputs.keys() - definition.inputs.keys():
            raise RoutineContractError('routine invocation contains an unknown input')
        for name, spec in definition.inputs.items():
            value = invocation.inputs.get(name)
            if value is None:
                if spec.required:
                    raise RoutineContractError(f'routine input {name} is required')
                continue
            if len(value) > spec.max_length or not value.strip():
                raise RoutineContractError(f'routine input {name} is empty or too long')
            if any(ord(char) < 32 and char not in '\n\t' for char in value):
                raise RoutineContractError(f'routine input {name} contains a control character')
            if spec.input_type == 'url':
                try:
                    base_policy.validate_url(value)
                except Exception as exc:
                    raise RoutineContractError('routine URL failed the existing public destination policy') from exc
                parsed = urlsplit(value)
                origin = f'https://{parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()}'
                try:
                    decoded_path = safe_decoded_path(parsed.path)
                except RoutineContractError as exc:
                    raise RoutineContractError('routine URL path is outside its safe prefix') from exc
                if origin not in definition.allowed_origins:
                    raise RoutineContractError('routine URL origin is not in this exact version')
                if not any(decoded_path.startswith(prefix) for prefix in definition.input_path_prefixes[name]):
                    raise RoutineContractError('routine URL path is outside its safe prefix')
        limits = installation_budget or RoutineBudget(20, 30, 3, 180)
        budget = RoutineBudget(
            min(definition.budget.decisions, limits.decisions),
            min(definition.budget.actions, limits.actions),
            min(definition.budget.pages, limits.pages),
            min(definition.budget.seconds, limits.seconds),
        )
        prefixes = tuple(prefix for values in definition.input_path_prefixes.values() for prefix in values)
        policy = RoutinePolicy(base_policy, allowed_origins=definition.allowed_origins,
                               path_prefixes=prefixes, allowed_actions=definition.allowed_actions)
        frozen_inputs = MappingProxyType(dict(invocation.inputs))
        return PreparedRoutine(definition, invocation, budget, frozen_inputs, policy, request.task_id)

    def _definition(self, routine_id: str, version: int) -> RoutineDefinition:
        if not isinstance(routine_id, str) or type(version) is not int:
            raise RoutineContractError('routine reference is invalid')
        definition = self._definitions.get((routine_id, version))
        if definition is None:
            raise RoutineContractError('routine version is not installed')
        return definition

    def _read_disabled(self) -> set[tuple[str, int, str]]:
        if self.disabled_path is None or not self.disabled_path.exists():
            return set()
        if self.disabled_path.is_symlink():
            raise RoutineContractError('routine disable state must be an owner-local regular file')
        try:
            payload = json.loads(self.disabled_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RoutineContractError('routine disable state is unreadable') from exc
        if not isinstance(payload, list) or len(payload) > MAX_MANIFESTS:
            raise RoutineContractError('routine disable state is malformed')
        disabled = set()
        for item in payload:
            if (not isinstance(item, dict) or item.keys() != {'routine_id', 'version', 'digest'}
                    or not isinstance(item['routine_id'], str) or type(item['version']) is not int
                    or not isinstance(item['digest'], str)
                    or not ROUTINE_ID.fullmatch(item['routine_id'])
                    or item['version'] < 1
                    or len(item['digest']) != 64
                    or any(char not in '0123456789abcdef' for char in item['digest'])):
                raise RoutineContractError('routine disable state is malformed')
            key = (item['routine_id'], item['version'], item['digest'])
            if key in self._configured:
                disabled.add(key)
        return disabled

    def _write_disabled(self, references) -> None:
        if self.disabled_path is None:
            return
        if self.disabled_path.is_symlink():
            raise RoutineContractError('routine disable state must be an owner-local regular file')
        self.disabled_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([
            {'routine_id': routine_id, 'version': version, 'digest': digest}
            for routine_id, version, digest in sorted(references)
        ], separators=(',', ':'), ensure_ascii=False)
        handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.disabled_path.parent,
                                             prefix='routine-state-', suffix='.tmp', delete=False)
        temp_path = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.disabled_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
