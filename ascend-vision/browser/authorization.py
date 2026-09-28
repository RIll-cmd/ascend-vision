"""Owner-reviewed, single-use authorization for consequential browser actions."""

from dataclasses import dataclass
import hashlib
import json
import math
import threading
import time
from urllib.parse import urlsplit


_ALLOWED_ACTIONS = {'click', 'fill', 'select', 'submit', 'upload', 'download'}
_MAX_TEXT = 2_000


@dataclass(frozen=True)
class ActionProposal:
    task_id: str
    action_id: str
    owner: str
    observation_id: str
    document_revision: int
    origin: str
    action: str
    target_ref: str
    target_label: str
    arguments: dict
    expected_effect: str
    expires_at: float

    def __post_init__(self):
        for name in ('task_id', 'action_id', 'owner', 'observation_id', 'target_ref'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f'{name} must be a nonempty bounded string')
        if type(self.document_revision) is not int or self.document_revision < 0:
            raise ValueError('document_revision must be a non-negative integer')
        if self.action not in _ALLOWED_ACTIONS:
            raise ValueError('action is not an authorizable browser action')
        parsed = urlsplit(self.origin)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError('origin must be an HTTP(S) origin')
        if not isinstance(self.target_label, str) or len(self.target_label) > 300:
            raise ValueError('target_label must be bounded text')
        if not isinstance(self.expected_effect, str) or not self.expected_effect.strip() or len(self.expected_effect) > 500:
            raise ValueError('expected_effect must be nonempty bounded text')
        if not isinstance(self.arguments, dict) or len(self.arguments) > 8:
            raise ValueError('arguments must be a small object')
        for key, value in self.arguments.items():
            if (not isinstance(key, str) or len(key) > 64 or not isinstance(value, str)
                    or len(value) > _MAX_TEXT):
                raise ValueError('proposal arguments must contain bounded text only')
        if (isinstance(self.expires_at, bool) or not isinstance(self.expires_at, (int, float))
                or not math.isfinite(self.expires_at)):
            raise ValueError('expires_at must be a finite timestamp')

    def canonical_payload(self) -> dict:
        parsed = urlsplit(self.origin)
        origin = f'{parsed.scheme.lower()}://{parsed.hostname.lower()}'
        if parsed.port is not None:
            origin += f':{parsed.port}'
        return {
            'task_id': self.task_id,
            'action_id': self.action_id,
            'owner': self.owner,
            'observation_id': self.observation_id,
            'document_revision': self.document_revision,
            'origin': origin,
            'action': self.action,
            'target_ref': self.target_ref,
            'target_label': self.target_label,
            'arguments': self.arguments,
            'expected_effect': self.expected_effect,
            'expires_at': self.expires_at,
        }

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.canonical_payload(), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def to_payload(self) -> dict:
        return {**self.canonical_payload(), 'digest': self.digest}


@dataclass(frozen=True)
class ActionGrant:
    task_id: str
    action_id: str
    owner: str
    proposal_digest: str
    expires_at: float


class GrantBook:
    """Tracks proposals and consumes owner approvals exactly once."""

    def __init__(self, *, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], ActionProposal] = {}
        self._grants: dict[tuple[str, str], ActionGrant] = {}

    def register(self, proposal: ActionProposal) -> None:
        if not isinstance(proposal, ActionProposal):
            raise TypeError('proposal must be validated')
        key = (proposal.task_id, proposal.action_id)
        with self._lock:
            self._pending[key] = proposal
            self._grants.pop(key, None)

    def approve(self, task_id: str, action_id: str, proposal_digest: str, owner: str) -> ActionGrant | None:
        key = (task_id, action_id)
        with self._lock:
            proposal = self._pending.get(key)
            if (proposal is None or proposal.owner != owner or proposal.digest != proposal_digest
                    or self._clock() >= proposal.expires_at):
                return None
            grant = ActionGrant(task_id, action_id, owner, proposal_digest, proposal.expires_at)
            self._grants[key] = grant
            return grant

    def reject(self, task_id: str, action_id: str, proposal_digest: str, owner: str) -> bool:
        key = (task_id, action_id)
        with self._lock:
            proposal = self._pending.get(key)
            if proposal is None or proposal.owner != owner or proposal.digest != proposal_digest:
                return False
            self._pending.pop(key, None)
            self._grants.pop(key, None)
            return True

    def consume(self, proposal: ActionProposal, *, owner: str) -> bool:
        key = (proposal.task_id, proposal.action_id)
        with self._lock:
            current = self._pending.get(key)
            grant = self._grants.get(key)
            if (current is None or grant is None or current.digest != proposal.digest
                    or grant.proposal_digest != proposal.digest or grant.owner != owner
                    or current.owner != owner or self._clock() >= grant.expires_at):
                return False
            self._pending.pop(key, None)
            self._grants.pop(key, None)
            return True
